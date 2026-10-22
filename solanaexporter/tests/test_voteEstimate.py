"""Tests for the credit-based Alpenglow vote estimate."""

import base64
import math
import struct
from unittest.mock import patch

import pytest
import requests
import requests_mock

from solanaexporter.voteEstimate import VotesLast300Slots

VOTE = "vote-account"
IDENTITY = "validator-identity"
EPOCH = 7
START = 1_000_000
SLOTS = 432_000
LOCAL = "http://localhost:8899"
PUBLIC = "https://public.example/rpc"


def inputs(index, credits=None, epoch=EPOCH, status="current", identity=IDENTITY, stake=100):
    """Return a local poll with an explicit epoch and credit amount."""
    account = {
        "votePubkey": VOTE,
        "nodePubkey": identity,
        "activatedStake": stake,
        "epochVoteAccount": True,
        "epochCredits": [[epoch, index * 10 if credits is None else credits, 0]],
    }
    votes = {"current": [], "delinquent": []}
    votes[status].append(account)
    info = {
        "absoluteSlot": START + (epoch - EPOCH) * SLOTS + index,
        "slotIndex": index,
        "slotsInEpoch": SLOTS,
        "epoch": epoch,
    }
    return votes, info


def observe(estimator, index, credits=None, schedule=None, local_genesis=None, **kwargs):
    """Feed one existing local poll into the estimate."""
    votes, info = inputs(index, credits, **kwargs)
    return estimator.observe(
        votes,
        info,
        {} if schedule is None else schedule,
        IDENTITY,
        local_genesis=local_genesis,
    )


@pytest.fixture
def estimator():
    """Use a fixed independently supplied reward rate for window tests."""
    result = VotesLast300Slots(VOTE, LOCAL, PUBLIC)
    with patch.object(result, "_credits_per_vote", return_value=10):
        yield result


def test_full_window_and_missing_reward_inclusions(estimator):
    for index in (10, 110, 210):
        assert math.isnan(observe(estimator, index))
    assert observe(estimator, 310, 3050) == 295


def test_partial_boundary_uses_interpolation(estimator):
    for index, credits in ((10, 0), (110, 500), (210, 1500), (310, 2500)):
        observe(estimator, index, credits)
    assert observe(estimator, 360, 3000) == 275


def test_zero_credits_is_zero_votes(estimator):
    for index in (10, 110, 210):
        observe(estimator, index, 100, status="delinquent")
    assert observe(estimator, 310, 100, status="delinquent") == 0


def test_four_leader_slots_count_zero_without_resetting_window(estimator):
    schedule = {IDENTITY: [150, 151, 152, 153]}
    for index in (10, 110, 210):
        credits = index * 10 + (1_000_000 if index >= 150 else 0)
        assert math.isnan(observe(estimator, index, credits, schedule=schedule))
    assert observe(estimator, 310, 1_003_100, schedule=schedule) == 296
    assert observe(estimator, 360, 1_003_600, schedule=schedule) == 296
    # Cutoff=151 excludes leaders 150 and 151 but still contains 152 and 153.
    assert observe(estimator, 451, 1_004_510, schedule=schedule) == 298
    assert observe(estimator, 460, 1_004_600, schedule=schedule) == 300


def test_leader_interval_keeps_ordinary_slots_in_sparse_poll(estimator):
    schedule = {IDENTITY: [200, 201, 202, 203]}
    observe(estimator, 10, schedule=schedule)
    observe(estimator, 110, schedule=schedule)
    # One 200-slot poll interval mixes ordinary participation with a large leader bonus.
    assert observe(estimator, 310, 1_003_100, schedule=schedule) == 296


def test_mixed_poll_counts_credit_quanta_capped_at_ordinary_slots(estimator):
    schedule = {IDENTITY: [150, 151, 152, 153]}
    observe(estimator, 10, 0, schedule=schedule)
    observe(estimator, 110, 500, schedule=schedule)  # 50% clean participation.
    observe(estimator, 210, 1_001_000, schedule=schedule)
    # First poll counts 50; the bonus-filled mixed poll is capped at 96; the final poll counts 100.
    assert observe(estimator, 310, 1_002_000, schedule=schedule) == 246


