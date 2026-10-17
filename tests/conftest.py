import io
import wave
from collections.abc import Iterator, Sequence

import pytest
from fastapi.testclient import TestClient

from ishaara.clients.gemini_gateway import (
    AnalyzeClipResult,
    JudgeGapsResult,
    NarrationResult,
    SpeechResult,
)
from ishaara.errors import StorageError
from ishaara.main import create_app
from ishaara.models.api_models import YouTubeVideoMetadata
from ishaara.models.domain import NarrationCue, NarrationLanguage, SpeechCoverage
from ishaara.models.gemini_schemas import ChunkAnalysis, GapJudgement, GapReport
from ishaara.pipeline.chunking import ClipWindow


def make_wav(duration_seconds: float = 1.0, sample_rate: int = 24000) -> bytes:
    """Build a silent mono 16-bit WAV file of the given length."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * int(sample_rate * duration_seconds))
    return buffer.getvalue()


class FakeGeminiGateway:
    """Returns canned analyses keyed by window start; never touches the network.

    ``judge_gaps`` marks every event ``not_spoken`` unless a report is supplied.
    """

    def __init__(
        self,
        analyses_by_window_start: dict[int, ChunkAnalysis] | None = None,
        reports_by_window_start: dict[int, GapReport] | None = None,
    ) -> None:
        self.analyses_by_window_start = analyses_by_window_start or {}
        self.reports_by_window_start = reports_by_window_start or {}
        self.analyzed_windows: list[ClipWindow] = []

    async def analyze_clip(self, youtube_video_id: str, window: ClipWindow) -> AnalyzeClipResult:
        self.analyzed_windows.append(window)
        analysis = self.analyses_by_window_start.get(
            window.start_seconds, ChunkAnalysis(transcript_segments=[], visual_events=[])
        )
        return AnalyzeClipResult(analysis, analysis.model_dump_json(), 0, 0)

    async def judge_gaps(self, window: ClipWindow, analysis: ChunkAnalysis) -> JudgeGapsResult:
        report = self.reports_by_window_start.get(window.start_seconds) or GapReport(
            judgements=[
                GapJudgement(
                    event_index=event_index,
                    speech_coverage=SpeechCoverage.NOT_SPOKEN,
                    gap_summary=event.content_description,
                    confidence=0.9,
                )
                for event_index, event in enumerate(analysis.visual_events)
            ]
        )
        return JudgeGapsResult(report, report.model_dump_json(), 0, 0)

    async def write_narration(
        self, cues: Sequence[NarrationCue], narration_language: NarrationLanguage
    ) -> NarrationResult:
        lines = {cue.cue_id: f"On the board: {cue.board_description}" for cue in cues}
        return NarrationResult(lines, 10, 5)

    async def synthesize_speech(
        self, narration_text: str, narration_language: NarrationLanguage
    ) -> SpeechResult:
        return SpeechResult(make_wav(), 3, 7)

    async def answer_question(self, question: str, context_text: str) -> str:
        return "fake answer"


class FakeBlobStorage:
    """Keeps uploads in memory; can be told to fail specific uploads."""

    def __init__(self, fail_private: bool = False) -> None:
        self.public: dict[str, bytes] = {}
        self.private: dict[str, bytes] = {}
        self.fail_private = fail_private

    async def upload_public(
        self, path: str, data: bytes, content_type: str, cache_control: str
    ) -> str:
        self.public[path] = data
        return f"https://audio.test/{path}"

    async def upload_private(self, path: str, data: bytes, content_type: str) -> str:
        if self.fail_private:
            raise StorageError("private storage is down")
        self.private[path] = data
        return path

    async def download_private(self, path: str) -> bytes:
        if path not in self.private:
            raise StorageError("no such file")
        return self.private[path]


class FakeMetadataSource:
    """Returns fixed YouTube metadata; never touches the network."""

    def __init__(self, privacy_status: str = "public", duration_seconds: int = 600) -> None:
        self.privacy_status = privacy_status
        self.duration_seconds = duration_seconds
        self.fetches = 0

    async def fetch(self, youtube_video_id: str) -> YouTubeVideoMetadata:
        self.fetches += 1
        return YouTubeVideoMetadata(
            title="Lecture",
            channel_title="Prof",
            duration_seconds=self.duration_seconds,
            privacy_status=self.privacy_status,
            embeddable=True,
            live_broadcast_content="none",
        )


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client
