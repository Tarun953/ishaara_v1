"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ishaara.api import health
from ishaara.errors import IshaaraError, problem_json_handler
from ishaara.logging_config import configure_logging
from ishaara.settings import get_settings


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configure logging and own the shared HTTP client for the app's lifetime."""
    configure_logging(get_settings().log_level)
    async with httpx.AsyncClient() as http_client:
        app.state.http_client = http_client
        yield


def create_app() -> FastAPI:
    """Build the Ishaara FastAPI application.

    Returns:
        The configured ``FastAPI`` app with CORS, error handling and routers.
    """
    settings = get_settings()
    app = FastAPI(title="Ishaara API", lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(IshaaraError, problem_json_handler)
    app.include_router(health.router)
    return app
