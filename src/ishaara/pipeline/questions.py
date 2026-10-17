"""Pure helpers for answering a student's question about a lecture."""

from collections.abc import Sequence

from ishaara.models.domain import NarrationCue, TranscriptSegment
from ishaara.pipeline.chunking import ClipWindow
from ishaara.pipeline.timestamps import format_timestamp, parse_timestamp

CONTEXT_RADIUS_SECONDS = 120.0
VISUAL_CLIP_RADIUS_SECONDS = 15
MAX_TRANSCRIPT_CHARACTERS = 40_000
NOTHING_HERE = "(none in this part of the lecture)"
TRUNCATED_MARKER = "[transcript truncated]"


def build_question_context(
    cues: Sequence[NarrationCue],
    segments: Sequence[TranscriptSegment],
    at_seconds: float | None,
) -> str:
    """Build the lecture material shown to Gemini when answering a question.

    With a time, only transcript sentences and cues within 120 seconds either side are
    included. Without one, every cue and the transcript (capped at 40,000 characters) are.

    Args:
        cues: The video's cues.
        segments: The transcript sentences; may be empty.
        at_seconds: Where the student is in the video, if known.

    Returns:
        Text with a transcript section and an on-screen items section.
    """
    ordered_segments = sorted(segments, key=lambda segment: segment.start_seconds)
    ordered_cues = sorted(cues, key=lambda cue: cue.start_seconds)
    if at_seconds is not None:
        low, high = at_seconds - CONTEXT_RADIUS_SECONDS, at_seconds + CONTEXT_RADIUS_SECONDS
        ordered_segments = [s for s in ordered_segments if low <= s.start_seconds <= high]
        ordered_cues = [c for c in ordered_cues if low <= c.start_seconds <= high]
    transcript_lines = [
        f"[{format_timestamp(int(segment.start_seconds))}] {segment.text.strip()}"
        for segment in ordered_segments
    ]
    cue_lines = [
        f"[{format_timestamp(int(cue.start_seconds))}] {cue.board_description}"
        for cue in ordered_cues
    ]
    return (
        "TRANSCRIPT\n"
        + "\n".join(_cap_lines(transcript_lines) or [NOTHING_HERE])
        + "\n\nON-SCREEN ITEMS THE TEACHER DID NOT FULLY EXPLAIN\n"
        + "\n".join(cue_lines or [NOTHING_HERE])
    )


def _cap_lines(lines: Sequence[str]) -> list[str]:
    """Keep whole lines up to the character cap, marking where the text was cut."""
    kept: list[str] = []
    total = 0
    for line in lines:
        total += len(line) + 1
        if total > MAX_TRANSCRIPT_CHARACTERS:
            kept.append(TRUNCATED_MARKER)
            break
        kept.append(line)
    return kept


def visual_clip_window(at_seconds: float, duration_seconds: int) -> ClipWindow:
    """Return the 30-second clip around the student's position, kept inside the video.

    Args:
        at_seconds: Where the student is, between 0 and ``duration_seconds``.
        duration_seconds: Video length.

    Returns:
        A window of ``[at - 15, at + 15]`` clamped to ``[0, duration_seconds]``.
    """
    start_seconds = max(0, int(at_seconds) - VISUAL_CLIP_RADIUS_SECONDS)
    end_seconds = min(duration_seconds, int(at_seconds) + VISUAL_CLIP_RADIUS_SECONDS)
    if end_seconds <= start_seconds:
        end_seconds = start_seconds + 1
    return ClipWindow(start_seconds, end_seconds)


def parse_referenced_seconds(timestamps: Sequence[str], duration_seconds: int) -> list[float]:
    """Convert Gemini's ``MM:SS`` strings to sorted, unique seconds inside the video.

    Args:
        timestamps: Times as returned by Gemini.
        duration_seconds: Video length; later times are dropped.

    Returns:
        Valid times in ascending order.
    """
    seconds: set[float] = set()
    for timestamp in timestamps:
        try:
            parsed = parse_timestamp(timestamp)
        except ValueError:
            continue
        if 0 <= parsed <= duration_seconds:
            seconds.add(parsed)
    return sorted(seconds)