def test_leader_bonus_does_not_hide_clean_interval_outage(estimator):
    schedule = {IDENTITY: [150, 151, 152, 153]}
    observe(estimator, 10, schedule=schedule)
    observe(estimator, 110, schedule=schedule)
    observe(estimator, 210, 1_002_100, schedule=schedule)
    # 51 missing credited votes in the final clean interval are retained despite the earlier bonus.
    assert observe(estimator, 310, 1_002_590, schedule=schedule) == 245


def test_alert_boundary_for_fifty_missed_estimated_votes(estimator):
    for index in (10, 110, 210):
        observe(estimator, index)
    assert observe(estimator, 310, 2600) == 250
    assert observe(estimator, 320, 2690) < 250


def test_leader_outside_partial_window_does_not_invalidate_ordinary_slots(estimator):
    for index in (10, 110, 210):
        observe(estimator, index)
    # The anchor includes leader slot 40, but the visible tail contains 300 ordinary slots.
    assert observe(estimator, 360, schedule={IDENTITY: [40]}) == 300
    assert observe(estimator, 410, schedule={IDENTITY: [40]}) == 300


def test_expected_credit_quantum_counts_one_vote_with_remainder(estimator):
    observe(estimator, 10, 0)
    observe(estimator, 11, 9)
    observe(estimator, 12, 10)
    for index in (110, 210):
        observe(estimator, index, 10)
    assert observe(estimator, 310, 10) == 1


def test_leader_interval_discards_prior_credit_remainder(estimator):
    schedule = {IDENTITY: [20]}
    for index, credits in ((10, 0), (11, 9), (21, 10), (110, 10), (210, 10)):
        observe(estimator, index, credits, schedule=schedule)
    assert observe(estimator, 310, 10, schedule=schedule) == 0


def test_leader_bonus_remainder_cannot_credit_following_clean_slots(estimator):
    schedule = {IDENTITY: [20, 21]}
    for index, credits in ((10, 0), (40, 1_000_009), (50, 1_000_010), (110, 1_000_010), (210, 1_000_010)):
        observe(estimator, index, credits, schedule=schedule)
    assert observe(estimator, 310, 1_000_010, schedule=schedule) == 28


def test_credit_remainder_survives_window_pruning(estimator):
    for index in range(10, 1011):
        value = observe(estimator, index, (index - 10) * 9)
        if index >= 310:
            assert value == 270


def test_leader_slots_remain_zero_with_small_reference_bias(estimator):
    schedule = {IDENTITY: [150, 151, 152, 153]}
    with patch.object(estimator, "_credits_per_vote", return_value=9.9):
        for index in (10, 110, 210):
            observe(estimator, index, index * 10 + (1_000_000 if index >= 150 else 0), schedule=schedule)
        assert observe(estimator, 310, 1_003_100, schedule=schedule) == 296


def test_leader_schedule_uses_vote_account_identity(estimator):
    for index in (10, 110, 210):
        observe(estimator, index)
    assert observe(estimator, 310, schedule={"another-validator": [200]}) == 300
    votes, info = inputs(320)
    assert math.isnan(estimator.observe(votes, info, {}, "backup-identity"))
    assert math.isnan(observe(estimator, 330))


@pytest.mark.parametrize("next_index,next_credits", [(210, 2100), (205, 2050), (510, 5100), (220, 100)])
def test_stall_backward_gap_and_credit_regression_reset(estimator, next_index, next_credits):
    for index in (10, 110, 210):
        observe(estimator, index)
    assert math.isnan(observe(estimator, next_index, next_credits))
    assert math.isnan(observe(estimator, next_index + 10, next_credits + 100))


def test_epoch_and_stake_change_reset(estimator):
    for index in (10, 110, 210, 310):
        observe(estimator, index)
    assert math.isnan(observe(estimator, 320, epoch=EPOCH + 1))
    assert math.isnan(observe(estimator, 330, epoch=EPOCH + 1, stake=101))


@pytest.mark.parametrize("index", [0, 1, 7])
def test_epoch_processing_margin(estimator, index):
    assert math.isnan(observe(estimator, index))
    assert math.isnan(observe(estimator, 308))


