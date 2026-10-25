import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ringkeeper import __version__
from ringkeeper.api import live_routes, manage_routes, owner_routes, work_routes
from ringkeeper.api.routes import public, router
from ringkeeper.api.supervisor import Supervisor
from ringkeeper.config import get_settings
from ringkeeper.errors import AlreadyExistsError, NotFoundError, RuleViolationError
from ringkeeper.scrub import install_log_scrubbing
from ringkeeper.services import owner

MAX_BODY_BYTES = 1_000_000
# Endpoints with their own (bigger) limit, checked as they stream.
OWN_LIMIT_PATHS = frozenset({"/voice/transcribe"})


class BodyLimit:
    """Refuse request bodies over 1 MB (nothing here needs more), by the
    Content-Length header and while reading, so a big body is never buffered."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in OWN_LIMIT_PATHS:
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
            response = JSONResponse(status_code=413, content={"detail": "the request is too big"})
            await response(scope, receive, send)
            return
        seen = 0

        async def limited() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > MAX_BODY_BYTES:
                    raise HTTPException(413, detail="the request is too big")
            return message

        await self.app(scope, limited, send)


async def _invalid(_request: Request, error: Exception) -> JSONResponse:
    """422 with where and what, but never the submitted value: it may be a secret."""
    problems: list[dict[str, Any]] = (
        list(error.errors()) if isinstance(error, RequestValidationError) else []
    )
    detail = [
        {
            "loc": [str(part) for part in p.get("loc", ())],
            "msg": str(p.get("msg", "")),
            "type": str(p.get("type", "")),
        }
        for p in problems
    ]
    return JSONResponse(status_code=422, content={"detail": detail})


async def _not_found(_request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(error)})


async def _rule_violation(_request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(error)})


async def _auth_error(_request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=401, content={"detail": str(error)})


async def _too_many(_request: Request, error: Exception) -> JSONResponse:
    retry = str(getattr(error, "retry_after", 900))
    return JSONResponse(
        status_code=429, content={"detail": str(error)}, headers={"Retry-After": retry}
    )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Run the session manager, router and Telegram bridge alongside the API.

    Only the api service sets RINGKEEPER_SESSION_MANAGER, and only it mounts the
    Docker socket (which makes it root-equivalent on the host). It also does
    the first-start setup (master key, admin token, setup code). The router and
    the bridge run under the supervisor, which applies settings changes live.
    """
    stop = threading.Event()
    settings = get_settings()
    if settings.session_manager:
        from ringkeeper import bootstrap  # noqa: PLC0415

        bootstrap.run(settings)
    supervisor = Supervisor(settings)
    app.state.supervisor = supervisor
    if settings.session_manager or settings.router or settings.telegram:
        install_log_scrubbing()
        threading.Thread(
            target=supervisor.run_forever, args=(stop,), daemon=True, name="rk-supervisor"
        ).start()
    if settings.session_manager:
        from ringkeeper.vault import get_vault  # noqa: PLC0415 (Docker and the vault: only here)
        from ringkeeper.workers.manager import Manager  # noqa: PLC0415
        from ringkeeper.workers.runtime import DockerRuntime  # noqa: PLC0415

        install_log_scrubbing()
        manager = Manager(DockerRuntime(), get_vault())
        threading.Thread(
            target=manager.run_forever, args=(stop,), daemon=True, name="rk-manager"
        ).start()
    try:
        yield
    finally:
        stop.set()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Ringkeeper",
        version=__version__,
        description="Ringkeeper's projects, messages and inbox. Never returns secrets.",
        lifespan=_lifespan,
        # The schema and docs pages would be unauthenticated; leave them off.
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.include_router(public)
    app.include_router(router)
    app.include_router(owner_routes.router)
    app.include_router(manage_routes.router)
    app.include_router(work_routes.router)
    app.include_router(live_routes.router)
    app.add_middleware(BodyLimit)
    app.add_exception_handler(RequestValidationError, _invalid)
    app.add_exception_handler(NotFoundError, _not_found)
    app.add_exception_handler(RuleViolationError, _rule_violation)
    app.add_exception_handler(AlreadyExistsError, _rule_violation)
    app.add_exception_handler(owner.AuthError, _auth_error)
    app.add_exception_handler(owner.TooManyAttemptsError, _too_many)
    return app
