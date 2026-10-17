import pytest

from ishaara.errors import InvalidYouTubeUrlError
from ishaara.pipeline.youtube_url import canonical_watch_url, parse_youtube_video_id

VIDEO_ID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "raw_url",
    [
        f"https://www.youtube.com/watch?v={VIDEO_ID}",
        f"https://m.youtube.com/watch?v={VIDEO_ID}&t=42s&list=PL123",
        f"https://youtu.be/{VIDEO_ID}?si=abc",
        f"https://www.youtube.com/shorts/{VIDEO_ID}",
        f"https://www.youtube.com/embed/{VIDEO_ID}",
        f"https://www.youtube.com/live/{VIDEO_ID}?feature=share",
        VIDEO_ID,
    ],
)
def test_parses_all_supported_forms(raw_url: str) -> None:
    assert parse_youtube_video_id(raw_url) == VIDEO_ID


@pytest.mark.parametrize(
    "raw_url",
    [
        "",
        f"https://evil.com/watch?v={VIDEO_ID}",
        "https://www.youtube.com/watch?v=tooshort",
        "https://www.youtube.com/watch",
        "not a url at all",
    ],
)
def test_rejects_invalid_inputs(raw_url: str) -> None:
    with pytest.raises(InvalidYouTubeUrlError):
        parse_youtube_video_id(raw_url)


def test_canonical_watch_url() -> None:
    assert canonical_watch_url(VIDEO_ID) == f"https://www.youtube.com/watch?v={VIDEO_ID}"
