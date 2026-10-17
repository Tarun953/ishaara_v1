"""Question endpoint for a processed video."""

from typing import Annotated

from fastapi import APIRouter, Depends

from ishaara.api.dependencies import get_current_user, get_question_service, get_ready_video
from ishaara.models.api_models import AskQuestionRequest, AuthenticatedUser, QuestionResponse
from ishaara.models.domain import VideoRecord
from ishaara.services.question_service import QuestionService

router = APIRouter(prefix="/v1", dependencies=[Depends(get_current_user)])


@router.post("/videos/{video_key}/questions")
async def ask_question(
    body: AskQuestionRequest,
    video: Annotated[VideoRecord, Depends(get_ready_video)],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    question_service: Annotated[QuestionService, Depends(get_question_service)],
) -> QuestionResponse:
    """Answer a question about a finished video, in text and as speech.

    Raises:
        VideoNotFoundError: If no video has that key.
        VideoNotReadyError: If the video has not finished processing.
        InvalidRequestError: If ``at_seconds`` is beyond the end of the video.
        DailyLimitReachedError: If the user's daily question limit is used up.
    """
    result = await question_service.answer(user, video, body.question, body.at_seconds)
    return QuestionResponse(
        answer_text=result.answer_text,
        answer_audio_url=result.answer_audio_url,
        referenced_seconds=result.referenced_seconds,
    )
