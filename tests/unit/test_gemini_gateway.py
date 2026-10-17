from ishaara.clients.gemini_gateway import GeminiGateway
from ishaara.models.domain import ReferentKind
from ishaara.models.gemini_schemas import ChunkAnalysis, TranscriptSegmentRaw, VisualEvent
from ishaara.pipeline.chunking import ClipWindow
from ishaara.pipeline.prompts import build_analysis_prompt, build_gap_judging_prompt
from ishaara.pipeline.timestamps import format_timestamp
from tests.conftest import FakeGeminiGateway


def test_fake_gateway_satisfies_protocol() -> None:
    assert isinstance(FakeGeminiGateway(), GeminiGateway)


async def test_fake_gateway_returns_canned_analysis() -> None:
    canned = ChunkAnalysis(transcript_segments=[], visual_events=[])
    gateway = FakeGeminiGateway({600: canned})

    result = await gateway.analyze_clip("dQw4w9WgXcQ", ClipWindow(600, 1200))

    assert result.analysis == canned
    assert gateway.analyzed_windows == [ClipWindow(600, 1200)]


def test_format_timestamp() -> None:
    assert format_timestamp(0) == "00:00"
    assert format_timestamp(760) == "12:40"
    assert format_timestamp(3723) == "01:02:03"


def test_prompt_contains_absolute_window_times() -> None:
    prompt = build_analysis_prompt(ClipWindow(590, 1190))

    assert "09:50 to 19:50" in prompt


def test_gap_prompt_lists_transcript_and_numbered_events() -> None:
    analysis = ChunkAnalysis(
        transcript_segments=[
            TranscriptSegmentRaw(start_timestamp="00:10", end_timestamp="00:14", text="yahan dekho")
        ],
        visual_events=[
            VisualEvent(
                timestamp="00:12",
                referent_kind=ReferentKind.EQUATION,
                content_description="x squared",
                words_spoken_nearby="",
                confidence=0.9,
            )
        ],
    )

    prompt = build_gap_judging_prompt(ClipWindow(0, 180), analysis)

    assert "[00:10-00:14] yahan dekho" in prompt
    assert "Event 0 [00:12] (equation): x squared | Teacher said nearby: (silent)" in prompt
    assert "00:00 to 03:00" in prompt
