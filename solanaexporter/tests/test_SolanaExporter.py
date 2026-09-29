"""Regression tests for local validator collection and optional bond monitoring."""

import math

import pytest

from solanaexporter.solanaExporter import SolanaExporter


def test_collection_uses_local_identity_and_reference(node, rpc_mock):
    """Follow local failover identity, preserve vote attribution, and use true cluster lag."""
    exporter = SolanaExporter("fromEnv")
    exporter.collect_metrics()
    assert exporter.missed_slots._value.get() == 5
    assert exporter.leader_slots._value.get() == 8
    assert exporter.blocks_produced._value.get() == 3
    assert exporter.skip_ratio._value.get() == 5 / 8
    assert exporter.block_production_success._value.get() == 3 / 8
    assert exporter.slot_lag._value.get() == 5
    assert exporter.vote_distance._value.get() == 1
    assert exporter.vote_account_balance._value.get() == 2
    assert exporter.credits_earned._value.get() == 20
    assert exporter.build_info._value["version"] == "4.3.0"
    for call in rpc_mock[1].call_args_list:
        payload = call.kwargs["json"]
        for request in payload if isinstance(payload, list) else [payload]:
            if request["method"] == "getBlockProduction":
                assert request["params"] == [
                    {"identity": "ACTIVE", "commitment": "finalized", "range": {"firstSlot": 1000, "lastSlot": 1010}}
                ]


def test_production_failure_and_malformed_payload_do_not_create_zero(node, rpc_mock):
    """Unchanged recovered cumulative misses must never pass through a fabricated zero."""
    exporter = SolanaExporter("fromEnv")
    exporter.collect_metrics()
    assert exporter.missed_slots._value.get() == 5
    rpc_mock[0]["fail"].add("getBlockProduction")
    exporter.collect_metrics()
    assert math.isnan(exporter.missed_slots._value.get())
    assert exporter.production_data_valid._value.get() == 0
    rpc_mock[0]["fail"].clear()
    rpc_mock[0]["raw_production"] = {}
    exporter.collect_metrics()
    assert math.isnan(exporter.missed_slots._value.get())
    del rpc_mock[0]["raw_production"]
    exporter.collect_metrics()
    assert exporter.missed_slots._value.get() == 5


def test_stalled_or_backwards_slot_is_safe(node, rpc_mock):
    """A stalled or reset node continues exporting data without division by zero."""
    exporter = SolanaExporter("fromEnv")
    exporter.collect_metrics()
    exporter.collect_metrics()
    assert math.isnan(exporter.slot_time._value.get())
    assert exporter.missed_slots._value.get() == 5
    rpc_mock[0]["slot"] -= 1
    exporter.collect_metrics()
    assert math.isnan(exporter.slot_time._value.get())


def test_missing_identity_does_not_use_configured_fallback(node, rpc_mock):
    """An unavailable local identity cannot silently select another configured validator."""
    rpc_mock[0]["fail"].add("getIdentity")
    exporter = SolanaExporter("fromEnv")
    exporter.collect_metrics()
    assert math.isnan(exporter.missed_slots._value.get())
    assert math.isnan(exporter.balance._value.get())


def test_delinquent_vote_account_and_health_error(node, rpc_mock):
    """Delinquency retains delegated stake and vote distance, while health failure stays unhealthy."""
    rpc_mock[0]["votes"] = {
        "current": [],
        "delinquent": [
            {"votePubkey": "VOTE", "activatedStake": 7_000_000_000, "lastVote": 900, "epochCredits": [[20, 100, 70]]}
        ],
    }
    rpc_mock[0]["fail"].add("getHealth")
    exporter = SolanaExporter("fromEnv")
    exporter.collect_metrics()
    assert exporter.total_delegated_stake._value.get() == 7
    assert exporter.delinquent_stake._value.get() == 7
    assert exporter.vote_distance._value.get() == 110
    assert exporter.sync_status._value.get() == 0


def test_reference_cluster_mismatch(node, rpc_mock):
    """A cross-cluster reference cannot produce a seemingly healthy lag value."""
    rpc_mock[0]["wrong_cluster"] = True
    exporter = SolanaExporter("fromEnv")
    exporter.collect_metrics()
    assert math.isnan(exporter.slot_lag._value.get())
    assert exporter.sync_status._value.get() == 0


def test_stake_scan_cadence(node, rpc_mock):
    """Heavy scans run at startup and every fifth subsequent poll."""
    exporter = SolanaExporter("fromEnv")
    for _ in range(11):
        exporter.collect_metrics()
    assert rpc_mock[0]["scans"] == 3


