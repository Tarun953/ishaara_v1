"""Pure rendering of accessible lecture notes (Markdown and HTML)."""

import html
import re
from collections.abc import Sequence
from dataclasses import dataclass

from markdown_it import MarkdownIt

from ishaara.models.domain import NarrationCue, NarrationLanguage, TranscriptSegment, VideoRecord
from ishaara.pipeline.timestamps import format_timestamp

SECTION_SECONDS = 300
PARAGRAPH_PAUSE_SECONDS = 4.0
PARAGRAPH_MAX_SEGMENTS = 5

LANGUAGE_NAMES = {NarrationLanguage.ENGLISH: "English", NarrationLanguage.HINDI: "Hindi"}
NO_TRANSCRIPT_NOTE = "*The full transcript is not available for this video.*"
NO_GAPS_NOTE = "*No unexplained visuals were found: the teacher described what appeared on screen.*"

_INLINE_SPECIALS = re.compile(r"([\\`*_\[\]<>])")
_LINE_START_MARKER = re.compile(r"^(\s*)([#>+\-=])")
_LINE_START_NUMBER = re.compile(r"^(\s*)(\d+)([.)])")


@dataclass(frozen=True)
class Paragraph:
    """A run of consecutive transcript sentences.

    Attributes:
        start_seconds: When the first sentence starts.
        text: The sentences joined with spaces.
    """

    start_seconds: float
    text: str


def escape_markdown(text: str) -> str:
    """Escape text so it renders literally instead of as Markdown.

    Args:
        text: Untrusted text such as a YouTube title or a transcript sentence.

    Returns:
        The text with Markdown control characters backslash-escaped.
    """
    flattened = " ".join(text.split())
    escaped = _INLINE_SPECIALS.sub(r"\\\1", flattened)
    escaped = _LINE_START_MARKER.sub(r"\1\\\2", escaped)
    return _LINE_START_NUMBER.sub(r"\1\2\\\3", escaped)


def group_into_paragraphs(segments: Sequence[TranscriptSegment]) -> list[Paragraph]:
    """Join transcript sentences into readable paragraphs.

    A new paragraph starts after a pause of 4 seconds or more, or after 5 sentences.

    Args:
        segments: Transcript sentences in any order.

    Returns:
        Paragraphs in time order.
    """
    paragraphs: list[Paragraph] = []
    current: list[TranscriptSegment] = []
    for segment in sorted(segments, key=lambda item: item.start_seconds):
        if current and (
            len(current) >= PARAGRAPH_MAX_SEGMENTS
            or segment.start_seconds - current[-1].end_seconds >= PARAGRAPH_PAUSE_SECONDS
        ):
            paragraphs.append(_close_paragraph(current))
            current = []
        current.append(segment)
    if current:
        paragraphs.append(_close_paragraph(current))
    return paragraphs


def _close_paragraph(segments: Sequence[TranscriptSegment]) -> Paragraph:
    """Build a paragraph from consecutive sentences."""
    return Paragraph(
        start_seconds=segments[0].start_seconds,
        text=" ".join(segment.text.strip() for segment in segments if segment.text.strip()),
    )


def render_notes_markdown(record: VideoRecord, segments: Sequence[TranscriptSegment]) -> str:
    """Render the notes: transcript paragraphs with each board description inlined.

    Args:
        record: A processed video.
        segments: Its transcript; may be empty.

    Returns:
        Markdown with a title, a summary line and one ``##`` section per 5 minutes.
    """
    language_name = LANGUAGE_NAMES[record.narration_language]
    lines = [
        f"# {escape_markdown(record.title)}",
        "",
        f"{escape_markdown(record.channel_title)} · {format_timestamp(record.duration_seconds)}"
        f" · narrated in {language_name}",
        "",
    ]
    if not segments:
        lines += [NO_TRANSCRIPT_NOTE, ""]
    if not record.cues:
        lines += [NO_GAPS_NOTE, ""]
    items = _timeline_items(record.cues, group_into_paragraphs(segments))
    section_index = None
    for start_seconds, text in items:
        index = int(start_seconds // SECTION_SECONDS)
        if index != section_index:
            section_index = index
            lines += [f"## {_section_label(index, record.duration_seconds)}", ""]
        lines += [text, ""]
    return "\n".join(lines)


def render_notes_html(record: VideoRecord, segments: Sequence[TranscriptSegment]) -> str:
    """Render the notes as a complete, accessible HTML page.

    Raw HTML in any text is escaped, never interpreted.

    Args:
        record: A processed video.
        segments: Its transcript; may be empty.

    Returns:
        A full HTML document with a language, a title and a ``main`` landmark.
    """
    markdown = MarkdownIt("commonmark", {"html": False})
    body = markdown.render(render_notes_markdown(record, segments))
    title = html.escape(f"{record.title} - lecture notes")
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n"
        "</head>\n"
        "<body>\n"
        "<main>\n"
        f"{body}"
        "</main>\n"
        "</body>\n"
        "</html>\n"
    )


def _timeline_items(
    cues: Sequence[NarrationCue], paragraphs: Sequence[Paragraph]
) -> list[tuple[float, str]]:
    """Interleave transcript paragraphs and board descriptions by time."""
    items: list[tuple[float, int, str]] = [
        (paragraph.start_seconds, 0, escape_markdown(paragraph.text)) for paragraph in paragraphs
    ]
    items += [
        (
            cue.start_seconds,
            1,
            f"**[Board, {format_timestamp(int(cue.start_seconds))}]** "
            f"{escape_markdown(cue.board_description)}",
        )
        for cue in cues
    ]
    return [(start, text) for start, _, text in sorted(items, key=lambda item: item[:2])]


def _section_label(section_index: int, duration_seconds: int) -> str:
    """Return a heading such as ``05:00 – 08:47`` for a 5-minute section."""
    start_seconds = section_index * SECTION_SECONDS
    end_seconds = (section_index + 1) * SECTION_SECONDS
    if duration_seconds > start_seconds:
        end_seconds = min(end_seconds, duration_seconds)
    return f"{format_timestamp(start_seconds)} – {format_timestamp(end_seconds)}"
