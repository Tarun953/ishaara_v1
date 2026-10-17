from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from google.api_core.exceptions import NotFound, ServiceUnavailable, TooManyRequests

from ishaara.clients.blob_storage import validate_blob_path
from ishaara.clients.blob_storage_factory import build_blob_storage
from ishaara.clients.gcs_blob_storage import GcsBlobStorage
from ishaara.clients.local_blob_storage import LocalBlobStorage
from ishaara.clients.supabase_blob_storage import SupabaseBlobStorage
from ishaara.errors import StorageError
from ishaara.settings import Settings

CACHE = "public, max-age=31536000"


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("asyncio.sleep", instant_sleep)


@pytest.mark.parametrize("path", ["", "/abs/x.wav", "a/../b", "../x", "a//b", "a\\b"])
def test_unsafe_paths_are_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        validate_blob_path(path)


def test_normal_path_is_accepted() -> None:
    assert validate_blob_path("audio/vid_en_v2/cue-0001.wav") == "audio/vid_en_v2/cue-0001.wav"


# ---- local ----


async def test_local_public_upload_writes_file_and_returns_media_url(tmp_path: Path) -> None:
    storage = LocalBlobStorage(tmp_path, "http://localhost:8000/")

    url = await storage.upload_public("audio/k/cue-0001.wav", b"RIFF", "audio/wav", CACHE)

    assert url == "http://localhost:8000/media/audio/k/cue-0001.wav"
    assert (tmp_path / "public" / "audio" / "k" / "cue-0001.wav").read_bytes() == b"RIFF"


async def test_local_private_upload_is_not_in_the_public_directory(tmp_path: Path) -> None:
    storage = LocalBlobStorage(tmp_path, "http://localhost:8000")

    blob_path = await storage.upload_private("transcripts/k.json", b"{}", "application/json")

    assert blob_path == "transcripts/k.json"
    assert (tmp_path / "private" / "transcripts" / "k.json").exists()
    assert not (tmp_path / "public" / "transcripts").exists()


async def test_local_upload_overwrites_and_rejects_traversal(tmp_path: Path) -> None:
    storage = LocalBlobStorage(tmp_path, "http://x")
    await storage.upload_public("a/b.wav", b"one", "audio/wav", CACHE)
    await storage.upload_public("a/b.wav", b"two", "audio/wav", CACHE)

    assert (tmp_path / "public" / "a" / "b.wav").read_bytes() == b"two"
    with pytest.raises(ValueError):
        await storage.upload_public("../escape.wav", b"x", "audio/wav", CACHE)


async def test_local_private_round_trip_and_missing_file(tmp_path: Path) -> None:
    storage = LocalBlobStorage(tmp_path, "http://x")
    await storage.upload_private("transcripts/k.json", b'{"segments": []}', "application/json")

    assert await storage.download_private("transcripts/k.json") == b'{"segments": []}'
    with pytest.raises(StorageError):
        await storage.download_private("transcripts/missing.json")
    with pytest.raises(ValueError):
        await storage.download_private("../private/transcripts/k.json")


# ---- GCS ----


class _FakeBlob:
    def __init__(
        self, uploads: list[dict[str, Any]], bucket: str, path: str, failures: list[Exception]
    ):
        self._uploads, self._bucket, self._path, self._failures = uploads, bucket, path, failures
        self.cache_control: str | None = None

    def download_as_bytes(self, timeout: float) -> bytes:
        if self._failures:
            raise self._failures.pop(0)
        for upload in self._uploads:
            if (upload["bucket"], upload["path"]) == (self._bucket, self._path):
                data: bytes = upload["data"]
                return data
        raise NotFound("no such object")

    def upload_from_string(self, data: bytes, content_type: str, timeout: float) -> None:
        if self._failures:
            raise self._failures.pop(0)
        self._uploads.append(
            {
                "bucket": self._bucket,
                "path": self._path,
                "data": data,
                "type": content_type,
                "cache": self.cache_control,
                "timeout": timeout,
            }
        )


