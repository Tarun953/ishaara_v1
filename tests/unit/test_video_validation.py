import pytest

from ishaara.errors import (
    VideoIsLiveError,
    VideoNotEmbeddableError,
    VideoNotPublicError,
    VideoTooLongError,
)
from ishaara.models.api_models import YouTubeVideoMetadata
from ishaara.services.video_service import validate_video_for_processing


def _metadata(**overrides: object) -> YouTubeVideoMetadata:
    fields: dict[str, object] = {
        "title": "Lecture",
        "channel_title": "Prof",
        "duration_seconds": 30 * 60,
        "privacy_status": "public",
        "embeddable": True,
        "live_broadcast_content": "none",
    }
    fields.update(overrides)
    return YouTubeVideoMetadata.model_validate(fields)


def test_accepts_valid_video() -> None:
    validate_video_for_processing(_metadata(), max_video_minutes=45)


def test_accepts_video_exactly_at_limit() -> None:
    validate_video_for_processing(_metadata(duration_seconds=45 * 60), max_video_minutes=45)


@pytest.mark.parametrize(
    ("overrides", "expected_error"),
    [
        ({"privacy_status": "private"}, VideoNotPublicError),
        ({"privacy_status": "unlisted"}, VideoNotPublicError),
        ({"embeddable": False}, VideoNotEmbeddableError),
        ({"live_broadcast_content": "live"}, VideoIsLiveError),
        ({"live_broadcast_content": "upcoming"}, VideoIsLiveError),
        ({"duration_seconds": 74 * 60}, VideoTooLongError),
    ],
)
def test_rejects_unprocessable_videos(
    overrides: dict[str, object], expected_error: type[Exception]
) -> None:
    with pytest.raises(expected_error):
        validate_video_for_processing(_metadata(**overrides), max_video_minutes=45)


def test_too_long_message_names_both_durations() -> None:
    with pytest.raises(VideoTooLongError) as error_info:
        validate_video_for_processing(_metadata(duration_seconds=74 * 60), max_video_minutes=45)

    assert error_info.value.detail == "This video is 74 minutes; the limit is 45 minutes."
