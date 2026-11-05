"""The model proxy: a worker's only way to reach a model.

Claude Code in a worker (and any later engine) calls /model/<place>/... with a per-task token.
The token can do nothing else: it isn't accepted on an MCP endpoint, it ends with the session,
and the real credential is attached here, on the way out (services.model_upstream). So nothing
a worker can read holds a model key.

The answer streams straight back, and the tokens it reports are counted as it passes, for
any engine and any place. A token, a key or a prompt is never logged.
"""

import base64
import json
import logging
import struct
import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

import anyio.to_thread
import httpx2
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.services import model_upstream, model_usage, revocations
from ringkeeper.services.model_upstream import Upstream, UpstreamError
from ringkeeper.services.runtime import current_settings
from ringkeeper.tokens import TokenError, TokenRole, WorkerClaims, verify_token
from ringkeeper.vault import Vault

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = 15.0
READ_TIMEOUT = 600.0  # a long answer pauses between events while the model thinks
# Hop-by-hop headers, and whatever signs in: the worker's token stops here.
DROPPED_REQUEST = frozenset({
    "authorization", "x-api-key", "host", "content-length", "connection", "cookie",
    "accept-encoding", "transfer-encoding", "x-amz-security-token", "x-amz-date",
    "x-amz-content-sha256", "te", "upgrade", "proxy-authorization",
})  # fmt: skip
DROPPED_RESPONSE = frozenset({
    "connection", "transfer-encoding", "content-length", "content-encoding", "set-cookie",
    "keep-alive",
})  # fmt: skip
# Tests swap in httpx2.MockTransport; production talks to the model.
transport: httpx2.AsyncBaseTransport | None = None


class Counter:
    """The usage an answer reports, read as it passes: Anthropic's events, a plain JSON answer
    or Bedrock's binary frames, which wrap the same events."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.buffer = b""
        self.model = ""
        self.tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        self.whole = bytearray()  # a plain JSON answer, until it's known not to be a stream
        self.streaming = False

    def _usage(self, found: dict[str, Any]) -> None:
        names = {
            "input_tokens": "input", "output_tokens": "output",
            "cache_read_input_tokens": "cache_read", "cache_creation_input_tokens": "cache_write",
        }  # fmt: skip
        for name, key in names.items():
            value = found.get(name)
            if isinstance(value, int):
                self.tokens[key] = max(self.tokens[key], value)  # events repeat a running total

    def _event(self, event: dict[str, Any]) -> None:
        message = cast(dict[str, Any], event.get("message") or {})
        self.model = str(message.get("model") or self.model)
        for part in (message.get("usage"), event.get("usage")):
            if isinstance(part, dict):
                self._usage(cast(dict[str, Any], part))

    def _line(self, line: bytes) -> None:
        if line.startswith(b"data:"):
            self.streaming = True
            try:
                parsed: Any = json.loads(line[5:].strip())
            except ValueError:
                return
            if isinstance(parsed, dict):
                self._event(cast(dict[str, Any], parsed))

    def _frames(self) -> None:
        """AWS event stream: length, headers length, a check, headers, payload, a check."""
        while len(self.buffer) >= 12:
            total, headers = struct.unpack(">II", self.buffer[:8])
            if total < 16 or len(self.buffer) < total:
                return
            payload = self.buffer[12 + headers : total - 4]
            self.buffer = self.buffer[total:]
            try:
                wrapped = cast(dict[str, Any], json.loads(payload))
                self._event(json.loads(base64.b64decode(str(wrapped.get("bytes") or ""))))
            except (ValueError, TypeError):
                continue

    def feed(self, chunk: bytes) -> None:
        self.buffer += chunk
        if self.kind == "bedrock" and not self.buffer.startswith(b"{"):
            self.streaming = True
            self._frames()
            return
        lines = self.buffer.split(b"\n")
        self.buffer = lines.pop()
        for line in lines:
            self._line(line.rstrip(b"\r"))
        if not self.streaming and len(self.whole) < model_usage.MAX_PLAIN_ANSWER:
            self.whole += chunk

    def finish(self) -> None:
        if self.streaming:
            return
        try:
            found: Any = json.loads(bytes(self.whole) or self.buffer)
        except ValueError:
            return
        if isinstance(found, dict):
            fields = cast(dict[str, Any], found)
            self.model = str(fields.get("model") or self.model)
            if isinstance(fields.get("usage"), dict):
                self._usage(cast(dict[str, Any], fields["usage"]))


def _token_of(request: Request) -> str:
    """The token, however the engine sends it: a bearer header, or an API-key one."""
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return request.headers.get("x-api-key", "").strip()


def _claims(request: Request) -> WorkerClaims:
    token = _token_of(request)
    if not token:
        raise TokenError("a task's model token is required")
    claims = verify_token(token)
    if claims.role is not TokenRole.MODEL:
        raise TokenError("this needs a task's model token")
    with session_scope(commit=False) as session:
        if revocations.is_revoked(session, claims.token_id):
            raise TokenError("this task's model token has ended")
    return claims


def _plan(vault: Vault, claims: WorkerClaims, kind: str, tail: str, query: str) -> Upstream | None:
    settings = current_settings(get_settings())
    with session_scope(commit=False) as session:
        return model_upstream.upstream_for(
            session, vault, settings, claims.project, kind, tail, query
        )


def _refused(status: int, message: str) -> JSONResponse:
    """In Anthropic's own error shape, so the engine reports it plainly."""
    body = {"type": "error", "error": {"type": "ringkeeper_error", "message": message}}
    return JSONResponse(body, status_code=status)


