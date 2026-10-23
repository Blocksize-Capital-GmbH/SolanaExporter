"""Finalized dual JPool bond balance contract tests."""

import math

import pytest
from prometheus_client import generate_latest

from solanaexporter.solanaExporter import SolanaExporter

LEGACY = "LEGACY_BOND"
JSOL = "JSOL_VAULT"
TOKEN_OWNER = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"  # nosec B105
JSOL_MINT = "7Q2afV64in6N6SeZsAAB81TJzwDoD6zpqmHkzi9Dcavn"


def account_info(*, amount="4123140000", decimals=9, owner=TOKEN_OWNER, mint=JSOL_MINT, account_type="account"):
    return {
        "value": {
            "owner": owner,
            "data": {
                "parsed": {
                    "type": account_type,
                    "info": {"mint": mint, "tokenAmount": {"amount": amount, "decimals": decimals}},
                }
            },
        }
    }


def test_dual_config_starts_unavailable_and_accepts_file_values(node, tmp_path):
    config = tmp_path / "exporter.env"
    config.write_text(
        "\n".join(
            f"{key}={value}"
            for key, value in {**node, "JPOOL_LEGACY_BOND_ADDRESS": LEGACY, "JPOOL_JSOL_BOND_ADDRESS": JSOL}.items()
        )
    )
    exporter = SolanaExporter("fromFile", str(config))
    assert exporter.config.jpool_legacy_bond_address == LEGACY
    assert exporter.config.jpool_jsol_bond_address == JSOL
    assert math.isnan(exporter.legacy_jpool_bond_balance._value.get())
    assert math.isnan(exporter.jpool_jsol_bond_balance._value.get())


@pytest.mark.parametrize("lamports, expected", [(0, 0), (5_701_782_990, 5.70178299)])
def test_legacy_balance_accepts_finalized_nonnegative_lamports(node, rpc_mock, monkeypatch, lamports, expected):
    monkeypatch.setenv("JPOOL_LEGACY_BOND_ADDRESS", LEGACY)
    exporter = SolanaExporter("fromEnv")
    rpc_mock[0]["balance"] = {"value": lamports}
    exporter._get_legacy_jpool_bond_balance()
    assert exporter.legacy_jpool_bond_balance._value.get() == expected
    request = rpc_mock[1].call_args.kwargs["json"]
    assert request["method"] == "getBalance"
    assert request["params"] == [LEGACY, {"commitment": "finalized"}]


@pytest.mark.parametrize("result", [None, {}, {"value": None}, {"value": True}, {"value": -1}, {"value": "5"}])
def test_legacy_malformed_balance_invalidates_previous_value(node, rpc_mock, monkeypatch, result):
    monkeypatch.setenv("JPOOL_LEGACY_BOND_ADDRESS", LEGACY)
    exporter = SolanaExporter("fromEnv")
    exporter.legacy_jpool_bond_balance.set(5.7)
    rpc_mock[0]["balance"] = result
    exporter._get_legacy_jpool_bond_balance()
    assert math.isnan(exporter.legacy_jpool_bond_balance._value.get())


@pytest.mark.parametrize("info", [account_info(), account_info(amount="0"), {"value": None}])
def test_jsol_existing_and_absent_vault_are_valid(node, rpc_mock, monkeypatch, info):
    monkeypatch.setenv("JPOOL_JSOL_BOND_ADDRESS", JSOL)
    exporter = SolanaExporter("fromEnv")
    rpc_mock[0]["account_info"] = info
    exporter._get_jpool_jsol_bond_balance()
    expected = (
        4.12314
        if info["value"] is not None and info["value"]["data"]["parsed"]["info"]["tokenAmount"]["amount"] != "0"
        else 0
    )
    assert exporter.jpool_jsol_bond_balance._value.get() == expected
    request = rpc_mock[1].call_args.kwargs["json"]
    assert request["method"] == "getAccountInfo"
    assert request["params"] == [JSOL, {"commitment": "finalized", "encoding": "jsonParsed"}]