def test_jpool_optional_and_failed_scan(node, rpc_mock, monkeypatch):
    """Keep the bond feature opt-in and never publish a failed scan as an empty bond."""
    assert not hasattr(SolanaExporter("fromEnv"), "jpool_bond_balance")
    monkeypatch.setenv("JPOOL_BOND_WITHDRAWER_AUTHORITY", "WITHDRAWER")
    exporter = SolanaExporter("fromEnv")
    exporter.collect_metrics()
    assert exporter.jpool_bond_balance._value.get() == 0
    rpc_mock[0]["fail"].add("getProgramAccounts")
    exporter._get_jpool_bond_balance()
    assert math.isnan(exporter.jpool_bond_balance._value.get())
    payload = rpc_mock[1].call_args.kwargs["json"]
    filters = payload["params"][1]["filters"]
    assert {"memcmp": {"offset": 44, "bytes": "WITHDRAWER"}} in filters
    assert {"memcmp": {"offset": 124, "bytes": "VOTE"}} in filters


@pytest.mark.parametrize("stats", [[1, 2], [-1, 0], [True, 0], [1], "bad"])
def test_invalid_production_counts(node, stats):
    """Impossible and malformed production counts are unavailable, never negative misses."""
    exporter = SolanaExporter("fromEnv")
    exporter._update_block_production_metrics(
        {"value": {"byIdentity": {"ACTIVE": stats}, "range": {"firstSlot": 1, "lastSlot": 10}}}, "ACTIVE"
    )
    assert math.isnan(exporter.missed_slots._value.get())


def test_empty_valid_production_means_no_opportunities(node):
    """A validated empty identity map means zero opportunities and an undefined ratio."""
    exporter = SolanaExporter("fromEnv")
    exporter._update_block_production_metrics(
        {"value": {"byIdentity": {}, "range": {"firstSlot": 1, "lastSlot": 10}}}, "ACTIVE"
    )
    assert exporter.missed_slots._value.get() == 0
    assert exporter.leader_slots._value.get() == 0
    assert math.isnan(exporter.skip_ratio._value.get())


def test_schedule_retry_after_rpc_failure(node, rpc_mock):
    """An unavailable leader schedule is retried within the same epoch."""
    rpc_mock[0]["fail"].add("getLeaderSchedule")
    exporter = SolanaExporter("fromEnv")
    exporter.collect_metrics()
    assert math.isnan(exporter.leader_status._value.get())
    rpc_mock[0]["fail"].clear()
    exporter.collect_metrics()
    assert exporter.leader_status._value.get() == 1


@pytest.mark.parametrize("method", ["getIdentity", "getEpochInfo", "getSlot"])
def test_health_needs_essential_local_snapshot(node, rpc_mock, method):
    """A successful health endpoint cannot hide an unavailable essential snapshot."""
    rpc_mock[0]["fail"].add(method)
    exporter = SolanaExporter("fromEnv")
    exporter.collect_metrics()
    assert exporter.health_status._value.get() == 0
    assert exporter.sync_status._value.get() == 0


@pytest.mark.parametrize("member", [None, {}, {"votePubkey": "VOTE", "activatedStake": "bad"}])
def test_invalid_vote_members_are_unknown(node, rpc_mock, member):
    """Malformed vote data cannot turn a staked validator into an unstaked label."""
    rpc_mock[0]["votes"] = {"current": [member], "delinquent": []}
    exporter = SolanaExporter("fromEnv")
    exporter.collect_metrics()
    assert math.isnan(exporter.total_delegated_stake._value.get())
    assert exporter._stake_state == "unknown"
    assert exporter.missed_slots._value.get() == 5
    assert exporter.collection_success._value.get() == 0


@pytest.mark.parametrize("genesis", [True, 42, {}, [], ""])
def test_malformed_genesis_never_validates_reference(node, rpc_mock, genesis):
    """Matching malformed genesis replies must not establish cluster identity."""
    from unittest.mock import patch

    import solanaexporter.solanaExporter as collector

    original = collector.send_rpc

    def malformed_genesis(rpc_url, rpc_requests, logger=None):
        responses = original(rpc_url, rpc_requests, logger=logger)
        requests = rpc_requests if isinstance(rpc_requests, list) else [rpc_requests]
        for request, response in zip(requests, responses):
            if request.method == "getGenesisHash":
                response.result = genesis
        return responses

    with patch.object(collector, "send_rpc", side_effect=malformed_genesis):
        exporter = SolanaExporter("fromEnv")
        exporter.collect_metrics()
    assert exporter.reference_valid._value.get() == 0
    assert math.isnan(exporter.slot_lag._value.get())
    assert exporter.sync_status._value.get() == 0
