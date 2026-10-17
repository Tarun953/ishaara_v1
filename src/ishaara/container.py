"""The composition root: builds every long-lived collaborator once."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from ishaara.clients.blob_storage_factory import build_blob_storage
from ishaara.clients.gemini_gateway import GoogleGeminiGateway
from ishaara.clients.repositories import UsageRepository, VideoRepository
from ishaara.clients.repository_factory import build_repositories
from ishaara.clients.youtube_metadata import YouTubeMetadataClient
from ishaara.pipeline.orchestrator import VideoPipeline
from ishaara.services.job_runner import JobRunner
from ishaara.services.video_service import VideoService
from ishaara.settings import Settings


async def _nothing_to_close() -> None:
    """Close hook for containers that hold no extra resources."""


@dataclass(frozen=True)
class AppContainer:
    """Everything the request handlers need.

    Attributes:
        video_service: Accepts submissions.
        videos: Video and history storage.
        usage: Daily quota counters.
        job_runner: Runs background jobs.
        close_callback: Releases connections after the jobs are stopped.
    """

    video_service: VideoService
    videos: VideoRepository
    usage: UsageRepository
    job_runner: JobRunner
    close_callback: Callable[[], Awaitable[None]] = _nothing_to_close

    async def close(self) -> None:
        """Cancel running jobs, then release connections."""
        await self.job_runner.shutdown()
        await self.close_callback()


def build_container(settings: Settings, http_client: httpx.AsyncClient) -> AppContainer:
    """Wire the real collaborators from settings.

    Args:
        settings: Application settings.
        http_client: Shared HTTP client for the YouTube Data API and Supabase Storage.

    Returns:
        A container using the configured database backend and the real Gemini gateway.
    """
    repositories = build_repositories(settings)
    metadata_client = YouTubeMetadataClient(settings.youtube_api_key, http_client)
    pipeline = VideoPipeline(
        repositories.videos,
        metadata_client,
        GoogleGeminiGateway(settings),
        build_blob_storage(settings, http_client),
        settings,
    )
    job_runner = JobRunner(repositories.videos, pipeline.run)
    video_service = VideoService(
        repositories.videos, repositories.usage, metadata_client, job_runner, settings
    )
    return AppContainer(
        video_service, repositories.videos, repositories.usage, job_runner, repositories.close
    )
