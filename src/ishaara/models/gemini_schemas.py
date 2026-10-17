"""Pydantic schemas passed to Gemini as response formats.

Every field carries a description because Gemini reads descriptions as instructions.
"""

from pydantic import BaseModel, Field

from ishaara.models.domain import NarrationLanguage, ReferentKind, SpeechCoverage


class TranscriptSegmentRaw(BaseModel):
    """One transcribed sentence with timestamp strings, converted to seconds later."""

    start_timestamp: str = Field(description="MM:SS (or HH:MM:SS) where the sentence starts.")
    end_timestamp: str = Field(description="MM:SS (or HH:MM:SS) where the sentence ends.")
    text: str = Field(description="The sentence exactly as spoken, in its original language.")


class VisualEvent(BaseModel):
    """Something that appears or changes on screen."""

    timestamp: str = Field(description="MM:SS when the item first appears or changes.")
    referent_kind: ReferentKind
    content_description: str = Field(
        description=(
            "Full description so a person who cannot see it understands it. Math written "
            "out in words. For diagrams: shape, labels and where each item sits."
        )
    )
    words_spoken_nearby: str = Field(
        description=(
            "The words the teacher says within about five seconds of the event, copied as "
            "spoken. Empty string if the teacher is silent."
        )
    )
    confidence: float = Field(ge=0, le=1, description="0 to 1 that you saw and read it correctly.")


class ChunkAnalysis(BaseModel):
    """Pass 1: Gemini's perception of one clip window (no judging)."""

    transcript_segments: list[TranscriptSegmentRaw]
    visual_events: list[VisualEvent]


class GapJudgement(BaseModel):
    """Pass 2: whether one visual event is explained by the speech."""

    event_index: int = Field(description="The event number from the input list.")
    speech_coverage: SpeechCoverage
    gap_summary: str = Field(
        description=(
            "What a listener who cannot see the screen would miss, in plain words with math "
            "written out. Empty string when the speech fully covers the event."
        )
    )
    confidence: float = Field(ge=0, le=1, description="0 to 1 that this judgement is right.")


class GapReport(BaseModel):
    """Pass 2 output: one judgement per visual event."""

    judgements: list[GapJudgement]


class NarrationLine(BaseModel):
    """The spoken description for one cue."""

    cue_id: str = Field(description="The cue id from the input, copied exactly.")
    narration_text: str = Field(
        description="Spoken description, at most 30 words, in the target language."
    )


class NarrationBatch(BaseModel):
    """Narration output: exactly one line per input cue."""

    lines: list[NarrationLine]


class QuestionAnswer(BaseModel):
    """Gemini's answer to a student's question about the lecture."""

    answer_text: str = Field(
        description="The answer: at most 80 words, plain spoken language, math written in words."
    )
    referenced_timestamps: list[str] = Field(
        description="Lecture times the answer relies on, as MM:SS (or HH:MM:SS)."
    )
    needs_visual_check: bool = Field(
        description="True if the lecture material is not enough and the video must be looked at."
    )
    answer_language: NarrationLanguage = Field(
        description="Language the answer is written in: 'en' for English or 'hi' for Hindi."
    )
