import requests
from exporter.jsonRPCRequest import JsonRPCRequest

from solanaexporter.rpc import send_rpc


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _requests():
    return [JsonRPCRequest("first", params=[1]), JsonRPCRequest("second", params=[2])]


def test_batch_correlates_reversed_replies_and_emits_unique_ids(mocker):
    post = mocker.patch(
        "solanaexporter.rpc.requests.post",
        return_value=FakeResponse(
            [
                {"jsonrpc": "2.0", "id": 2, "result": "second-result"},
                {"jsonrpc": "2.0", "id": 1, "result": "first-result"},
            ]
        ),
    )

    responses = send_rpc("https://rpc.example", _requests())

    assert [response.result for response in responses] == ["first-result", "second-result"]
    assert post.call_args.kwargs["json"] == [
        {"jsonrpc": "2.0", "id": 1, "method": "first", "params": [1]},
        {"jsonrpc": "2.0", "id": 2, "method": "second", "params": [2]},
    ]
    assert post.call_args.kwargs["timeout"] == 10


def test_missing_duplicate_and_unknown_ids_are_errors(mocker):
    post = mocker.patch("solanaexporter.rpc.requests.post")

    post.return_value = FakeResponse([{"id": 1, "result": "ok"}])
    missing = send_rpc("url", _requests())
    assert missing[0].result == "ok"
    assert missing[1].error["message"] == "missing JSON-RPC response id"

    post.return_value = FakeResponse(
        [
            {"id": 1, "result": "first"},
            {"id": 1, "result": "duplicate"},
            {"id": 99, "result": "unknown"},
        ]
    )
    invalid = send_rpc("url", _requests())
    assert [response.error["message"] for response in invalid] == [
        "duplicate JSON-RPC response id",
        "missing JSON-RPC response id",
    ]


def test_per_method_errors_are_preserved(mocker):
    mocker.patch(
        "solanaexporter.rpc.requests.post",
        return_value=FakeResponse(
            [
                {"id": 2, "error": {"code": -32000, "message": "second failed", "data": {"x": 1}}},
                {"id": 1, "result": "ok"},
            ]
        ),
    )

    responses = send_rpc("url", _requests())

    assert responses[0].is_successful()
    assert responses[1].error == {"code": -32000, "message": "second failed", "data": {"x": 1}}


def test_single_request_is_an_object_and_accepts_object_response(mocker):
    post = mocker.patch("solanaexporter.rpc.requests.post", return_value=FakeResponse({"id": 1, "result": 42}))

    responses = send_rpc("url", JsonRPCRequest("getSlot"))

    assert [response.result for response in responses] == [42]
    assert post.call_args.kwargs["json"] == {"jsonrpc": "2.0", "id": 1, "method": "getSlot", "params": []}


def test_malformed_json_http_and_network_failures_return_errors(mocker):
    post = mocker.patch("solanaexporter.rpc.requests.post")

    post.return_value = FakeResponse(ValueError("bad json"))
    assert send_rpc("url", _requests())[0].error["message"] == "malformed JSON-RPC response"

    post.return_value = FakeResponse({}, status_code=503)
    assert send_rpc("url", _requests())[0].error["code"] == 503

    post.side_effect = requests.ConnectionError("secret-url")
    assert send_rpc("url", _requests())[0].error["message"] == "JSON-RPC network request failed"
