import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ringkeeper import __version__
from ringkeeper.api.routes import public, router
from ringkeeper.config import get_settings
from ringkeeper.errors import NotFoundError
from ringkeeper.scrub import install_log_scrubbing


async def _not_found(_request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(error)})


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Run the session manager alongside the API when this process is configured to.

    Only the api service sets RINGKEEPER_SESSION_MANAGER, and only it mounts the
    Docker socket (which makes it root-equivalent on the host).
    """
    stop = threading.Event()
    if get_settings().session_manager:
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
    return app
