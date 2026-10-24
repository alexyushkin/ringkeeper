import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ringkeeper import __version__
from ringkeeper.api.routes import public, router
from ringkeeper.config import get_settings
from ringkeeper.errors import NotFoundError, RuleViolationError
from ringkeeper.scrub import install_log_scrubbing


async def _not_found(_request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(error)})


async def _rule_violation(_request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(error)})


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Run the session manager and router alongside the API when configured to.

    Only the api service sets RINGKEEPER_SESSION_MANAGER, and only it mounts the
    Docker socket (which makes it root-equivalent on the host).
    """
    stop = threading.Event()
    settings = get_settings()
    if settings.router:
        from ringkeeper.router.model import AgentSdkModel  # noqa: PLC0415 (Agent SDK: only here)
        from ringkeeper.router.service import Router  # noqa: PLC0415
        from ringkeeper.vault import get_vault  # noqa: PLC0415

        install_log_scrubbing()
        router = Router(AgentSdkModel(get_vault(settings), settings), settings)
        threading.Thread(
            target=router.run_forever, args=(stop,), daemon=True, name="rk-router"
        ).start()
    if settings.telegram:
        from ringkeeper.telegram.api import from_vault  # noqa: PLC0415
        from ringkeeper.telegram.bridge import Bridge  # noqa: PLC0415
        from ringkeeper.vault import get_vault  # noqa: PLC0415

        install_log_scrubbing()
        bridge = Bridge(from_vault(get_vault(settings), settings), settings)
        threading.Thread(
            target=bridge.run_forever, args=(stop,), daemon=True, name="rk-telegram"
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
    app.add_exception_handler(NotFoundError, _not_found)
    app.add_exception_handler(RuleViolationError, _rule_violation)
    return app
