import httpx
import pytest

from ishaara.clients.gemini_gateway import GeminiGateway, GoogleGeminiGateway
from ishaara.errors import GeminiError
from ishaara.models.domain import NarrationCue, NarrationLanguage, ReferentKind
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


def _narration_json(*pairs: tuple[str, str]) -> str:
    lines = ",".join(f'{{"cue_id": "{c}", "narration_text": "{t}"}}' for c, t in pairs)
    return f'{{"lines": [{lines}]}}'


def _cue(cue_id: str) -> NarrationCue:
    return NarrationCue(
        cue_id=cue_id,
        start_seconds=1.0,
        spoken_phrase="",
        referent_kind=ReferentKind.TEXT,
        board_description="A heading.",
        confidence=0.9,
    )


class _Usage:
    total_input_tokens = 11
    total_output_tokens = 4


async def test_narration_retries_once_when_a_cue_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies = iter(
        [_narration_json(("cue-0001", "A")), _narration_json(("cue-0001", "A"), ("cue-0002", "B"))]
    )
    calls = 0

    async def reply(*args: object, **kwargs: object) -> _FakeInteraction:
        nonlocal calls
        calls += 1
        interaction = _FakeInteraction(next(replies))
        interaction.usage = _Usage()  # type: ignore[assignment]
        return interaction

    gateway = _gateway()
    monkeypatch.setattr(gateway, "_create_json_interaction", reply)

    result = await gateway.write_narration(
        [_cue("cue-0001"), _cue("cue-0002")], NarrationLanguage.ENGLISH
    )

    assert calls == 2
    assert result.lines == {"cue-0001": "A", "cue-0002": "B"}
    assert (result.input_tokens, result.output_tokens) == (22, 8)


async def test_narration_fails_after_two_incomplete_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def always_incomplete(*args: object, **kwargs: object) -> _FakeInteraction:
        return _FakeInteraction(_narration_json(("cue-0001", "A")))

    gateway = _gateway()
    monkeypatch.setattr(gateway, "_create_json_interaction", always_incomplete)

    with pytest.raises(GeminiError) as error_info:
        await gateway.write_narration(
            [_cue("cue-0001"), _cue("cue-0002")], NarrationLanguage.ENGLISH
        )

    assert error_info.value.code == "gemini_invalid_response"


async def test_narration_with_no_cues_makes_no_call() -> None:
    result = await _gateway().write_narration([], NarrationLanguage.HINDI)

    assert result.lines == {} and result.input_tokens == 0


class _Audio:
    def __init__(self, data: object) -> None:
        self.data = data


class _SpeechInteraction:
    def __init__(self, audio: _Audio | None) -> None:
        self.output_audio = audio
        self.usage = _Usage()


async def test_speech_decodes_base64_audio_and_passes_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import base64

    sent: dict[str, object] = {}

    async def reply(*args: object, **kwargs: object) -> _SpeechInteraction:
        sent.update(kwargs)
        return _SpeechInteraction(_Audio(base64.b64encode(b"RIFFdata").decode()))

    gateway = _gateway()
    monkeypatch.setattr(gateway, "_create_interaction", reply)

    result = await gateway.synthesize_speech("नमस्ते", NarrationLanguage.HINDI)

    assert result.wav_bytes == b"RIFFdata"
    assert (result.input_tokens, result.output_tokens) == (11, 4)
    speech_config = sent["generation_config"]["speech_config"][0]  # type: ignore[index]
    assert speech_config == {"voice": "Kore", "language": "hi-IN"}


async def test_speech_without_audio_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def reply(*args: object, **kwargs: object) -> _SpeechInteraction:
        return _SpeechInteraction(None)

    gateway = _gateway()
    monkeypatch.setattr(gateway, "_create_interaction", reply)

    with pytest.raises(GeminiError) as error_info:
        await gateway.synthesize_speech("hello", NarrationLanguage.ENGLISH)

    assert error_info.value.code == "gemini_invalid_response"


def test_each_retry_is_logged_with_model_attempt_and_error() -> None:
    from structlog.testing import capture_logs
    from tenacity import RetryCallState

    from ishaara.clients.gemini_gateway import _log_retry

    state = RetryCallState(retry_object=None, fn=None, args=(), kwargs={"model": "gemini-x"})  # type: ignore[arg-type]
    state.attempt_number = 2
    try:
        raise httpx.ReadTimeout("took too long")
    except httpx.ReadTimeout as error:
        state.set_exception((type(error), error, error.__traceback__))  # type: ignore[arg-type]

    with capture_logs() as logs:
        _log_retry(state)

    assert logs == [
        {
            "event": "gemini_retry",
            "log_level": "warning",
            "model": "gemini-x",
            "attempt": 2,
            "error_type": "ReadTimeout",
            "error": "took too long",
        }
    ]
