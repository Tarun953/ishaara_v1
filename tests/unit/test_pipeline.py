import json

import pytest

from ishaara.clients.memory_repository import InMemoryVideoRepository
from ishaara.errors import GeminiError
from ishaara.models.domain import NarrationLanguage, PipelineStage, ReferentKind, VideoStatus
from ishaara.models.gemini_schemas import ChunkAnalysis, TranscriptSegmentRaw, VisualEvent
from ishaara.pipeline.chunking import ClipWindow
from ishaara.pipeline.orchestrator import VideoPipeline
from ishaara.settings import Settings
from tests.conftest import FakeBlobStorage, FakeGeminiGateway, FakeMetadataSource, make_wav
from tests.contract.conftest import FakeClock, make_record

VIDEO_KEY = "dQw4w9WgXcQ_en_v2"


class ProgressSpyRepository(InMemoryVideoRepository):
    def __init__(self, clock: FakeClock) -> None:
        super().__init__(clock)
        self.progress: list[tuple[PipelineStage, int]] = []

    async def update_progress(self, video_key: str, stage: PipelineStage, percent: int) -> None:
        self.progress.append((stage, percent))
        await super().update_progress(video_key, stage, percent)


class FailingAnalysisGateway(FakeGeminiGateway):
    async def analyze_clip(self, youtube_video_id: str, window: ClipWindow):  # type: ignore[no-untyped-def]
        raise GeminiError("gemini_error", "Gemini request failed (status 503).")


