"""Domain models and enums shared across the pipeline (VideoRecord arrives in M3)."""

from enum import StrEnum

from pydantic import BaseModel


class VideoStatus(StrEnum):
    """Lifecycle state of a processed video."""

    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class PipelineStage(StrEnum):
    """Stage of the processing pipeline, reported while a video is processing."""

    RESOLVING = "resolving"
    ANALYZING = "analyzing"
    WRITING = "writing"
    VOICING = "voicing"
    SAVING = "saving"


class NarrationLanguage(StrEnum):
    """Language the narration is spoken in."""

    ENGLISH = "en"
    HINDI = "hi"


class ReferentKind(StrEnum):
    """What a pointing word refers to on screen."""

    EQUATION = "equation"
    DIAGRAM = "diagram"
    GRAPH = "graph"
    CODE = "code"
    TABLE = "table"
    TEXT = "text"
    PHYSICAL_OBJECT = "physical_object"


class TranscriptSegment(BaseModel):
    """One transcribed sentence with absolute video times."""

    start_seconds: float
    end_seconds: float
    text: str


class SpeechCoverage(StrEnum):
    """How much of a visual event a listener gets from the teacher's speech alone."""

    FULLY_SPOKEN = "fully_spoken"
    PARTLY_SPOKEN = "partly_spoken"
    NOT_SPOKEN = "not_spoken"


class TimedVisualEvent(BaseModel):
    """A judged visual event with an absolute time, before events are merged into cues.

    Attributes:
        window_index: Index of the clip window the event came from; used to spot
            duplicates produced by overlapping windows.
    """

    window_index: int
    start_seconds: float
    referent_kind: ReferentKind
    content_description: str
    words_spoken_nearby: str
    speech_coverage: SpeechCoverage
    gap_summary: str
    confidence: float


class NarrationCue(BaseModel):
    """One pause point in the video with its description and audio."""

    cue_id: str
    start_seconds: float
    spoken_phrase: str  # words the teacher said around the event
    referent_kind: ReferentKind
    board_description: str  # full description of what is on screen
    gap_summary: str = ""  # what a listener would miss without seeing it
    narration_text: str = ""  # filled in by the narration stage (M4)
    audio_url: str | None = None
    audio_duration_seconds: float | None = None
    confidence: float
