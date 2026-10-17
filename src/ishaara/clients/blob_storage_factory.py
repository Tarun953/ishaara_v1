"""Chooses the blob storage backend from settings."""

from pathlib import Path

import httpx

from ishaara.clients.blob_storage import BlobStorage
from ishaara.clients.local_blob_storage import LocalBlobStorage
from ishaara.settings import Settings


def build_blob_storage(settings: Settings, http_client: httpx.AsyncClient) -> BlobStorage:
    """Build the blob storage for ``settings.storage_backend``.

    Args:
        settings: Application settings.
        http_client: Shared HTTP client (used by the Supabase backend).

    Returns:
        The configured storage adapter.

    Raises:
        ValueError: If the chosen backend is missing required settings.
    """
    if settings.storage_backend == "gcs":
        if not (settings.audio_bucket and settings.transcript_bucket):
            raise ValueError("STORAGE_BACKEND=gcs needs AUDIO_BUCKET and TRANSCRIPT_BUCKET")
        from ishaara.clients.gcs_blob_storage import GcsBlobStorage, create_storage_client

        client = create_storage_client(project=settings.gcp_project_id or None)
        return GcsBlobStorage(client, settings.audio_bucket, settings.transcript_bucket)
    if settings.storage_backend == "supabase":
        if not (
            settings.supabase_url
            and settings.supabase_service_key
            and settings.audio_bucket
            and settings.transcript_bucket
        ):
            raise ValueError(
                "STORAGE_BACKEND=supabase needs SUPABASE_URL, SUPABASE_SERVICE_KEY, "
                "AUDIO_BUCKET and TRANSCRIPT_BUCKET"
            )
        from ishaara.clients.supabase_blob_storage import SupabaseBlobStorage

        return SupabaseBlobStorage(
            http_client,
            settings.supabase_url,
            settings.supabase_service_key,
            settings.audio_bucket,
            settings.transcript_bucket,
        )
    return LocalBlobStorage(Path(settings.local_storage_dir), settings.public_base_url)