def _record(claims: WorkerClaims, kind: str, counter: Counter) -> None:
    try:
        session_id = uuid.UUID(claims.session_id)
    except ValueError:
        return
    with session_scope() as session:
        model_usage.record(session, session_id, claims.project, kind, counter.model, counter.tokens)


def make_route(vault: Vault) -> Route:
    async def proxy(request: Request) -> Response:
        try:
            claims = await anyio.to_thread.run_sync(_claims, request)
        except TokenError as error:
            return _refused(401, str(error))
        kind = str(request.path_params["kind"])
        raw = cast(bytes, request.scope.get("raw_path") or request.url.path.encode()).decode()
        tail = raw.split(f"/model/{kind}/", 1)[-1]
        query = f"?{request.url.query}" if request.url.query else ""
        try:
            target = await anyio.to_thread.run_sync(_plan, vault, claims, kind, tail, query)
        except UpstreamError as error:
            return _refused(503, str(error))
        if target is None:
            return _refused(404, "that isn't a model route for this project")
        body = await request.body()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in DROPPED_REQUEST}
        headers["accept-encoding"] = "identity"  # the counter reads the answer as it passes
        extra = dict(target.headers)
        if betas := extra.pop("anthropic-beta", None):
            given = headers.get("anthropic-beta")
            headers["anthropic-beta"] = ",".join(x for x in (given, betas) if x)
        headers |= extra
        if target.sign is not None:
            headers |= target.sign(request.method, target.url, body)
        timeout = httpx2.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)
        client = httpx2.AsyncClient(timeout=timeout, transport=transport, follow_redirects=False)
        sent = client.build_request(request.method, target.url, headers=headers, content=body)
        try:
            answer = await client.send(sent, stream=True)
        except httpx2.HTTPError as error:
            await client.aclose()
            return _refused(502, f"the model couldn't be reached ({type(error).__name__})")
        counter = Counter(kind)

        async def stream() -> AsyncIterator[bytes]:
            try:
                async for chunk in answer.aiter_bytes():
                    counter.feed(chunk)
                    yield chunk
            finally:
                await answer.aclose()
                await client.aclose()
                counter.finish()
                if any(counter.tokens.values()):
                    await anyio.to_thread.run_sync(_record, claims, kind, counter)

        passed = {k: v for k, v in answer.headers.items() if k.lower() not in DROPPED_RESPONSE}
        return StreamingResponse(stream(), status_code=answer.status_code, headers=passed)

    return Route("/model/{kind}/{tail:path}", endpoint=proxy, methods=["GET", "POST"])
