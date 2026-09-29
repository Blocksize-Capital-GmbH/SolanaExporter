"""Bounded JSON-RPC transport with response correlation by request ID."""

from typing import Any, Dict, List, Optional, Union

import requests
from exporter.jsonRPCRequest import JsonRPCRequest
from exporter.jsonRPCResponse import JsonRPCResponse

_TIMEOUT_SECONDS = 10


def _error(message: str, code: Optional[int] = None) -> Dict[str, Any]:
    """Build the small, stable error shape consumed by the exporter."""
    error: Dict[str, Any] = {"message": message}
    if code is not None:
        error["code"] = code
    return error


def _log(logger: Any, message: str) -> None:
    """Log a transport diagnostic without including URL or request payload."""
    if logger is not None:
        logger.error(message)


def _failed(count: int, message: str, code: Optional[int] = None) -> List[JsonRPCResponse]:
    """Return one failed response for each request in a failed transport."""
    return [JsonRPCResponse(result=None, error=_error(message, code)) for _ in range(count)]


def _response_error(response: Any) -> Optional[str]:
    """Return a protocol error for a response member, if one exists."""
    if not isinstance(response, dict):
        return "JSON-RPC response member is not an object"
    if "id" not in response:
        return "JSON-RPC response member has no id"
    response_id = response["id"]
    if isinstance(response_id, bool) or not isinstance(response_id, int):
        return "JSON-RPC response id is not an integer"
    if "result" not in response and "error" not in response:
        return "JSON-RPC response has neither result nor error"
    if "result" in response and "error" in response:
        return "JSON-RPC response has both result and error"
    if "error" in response and not isinstance(response["error"], dict):
        return "JSON-RPC error is not an object"
    return None


def send_rpc(
    rpc_url: str,
    rpc_requests: Union[JsonRPCRequest, List[JsonRPCRequest]],
    logger: Any = None,
) -> List[JsonRPCResponse]:
    """Send one or more JSON-RPC requests and correlate responses by integer ID.

    The return list always follows request order and has one member per request.
    Network, HTTP, JSON, and protocol failures are represented as response errors
    so callers do not need to catch transport exceptions.
    """
    if isinstance(rpc_requests, JsonRPCRequest):
        requests_list = [rpc_requests]
        is_single = True
    elif isinstance(rpc_requests, list) and all(isinstance(request, JsonRPCRequest) for request in rpc_requests):
        requests_list = rpc_requests
        is_single = False
    else:
        _log(logger, "JSON-RPC request input is invalid")
        return _failed(1, "invalid JSON-RPC request input")

    if not requests_list:
        return []

    request_ids = list(range(1, len(requests_list) + 1))
    payload = []
    for request_id, request in enumerate(requests_list, start=1):
        payload.append(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": request.method,
                "params": request.params if request.params is not None else [],
            }
        )

    try:
        response = requests.post(
            rpc_url,
            json=payload[0] if is_single else payload,
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        _log(logger, "JSON-RPC request failed at the network layer")
        return _failed(len(requests_list), "JSON-RPC network request failed")
    except Exception:
        _log(logger, "JSON-RPC request failed unexpectedly")
        return _failed(len(requests_list), "JSON-RPC request failed")

    if response.status_code < 200 or response.status_code >= 300:
        _log(logger, "JSON-RPC endpoint returned an HTTP error")
        return _failed(len(requests_list), "JSON-RPC HTTP request failed", response.status_code)

    try:
        raw_response = response.json()
    except (ValueError, TypeError):
        _log(logger, "JSON-RPC endpoint returned malformed JSON")
        return _failed(len(requests_list), "malformed JSON-RPC response")

    if isinstance(raw_response, dict):
        members: Optional[List[Any]] = [raw_response]
    elif isinstance(raw_response, list):
        members = raw_response
    else:
        members = None
    if members is None or not members:
        _log(logger, "JSON-RPC endpoint returned an invalid response root")
        return _failed(len(requests_list), "invalid JSON-RPC response root")

    for member in members:
        member_error = _response_error(member)
        if member_error is not None:
            _log(logger, "JSON-RPC endpoint returned an invalid response member")
            return _failed(len(requests_list), member_error)

    response_by_id = {}
    duplicate_ids = set()
    unknown_id = False
    for member in members:
        response_id = member["id"]
        if response_id not in request_ids:
            unknown_id = True
            continue
        if response_id in response_by_id:
            duplicate_ids.add(response_id)
        else:
            response_by_id[response_id] = member

    if unknown_id:
        _log(logger, "JSON-RPC endpoint returned an unknown response id")
    if duplicate_ids:
        _log(logger, "JSON-RPC endpoint returned duplicate response ids")

    responses = []
    for request_id in request_ids:
        member = response_by_id.get(request_id)
        if request_id in duplicate_ids:
            responses.append(JsonRPCResponse(error=_error("duplicate JSON-RPC response id")))
        elif member is None:
            responses.append(JsonRPCResponse(error=_error("missing JSON-RPC response id")))
        elif "error" in member:
            responses.append(JsonRPCResponse(result=None, error=member["error"]))
        else:
            responses.append(JsonRPCResponse(result=member["result"], error=None))
    return responses
