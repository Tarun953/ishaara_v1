import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ishaara.clients.memory_repository import InMemoryUsageRepository, InMemoryVideoRepository
from ishaara.container import AppContainer
from ishaara.main import create_app
from ishaara.models.domain import NarrationLanguage, ReferentKind
from ishaara.models.gemini_schemas import ChunkAnalysis, VisualEvent
from ishaara.pipeline.orchestrator import VideoPipeline
from ishaara.services.job_runner import JobRunner
from ishaara.services.video_service import VideoService, build_video_key
from ishaara.settings import Settings, get_settings
from tests.conftest import FakeBlobStorage, FakeGeminiGateway, FakeMetadataSource

URL = "https://youtu.be/dQw4w9WgXcQ"
VIDEO_KEY = build_video_key(
    "dQw4w9WgXcQ", NarrationLanguage.ENGLISH, Settings(_env_file=None).pipeline_version
)
BODY = {"youtube_url": URL, "narration_language": "en"}


def _container(settings: Settings, privacy_status: str = "public") -> AppContainer:
    videos, usage = InMemoryVideoRepository(), InMemoryUsageRepository()
    metadata = FakeMetadataSource(privacy_status=privacy_status)
    analysis = ChunkAnalysis(
        transcript_segments=[],
        visual_events=[
            VisualEvent(
                timestamp="00:10",
                referent_kind=ReferentKind.TEXT,
                content_description="A slide title.",
                words_spoken_nearby="",
                confidence=0.9,
            )
        ],
    )
    pipeline = VideoPipeline(
        videos, metadata, FakeGeminiGateway({0: analysis}), FakeBlobStorage(), settings
    )
    runner = JobRunner(videos, pipeline.run)
    return AppContainer(
        VideoService(videos, usage, metadata, runner, settings), videos, usage, runner
    )


def _client(settings: Settings, privacy_status: str = "public") -> TestClient:
    app = create_app(_container(settings, privacy_status))
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with _client(Settings(_env_file=None, auth_bypass_uid="user-1")) as test_client:
        yield test_client


def _wait_for_final_status(client: TestClient, video_key: str) -> dict[str, object]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        body: dict[str, object] = client.get(f"/v1/videos/{video_key}").json()
        if body["status"] in ("ready", "failed"):
            return body
        time.sleep(0.02)
    raise AssertionError("video did not finish in time")


def test_routes_require_a_token() -> None:
    with _client(Settings(_env_file=None)) as unauthenticated:
        response = unauthenticated.get("/v1/me/videos")

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_invalid_url_is_a_400_problem(client: TestClient) -> None:
    response = client.post("/v1/videos", json={**BODY, "youtube_url": "https://evil.com/x"})

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "invalid_youtube_url"


def test_missing_field_is_a_422_problem(client: TestClient) -> None:
    response = client.post("/v1/videos", json={"youtube_url": URL})

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert "narration_language" in response.json()["detail"]


def test_submit_poll_ready_then_cache_hit(client: TestClient) -> None:
    first = client.post("/v1/videos", json=BODY)

    assert first.status_code == 202
    assert first.json()["video_key"] == VIDEO_KEY

    final = _wait_for_final_status(client, VIDEO_KEY)
    assert final["status"] == "ready"
    assert final["progress_percent"] == 100

    second = client.post("/v1/videos", json=BODY)
    assert second.status_code == 200
    assert second.json()["status"] == "ready"


def test_unknown_video_is_a_404_problem(client: TestClient) -> None:
    response = client.get("/v1/videos/nope_en_v1")

    assert response.status_code == 404
    assert response.json()["code"] == "video_not_found"


def test_history_lists_submissions_newest_first(client: TestClient) -> None:
    client.post("/v1/videos", json=BODY)
    client.post("/v1/videos", json={**BODY, "youtube_url": "https://youtu.be/abcdefghijk"})

    response = client.get("/v1/me/videos", params={"limit": 5})

    assert response.status_code == 200
    keys = [row["video_key"] for row in response.json()["videos"]]
    assert keys == [
        build_video_key(
            "abcdefghijk", NarrationLanguage.ENGLISH, Settings(_env_file=None).pipeline_version
        ),
        VIDEO_KEY,
    ]


def test_history_limit_is_validated(client: TestClient) -> None:
    assert client.get("/v1/me/videos", params={"limit": 0}).status_code == 422


def test_daily_limit_is_a_429_problem() -> None:
    settings = Settings(_env_file=None, auth_bypass_uid="user-1", daily_video_limit=1)
    with _client(settings) as limited:
        limited.post("/v1/videos", json=BODY)
        response = limited.post(
            "/v1/videos", json={**BODY, "youtube_url": "https://youtu.be/abcdefghijk"}
        )

    assert response.status_code == 429
    assert response.json()["code"] == "daily_limit_reached"


def test_private_video_is_a_422_problem() -> None:
    settings = Settings(_env_file=None, auth_bypass_uid="user-1")
    with _client(settings, privacy_status="private") as private_client:
        response = private_client.post("/v1/videos", json=BODY)

    assert response.status_code == 422
    assert response.json()["code"] == "video_not_public"


def test_local_media_files_are_served_under_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "local")  # do not depend on the developer's .env
    monkeypatch.setenv("LOCAL_STORAGE_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        audio = tmp_path / "public" / "audio" / "k" / "cue-0001.wav"
        audio.parent.mkdir(parents=True)
        audio.write_bytes(b"RIFFdata")

        response = TestClient(create_app()).get("/media/audio/k/cue-0001.wav")

        assert response.status_code == 200
        assert response.content == b"RIFFdata"
    finally:
        get_settings.cache_clear()
