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


def _answer_json(**overrides: object) -> str:
    import json

    fields: dict[str, object] = {
        "answer_text": "A circle with a tick mark.",
        "referenced_timestamps": ["00:23"],
        "needs_visual_check": False,
        "answer_language": "en",
    }
    fields.update(overrides)
    return json.dumps(fields)


class _TokenInteraction(_FakeInteraction):
    def __init__(self, output_text: str) -> None:
        super().__init__(output_text)
        self.usage = _Usage()  # type: ignore[assignment]


async def test_text_answer_uses_the_text_model_and_puts_context_in_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, object] = {}

    async def reply(*args: object, **kwargs: object) -> _TokenInteraction:
        sent.update(kwargs)
        return _TokenInteraction(_answer_json(needs_visual_check=True))

    gateway = _gateway()
    monkeypatch.setattr(gateway, "_create_json_interaction", reply)

    result = await gateway.answer_question("What is on the board?", "[00:20] A and B run.", 83.0)

    assert sent["model"] == "gemini-3.5-flash-lite"
    prompt = sent["input_items"][0]["text"]  # type: ignore[index]
    assert "What is on the board?" in prompt and "[00:20] A and B run." in prompt
    assert "The student was at 01:23 in the video." in prompt
    assert result.answer.needs_visual_check is True
    assert result.answer.answer_language == NarrationLanguage.ENGLISH
    assert (result.input_tokens, result.output_tokens) == (11, 4)


async def test_text_answer_without_a_time_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []

    async def reply(*args: object, **kwargs: object) -> _TokenInteraction:
        prompts.append(kwargs["input_items"][0]["text"])  # type: ignore[index]
        return _TokenInteraction(_answer_json())

    gateway = _gateway()
    monkeypatch.setattr(gateway, "_create_json_interaction", reply)

    await gateway.answer_question("What was this about?", "context", None)

    assert "did not say where in the video" in prompts[0]


async def test_video_answer_sends_the_clip_offsets_and_uses_the_video_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, object] = {}

    async def reply(*args: object, **kwargs: object) -> _TokenInteraction:
        sent.update(kwargs)
        return _TokenInteraction(_answer_json(answer_language="hi"))

    gateway = _gateway()
    monkeypatch.setattr(gateway, "_create_json_interaction", reply)

    result = await gateway.answer_with_video(
        "dQw4w9WgXcQ", "बोर्ड पर क्या है?", ClipWindow(65, 95), 80.0
    )

    assert sent["model"] == "gemini-3.8-flash"
    video, text = sent["input_items"]  # type: ignore[misc]
    assert video["uri"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert video["processing"] == {"type": "static", "start_offset": "65s", "end_offset": "95s"}
    assert "01:05 to 01:35" in text["text"] and "01:20" in text["text"]
    assert result.answer.answer_language == NarrationLanguage.HINDI


async def test_unusable_answer_json_becomes_gemini_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def reply(*args: object, **kwargs: object) -> _FakeInteraction:
        return _FakeInteraction('{"answer_text": "x"}')

    gateway = _gateway()
    monkeypatch.setattr(gateway, "_create_json_interaction", reply)

    with pytest.raises(GeminiError) as error_info:
        await gateway.answer_question("q", "c", None)
    with pytest.raises(GeminiError):
        await gateway.answer_with_video("dQw4w9WgXcQ", "q", ClipWindow(0, 30), 15.0)

    assert error_info.value.code == "gemini_invalid_response"


def test_the_sdk_client_is_built_with_its_own_retries_turned_off() -> None:
    from google.genai._gaos.google_genai import _translate_retry_config

    from ishaara.clients.gemini_gateway import sdk_http_options

    assert _translate_retry_config(sdk_http_options()).max_retries == 0


async def test_a_hung_call_is_cut_off_at_its_deadline_and_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import threading
    from types import SimpleNamespace

    from ishaara.clients import gemini_gateway

    release = threading.Event()
    started = 0

    def create(**kwargs: object) -> None:
        nonlocal started
        started += 1
        release.wait(5)  # a real blocked thread, like a Gemini call that never answers

    real_sleep = asyncio.sleep

    async def instant_backoff(delay: float, *args: object) -> None:
        await real_sleep(0)

    monkeypatch.setattr(gemini_gateway, "TTS_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(gemini_gateway, "DEADLINE_MARGIN_SECONDS", 0.05)
    monkeypatch.setattr(asyncio, "sleep", instant_backoff)
    gateway = GoogleGeminiGateway(Settings(_env_file=None, gemini_api_key="test-key"))
    gateway._sdk_client = SimpleNamespace(interactions=SimpleNamespace(create=create))  # type: ignore[assignment]

    try:
        with pytest.raises(GeminiError) as error_info:
            await gateway.synthesize_speech("hello", NarrationLanguage.ENGLISH)
    finally:
        release.set()
        gateway.close()

    assert error_info.value.code == "gemini_error"
    assert "in time" in error_info.value.detail
    assert started == 3  # the three attempts the retry layer allows, and no more


async def test_hung_gemini_calls_cannot_starve_the_shared_thread_pool() -> None:
    """Regression: blocked SDK calls once left every authenticated request waiting for a thread."""
    import asyncio
    import threading
    from types import SimpleNamespace

    release = threading.Event()
    running = threading.Semaphore(0)

    def create(**kwargs: object) -> None:
        running.release()
        release.wait(10)

    gateway = GoogleGeminiGateway(Settings(_env_file=None, gemini_api_key="k"), threads=2)
    gateway._sdk_client = SimpleNamespace(interactions=SimpleNamespace(create=create))  # type: ignore[assignment]
    stuck = [
        asyncio.create_task(gateway.synthesize_speech("a", NarrationLanguage.ENGLISH)),
        asyncio.create_task(gateway.synthesize_speech("b", NarrationLanguage.ENGLISH)),
    ]
    try:
        for _ in range(2):  # wait until both Gemini threads are really blocked
            assert await asyncio.to_thread(running.acquire, True, 5)

        # this is what Firebase token verification does on every authenticated request
        answer = await asyncio.wait_for(asyncio.to_thread(lambda: 42), timeout=2)

        assert answer == 42
    finally:
        release.set()
        await asyncio.gather(*stuck, return_exceptions=True)
        gateway.close()
