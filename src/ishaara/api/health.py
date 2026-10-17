"""Health check endpoint."""

from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter

router = APIRouter()


def _package_version() -> str:
    """Return the installed package version, or ``"unknown"`` if not installed."""
    try:
        return version("ishaara-backend")
    except PackageNotFoundError:
        return "unknown"


@router.get("/health")
@router.get("/healthz")
async def health() -> dict[str, str]:
    """Report liveness.

    ``/health`` exists because Cloud Run's front end reserves paths such as ``/healthz`` and
    answers them with its own 404 before the request reaches the app; ``/healthz`` still works
    everywhere else (local runs, Docker, other hosts).

    Returns:
        A body with ``status`` and the service ``version``.
    """
    return {"status": "ok", "version": _package_version()}
