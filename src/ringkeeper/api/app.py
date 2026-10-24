from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ringkeeper import __version__
from ringkeeper.api.routes import router
from ringkeeper.errors import NotFoundError


async def _not_found(_request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(error)})


def create_app() -> FastAPI:
    app = FastAPI(
        title="Ringkeeper",
        version=__version__,
        description="Read-only view of Ringkeeper's projects and instances. Never returns secrets.",
    )
    app.include_router(router)
    app.add_exception_handler(NotFoundError, _not_found)
    return app