@pytest.mark.parametrize(
    "info",
    [
        {},
        {"value": []},
        account_info(amount="bad"),
        account_info(owner="WrongOwner"),
        account_info(mint="WrongMint"),
        account_info(account_type="mint"),
        {"value": {"owner": TOKEN_OWNER, "data": ["base64-data", "base64"]}},
        {"value": {"owner": TOKEN_OWNER, "data": {}}},
        {"value": {"owner": TOKEN_OWNER, "data": {"parsed": {}}}},
        {"value": {"owner": TOKEN_OWNER, "data": {"parsed": {"type": "account"}}}},
        {"value": {"owner": TOKEN_OWNER, "data": {"parsed": {"type": "account", "info": {}}}}},
        {
            "value": {
                "owner": TOKEN_OWNER,
                "data": {"parsed": {"type": "account", "info": {"mint": JSOL_MINT}}},
            }
        },
    ],
)
def test_jsol_malformed_account_invalidates_previous_value(node, rpc_mock, monkeypatch, info):
    monkeypatch.setenv("JPOOL_JSOL_BOND_ADDRESS", JSOL)
    exporter = SolanaExporter("fromEnv")
    exporter.jpool_jsol_bond_balance.set(4.12314)
    rpc_mock[0]["account_info"] = info
    exporter._get_jpool_jsol_bond_balance()
    assert math.isnan(exporter.jpool_jsol_bond_balance._value.get())


@pytest.mark.parametrize("amount", ["-1", True, 1, "4.5", "", "١", "18446744073709551616"])
def test_jsol_invalid_amounts_invalidate_previous_value(node, rpc_mock, monkeypatch, amount):
    monkeypatch.setenv("JPOOL_JSOL_BOND_ADDRESS", JSOL)
    exporter = SolanaExporter("fromEnv")
    exporter.jpool_jsol_bond_balance.set(4.12314)
    rpc_mock[0]["account_info"] = account_info(amount=amount)
    exporter._get_jpool_jsol_bond_balance()
    assert math.isnan(exporter.jpool_jsol_bond_balance._value.get())


@pytest.mark.parametrize("decimals", [True, "9", 8, 10, 0])
def test_jsol_invalid_decimals_invalidate_previous_value(node, rpc_mock, monkeypatch, decimals):
    monkeypatch.setenv("JPOOL_JSOL_BOND_ADDRESS", JSOL)
    exporter = SolanaExporter("fromEnv")
    exporter.jpool_jsol_bond_balance.set(4.12314)
    rpc_mock[0]["account_info"] = account_info(decimals=decimals)
    exporter._get_jpool_jsol_bond_balance()
    assert math.isnan(exporter.jpool_jsol_bond_balance._value.get())


def test_jsol_rpc_error_is_unknown_not_zero(node, rpc_mock, monkeypatch):
    monkeypatch.setenv("JPOOL_JSOL_BOND_ADDRESS", JSOL)
    exporter = SolanaExporter("fromEnv")
    exporter.jpool_jsol_bond_balance.set(4.12314)
    rpc_mock[0]["fail"].add("getAccountInfo")
    exporter._get_jpool_jsol_bond_balance()
    assert math.isnan(exporter.jpool_jsol_bond_balance._value.get())


def test_dual_balances_refresh_each_poll_and_reference_mismatch_invalidates_both(node, rpc_mock, monkeypatch):
    monkeypatch.setenv("JPOOL_LEGACY_BOND_ADDRESS", LEGACY)
    monkeypatch.setenv("JPOOL_JSOL_BOND_ADDRESS", JSOL)
    rpc_mock[0]["account_info"] = account_info()
    exporter = SolanaExporter("fromEnv")
    for _ in range(3):
        exporter.collect_metrics()
    requests = [call.kwargs["json"] for call in rpc_mock[1].call_args_list if isinstance(call.kwargs["json"], dict)]
    assert len([request for request in requests if request["method"] == "getBalance"]) == 3
    assert len([request for request in requests if request["method"] == "getAccountInfo"]) == 3
    rpc_mock[0]["wrong_cluster"] = True
    exporter.collect_metrics()
    assert math.isnan(exporter.legacy_jpool_bond_balance._value.get())
    assert math.isnan(exporter.jpool_jsol_bond_balance._value.get())


def test_metric_names_are_exact_and_old_metric_is_absent(node, monkeypatch):
    monkeypatch.setenv("JPOOL_LEGACY_BOND_ADDRESS", LEGACY)
    monkeypatch.setenv("JPOOL_JSOL_BOND_ADDRESS", JSOL)
    exporter = SolanaExporter("fromEnv")
    exposition = generate_latest(exporter.registry)
    assert b"solana_legacy_jpool_bond_balance" in exposition
    assert b"solana_jpool_jsol_bond_balance" in exposition
    assert b"solana_jpool_bond_balance" not in exposition


def test_no_config_exposes_neither_optional_metric_and_old_aliases_do_not_enable_them(node, monkeypatch):
    monkeypatch.setenv("JPOOL_BOND_ADDRESS", "OLD_BOND")
    monkeypatch.setenv("JPOOL_BOND_WITHDRAWER_AUTHORITY", "OLD_AUTHORITY")
    exporter = SolanaExporter("fromEnv")
    assert not hasattr(exporter, "legacy_jpool_bond_balance")
    assert not hasattr(exporter, "jpool_jsol_bond_balance")
