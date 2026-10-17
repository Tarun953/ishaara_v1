from collections.abc import Iterator, Sequence

import pytest
from fastapi.testclient import TestClient

from ishaara.clients.gemini_gateway import AnalyzeClipResult, JudgeGapsResult
from ishaara.main import create_app
from ishaara.models.domain import NarrationCue, NarrationLanguage, SpeechCoverage
from ishaara.models.gemini_schemas import ChunkAnalysis, GapJudgement, GapReport
from ishaara.pipeline.chunking import ClipWindow


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
    ) -> dict[str, str]:
        return {cue.cue_id: cue.board_description for cue in cues}

    async def synthesize_speech(self, narration_text: str) -> bytes:
        return b"RIFF"

    async def answer_question(self, question: str, context_text: str) -> str:
        return "fake answer"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client
