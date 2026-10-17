"""Pure parsing of YouTube URLs into video ids."""

import re
from urllib.parse import parse_qs, urlparse

from ishaara.errors import InvalidYouTubeUrlError

_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
_WATCH_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
_SHORT_HOST = "youtu.be"
_PATH_PREFIXES = ("shorts", "embed", "live")


def parse_youtube_video_id(raw_url: str) -> str:
    """Extract the 11-character video id from a YouTube URL or bare id.

    Accepts ``watch?v=``, ``youtu.be/``, ``/shorts/``, ``/embed/``, ``/live/`` URLs on
    youtube.com, www.youtube.com and m.youtube.com, and a bare id. Extra query
    parameters are ignored.

    Args:
        raw_url: User-supplied URL or video id.

    Returns:
        The 11-character YouTube video id.

    Raises:
        InvalidYouTubeUrlError: If no valid video id can be extracted.
    """
    candidate = raw_url.strip()
    if _VIDEO_ID_PATTERN.match(candidate):
        return candidate
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed_url = urlparse(candidate)
    hostname = (parsed_url.hostname or "").lower()
    youtube_video_id = _extract_id(hostname, parsed_url.path, parsed_url.query)
    if youtube_video_id is None or not _VIDEO_ID_PATTERN.match(youtube_video_id):
        raise InvalidYouTubeUrlError("Could not find a valid YouTube video id in the link.")
    return youtube_video_id


def _extract_id(hostname: str, path: str, query: str) -> str | None:
    """Return the raw id segment for a known YouTube host, or ``None``."""
    path_parts = [part for part in path.split("/") if part]
    if hostname == _SHORT_HOST:
        return path_parts[0] if path_parts else None
    if hostname not in _WATCH_HOSTS:
        return None
    if len(path_parts) == 1 and path_parts[0] == "watch":
        return parse_qs(query).get("v", [None])[0]
    if len(path_parts) == 2 and path_parts[0] in _PATH_PREFIXES:
        return path_parts[1]
    return None


def canonical_watch_url(youtube_video_id: str) -> str:
    """Build the canonical watch URL sent to Gemini.

    Args:
        youtube_video_id: An 11-character YouTube video id.

    Returns:
        ``https://www.youtube.com/watch?v={id}``.
    """
    return f"https://www.youtube.com/watch?v={youtube_video_id}"
