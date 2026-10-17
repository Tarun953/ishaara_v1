import os
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ishaara.models.domain import (
    NarrationCue,
    NarrationLanguage,
    ReferentKind,
    TranscriptSegment,
    VideoRecord,
    VideoStatus,
)
from ishaara.renderers.notes import (
    escape_markdown,
    group_into_paragraphs,
    render_notes_html,
    render_notes_markdown,
)
from ishaara.renderers.webvtt import (
    cue_duration_seconds,
    format_vtt_timestamp,
    render_description_track,
)

GOLDEN = Path(__file__).parent.parent / "fixtures" / "golden"
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def _cue(
    number: int,
    start_seconds: float,
    narration: str = "On the board: something appears.",
    audio_seconds: float | None = 5.0,
    description: str = "Something appears on the board.",
) -> NarrationCue:
    return NarrationCue(
        cue_id=f"cue-{number:04d}",
        start_seconds=start_seconds,
        spoken_phrase="",
        referent_kind=ReferentKind.DIAGRAM,
        board_description=description,
        gap_summary="A listener would miss this.",
        narration_text=narration,
        audio_url="https://audio.test/x.wav" if audio_seconds else None,
        audio_duration_seconds=audio_seconds,
        confidence=0.9,
    )


def _record(
    cues: list[NarrationCue],
    title: str = "Circular Tracks for CAT",
    language: NarrationLanguage = NarrationLanguage.ENGLISH,
    duration_seconds: int = 527,
) -> VideoRecord:
    return VideoRecord(
        video_key="v8kBd64_WAc_en_v2",
        youtube_video_id="v8kBd64_WAc",
        narration_language=language,
        pipeline_version=2,
        title=title,
        channel_title="Sayali Ma'am",
        duration_seconds=duration_seconds,
        status=VideoStatus.READY,
        cues=cues,
        created_by_uid="u",
        created_at=NOW,
        updated_at=NOW,
    )


def _segment(start: float, end: float, text: str) -> TranscriptSegment:
    return TranscriptSegment(start_seconds=start, end_seconds=end, text=text)


SAMPLE_CUES = [
    _cue(
        1,
        23.0,
        "On the board: a tick mark at the bottom, labelled A and B.",
        10.48,
        "A starting tick mark at the bottom of the circle with labels A and B and arrows.",
    ),
    _cue(2, 162.0, "On the board: the notes are cleared.", None, "The board is cleared."),
    _cue(3, 3725.0, "On the board: a new graph.", 4.2, "A new graph is drawn."),
]
SAMPLE_SEGMENTS = [
    _segment(0, 4, "Now what is a circular track?"),
    _segment(4, 9, "Instead of running in a straight line, people run in circles."),
    _segment(20, 26, "A and B are running in the same direction."),
    _segment(310, 316, "So there is a formula over here."),
    _segment(318, 322, "It is based on relative velocity."),
]


def _golden(name: str, actual: str) -> None:
    path = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(actual, encoding="utf-8")
    assert actual == path.read_text(encoding="utf-8")


# ---------------- WebVTT ----------------


def test_vtt_golden_file() -> None:
    _golden("cues.vtt", render_description_track(_record(SAMPLE_CUES, duration_seconds=4000)))


def test_vtt_timestamps_include_hours_and_milliseconds() -> None:
    assert format_vtt_timestamp(0) == "00:00:00.000"
    assert format_vtt_timestamp(23.0) == "00:00:23.000"
    assert format_vtt_timestamp(3725.5) == "01:02:05.500"


def test_vtt_header_names_kind_and_language() -> None:
    vtt = render_description_track(_record([], language=NarrationLanguage.HINDI))

    assert vtt.splitlines()[:3] == ["WEBVTT", "Kind: descriptions", "Language: hi"]


def test_vtt_cue_never_runs_into_the_next_cue() -> None:
    vtt = render_description_track(_record([_cue(1, 10.0, audio_seconds=30.0), _cue(2, 20.0)]))

    assert "00:00:10.000 --> 00:00:20.000" in vtt


def test_vtt_estimates_duration_when_there_is_no_audio() -> None:
    five_words = _cue(1, 0.0, "one two three four five", None)
    thirty_words = _cue(2, 0.0, " ".join(["word"] * 30), None)

    assert cue_duration_seconds(five_words) == 2.0
    assert cue_duration_seconds(thirty_words) == pytest.approx(12.0)


def test_vtt_escapes_markup_and_flattens_blank_lines() -> None:
    cue = _cue(1, 1.0, "x < 2 & y > 1\n\nnext --> line")

    vtt = render_description_track(_record([cue]))

    assert "x &lt; 2 &amp; y &gt; 1 next --&gt; line" in vtt


