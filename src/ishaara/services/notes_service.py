"""Loading what the notes need beyond the video record."""

import json

import structlog
from pydantic import TypeAdapter

from ishaara.clients.blob_storage import BlobStorage
from ishaara.errors import StorageError
from ishaara.models.domain import TranscriptSegment, VideoRecord

logger = structlog.get_logger()

_SEGMENTS = TypeAdapter(list[TranscriptSegment])


async def load_transcript(
    blob_storage: BlobStorage, record: VideoRecord
) -> list[TranscriptSegment]:
    """Read the stored transcript for a video.

    A missing, unreadable or malformed transcript is not an error: the notes simply carry
    the board descriptions without transcript paragraphs.

    Args:
        blob_storage: Where the transcript JSON is stored.
        record: The processed video.

    Returns:
        The transcript sentences, or an empty list if they are unavailable.
    """
    if not record.transcript_blob_path:
        return []
    try:
        payload = json.loads(await blob_storage.download_private(record.transcript_blob_path))
        return _SEGMENTS.validate_python(payload["segments"])
    except (StorageError, ValueError, KeyError, TypeError) as error:
        logger.warning("transcript_unavailable", video_key=record.video_key, error=str(error)[:200])
        return []