class _FakeBucket:
    def __init__(self, client: "_FakeGcsClient", name: str) -> None:
        self._client, self._name = client, name

    def blob(self, path: str) -> _FakeBlob:
        return _FakeBlob(self._client.uploads, self._name, path, self._client.failures)


class _FakeGcsClient:
    def __init__(self, failures: list[Exception] | None = None) -> None:
        self.uploads: list[dict[str, Any]] = []
        self.failures = failures or []

    def bucket(self, name: str) -> _FakeBucket:
        return _FakeBucket(self, name)


async def test_gcs_public_and_private_go_to_their_buckets() -> None:
    client = _FakeGcsClient()
    storage = GcsBlobStorage(client, "audio-bucket", "transcript-bucket")

    url = await storage.upload_public("audio/k/cue 1.wav", b"RIFF", "audio/wav", CACHE)
    blob_path = await storage.upload_private("transcripts/k.json", b"{}", "application/json")

    assert url == "https://storage.googleapis.com/audio-bucket/audio/k/cue%201.wav"
    assert blob_path == "transcripts/k.json"
    public, private = client.uploads
    assert (public["bucket"], public["cache"], public["type"]) == (
        "audio-bucket",
        CACHE,
        "audio/wav",
    )
    assert (private["bucket"], private["cache"]) == ("transcript-bucket", None)


async def test_gcs_retries_429_and_5xx_then_succeeds() -> None:
    client = _FakeGcsClient([TooManyRequests("slow down"), ServiceUnavailable("down")])
    storage = GcsBlobStorage(client, "a", "t")

    await storage.upload_public("x/y.wav", b"1", "audio/wav", CACHE)

    assert len(client.uploads) == 1


async def test_gcs_gives_up_after_three_attempts_with_storage_error() -> None:
    failures: list[Exception] = [ServiceUnavailable("down")] * 3
    storage = GcsBlobStorage(_FakeGcsClient(failures), "a", "t")

    with pytest.raises(StorageError):
        await storage.upload_public("x/y.wav", b"1", "audio/wav", CACHE)


async def test_gcs_download_reads_the_transcript_bucket_and_maps_not_found() -> None:
    client = _FakeGcsClient()
    storage = GcsBlobStorage(client, "audio-bucket", "transcript-bucket")
    await storage.upload_private("transcripts/k.json", b"[1]", "application/json")

    assert await storage.download_private("transcripts/k.json") == b"[1]"
    with pytest.raises(StorageError):
        await storage.download_private("transcripts/missing.json")


async def test_gcs_download_retries_503_then_succeeds() -> None:
    client = _FakeGcsClient()
    storage = GcsBlobStorage(client, "audio-bucket", "transcript-bucket")
    await storage.upload_private("t/k.json", b"ok", "application/json")
    client.failures.append(ServiceUnavailable("down"))

    assert await storage.download_private("t/k.json") == b"ok"


# ---- Supabase ----

BASE = "https://proj.supabase.co"


def _supabase(client: httpx.AsyncClient) -> SupabaseBlobStorage:
    return SupabaseBlobStorage(client, BASE + "/", "service-key", "audio", "transcripts")


@respx.mock
async def test_supabase_public_upload_posts_with_service_key_and_returns_public_url() -> None:
    route = respx.post(f"{BASE}/storage/v1/object/audio/audio/k/cue-0001.wav").mock(
        return_value=httpx.Response(200, json={"Key": "audio/audio/k/cue-0001.wav"})
    )
    async with httpx.AsyncClient() as client:
        url = await _supabase(client).upload_public(
            "audio/k/cue-0001.wav", b"RIFF", "audio/wav", CACHE
        )

    assert url == f"{BASE}/storage/v1/object/public/audio/audio/k/cue-0001.wav"
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer service-key"
    assert request.headers["x-upsert"] == "true"
    assert request.headers["content-type"] == "audio/wav"
    assert request.headers["cache-control"] == CACHE
    assert request.content == b"RIFF"