def test_vtt_skips_cues_without_narration_and_sorts_by_time() -> None:
    vtt = render_description_track(_record([_cue(2, 50.0), _cue(1, 5.0, narration="  ")]))

    assert "cue-0001" not in vtt and "cue-0002" in vtt


TIMING = re.compile(r"^(\d{2,}):(\d{2}):(\d{2})\.(\d{3}) --> (\d{2,}):(\d{2}):(\d{2})\.(\d{3})$")


def _seconds(parts: tuple[str, ...]) -> float:
    hours, minutes, seconds, milliseconds = (int(part) for part in parts)
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def test_vtt_output_satisfies_the_webvtt_grammar() -> None:
    vtt = render_description_track(_record(SAMPLE_CUES, duration_seconds=4000))

    header, *blocks = vtt.rstrip("\n").split("\n\n")
    assert header.splitlines()[0] == "WEBVTT"
    assert all("-->" not in line for line in header.splitlines())
    previous_start = -1.0
    for block in blocks:
        identifier, timing, *text_lines = block.split("\n")
        assert "-->" not in identifier and identifier
        match = TIMING.match(timing)
        assert match, timing
        start, end = _seconds(match.groups()[:4]), _seconds(match.groups()[4:])
        assert end > start >= previous_start
        previous_start = start
        assert text_lines and all(line.strip() for line in text_lines)
        assert all("-->" not in line and "<" not in line for line in text_lines)
        assert not re.search(r"&(?!amp;|lt;|gt;)", "\n".join(text_lines))


# ---------------- notes ----------------


def test_markdown_golden_file() -> None:
    _golden(
        "notes.md",
        render_notes_markdown(_record(SAMPLE_CUES, duration_seconds=4000), SAMPLE_SEGMENTS),
    )


def test_html_golden_file() -> None:
    _golden(
        "notes.html",
        render_notes_html(_record(SAMPLE_CUES, duration_seconds=4000), SAMPLE_SEGMENTS),
    )


def test_sections_cover_five_minutes_and_cues_sit_at_their_time() -> None:
    markdown = render_notes_markdown(_record(SAMPLE_CUES[:2]), SAMPLE_SEGMENTS)

    headings = [line for line in markdown.splitlines() if line.startswith("## ")]
    assert headings == ["## 00:00 – 05:00", "## 05:00 – 08:47"]
    assert markdown.index("**[Board, 00:23]**") > markdown.index("Now what is a circular track?")
    assert markdown.index("**[Board, 02:42]**") < markdown.index("## 05:00")


def test_hour_long_timestamps_are_labelled_with_hours() -> None:
    markdown = render_notes_markdown(_record(SAMPLE_CUES, duration_seconds=4000), [])

    assert "**[Board, 01:02:05]**" in markdown


def test_missing_transcript_and_missing_cues_are_stated_not_silent() -> None:
    markdown = render_notes_markdown(_record([]), [])

    assert "The full transcript is not available" in markdown
    assert "No unexplained visuals were found" in markdown


def test_paragraphs_split_on_pauses_and_length() -> None:
    steady = [_segment(i * 2, i * 2 + 2, f"s{i}") for i in range(6)]
    paused = [_segment(0, 2, "a"), _segment(2, 4, "b"), _segment(10, 12, "c")]

    assert [p.text for p in group_into_paragraphs(steady)] == ["s0 s1 s2 s3 s4", "s5"]
    assert [p.text for p in group_into_paragraphs(paused)] == ["a b", "c"]


HOSTILE = "# Pwn *me* [x](javascript:alert(1)) <script>alert(1)</script> `code` _u_"


def test_hostile_text_is_rendered_literally_never_as_markup() -> None:
    record = _record([_cue(1, 5.0, description=HOSTILE)], title=HOSTILE, duration_seconds=100)

    page = render_notes_html(record, [_segment(0, 3, HOSTILE)])

    assert "<script" not in page.lower()
    assert "<a " not in page and "<em>" not in page and "<code>" not in page
    assert page.count("<h1>") == 1
    assert "javascript:alert(1)" in page  # present only as visible text


def test_escape_markdown_protects_line_starts() -> None:
    assert escape_markdown("# not a heading") == "\\# not a heading"
    assert escape_markdown("1. not a list") == "1\\. not a list"
    assert escape_markdown("a\n\nb") == "a b"


def test_html_page_is_structurally_accessible() -> None:
    page = render_notes_html(_record(SAMPLE_CUES, duration_seconds=4000), SAMPLE_SEGMENTS)

    assert page.startswith("<!doctype html>")
    assert '<html lang="en">' in page
    assert re.search(r"<title>[^<]+</title>", page)
    assert page.count("<main>") == 1 and page.count("<h1>") == 1
    assert "<h3" not in page and "<h2>" in page  # no skipped heading level
