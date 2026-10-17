"""Video submission, caching, idempotency and quotas (submission logic arrives in M3)."""

from ishaara.errors import (
    VideoIsLiveError,
    VideoNotEmbeddableError,
    VideoNotPublicError,
    VideoTooLongError,
)
from ishaara.models.api_models import YouTubeVideoMetadata

SECONDS_PER_MINUTE = 60


def validate_video_for_processing(metadata: YouTubeVideoMetadata, max_video_minutes: int) -> None:
    """Check that a video can be analyzed and embedded.

    Args:
        metadata: Metadata fetched from the YouTube Data API.
        max_video_minutes: Longest accepted video, in minutes.

    Raises:
        VideoNotPublicError: If the video is private or unlisted.
        VideoNotEmbeddableError: If embedding is disabled.
        VideoIsLiveError: If the video is live or upcoming.
        VideoTooLongError: If the video is longer than ``max_video_minutes``.
    """
    if metadata.privacy_status != "public":
        raise VideoNotPublicError("Only public videos can be processed.")
    if not metadata.embeddable:
        raise VideoNotEmbeddableError("The video owner has disabled embedding.")
    if metadata.live_broadcast_content != "none":
        raise VideoIsLiveError("Live and upcoming broadcasts cannot be processed.")
    video_minutes = round(metadata.duration_seconds / SECONDS_PER_MINUTE)
    if metadata.duration_seconds > max_video_minutes * SECONDS_PER_MINUTE:
        raise VideoTooLongError(
            f"This video is {video_minutes} minutes; the limit is {max_video_minutes} minutes."
        )
