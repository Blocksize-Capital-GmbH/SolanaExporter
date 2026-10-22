"""Estimate rewarded Alpenglow vote slots without per-slot RPC requests."""

import base64
import ipaddress
import logging
import math
import struct
import time
from bisect import bisect_right
from collections import deque
from itertools import islice
from urllib.parse import urlsplit

import requests

# PDA: seed b"vote_reward_account", Alpenglow feature program A1pengvuM6...ATGawhJS.
REWARD_ACCOUNT = "3vJzniWALu2qGFJ1ZY6JD3Y2ZmA5zWqmVdDX7yBXHgu7"
WINDOW = 300
PROCESSING_MARGIN = 8
RETRY_SECONDS = 300


def _hostname(url):
    host = urlsplit(url).hostname
    if not isinstance(host, str) or not host:
        raise ValueError("Missing RPC hostname")
    host = host.encode("idna").decode("ascii").lower().rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host, host == "localhost" or host.endswith(".localhost") or host.startswith("127.")
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return str(address), address.is_loopback or address.is_unspecified


def _u64(value):
    if type(value) is not int or not 0 <= value < 2**64:
        raise ValueError("Invalid unsigned integer")
    return value


def _account(votes, vote_pubkey):
    if not isinstance(votes, dict) or any(not isinstance(votes.get(k), list) for k in ("current", "delinquent")):
        raise ValueError("Invalid vote-account response")
    matches = [
        a
        for k in ("current", "delinquent")
        for a in votes[k]
        if isinstance(a, dict) and a.get("votePubkey") == vote_pubkey
    ]
    if len(matches) != 1:
        raise ValueError("Vote account missing or duplicated")
    return matches[0]


def _credit_entry(account, epoch):
    entries = account.get("epochCredits")
    if not isinstance(entries, list):
        raise ValueError("Missing credit history")
    marker = [2**64 - 1] * 3
    if marker in entries:
        entries = entries[entries.index(marker) + 1 :]
    if not entries or any(not isinstance(e, list) or len(e) != 3 for e in entries):
        raise ValueError("Invalid credit history")
    entries = [[_u64(v) for v in e] for e in entries]
    if entries[-1][0] != epoch or any(e[0] > epoch for e in entries):
        raise ValueError("Credit history belongs to another epoch")
    matches = [e for e in entries if e[0] == epoch]
    if len(matches) != 1:
        raise ValueError("Current epoch credit entry missing or duplicated")
    entry = [_u64(v) for v in matches[0]]
    if entry[1] < entry[2]:
        raise ValueError("Negative earned credits")
    return entry


