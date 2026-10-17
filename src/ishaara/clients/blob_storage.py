"""Blob storage port: where narration audio and transcripts are kept.

Implementations: local files (development), Google Cloud Storage, and Supabase Storage.
"""

from typing import Protocol


class BlobStorage(Protocol):
    """Stores files that are either publicly playable or private to the backend."""

    async def upload_public(
        self, path: str, data: bytes, content_type: str, cache_control: str
    ) -> str:
        """Store a file anyone can fetch and return its public URL.

        Raises:
            StorageError: If the file could not be stored.
        """
        ...

    async def upload_private(self, path: str, data: bytes, content_type: str) -> str:
        """Store a file only the backend can read and return its blob path.

        Raises:
            StorageError: If the file could not be stored.
        """
        ...


def validate_blob_path(path: str) -> str:
    """Reject paths that could escape a storage root.

    Args:
        path: A relative blob path such as ``audio/<video_key>/cue-0001.wav``.

    Returns:
        The path unchanged.

    Raises:
        ValueError: If the path is empty, absolute, or contains ``..``.
    """
    segments = path.split("/")
    if not path or path.startswith("/") or "\\" in path or ".." in segments or "" in segments:
        raise ValueError(f"Unsafe blob path: {path!r}")
    return path
