"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from ishaara.api import health, questions, videos
from ishaara.clients.local_blob_storage import MEDIA_URL_PREFIX, PUBLIC_DIRECTORY
from ishaara.container import AppContainer, build_container
from ishaara.errors import (
    IshaaraError,
    http_exception_handler,
    problem_json_handler,
    unexpected_error_handler,
    validation_error_handler,
)
from ishaara.logging_config import configure_logging
from ishaara.settings import get_settings


def create_app(container: AppContainer | None = None) -> FastAPI:
    """Build the Ishaara FastAPI application.

    Args:
        container: Prebuilt collaborators, used by tests to inject fakes. By default the
            real ones are built from settings when the app starts.

    Returns:
        The configured ``FastAPI`` app with CORS, error handling and routers.
    """
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Configure logging, build the container and tear it down on shutdown."""
        configure_logging(settings.log_level)
        if container is not None:
            app.state.container = container
            try:
                yield
            finally:
                await container.close()
            return
        async with httpx.AsyncClient() as http_client:
            app.state.container = build_container(settings, http_client)
            try:
                yield
            finally:
                await app.state.container.close()

    app = FastAPI(title="Ishaara API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(IshaaraError, problem_json_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
    app.include_router(health.router)
    app.include_router(videos.router)
    app.include_router(questions.router)
    if settings.storage_backend == "local":
        # Local development only (production refuses STORAGE_BACKEND=local).
        media_directory = Path(settings.local_storage_dir) / PUBLIC_DIRECTORY
        media_directory.mkdir(parents=True, exist_ok=True)
        app.mount(MEDIA_URL_PREFIX, StaticFiles(directory=media_directory), name="media")
    return app
