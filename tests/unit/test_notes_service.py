import json

from ishaara.services.notes_service import load_transcript
from tests.conftest import FakeBlobStorage
from tests.contract.conftest import make_record

SEGMENTS = [{"start_seconds": 1.0, "end_seconds": 3.5, "text": "Hello class."}]


async def _load(storage: FakeBlobStorage, path: str | None):  # type: ignore[no-untyped-def]
    record = make_record().model_copy(update={"transcript_blob_path": path})
    return await load_transcript(storage, record)


async def test_reads_the_stored_segments() -> None:
    storage = FakeBlobStorage()
    storage.private["t/k.json"] = json.dumps({"video_key": "k", "segments": SEGMENTS}).encode()

    segments = await _load(storage, "t/k.json")

    assert [(s.start_seconds, s.text) for s in segments] == [(1.0, "Hello class.")]


async def test_no_path_missing_file_and_bad_content_all_give_an_empty_transcript() -> None:
    storage = FakeBlobStorage()
    storage.private["t/not-json"] = b"<html>"
    storage.private["t/no-segments"] = b'{"video_key": "k"}'
    storage.private["t/wrong-shape"] = b'{"segments": [{"text": 5}]}'

    assert await _load(storage, None) == []
    assert await _load(storage, "t/missing.json") == []
    assert await _load(storage, "t/not-json") == []
    assert await _load(storage, "t/no-segments") == []
    assert await _load(storage, "t/wrong-shape") == []
