"""YouTube Data API v3 metadata client."""

from typing import Protocol

import httpx
import isodate
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from ishaara.errors import IshaaraError, VideoNotFoundError
from ishaara.models.api_models import YouTubeVideoMetadata

YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
REQUEST_TIMEOUT_SECONDS = 10.0
MAX_ATTEMPTS = 3


class VideoMetadataSource(Protocol):
    """Where video metadata comes from (the YouTube Data API in production)."""

    async def fetch(self, youtube_video_id: str) -> YouTubeVideoMetadata:
        """Return metadata for one video."""
        ...


def _is_retryable(error: BaseException) -> bool:
    """Return True for network errors, 429 and 5xx responses."""
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        return status_code == 429 or status_code >= 500
    return isinstance(error, httpx.TransportError)


class YouTubeMetadataClient:
    """Fetches video details from the YouTube Data API (1 quota unit per call).

    Args:
        api_key: YouTube Data API v3 key.
        http_client: Shared async HTTP client.
    """

    def __init__(self, api_key: str, http_client: httpx.AsyncClient) -> None:
        """Store the API key and HTTP client."""
        self._api_key = api_key
        self._http_client = http_client

    async def fetch(self, youtube_video_id: str) -> YouTubeVideoMetadata:
        """Fetch metadata for one video.

        Args:
            youtube_video_id: An 11-character YouTube video id.

        Returns:
            The video's metadata.

        Raises:
            VideoNotFoundError: If YouTube returns no item for the id.
            IshaaraError: With code ``youtube_unavailable`` if YouTube keeps failing.
        """
        try:
            response_body = await self._get_video_json(youtube_video_id)
        except (httpx.HTTPStatusError, httpx.TransportError) as error:
            raise IshaaraError(
                "youtube_unavailable",
                502,
                "YouTube is unavailable",
                "Could not reach the YouTube Data API. Try again shortly.",
            ) from error
        return _parse_metadata(response_body, youtube_video_id)

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=0.5, max=4),
        reraise=True,
    )
    async def _get_video_json(self, youtube_video_id: str) -> dict[str, object]:
        """Call ``videos.list`` once, raising on any non-2xx status."""
        response = await self._http_client.get(
            YOUTUBE_VIDEOS_URL,
            params={
                "part": "snippet,contentDetails,status",
                "id": youtube_video_id,
                "key": self._api_key,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        response_body: dict[str, object] = response.json()
        return response_body


def _parse_metadata(
    response_body: dict[str, object], youtube_video_id: str
) -> YouTubeVideoMetadata:
    """Convert a ``videos.list`` response into ``YouTubeVideoMetadata``."""
    items = response_body.get("items")
    if not isinstance(items, list) or not items:
        raise VideoNotFoundError(f"No YouTube video found for id {youtube_video_id}.")
    item: dict[str, dict[str, object]] = items[0]
    snippet, content_details, status = item["snippet"], item["contentDetails"], item["status"]
    duration = isodate.parse_duration(str(content_details["duration"]))
    return YouTubeVideoMetadata(
        title=str(snippet["title"]),
        channel_title=str(snippet["channelTitle"]),
        duration_seconds=int(duration.total_seconds()),
        privacy_status=str(status["privacyStatus"]),
        embeddable=bool(status["embeddable"]),
        live_broadcast_content=str(snippet["liveBroadcastContent"]),
    )
