"""Behaviour every VideoRepository / UsageRepository implementation must share."""

import asyncio
from datetime import timedelta

from ishaara.clients.repositories import DailyUsageField
from ishaara.models.domain import (
    NarrationCue,
    PipelineStage,
    ReferentKind,
    VideoStatus,
)
from tests.contract.conftest import Backend, make_record


def _cue() -> NarrationCue:
    return NarrationCue(
        cue_id="cue-0001",
        start_seconds=5.0,
        spoken_phrase="",
        referent_kind=ReferentKind.TEXT,
        board_description="A slide title.",
        gap_summary="A slide title appears.",
        confidence=0.9,
    )


async def test_get_missing_returns_none(backend: Backend) -> None:
    assert await backend.videos.get("nope") is None


async def test_create_if_absent_keeps_the_first_record(backend: Backend) -> None:
    first = make_record(uid="user-1")
    second = make_record(uid="user-2")

    created_record, was_created = await backend.videos.create_if_absent(first)
    existing_record, second_was_created = await backend.videos.create_if_absent(second)

    assert (was_created, second_was_created) == (True, False)
    assert created_record.created_by_uid == "user-1"
    assert existing_record.created_by_uid == "user-1"
    stored = await backend.videos.get(first.video_key)
    assert stored is not None and stored.created_by_uid == "user-1"


async def test_claim_restart_resets_a_failed_job(backend: Backend) -> None:
    record = make_record(status=VideoStatus.FAILED)
    await backend.videos.create_if_absent(record)
    await backend.videos.mark_failed(record.video_key, "boom", "it broke")
    lease = backend.clock() + timedelta(seconds=90)

    restarted = await backend.videos.claim_restart(record.video_key, lease)

    assert restarted is not None
    assert restarted.status == VideoStatus.QUEUED
    assert restarted.error_code is None and restarted.progress_percent == 0
    assert restarted.lease_expires_at == lease


async def test_claim_restart_refuses_a_live_lease_then_allows_it_after_expiry(
    backend: Backend,
) -> None:
    lease = backend.clock() + timedelta(seconds=90)
    record = make_record(status=VideoStatus.PROCESSING, lease_expires_at=lease)
    await backend.videos.create_if_absent(record)
    new_lease = backend.clock() + timedelta(seconds=300)

    assert await backend.videos.claim_restart(record.video_key, new_lease) is None

    backend.clock.advance(seconds=91)
    assert await backend.videos.claim_restart(record.video_key, new_lease) is not None


async def test_claim_restart_refuses_ready_and_missing(backend: Backend) -> None:
    record = make_record()
    await backend.videos.create_if_absent(record)
    await backend.videos.mark_ready(record.video_key, [_cue()], {}, {}, None)
    lease = backend.clock() + timedelta(seconds=90)

    assert await backend.videos.claim_restart(record.video_key, lease) is None
    assert await backend.videos.claim_restart("missing", lease) is None


async def test_progress_and_lease_updates(backend: Backend) -> None:
    record = make_record()
    await backend.videos.create_if_absent(record)
    lease = backend.clock() + timedelta(seconds=60)

    await backend.videos.update_progress(record.video_key, PipelineStage.ANALYZING, 40)
    await backend.videos.renew_lease(record.video_key, lease)

    stored = await backend.videos.get(record.video_key)
    assert stored is not None
    assert (stored.status, stored.stage, stored.progress_percent) == (
        VideoStatus.PROCESSING,
        PipelineStage.ANALYZING,
        40,
    )
    assert stored.lease_expires_at == lease


async def test_mark_ready_stores_the_result(backend: Backend) -> None:
    record = make_record(status=VideoStatus.PROCESSING)
    await backend.videos.create_if_absent(record)

    await backend.videos.mark_ready(
        record.video_key, [_cue()], {"analysis": "gemini-3.8-flash"}, {"input": 10}, "t/x.json"
    )

    stored = await backend.videos.get(record.video_key)
    assert stored is not None
    assert stored.status == VideoStatus.READY and stored.progress_percent == 100
    assert [cue.cue_id for cue in stored.cues] == ["cue-0001"]
    assert stored.model_versions == {"analysis": "gemini-3.8-flash"}
    assert stored.token_usage == {"input": 10}
    assert stored.transcript_blob_path == "t/x.json"
    assert stored.lease_expires_at is None


async def test_mark_failed_stores_the_error(backend: Backend) -> None:
    record = make_record(status=VideoStatus.PROCESSING)
    await backend.videos.create_if_absent(record)

    await backend.videos.mark_failed(record.video_key, "gemini_error", "Gemini failed")

    stored = await backend.videos.get(record.video_key)
    assert stored is not None
    assert (stored.status, stored.error_code) == (VideoStatus.FAILED, "gemini_error")


async def test_history_is_newest_first_limited_and_per_user(backend: Backend) -> None:
    start = backend.clock()
    for offset, video_key in enumerate(
        ["aaaaaaaaaaa_en_v1", "bbbbbbbbbbb_en_v1", "ccccccccccc_en_v1"]
    ):
        await backend.videos.record_submission(
            "user-1", video_key, video_key, start + timedelta(minutes=offset)
        )
    await backend.videos.record_submission("user-2", "ddddddddddd_en_v1", "other", start)

    rows = await backend.videos.list_for_user("user-1", limit=2)

    assert [row.video_key for row in rows] == ["ccccccccccc_en_v1", "bbbbbbbbbbb_en_v1"]


async def test_resubmitting_does_not_duplicate_history(backend: Backend) -> None:
    start = backend.clock()
    await backend.videos.record_submission("user-1", "aaaaaaaaaaa_en_v1", "t", start)
    await backend.videos.record_submission(
        "user-1", "aaaaaaaaaaa_en_v1", "t", start + timedelta(minutes=5)
    )

    rows = await backend.videos.list_for_user("user-1", limit=10)

    assert len(rows) == 1
    assert rows[0].submitted_at == start + timedelta(minutes=5)


async def test_quota_counts_up_to_the_limit_then_refuses(backend: Backend) -> None:
    field = DailyUsageField.VIDEOS_SUBMITTED

    results = [await backend.usage.increment_and_check("user-1", field, 2) for _ in range(4)]

    assert results == [True, True, False, False]


async def test_quota_is_per_user_per_field_and_resets_next_day(backend: Backend) -> None:
    videos, questions = DailyUsageField.VIDEOS_SUBMITTED, DailyUsageField.QUESTIONS_ASKED
    assert await backend.usage.increment_and_check("user-1", videos, 1)

    assert not await backend.usage.increment_and_check("user-1", videos, 1)
    assert await backend.usage.increment_and_check("user-2", videos, 1)
    assert await backend.usage.increment_and_check("user-1", questions, 1)

    backend.clock.advance(days=1)
    assert await backend.usage.increment_and_check("user-1", videos, 1)


async def test_concurrent_increments_never_exceed_the_limit(backend: Backend) -> None:
    field = DailyUsageField.VIDEOS_SUBMITTED

    results = await asyncio.gather(
        *(backend.usage.increment_and_check("user-1", field, 3) for _ in range(10))
    )

    assert results.count(True) == 3
