"""Blob storage on the local disk, for development without cloud credentials."""

import asyncio
from pathlib import Path

from ishaara.clients.blob_storage import validate_blob_path
from ishaara.errors import StorageError

PUBLIC_DIRECTORY = "public"
PRIVATE_DIRECTORY = "private"
MEDIA_URL_PREFIX = "/media"


class LocalBlobStorage:
    """``BlobStorage`` that writes files under a local directory.

    Public files live in ``<root>/public`` (served by the app at ``/media`` in local
    mode); private files live in ``<root>/private`` and are never served.

    Args:
        root: Base directory for stored files.
        public_base_url: Origin the app is reachable at, used to build file URLs.
    """

    def __init__(self, root: Path, public_base_url: str) -> None:
        """Store the root directory and base URL."""
        self._root = root
        self._public_base_url = public_base_url.rstrip("/")

    @property
    def public_directory(self) -> Path:
        """Return the directory served at ``/media``."""
        return self._root / PUBLIC_DIRECTORY

    async def upload_public(
        self, path: str, data: bytes, content_type: str, cache_control: str
    ) -> str:
        """Write a public file and return its URL under ``/media``."""
        await self._write(self.public_directory / validate_blob_path(path), data)
        return f"{self._public_base_url}{MEDIA_URL_PREFIX}/{path}"

    async def upload_private(self, path: str, data: bytes, content_type: str) -> str:
        """Write a private file and return its blob path."""
        await self._write(self._root / PRIVATE_DIRECTORY / validate_blob_path(path), data)
        return path

    async def _write(self, destination: Path, data: bytes) -> None:
        """Write ``data`` to ``destination`` without blocking the event loop."""

        def write() -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)

        try:
            await asyncio.to_thread(write)
        except OSError as error:
            raise StorageError("Could not write the file to local storage.") from error
