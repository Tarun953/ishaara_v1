import asyncio
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ishaara.clients.memory_repository import InMemoryUsageRepository, InMemoryVideoRepository
from ishaara.container import AppContainer
from ishaara.main import create_app
from ishaara.models.domain import NarrationLanguage, ReferentKind, VideoStatus
from ishaara.models.gemini_schemas import ChunkAnalysis, TranscriptSegmentRaw, VisualEvent
from ishaara.pipeline.orchestrator import VideoPipeline
from ishaara.services.job_runner import JobRunner
from ishaara.services.question_service import QuestionService
from ishaara.services.video_service import VideoService, build_video_key
from ishaara.settings import Settings, get_settings
from tests.conftest import FakeBlobStorage, FakeGeminiGateway, FakeMetadataSource
from tests.contract.conftest import make_record

URL = "https://youtu.be/dQw4w9WgXcQ"
VIDEO_KEY = build_video_key(
    "dQw4w9WgXcQ", NarrationLanguage.ENGLISH, Settings(_env_file=None).pipeline_version
)
BODY = {"youtube_url": URL, "narration_language": "en"}


def _container(settings: Settings, privacy_status: str = "public") -> AppContainer:
    videos, usage = InMemoryVideoRepository(), InMemoryUsageRepository()
    storage = FakeBlobStorage()
    metadata = FakeMetadataSource(privacy_status=privacy_status)
    analysis = ChunkAnalysis(
        transcript_segments=[
            TranscriptSegmentRaw(
                start_timestamp="00:00", end_timestamp="00:04", text="Hello class."
            )
        ],
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
    gateway = FakeGeminiGateway({0: analysis})
    pipeline = VideoPipeline(videos, metadata, gateway, storage, settings)
    runner = JobRunner(videos, pipeline.run)
    return AppContainer(
        VideoService(videos, usage, metadata, runner, settings),
        videos,
        usage,
        runner,
        storage,
        QuestionService(usage, gateway, storage, settings),
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


def test_history_rows_carry_duration_and_current_status(client: TestClient) -> None:
    client.post("/v1/videos", json=BODY)
    _wait_for_final_status(client, VIDEO_KEY)

    row = client.get("/v1/me/videos").json()["videos"][0]

    assert row["status"] == "ready"
    assert row["duration_seconds"] > 0
    assert row["error_message"] is None


def test_video_status_includes_the_channel_title(client: TestClient) -> None:
    submitted = client.post("/v1/videos", json=BODY).json()

    polled = client.get(f"/v1/videos/{VIDEO_KEY}").json()
    assert submitted["channel_title"]
    assert polled["channel_title"] == submitted["channel_title"]


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


# ---------------- cues, WebVTT and notes (M5) ----------------

RESULT_PATHS = ["cues", "cues.vtt", "notes"]


def _ready_video(client: TestClient) -> str:
    assert client.post("/v1/videos", json=BODY).status_code == 202
    assert _wait_for_final_status(client, VIDEO_KEY)["status"] == "ready"
    return VIDEO_KEY


@pytest.mark.parametrize("suffix", RESULT_PATHS)
def test_result_routes_require_a_token(suffix: str) -> None:
    with _client(Settings(_env_file=None)) as unauthenticated:
        response = unauthenticated.get(f"/v1/videos/{VIDEO_KEY}/{suffix}")

    assert response.status_code == 401


@pytest.mark.parametrize("suffix", RESULT_PATHS)
def test_result_routes_return_404_for_an_unknown_video(client: TestClient, suffix: str) -> None:
    response = client.get(f"/v1/videos/nope_en_v2/{suffix}")

    assert response.status_code == 404
    assert response.json()["code"] == "video_not_found"


def test_result_routes_return_409_while_the_video_is_not_ready() -> None:
    settings = Settings(_env_file=None, auth_bypass_uid="user-1")
    container = _container(settings)
    app = create_app(container)
    app.dependency_overrides[get_settings] = lambda: settings
    states = {
        "QueuedVideo_en_v2": (VideoStatus.QUEUED, "queued"),
        "WorkingVide_en_v2": (VideoStatus.PROCESSING, "still being processed"),
        "BrokenVideo_en_v2": (VideoStatus.FAILED, "gemini_error"),
    }
    with TestClient(app) as test_client:
        for key, (status, _) in states.items():
            record = make_record(key, status=status)
            if status == VideoStatus.FAILED:
                record = record.failed("gemini_error", "Gemini request failed", record.created_at)
            asyncio.run(container.videos.create_if_absent(record))
        for key, (_, fragment) in states.items():
            for suffix in RESULT_PATHS:
                response = test_client.get(f"/v1/videos/{key}/{suffix}")
                assert response.status_code == 409, (key, suffix)
                assert response.json()["code"] == "video_not_ready"
                assert fragment in response.json()["detail"]


def test_cues_endpoint_returns_narrated_cues(client: TestClient) -> None:
    key = _ready_video(client)

    response = client.get(f"/v1/videos/{key}/cues")

    assert response.status_code == 200
    cues = response.json()["cues"]
    assert len(cues) == 1
    assert cues[0]["cue_id"] == "cue-0001"
    assert cues[0]["narration_text"].startswith("On the board:")
    assert cues[0]["audio_url"].startswith("https://audio.test/audio/")
    assert cues[0]["audio_duration_seconds"] == 1.0


def test_vtt_endpoint_returns_a_description_track(client: TestClient) -> None:
    key = _ready_video(client)

    response = client.get(f"/v1/videos/{key}/cues.vtt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/vtt")
    assert response.text.startswith("WEBVTT\nKind: descriptions\nLanguage: en\n")
    assert "00:00:10.000 --> 00:00:11.000" in response.text
    assert "On the board: A slide title." in response.text


def test_notes_default_to_markdown_with_the_transcript_inlined(client: TestClient) -> None:
    key = _ready_video(client)

    response = client.get(f"/v1/videos/{key}/notes")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.text.startswith("# Lecture")
    assert "## 00:00 – 05:00" in response.text
    assert "Hello class." in response.text
    assert "**[Board, 00:10]** A slide title." in response.text


def test_notes_can_be_requested_as_html(client: TestClient) -> None:
    key = _ready_video(client)

    response = client.get(f"/v1/videos/{key}/notes", params={"format": "html"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<main>" in response.text and "<h1>Lecture</h1>" in response.text


def test_notes_reject_an_unknown_format(client: TestClient) -> None:
    key = _ready_video(client)

    response = client.get(f"/v1/videos/{key}/notes", params={"format": "pdf"})

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"


# ---------------- M7 hardening ----------------


@pytest.mark.parametrize("path", ["/v1/videos", "/v1/videos/anything/questions"])
def test_a_body_with_an_oversized_content_length_is_rejected_with_413(
    client: TestClient, path: str
) -> None:
    response = client.post(
        path, content=b"x" * 20_000, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 413
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "request_too_large"


def test_a_chunked_body_without_a_length_is_still_limited(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(20):
            yield b"x" * 1_000

    response = client.post(
        "/v1/videos", content=chunks(), headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"


def test_a_request_at_the_limit_still_works(client: TestClient) -> None:
    body = {"youtube_url": URL, "narration_language": "en"}

    assert client.post("/v1/videos", json=body).status_code == 202


def test_an_oversized_request_is_rejected_before_authentication() -> None:
    with _client(Settings(_env_file=None)) as unauthenticated:  # no auth bypass
        response = unauthenticated.post("/v1/videos", content=b"x" * 20_000)

    assert response.status_code == 413


def test_the_oversized_error_carries_cors_headers(client: TestClient) -> None:
    response = client.post(
        "/v1/videos",
        content=b"x" * 20_000,
        headers={"Origin": "http://localhost:5173", "Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_api_responses_are_not_cacheable_and_nosniff(client: TestClient) -> None:
    api = client.get("/v1/me/videos")
    health = client.get("/health")

    assert api.headers["cache-control"] == "no-store"
    assert api.headers["x-content-type-options"] == "nosniff"
    assert health.headers["x-content-type-options"] == "nosniff"
    assert "cache-control" not in health.headers


@pytest.mark.parametrize(
    "bad_key",
    ["x", "a" * 300, "dQw4w9WgXcQ_fr_v2", "dQw4w9WgXcQ_en_vX", "dQw4w9WgXc!_en_v2"],
)
@pytest.mark.parametrize("suffix", ["", "/cues", "/cues.vtt", "/notes"])
def test_a_malformed_video_key_is_a_404_that_never_reaches_storage(
    client: TestClient, bad_key: str, suffix: str
) -> None:
    response = client.get(f"/v1/videos/{bad_key}{suffix}")

    assert response.status_code == 404
    assert response.json()["code"] == "video_not_found"
    assert bad_key not in response.json()["detail"]


def test_a_malformed_key_cannot_be_asked_about(client: TestClient) -> None:
    response = client.post("/v1/videos/not-a-key/questions", json={"question": "hi"})

    assert response.status_code == 404


def test_an_encoded_slash_in_a_key_matches_no_route_at_all(client: TestClient) -> None:
    response = client.get("/v1/videos/..%2Fsecrets/cues")

    assert response.status_code == 404
