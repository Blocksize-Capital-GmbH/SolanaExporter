"""Behavioral tests for the Solana Foundation Delegated Program monitor."""

import math
from unittest.mock import MagicMock, patch

import pytest
import requests
from prometheus_client import CollectorRegistry

from solanaexporter.sfdp import SfdpMonitor

POLICY_URL = "https://api.solana.org/api/community/v1/sfdp_required_versions"


def policy(*rows):
    return {"data": list(rows)}


def row(
    epoch,
    *,
    cluster="testnet",
    agave_min_version="1.18.0",
    agave_max_version=None,
    firedancer_min_version=None,
    firedancer_max_version=None,
    firedancer_full_min_version=None,
    firedancer_full_max_version=None,
):
    value = {"epoch": epoch, "cluster": cluster, "agave_min_version": agave_min_version}
    for key, item in (
        ("agave_max_version", agave_max_version),
        ("firedancer_min_version", firedancer_min_version),
        ("firedancer_max_version", firedancer_max_version),
        ("firedancer_full_min_version", firedancer_full_min_version),
        ("firedancer_full_max_version", firedancer_full_max_version),
    ):
        if item is not None:
            value[key] = item
    return value


def response(payload, status=200):
    result = MagicMock()
    result.status_code = status
    result.json.return_value = payload
    if status != 200:
        result.raise_for_status.side_effect = requests.RequestException("HTTP error")
    return result


def metric(monitor, name, labels=None):
    return monitor.registry.get_sample_value(name, labels or {})


def make_monitor(cluster="testnet", *, client="agave", clock=None):
    kwargs = {"registry": CollectorRegistry(), "cluster": cluster, "client": client}
    if clock is not None:
        kwargs["clock"] = clock
    return SfdpMonitor(**kwargs)


def update(monitor, version="1.18.0", epoch=1042, *, feature_set=None, samples=None):
    version_result = {"version": version}
    if feature_set is not None:
        version_result["feature-set"] = feature_set
    epoch_info = {"epoch": epoch, "slotIndex": 0, "slotsInEpoch": 100}
    monitor.update(version_result, epoch_info, samples or [{"samplePeriodSecs": 40, "numSlots": 100}])


@pytest.mark.parametrize("epoch, expected", [(1041, 40), (1042, 0), (1043, -40)])
def test_signed_due_seconds_use_sample_slot_duration(epoch, expected):
    monitor = make_monitor()
    with patch("requests.get", return_value=response(policy(row(1042, agave_min_version="1.18.1")))) as get:
        update(monitor, version="1.18.0", epoch=epoch)
    get.assert_called_once_with(POLICY_URL, params={"cluster": "testnet"}, timeout=10)
    assert metric(monitor, "solana_sfdp_upgrade_due_seconds") == expected


@pytest.mark.parametrize("version, expected", [("4.10.0", 0), ("4.9.0", 1), ("4.10.0-rc.1", 1), ("4.10.0+build.1", 0)])
def test_versions_use_semver_order_and_prerelease_build_metadata(version, expected):
    monitor = make_monitor()
    with patch("requests.get", return_value=response(policy(row(1042, agave_min_version="4.10.0")))):
        update(monitor, version=version)
    assert metric(monitor, "solana_sfdp_upgrade_required") == expected


@pytest.mark.parametrize(
    "client, version, fields",
    [
        ("agave", "1.18.5", {"agave_min_version": "1.18.0", "agave_max_version": "1.19.0"}),
        ("jito", "1.18.5-jito", {"agave_min_version": "1.18.0", "agave_max_version": "1.19.0"}),
        (
            "frankendancer",
            "0.1.5",
            {"agave_min_version": None, "firedancer_min_version": "0.1.0", "firedancer_max_version": "0.2.0"},
        ),
        (
            "firedancer",
            "0.1.5",
            {"agave_min_version": None, "firedancer_full_min_version": "0.1.0", "firedancer_full_max_version": "0.2.0"},
        ),
    ],
)
def test_client_version_field_mapping(client, version, fields):
    monitor = make_monitor(client=client)
    with patch("requests.get", return_value=response(policy(row(1042, **fields)))):
        update(monitor, version=version)
    assert metric(monitor, "solana_sfdp_upgrade_required") == 0
    assert metric(monitor, "solana_sfdp_version_compliant") == 1


def test_maximum_bound_failure_does_not_mark_upgrade_required():
    monitor = make_monitor()
    with patch(
        "requests.get",
        return_value=response(policy(row(1042, agave_min_version="1.0.0", agave_max_version="2.0.0"))),
    ):
        update(monitor, version="2.0.1")
    assert metric(monitor, "solana_sfdp_upgrade_required") == 0
    assert metric(monitor, "solana_sfdp_version_compliant") == 0


