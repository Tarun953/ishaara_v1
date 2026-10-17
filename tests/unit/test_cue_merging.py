from ishaara.models.domain import ReferentKind, SpeechCoverage, TimedVisualEvent
from ishaara.models.gemini_schemas import ChunkAnalysis, GapJudgement, GapReport, VisualEvent
from ishaara.pipeline.chunking import ClipWindow
from ishaara.pipeline.cue_merging import merge_visual_events, to_timed_events

NOT_SPOKEN = SpeechCoverage.NOT_SPOKEN


def _event(
    start_seconds: float,
    confidence: float = 0.9,
    kind: ReferentKind = ReferentKind.EQUATION,
    window_index: int = 0,
    coverage: SpeechCoverage = NOT_SPOKEN,
    said: str = "this",
    description: str = "x squared",
    gap: str = "x squared appears",
) -> TimedVisualEvent:
    return TimedVisualEvent(
        window_index=window_index,
        start_seconds=start_seconds,
        referent_kind=kind,
        content_description=description,
        words_spoken_nearby=said,
        speech_coverage=coverage,
        gap_summary=gap,
        confidence=confidence,
    )


def _merge(events: list[TimedVisualEvent]) -> list:  # type: ignore[type-arg]
    return merge_visual_events(events, min_confidence=0.5, min_cue_gap_seconds=6)


def test_fully_spoken_events_make_no_cue() -> None:
    assert _merge([_event(10, coverage=SpeechCoverage.FULLY_SPOKEN)]) == []


def test_partly_and_not_spoken_events_make_cues() -> None:
    cues = _merge(
        [_event(10, coverage=SpeechCoverage.PARTLY_SPOKEN), _event(100, coverage=NOT_SPOKEN)]
    )

    assert [cue.start_seconds for cue in cues] == [10, 100]


def test_low_confidence_events_are_dropped() -> None:
    assert _merge([_event(10, confidence=0.49)]) == []
    assert len(_merge([_event(10, confidence=0.5)])) == 1


def test_overlap_duplicates_keep_higher_confidence() -> None:
    cues = _merge([_event(100, 0.6, window_index=0), _event(102, 0.9, window_index=1)])

    assert len(cues) == 1
    assert cues[0].confidence == 0.9
    assert cues[0].start_seconds == 102


def test_same_window_close_events_are_merged_with_joined_text() -> None:
    cues = _merge(
        [
            _event(100, said="this", description="a", gap="gap a"),
            _event(102, said="that", description="b", gap="gap b"),
        ]
    )

    assert len(cues) == 1
    assert cues[0].spoken_phrase == "this; that"
    assert cues[0].board_description == "a b"
    assert cues[0].gap_summary == "gap a gap b"


def test_silent_events_leave_spoken_phrase_empty() -> None:
    cues = _merge([_event(100, said="")])

    assert cues[0].spoken_phrase == ""


def test_events_six_seconds_apart_stay_separate() -> None:
    cues = _merge([_event(100), _event(106)])

    assert [cue.start_seconds for cue in cues] == [100, 106]


def test_cue_ids_follow_time_order() -> None:
    cues = _merge([_event(300), _event(10), _event(100)])

    assert [(cue.cue_id, cue.start_seconds) for cue in cues] == [
        ("cue-0001", 10),
        ("cue-0002", 100),
        ("cue-0003", 300),
    ]


def _raw_event(timestamp: str) -> VisualEvent:
    return VisualEvent(
        timestamp=timestamp,
        referent_kind=ReferentKind.TEXT,
        content_description="a heading",
        words_spoken_nearby="",
        confidence=0.8,
    )


def _judgement(event_index: int) -> GapJudgement:
    return GapJudgement(
        event_index=event_index, speech_coverage=NOT_SPOKEN, gap_summary="gap", confidence=0.7
    )


def test_to_timed_events_normalizes_and_skips_unusable_events() -> None:
    analysis = ChunkAnalysis(
        transcript_segments=[],
        visual_events=[
            _raw_event("00:30"),
            _raw_event("oops"),
            _raw_event("05:00"),
            _raw_event("06:00"),
        ],
    )
    report = GapReport(judgements=[_judgement(0), _judgement(1), _judgement(2)])  # none for 3

    timed = to_timed_events(analysis, report, ClipWindow(600, 1200), window_index=1)

    assert [event.start_seconds for event in timed] == [630.0, 900.0]
    assert all(event.window_index == 1 for event in timed)
    assert timed[0].confidence == 0.7  # min of event (0.8) and judgement (0.7)
