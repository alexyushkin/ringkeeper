from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from interlock import __version__
from interlock.api.routes import router
from interlock.errors import NotFoundError


async def _not_found(_request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(error)})


def create_app() -> FastAPI:
    app = FastAPI(
        title="Interlock",
        version=__version__,
        description="Read-only view of Interlock's projects and instances. Never returns secrets.",
    )
    app.include_router(router)
    app.add_exception_handler(NotFoundError, _not_found)
    return app
