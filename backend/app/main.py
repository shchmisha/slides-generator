import logging

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import artifacts, auth, brand, health, jobs, projects, scenarios, voice
from app.api.errors import describe_errors
from app.auth.deps import require_csrf_header
from app.config import get_settings
from app.log_config import configure_logging

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    docs = settings.enable_api_docs  # off unless ENABLE_API_DOCS=true: a map of the API
    app = FastAPI(
        title="Slides Generator API",
        docs_url="/api/docs" if docs else None,
        openapi_url="/api/openapi.json" if docs else None,
        redoc_url=None,
    )
    app.include_router(health.router, prefix="/api")
    csrf = [Depends(require_csrf_header)]
    app.include_router(auth.router, prefix="/api", dependencies=csrf)
    for module in (projects, brand, scenarios, jobs, artifacts, voice):
        app.include_router(module.router, prefix="/api", dependencies=csrf)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": describe_errors(exc.errors())})

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        # Never leak internals (paths, SQL, keys) to the client; full trace goes to the log.
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера"})

    return app


app = create_app()
