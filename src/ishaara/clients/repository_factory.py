"""Chooses the storage backend from settings."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ishaara.clients.memory_repository import InMemoryUsageRepository, InMemoryVideoRepository
from ishaara.clients.repositories import UsageRepository, VideoRepository
from ishaara.clock import Clock, utc_now
from ishaara.settings import Settings


async def _nothing_to_close() -> None:
    """Close hook for backends that hold no resources."""


@dataclass(frozen=True)
class Repositories:
    """The storage ports the app uses.

    Attributes:
        videos: Video and history storage.
        usage: Daily quota counters.
        close_callback: Releases connections; called on shutdown.
    """

    videos: VideoRepository
    usage: UsageRepository
    close_callback: Callable[[], Awaitable[None]] = _nothing_to_close

    async def close(self) -> None:
        """Release any connections held by the backend."""
        await self.close_callback()


def build_repositories(settings: Settings, clock: Clock = utc_now) -> Repositories:
    """Build the repositories for ``settings.database_backend``.

    Args:
        settings: Application settings.
        clock: Source of the current time.

    Returns:
        The video and usage repositories for the chosen backend.

    Raises:
        ValueError: If the Supabase backend is chosen without ``SUPABASE_DB_URL``.
    """
    if settings.database_backend == "firestore":
        from google.cloud import firestore

        from ishaara.clients.firestore_repository import (
            FirestoreUsageRepository,
            FirestoreVideoRepository,
        )

        client = firestore.AsyncClient(project=settings.gcp_project_id or None)
        return Repositories(
            FirestoreVideoRepository(client, clock), FirestoreUsageRepository(client, clock)
        )
    if settings.database_backend == "supabase":
        from ishaara.clients.supabase_repository import (
            SupabaseDatabase,
            SupabaseUsageRepository,
            SupabaseVideoRepository,
        )

        if not settings.supabase_db_url:
            raise ValueError("DATABASE_BACKEND=supabase needs SUPABASE_DB_URL")
        database = SupabaseDatabase(settings.supabase_db_url)
        return Repositories(
            SupabaseVideoRepository(database, clock),
            SupabaseUsageRepository(database, clock),
            database.close,
        )
    return Repositories(InMemoryVideoRepository(clock), InMemoryUsageRepository(clock))
