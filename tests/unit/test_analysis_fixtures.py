"""Run the saved real Gemini responses through the pure pipeline code (no network)."""

from pathlib import Path

import pytest

from ishaara.models.gemini_schemas import ChunkAnalysis, GapReport
from ishaara.pipeline.chunking import ClipWindow
from ishaara.pipeline.cue_merging import merge_visual_events, to_timed_events

FIXTURES = Path(__file__).parent.parent / "fixtures"
WINDOW = ClipWindow(0, 180)
HINGLISH_LECTURE = "EaupQhQ4dsw"  # everything written is also said aloud
ENGLISH_LECTURE = "v8kBd64_WAc"  # slide, tick marks and redrawn circles are silent


def _load(youtube_video_id: str) -> tuple[ChunkAnalysis, GapReport]:
    analysis = ChunkAnalysis.model_validate_json(
        (FIXTURES / f"{youtube_video_id}_w0.json").read_text(encoding="utf-8")
    )
    report = GapReport.model_validate_json(
        (FIXTURES / f"{youtube_video_id}_w0_gaps.json").read_text(encoding="utf-8")
    )
    return analysis, report


@pytest.mark.parametrize("youtube_video_id", [HINGLISH_LECTURE, ENGLISH_LECTURE])
def test_every_judgement_points_at_a_real_event(youtube_video_id: str) -> None:
    analysis, report = _load(youtube_video_id)

    event_indexes = {judgement.event_index for judgement in report.judgements}

    assert event_indexes == set(range(len(analysis.visual_events)))


@pytest.mark.parametrize("youtube_video_id", [HINGLISH_LECTURE, ENGLISH_LECTURE])
def test_event_times_stay_inside_the_window(youtube_video_id: str) -> None:
    analysis, report = _load(youtube_video_id)

    events = to_timed_events(analysis, report, WINDOW, window_index=0)

    assert len(events) == len(analysis.visual_events)
    assert all(
        WINDOW.start_seconds <= event.start_seconds <= WINDOW.end_seconds for event in events
    )


def test_fully_explained_lecture_makes_no_cues() -> None:
    analysis, report = _load(HINGLISH_LECTURE)

    events = to_timed_events(analysis, report, WINDOW, window_index=0)

    assert merge_visual_events(events, min_confidence=0.5, min_cue_gap_seconds=6) == []


def test_silent_visuals_make_ordered_cues() -> None:
    analysis, report = _load(ENGLISH_LECTURE)

    events = to_timed_events(analysis, report, WINDOW, window_index=0)
    cues = merge_visual_events(events, min_confidence=0.5, min_cue_gap_seconds=6)

    assert 3 <= len(cues) <= 5
    assert [cue.cue_id for cue in cues] == [f"cue-{n:04d}" for n in range(1, len(cues) + 1)]
    assert [cue.start_seconds for cue in cues] == sorted(cue.start_seconds for cue in cues)
    assert all(cue.board_description and cue.gap_summary for cue in cues)
