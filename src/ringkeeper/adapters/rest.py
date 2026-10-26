"""The HTTP part the REST adapters share: one request, and errors the agent can read.

Each adapter builds its own client (base URL, auth, its module's `transport`
for tests) and calls `call`. Failures become ToolError with the service's own
message when it gives one; the gateway scrubs them before the agent sees them.
"""

from typing import Any, cast

import httpx2

from ringkeeper.adapters.base import JSON, ToolError

TIMEOUT = 30.0


def error_message(response: httpx2.Response) -> str:
    """The service's own explanation: {"error": {"message"}}, {"message"}, or {"error": "..."}."""
    try:
        body: Any = response.json()
    except ValueError:
        return response.reason_phrase or "no details"
    if isinstance(body, dict):
        fields = cast(dict[str, Any], body)
        error = fields.get("error")
        if isinstance(error, dict):
            nested = cast(dict[str, Any], error).get("message")
            if isinstance(nested, str):
                return nested
        candidates: list[object] = [fields.get("message"), error]
        for value in candidates:
            if isinstance(value, str):
                return value
    return response.reason_phrase or "no details"


async def call(
    client: httpx2.AsyncClient,
    service: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: JSON | None = None,
    data: dict[str, str] | None = None,
) -> JSON:
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        async with client:
            response = await client.request(method, path, params=clean, json=json, data=data)
    except httpx2.TimeoutException:
        raise ToolError(f"{service} timed out") from None
    except httpx2.HTTPError as error:
        raise ToolError(f"cannot reach {service}: {type(error).__name__}") from None
    if response.status_code >= 400:
        raise ToolError(f"{service} returned {response.status_code}: {error_message(response)}")
    body: Any = response.json() if response.content else {}
    return cast(JSON, body) if isinstance(body, dict) else {"data": body}
