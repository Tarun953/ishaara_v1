import asyncio
from datetime import datetime

import pytest
from structlog.testing import capture_logs

from ishaara.clients.memory_repository import InMemoryVideoRepository
from ishaara.models.domain import VideoStatus
from ishaara.services.job_runner import JobRunner
from tests.contract.conftest import FakeClock, make_record


class SpyVideoRepository(InMemoryVideoRepository):
    def __init__(self, clock: FakeClock) -> None:
        super().__init__(clock)
        self.lease_renewals = 0

    async def renew_lease(self, video_key: str, lease_expires_at: datetime) -> None:
        self.lease_renewals += 1
        await super().renew_lease(video_key, lease_expires_at)


@pytest.fixture
def videos() -> SpyVideoRepository:
    return SpyVideoRepository(FakeClock())


def _runner(videos: SpyVideoRepository, pipeline) -> JobRunner:  # type: ignore[no-untyped-def]
    return JobRunner(videos, pipeline, videos._clock, heartbeat_interval_seconds=0.01)


async def test_runs_the_pipeline_and_releases_the_task(videos: SpyVideoRepository) -> None:
    ran: list[str] = []

    async def pipeline(video_key: str) -> None:
        ran.append(video_key)

    runner = _runner(videos, pipeline)

    assert runner.start("abc") is True
    assert runner.running_keys == {"abc"}
    await runner.wait_until_idle()

    assert ran == ["abc"]
    assert runner.running_keys == set()
    assert runner.start("abc") is True  # a finished key can run again
    await runner.wait_until_idle()


async def test_heartbeat_renews_the_lease_while_running_and_stops_after(
    videos: SpyVideoRepository,
) -> None:
    await videos.create_if_absent(make_record("abc"))
    release = asyncio.Event()

    async def pipeline(video_key: str) -> None:
        await release.wait()

    runner = _runner(videos, pipeline)
    runner.start("abc")
    await asyncio.sleep(0.08)

    assert videos.lease_renewals >= 2

    release.set()
    await runner.wait_until_idle()
    renewals_at_finish = videos.lease_renewals
    await asyncio.sleep(0.05)
    assert videos.lease_renewals == renewals_at_finish


async def test_duplicate_start_is_ignored(videos: SpyVideoRepository) -> None:
    release = asyncio.Event()
    calls = 0

    async def pipeline(video_key: str) -> None:
        nonlocal calls
        calls += 1
        await release.wait()

    runner = _runner(videos, pipeline)

    assert runner.start("abc") is True
    assert runner.start("abc") is False
    release.set()
    await runner.wait_until_idle()

    assert calls == 1


async def test_crash_is_logged_and_marks_the_video_failed(videos: SpyVideoRepository) -> None:
    await videos.create_if_absent(make_record("abc", status=VideoStatus.PROCESSING))

    async def pipeline(video_key: str) -> None:
        raise RuntimeError("kaboom")

    runner = _runner(videos, pipeline)
    with capture_logs() as logs:
        runner.start("abc")
        await runner.wait_until_idle()

    stored = await videos.get("abc")
    assert stored is not None
    assert (stored.status, stored.error_code) == (VideoStatus.FAILED, "internal_error")
    assert any(entry["event"] == "job_crashed" for entry in logs)


async def test_shutdown_cancels_running_jobs(videos: SpyVideoRepository) -> None:
    async def pipeline(video_key: str) -> None:
        await asyncio.Event().wait()

    runner = _runner(videos, pipeline)
    runner.start("abc")
    await asyncio.sleep(0)

    await runner.shutdown()

    assert runner.running_keys == set()


async def test_shutdown_expires_the_lease_so_the_next_request_restarts_the_job(
    videos: SpyVideoRepository,
) -> None:
    from datetime import timedelta

    clock = videos._clock
    live_lease = clock() + timedelta(seconds=90)
    await videos.create_if_absent(
        make_record("abc", status=VideoStatus.PROCESSING, lease_expires_at=live_lease)
    )

    async def pipeline(video_key: str) -> None:
        await asyncio.Event().wait()

    runner = _runner(videos, pipeline)
    runner.start("abc")
    await asyncio.sleep(0)
    before = await videos.get("abc")
    assert before is not None and not before.is_restartable(clock())

    await runner.shutdown()

    after = await videos.get("abc")
    assert after is not None
    assert after.is_restartable(clock())
    assert after.status == VideoStatus.PROCESSING  # left for the next request to restart


async def test_shutdown_with_nothing_running_touches_nothing(videos: SpyVideoRepository) -> None:
    async def pipeline(video_key: str) -> None:
        return None

    runner = _runner(videos, pipeline)

    await runner.shutdown()

    assert videos.lease_renewals == 0


async def test_a_failing_lease_write_during_shutdown_does_not_raise(
    videos: SpyVideoRepository,
) -> None:
    async def broken_renew(video_key: str, lease_expires_at: object) -> None:
        raise RuntimeError("database is gone")

    videos.renew_lease = broken_renew  # type: ignore[method-assign]

    async def pipeline(video_key: str) -> None:
        await asyncio.Event().wait()

    runner = _runner(videos, pipeline)
    runner.start("abc")
    await asyncio.sleep(0)

    await runner.shutdown()  # must not raise

    assert runner.running_keys == set()
