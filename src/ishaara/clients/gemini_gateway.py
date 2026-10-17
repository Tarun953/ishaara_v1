"""The single place that talks to Gemini: a Protocol and its Google implementation."""

import asyncio
import base64
import contextlib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx
import structlog
from google import genai
from google.genai import errors as genai_errors
from google.genai import interactions
from pydantic import ValidationError
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from ishaara.errors import GeminiError
from ishaara.models.domain import NarrationCue, NarrationLanguage
from ishaara.models.gemini_schemas import ChunkAnalysis, GapReport, NarrationBatch
from ishaara.pipeline.chunking import ClipWindow
from ishaara.pipeline.narration import lines_by_cue_id
from ishaara.pipeline.prompts import (
    build_analysis_prompt,
    build_gap_judging_prompt,
    build_narration_prompt,
)
from ishaara.pipeline.youtube_url import canonical_watch_url
from ishaara.settings import Settings

logger = structlog.get_logger()

NARRATION_ATTEMPTS = 2
ANALYSIS_TIMEOUT_SECONDS = 180.0
TTS_TIMEOUT_SECONDS = 30.0
MAX_ATTEMPTS = 3
SPEECH_STYLE = "calm and clear"
SPEECH_LANGUAGE_CODES = {NarrationLanguage.ENGLISH: "en-US", NarrationLanguage.HINDI: "hi-IN"}


@dataclass(frozen=True)
class AnalyzeClipResult:
    """The outcome of analyzing one clip.

    Attributes:
        analysis: Parsed and validated Gemini output.
        raw_response_text: The JSON text Gemini returned, kept for fixtures.
        input_tokens: Input tokens billed for the call (0 if not reported).
        output_tokens: Output tokens billed for the call (0 if not reported).
    """

    analysis: ChunkAnalysis
    raw_response_text: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class JudgeGapsResult:
    """The outcome of judging one window's visual events against its speech.

    Attributes:
        report: Parsed and validated Gemini output.
        raw_response_text: The JSON text Gemini returned, kept for fixtures.
        input_tokens: Input tokens billed for the call (0 if not reported).
        output_tokens: Output tokens billed for the call (0 if not reported).
    """

    report: GapReport
    raw_response_text: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class NarrationResult:
    """The outcome of writing narration for a batch of cues.

    Attributes:
        lines: Spoken text keyed by ``cue_id``; every requested cue is present.
        input_tokens: Input tokens billed across the calls made.
        output_tokens: Output tokens billed across the calls made.
    """

    lines: dict[str, str]
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class SpeechResult:
    """The outcome of one text-to-speech call.

    Attributes:
        wav_bytes: A complete WAV file.
        input_tokens: Input tokens billed.
        output_tokens: Output tokens billed.
    """

    wav_bytes: bytes
    input_tokens: int
    output_tokens: int


@runtime_checkable
class GeminiGateway(Protocol):
    """Everything the app needs from Gemini. Business logic depends only on this."""

    async def analyze_clip(self, youtube_video_id: str, window: ClipWindow) -> AnalyzeClipResult:
        """Pass 1: transcribe a clip and list its visual events."""
        ...

    async def judge_gaps(self, window: ClipWindow, analysis: ChunkAnalysis) -> JudgeGapsResult:
        """Pass 2: decide which visual events the speech leaves unexplained."""
        ...

    async def write_narration(
        self, cues: Sequence[NarrationCue], narration_language: NarrationLanguage
    ) -> NarrationResult:
        """Write one spoken line per cue."""
        ...

    async def synthesize_speech(
        self, narration_text: str, narration_language: NarrationLanguage
    ) -> SpeechResult:
        """Speak the text and return it as WAV."""
        ...

    async def answer_question(self, question: str, context_text: str) -> str:
        """Answer a question from text context (arrives in M6)."""
        ...


def _is_retryable(error: BaseException) -> bool:
    """Return True for 429 and 5xx API errors and network failures."""
    if isinstance(error, genai_errors.APIError):
        return error.code == 429 or error.code >= 500
    return isinstance(error, httpx.TransportError)


