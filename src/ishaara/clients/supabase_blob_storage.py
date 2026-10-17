"""Blob storage on Supabase Storage (REST API, service-role key).

The audio bucket must be public; the transcript bucket must be private.
"""

from urllib.parse import quote

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ishaara.clients.blob_storage import validate_blob_path
from ishaara.errors import StorageError

UPLOAD_TIMEOUT_SECONDS = 30.0
MAX_ATTEMPTS = 3


def _is_retryable(error: BaseException) -> bool:
    """Return True for network errors, 429 and 5xx responses."""
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        return status_code == 429 or status_code >= 500
    return isinstance(error, httpx.TransportError)


class SupabaseBlobStorage:
    """``BlobStorage`` backed by two Supabase Storage buckets.

    Args:
        http_client: Shared async HTTP client.
        supabase_url: Project URL, e.g. ``https://<ref>.supabase.co``.
        service_key: Service-role key (server side only; never sent to browsers).
        audio_bucket: Public bucket for narration audio.
        transcript_bucket: Private bucket for transcript JSON.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        supabase_url: str,
        service_key: str,
        audio_bucket: str,
        transcript_bucket: str,
    ) -> None:
        """Store connection details."""
        self._http_client = http_client
        self._base_url = supabase_url.rstrip("/")
        self._service_key = service_key
        self._audio_bucket = audio_bucket
        self._transcript_bucket = transcript_bucket

    async def upload_public(
        self, path: str, data: bytes, content_type: str, cache_control: str
    ) -> str:
        """Upload to the audio bucket and return its public URL."""
        await self._upload(self._audio_bucket, path, data, content_type, cache_control)
        return f"{self._base_url}/storage/v1/object/public/{self._audio_bucket}/{quote(path)}"

    async def upload_private(self, path: str, data: bytes, content_type: str) -> str:
        """Upload to the transcript bucket and return the blob path."""
        await self._upload(self._transcript_bucket, path, data, content_type, None)
        return path

    async def download_private(self, path: str) -> bytes:
        """Download a file from the transcript bucket."""
        validate_blob_path(path)
        try:
            return await self._get_with_retries(self._transcript_bucket, path)
        except (httpx.HTTPStatusError, httpx.TransportError) as error:
            raise StorageError("Could not read the file from Supabase Storage.") from error

    async def _upload(
        self, bucket: str, path: str, data: bytes, content_type: str, cache_control: str | None
    ) -> None:
        """Upload one object, translating failures into ``StorageError``."""
        validate_blob_path(path)
        try:
            await self._post_with_retries(bucket, path, data, content_type, cache_control)
        except (httpx.HTTPStatusError, httpx.TransportError) as error:
            raise StorageError("Could not store the file in Supabase Storage.") from error

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=0.5, max=4),
        reraise=True,
    )
    async def _post_with_retries(
        self, bucket: str, path: str, data: bytes, content_type: str, cache_control: str | None
    ) -> None:
        """POST the object (overwriting any existing one); 429/5xx are retried."""
        headers = {
            "Authorization": f"Bearer {self._service_key}",
            "apikey": self._service_key,
            "Content-Type": content_type,
            "x-upsert": "true",
        }
        if cache_control:
            headers["Cache-Control"] = cache_control
        response = await self._http_client.post(
            f"{self._base_url}/storage/v1/object/{bucket}/{quote(path)}",
            content=data,
            headers=headers,
            timeout=UPLOAD_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=0.5, max=4),
        reraise=True,
    )
    async def _get_with_retries(self, bucket: str, path: str) -> bytes:
        """GET the object with the service key; 429/5xx are retried."""
        response = await self._http_client.get(
            f"{self._base_url}/storage/v1/object/{bucket}/{quote(path)}",
            headers={
                "Authorization": f"Bearer {self._service_key}",
                "apikey": self._service_key,
            },
            timeout=UPLOAD_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.content
