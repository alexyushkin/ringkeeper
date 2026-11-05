"""Any HTTP API: an address, a way to sign in, and optionally an OpenAPI description.

The address is the connection's; the secret is JSON:

    {"auth": "bearer", "token": "..."}
    {"auth": "header", "header_name": "X-Api-Key", "token": "..."}
    {"auth": "basic", "username": "...", "password": "..."}
    {"auth": "query", "query_name": "api_key", "token": "..."}
    {"auth": "none"}

and may add "spec_url": where the API's OpenAPI description is.

GET and HEAD run. Anything else waits for approval, even on test data: an API can do
anything with a POST (DataForSEO's cost money). DELETE needs the name typed. A call stays
under the connection's address: paths can't climb out of it, and redirects aren't followed,
so the key can't be carried somewhere else.
"""

import base64
import json
import re
from typing import Annotated, Any, Literal, cast
from urllib.parse import unquote

import httpx2
import yaml
from pydantic import Field

from ringkeeper import netpolicy
from ringkeeper.adapters import rest
from ringkeeper.adapters.base import JSON, Adapter, Target, ToolError, final, read
from ringkeeper.models import InstanceType

# Tests swap in httpx2.MockTransport; production uses the real network.
transport: httpx2.AsyncBaseTransport | None = None
AUTHS = ("bearer", "header", "basic", "query", "none")
MAX_ANSWER_CHARS = 50_000
MAX_SPEC_BYTES = 8_000_000
MAX_ENDPOINTS = 200
OPENAPI_METHODS = ("get", "post", "put", "patch", "delete", "head")
HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,100}$")
QUERY_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
Path = Annotated[
    str,
    Field(
        min_length=1, max_length=2000, pattern=r"^/",
        description="Starts with /, after the connection's address, e.g. /v1/forms.",
    ),
]  # fmt: skip
Params = Annotated[
    dict[str, str | int | float | bool] | None, Field(description="Query parameters.")
]


def settings(value: str) -> JSON:
    """The stored JSON, checked; raises ToolError if it can't be used."""
    try:
        data: Any = json.loads(value)
    except ValueError:
        raise ToolError("this API connection's secret isn't in the expected form") from None
    fields = cast(JSON, data) if isinstance(data, dict) else {}
    auth = str(fields.get("auth") or "bearer")
    if auth not in AUTHS:
        raise ToolError(f"unknown sign-in kind '{auth}'")
    if auth in ("bearer", "header", "query") and not str(fields.get("token") or "").strip():
        raise ToolError("this API connection needs its key or token")
    if auth == "basic" and not (fields.get("username") and fields.get("password")):
        raise ToolError("this API connection needs a username and password")
    if auth == "header" and not HEADER_NAME.match(str(fields.get("header_name") or "")):
        raise ToolError("the header's name is missing, or has characters a header can't")
    if auth == "query" and not QUERY_NAME.match(str(fields.get("query_name") or "")):
        raise ToolError("the query parameter's name is missing, or isn't a plain name")
    return {**fields, "auth": auth}


def _basic(fields: JSON) -> str:
    pair = f"{fields.get('username')}:{fields.get('password')}"
    return base64.b64encode(pair.encode()).decode()


def secret_fragments(value: str) -> list[str]:
    try:
        fields = settings(value)
    except ToolError:
        return []
    found = [str(fields.get(k) or "") for k in ("token", "password")]
    if fields["auth"] == "basic":
        found.append(_basic(fields))
    return [f for f in found if f]


def _signed(fields: JSON) -> tuple[dict[str, str], dict[str, str]]:
    """The headers and query parameters that sign a request in."""
    token = str(fields.get("token") or "").strip()
    match fields["auth"]:
        case "bearer":
            return {"Authorization": f"Bearer {token}"}, {}
        case "header":
            return {str(fields["header_name"]): token}, {}
        case "basic":
            return {"Authorization": f"Basic {_basic(fields)}"}, {}
        case "query":
            return {}, {str(fields["query_name"]): token}
        case _:
            return {}, {}


def inside(path: str) -> str:
    """A path that stays under the connection's address, or ToolError."""
    decoded = unquote(unquote(path))
    if not path.startswith("/") or path.startswith("//") or "\\" in decoded or "://" in decoded:
        raise ToolError("the path must start with / and stay on this connection's address")
    if ".." in decoded.split("?", 1)[0].split("/"):
        raise ToolError("the path can't climb out of this connection's address")
    return path


def _base(target: Target) -> str:
    if not target.base_url:
        raise ToolError("this API connection has no address")
    return netpolicy.check_url(target.base_url).rstrip("/")


