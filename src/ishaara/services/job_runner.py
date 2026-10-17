"""In-process background job runner with a lease heartbeat.

Jobs run as ``asyncio`` tasks inside the API process. While a job runs, a heartbeat keeps
renewing the video's lease; if the instance dies the lease expires and the next request
restarts the job (see ``VideoRecord.is_restartable``).
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import structlog

from ishaara.clients.repositories import VideoRepository
from ishaara.clock import Clock, utc_now

PipelineRunner = Callable[[str], Awaitable[None]]

DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0
DEFAULT_LEASE_SECONDS = 90.0
INTERNAL_ERROR_CODE = "internal_error"

logger = structlog.get_logger()


class JobRunner:
    """Runs video pipelines as background tasks and keeps their leases alive.

    Args:
        videos: Where leases and failures are written.
        run_pipeline: Coroutine function that processes one ``video_key``.
        clock: Source of the current time.
        heartbeat_interval_seconds: How often the lease is renewed.
        lease_seconds: How far ahead each renewal sets the lease expiry.
    """

    def __init__(
        self,
        videos: VideoRepository,
        run_pipeline: PipelineRunner,
        clock: Clock = utc_now,
        heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> None:
        """Store collaborators; no task is started."""
        self._videos = videos
        self._run_pipeline = run_pipeline
        self._clock = clock
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._lease_seconds = lease_seconds
        self._tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def running_keys(self) -> set[str]:
        """Return the video keys with a job running in this process."""
        return set(self._tasks)

    def lease_expiry(self) -> datetime:
        """Return the lease expiry to set for a job that is alive right now."""
        return self._clock() + timedelta(seconds=self._lease_seconds)

    def start(self, video_key: str) -> bool:
        """Start a background job unless one is already running for this key.

        Args:
            video_key: Key of the video to process.

        Returns:
            ``True`` if a task was started, ``False`` if one is already running here.
        """
        if video_key in self._tasks:
            return False
        task = asyncio.create_task(self._run(video_key), name=f"job-{video_key}")
        self._tasks[video_key] = task  # strong reference: keeps the task from being collected
        task.add_done_callback(lambda _finished: self._tasks.pop(video_key, None))
        return True

    async def wait_until_idle(self) -> None:
        """Wait for every running job to finish."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)

    async def shutdown(self) -> None:
        """Cancel every running job and wait for the cancellations to finish."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, video_key: str) -> None:
        """Run the pipeline with a heartbeat; log and record any crash."""
        heartbeat = asyncio.create_task(self._heartbeat(video_key))
        try:
            await self._run_pipeline(video_key)
        except Exception:
            logger.exception("job_crashed", video_key=video_key)
            await self._mark_crashed(video_key)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _heartbeat(self, video_key: str) -> None:
        """Renew the lease at a fixed interval until cancelled."""
        while True:
            await asyncio.sleep(self._heartbeat_interval_seconds)
            try:
                await self._videos.renew_lease(video_key, self.lease_expiry())
            except Exception:
                logger.warning("lease_renewal_failed", video_key=video_key, exc_info=True)

    async def _mark_crashed(self, video_key: str) -> None:
        """Safety net: mark the video failed if the pipeline crashed without doing so."""
        try:
            await self._videos.mark_failed(
                video_key, INTERNAL_ERROR_CODE, "Processing failed unexpectedly."
            )
        except Exception:
            logger.error("mark_failed_error", video_key=video_key, exc_info=True)
