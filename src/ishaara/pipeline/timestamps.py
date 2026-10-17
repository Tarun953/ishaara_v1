"""Pure parsing and normalization of Gemini timestamps."""

from collections.abc import Sequence

from ishaara.pipeline.chunking import ClipWindow

SECONDS_PER_MINUTE = 60


def parse_timestamp(raw_timestamp: str) -> float:
    """Parse ``MM:SS`` or ``HH:MM:SS`` into seconds.

    Args:
        raw_timestamp: Timestamp text as returned by Gemini.

    Returns:
        The time in seconds.

    Raises:
        ValueError: If the text is not a valid timestamp.
    """
    parts = raw_timestamp.strip().split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"Not a MM:SS or HH:MM:SS timestamp: {raw_timestamp!r}")
    try:
        numbers = [float(part) for part in parts]
    except ValueError as error:
        raise ValueError(f"Not a MM:SS or HH:MM:SS timestamp: {raw_timestamp!r}") from error
    if any(number < 0 for number in numbers):
        raise ValueError(f"Negative timestamp: {raw_timestamp!r}")
    total_seconds = 0.0
    for number in numbers:
        total_seconds = total_seconds * SECONDS_PER_MINUTE + number
    return total_seconds


def normalize_clip_timestamps(
    timestamp_seconds: Sequence[float], window: ClipWindow
) -> list[float | None]:
    """Convert a window's timestamps to absolute video time and drop strays.

    The prompt asks for absolute time, but models sometimes answer relative to the clip.
    If the window does not start at 0 and every timestamp is below the window length,
    the timestamps are treated as clip-relative and ``window.start_seconds`` is added.
    (A window whose timestamps are all absolute and fall in the first ``length`` seconds
    would be misread, which can only happen when every moment is in that narrow band.)

    Args:
        timestamp_seconds: Timestamps from one window, in seconds.
        window: The window they came from.

    Returns:
        A list aligned with the input: the absolute time, or ``None`` when the time falls
        outside ``[window.start_seconds, window.end_seconds]``.
    """
    looks_clip_relative = (
        window.start_seconds > 0
        and len(timestamp_seconds) > 0
        and all(seconds < window.length_seconds for seconds in timestamp_seconds)
    )
    offset_seconds = window.start_seconds if looks_clip_relative else 0
    normalized: list[float | None] = []
    for seconds in timestamp_seconds:
        absolute_seconds = seconds + offset_seconds
        in_window = window.start_seconds <= absolute_seconds <= window.end_seconds
        normalized.append(absolute_seconds if in_window else None)
    return normalized


def format_timestamp(total_seconds: int) -> str:
    """Format seconds as ``MM:SS``, or ``HH:MM:SS`` from one hour up.

    Args:
        total_seconds: A non-negative number of whole seconds.

    Returns:
        The formatted timestamp.
    """
    hours, remainder = divmod(total_seconds, SECONDS_PER_MINUTE * SECONDS_PER_MINUTE)
    minutes, seconds = divmod(remainder, SECONDS_PER_MINUTE)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"
