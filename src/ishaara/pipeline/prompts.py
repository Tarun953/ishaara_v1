"""Prompt templates sent to Gemini. Bump PIPELINE_VERSION whenever these change."""

from ishaara.models.gemini_schemas import ChunkAnalysis
from ishaara.pipeline.chunking import ClipWindow
from ishaara.pipeline.timestamps import format_timestamp

ANALYSIS_PROMPT = """\
You are helping blind students follow a recorded lecture.
This clip covers {clip_start_mmss} to {clip_end_mmss} of the full video.
Write every timestamp as absolute video time in MM:SS (or HH:MM:SS).

1. Transcribe the speech as segments of one sentence each. Keep Hindi, English
   and Hinglish exactly as spoken.
2. Watch the whole clip and list every VISUAL EVENT: anything that appears, is
   written, drawn, erased, highlighted or changed on screen. This includes board
   writing, slides and slide changes, diagrams, graphs, tables, code, objects and
   demonstrations. Go through the clip second by second so nothing is skipped. Do
   NOT skip something because the teacher does not mention it; those are the most
   important events. Skip only purely decorative marks with no meaning.
3. For each event, describe it so a person who cannot see it understands it:
   - Write math in words: "x squared plus 2x, all over x minus 1".
   - The first time a diagram appears, describe it fully: its shape, labels, and
     where each labelled item sits. Afterwards, describe only what is new.
   - Read out text on slides and boards exactly.
4. For each event, copy the words the teacher says within about five seconds of it
   (words_spoken_nearby), or an empty string if the teacher is silent.
5. Give a confidence from 0 to 1 that you saw and read it correctly.
Return JSON matching the schema.
"""

GAP_JUDGING_PROMPT = """\
You are helping blind students follow a recorded lecture. A blind student hears
only the teacher's speech. Below are the transcript and the visual events for the
clip {clip_start_mmss} to {clip_end_mmss}.

For EVERY event, decide how much of its content a listener gets from the speech
alone. Judge by meaning, not exact words. Teachers often write something and then
read it out, or read it out and then write it, so look at the transcript from
about 20 seconds before to 20 seconds after the event, not only at the words
said nearby.
- fully_spoken: the teacher says the content (reads the equation or text aloud,
  names the labelled parts), so a listener loses nothing.
- partly_spoken: the teacher refers to it but leaves out details a listener needs,
  for example says "this" or "yahan" without saying what it is, or reads only part.
- not_spoken: the speech gives no meaningful description of it.
A fragment of an expression the teacher is spelling out (a single bracket, comma
or symbol that is part of a list or equation they are saying) is not a gap. But
purely visual details a listener cannot infer from words, such as labels, arrows,
tick marks, colours, positions and the shape of a drawing, ARE gaps unless the
teacher names them aloud: saying "A and B are running" does not describe labels
or arrows drawn on a diagram. Use
partly_spoken only when the missing information is something a listener would
actually need to follow the lecture.
Write gap_summary as what the listener would miss, in plain words with math
written out. Use an empty string for fully_spoken.
Return exactly one judgement per event, using the event number.

TRANSCRIPT
{transcript_lines}

VISUAL EVENTS
{event_lines}
"""


def build_analysis_prompt(window: ClipWindow) -> str:
    """Fill the pass 1 (perception) prompt for one clip window.

    Args:
        window: The window being analyzed.

    Returns:
        The prompt text with the window's absolute start and end times.
    """
    return ANALYSIS_PROMPT.format(
        clip_start_mmss=format_timestamp(window.start_seconds),
        clip_end_mmss=format_timestamp(window.end_seconds),
    )


def build_gap_judging_prompt(window: ClipWindow, analysis: ChunkAnalysis) -> str:
    """Fill the pass 2 (gap judging) prompt from a window's pass 1 output.

    Args:
        window: The window that was analyzed.
        analysis: Pass 1 output: transcript segments and visual events.

    Returns:
        The prompt text listing the transcript and the numbered visual events.
    """
    transcript_lines = "\n".join(
        f"[{segment.start_timestamp}-{segment.end_timestamp}] {segment.text}"
        for segment in analysis.transcript_segments
    )
    event_lines = "\n".join(
        f"Event {event_index} [{event.timestamp}] ({event.referent_kind.value}): "
        f"{event.content_description} | "
        f"Teacher said nearby: {event.words_spoken_nearby or '(silent)'}"
        for event_index, event in enumerate(analysis.visual_events)
    )
    return GAP_JUDGING_PROMPT.format(
        clip_start_mmss=format_timestamp(window.start_seconds),
        clip_end_mmss=format_timestamp(window.end_seconds),
        transcript_lines=transcript_lines or "(no speech)",
        event_lines=event_lines or "(no events)",
    )
