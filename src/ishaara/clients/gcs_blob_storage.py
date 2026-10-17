"""Blob storage on Google Cloud Storage.

The audio bucket is public-read (set once at bucket level), the transcript bucket is
private. Credentials come from Application Default Credentials / the Cloud Run service
account.
"""

import asyncio
from typing import Any
from urllib.parse import quote

from google.api_core.exceptions import GoogleAPICallError
from google.cloud import storage  # type: ignore[attr-defined]
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ishaara.clients.blob_storage import validate_blob_path
from ishaara.errors import StorageError

UPLOAD_TIMEOUT_SECONDS = 30.0
MAX_ATTEMPTS = 3


def create_storage_client(project: str | None) -> Any:
    """Create a Cloud Storage client using Application Default Credentials."""
    return storage.Client(project=project)


def _is_retryable(error: BaseException) -> bool:
    """Return True for 429 and 5xx responses."""
    return isinstance(error, GoogleAPICallError) and (
        error.code == 429 or (error.code is not None and error.code >= 500)
    )


class GcsBlobStorage:
    """``BlobStorage`` backed by two Cloud Storage buckets.

    Args:
        client: A ``google.cloud.storage.Client``.
        audio_bucket: Public-read bucket for narration audio.
        transcript_bucket: Private bucket for transcript JSON.
    """

    def __init__(self, client: Any, audio_bucket: str, transcript_bucket: str) -> None:
        """Store the client and bucket names."""
        self._client = client
        self._audio_bucket = audio_bucket
        self._transcript_bucket = transcript_bucket

    async def upload_public(
        self, path: str, data: bytes, content_type: str, cache_control: str
    ) -> str:
        """Upload to the audio bucket and return its public URL."""
        await self._upload(self._audio_bucket, path, data, content_type, cache_control)
        return f"https://storage.googleapis.com/{self._audio_bucket}/{quote(path)}"

    async def upload_private(self, path: str, data: bytes, content_type: str) -> str:
        """Upload to the transcript bucket and return the blob path."""
        await self._upload(self._transcript_bucket, path, data, content_type, None)
        return path

    async def _upload(
        self, bucket_name: str, path: str, data: bytes, content_type: str, cache_control: str | None
    ) -> None:
        """Upload one object, translating failures into ``StorageError``."""
        validate_blob_path(path)
        try:
            await self._upload_with_retries(bucket_name, path, data, content_type, cache_control)
        except GoogleAPICallError as error:
            raise StorageError("Could not store the file in Cloud Storage.") from error

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=0.5, max=4),
        reraise=True,
    )
    async def _upload_with_retries(
        self, bucket_name: str, path: str, data: bytes, content_type: str, cache_control: str | None
    ) -> None:
        """Upload once in a worker thread; 429/5xx are retried by the decorator."""

        def upload() -> None:
            blob = self._client.bucket(bucket_name).blob(path)
            if cache_control:
                blob.cache_control = cache_control
            blob.upload_from_string(data, content_type=content_type, timeout=UPLOAD_TIMEOUT_SECONDS)

        await asyncio.to_thread(upload)