def test_small_reference_bias_saturates_but_large_excess_is_unknown(estimator):
    for index in (10, 110, 210):
        observe(estimator, index)
    assert observe(estimator, 310, 3110) == 300
    assert math.isnan(observe(estimator, 410, 7000))


@pytest.mark.parametrize("bad", [None, {}, {"current": [], "delinquent": []}])
def test_missing_data_invalidates_previous_value(estimator, bad):
    for index in (10, 110, 210, 310):
        observe(estimator, index)
    _, info = inputs(320)
    assert math.isnan(estimator.observe(bad, info, {}, IDENTITY))
    assert math.isnan(observe(estimator, 330))


@pytest.mark.parametrize("field,value", [("epoch", True), ("slotIndex", -1), ("slotsInEpoch", 0)])
def test_invalid_epoch_info(estimator, field, value):
    votes, info = inputs(10)
    info[field] = value
    assert math.isnan(estimator.observe(votes, info, {}, IDENTITY))


@pytest.mark.parametrize(
    "change", ["duplicate", "missing_epoch", "bool_credit", "negative_stake", "bad_schedule", "duplicate_leader"]
)
def test_invalid_account_or_schedule(estimator, change):
    votes, info = inputs(10)
    schedule = {}
    if change == "duplicate":
        votes["delinquent"] = votes["current"]
    elif change == "missing_epoch":
        votes["current"][0]["epochCredits"] = [[EPOCH - 1, 100, 0]]
    elif change == "bool_credit":
        votes["current"][0]["epochCredits"][0][1] = True
    elif change == "negative_stake":
        votes["current"][0]["activatedStake"] = -1
    elif change == "duplicate_leader":
        schedule = {IDENTITY: [100, 100]}
    else:
        schedule = {IDENTITY: [SLOTS]}
    assert math.isnan(estimator.observe(votes, info, schedule, IDENTITY))


def reference_responses(genesis="genesis", budget_epoch=EPOCH, bad_budget=False, budget=86_400_000):
    """Return public reference responses deliberately out of request order."""
    data = struct.pack("<QQQBQQQ", budget, SLOTS, budget_epoch, 1, 80_000_000, SLOTS, EPOCH - 1)
    if bad_budget:
        data += b"unexpected-layout"
    account = {"owner": "11111111111111111111111111111111", "data": [base64.b64encode(data).decode(), "base64"]}
    target = inputs(310)[0]["current"][0]
    peer = dict(target, votePubkey="peer", nodePubkey="peer-identity", activatedStake=900)
    return [
        {"jsonrpc": "2.0", "id": 3, "result": {"current": [target], "delinquent": [peer]}},
        {"jsonrpc": "2.0", "id": 1, "result": genesis},
        {"jsonrpc": "2.0", "id": 2, "result": {"context": {"slot": START + 310}, "value": account}},
    ]


def test_reference_calls_are_cached_and_public_responses_correlated():
    result = VotesLast300Slots(VOTE, LOCAL, PUBLIC)
    with requests_mock.Mocker() as mock:
        mock.post(LOCAL, json=[{"jsonrpc": "2.0", "id": 1, "result": "genesis"}])
        mock.post(PUBLIC, json=reference_responses())
        for index in (10, 110, 210):
            assert math.isnan(observe(result, index))
        assert mock.call_count == 0  # Reference work is deferred until a full window exists.
        assert observe(result, 310) == 300
        assert observe(result, 410) == 300
        assert mock.call_count == 2
        local, public = mock.request_history
        assert [r["method"] for r in local.json()] == ["getGenesisHash"]
        assert [r["method"] for r in public.json()] == ["getGenesisHash", "getAccountInfo", "getVoteAccounts"]
        assert len({r["id"] for r in public.json()}) == 3
        assert public.json()[2]["params"] == [{"commitment": "finalized"}]


def test_supplied_local_genesis_skips_local_reference_rpc():
    result = VotesLast300Slots(VOTE, LOCAL, PUBLIC)
    with requests_mock.Mocker() as mock:
        mock.post(PUBLIC, json=reference_responses(budget=86_400_000))
        for index in (10, 110, 210):
            assert math.isnan(observe(result, index, local_genesis="genesis"))
        assert observe(result, 310, local_genesis="genesis") == 300
        assert mock.call_count == 1
        assert mock.request_history[0].url == PUBLIC