class FlakySpeechGateway(FakeGeminiGateway):
    """Fails the first N speech calls for texts containing a given fragment."""

    def __init__(self, *args, failures: dict[str, int], **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.failures = dict(failures)
        self.speech_calls = 0
        self.narration_calls = 0

    async def write_narration(self, cues, narration_language):  # type: ignore[no-untyped-def]
        self.narration_calls += 1
        return await super().write_narration(cues, narration_language)

    async def synthesize_speech(self, narration_text, narration_language):  # type: ignore[no-untyped-def]
        self.speech_calls += 1
        for fragment, remaining in self.failures.items():
            if fragment in narration_text and remaining > 0:
                self.failures[fragment] -= 1
                raise GeminiError("gemini_error", "Gemini request failed (status 503).")
        return await super().synthesize_speech(narration_text, narration_language)


class BrokenNarrationGateway(FakeGeminiGateway):
    async def write_narration(self, cues, narration_language):  # type: ignore[no-untyped-def]
        raise GeminiError(
            "gemini_invalid_response", "Gemini did not return one narration line for every cue."
        )


def _analysis(*described: tuple[str, str]) -> ChunkAnalysis:
    return ChunkAnalysis(
        transcript_segments=[
            TranscriptSegmentRaw(start_timestamp="00:00", end_timestamp="00:04", text="hello class")
        ],
        visual_events=[
            VisualEvent(
                timestamp=timestamp,
                referent_kind=ReferentKind.TEXT,
                content_description=description,
                words_spoken_nearby="",
                confidence=0.9,
            )
            for timestamp, description in described
        ],
    )


async def _setup(
    duration_seconds: int = 600, language: NarrationLanguage = NarrationLanguage.ENGLISH
):  # type: ignore[no-untyped-def]
    clock = FakeClock()
    videos = ProgressSpyRepository(clock)
    record = make_record(VIDEO_KEY).model_copy(
        update={"duration_seconds": duration_seconds, "narration_language": language}
    )
    await videos.create_if_absent(record)
    return videos, clock


def _pipeline(videos, gateway, storage, clock, metadata=None):  # type: ignore[no-untyped-def]
    return VideoPipeline(
        videos, metadata or FakeMetadataSource(), gateway, storage, Settings(_env_file=None), clock
    )


async def test_happy_path_narrates_voices_and_saves_the_transcript() -> None:
    videos, clock = await _setup(duration_seconds=1500)  # three windows
    gateway = FakeGeminiGateway(
        {
            0: _analysis(("00:10", "first slide"), ("05:00", "second slide")),
            590: _analysis(("15:00", "third slide")),
        }
    )
    storage = FakeBlobStorage()

    await _pipeline(videos, gateway, storage, clock).run(VIDEO_KEY)

    stored = await videos.get(VIDEO_KEY)
    assert stored is not None
    assert stored.status == VideoStatus.READY
    assert [cue.start_seconds for cue in stored.cues] == [10.0, 300.0, 900.0]
    assert all(cue.narration_text.startswith("On the board:") for cue in stored.cues)
    assert [cue.audio_url for cue in stored.cues] == [
        f"https://audio.test/audio/{VIDEO_KEY}/cue-000{n}.wav" for n in (1, 2, 3)
    ]
    assert all(cue.audio_duration_seconds == 1.0 for cue in stored.cues)
    assert set(storage.public) == {f"audio/{VIDEO_KEY}/cue-000{n}.wav" for n in (1, 2, 3)}
    assert storage.public[f"audio/{VIDEO_KEY}/cue-0001.wav"] == make_wav()
    assert stored.transcript_blob_path == f"transcripts/{VIDEO_KEY}.json"
    transcript = json.loads(storage.private[f"transcripts/{VIDEO_KEY}.json"])
    assert transcript["segments"][0]["text"] == "hello class"
    assert set(stored.model_versions) == {"analysis", "gap_judging", "narration", "speech"}
    assert stored.token_usage == {"input_tokens": 10 + 3 * 3, "output_tokens": 5 + 3 * 7}


async def test_progress_never_goes_backwards_and_covers_every_stage() -> None:
    videos, clock = await _setup(duration_seconds=1500)
    gateway = FakeGeminiGateway(
        {0: _analysis(("00:10", "a"), ("05:00", "b")), 590: _analysis(("15:00", "c"))}
    )

    await _pipeline(videos, gateway, FakeBlobStorage(), clock).run(VIDEO_KEY)

    percents = [percent for _, percent in videos.progress]
    assert percents == sorted(percents)
    stages = {stage for stage, _ in videos.progress}
    assert stages == {
        PipelineStage.RESOLVING,
        PipelineStage.ANALYZING,
        PipelineStage.WRITING,
        PipelineStage.VOICING,
        PipelineStage.SAVING,
    }
    assert videos.progress[-1] == (PipelineStage.SAVING, 99)


async def test_a_cue_whose_speech_fails_stays_silent_and_is_not_retried_again_here() -> None:
    videos, clock = await _setup()
    gateway = FlakySpeechGateway(
        {0: _analysis(("00:10", "good slide"), ("05:00", "BROKEN slide"))}, failures={"BROKEN": 99}
    )

    await _pipeline(videos, gateway, FakeBlobStorage(), clock).run(VIDEO_KEY)

    stored = await videos.get(VIDEO_KEY)
    assert stored is not None
    assert stored.status == VideoStatus.READY
    good, broken = stored.cues
    assert good.audio_url is not None
    assert broken.audio_url is None and broken.audio_duration_seconds is None
    assert broken.narration_text  # the text is still available for the screen reader
    # the real gateway already retries a failing Gemini call; repeating it here would multiply
    # the waits, so a Gemini failure costs exactly one call
    assert gateway.speech_calls == 1 + 1


async def test_a_storage_hiccup_while_saving_audio_is_retried_and_succeeds() -> None:
    videos, clock = await _setup()
    gateway = FakeGeminiGateway({0: _analysis(("00:10", "wobbly slide"))})
    storage = FakeBlobStorage(public_failures=2)

    await _pipeline(videos, gateway, storage, clock).run(VIDEO_KEY)

    stored = await videos.get(VIDEO_KEY)
    assert stored is not None and stored.cues[0].audio_url is not None
    assert storage.public_failures == 0  # it took the two failures and then succeeded


async def test_speech_that_never_answers_cannot_hold_the_job_past_the_voicing_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import time

    from ishaara.pipeline import orchestrator

    class HangingSpeechGateway(FakeGeminiGateway):
        async def synthesize_speech(self, narration_text, narration_language):  # type: ignore[no-untyped-def]
            await asyncio.Event().wait()

    monkeypatch.setattr(orchestrator, "VOICING_BASE_SECONDS", 0.1)
    monkeypatch.setattr(orchestrator, "VOICING_PER_CUE_SECONDS", 0.0)
    videos, clock = await _setup()
    gateway = HangingSpeechGateway({0: _analysis(("00:10", "a"), ("05:00", "b"))})
    started = time.monotonic()

    await _pipeline(videos, gateway, FakeBlobStorage(), clock).run(VIDEO_KEY)

    assert time.monotonic() - started < 3
    stored = await videos.get(VIDEO_KEY)
    assert stored is not None and stored.status == VideoStatus.READY
    assert [cue.audio_url for cue in stored.cues] == [None, None]
    assert all(cue.narration_text for cue in stored.cues)


async def test_incomplete_narration_fails_the_job_with_its_code() -> None:
    videos, clock = await _setup()
    gateway = BrokenNarrationGateway({0: _analysis(("00:10", "a"))})

    await _pipeline(videos, gateway, FakeBlobStorage(), clock).run(VIDEO_KEY)

    stored = await videos.get(VIDEO_KEY)
    assert stored is not None
    assert (stored.status, stored.error_code) == (VideoStatus.FAILED, "gemini_invalid_response")


async def test_a_video_with_no_gaps_skips_narration_and_speech() -> None:
    videos, clock = await _setup()
    gateway = FlakySpeechGateway(failures={})
    storage = FakeBlobStorage()

    await _pipeline(videos, gateway, storage, clock).run(VIDEO_KEY)

    stored = await videos.get(VIDEO_KEY)
    assert stored is not None and stored.status == VideoStatus.READY and stored.cues == []
    assert gateway.narration_calls == 0 and gateway.speech_calls == 0
    assert storage.public == {} and stored.transcript_blob_path is not None


async def test_a_failed_transcript_upload_only_loses_the_transcript() -> None:
    videos, clock = await _setup()
    gateway = FakeGeminiGateway({0: _analysis(("00:10", "a"))})

    await _pipeline(videos, gateway, FakeBlobStorage(fail_private=True), clock).run(VIDEO_KEY)

    stored = await videos.get(VIDEO_KEY)
    assert stored is not None
    assert stored.status == VideoStatus.READY
    assert stored.transcript_blob_path is None
    assert stored.cues[0].audio_url is not None


async def test_video_that_became_private_fails_with_its_error_code() -> None:
    videos, clock = await _setup()

    await _pipeline(
        videos,
        FakeGeminiGateway(),
        FakeBlobStorage(),
        clock,
        FakeMetadataSource(privacy_status="private"),
    ).run(VIDEO_KEY)

    stored = await videos.get(VIDEO_KEY)
    assert stored is not None
    assert (stored.status, stored.error_code) == (VideoStatus.FAILED, "video_not_public")


async def test_analysis_failure_ends_failed_with_gemini_error() -> None:
    videos, clock = await _setup()

    await _pipeline(videos, FailingAnalysisGateway(), FakeBlobStorage(), clock).run(VIDEO_KEY)

    stored = await videos.get(VIDEO_KEY)
    assert stored is not None
    assert (stored.status, stored.error_code) == (VideoStatus.FAILED, "gemini_error")
    assert stored.cues == []


async def test_missing_record_returns_quietly() -> None:
    videos = ProgressSpyRepository(FakeClock())

    await _pipeline(videos, FakeGeminiGateway(), FakeBlobStorage(), FakeClock()).run(
        "does-not-exist"
    )

    assert videos.progress == []


async def test_each_voiced_cue_logs_how_long_it_took() -> None:
    from structlog.testing import capture_logs

    videos, clock = await _setup()
    gateway = FakeGeminiGateway({0: _analysis(("00:10", "a"), ("05:00", "b"))})

    with capture_logs() as logs:
        await _pipeline(videos, gateway, FakeBlobStorage(), clock).run(VIDEO_KEY)

    voiced = [entry for entry in logs if entry["event"] == "cue_voiced"]
    assert [entry["cue_id"] for entry in voiced] == ["cue-0001", "cue-0002"]
    assert all(entry["attempt"] == 1 and "seconds" in entry for entry in voiced)
