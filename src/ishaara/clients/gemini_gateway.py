"""The single place that talks to Gemini: a Protocol and its Google implementation."""

import asyncio
import contextlib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import interactions
from pydantic import ValidationError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ishaara.errors import GeminiError
from ishaara.models.domain import NarrationCue, NarrationLanguage
from ishaara.models.gemini_schemas import ChunkAnalysis, GapReport
from ishaara.pipeline.chunking import ClipWindow
from ishaara.pipeline.prompts import build_analysis_prompt, build_gap_judging_prompt
from ishaara.pipeline.youtube_url import canonical_watch_url
from ishaara.settings import Settings

ANALYSIS_TIMEOUT_SECONDS = 180.0
MAX_ATTEMPTS = 3


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
    ) -> dict[str, str]:
        """Return spoken narration text keyed by ``cue_id`` (arrives in M4)."""
        ...

    async def synthesize_speech(self, narration_text: str) -> bytes:
        """Return WAV bytes for the text (arrives in M4)."""
        ...

    async def answer_question(self, question: str, context_text: str) -> str:
        """Answer a question from text context (arrives in M6)."""
        ...


def _is_retryable(error: BaseException) -> bool:
    """Return True for 429 and 5xx API errors and network failures."""
    if isinstance(error, genai_errors.APIError):
        return error.code == 429 or error.code >= 500
    return isinstance(error, httpx.TransportError)


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

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=1, max=10),
        reraise=True,
    )
    async def _create_json_interaction(
        self, model: str, input_items: list[dict[str, Any]], response_schema: dict[str, Any]
    ) -> interactions.Interaction:
        """Make one JSON-schema interaction (blocking SDK call run in a thread)."""
        interaction = await asyncio.to_thread(
            self._client.interactions.create,
            model=model,
            input=input_items,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": response_schema,
            },
            timeout=ANALYSIS_TIMEOUT_SECONDS,
        )
        if not isinstance(interaction, interactions.Interaction):
            raise TypeError("Expected a unary Interaction, got a stream")
        return interaction

    async def write_narration(
        self, cues: Sequence[NarrationCue], narration_language: NarrationLanguage
    ) -> dict[str, str]:
        """Not implemented until M4."""
        raise NotImplementedError("write_narration arrives in M4")

    async def synthesize_speech(self, narration_text: str) -> bytes:
        """Not implemented until M4."""
        raise NotImplementedError("synthesize_speech arrives in M4")

    async def answer_question(self, question: str, context_text: str) -> str:
        """Not implemented until M6."""
        raise NotImplementedError("answer_question arrives in M6")
