import httpx
import pytest

from ishaara.clients.gemini_gateway import GeminiGateway, GoogleGeminiGateway
from ishaara.errors import GeminiError
from ishaara.models.domain import ReferentKind
from ishaara.models.gemini_schemas import ChunkAnalysis, TranscriptSegmentRaw, VisualEvent
from ishaara.pipeline.chunking import ClipWindow
from ishaara.pipeline.prompts import build_analysis_prompt, build_gap_judging_prompt
from ishaara.pipeline.timestamps import format_timestamp
from ishaara.settings import Settings
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


class _FakeInteraction:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text
        self.usage = None


def _gateway() -> GoogleGeminiGateway:
    return GoogleGeminiGateway(Settings(_env_file=None))


async def test_transport_failure_becomes_gemini_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(*args: object, **kwargs: object) -> None:
        raise httpx.ConnectError("no route")

    gateway = _gateway()
    monkeypatch.setattr(gateway, "_create_json_interaction", fail)

    with pytest.raises(GeminiError) as error_info:
        await gateway.analyze_clip("dQw4w9WgXcQ", ClipWindow(0, 60))

    assert error_info.value.code == "gemini_error"


async def test_malformed_json_becomes_gemini_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def not_json(*args: object, **kwargs: object) -> _FakeInteraction:
        return _FakeInteraction("this is not json")

    gateway = _gateway()
    monkeypatch.setattr(gateway, "_create_json_interaction", not_json)

    with pytest.raises(GeminiError) as error_info:
        await gateway.judge_gaps(
            ClipWindow(0, 60), ChunkAnalysis(transcript_segments=[], visual_events=[])
        )

    assert error_info.value.code == "gemini_invalid_response"


async def test_valid_json_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def good(*args: object, **kwargs: object) -> _FakeInteraction:
        return _FakeInteraction('{"transcript_segments": [], "visual_events": []}')

    gateway = _gateway()
    monkeypatch.setattr(gateway, "_create_json_interaction", good)

    result = await gateway.analyze_clip("dQw4w9WgXcQ", ClipWindow(0, 60))

    assert result.analysis.visual_events == []
    assert (result.input_tokens, result.output_tokens) == (0, 0)