def _log_retry(retry_state: RetryCallState) -> None:
    """Log each retried Gemini call, so slow or flaky requests can be diagnosed."""
    outcome = retry_state.outcome
    error = outcome.exception() if outcome else None
    logger.warning(
        "gemini_retry",
        model=retry_state.kwargs.get("model"),
        attempt=retry_state.attempt_number,
        error_type=type(error).__name__ if error else None,
        error=str(error)[:200] if error else None,
    )


def _parse_or_none(model: type[NarrationBatch], raw_text: str | None) -> NarrationBatch | None:
    """Parse JSON text into ``model``, returning ``None`` if it does not validate."""
    try:
        return model.model_validate_json(raw_text or "")
    except ValidationError:
        return None


def _token_counts(interaction: interactions.Interaction) -> tuple[int, int]:
    """Return (input_tokens, output_tokens) from an interaction, 0 if unreported."""
    usage = interaction.usage
    if usage is None:
        return 0, 0
    return usage.total_input_tokens or 0, usage.total_output_tokens or 0


@contextlib.contextmanager
def _gemini_errors() -> Iterator[None]:
    """Translate SDK, network and parsing failures into ``GeminiError``."""
    try:
        yield
    except genai_errors.APIError as error:
        raise GeminiError(
            "gemini_error", f"Gemini request failed (status {error.code})."
        ) from error
    except httpx.TransportError as error:
        raise GeminiError("gemini_error", "Could not reach Gemini.") from error
    except ValidationError as error:
        raise GeminiError(
            "gemini_invalid_response", "Gemini returned a response in an unexpected format."
        ) from error


