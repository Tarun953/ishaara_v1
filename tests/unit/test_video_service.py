import asyncio
from datetime import timedelta

import pytest

from ishaara.clients.memory_repository import InMemoryUsageRepository, InMemoryVideoRepository
from ishaara.errors import DailyLimitReachedError, VideoNotPublicError
from ishaara.models.api_models import AuthenticatedUser
from ishaara.models.domain import NarrationLanguage, VideoStatus
from ishaara.services.job_runner import JobRunner
from ishaara.services.video_service import VideoService
from ishaara.settings import Settings
from tests.conftest import FakeMetadataSource
from tests.contract.conftest import FakeClock, make_record

URL = "https://youtu.be/dQw4w9WgXcQ"
VIDEO_KEY = "dQw4w9WgXcQ_en_v1"
USER = AuthenticatedUser(uid="user-1")
ENGLISH = NarrationLanguage.ENGLISH


class Harness:
    def __init__(self, daily_video_limit: int = 10, privacy_status: str = "public") -> None:
        self.clock = FakeClock()
        self.videos = InMemoryVideoRepository(self.clock)
        self.usage = InMemoryUsageRepository(self.clock)
        self.metadata = FakeMetadataSource(privacy_status)
        self.started: list[str] = []
        self.release = asyncio.Event()
        self.runner = JobRunner(self.videos, self._pipeline, self.clock, lease_seconds=90)
        self.service = VideoService(
            self.videos,
            self.usage,
            self.metadata,
            self.runner,
            Settings(_env_file=None, daily_video_limit=daily_video_limit),
            self.clock,
        )

    async def _pipeline(self, video_key: str) -> None:
        self.started.append(video_key)
        await self.release.wait()

    async def finish(self) -> None:
        self.release.set()
        await self.runner.wait_until_idle()


@pytest.fixture
async def harness() -> Harness:
    return Harness()


async def test_new_video_is_queued_and_a_job_starts(harness: Harness) -> None:
    result = await harness.service.submit(USER, URL, ENGLISH)
    await asyncio.sleep(0)

    assert result.http_status == 202
    assert result.video.video_key == VIDEO_KEY
    assert result.video.status == VideoStatus.QUEUED
    assert harness.started == [VIDEO_KEY]
    await harness.finish()


async def test_submitting_the_same_url_twice_starts_one_job(harness: Harness) -> None:
    first = await harness.service.submit(USER, URL, ENGLISH)
    second = await harness.service.submit(USER, URL, ENGLISH)
    await asyncio.sleep(0)

    assert (first.http_status, second.http_status) == (202, 202)
    assert harness.started == [VIDEO_KEY]
    assert harness.metadata.fetches == 1
    await harness.finish()


async def test_ready_video_returns_200_without_a_job_or_quota(harness: Harness) -> None:
    await harness.videos.create_if_absent(make_record(VIDEO_KEY))
    await harness.videos.mark_ready(VIDEO_KEY, [], {}, {}, None)

    result = await harness.service.submit(AuthenticatedUser(uid="user-2"), URL, ENGLISH)

    assert result.http_status == 200
    assert result.video.status == VideoStatus.READY
    assert harness.started == []
    assert await harness.usage.increment_and_check("user-2", _videos_field(), 1) is True
    history = await harness.videos.list_for_user("user-2", 10)
    assert [row.video_key for row in history] == [VIDEO_KEY]


async def test_expired_lease_restarts_the_job(harness: Harness) -> None:
    lease = harness.clock() + timedelta(seconds=90)
    await harness.videos.create_if_absent(
        make_record(VIDEO_KEY, status=VideoStatus.PROCESSING, lease_expires_at=lease)
    )
    harness.clock.advance(seconds=120)

    result = await harness.service.submit(USER, URL, ENGLISH)
    await asyncio.sleep(0)

    assert result.http_status == 202
    assert harness.started == [VIDEO_KEY]
    await harness.finish()


async def test_failed_video_is_restarted(harness: Harness) -> None:
    await harness.videos.create_if_absent(make_record(VIDEO_KEY))
    await harness.videos.mark_failed(VIDEO_KEY, "gemini_error", "boom")

    result = await harness.service.submit(USER, URL, ENGLISH)
    await asyncio.sleep(0)

    assert result.video.status == VideoStatus.QUEUED
    assert result.video.error_code is None
    assert harness.started == [VIDEO_KEY]
    await harness.finish()


async def test_live_lease_is_joined_not_restarted(harness: Harness) -> None:
    lease = harness.clock() + timedelta(seconds=90)
    await harness.videos.create_if_absent(
        make_record(VIDEO_KEY, status=VideoStatus.PROCESSING, lease_expires_at=lease)
    )

    result = await harness.service.submit(USER, URL, ENGLISH)

    assert result.http_status == 202
    assert harness.started == []


async def test_daily_limit_returns_429(harness: Harness) -> None:
    limited = Harness(daily_video_limit=1)
    await limited.service.submit(USER, URL, ENGLISH)

    with pytest.raises(DailyLimitReachedError) as error_info:
        await limited.service.submit(USER, "https://youtu.be/abcdefghijk", ENGLISH)

    assert error_info.value.status == 429
    await limited.finish()


async def test_invalid_video_uses_no_quota_and_stores_nothing() -> None:
    private = Harness(daily_video_limit=1, privacy_status="private")

    with pytest.raises(VideoNotPublicError):
        await private.service.submit(USER, URL, ENGLISH)

    assert await private.videos.get(VIDEO_KEY) is None
    assert await private.usage.increment_and_check("user-1", _videos_field(), 1) is True


def _videos_field():  # type: ignore[no-untyped-def]
    from ishaara.clients.repositories import DailyUsageField

    return DailyUsageField.VIDEOS_SUBMITTED