def test_current_epoch_coverage_and_compliance_require_current_policy_record():
    monitor = make_monitor()
    rows = [row(epoch, agave_min_version="1.0.0") for epoch in range(1042, 1046)]
    with patch("requests.get", return_value=response(policy(*rows))):
        update(monitor, version="1.0.0", epoch=1046)
    assert metric(monitor, "solana_sfdp_policy_current_epoch_covered") == 0
    assert math.isnan(metric(monitor, "solana_sfdp_version_compliant"))


def test_missing_version_and_zero_valid_performance_are_unavailable():
    monitor = make_monitor()
    with patch("requests.get", return_value=response(policy(row(1042)))):
        monitor.update(
            {}, {"epoch": 1042, "slotIndex": 0, "slotsInEpoch": 100}, [{"samplePeriodSecs": 0, "numSlots": 0}]
        )
    assert math.isnan(metric(monitor, "solana_sfdp_upgrade_required"))
    assert math.isnan(metric(monitor, "solana_sfdp_version_compliant"))
    assert math.isnan(metric(monitor, "solana_sfdp_upgrade_due_seconds"))


def test_version_info_labels_are_exposed():
    monitor = make_monitor(cluster="mainnet-beta", client="jito")
    with patch("requests.get", return_value=response(policy(row(1042, cluster="mainnet-beta")))):
        update(monitor, version="1.18.0-jito", feature_set=123)
    assert (
        metric(
            monitor,
            "solana_node_version_info",
            {"version": "1.18.0-jito", "feature_set": "123", "client": "jito", "cluster": "mainnet-beta"},
        )
        == 1
    )


def test_invalid_cluster_does_not_fetch_policy():
    monitor = make_monitor(cluster="unknown")
    with patch("requests.get") as get:
        update(monitor)
    get.assert_not_called()


def test_policy_cache_ttl_stale_fallback_and_expiry():
    now = [1000.0]
    monitor = make_monitor(clock=lambda: now[0])
    failed = response({}, status=503)
    with patch(
        "requests.get",
        side_effect=[response(policy(row(1042, agave_min_version="1.0.0"))), failed, failed],
    ) as get:
        update(monitor, version="1.0.0")
        assert metric(monitor, "solana_sfdp_policy_fetch_success") == 1
        assert metric(monitor, "solana_sfdp_policy_max_epoch") == 1042
        now[0] += 15 * 60 + 1
        update(monitor, version="1.0.0")
        assert metric(monitor, "solana_sfdp_policy_fetch_success") == 0
        assert metric(monitor, "solana_sfdp_upgrade_required") == 0
        now[0] += 60 * 60 + 1
        update(monitor, version="1.0.0")
    assert get.call_count == 3
    assert math.isnan(metric(monitor, "solana_sfdp_upgrade_required"))
    assert math.isnan(metric(monitor, "solana_sfdp_policy_max_epoch"))


def test_empty_policy_fails_closed_for_derived_metrics():
    monitor = make_monitor()
    with patch("requests.get", return_value=response(policy())):
        update(monitor)
    assert metric(monitor, "solana_sfdp_policy_fetch_success") == 0
    for name in (
        "solana_sfdp_upgrade_required",
        "solana_sfdp_policy_current_epoch_covered",
        "solana_sfdp_version_compliant",
        "solana_sfdp_policy_max_epoch",
    ):
        assert math.isnan(metric(monitor, name))


def test_policy_row_for_wrong_cluster_is_ignored():
    monitor = make_monitor(cluster="testnet")
    wrong_cluster = row(1042, agave_min_version="1.0.0")
    wrong_cluster["cluster"] = "mainnet-beta"
    with patch("requests.get", return_value=response(policy(wrong_cluster))):
        update(monitor, version="0.1.0")
    assert metric(monitor, "solana_sfdp_policy_fetch_success") == 0
    assert math.isnan(metric(monitor, "solana_sfdp_policy_current_epoch_covered"))
    assert math.isnan(metric(monitor, "solana_sfdp_version_compliant"))
    assert math.isnan(metric(monitor, "solana_sfdp_upgrade_required"))


@pytest.mark.parametrize("client", ["agave", "frankendancer", "firedancer"])
def test_relevant_client_row_without_both_bounds_is_unknown(client):
    monitor = make_monitor(client=client)
    record = {"epoch": 1042, "cluster": "testnet"}
    with patch("requests.get", return_value=response(policy(record))):
        update(monitor)
    assert metric(monitor, "solana_sfdp_policy_fetch_success") == 0
    assert math.isnan(metric(monitor, "solana_sfdp_upgrade_required"))
    assert math.isnan(metric(monitor, "solana_sfdp_version_compliant"))


def test_present_null_bound_is_unbounded():
    monitor = make_monitor()
    record = {"epoch": 1042, "cluster": "testnet", "agave_min_version": "1.0.0", "agave_max_version": None}
    with patch("requests.get", return_value=response(policy(record))):
        update(monitor, version="9.0.0")
    assert metric(monitor, "solana_sfdp_upgrade_required") == 0
    assert metric(monitor, "solana_sfdp_version_compliant") == 1


