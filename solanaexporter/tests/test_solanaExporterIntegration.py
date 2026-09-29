"""Integration tests for production rollover, failover, and metric exposition."""

import math
from unittest.mock import Mock, patch

from prometheus_client import generate_latest

from solanaexporter.solanaExporter import SolanaExporter


def test_epoch_rollover_cannot_retain_previous_misses(node, rpc_mock):
    """A new epoch's validated zero replaces 56 previous-epoch misses."""
    exporter = SolanaExporter("fromEnv")
    exporter._current_epoch = 19
    exporter._update_block_production_metrics(
        {"value": {"byIdentity": {"ACTIVE": [572, 516]}, "range": {"firstSlot": 0, "lastSlot": 999}}}, "ACTIVE"
    )
    assert exporter.missed_slots._value.get() == 56
    rpc_mock[0]["production"] = [4, 4]
    exporter.collect_metrics()
    assert exporter.production_epoch._value.get() == 20
    assert exporter.missed_slots._value.get() == 0
    assert exporter.production_first_slot._value.get() == 1000


def test_wrong_production_range_rejected(node, rpc_mock):
    """A provider response for another epoch is not accepted for this poll."""
    rpc_mock[0]["raw_production"] = {
        "value": {"byIdentity": {"ACTIVE": [572, 516]}, "range": {"firstSlot": 0, "lastSlot": 999}}
    }
    exporter = SolanaExporter("fromEnv")
    exporter.collect_metrics()
    assert math.isnan(exporter.missed_slots._value.get())
    assert exporter.production_data_valid._value.get() == 0


def test_failover_changes_info_and_schedule(node, rpc_mock):
    """Local identity changes evict the previous info series and schedule cache."""
    exporter = SolanaExporter("fromEnv")
    exporter.collect_metrics()
    previous_calls = len(rpc_mock[1].call_args_list)
    rpc_mock[0]["identity"] = "BACKUP"
    exporter.collect_metrics()
    exposition = generate_latest(exporter.registry).decode()
    assert 'identity_pubkey="BACKUP"' in exposition
    assert 'identity_pubkey="ACTIVE"' not in exposition
    assert exporter.vote_identity_match._value.get() == 0
    assert any(
        request["method"] == "getLeaderSchedule"
        for call in rpc_mock[1].call_args_list[previous_calls:]
        for request in (call.kwargs["json"] if isinstance(call.kwargs["json"], list) else [call.kwargs["json"]])
    )


def test_jpool_balance_sum_and_exposition(node, rpc_mock, monkeypatch):
    """Bond balances sum matching stake accounts and are exposed alongside core metrics."""
    monkeypatch.setenv("JPOOL_BOND_WITHDRAWER_AUTHORITY", "WITHDRAWER")
    exporter = SolanaExporter("fromEnv")
    response = Mock(status_code=200)
    response.json.return_value = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": [{"account": {"lamports": 1_250_000_000}}, {"account": {"lamports": 2_750_000_000}}],
    }
    with patch("requests.post", return_value=response):
        exporter._get_jpool_bond_balance()
    assert exporter.jpool_bond_balance._value.get() == 4
    assert b"solana_jpool_bond_balance 4.0" in generate_latest(exporter.registry)


def test_optional_balance_and_version_exposition(node, rpc_mock, monkeypatch):
    """Use the observed node version instead of the stale deployment VERSION label."""
    monkeypatch.setenv("DOUBLE_ZERO_FEES_ADDRESS", "FEE_ACCOUNT")
    monkeypatch.delenv("VERSION")
    exporter = SolanaExporter("fromEnv")
    exporter.collect_metrics()
    assert exporter.double_zero_balance._value.get() == 2
    exposition = generate_latest(exporter.registry).decode()
    assert 'version="4.3.0"' in exposition
    assert "solana_sfdp_upgrade_due_seconds -2.0" in exposition


def test_whole_rpc_outage_is_unavailable(node, rpc_mock):
    """Network failure never leaves previously healthy production values visible."""
    exporter = SolanaExporter("fromEnv")
    exporter.collect_metrics()
    response = Mock(status_code=503)
    with patch("requests.post", return_value=response):
        exporter.collect_metrics()
    assert math.isnan(exporter.missed_slots._value.get())
    assert exporter.health_status._value.get() == 0
    assert exporter.sync_status._value.get() == 0
    assert exporter.collection_success._value.get() == 0
    assert 'version="4.3.0"' not in generate_latest(exporter.registry).decode()


def test_malformed_vote_response_does_not_stop_exporter(node, rpc_mock):
    """Unexpected vote-account shapes cannot terminate the collection loop."""
    rpc_mock[0]["votes"] = {"current": None, "delinquent": []}
    exporter = SolanaExporter("fromEnv")
    exporter.collect_metrics()
    assert exporter.collection_success._value.get() == 0
    assert exporter.missed_slots._value.get() == 5
    assert math.isnan(exporter.total_delegated_stake._value.get())
