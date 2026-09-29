"""Shared local-node RPC fixtures."""

from unittest.mock import patch

import pytest

GENESIS = "4uhcVJyU9pJkvQyS88uRDiswHXSCkY3zQawwpjk2NsNY"


@pytest.fixture
def node(monkeypatch):
    """Provide a local validator and an independent reference on the same cluster."""
    values = {
        "SOLANA_RPC_URL": "http://localhost:8899",
        "SOLANA_PUBLIC_RPC_URL": "https://reference.invalid",
        "EXPORTER_PORT": "7896",
        "POLL_INTERVAL": "10",
        "VOTE_PUBKEY": "VOTE",
        "VALIDATOR_PUBKEY": "CONFIGURED_OLD_IDENTITY",
        "LABEL": "testnet-node",
        "VERSION": "stale-config-version",
        "SOLANA_CLUSTER": "testnet",
    }
    for key in ("DOUBLE_ZERO_FEES_ADDRESS", "JPOOL_BOND_WITHDRAWER_AUTHORITY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


@pytest.fixture
def rpc_mock():
    """Emulate ID-correlated RPC replies, including reordered batch responses."""
    from unittest.mock import Mock

    state = {"slot": 1010, "identity": "ACTIVE", "production": [8, 3], "fail": set(), "scans": 0}

    def post(url, json, **kwargs):
        requests = json if isinstance(json, list) else [json]
        responses = []
        for request in requests:
            method = request["method"]
            if method in state["fail"]:
                responses.append({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32000}})
                continue
            result = {
                "getIdentity": {"identity": state["identity"]},
                "getVersion": {"solana-core": "4.3.0", "feature-set": 123},
                "getGenesisHash": GENESIS,
                "getEpochInfo": {"epoch": 20, "absoluteSlot": state["slot"], "slotIndex": 10, "slotsInEpoch": 100},
                "getRecentPerformanceSamples": [{"numSlots": 300, "samplePeriodSecs": 60}],
                "getSlot": state["slot"] + (5 if "reference" in url else 0),
                "getBalance": {"value": 2_000_000_000},
                "getVoteAccounts": {
                    "current": [
                        {
                            "votePubkey": "VOTE",
                            "nodePubkey": "ACTIVE",
                            "activatedStake": 10_000_000_000,
                            "lastVote": 1009,
                            "epochCredits": [[19, 80, 50], [20, 100, 80]],
                        }
                    ],
                    "delinquent": [],
                },
                "getLeaderSchedule": {state["identity"]: [10, 11]},
                "getBlockProduction": {
                    "context": {"slot": state["slot"]},
                    "value": {
                        "byIdentity": {state["identity"]: state["production"]},
                        "range": {"firstSlot": state["slot"] - 10, "lastSlot": state["slot"]},
                    },
                },
                "getHealth": "ok",
                "getProgramAccounts": [],
            }[method]
            if method == "getProgramAccounts":
                state["scans"] += 1
            if method == "getBlockProduction" and "raw_production" in state:
                result = state["raw_production"]
            if method == "getVoteAccounts" and "votes" in state:
                result = state["votes"]
            if method == "getGenesisHash" and "reference" in url and state.get("wrong_cluster"):
                result = "wrong-cluster"
            responses.append({"jsonrpc": "2.0", "id": request["id"], "result": result})
        response = Mock(status_code=200)
        response.json.return_value = list(reversed(responses)) if isinstance(json, list) else responses[0]
        return response

    with patch("requests.post", side_effect=post) as mock, patch("requests.get") as get:
        get.return_value.status_code = 200
        get.return_value.json.return_value = {
            "data": [{"cluster": "testnet", "epoch": 20, "agave_min_version": "4.4.0", "agave_max_version": None}]
        }
        yield state, mock
