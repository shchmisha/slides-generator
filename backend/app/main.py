import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import health
from app.config import get_settings
from app.log_config import configure_logging

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    configure_logging(get_settings().log_level)

    app = FastAPI(
        title="Slides Generator API",
        # Interactive docs are useful locally but are an information leak in production;
        # they stay under /api so nginx can expose or hide them in one place.
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.include_router(health.router, prefix="/api")

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        # Never leak internals (paths, SQL, keys) to the client; full trace goes to the log.
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера"})

    return app


app = create_app()