@pytest.mark.parametrize(
    "failure",
    [
        "wrong_cluster",
        "wrong_epoch",
        "bad_layout",
        "http_error",
        "duplicate_id",
        "timeout",
        "rpc_error",
        "stale_context",
    ],
)
def test_reference_failures_back_off_and_recover(failure):
    result = VotesLast300Slots(VOTE, LOCAL, PUBLIC)
    response = reference_responses(
        genesis="wrong" if failure == "wrong_cluster" else "genesis",
        budget_epoch=EPOCH - 1 if failure == "wrong_epoch" else EPOCH,
        bad_budget=failure == "bad_layout",
    )
    if failure == "duplicate_id":
        response[0]["id"] = 1
    elif failure == "rpc_error":
        response[0] = {"id": 3, "error": {"code": -32005, "message": "unavailable"}}
    elif failure == "stale_context":
        response[2]["result"]["context"]["slot"] -= 301
    with requests_mock.Mocker() as mock, patch("solanaexporter.voteEstimate.time.monotonic", return_value=100) as clock:
        mock.post(LOCAL, json=[{"jsonrpc": "2.0", "id": 1, "result": "genesis"}])
        if failure == "timeout":
            mock.post(PUBLIC, exc=requests.Timeout)
        else:
            mock.post(PUBLIC, json=response, status_code=503 if failure == "http_error" else 200)
        for index in (10, 110, 210, 310):
            assert math.isnan(observe(result, index))
        calls = mock.call_count
        assert math.isnan(observe(result, 410))
        assert mock.call_count == calls
        clock.return_value = 401
        mock.post(PUBLIC, json=reference_responses())
        assert observe(result, 510) == 300


@pytest.mark.parametrize(
    "local,public",
    [
        (LOCAL, "http://127.0.0.1:8899"),
        (LOCAL, "http://localhost.:8899"),
        (LOCAL, "http://[::ffff:127.0.0.1]:8899"),
        (LOCAL, "http://[::1]:8899"),
        (LOCAL, "http://0.0.0.0:8899"),
        ("https://rpc.example:8899", "https://RPC.EXAMPLE.:443"),
        ("http://[::ffff:192.0.2.1]:8899", "http://192.0.2.1:8899"),
    ],
)
def test_public_reference_never_uses_local_endpoint(local, public):
    result = VotesLast300Slots(VOTE, local, public)
    with requests_mock.Mocker() as mock:
        for index in (10, 110, 210, 310):
            assert math.isnan(observe(result, index))
        assert mock.call_count == 0


def test_window_storage_is_bounded(estimator):
    for index in range(10, 1010):
        observe(estimator, index)
    assert len(estimator._samples) <= 302


def test_credit_differences_keep_precision_near_u64_limit(estimator):
    base = 2**64 - 10_000
    for index in (10, 110, 210):
        observe(estimator, index, base + (index - 10) * 10)
    assert observe(estimator, 310, base + 3000) == 300


def test_future_leader_margin_does_not_zero_ordinary_slots(estimator):
    for index in (10, 110, 210):
        observe(estimator, index)
    assert observe(estimator, 310, schedule={IDENTITY: [318]}) == 300


def test_new_epoch_reference_failure_never_reuses_old_rate():
    result = VotesLast300Slots(VOTE, LOCAL, PUBLIC)
    with requests_mock.Mocker() as mock, patch("solanaexporter.voteEstimate.time.monotonic", return_value=100) as clock:
        mock.post(LOCAL, json=[{"id": 1, "result": "genesis"}])
        mock.post(PUBLIC, json=reference_responses())
        for index in (10, 110, 210, 310):
            value = observe(result, index)
        assert value == 300
        mock.post(PUBLIC, status_code=503)
        for index in (10, 110, 210, 310):
            assert math.isnan(observe(result, index, epoch=EPOCH + 1))
        assert mock.call_count == 3
        response = reference_responses(budget_epoch=EPOCH + 1)
        response[2]["result"]["context"]["slot"] += SLOTS
        response[2]["result"]["value"]["data"][0] = base64.b64encode(
            struct.pack("<QQQBQQQ", 172_800_000, SLOTS, EPOCH + 1, 1, 86_400_000, SLOTS, EPOCH)
        ).decode()
        response[0]["result"]["current"][0]["epochCredits"] = [[EPOCH + 1, 3100, 0]]
        mock.post(PUBLIC, json=response)
        assert math.isnan(observe(result, 410, epoch=EPOCH + 1))
        assert mock.call_count == 3
        clock.return_value = 401
        assert observe(result, 510, epoch=EPOCH + 1) == 150
        assert mock.call_count == 4  # Local genesis is still cached; only public reference is refreshed.


