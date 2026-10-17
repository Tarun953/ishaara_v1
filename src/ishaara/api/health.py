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


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Report liveness.

    Returns:
        A body with ``status`` and the service ``version``.
    """
    return {"status": "ok", "version": _package_version()}
