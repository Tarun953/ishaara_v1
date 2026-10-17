import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from ishaara.container import AppContainer
from ishaara.main import create_app
from ishaara.models.domain import NarrationLanguage, VideoStatus
from ishaara.models.gemini_schemas import QuestionAnswer
from ishaara.services.question_service import QuestionService
from ishaara.settings import Settings, get_settings
from tests.api.test_videos_api import _container
from tests.conftest import FakeGeminiGateway
from tests.contract.conftest import make_record

KEY = "dQw4w9WgXcQ_en_v2"
URL = f"/v1/videos/{KEY}/questions"


class QuestionHarness:
    def __init__(self, daily_question_limit: int = 50, gateway: FakeGeminiGateway | None = None):
        self.settings = Settings(
            _env_file=None, auth_bypass_uid="student-1", daily_question_limit=daily_question_limit
        )
        base = _container(self.settings)
        self.gateway = gateway or FakeGeminiGateway()
        self.container = AppContainer(
            base.video_service,
            base.videos,
            base.usage,
            base.job_runner,
            base.blob_storage,
            QuestionService(base.usage, self.gateway, base.blob_storage, self.settings),
        )
        asyncio.run(
            self.container.videos.create_if_absent(
                make_record(KEY, status=VideoStatus.READY).model_copy(
                    update={"duration_seconds": 527}
                )
            )
        )
        app = create_app(self.container)
        app.dependency_overrides[get_settings] = lambda: self.settings
        self.client = TestClient(app)


@pytest.fixture
def harness() -> Iterator[QuestionHarness]:
    built = QuestionHarness()
    with built.client:
        yield built


def test_requires_a_token() -> None:
    built = QuestionHarness()
    built.settings = Settings(_env_file=None)
    app = create_app(built.container)
    app.dependency_overrides[get_settings] = lambda: built.settings
    with TestClient(app) as unauthenticated:
        response = unauthenticated.post(URL, json={"question": "hi"})

    assert response.status_code == 401


def test_unknown_video_is_404(harness: QuestionHarness) -> None:
    response = harness.client.post("/v1/videos/nope_en_v2/questions", json={"question": "hi"})

    assert response.status_code == 404
    assert response.json()["code"] == "video_not_found"


def test_video_that_is_not_ready_is_409() -> None:
    built = QuestionHarness()
    asyncio.run(
        built.container.videos.create_if_absent(
            make_record("queued_en_v2", status=VideoStatus.QUEUED)
        )
    )
    with built.client:
        response = built.client.post("/v1/videos/queued_en_v2/questions", json={"question": "hi"})

    assert response.status_code == 409
    assert response.json()["code"] == "video_not_ready"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"question": ""},
        {"question": "   "},
        {"question": "x" * 501},
        {"question": "ok", "at_seconds": -1},
        {"question": "ok", "at_seconds": "soon"},
        {"question": "ok", "at_seconds": 9999},
    ],
)
def test_bad_requests_are_422_problems(harness: QuestionHarness, body: dict[str, object]) -> None:
    response = harness.client.post(URL, json=body)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert harness.gateway.text_questions == []


def test_a_question_gets_a_written_and_spoken_answer(harness: QuestionHarness) -> None:
    response = harness.client.post(
        URL, json={"question": "  What was on the board?  ", "at_seconds": 30}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer_text"] == "A circle was drawn."
    assert body["answer_audio_url"].startswith(f"https://audio.test/answers/{KEY}/")
    assert body["referenced_seconds"] == [23.0]
    assert harness.gateway.text_questions[0][0] == "What was on the board?"  # trimmed


def test_a_question_with_no_time_is_accepted(harness: QuestionHarness) -> None:
    response = harness.client.post(URL, json={"question": "What is this lecture about?"})

    assert response.status_code == 200
    assert harness.gateway.text_questions[0][2] is None


def test_a_visual_question_uses_the_clip_answer() -> None:
    gateway = FakeGeminiGateway(
        text_answer=QuestionAnswer(
            answer_text="I can't tell.",
            referenced_timestamps=[],
            needs_visual_check=True,
            answer_language=NarrationLanguage.ENGLISH,
        )
    )
    built = QuestionHarness(gateway=gateway)
    with built.client:
        response = built.client.post(URL, json={"question": "What is drawn?", "at_seconds": 100})

    assert response.json()["answer_text"] == "The clip shows a tick mark."
    assert len(gateway.visual_questions) == 1


def test_an_answer_that_could_not_be_voiced_has_a_null_audio_url() -> None:
    gateway = FakeGeminiGateway()
    gateway.fail_speech = True
    built = QuestionHarness(gateway=gateway)
    with built.client:
        response = built.client.post(URL, json={"question": "What is drawn?"})

    assert response.status_code == 200
    assert response.json()["answer_audio_url"] is None
    assert response.json()["answer_text"]


def test_the_daily_question_limit_is_a_429_problem() -> None:
    built = QuestionHarness(daily_question_limit=1)
    with built.client:
        first = built.client.post(URL, json={"question": "one"})
        second = built.client.post(URL, json={"question": "two"})

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["code"] == "daily_limit_reached"