def _answer(response: httpx2.Response) -> JSON:
    kind = response.headers.get("content-type", "")
    answer: JSON = {"status": response.status_code}
    if response.is_redirect:
        answer["redirect"] = "not followed: a key is never carried to another address"
    if not response.content:
        return answer
    if "json" in kind:
        try:
            parsed = response.json()
        except ValueError:
            parsed = None
        text = json.dumps(parsed)
        if parsed is not None and len(text) <= MAX_ANSWER_CHARS:
            return answer | {"json": parsed}
        return answer | {"text": text[:MAX_ANSWER_CHARS], "truncated": True}
    if kind.startswith(("text/", "application/xml", "application/yaml")):
        text = response.text
        return answer | {"text": text[:MAX_ANSWER_CHARS], "truncated": len(text) > MAX_ANSWER_CHARS}
    return answer | {"content_type": kind, "bytes": len(response.content)}


async def _request(
    target: Target, method: str, path: str, params: JSON | None = None, body: Any = None
) -> JSON:
    fields = settings(target.secret.reveal())
    headers, signed = _signed(fields)
    url = _base(target) + inside(path)
    query = {k: v for k, v in (params or {}).items() if v is not None} | signed
    client = httpx2.AsyncClient(timeout=rest.TIMEOUT, transport=transport, follow_redirects=False)
    try:
        async with client:
            response = await client.request(
                method, url, params=query or None, headers=headers, json=body
            )
    except httpx2.TimeoutException:
        raise ToolError("the API timed out") from None
    except httpx2.HTTPError as error:
        raise ToolError(f"cannot reach the API: {type(error).__name__}") from None
    if response.status_code >= 400:
        raise ToolError(f"the API returned {response.status_code}: {rest.error_message(response)}")
    return _answer(response)


async def probe(target: Target) -> str:
    """One GET of the address itself. Any answer but a refusal means it's reachable."""
    try:
        found = await _request(target, "GET", "/")
    except ToolError as error:
        if re.search(r"returned 40[13]", str(error)):
            raise
        if "returned" in str(error):  # a 404 at the root: the API is there
            return "Connected: the address answers. Nothing's known about the key until a call."
        raise
    return f"Connected: the address answered {found['status']}."


@read
async def get(target: Target, path: Path, params: Params = None) -> JSON:
    """GET a path under this connection's address. Runs without asking."""
    return await _request(target, "GET", path, params)


@read
async def head(target: Target, path: Path, params: Params = None) -> JSON:
    """HEAD a path under this connection's address: its status, no content."""
    return await _request(target, "HEAD", path, params)


def _deletes(args: JSON) -> str | None:
    return "deletes through the API" if args.get("method") == "DELETE" else None


@final(_deletes)
async def send_request(
    target: Target,
    method: Literal["POST", "PUT", "PATCH", "DELETE"],
    path: Path,
    json_body: Annotated[Any, Field(description="The JSON to send, if any.")] = None,
    params: Params = None,
) -> JSON:
    """POST, PUT, PATCH or DELETE a path. Always waits for the user's approval; a DELETE also
    needs the connection's name typed."""
    return await _request(target, method, path, params, json_body)


def _parsed_spec(text: str) -> JSON:
    try:
        data: Any = json.loads(text)
    except ValueError:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            raise ToolError("the OpenAPI description isn't JSON or YAML") from None
    if not isinstance(data, dict) or "paths" not in data:
        raise ToolError("that file isn't an OpenAPI description (it has no paths)")
    return cast(JSON, data)


@read
async def list_endpoints(
    target: Target, search: Annotated[str | None, Field(max_length=100)] = None
) -> JSON:
    """What the API offers, from its OpenAPI description: method, path and summary. Needs the
    connection's OpenAPI address; `search` narrows it by words in the path or summary."""
    url = str(settings(target.secret.reveal()).get("spec_url") or "").strip()
    if not url:
        raise ToolError("this connection has no OpenAPI address, so its endpoints aren't known")
    netpolicy.check_url(url)
    client = httpx2.AsyncClient(timeout=rest.TIMEOUT, transport=transport, follow_redirects=False)
    try:
        async with client:
            response = await client.get(url)
    except httpx2.HTTPError as error:
        raise ToolError(f"cannot read the OpenAPI description: {type(error).__name__}") from None
    if response.status_code != 200 or len(response.content) > MAX_SPEC_BYTES:
        raise ToolError(f"cannot read the OpenAPI description ({response.status_code})")
    paths = cast(JSON, _parsed_spec(response.text)["paths"])
    words = (search or "").lower().split()
    found: list[JSON] = []
    for path, item in paths.items():
        operations = cast(JSON, item) if isinstance(item, dict) else {}
        for method in OPENAPI_METHODS:
            operation = operations.get(method)
            if not isinstance(operation, dict):
                continue
            summary = str(cast(JSON, operation).get("summary") or "")
            if all(w in f"{path} {summary}".lower() for w in words):
                found.append({"method": method.upper(), "path": path, "summary": summary[:200]})
    return {"endpoints": found[:MAX_ENDPOINTS], "more": len(found) > MAX_ENDPOINTS}


ADAPTER = Adapter(
    service=InstanceType.HTTP,
    tools=(get, head, list_endpoints, send_request),
    secret_fragments=secret_fragments,
)
