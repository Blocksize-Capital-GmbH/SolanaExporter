"""Prometheus exporter for Solana validator metrics."""

import os
import socket
import time
from typing import List, Optional

from exporter.jsonRPCRequest import JsonRPCRequest
from exporter.jsonRPCResponse import JsonRPCResponse
from exporter.rpcExporter import RPCExporter
from prometheus_client import Gauge, Info

from solanaexporter.rpc import send_rpc
from solanaexporter.sfdp import SfdpMonitor

# Solana-specific configuration keys
# Required configuration keys - these must be present
REQUIRED_CONFIG_KEYS = {
    "rpc_url": "SOLANA_RPC_URL",
    "public_rpc_url": "SOLANA_PUBLIC_RPC_URL",
    "exporter_port": "EXPORTER_PORT",
    "poll_interval": "POLL_INTERVAL",
    "vote_pubkey": "VOTE_PUBKEY",
    "validator_pubkey": "VALIDATOR_PUBKEY",
    "label": "LABEL",
}

# Optional configuration keys - these can be omitted
OPTIONAL_CONFIG_KEYS = {
    "version": "VERSION",
    "cluster": "SOLANA_CLUSTER",
    "client": "SOLANA_CLIENT",
    "double_zero_fees_address": "DOUBLE_ZERO_FEES_ADDRESS",
    "jpool_bond_withdrawer_authority": "JPOOL_BOND_WITHDRAWER_AUTHORITY",
}

# All configuration keys combined
ALL_CONFIG_KEYS = {**REQUIRED_CONFIG_KEYS, **OPTIONAL_CONFIG_KEYS}