def test_invalid_bounds_fail_closed():
    monitor = make_monitor()
    record = row(1042, agave_min_version="2.0.0", agave_max_version="1.0.0")
    with patch("requests.get", return_value=response(policy(record))):
        update(monitor, version="1.5.0")
    assert metric(monitor, "solana_sfdp_policy_fetch_success") == 0
    assert math.isnan(metric(monitor, "solana_sfdp_upgrade_required"))
    assert math.isnan(metric(monitor, "solana_sfdp_version_compliant"))


def test_conflicting_duplicate_epoch_fails_closed():
    monitor = make_monitor()
    with patch(
        "requests.get",
        return_value=response(policy(row(1042, agave_min_version="1.0.0"), row(1042, agave_min_version="2.0.0"))),
    ):
        update(monitor, version="1.5.0")
    assert metric(monitor, "solana_sfdp_policy_fetch_success") == 0
    assert math.isnan(metric(monitor, "solana_sfdp_upgrade_required"))
    assert math.isnan(metric(monitor, "solana_sfdp_policy_current_epoch_covered"))
    assert math.isnan(metric(monitor, "solana_sfdp_version_compliant"))


def test_cluster_change_invalidates_cached_policy():
    monitor = make_monitor(cluster="testnet")
    with patch(
        "requests.get",
        side_effect=[
            response(policy(row(1042, agave_min_version="1.0.0"))),
            response(policy(row(1042, agave_min_version="2.0.0"))),
        ],
    ) as get:
        update(monitor, version="1.0.0")
        monitor.cluster = "mainnet-beta"
        update(monitor, version="1.0.0")
    assert [call.kwargs["params"] for call in get.call_args_list] == [
        {"cluster": "testnet"},
        {"cluster": "mainnet-beta"},
    ]


def test_policy_failure_retry_is_bounded_and_retries_after_sixty_seconds():
    now = [1000.0]
    monitor = make_monitor(clock=lambda: now[0])
    failure = response({}, status=503)
    success = response(policy(row(1042)))
    with patch("requests.get", side_effect=[failure, success]) as get:
        update(monitor)
        update(monitor)
        assert get.call_count == 1
        now[0] += 60
        update(monitor)
    assert get.call_count == 2


@pytest.mark.parametrize(
    "epoch_info, samples",
    [
        ({"epoch": -1, "slotIndex": 0, "slotsInEpoch": 100}, [{"samplePeriodSecs": 40, "numSlots": 100}]),
        ({"epoch": True, "slotIndex": 0, "slotsInEpoch": 100}, [{"samplePeriodSecs": 40, "numSlots": 100}]),
        ({"epoch": 1042, "slotIndex": 100, "slotsInEpoch": 100}, [{"samplePeriodSecs": 40, "numSlots": 100}]),
        ({"epoch": 1042, "slotIndex": 0, "slotsInEpoch": 100}, [{"samplePeriodSecs": -1, "numSlots": 100}]),
    ],
)
def test_due_seconds_reject_invalid_epoch_or_performance_inputs(epoch_info, samples):
    monitor = make_monitor()
    with patch("requests.get", return_value=response(policy(row(1043, agave_min_version="2.0.0")))):
        monitor.update({"version": "1.0.0"}, epoch_info, samples)
    assert math.isnan(metric(monitor, "solana_sfdp_upgrade_due_seconds"))


@pytest.mark.parametrize("invalid_version", [1, True, [], {}, float("nan"), float("inf"), float("-inf")])
def test_invalid_version_values_are_not_exposed_or_used(invalid_version):
    monitor = make_monitor()
    with patch("requests.get", return_value=response(policy(row(1042, agave_min_version="1.0.0")))):
        monitor.update(
            {"version": invalid_version},
            {"epoch": 1042, "slotIndex": 0, "slotsInEpoch": 100},
            [{"samplePeriodSecs": 40, "numSlots": 100}],
        )
    assert metric(monitor, "solana_node_version_info") is None
    assert math.isnan(metric(monitor, "solana_sfdp_upgrade_required"))
    assert math.isnan(metric(monitor, "solana_sfdp_version_compliant"))


def test_unparseable_version_retains_raw_info_but_is_not_used_for_derived_metrics():
    monitor = make_monitor()
    with patch("requests.get", return_value=response(policy(row(1042, agave_min_version="1.0.0")))):
        update(monitor, version="vv4.3.0")
    assert (
        metric(
            monitor,
            "solana_node_version_info",
            {"version": "vv4.3.0", "feature_set": "", "client": "agave", "cluster": "testnet"},
        )
        == 1
    )
    assert math.isnan(metric(monitor, "solana_sfdp_upgrade_required"))
    assert math.isnan(metric(monitor, "solana_sfdp_version_compliant"))
