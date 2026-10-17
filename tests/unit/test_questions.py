import json

import pytest

from ishaara.clients.memory_repository import InMemoryUsageRepository
from ishaara.errors import DailyLimitReachedError, GeminiError, InvalidRequestError
from ishaara.models.api_models import AuthenticatedUser
from ishaara.models.domain import (
    NarrationCue,
    NarrationLanguage,
    ReferentKind,
    TranscriptSegment,
    VideoStatus,
)
from ishaara.models.gemini_schemas import QuestionAnswer
from ishaara.pipeline.chunking import ClipWindow
from ishaara.pipeline.questions import (
    MAX_TRANSCRIPT_CHARACTERS,
    build_question_context,
    parse_referenced_seconds,
    visual_clip_window,
)
from ishaara.services.question_service import QuestionService
from ishaara.settings import Settings
from tests.conftest import FakeBlobStorage, FakeGeminiGateway
from tests.contract.conftest import FakeClock, make_record

USER = AuthenticatedUser(uid="student-1")


def _cue(number: int, start: float, text: str) -> NarrationCue:
    return NarrationCue(
        cue_id=f"cue-{number:04d}",
        start_seconds=start,
        spoken_phrase="",
        referent_kind=ReferentKind.DIAGRAM,
        board_description=text,
        gap_summary=text,
        narration_text=f"On the board: {text}",
        confidence=0.9,
    )


def _segment(start: float, text: str) -> TranscriptSegment:
    return TranscriptSegment(start_seconds=start, end_seconds=start + 3, text=text)


# ---------------- pure helpers ----------------


def test_context_with_a_time_keeps_only_the_surrounding_four_minutes() -> None:
    cues = [_cue(1, 30, "early circle"), _cue(2, 400, "middle graph"), _cue(3, 900, "late table")]
    segments = [_segment(10, "intro"), _segment(390, "so here"), _segment(850, "outro")]

    context = build_question_context(cues, segments, at_seconds=400)

    assert "[06:30] so here" in context and "[06:40] middle graph" in context
    assert "intro" not in context and "early circle" not in context
    assert "outro" not in context and "late table" not in context


def test_context_without_a_time_includes_everything_in_order() -> None:
    cues = [_cue(2, 400, "second"), _cue(1, 30, "first")]
    segments = [_segment(390, "later"), _segment(10, "earlier")]

    context = build_question_context(cues, segments, at_seconds=None)

    assert context.index("earlier") < context.index("later")
    assert context.index("first") < context.index("second")


def test_empty_parts_say_so_instead_of_being_blank() -> None:
    context = build_question_context([], [], at_seconds=100)

    assert context.count("(none in this part of the lecture)") == 2


def test_very_long_transcripts_are_cut_at_a_line_with_a_marker() -> None:
    segments = [_segment(i, "x" * 200) for i in range(400)]

    context = build_question_context([], segments, at_seconds=None)

    transcript = context.split("\n\nON-SCREEN")[0]
    assert len(transcript) <= MAX_TRANSCRIPT_CHARACTERS + 100
    assert "[transcript truncated]" in transcript


@pytest.mark.parametrize(
    ("at_seconds", "duration", "expected"),
    [
        (100, 527, ClipWindow(85, 115)),
        (5, 527, ClipWindow(0, 20)),
        (520, 527, ClipWindow(505, 527)),
        (527, 527, ClipWindow(512, 527)),
        (10, 12, ClipWindow(0, 12)),
    ],
)
def test_visual_window_is_thirty_seconds_clamped_to_the_video(
    at_seconds: float, duration: int, expected: ClipWindow
) -> None:
    assert visual_clip_window(at_seconds, duration) == expected


def test_referenced_times_are_parsed_deduplicated_and_kept_inside_the_video() -> None:
    result = parse_referenced_seconds(["01:00", "00:23", "bad", "99:00", "00:23", "-1:00"], 527)

    assert result == [23.0, 60.0]


# ---------------- service ----------------

QUESTION = "What was on the board?"


class Setup:
    def __init__(self, daily_question_limit: int = 50, gateway: FakeGeminiGateway | None = None):
        self.gateway = gateway or FakeGeminiGateway()
        self.storage = FakeBlobStorage()
        self.usage = InMemoryUsageRepository(FakeClock())
        settings = Settings(_env_file=None, daily_question_limit=daily_question_limit)
        self.service = QuestionService(
            self.usage, self.gateway, self.storage, settings, new_id=lambda: "abc123"
        )
        self.video = make_record("dQw4w9WgXcQ_en_v2", status=VideoStatus.READY).model_copy(
            update={
                "duration_seconds": 527,
                "cues": [_cue(1, 23.0, "A tick mark at the bottom.")],
                "transcript_blob_path": "transcripts/t.json",
            }
        )
        self.storage.private["transcripts/t.json"] = json.dumps(
            {"segments": [_segment(20, "A and B run.").model_dump(mode="json")]}
        ).encode()


def _answer(**overrides: object) -> QuestionAnswer:
    fields: dict[str, object] = {
        "answer_text": "A circle was drawn.",
        "referenced_timestamps": ["00:23"],
        "needs_visual_check": False,
        "answer_language": NarrationLanguage.ENGLISH,
    }
    fields.update(overrides)
    return QuestionAnswer.model_validate(fields)


