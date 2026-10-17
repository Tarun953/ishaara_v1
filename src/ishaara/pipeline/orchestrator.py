"""The video processing pipeline: resolve, analyze, merge, narrate, voice and save."""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

import structlog

from ishaara.clients.blob_storage import BlobStorage
from ishaara.clients.gemini_gateway import AnalyzeClipResult, GeminiGateway, JudgeGapsResult
from ishaara.clients.repositories import VideoRepository
from ishaara.clients.youtube_metadata import VideoMetadataSource
from ishaara.clock import Clock, utc_now
from ishaara.errors import IshaaraError, StorageError
from ishaara.models.domain import NarrationCue, PipelineStage, VideoRecord
from ishaara.pipeline.audio import wav_duration_seconds
from ishaara.pipeline.chunking import ClipWindow, plan_clip_windows
from ishaara.pipeline.cue_merging import merge_visual_events, to_timed_events
from ishaara.pipeline.transcript import assemble_transcript
from ishaara.services.video_service import validate_video_for_processing
from ishaara.settings import Settings

logger = structlog.get_logger()

RESOLVE_PERCENT = 1
ANALYZE_START_PERCENT = 5
ANALYZE_END_PERCENT = 70
MERGE_DONE_PERCENT = 75
NARRATION_DONE_PERCENT = 80
VOICING_END_PERCENT = 98
SAVING_PERCENT = 99
TTS_ATTEMPTS = 3  # first try plus two retries
AUDIO_CACHE_CONTROL = "public, max-age=31536000"

ProgressReporter = Callable[[int, int], Awaitable[None]]


@dataclass(frozen=True)
class WindowResult:
    """Both passes' outputs for one clip window.

    Attributes:
        analysis: Pass 1 (perception) result.
        gaps: Pass 2 (gap judging) result.
    """

    analysis: AnalyzeClipResult
    gaps: JudgeGapsResult


async def analyze_windows(
    gateway: GeminiGateway,
    youtube_video_id: str,
    windows: Sequence[ClipWindow],
    concurrency: int,
    on_window_done: ProgressReporter | None = None,
) -> list[WindowResult]:
    """Run both passes on every window, at most ``concurrency`` windows at a time.

    Args:
        gateway: Where Gemini calls go.
        youtube_video_id: The 11-character video id.
        windows: Windows to analyze.
        concurrency: Maximum windows processed in parallel.
        on_window_done: Optional callback with (windows_done, windows_total) after each window.

    Returns:
        One result per window, in window order.
    """
    semaphore = asyncio.Semaphore(concurrency)
    windows_done = 0

    async def analyze_one(window: ClipWindow) -> WindowResult:
        nonlocal windows_done
        async with semaphore:
            analysis = await gateway.analyze_clip(youtube_video_id, window)
            gaps = await gateway.judge_gaps(window, analysis.analysis)
        windows_done += 1
        if on_window_done:
            await on_window_done(windows_done, len(windows))
        return WindowResult(analysis, gaps)

    return list(await asyncio.gather(*(analyze_one(window) for window in windows)))


def merge_window_results(
    windows: Sequence[ClipWindow],
    results: Sequence[WindowResult],
    min_confidence: float,
    min_cue_gap_seconds: float,
) -> list[NarrationCue]:
    """Normalize timestamps across windows and merge unexplained events into cues.

    Args:
        windows: The analyzed windows.
        results: Their results, aligned with ``windows``.
        min_confidence: Events below this confidence are dropped.
        min_cue_gap_seconds: Closer events merge into one cue.

    Returns:
        Narration cues in time order.
    """
    timed_events = [
        event
        for window_index, (window, result) in enumerate(zip(windows, results, strict=True))
        for event in to_timed_events(
            result.analysis.analysis, result.gaps.report, window, window_index
        )
    ]
    return merge_visual_events(timed_events, min_confidence, min_cue_gap_seconds)


@dataclass
class TokenTally:
    """Running total of billed tokens across every Gemini call of one job."""

    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, input_tokens: int, output_tokens: int) -> None:
        """Add one call's usage."""
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens


class VideoPipeline:
    """Processes one video end to end and records progress as it goes.

    Args:
        videos: Where progress and results are written.
        metadata_source: Where YouTube metadata comes from.
        gateway: Where Gemini calls go.
        blob_storage: Where audio and the transcript are stored.
        settings: Application settings.
        clock: Source of the current time.
    """

    def __init__(
        self,
        videos: VideoRepository,
        metadata_source: VideoMetadataSource,
        gateway: GeminiGateway,
        blob_storage: BlobStorage,
        settings: Settings,
        clock: Clock = utc_now,
    ) -> None:
        """Store collaborators."""
        self._videos = videos
        self._metadata_source = metadata_source
        self._gateway = gateway
        self._blob_storage = blob_storage
        self._settings = settings
        self._clock = clock

    async def run(self, video_key: str) -> None:
        """Process the video; known failures are recorded on the video as ``failed``.

        Unexpected exceptions propagate to the job runner, which records ``internal_error``.

        Args:
            video_key: Key of a stored, queued video.
        """
        record = await self._videos.get(video_key)
        if record is None:
            logger.warning("pipeline_video_missing", video_key=video_key)
            return
        try:
            await self._run_stages(record)
        except IshaaraError as error:
            logger.warning("pipeline_failed", video_key=video_key, code=error.code)
            await self._videos.mark_failed(video_key, error.code, error.detail)

    async def _run_stages(self, record: VideoRecord) -> None:
        """Run every stage in order, then store the finished result."""
        video_key = record.video_key
        await self._videos.update_progress(video_key, PipelineStage.RESOLVING, RESOLVE_PERCENT)
        metadata = await self._metadata_source.fetch(record.youtube_video_id)
        validate_video_for_processing(metadata, self._settings.max_video_minutes)

        windows, results = await self._analyze(record)
        tally = TokenTally(
            sum(r.analysis.input_tokens + r.gaps.input_tokens for r in results),
            sum(r.analysis.output_tokens + r.gaps.output_tokens for r in results),
        )
        cues = merge_window_results(
            windows, results, self._settings.min_confidence, self._settings.min_cue_gap_seconds
        )
        cues = await self._write_narration(record, cues, tally)
        cues = await self._voice_cues(record, cues, tally)
        transcript_path = await self._save_transcript(record, windows, results)
        await self._videos.mark_ready(
            video_key,
            cues,
            {
                "analysis": self._settings.analysis_model,
                "gap_judging": self._settings.analysis_model,
                "narration": self._settings.text_model,
                "speech": self._settings.tts_model,
            },
            {"input_tokens": tally.input_tokens, "output_tokens": tally.output_tokens},
            transcript_path,
        )

    async def _analyze(self, record: VideoRecord) -> tuple[list[ClipWindow], list[WindowResult]]:
        """Stage 2: run both Gemini passes on every window."""
        video_key = record.video_key
        await self._videos.update_progress(
            video_key, PipelineStage.ANALYZING, ANALYZE_START_PERCENT
        )
        windows = plan_clip_windows(
            record.duration_seconds,
            self._settings.clip_window_seconds,
            self._settings.clip_overlap_seconds,
        )
        results = await analyze_windows(
            self._gateway,
            record.youtube_video_id,
            windows,
            self._settings.analysis_concurrency,
            self._progress_reporter(
                video_key, PipelineStage.ANALYZING, ANALYZE_START_PERCENT, ANALYZE_END_PERCENT
            ),
        )
        return windows, results

    async def _write_narration(
        self, record: VideoRecord, cues: list[NarrationCue], tally: TokenTally
    ) -> list[NarrationCue]:
        """Stage 4: one call writes the spoken line for every cue."""
        await self._videos.update_progress(
            record.video_key, PipelineStage.WRITING, MERGE_DONE_PERCENT
        )
        if not cues:
            return cues
        result = await self._gateway.write_narration(cues, record.narration_language)
        tally.add(result.input_tokens, result.output_tokens)
        await self._videos.update_progress(
            record.video_key, PipelineStage.WRITING, NARRATION_DONE_PERCENT
        )
        return [cue.model_copy(update={"narration_text": result.lines[cue.cue_id]}) for cue in cues]

    async def _voice_cues(
        self, record: VideoRecord, cues: list[NarrationCue], tally: TokenTally
    ) -> list[NarrationCue]:
        """Stage 5: speak and upload every cue; a cue that keeps failing stays silent."""
        if not cues:
            return cues
        await self._videos.update_progress(
            record.video_key, PipelineStage.VOICING, NARRATION_DONE_PERCENT
        )
        semaphore = asyncio.Semaphore(self._settings.tts_concurrency)
        report = self._progress_reporter(
            record.video_key, PipelineStage.VOICING, NARRATION_DONE_PERCENT, VOICING_END_PERCENT
        )
        cues_done = 0

        async def voice_one(cue: NarrationCue) -> NarrationCue:
            nonlocal cues_done
            async with semaphore:
                voiced = await self._voice_with_retries(record, cue, tally)
            cues_done += 1
            await report(cues_done, len(cues))
            return voiced

        return list(await asyncio.gather(*(voice_one(cue) for cue in cues)))

    async def _voice_with_retries(
        self, record: VideoRecord, cue: NarrationCue, tally: TokenTally
    ) -> NarrationCue:
        """Speak and upload one cue, retrying; return the cue unchanged if it keeps failing."""
        for attempt in range(1, TTS_ATTEMPTS + 1):
            started_at = time.monotonic()
            try:
                speech = await self._gateway.synthesize_speech(
                    cue.narration_text, record.narration_language
                )
                tally.add(speech.input_tokens, speech.output_tokens)
                duration_seconds = wav_duration_seconds(speech.wav_bytes)
                audio_url = await self._blob_storage.upload_public(
                    f"audio/{record.video_key}/{cue.cue_id}.wav",
                    speech.wav_bytes,
                    "audio/wav",
                    AUDIO_CACHE_CONTROL,
                )
            except (IshaaraError, ValueError) as error:
                logger.warning(
                    "cue_voicing_failed",
                    video_key=record.video_key,
                    cue_id=cue.cue_id,
                    attempt=attempt,
                    error=str(error),
                )
                continue
            logger.info(
                "cue_voiced",
                video_key=record.video_key,
                cue_id=cue.cue_id,
                attempt=attempt,
                seconds=round(time.monotonic() - started_at, 1),
            )
            return cue.model_copy(
                update={"audio_url": audio_url, "audio_duration_seconds": duration_seconds}
            )
        logger.error("cue_left_silent", video_key=record.video_key, cue_id=cue.cue_id)
        return cue

    async def _save_transcript(
        self, record: VideoRecord, windows: Sequence[ClipWindow], results: Sequence[WindowResult]
    ) -> str | None:
        """Store the transcript privately; a failure only loses the transcript."""
        await self._videos.update_progress(record.video_key, PipelineStage.SAVING, SAVING_PERCENT)
        segments = assemble_transcript(windows, [r.analysis.analysis for r in results])
        payload = json.dumps(
            {
                "video_key": record.video_key,
                "segments": [segment.model_dump(mode="json") for segment in segments],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        try:
            return await self._blob_storage.upload_private(
                f"transcripts/{record.video_key}.json", payload, "application/json"
            )
        except StorageError:
            logger.warning("transcript_upload_failed", video_key=record.video_key)
            return None

    def _progress_reporter(
        self, video_key: str, stage: PipelineStage, start_percent: int, end_percent: int
    ) -> ProgressReporter:
        """Build a callback mapping finished items to a percentage range, never going backwards."""
        lock = asyncio.Lock()
        highest_percent = start_percent

        async def report(items_done: int, items_total: int) -> None:
            nonlocal highest_percent
            percent = start_percent + (end_percent - start_percent) * items_done // items_total
            async with lock:
                if percent > highest_percent:
                    highest_percent = percent
                    await self._videos.update_progress(video_key, stage, percent)

        return report
