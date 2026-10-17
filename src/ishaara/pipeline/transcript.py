"""Pure assembly of the full transcript from per-window transcripts."""

import math
from collections.abc import Sequence

from ishaara.models.domain import TranscriptSegment
from ishaara.models.gemini_schemas import ChunkAnalysis
from ishaara.pipeline.chunking import ClipWindow
from ishaara.pipeline.timestamps import clip_relative_offset_seconds, parse_timestamp


def assemble_transcript(
    windows: Sequence[ClipWindow], analyses: Sequence[ChunkAnalysis]
) -> list[TranscriptSegment]:
    """Merge every window's sentences into one absolute-time transcript.

    Each overlap between neighbouring windows is split at its midpoint, so a sentence heard
    in both windows appears once. Sentences with unreadable timestamps are skipped.

    Args:
        windows: The analyzed windows, in time order.
        analyses: Their pass 1 outputs, aligned with ``windows``.

    Returns:
        Segments in time order.
    """
    segments: list[TranscriptSegment] = []
    last_index = len(windows) - 1
    for index, (window, analysis) in enumerate(zip(windows, analyses, strict=True)):
        lower = (
            -math.inf if index == 0 else (window.start_seconds + windows[index - 1].end_seconds) / 2
        )
        upper = (
            math.inf
            if index == last_index
            else (windows[index + 1].start_seconds + window.end_seconds) / 2
        )
        segments.extend(_window_segments(window, analysis, lower, upper))
    return sorted(segments, key=lambda segment: segment.start_seconds)


def _window_segments(
    window: ClipWindow, analysis: ChunkAnalysis, lower: float, upper: float
) -> list[TranscriptSegment]:
    """Return one window's segments in absolute time that start inside ``[lower, upper)``."""
    parsed: list[tuple[float, float, str]] = []
    for segment in analysis.transcript_segments:
        try:
            parsed.append(
                (
                    parse_timestamp(segment.start_timestamp),
                    parse_timestamp(segment.end_timestamp),
                    segment.text,
                )
            )
        except ValueError:
            continue
    offset = clip_relative_offset_seconds([start for start, _, _ in parsed], window)
    kept: list[TranscriptSegment] = []
    for start, end, text in parsed:
        absolute_start, absolute_end = start + offset, end + offset
        if not window.start_seconds <= absolute_start <= window.end_seconds:
            continue
        if lower <= absolute_start < upper:
            kept.append(
                TranscriptSegment(
                    start_seconds=absolute_start,
                    end_seconds=max(absolute_end, absolute_start),
                    text=text,
                )
            )
    return kept