@respx.mock
async def test_supabase_private_upload_uses_the_transcript_bucket() -> None:
    route = respx.post(f"{BASE}/storage/v1/object/transcripts/transcripts/k.json").mock(
        return_value=httpx.Response(200, json={})
    )
    async with httpx.AsyncClient() as client:
        blob_path = await _supabase(client).upload_private(
            "transcripts/k.json", b"{}", "application/json"
        )

    assert blob_path == "transcripts/k.json"
    assert route.called


@respx.mock
async def test_supabase_retries_503_but_not_400() -> None:
    url = f"{BASE}/storage/v1/object/audio/x/y.wav"
    flaky = respx.post(url).mock(side_effect=[httpx.Response(503), httpx.Response(200, json={})])
    async with httpx.AsyncClient() as client:
        await _supabase(client).upload_public("x/y.wav", b"1", "audio/wav", CACHE)
    assert flaky.call_count == 2

    respx.reset()
    bad = respx.post(url).mock(return_value=httpx.Response(400, json={"error": "bad bucket"}))
    async with httpx.AsyncClient() as client:
        with pytest.raises(StorageError):
            await _supabase(client).upload_public("x/y.wav", b"1", "audio/wav", CACHE)
    assert bad.call_count == 1


# ---- factory and settings ----


async def test_factory_builds_local_by_default_and_checks_required_settings(tmp_path: Path) -> None:
    async with httpx.AsyncClient() as client:
        local = build_blob_storage(
            Settings(_env_file=None, local_storage_dir=str(tmp_path)), client
        )
        assert isinstance(local, LocalBlobStorage)
        with pytest.raises(ValueError, match="AUDIO_BUCKET"):
            build_blob_storage(Settings(_env_file=None, storage_backend="gcs"), client)
        with pytest.raises(ValueError, match="SUPABASE_SERVICE_KEY"):
            build_blob_storage(Settings(_env_file=None, storage_backend="supabase"), client)
        supabase = build_blob_storage(
            Settings(
                _env_file=None,
                storage_backend="supabase",
                supabase_url=BASE,
                supabase_service_key="k",
                audio_bucket="a",
                transcript_bucket="t",
            ),
            client,
        )
        assert isinstance(supabase, SupabaseBlobStorage)


def test_production_refuses_local_storage() -> None:
    with pytest.raises(ValueError, match="STORAGE_BACKEND=local"):
        Settings(_env_file=None, environment="production", database_backend="supabase")


@respx.mock
async def test_supabase_download_reads_the_private_bucket_with_the_service_key() -> None:
    route = respx.get(f"{BASE}/storage/v1/object/transcripts/transcripts/k.json").mock(
        return_value=httpx.Response(200, content=b'{"segments": []}')
    )
    async with httpx.AsyncClient() as client:
        data = await _supabase(client).download_private("transcripts/k.json")

    assert data == b'{"segments": []}'
    assert route.calls.last.request.headers["authorization"] == "Bearer service-key"
    assert route.calls.last.request.headers["apikey"] == "service-key"


@respx.mock
async def test_supabase_download_retries_503_and_maps_a_missing_file() -> None:
    url = f"{BASE}/storage/v1/object/transcripts/transcripts/k.json"
    flaky = respx.get(url).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, content=b"ok")]
    )
    async with httpx.AsyncClient() as client:
        assert await _supabase(client).download_private("transcripts/k.json") == b"ok"
    assert flaky.call_count == 2

    respx.reset()
    respx.get(url).mock(return_value=httpx.Response(400, json={"error": "not_found"}))
    async with httpx.AsyncClient() as client:
        with pytest.raises(StorageError):
            await _supabase(client).download_private("transcripts/k.json")
