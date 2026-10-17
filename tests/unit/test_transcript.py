from ishaara.models.gemini_schemas import ChunkAnalysis, TranscriptSegmentRaw
from ishaara.pipeline.chunking import ClipWindow
from ishaara.pipeline.transcript import assemble_transcript


def _analysis(*sentences: tuple[str, str, str]) -> ChunkAnalysis:
    return ChunkAnalysis(
        transcript_segments=[
            TranscriptSegmentRaw(start_timestamp=a, end_timestamp=b, text=t)
            for a, b, t in sentences
        ],
        visual_events=[],
    )


def test_single_window_keeps_everything_in_order() -> None:
    analysis = _analysis(("00:05", "00:08", "second"), ("00:00", "00:04", "first"))

    segments = assemble_transcript([ClipWindow(0, 600)], [analysis])

    assert [(s.start_seconds, s.text) for s in segments] == [(0.0, "first"), (5.0, "second")]


def test_overlap_is_split_at_the_midpoint_so_sentences_are_not_repeated() -> None:
    windows = [ClipWindow(0, 600), ClipWindow(590, 1190)]
    first = _analysis(
        ("09:30", "09:35", "before"), ("09:52", "09:55", "dup late"), ("09:56", "09:58", "tail")
    )
    second = _analysis(
        ("09:52", "09:55", "dup late"), ("09:56", "09:58", "tail"), ("10:30", "10:35", "after")
    )

    segments = assemble_transcript(windows, [first, second])

    # boundary at (590 + 600) / 2 = 595 s = 09:55
    assert [s.text for s in segments] == ["before", "dup late", "tail", "after"]
    assert [s.start_seconds for s in segments] == [570.0, 592.0, 596.0, 630.0]


def test_clip_relative_timestamps_are_shifted_and_bad_lines_skipped() -> None:
    windows = [ClipWindow(0, 600), ClipWindow(590, 1190)]
    first = _analysis(("00:10", "00:12", "a"))
    second = _analysis(("01:00", "01:05", "relative"), ("oops", "01:09", "bad"))

    segments = assemble_transcript(windows, [first, second])

    assert [(s.start_seconds, s.text) for s in segments] == [(10.0, "a"), (650.0, "relative")]


def test_out_of_window_sentences_are_dropped_and_end_never_precedes_start() -> None:
    analysis = _analysis(("20:00", "20:05", "stray"), ("00:10", "00:05", "backwards"))

    segments = assemble_transcript([ClipWindow(0, 600)], [analysis])

    assert [(s.text, s.start_seconds, s.end_seconds) for s in segments] == [
        ("backwards", 10.0, 10.0)
    ]
