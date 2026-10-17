"""Pure conversion of judged visual events into merged narration cues."""

from collections.abc import Sequence

from ishaara.models.domain import NarrationCue, SpeechCoverage, TimedVisualEvent
from ishaara.models.gemini_schemas import ChunkAnalysis, GapReport
from ishaara.pipeline.chunking import ClipWindow
from ishaara.pipeline.timestamps import normalize_clip_timestamps, parse_timestamp

DUPLICATE_WINDOW_SECONDS = 3.0


def to_timed_events(
    analysis: ChunkAnalysis, report: GapReport, window: ClipWindow, window_index: int
) -> list[TimedVisualEvent]:
    """Join one window's visual events with their gap judgements and absolute times.

    Events with no judgement, an unparseable timestamp, or a time outside the window are
    dropped.

    Args:
        analysis: Pass 1 output for the window.
        report: Pass 2 judgements for the window's events.
        window: The window that was analyzed.
        window_index: Position of the window, kept to detect overlap duplicates.

    Returns:
        Timed events in the analysis order.
    """
    judgements_by_event = {judgement.event_index: judgement for judgement in report.judgements}
    parsed: list[tuple[float, int]] = []
    for event_index, event in enumerate(analysis.visual_events):
        if event_index not in judgements_by_event:
            continue
        try:
            parsed.append((parse_timestamp(event.timestamp), event_index))
        except ValueError:
            continue
    normalized = normalize_clip_timestamps([seconds for seconds, _ in parsed], window)
    timed_events: list[TimedVisualEvent] = []
    for absolute_seconds, (_, event_index) in zip(normalized, parsed, strict=True):
        if absolute_seconds is None:
            continue
        event = analysis.visual_events[event_index]
        judgement = judgements_by_event[event_index]
        timed_events.append(
            TimedVisualEvent(
                window_index=window_index,
                start_seconds=absolute_seconds,
                referent_kind=event.referent_kind,
                content_description=event.content_description,
                words_spoken_nearby=event.words_spoken_nearby,
                speech_coverage=judgement.speech_coverage,
                gap_summary=judgement.gap_summary,
                confidence=min(event.confidence, judgement.confidence),
            )
        )
    return timed_events


def merge_visual_events(
    events: Sequence[TimedVisualEvent], min_confidence: float, min_cue_gap_seconds: float
) -> list[NarrationCue]:
    """Keep the unexplained events, deduplicate and merge them into cues.

    Rules: keep only events the speech does not fully cover; drop events below
    ``min_confidence``; treat events within 3 seconds with the same kind from different
    windows as duplicates and keep the more confident; merge events less than
    ``min_cue_gap_seconds`` after the previous event into one cue; number cues
    ``cue-0001`` onward in time order.

    Args:
        events: Timed, judged events from all windows, in any order.
        min_confidence: Events with lower confidence are dropped.
        min_cue_gap_seconds: Closer events merge into a single cue.

    Returns:
        Narration cues in time order, without narration text or audio yet.
    """
    unexplained = sorted(
        (
            event
            for event in events
            if event.speech_coverage != SpeechCoverage.FULLY_SPOKEN
            and event.confidence >= min_confidence
        ),
        key=lambda event: event.start_seconds,
    )
    distinct = _drop_overlap_duplicates(unexplained)
    groups = _group_close_events(distinct, min_cue_gap_seconds)
    return [_build_cue(cue_number, group) for cue_number, group in enumerate(groups, start=1)]


def _drop_overlap_duplicates(sorted_events: Sequence[TimedVisualEvent]) -> list[TimedVisualEvent]:
    """Collapse same-kind events from different windows that are within 3 seconds."""
    kept: list[TimedVisualEvent] = []
    for event in sorted_events:
        duplicate_position = _find_duplicate_position(kept, event)
        if duplicate_position is None:
            kept.append(event)
        elif event.confidence > kept[duplicate_position].confidence:
            kept[duplicate_position] = event
    return kept


def _find_duplicate_position(
    kept: Sequence[TimedVisualEvent], event: TimedVisualEvent
) -> int | None:
    """Return the index of an earlier duplicate of ``event`` in ``kept``, if any."""
    for position in range(len(kept) - 1, -1, -1):
        earlier = kept[position]
        if event.start_seconds - earlier.start_seconds > DUPLICATE_WINDOW_SECONDS:
            return None
        if (
            earlier.window_index != event.window_index
            and earlier.referent_kind == event.referent_kind
        ):
            return position
    return None


def _group_close_events(
    sorted_events: Sequence[TimedVisualEvent], min_cue_gap_seconds: float
) -> list[list[TimedVisualEvent]]:
    """Group events whose start is less than the gap after the previous event."""
    groups: list[list[TimedVisualEvent]] = []
    for event in sorted_events:
        previous = groups[-1][-1] if groups else None
        if previous and event.start_seconds - previous.start_seconds < min_cue_gap_seconds:
            groups[-1].append(event)
        else:
            groups.append([event])
    return groups


def _build_cue(cue_number: int, group: Sequence[TimedVisualEvent]) -> NarrationCue:
    """Combine a group of events into one cue."""
    most_confident = max(group, key=lambda event: event.confidence)
    return NarrationCue(
        cue_id=f"cue-{cue_number:04d}",
        start_seconds=group[0].start_seconds,
        spoken_phrase="; ".join(
            event.words_spoken_nearby for event in group if event.words_spoken_nearby
        ),
        referent_kind=most_confident.referent_kind,
        board_description=" ".join(event.content_description for event in group),
        gap_summary=" ".join(event.gap_summary for event in group if event.gap_summary),
        confidence=most_confident.confidence,
    )
