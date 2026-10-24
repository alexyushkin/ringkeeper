"""The MCP gateway: one Streamable HTTP endpoint per service type.

    /mcp/supabase   /mcp/n8n   (one per registered adapter)
    /health

Every request must carry `Authorization: Bearer <worker token>`. The token is
verified on every request (the endpoints are stateless), so an expired token
stops working mid-session. Everything about the caller comes from the token;
the tool call then goes through `services.calls.run_call`.
"""

import inspect
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Annotated, Any, get_type_hints

import anyio.to_thread
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError as McpToolError
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.http import StarletteWithLifespan
from mcp_types import ToolAnnotations
from pydantic import Field
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from interlock import __version__
from interlock.adapters import ADAPTERS
from interlock.adapters.base import Adapter, ToolKind, ToolSpec
from interlock.calls import Caller, ToolCall
from interlock.config import get_settings
from interlock.errors import InterlockError
from interlock.keys import load_master_key
from interlock.models import InstanceType
from interlock.scrub import install_log_scrubbing, scrub
from interlock.services.calls import run_call, unauthenticated
from interlock.tokens import TokenError, verify_token
from interlock.vault import Vault, get_vault

CALLER_KEY = "interlock_caller"

InstanceArg = Annotated[
    str | None,
    Field(
        description=(
            "Instance to act on. Optional for reads (the project's default is used); "
            "required for writes."
        )
    ),
]


class WorkerTokenAuth:
    """ASGI middleware: verify the bearer worker token, or answer 401 with the reason."""

    def __init__(self, app: ASGIApp, service: InstanceType) -> None:
        self.app = app
        self.service = service

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        scheme, _, token = Headers(scope=scope).get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            reason = "a bearer worker token is required (Authorization: Bearer <token>)"
        else:
            try:
                claims = await anyio.to_thread.run_sync(verify_token, token.strip())
            except TokenError as error:
                reason = str(error)
            else:
                caller = Caller(claims.project, claims.session_id, claims.token_id)
                scope.setdefault("state", {})[CALLER_KEY] = caller
                await self.app(scope, receive, send)
                return
        await anyio.to_thread.run_sync(unauthenticated, self.service, reason)
        response = JSONResponse(
            {"error": "invalid_token", "error_description": reason},
            status_code=401,
            headers={
                "WWW-Authenticate": f'Bearer error="invalid_token", error_description="{reason}"'
            },
        )
        await response(scope, receive, send)


def current_caller() -> Caller:
    state: dict[str, Any] = get_http_request().scope.get("state", {})
    caller = state.get(CALLER_KEY)
    return caller if isinstance(caller, Caller) else Caller(None, None, None)


def _agent_description(spec: ToolSpec) -> str:
    if spec.kind is ToolKind.READ:
        return f"{spec.description}\n\n[read]"
    return (
        f"{spec.description}\n\n[write] Name the instance explicitly. On prod instances this is "
        "held for human approval unless pre-approved; the result then says so and gives a "
        "decision id."
    )


def make_tool(service: InstanceType, spec: ToolSpec, vault: Vault) -> Callable[..., Awaitable[Any]]:
    """Wrap an adapter handler as an MCP tool: drop `target`, add `instance`."""
    hints = get_type_hints(spec.handler, include_extras=True)
    handler_params = list(inspect.signature(spec.handler).parameters.values())[1:]
    params = [
        p.replace(kind=inspect.Parameter.KEYWORD_ONLY, annotation=hints[p.name])
        for p in handler_params
    ]
    params.append(
        inspect.Parameter(
            "instance", inspect.Parameter.KEYWORD_ONLY, default=None, annotation=InstanceArg
        )
    )

    async def tool(**kwargs: Any) -> dict[str, Any]:
        instance = kwargs.pop("instance", None)
        call = ToolCall(current_caller(), service, spec.name, instance, kwargs)
        try:
            return await run_call(call, vault)
        except InterlockError as error:
            # Shown to the agent verbatim, so it can relay the refusal.
            raise McpToolError(scrub(str(error))) from None

    tool.__name__ = spec.name
    tool.__doc__ = _agent_description(spec)
    tool.__signature__ = inspect.Signature(params, return_annotation=dict[str, Any])  # pyright: ignore[reportFunctionMemberAccess]
    tool.__annotations__ = {p.name: p.annotation for p in params} | {"return": dict[str, Any]}
    return tool


def build_server(adapter: Adapter, vault: Vault) -> FastMCP:
    server = FastMCP(
        name=f"interlock-{adapter.service}",
        version=__version__,
        instructions=(
            f"Interlock gateway for {adapter.service}. You can only reach instances bound to "
            "your project. Refusals explain why; relay them rather than retrying."
        ),
        mask_error_details=True,  # unexpected exceptions never reach the agent
    )
    for spec in adapter.tools:
        is_read = spec.kind is ToolKind.READ
        server.tool(
            make_tool(adapter.service, spec, vault),
            name=spec.name,
            description=_agent_description(spec),
            annotations=ToolAnnotations(
                read_only_hint=is_read, destructive_hint=not is_read, open_world_hint=True
            ),
        )
    return server


async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "services": sorted(ADAPTERS)})


def create_app(vault: Vault | None = None) -> Starlette:
    install_log_scrubbing()
    # The gateway never mints a master key: a fresh one would silently orphan the
    # vault and every token. Create it on the host first (any secret/token command).
    load_master_key(get_settings(), create=False)
    vault = vault or get_vault()
    routes: list[Route] = [Route("/health", health)]
    apps: list[StarletteWithLifespan] = []
    for service, adapter in ADAPTERS.items():
        path = f"/mcp/{service}"
        app = build_server(adapter, vault).http_app(
            path=path, stateless_http=True, json_response=True
        )
        apps.append(app)
        # Route (not Mount) keeps the full path, so /mcp/supabase works without a trailing slash.
        routes.append(
            Route(path, endpoint=WorkerTokenAuth(app, service), methods=["GET", "POST", "DELETE"])
        )

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncGenerator[None]:
        # Each FastMCP app has its own session manager; nested lifespans don't run
        # by themselves, so start them all here.
        async with AsyncExitStack() as stack:
            for app in apps:
                await stack.enter_async_context(app.lifespan(app))
            yield

    return Starlette(routes=routes, lifespan=lifespan)