def test_collector_reuses_existing_poll_and_clears_failed_batch():
    """The collector's existing responses drive the window without more polling."""
    from solanaexporter.solanaExporter import SolanaExporter

    env = {
        "SOLANA_RPC_URL": LOCAL,
        "SOLANA_PUBLIC_RPC_URL": PUBLIC,
        "EXPORTER_PORT": "7896",
        "POLL_INTERVAL": "10",
        "VOTE_PUBKEY": VOTE,
        "VALIDATOR_PUBKEY": IDENTITY,
        "VERSION": "test",
        "LABEL": "test",
    }
    with patch.dict("os.environ", env, clear=True):
        exporter = SolanaExporter(config_source="fromEnv")
    state = {"index": None, "failure": None}

    def read(requests, public=False):
        names = [name for name, _ in requests]
        if public:
            return {"genesis": "genesis", "slot": START + state["index"]}
        if state["failure"] == "bootstrap" and "identity" in names:
            return {}
        if state["failure"] == "epoch" and "epoch" in names:
            return {"identity": {"identity": IDENTITY}, "genesis": "genesis"}
        index = state["index"]
        votes, epoch = inputs(index)
        values = {
            "identity": {"identity": IDENTITY},
            "version": {"solana-core": "test"},
            "genesis": "genesis",
            "epoch": epoch,
            "performance": [],
            "slot": epoch["absoluteSlot"],
            "vote_balance": {"value": 1_000_000_000},
            "votes": votes,
            "health": "ok",
            "balance": {"value": 1_000_000_000},
            "production": {
                "value": {
                    "byIdentity": {IDENTITY: [4, 4]},
                    "range": {"firstSlot": START, "lastSlot": epoch["absoluteSlot"]},
                }
            },
            "schedule": {IDENTITY: []},
        }
        return {name: values[name] for name in names if name in values}

    with (
        patch.object(exporter, "_read", side_effect=read),
        patch.object(exporter, "_get_stake_accounts", return_value=[]),
        patch.object(exporter._vote_estimate, "_credits_per_vote", return_value=10),
    ):
        for index in (10, 110, 210, 310):
            state["index"] = index
            exporter.collect_metrics()
        assert exporter.votes_last_300_slots._value.get() == 300
        assert exporter.health_status._value.get() == 1
        assert exporter.total_delegated_stake._value.get() == 100 / 1e9
        state["failure"] = "bootstrap"
        exporter.collect_metrics()
        assert math.isnan(exporter.votes_last_300_slots._value.get())
        state["failure"] = "epoch"
        exporter.collect_metrics()
        assert math.isnan(exporter.votes_last_300_slots._value.get())


def test_reference_handles_initial_epoch_layout_and_migration_marker():
    result = VotesLast300Slots(VOTE, LOCAL, PUBLIC)
    response = reference_responses()
    account = response[2]["result"]["value"]
    account["data"][0] = base64.b64encode(struct.pack("<QQQB", 86_400_000, SLOTS, EPOCH, 0)).decode()
    target = response[0]["result"]["current"][0]
    target["epochCredits"][:0] = [[EPOCH, 1, 0], [2**64 - 1] * 3]
    with requests_mock.Mocker() as mock:
        mock.post(LOCAL, json=[{"jsonrpc": "2.0", "id": 1, "result": "genesis"}])
        mock.post(PUBLIC, json=response)
        for index in (10, 110, 210):
            observe(result, index)
        assert observe(result, 310) == 300