class SolanaExporter(RPCExporter):
    """Collect and expose Solana validator metrics via Prometheus."""

    def __init__(self, config_source: str, config_file: Optional[str] = None):
        """Initialize the exporter and register Prometheus metrics."""
        super().__init__(
            config_source=config_source,
            config_file=config_file,
            config_keys=ALL_CONFIG_KEYS,
            required_keys=REQUIRED_CONFIG_KEYS,
        )

        # Optional identity-role inputs (primary vs backup identity key)
        self.staked_identity_pubkey: str = (os.getenv("STAKED_IDENTITY_PUBKEY") or "").strip()
        self.unstaked_identity_pubkey: str = (os.getenv("UNSTAKED_IDENTITY_PUBKEY") or "").strip()
        self.hostname: str = (
            (os.getenv("EXPORTER_HOSTNAME") or "").strip()
            or (os.getenv("HOSTNAME") or "").strip()
            or socket.gethostname()
        )
        self._stake_state: str = "unknown"  # derived from vote-account stake (not identity role)
        self._last_validator_info_labelvalues: Optional[tuple[str, str, str, str, str, str]] = None

        # Prometheus metrics setup
        self.slot_number = Gauge(
            name="solana_slot_number",
            documentation="Current slot number of the Solana validator",
            registry=self.registry,
        )
        self.absolute_slot_number = Gauge(
            name="solana_absolute_slot_number",
            documentation="Absolute slot number of the Solana chain",
            registry=self.registry,
        )
        self.slot_lag = Gauge(
            name="solana_slot_lag",
            documentation="Slot number lag of validator vs the Solana chain",
            registry=self.registry,
        )
        self.sync_status = Gauge(
            name="solana_sync_status",
            documentation="Node sync status (1 for synced, 0 for not synced)",
            registry=self.registry,
        )
        self.slot_time = Gauge(
            name="solana_slot_time",
            documentation="Time taken to process a slot",
            registry=self.registry,
        )
        self.epoch = Gauge(
            name="solana_epoch",
            documentation="Current Solana epoch",
            registry=self.registry,
        )
        self.balance = Gauge(
            name="solana_account_balance",
            documentation="Validator's account balance",
            registry=self.registry,
        )
        self.double_zero_balance = Gauge(
            name="solana_double_zero_balance",
            documentation="Balance of the double zero fees address",
            registry=self.registry,
        )
        self.health_status = Gauge(
            name="solana_health_status",
            documentation="Health status of the Solana node",
            registry=self.registry,
        )
        self.total_delegated_stake = Gauge(
            name="solana_total_delegated_stake",
            documentation="Total stake delegated to the validator",
            registry=self.registry,
        )
        self.delinquent_stake = Gauge(
            name="solana_delinquent_stake",
            documentation="Stake that is delinquent",
            registry=self.registry,
        )
        self.pending_stake = Gauge(
            name="solana_pending_stake",
            documentation="Stake that is delegated but not active yet",
            registry=self.registry,
        )
        self.missed_slots = Gauge(
            name="solana_missed_slots",
            documentation="Skipped leader slots in the current finalized epoch range (gauge)",
            registry=self.registry,
        )
        self.leader_status = Gauge(
            name="solana_leader_status",
            documentation="Leader status (1 or 0)",
            registry=self.registry,
        )
        self.vote_distance = Gauge(
            name="solana_vote_distance",
            documentation="Vote distance from the highest known slot",
            registry=self.registry,
        )
        self.block_production_success = Gauge(
            name="solana_block_production_success",
            documentation="Produced blocks divided by scheduled slots; NaN without opportunities",
            registry=self.registry,
        )
        self.credits_earned = Gauge(
            name="solana_credits_earned",
            documentation="Total vote credits earned by the validator",
            registry=self.registry,
        )
        self.build_info = Info(
            name="solana_build",
            documentation="Build information including version and instance label",
            registry=self.registry,
        )
        self.validator_info = Gauge(
            "solana_validator_info",
            "Validator info metric (value=1) labeled with hostname, identity role, and keys",
            labelnames=[
                "hostname",
                "stake_state",
                "identity_role",
                "identity_pubkey",
                "vote_pubkey",
                "instance_label",
            ],
            registry=self.registry,
        )

        self._has_jpool_bond = (
            hasattr(self.config, "jpool_bond_withdrawer_authority") and self.config.jpool_bond_withdrawer_authority
        )
        if self._has_jpool_bond:
            self.jpool_bond_balance = Gauge(
                name="solana_jpool_bond_balance",
                documentation="JPool validator bond balance (in SOL)",
                registry=self.registry,
            )

        for attribute, name, description in [
            ("leader_slots", "solana_leader_slots", "Scheduled slots in the production range"),
            ("blocks_produced", "solana_blocks_produced", "Produced blocks in the production range"),
            ("skip_ratio", "solana_skip_ratio", "Skipped / scheduled slots; NaN without opportunities"),
            ("production_first_slot", "solana_production_first_slot", "Inclusive production range start"),
            ("production_last_slot", "solana_production_last_slot", "Inclusive production range end"),
            ("production_epoch", "solana_production_epoch", "Epoch associated with the production range"),
            ("production_data_valid", "solana_production_data_valid", "Whether production counts are valid"),
            (
                "production_last_success",
                "solana_production_last_success_timestamp_seconds",
                "Last valid production read",
            ),
            ("collection_success", "solana_collection_success", "Whether essential local collection succeeded"),
            ("collection_last_success", "solana_collection_last_success_timestamp_seconds", "Last complete local poll"),
            (
                "reference_valid",
                "solana_reference_valid",
                "Whether the independent reference matches the local cluster",
            ),
            ("vote_account_balance", "solana_vote_account_balance", "Vote account balance in SOL, including VAT funds"),
            (
                "vote_identity_match",
                "solana_vote_identity_match",
                "Whether the configured vote account belongs to the local identity",
            ),
        ]:
            setattr(self, attribute, Gauge(name, description, registry=self.registry))
        self.sfdp = SfdpMonitor(
            self.registry,
            cluster=getattr(self.config, "cluster", None) or "unknown",
            client=getattr(self.config, "client", None) or "agave",
        )
        self._schedule_key = None
        self._schedule = None
        self._observed_version = None
        self._current_epoch = None
        self._production_range = None
        self._invalidate_poll()

        self.programAccountsCallCounter: int = -1
        self.stake_accounts: List[JsonRPCResponse] = []
        self.last_absolute_slot: Optional[int] = None
        self.last_timestamp: Optional[float] = None

    def _invalidate_poll(self):
        """Invalidate observations while keeping their last-success timestamps."""
        for name in (
            "slot_number",
            "absolute_slot_number",
            "slot_lag",
            "slot_time",
            "epoch",
            "balance",
            "double_zero_balance",
            "vote_account_balance",
            "total_delegated_stake",
            "delinquent_stake",
            "pending_stake",
            "leader_status",
            "vote_distance",
            "credits_earned",
            "vote_identity_match",
        ):
            getattr(self, name).set(float("nan"))
        self._invalidate_production()
        self.health_status.set(0)
        self.sync_status.set(0)
        self.reference_valid.set(0)
        self.collection_success.set(0)
        self._stake_state = "unknown"

    def _invalidate_production(self):
        """Do not convert unavailable production data to zero misses."""
        for name in (
            "missed_slots",
            "leader_slots",
            "blocks_produced",
            "skip_ratio",
            "block_production_success",
            "production_first_slot",
            "production_last_slot",
            "production_epoch",
        ):
            getattr(self, name).set(float("nan"))
        self.production_data_valid.set(0)

    def _read(self, requests, public=False):
        """Return ID-correlated successful results keyed by their local request names."""
        responses = send_rpc(
            self.public_rpc_url if public else self.rpc_url,
            [request for _, request in requests],
            logger=self.logger,
        )
        return {name: response.result for (name, _), response in zip(requests, responses) if response.is_successful()}

    @staticmethod
    def _valid_epoch(value):
        """Require an internally consistent epoch snapshot before deriving slot bounds."""
        return (
            isinstance(value, dict)
            and all(type(value.get(key)) is int for key in ("epoch", "absoluteSlot", "slotIndex", "slotsInEpoch"))
            and value["epoch"] >= 0
            and value["absoluteSlot"] >= value["slotIndex"]
            and 0 <= value["slotIndex"] < value["slotsInEpoch"]
        )

    def collect_metrics(self):
        """Monitor the local node, using an independent RPC only as a cluster reference."""
        self._invalidate_poll()
        try:
            self._collect_metrics()
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            self.logger.error("Malformed collection data: %s", type(error).__name__)
            self._invalidate_poll()
            self._update_validator_info(None)
            self.sfdp.update(None, None, None)

    def _collect_metrics(self):
        """Collect independent local observations and cluster reference data."""
        bootstrap = self._read(
            [
                ("identity", JsonRPCRequest("getIdentity")),
                ("version", JsonRPCRequest("getVersion")),
                ("genesis", JsonRPCRequest("getGenesisHash")),
                ("epoch", JsonRPCRequest("getEpochInfo", [{"commitment": "finalized"}])),
                ("performance", JsonRPCRequest("getRecentPerformanceSamples", [10])),
            ]
        )
        identity_result = bootstrap.get("identity")
        identity = identity_result.get("identity") if isinstance(identity_result, dict) else None
        if not isinstance(identity, str) or not identity:
            identity = None
        epoch = bootstrap.get("epoch")
        epoch = epoch if self._valid_epoch(epoch) else None
        self._current_epoch = epoch["epoch"] if epoch else None
        self._production_range = (
            {"firstSlot": epoch["absoluteSlot"] - epoch["slotIndex"], "lastSlot": epoch["absoluteSlot"]}
            if epoch
            else None
        )
        if epoch:
            self._update_epoch_metrics(epoch)
        version = bootstrap.get("version")
        self._observed_version = version.get("solana-core") if isinstance(version, dict) else None
        if not isinstance(self._observed_version, str):
            self._observed_version = None
        self._update_build_info()
        cluster_by_genesis = {
            "4uhcVJyU9pJkvQyS88uRDiswHXSCkY3zQawwpjk2NsNY": "testnet",
            "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d": "mainnet-beta",
        }
        genesis = bootstrap.get("genesis")
        actual_cluster = cluster_by_genesis.get(genesis, "unknown") if isinstance(genesis, str) else "unknown"
        configured_cluster = getattr(self.config, "cluster", None)
        self.sfdp.cluster = (
            actual_cluster if not configured_cluster or configured_cluster == actual_cluster else "unknown"
        )
        self.sfdp.update(version, epoch, bootstrap.get("performance"))

        commitment = {"commitment": "finalized"}
        requests = [
            ("slot", JsonRPCRequest("getSlot", [commitment])),
            ("vote_balance", JsonRPCRequest("getBalance", [self.config.vote_pubkey, commitment])),
            (
                "votes",
                JsonRPCRequest(
                    "getVoteAccounts",
                    [{**commitment, "votePubkey": self.config.vote_pubkey, "keepUnstakedDelinquents": True}],
                ),
            ),
            ("health", JsonRPCRequest("getHealth")),
        ]
        if identity:
            requests.append(("balance", JsonRPCRequest("getBalance", [identity, commitment])))
        if getattr(self.config, "double_zero_fees_address", None):
            requests.append(
                ("double_zero", JsonRPCRequest("getBalance", [self.config.double_zero_fees_address, commitment]))
            )
        if identity and epoch:
            requests.append(
                (
                    "production",
                    JsonRPCRequest(
                        "getBlockProduction",
                        [
                            {
                                **commitment,
                                "identity": identity,
                                "range": self._production_range,
                            }
                        ],
                    ),
                )
            )
            schedule_key = (bootstrap.get("genesis"), epoch["epoch"], identity)
            if self._schedule_key != schedule_key:
                self._schedule = None
            if self._schedule is None:
                requests.append(
                    (
                        "schedule",
                        JsonRPCRequest(
                            "getLeaderSchedule",
                            [
                                self._production_range["firstSlot"],
                                {**commitment, "identity": identity},
                            ],
                        ),
                    )
                )
                self._schedule_key = schedule_key
        results = self._read(requests)
        slot = results.get("slot")
        if type(slot) is int and slot >= 0:
            self._update_slot_metrics(slot)
        local_snapshot_valid = bool(identity and epoch and type(slot) is int and slot >= 0)
        self.health_status.set(1 if results.get("health") == "ok" and local_snapshot_valid else 0)
        for key, gauge in (
            ("balance", self.balance),
            ("vote_balance", self.vote_account_balance),
            ("double_zero", self.double_zero_balance),
        ):
            result = results.get(key)
            if isinstance(result, dict) and type(result.get("value")) is int and result["value"] >= 0:
                gauge.set(result["value"] / 1_000_000_000)
        if identity and epoch:
            self._update_block_production_metrics(results.get("production"), identity)
            if isinstance(results.get("schedule"), dict):
                slots = results["schedule"].get(identity, [])
                if isinstance(slots, list) and all(type(i) is int and 0 <= i < epoch["slotsInEpoch"] for i in slots):
                    self._schedule = set(slots)
            if self._schedule is not None:
                self.leader_status.set(int(epoch["slotIndex"] in self._schedule))

        reference = self._read(
            [
                ("genesis", JsonRPCRequest("getGenesisHash")),
                ("slot", JsonRPCRequest("getSlot", [commitment])),
            ],
            public=True,
        )
        reference_ok = (
            isinstance(genesis, str)
            and bool(genesis)
            and isinstance(reference.get("genesis"), str)
            and reference["genesis"] == genesis
            and self.public_rpc_url != self.rpc_url
        )
        self.reference_valid.set(int(reference_ok))
        if reference_ok and type(slot) is int and type(reference.get("slot")) is int:
            self._update_slot_lag_and_sync_status(slot, reference["slot"])

        if not reference_ok:
            self.stake_accounts = []
        self.programAccountsCallCounter += 1
        if self.programAccountsCallCounter % 5 == 0:
            self.stake_accounts = self._get_stake_accounts() if reference_ok else []
            if self._has_jpool_bond:
                if reference_ok:
                    self._get_jpool_bond_balance()
                else:
                    self.jpool_bond_balance.set(float("nan"))
        votes = results.get("votes")
        votes = votes if self._valid_votes(votes) else None
        self._update_stake_metrics(votes)
        self._update_vote_distance(votes, epoch)
        self._update_credits_earned(votes)
        if isinstance(votes, dict) and identity:
            for account in votes.get("current", []) + votes.get("delinquent", []):
                if isinstance(account, dict) and account.get("votePubkey") == self.config.vote_pubkey:
                    if isinstance(account.get("nodePubkey"), str):
                        self.vote_identity_match.set(int(account["nodePubkey"] == identity))
                    break
        self._update_validator_info(identity)
        if (
            local_snapshot_valid
            and votes is not None
            and self.production_data_valid._value.get()
            and self.health_status._value.get() == 1
        ):
            self.collection_success.set(1)
            self.collection_last_success.set(time.time())

    def _get_identity_role(self, identity_pubkey: Optional[str]) -> str:
        """Classify active identity key as staked|unstaked|unknown (primary vs backup)."""
        if not identity_pubkey:
            return "unknown"
        if self.staked_identity_pubkey and identity_pubkey == self.staked_identity_pubkey:
            return "staked"
        if self.unstaked_identity_pubkey and identity_pubkey == self.unstaked_identity_pubkey:
            return "unstaked"
        return "unknown"

    def _update_validator_info(self, identity_pubkey: Optional[str]) -> None:
        """Expose a stable info series with identity role and hostname."""
        labelvalues: tuple[str, str, str, str, str, str] = (
            self.hostname,
            self._stake_state,
            self._get_identity_role(identity_pubkey),
            identity_pubkey or "unknown",
            self.config.vote_pubkey,
            str(self.config.label),
        )
        if self._last_validator_info_labelvalues and self._last_validator_info_labelvalues != labelvalues:
            try:
                self.validator_info.remove(*self._last_validator_info_labelvalues)
            except KeyError:
                pass
        self.validator_info.labels(*labelvalues).set(1)
        self._last_validator_info_labelvalues = labelvalues

    def _update_slot_lag_and_sync_status(self, slot_value, absolute_slot_value):
        """Compare finalized local and independent reference slots without overriding failed health."""
        slot_lag = max(0, absolute_slot_value - slot_value)
        self.slot_lag.set(slot_lag)
        self.sync_status.set(1 if slot_lag <= 64 and self.health_status._value.get() == 1 else 0)
        self.logger.debug("Updated reference slot lag: %s", slot_lag)

    @staticmethod
    def _valid_votes(value):
        """Validate vote lists before any stake, credit, or identity interpretation."""
        if not isinstance(value, dict):
            return False
        for category in ("current", "delinquent"):
            accounts = value.get(category)
            if not isinstance(accounts, list):
                return False
            for account in accounts:
                if (
                    not isinstance(account, dict)
                    or not isinstance(account.get("votePubkey"), str)
                    or type(account.get("activatedStake")) is not int
                    or account["activatedStake"] < 0
                ):
                    return False
        return True

    def _update_vote_distance(self, vote_accounts_result, epoch_info_result):
        """Measure vote distance for current or delinquent accounts; absence is unknown."""
        self.vote_distance.set(float("nan"))
        if not isinstance(vote_accounts_result, dict) or not epoch_info_result:
            return
        for account in vote_accounts_result.get("current", []) + vote_accounts_result.get("delinquent", []):
            if isinstance(account, dict) and account.get("votePubkey") == self.config.vote_pubkey:
                last_vote = account.get("lastVote")
                if type(last_vote) is int and last_vote > 0:
                    self.vote_distance.set(max(0, epoch_info_result["absoluteSlot"] - last_vote))
                return

    def _update_slot_metrics(self, current_slot):
        """Update slot-related metrics."""
        self.slot_number.set(current_slot)
        self.logger.debug(f"Updated slot number: {current_slot}")

    def _get_stake_accounts(self) -> List[JsonRPCResponse]:
        """Query stake accounts using the public RPC endpoint."""
        program_id = "Stake11111111111111111111111111111111111111"
        filters = [
            {"dataSize": 200},
            {
                "memcmp": {
                    "offset": 124,
                    "bytes": self.config.vote_pubkey,
                }
            },
        ]
        request = JsonRPCRequest(
            method="getProgramAccounts",
            params=[
                program_id,
                {"filters": filters, "encoding": "base64"},
            ],
        )

        responses: List[JsonRPCResponse] = send_rpc(
            rpc_url=self.public_rpc_url, rpc_requests=request, logger=self.logger
        )

        accounts = []
        for response in responses:
            if response.is_successful() and isinstance(response.result, list):
                accounts.append(response)

        if not accounts:
            self.logger.error("Failed to fetch stake accounts: All responses failed")
        else:
            self.logger.debug(f"Retrieved stake accounts: {accounts}")
        return accounts

    def _update_stake_metrics(self, vote_accounts) -> None:
        """Include delinquent stake while preserving unknown results on RPC failure."""
        self._stake_state = "unknown"
        for gauge in (self.total_delegated_stake, self.delinquent_stake, self.pending_stake):
            gauge.set(float("nan"))
        if not self._valid_votes(vote_accounts):
            return
        current, delinquent = vote_accounts.get("current"), vote_accounts.get("delinquent")
        if not isinstance(current, list) or not isinstance(delinquent, list):
            return
        accounts = [
            a for a in current + delinquent if isinstance(a, dict) and a.get("votePubkey") == self.config.vote_pubkey
        ]
        if any(type(a.get("activatedStake")) is not int or a["activatedStake"] < 0 for a in accounts):
            return
        total_stake = sum(a["activatedStake"] for a in accounts) / 1_000_000_000
        self.total_delegated_stake.set(total_stake)
        self.delinquent_stake.set(sum(a["activatedStake"] for a in accounts if a in delinquent) / 1_000_000_000)
        self._stake_state = "staked" if total_stake > 0 else "unstaked"
        # Legacy estimate includes rent and inactive balances; never use it for admission decisions.
        if self.stake_accounts:
            balances = [
                a.get("account", {}).get("lamports") for a in self.stake_accounts[0].result if isinstance(a, dict)
            ]
            if all(type(balance) is int and balance >= 0 for balance in balances):
                self.pending_stake.set(max(0, sum(balances) / 1_000_000_000 - total_stake))

    def _update_epoch_metrics(self, epoch_info):
        """Update metrics related to epoch and slot time."""
        current_absolute_slot = epoch_info.get("absoluteSlot", 0)
        current_timestamp = time.monotonic()

        self.epoch.set(epoch_info.get("epoch", 0))

        if self.last_absolute_slot is not None and self.last_timestamp is not None:
            elapsed_time = current_timestamp - self.last_timestamp
            slots_processed = current_absolute_slot - self.last_absolute_slot

            if elapsed_time > 0 and slots_processed > 0:
                slots_per_second = slots_processed / elapsed_time
                self.slot_time.set(1 / slots_per_second)
                self.logger.debug(f"Updated slot time: {1 / slots_per_second}, slots_per_second: {slots_per_second}")
            else:
                self.slot_time.set(float("nan"))

        self.last_absolute_slot = current_absolute_slot
        self.absolute_slot_number.set(self.last_absolute_slot)
        self.last_timestamp = current_timestamp

    def _update_block_production_metrics(self, block_production_data, identity_pubkey: str):
        """Validate finalized range counts before exposing epoch gauges and their ratios."""
        self._invalidate_production()
        value = block_production_data.get("value") if isinstance(block_production_data, dict) else None
        if not isinstance(value, dict) or not isinstance(value.get("byIdentity"), dict):
            return
        bounds = value.get("range")
        if not isinstance(bounds, dict) or any(type(bounds.get(k)) is not int for k in ("firstSlot", "lastSlot")):
            return
        if bounds["firstSlot"] < 0 or bounds["lastSlot"] < bounds["firstSlot"]:
            return
        if self._production_range is not None and bounds != self._production_range:
            return
        stats = value["byIdentity"].get(identity_pubkey, [0, 0])
        if not isinstance(stats, (list, tuple)) or len(stats) != 2 or any(type(v) is not int for v in stats):
            return
        leaders, produced = stats
        if not 0 <= produced <= leaders <= bounds["lastSlot"] - bounds["firstSlot"] + 1:
            return
        self.leader_slots.set(leaders)
        self.blocks_produced.set(produced)
        self.missed_slots.set(leaders - produced)
        self.skip_ratio.set((leaders - produced) / leaders if leaders else float("nan"))
        self.block_production_success.set(produced / leaders if leaders else float("nan"))
        self.production_first_slot.set(bounds["firstSlot"])
        self.production_last_slot.set(bounds["lastSlot"])
        self.production_epoch.set(self._current_epoch if self._current_epoch is not None else float("nan"))
        self.production_data_valid.set(1)
        self.production_last_success.set(time.time())

    def _update_credits_earned(self, vote_accounts_result) -> None:
        """Expose credits for the current observed epoch, including delinquent accounts."""
        self.credits_earned.set(float("nan"))
        if not isinstance(vote_accounts_result, dict) or self._current_epoch is None:
            return
        for account in vote_accounts_result.get("current", []) + vote_accounts_result.get("delinquent", []):
            if isinstance(account, dict) and account.get("votePubkey") == self.config.vote_pubkey:
                for credit in account.get("epochCredits", []):
                    if isinstance(credit, (list, tuple)) and len(credit) == 3 and all(type(v) is int for v in credit):
                        if credit[0] == self._current_epoch and credit[1] >= credit[2] >= 0:
                            self.credits_earned.set(credit[1] - credit[2])
                            return
                self.credits_earned.set(0)
                return

    def _get_jpool_bond_balance(self) -> None:
        """Query and update the JPool bond balance from on-chain stake accounts.

        Bond-funded stake accounts are identified by their withdrawal authority
        (the bonds_withdrawer_authority PDA) and voter (the validator's vote account).
        Stake account layout offsets: withdrawer at byte 44, voter at byte 124.
        """
        program_id = "Stake11111111111111111111111111111111111111"
        filters = [
            {"dataSize": 200},
            {
                "memcmp": {
                    "offset": 44,
                    "bytes": self.config.jpool_bond_withdrawer_authority,
                }
            },
            {
                "memcmp": {
                    "offset": 124,
                    "bytes": self.config.vote_pubkey,
                }
            },
        ]
        request = JsonRPCRequest(
            method="getProgramAccounts",
            params=[
                program_id,
                {"filters": filters, "encoding": "base64"},
            ],
        )

        responses: List[JsonRPCResponse] = send_rpc(
            rpc_url=self.public_rpc_url, rpc_requests=request, logger=self.logger
        )

        self.jpool_bond_balance.set(float("nan"))
        if len(responses) != 1 or not responses[0].is_successful() or not isinstance(responses[0].result, list):
            return
        total_lamports = 0
        for account in responses[0].result:
            value = account.get("account", {}).get("lamports") if isinstance(account, dict) else None
            if type(value) is not int or value < 0:
                return
            total_lamports += value
        self.jpool_bond_balance.set(total_lamports / 1_000_000_000)

    def _update_build_info(self) -> None:
        """Update build information with version and label as string values."""
        build_info_data = {"version": self._observed_version or "unknown", "label": str(self.config.label)}
        self.build_info.info(build_info_data)
        self.logger.debug(f"Updated build info: {build_info_data}")


if __name__ == "__main__":
    configFile: str | None = os.getenv("EXPORTER_ENV")
    print(f"starting solana exporter -- config {configFile}")
    exporter = SolanaExporter(config_source="fromFile", config_file=configFile)
    exporter.start_exporter()
