from ishaara.clients.memory_repository import InMemoryVideoRepository
from ishaara.errors import GeminiError
from ishaara.models.domain import PipelineStage, ReferentKind, VideoStatus
from ishaara.models.gemini_schemas import ChunkAnalysis, VisualEvent
from ishaara.pipeline.chunking import ClipWindow
from ishaara.pipeline.orchestrator import VideoPipeline
from ishaara.settings import Settings
from tests.conftest import FakeGeminiGateway, FakeMetadataSource
from tests.contract.conftest import FakeClock, make_record

VIDEO_KEY = "dQw4w9WgXcQ_en_v1"


class ProgressSpyRepository(InMemoryVideoRepository):
    def __init__(self, clock: FakeClock) -> None:
        super().__init__(clock)
        self.progress: list[tuple[PipelineStage, int]] = []

    async def update_progress(self, video_key: str, stage: PipelineStage, percent: int) -> None:
        self.progress.append((stage, percent))
        await super().update_progress(video_key, stage, percent)


class FailingGateway(FakeGeminiGateway):
    async def analyze_clip(self, youtube_video_id: str, window: ClipWindow):  # type: ignore[no-untyped-def]
        raise GeminiError("gemini_error", "Gemini request failed (status 503).")


def _analysis(*timestamps: str) -> ChunkAnalysis:
    return ChunkAnalysis(
        transcript_segments=[],
        visual_events=[
            VisualEvent(
                timestamp=timestamp,
                referent_kind=ReferentKind.TEXT,
                content_description="A slide title.",
                words_spoken_nearby="",
                confidence=0.9,
            )
            for timestamp in timestamps
        ],
    )


async def _setup(duration_seconds: int = 600, **overrides: object):  # type: ignore[no-untyped-def]
    clock = FakeClock()
    videos = ProgressSpyRepository(clock)
    record = make_record(VIDEO_KEY).model_copy(update={"duration_seconds": duration_seconds})
    await videos.create_if_absent(record)
    return videos, clock, overrides


async def test_happy_path_ends_ready_with_cues_and_monotonic_progress() -> None:
    videos, clock, _ = await _setup(duration_seconds=1500)  # three windows
    gateway = FakeGeminiGateway({0: _analysis("00:10", "05:00"), 590: _analysis("15:00")})
    pipeline = VideoPipeline(videos, FakeMetadataSource(), gateway, Settings(_env_file=None), clock)

    await pipeline.run(VIDEO_KEY)

    stored = await videos.get(VIDEO_KEY)
    assert stored is not None
    assert stored.status == VideoStatus.READY
    assert [cue.start_seconds for cue in stored.cues] == [10.0, 300.0, 900.0]
    assert stored.model_versions["analysis"] == "gemini-3.8-flash"
    percents = [percent for _, percent in videos.progress]
    assert percents == sorted(percents)
    assert videos.progress[0] == (PipelineStage.RESOLVING, 1)
    assert videos.progress[-1] == (PipelineStage.WRITING, 75)
    assert any(stage == PipelineStage.ANALYZING and 5 < pct < 70 for stage, pct in videos.progress)


async def test_video_that_became_private_fails_with_its_error_code() -> None:
    videos, clock, _ = await _setup()
    pipeline = VideoPipeline(
        videos,
        FakeMetadataSource(privacy_status="private"),
        FakeGeminiGateway(),
        Settings(_env_file=None),
        clock,
    )

    await pipeline.run(VIDEO_KEY)

    stored = await videos.get(VIDEO_KEY)
    assert stored is not None
    assert (stored.status, stored.error_code) == (VideoStatus.FAILED, "video_not_public")


async def test_gemini_failure_ends_failed_with_gemini_error() -> None:
    videos, clock, _ = await _setup()
    pipeline = VideoPipeline(
        videos, FakeMetadataSource(), FailingGateway(), Settings(_env_file=None), clock
    )

    await pipeline.run(VIDEO_KEY)

    stored = await videos.get(VIDEO_KEY)
    assert stored is not None
    assert (stored.status, stored.error_code) == (VideoStatus.FAILED, "gemini_error")
    assert stored.cues == []


async def test_missing_record_returns_quietly() -> None:
    videos = ProgressSpyRepository(FakeClock())
    pipeline = VideoPipeline(
        videos, FakeMetadataSource(), FakeGeminiGateway(), Settings(_env_file=None)
    )

    await pipeline.run("does-not-exist")

    assert videos.progress == []
