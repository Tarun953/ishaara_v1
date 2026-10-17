from ishaara.cli import build_parser
from ishaara.models.domain import ReferentKind, SpeechCoverage
from ishaara.models.gemini_schemas import ChunkAnalysis, GapJudgement, GapReport, VisualEvent
from ishaara.pipeline.chunking import ClipWindow
from ishaara.pipeline.orchestrator import analyze_windows, merge_window_results
from tests.conftest import FakeGeminiGateway


def _analysis(*timestamps: str) -> ChunkAnalysis:
    return ChunkAnalysis(
        transcript_segments=[],
        visual_events=[
            VisualEvent(
                timestamp=timestamp,
                referent_kind=ReferentKind.EQUATION,
                content_description="x squared",
                words_spoken_nearby="",
                confidence=0.9,
            )
            for timestamp in timestamps
        ],
    )


def test_parser_reads_flags() -> None:
    args = build_parser().parse_args(
        ["analyze", "dQw4w9WgXcQ", "--language", "hi", "--max-minutes", "5", "--from-fixtures"]
    )

    assert (args.language, args.max_minutes, args.from_fixtures) == ("hi", 5, True)


async def test_both_passes_and_merge_with_fake_gateway() -> None:
    windows = [ClipWindow(0, 600), ClipWindow(590, 1190)]
    gateway = FakeGeminiGateway({0: _analysis("09:55"), 590: _analysis("09:56", "15:00")})

    results = await analyze_windows(gateway, "dQw4w9WgXcQ", windows, concurrency=2)
    cues = merge_window_results(windows, results, min_confidence=0.5, min_cue_gap_seconds=6)

    assert gateway.analyzed_windows == windows
    # the 09:55 / 09:56 overlap duplicate collapses; the 15:00 event stays separate
    assert [cue.start_seconds for cue in cues] == [595.0, 900.0]


async def test_fully_spoken_events_are_filtered_out_of_cues() -> None:
    windows = [ClipWindow(0, 600)]
    report = GapReport(
        judgements=[
            GapJudgement(
                event_index=0,
                speech_coverage=SpeechCoverage.FULLY_SPOKEN,
                gap_summary="",
                confidence=0.9,
            ),
            GapJudgement(
                event_index=1,
                speech_coverage=SpeechCoverage.NOT_SPOKEN,
                gap_summary="A new equation appears.",
                confidence=0.9,
            ),
        ]
    )
    gateway = FakeGeminiGateway({0: _analysis("00:10", "01:00")}, {0: report})

    results = await analyze_windows(gateway, "dQw4w9WgXcQ", windows, concurrency=1)
    cues = merge_window_results(windows, results, min_confidence=0.5, min_cue_gap_seconds=6)

    assert [cue.start_seconds for cue in cues] == [60.0]
    assert cues[0].gap_summary == "A new equation appears."