class GoogleGeminiGateway:
    """``GeminiGateway`` backed by the ``google-genai`` Interactions API.

    Args:
        settings: Application settings providing the API key and model ids.
    """

    def __init__(self, settings: Settings) -> None:
        """Store settings; the SDK client is created lazily."""
        self._settings = settings
        self._sdk_client: genai.Client | None = None

    @property
    def _client(self) -> genai.Client:
        """Create the SDK client on first use, so an unset API key only fails real calls."""
        if self._sdk_client is None:
            self._sdk_client = genai.Client(api_key=self._settings.gemini_api_key)
        return self._sdk_client

    async def analyze_clip(self, youtube_video_id: str, window: ClipWindow) -> AnalyzeClipResult:
        """Pass 1: analyze one window of a public YouTube video.

        Args:
            youtube_video_id: The 11-character video id.
            window: The slice of the video to analyze.

        Returns:
            The parsed analysis with raw text and token usage.

        Raises:
            GeminiError: If the call keeps failing after retries or the JSON is unusable.
        """
        with _gemini_errors():
            interaction = await self._create_json_interaction(
                model=self._settings.analysis_model,
                input_items=[
                    {
                        "type": "video",
                        "uri": canonical_watch_url(youtube_video_id),
                        "processing": {
                            "type": "static",
                            "start_offset": f"{window.start_seconds}s",
                            "end_offset": f"{window.end_seconds}s",
                        },
                    },
                    {"type": "text", "text": build_analysis_prompt(window)},
                ],
                response_schema=ChunkAnalysis.model_json_schema(),
            )
            raw_response_text = interaction.output_text or ""
            input_tokens, output_tokens = _token_counts(interaction)
            return AnalyzeClipResult(
                ChunkAnalysis.model_validate_json(raw_response_text),
                raw_response_text,
                input_tokens,
                output_tokens,
            )

    async def judge_gaps(self, window: ClipWindow, analysis: ChunkAnalysis) -> JudgeGapsResult:
        """Pass 2: judge which visual events the speech leaves unexplained.

        Args:
            window: The window that was analyzed.
            analysis: Pass 1 output for that window.

        Returns:
            One judgement per visual event, with raw text and token usage.

        Raises:
            GeminiError: If the call keeps failing after retries or the JSON is unusable.
        """
        with _gemini_errors():
            interaction = await self._create_json_interaction(
                model=self._settings.analysis_model,
                input_items=[{"type": "text", "text": build_gap_judging_prompt(window, analysis)}],
                response_schema=GapReport.model_json_schema(),
            )
            raw_response_text = interaction.output_text or ""
            input_tokens, output_tokens = _token_counts(interaction)
            return JudgeGapsResult(
                GapReport.model_validate_json(raw_response_text),
                raw_response_text,
                input_tokens,
                output_tokens,
            )

    async def _create_json_interaction(
        self, model: str, input_items: list[dict[str, Any]], response_schema: dict[str, Any]
    ) -> interactions.Interaction:
        """Make one JSON-schema interaction."""
        return await self._create_interaction(
            model=model,
            input_items=input_items,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": response_schema,
            },
            timeout=ANALYSIS_TIMEOUT_SECONDS,
        )

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=1, max=10),
        before_sleep=_log_retry,
        reraise=True,
    )
    async def _create_interaction(
        self,
        model: str,
        input_items: list[dict[str, Any]],
        response_format: dict[str, Any],
        timeout: float,
        generation_config: dict[str, Any] | None = None,
    ) -> interactions.Interaction:
        """Make one interaction (blocking SDK call run in a thread), retrying 429/5xx."""
        request: dict[str, Any] = {
            "model": model,
            "input": input_items,
            "response_format": response_format,
            "timeout": timeout,
        }
        if generation_config:
            request["generation_config"] = generation_config
        interaction = await asyncio.to_thread(self._client.interactions.create, **request)
        if not isinstance(interaction, interactions.Interaction):
            raise TypeError("Expected a unary Interaction, got a stream")
        return interaction

    async def write_narration(
        self, cues: Sequence[NarrationCue], narration_language: NarrationLanguage
    ) -> NarrationResult:
        """Write one spoken line per cue, retrying once if the batch is incomplete.

        Args:
            cues: Cues to describe.
            narration_language: Language of the lines.

        Returns:
            A line for every cue and the tokens billed.

        Raises:
            GeminiError: If the call fails, or two attempts both miss a cue.
        """
        if not cues:
            return NarrationResult({}, 0, 0)
        prompt = build_narration_prompt(cues, narration_language)
        expected_cue_ids = [cue.cue_id for cue in cues]
        input_tokens = output_tokens = 0
        for attempt in range(NARRATION_ATTEMPTS):
            with _gemini_errors():
                interaction = await self._create_json_interaction(
                    model=self._settings.text_model,
                    input_items=[{"type": "text", "text": prompt}],
                    response_schema=NarrationBatch.model_json_schema(),
                )
                call_input, call_output = _token_counts(interaction)
                input_tokens, output_tokens = input_tokens + call_input, output_tokens + call_output
                batch = _parse_or_none(NarrationBatch, interaction.output_text)
            lines = lines_by_cue_id(batch, expected_cue_ids) if batch else None
            if lines is not None:
                return NarrationResult(lines, input_tokens, output_tokens)
            logger.warning("narration_incomplete", attempt=attempt + 1, cues=len(cues))
        raise GeminiError(
            "gemini_invalid_response", "Gemini did not return one narration line for every cue."
        )

    async def synthesize_speech(
        self, narration_text: str, narration_language: NarrationLanguage
    ) -> SpeechResult:
        """Speak ``narration_text`` and return the WAV file.

        Args:
            narration_text: The line to speak.
            narration_language: Language of the line, used to pick the speech language.

        Returns:
            WAV bytes and the tokens billed.

        Raises:
            GeminiError: If the call fails or returns no audio.
        """
        with _gemini_errors():
            interaction = await self._create_interaction(
                model=self._settings.tts_model,
                input_items=[
                    {
                        "type": "text",
                        "text": narration_text,
                        "annotations": [{"type": "speech_metadata", "style": SPEECH_STYLE}],
                    }
                ],
                response_format={"type": "audio"},
                generation_config={
                    "speech_config": [
                        {
                            "voice": self._settings.tts_voice,
                            "language": SPEECH_LANGUAGE_CODES[narration_language],
                        }
                    ]
                },
                timeout=TTS_TIMEOUT_SECONDS,
            )
        audio = interaction.output_audio
        if audio is None or not audio.data:
            raise GeminiError("gemini_invalid_response", "Gemini returned no audio.")
        wav_bytes = audio.data if isinstance(audio.data, bytes) else base64.b64decode(audio.data)
        input_tokens, output_tokens = _token_counts(interaction)
        return SpeechResult(wav_bytes, input_tokens, output_tokens)

    async def answer_question(self, question: str, context_text: str) -> str:
        """Not implemented until M6."""
        raise NotImplementedError("answer_question arrives in M6")