async def test_text_answer_is_voiced_and_uploaded_without_looking_at_the_video() -> None:
    setup = Setup()

    result = await setup.service.answer(USER, setup.video, QUESTION, 30.0)

    assert result.answer_text == "A circle was drawn."
    assert result.answer_audio_url == "https://audio.test/answers/dQw4w9WgXcQ_en_v2/abc123.wav"
    assert "answers/dQw4w9WgXcQ_en_v2/abc123.wav" in setup.storage.public
    assert result.referenced_seconds == [23.0]
    assert setup.gateway.visual_questions == []
    question, context, at_seconds = setup.gateway.text_questions[0]
    assert question == QUESTION and at_seconds == 30.0
    assert "A and B run." in context and "A tick mark at the bottom." in context


async def test_a_visual_check_with_a_time_looks_at_the_clip_and_uses_that_answer() -> None:
    gateway = FakeGeminiGateway(text_answer=_answer(needs_visual_check=True))
    setup = Setup(gateway=gateway)

    result = await setup.service.answer(USER, setup.video, QUESTION, 100.0)

    assert result.answer_text == "The clip shows a tick mark."
    assert gateway.visual_questions == [("dQw4w9WgXcQ", QUESTION, ClipWindow(85, 115), 100.0)]
    assert gateway.speech_requests[0][0] == "The clip shows a tick mark."


async def test_a_visual_check_without_a_time_keeps_the_text_answer() -> None:
    gateway = FakeGeminiGateway(text_answer=_answer(needs_visual_check=True))
    setup = Setup(gateway=gateway)

    result = await setup.service.answer(USER, setup.video, QUESTION, None)

    assert result.answer_text == "A circle was drawn."
    assert gateway.visual_questions == []


async def test_a_failing_visual_check_falls_back_to_the_text_answer() -> None:
    gateway = FakeGeminiGateway(text_answer=_answer(needs_visual_check=True))
    gateway.fail_visual = True
    setup = Setup(gateway=gateway)

    result = await setup.service.answer(USER, setup.video, QUESTION, 100.0)

    assert result.answer_text == "A circle was drawn."


async def test_the_answer_is_spoken_in_the_language_gemini_chose() -> None:
    gateway = FakeGeminiGateway(
        text_answer=_answer(
            answer_text="एक वृत्त बनाया गया।", answer_language=NarrationLanguage.HINDI
        )
    )
    setup = Setup(gateway=gateway)

    await setup.service.answer(USER, setup.video, "बोर्ड पर क्या था?", 30.0)

    assert gateway.speech_requests == [("एक वृत्त बनाया गया।", NarrationLanguage.HINDI)]


async def test_speech_failure_still_returns_the_text() -> None:
    gateway = FakeGeminiGateway()
    gateway.fail_speech = True

    result = await Setup(gateway=gateway).service.answer(
        USER, Setup(gateway=gateway).video, QUESTION, 30.0
    )

    assert result.answer_text == "A circle was drawn." and result.answer_audio_url is None


async def test_upload_failure_still_returns_the_text() -> None:
    setup = Setup()
    setup.storage.fail_public = True

    result = await setup.service.answer(USER, setup.video, QUESTION, 30.0)

    assert result.answer_audio_url is None and result.answer_text


async def test_missing_transcript_still_answers_from_the_cues() -> None:
    setup = Setup()
    setup.storage.private.clear()

    await setup.service.answer(USER, setup.video, QUESTION, 30.0)

    _, context, _ = setup.gateway.text_questions[0]
    assert "A tick mark at the bottom." in context


async def test_daily_limit_stops_the_question_before_any_gemini_call() -> None:
    setup = Setup(daily_question_limit=1)
    await setup.service.answer(USER, setup.video, QUESTION, 30.0)

    with pytest.raises(DailyLimitReachedError):
        await setup.service.answer(USER, setup.video, QUESTION, 30.0)

    assert len(setup.gateway.text_questions) == 1
    other_user = AuthenticatedUser(uid="student-2")
    await setup.service.answer(other_user, setup.video, QUESTION, 30.0)  # limits are per user


async def test_a_time_beyond_the_video_is_rejected_without_using_quota() -> None:
    setup = Setup(daily_question_limit=1)

    with pytest.raises(InvalidRequestError):
        await setup.service.answer(USER, setup.video, QUESTION, 9999.0)

    await setup.service.answer(USER, setup.video, QUESTION, 30.0)  # quota still available
    assert len(setup.gateway.text_questions) == 1


async def test_an_empty_answer_is_an_invalid_response() -> None:
    gateway = FakeGeminiGateway(text_answer=_answer(answer_text="   "))

    with pytest.raises(GeminiError) as error_info:
        await Setup(gateway=gateway).service.answer(
            USER, Setup(gateway=gateway).video, QUESTION, 30.0
        )

    assert error_info.value.code == "gemini_invalid_response"


async def test_a_slow_speech_call_returns_the_text_answer_within_the_budget() -> None:
    import asyncio
    import time

    class SlowSpeechGateway(FakeGeminiGateway):
        async def synthesize_speech(self, narration_text, narration_language):  # type: ignore[no-untyped-def]
            await asyncio.Event().wait()

    gateway = SlowSpeechGateway()
    setup = Setup(gateway=gateway)
    service = QuestionService(
        setup.usage, gateway, setup.storage, Settings(_env_file=None), speech_budget_seconds=0.05
    )
    started = time.monotonic()

    result = await service.answer(USER, setup.video, QUESTION, 30.0)

    assert time.monotonic() - started < 2
    assert result.answer_text == "A circle was drawn." and result.answer_audio_url is None