class VotesLast300Slots:
    """Keep a bounded credit window and cache its public reward-rate reference."""

    def __init__(self, vote_pubkey, local_rpc_url, public_rpc_url, logger=None):
        """Use existing endpoint configuration; defer reference reads until needed."""
        self.vote_pubkey = vote_pubkey
        self.local_rpc_url = local_rpc_url
        self.public_rpc_url = public_rpc_url
        self.logger = logger or logging.getLogger(__name__)
        self._samples = deque(maxlen=WINDOW + 2)
        self._prior_credit_sum = 0
        self._key = None
        self._reference_key = None
        self._rate = None
        self._local_genesis = None
        self._retry_key = None
        self._retry_at = 0.0

    def reset(self):
        """Discard the observation window without repeating cached reference work."""
        self._samples.clear()
        self._prior_credit_sum = 0
        self._key = None

    @staticmethod
    def _rpc(url, methods):
        payload = [{"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(methods, 1)]
        response = requests.post(url, json=payload, timeout=3)
        response.raise_for_status()
        raw = response.json()
        if not isinstance(raw, list) or len(raw) != len(payload):
            raise ValueError("Incomplete reference RPC response")
        result = {}
        for item in raw:
            if not isinstance(item, dict) or type(item.get("id")) is not int:
                raise ValueError("Invalid reference RPC response")
            index = item["id"]
            if index not in range(1, len(payload) + 1) or index in result or item.get("error") or "result" not in item:
                raise ValueError("Failed or uncorrelated reference RPC response")
            result[index] = item["result"]
        return result

    def _credits_per_vote(self, key, info):
        if self._reference_key == key:
            return self._rate
        now = time.monotonic()
        if self._retry_key == key and now < self._retry_at:
            return None
        self._retry_key, self._retry_at = key, now + RETRY_SECONDS
        epoch, identity, stake = key
        try:
            public_host, public_is_local = _hostname(self.public_rpc_url)
            local_host, _ = _hostname(self.local_rpc_url)
            if public_is_local or public_host == local_host:
                raise ValueError("An external public RPC endpoint is required")
            if self._local_genesis is None:
                genesis = self._rpc(self.local_rpc_url, [("getGenesisHash", [])])[1]
                if not isinstance(genesis, str) or not genesis:
                    raise ValueError("Missing local genesis hash")
                self._local_genesis = genesis
            reference = self._rpc(
                self.public_rpc_url,
                [
                    ("getGenesisHash", []),
                    ("getAccountInfo", [REWARD_ACCOUNT, {"encoding": "base64", "commitment": "finalized"}]),
                    ("getVoteAccounts", [{"commitment": "finalized"}]),
                ],
            )
            if reference[1] != self._local_genesis:
                raise ValueError("Public RPC is on a different cluster")
            budget_info = reference[2]
            context_slot = _u64(budget_info["context"]["slot"])
            account = budget_info["value"]
            if account["owner"] != "11111111111111111111111111111111":
                raise ValueError("Unexpected reward-account owner")
            encoded, encoding = account["data"]
            if encoding != "base64":
                raise ValueError("Unexpected reward-account encoding")
            data = base64.b64decode(encoded, validate=True)
            if len(data) not in (25, 49) or data[24] != (1 if len(data) == 49 else 0):
                raise ValueError("Unsupported reward-account layout")
            budget, slots, budget_epoch = struct.unpack_from("<QQQ", data)
            if budget_epoch != epoch or slots != info["slotsInEpoch"] or not budget:
                raise ValueError("Stale or empty reward budget")
            epoch_start = info["absoluteSlot"] - info["slotIndex"]
            if (
                not epoch_start <= context_slot < epoch_start + slots
                or abs(context_slot - info["absoluteSlot"]) > WINDOW
            ):
                raise ValueError("Public reference is not near the local epoch context")
            target = _account(reference[3], self.vote_pubkey)
            if (
                target.get("nodePubkey") != identity
                or target.get("epochVoteAccount") is not True
                or _u64(target.get("activatedStake")) != stake
            ):
                raise ValueError("Public target account differs from the local account")
            _credit_entry(target, epoch)
            total = 0
            seen = set()
            for category in ("current", "delinquent"):
                for peer in reference[3][category]:
                    if not isinstance(peer, dict) or not isinstance(peer.get("votePubkey"), str):
                        raise ValueError("Invalid public stake census")
                    if peer["votePubkey"] in seen or type(peer.get("epochVoteAccount")) is not bool:
                        raise ValueError("Duplicate or invalid public stake census")
                    seen.add(peer["votePubkey"])
                    peer_stake = _u64(peer.get("activatedStake"))
                    if peer["epochVoteAccount"]:
                        total += peer_stake
            if not stake <= total < 2**64:
                raise ValueError("Invalid admitted stake total")
            rate = (budget * stake // (slots * total)) // 2
            if rate <= 0:
                raise ValueError("Zero voter reward rate")
        except (requests.RequestException, ValueError, TypeError, KeyError, struct.error):
            self.logger.warning("Vote-estimate reference unavailable; retrying after %s seconds", RETRY_SECONDS)
            return None
        self._reference_key, self._rate = key, rate
        return rate

    @staticmethod
    def _leader_count(leaders, start, end):
        """Count scheduled leader slots in the half-open interval (start, end]."""
        return bisect_right(leaders, end) - bisect_right(leaders, start)

    def _leader_interval(self, leaders, start, end):
        # Margin classifies mixed polls; only actual leader slots are counted as zero.
        return self._leader_count(leaders, start - PROCESSING_MARGIN, end + PROCESSING_MARGIN) > 0

    def _window_votes(self, rate, cutoff, leaders):
        remainder = self._prior_credit_sum % rate
        estimate = 0
        raw_estimate = 0.0
        for (start, initial), (end, final) in zip(self._samples, islice(self._samples, 1, None)):
            span = end - start
            delta = final - initial
            nonleaders = span - self._leader_count(leaders, start, end)
            visible_start = max(start, cutoff)
            visible_nonleaders = end - visible_start - self._leader_count(leaders, visible_start, end)
            mixed = self._leader_interval(leaders, start, end)
            if mixed:
                remainder = 0
            count, remainder = divmod(delta + remainder, rate)
            count = min(nonleaders, int(count))
            # The poll does not locate individual votes: spread its integer count uniformly.
            visible_count = count - count * (nonleaders - visible_nonleaders) // nonleaders if nonleaders else 0
            estimate += visible_count
            if mixed:
                remainder = 0  # Neither a leader bonus nor its remainder can credit a later poll.
                raw_estimate += visible_count
            else:
                raw_estimate += delta * visible_nonleaders / (span * rate)
        if not math.isfinite(raw_estimate) or raw_estimate > WINDOW * 1.05:
            raise ValueError("Credit increase exceeds the estimate window capacity")
        return estimate

    def observe(self, vote_accounts, epoch_info, leader_schedule, active_identity, local_genesis=None):
        """Estimate non-leader rewarded inclusions over 300 processing slots."""
        try:
            if local_genesis is not None:
                if not isinstance(local_genesis, str) or not local_genesis:
                    raise ValueError("Invalid local genesis hash")
                if self._local_genesis != local_genesis:
                    self.reset()
                    self._reference_key, self._rate = None, None
                    self._retry_key, self._retry_at = None, 0.0
                self._local_genesis = local_genesis
            if not isinstance(epoch_info, dict) or not isinstance(leader_schedule, dict):
                raise ValueError("Missing local observation")
            epoch, slot, index, slots = (
                _u64(epoch_info[k]) for k in ("epoch", "absoluteSlot", "slotIndex", "slotsInEpoch")
            )
            if not slots or index >= slots or index > slot:
                raise ValueError("Invalid epoch position")
            account = _account(vote_accounts, self.vote_pubkey)
            identity = account.get("nodePubkey")
            stake = _u64(account.get("activatedStake"))
            if (
                not isinstance(identity, str)
                or not identity
                or identity != active_identity
                or account.get("epochVoteAccount") is not True
                or not stake
            ):
                raise ValueError("Local node is not the admitted voting identity")
            _, credits, previous = _credit_entry(account, epoch)
            schedule = leader_schedule.get(identity, [])
            if (
                not isinstance(schedule, list)
                or any(_u64(s) >= slots for s in schedule)
                or len(set(schedule)) != len(schedule)
            ):
                raise ValueError("Invalid leader schedule")
            leaders = sorted(slot - index + s for s in schedule)
            key = (epoch, identity, stake, previous)
            if key != self._key or index < PROCESSING_MARGIN:
                self.reset()
                self._key = key
            if index < PROCESSING_MARGIN:
                return math.nan
            if self._samples:
                prior_slot, prior_credit = self._samples[-1]
                if slot <= prior_slot or slot - prior_slot >= WINDOW or credits < prior_credit:
                    self._samples.clear()
                    self._prior_credit_sum = 0
            self._samples.append((slot, credits))
            cutoff = slot - WINDOW
            while len(self._samples) > 2 and self._samples[1][0] <= cutoff:
                start, initial = self._samples.popleft()
                end, final = self._samples[0]
                if self._leader_interval(leaders, start, end):
                    self._prior_credit_sum = 0
                else:
                    self._prior_credit_sum += final - initial
            anchor_slot = self._samples[0][0]
            if anchor_slot > cutoff or len(self._samples) < 2:
                return math.nan
            rate = self._credits_per_vote((epoch, identity, stake), epoch_info)
            if not rate:
                return math.nan
            estimate = self._window_votes(rate, cutoff, leaders)
            # The public census approximates frozen weights; tolerate at most 5% upward bias.
            if not math.isfinite(estimate) or estimate < 0 or estimate > WINDOW * 1.05:
                self.reset()
                return math.nan
            nonleader_capacity = WINDOW - self._leader_count(leaders, cutoff, slot)
            return min(float(nonleader_capacity), estimate)
        except (ValueError, TypeError, KeyError):
            self.reset()
            return math.nan
