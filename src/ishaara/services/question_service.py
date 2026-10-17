"""Answering a student's question about a processed video."""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import structlog

from ishaara.clients.blob_storage import BlobStorage
from ishaara.clients.gemini_gateway import AnswerResult, GeminiGateway
from ishaara.clients.repositories import DailyUsageField, UsageRepository
from ishaara.errors import (
    DailyLimitReachedError,
    GeminiError,
    InvalidRequestError,
    IshaaraError,
)
from ishaara.models.api_models import AuthenticatedUser
from ishaara.models.domain import NarrationLanguage, VideoRecord
from ishaara.pipeline.audio import wav_duration_seconds
from ishaara.pipeline.questions import (
    build_question_context,
    parse_referenced_seconds,
    visual_clip_window,
)
from ishaara.services.notes_service import load_transcript
from ishaara.settings import Settings

logger = structlog.get_logger()

ANSWER_CACHE_CONTROL = "public, max-age=31536000"
SPEECH_BUDGET_SECONDS = 25.0


@dataclass(frozen=True)
class QuestionResult:
    """What the student gets back.

    Attributes:
        answer_text: The written answer.
        answer_audio_url: The spoken answer, or ``None`` if it could not be voiced.
        referenced_seconds: Lecture times the answer relies on, ascending.
    """

    answer_text: str
    answer_audio_url: str | None
    referenced_seconds: list[float]


def _new_id() -> str:
    """Return a random id for an answer's audio file."""
    return uuid.uuid4().hex


class QuestionService:
    """Answers questions from the transcript and cues, looking at the video when needed.

    Args:
        usage: Daily quota counters.
        gateway: Where Gemini calls go.
        blob_storage: Where the transcript is read and answer audio is stored.
        settings: Application settings.
        new_id: Produces the random id used in the answer's audio path.
        speech_budget_seconds: Longest the answer may wait for its audio before it is
            returned as text only.
    """

    def __init__(
        self,
        usage: UsageRepository,
        gateway: GeminiGateway,
        blob_storage: BlobStorage,
        settings: Settings,
        new_id: Callable[[], str] = _new_id,
        speech_budget_seconds: float = SPEECH_BUDGET_SECONDS,
    ) -> None:
        """Store collaborators."""
        self._usage = usage
        self._gateway = gateway
        self._blob_storage = blob_storage
        self._settings = settings
        self._new_id = new_id
        self._speech_budget_seconds = speech_budget_seconds

    async def answer(
        self, user: AuthenticatedUser, video: VideoRecord, question: str, at_seconds: float | None
    ) -> QuestionResult:
        """Answer one question about a finished video.

        Args:
            user: The asking user; the question counts against their daily limit.
            video: A processed (``ready``) video.
            question: The student's question.
            at_seconds: Where the student is in the video, if they said.

        Returns:
            The answer text, its audio if it could be voiced, and the times it refers to.

        Raises:
            InvalidRequestError: If ``at_seconds`` is beyond the end of the video.
            DailyLimitReachedError: If the user's daily question limit is used up.
            GeminiError: If Gemini fails or returns an empty or unusable answer.
        """
        if at_seconds is not None and at_seconds > video.duration_seconds:
            raise InvalidRequestError(
                f"at_seconds is beyond the end of the video ({video.duration_seconds} seconds)."
            )
        await self._spend_daily_quota(user)
        transcript = await load_transcript(self._blob_storage, video)
        context_text = build_question_context(video.cues, transcript, at_seconds)
        result = await self._gateway.answer_question(question, context_text, at_seconds)
        answer, used_video = result.answer, False
        input_tokens, output_tokens = result.input_tokens, result.output_tokens
        if answer.needs_visual_check and at_seconds is not None:
            looked = await self._look_at_the_video(video, question, at_seconds)
            if looked is not None:
                answer, used_video = looked.answer, True
                input_tokens += looked.input_tokens
                output_tokens += looked.output_tokens
        if not answer.answer_text.strip():
            raise GeminiError("gemini_invalid_response", "Gemini returned an empty answer.")
        audio_url = await self._speak(video, answer.answer_text, answer.answer_language)
        logger.info(
            "question_answered",
            video_key=video.video_key,
            used_video=used_video,
            voiced=audio_url is not None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        return QuestionResult(
            answer.answer_text.strip(),
            audio_url,
            parse_referenced_seconds(answer.referenced_timestamps, video.duration_seconds),
        )

    async def _look_at_the_video(
        self, video: VideoRecord, question: str, at_seconds: float
    ) -> AnswerResult | None:
        """Answer from the 30-second clip around ``at_seconds``; ``None`` if that fails."""
        window = visual_clip_window(at_seconds, video.duration_seconds)
        try:
            return await self._gateway.answer_with_video(
                video.youtube_video_id, question, window, at_seconds
            )
        except GeminiError:
            logger.warning("visual_check_failed", video_key=video.video_key, exc_info=True)
            return None

    async def _speak(
        self, video: VideoRecord, answer_text: str, answer_language: NarrationLanguage
    ) -> str | None:
        """Voice and upload the answer within the speech budget; ``None`` if that fails."""
        try:
            return await asyncio.wait_for(
                self._voice_and_upload(video, answer_text, answer_language),
                timeout=self._speech_budget_seconds,
            )
        except (IshaaraError, ValueError, TimeoutError):
            logger.warning("answer_not_voiced", video_key=video.video_key, exc_info=True)
            return None

    async def _voice_and_upload(
        self, video: VideoRecord, answer_text: str, answer_language: NarrationLanguage
    ) -> str:
        """Speak the answer and store the audio, returning its public URL."""
        speech = await self._gateway.synthesize_speech(answer_text, answer_language)
        wav_duration_seconds(speech.wav_bytes)  # reject audio that is not a WAV file
        return await self._blob_storage.upload_public(
            f"answers/{video.video_key}/{self._new_id()}.wav",
            speech.wav_bytes,
            "audio/wav",
            ANSWER_CACHE_CONTROL,
        )

    async def _spend_daily_quota(self, user: AuthenticatedUser) -> None:
        """Count one question against the user's daily limit.

        Raises:
            DailyLimitReachedError: If the limit is already reached.
        """
        limit = self._settings.daily_question_limit
        if not await self._usage.increment_and_check(
            user.uid, DailyUsageField.QUESTIONS_ASKED, limit
        ):
            raise DailyLimitReachedError(f"You can ask {limit} questions per day.")
