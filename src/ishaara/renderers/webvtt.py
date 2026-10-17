"""Pure rendering of the WebVTT description track for a processed video."""

import html

from ishaara.models.domain import NarrationCue, VideoRecord

SECONDS_PER_WORD = 0.4
MIN_ESTIMATED_SECONDS = 2.0
MIN_CUE_SECONDS = 1.0
MILLISECONDS_PER_SECOND = 1000


def format_vtt_timestamp(seconds: float) -> str:
    """Format seconds as ``HH:MM:SS.mmm``.

    Args:
        seconds: A non-negative time in seconds.

    Returns:
        The WebVTT timestamp.
    """
    total_milliseconds = round(seconds * MILLISECONDS_PER_SECOND)
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, MILLISECONDS_PER_SECOND)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


def cue_duration_seconds(cue: NarrationCue) -> float:
    """Return how long a cue's description takes to speak.

    Uses the real audio length when there is audio, otherwise an estimate from the word
    count.

    Args:
        cue: A narrated cue.

    Returns:
        Duration in seconds.
    """
    if cue.audio_duration_seconds:
        return cue.audio_duration_seconds
    return max(MIN_ESTIMATED_SECONDS, len(cue.narration_text.split()) * SECONDS_PER_WORD)


def render_description_track(record: VideoRecord) -> str:
    """Render the video's narration as a WebVTT descriptions track.

    One cue per narration line, starting at the cue's time and lasting as long as the
    description takes to speak, but never running into the next cue.

    Args:
        record: A processed video.

    Returns:
        The WebVTT file as text.
    """
    cues = sorted(
        (cue for cue in record.cues if cue.narration_text.strip()),
        key=lambda cue: cue.start_seconds,
    )
    lines = [
        "WEBVTT",
        "Kind: descriptions",
        f"Language: {record.narration_language.value}",
        "",
    ]
    for index, cue in enumerate(cues):
        end_seconds = cue.start_seconds + cue_duration_seconds(cue)
        if index + 1 < len(cues):
            end_seconds = min(end_seconds, cues[index + 1].start_seconds)
        end_seconds = max(end_seconds, cue.start_seconds + MIN_CUE_SECONDS)
        lines.append(cue.cue_id)
        lines.append(
            f"{format_vtt_timestamp(cue.start_seconds)} --> {format_vtt_timestamp(end_seconds)}"
        )
        lines.append(html.escape(" ".join(cue.narration_text.split()), quote=False))
        lines.append("")
    return "\n".join(lines)
