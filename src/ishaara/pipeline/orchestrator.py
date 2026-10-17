"""The video processing pipeline: resolve, analyze and merge (narration and voice arrive in M4)."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

import structlog

from ishaara.clients.gemini_gateway import AnalyzeClipResult, GeminiGateway, JudgeGapsResult
from ishaara.clients.repositories import VideoRepository
from ishaara.clients.youtube_metadata import VideoMetadataSource
from ishaara.clock import Clock, utc_now
from ishaara.errors import IshaaraError
from ishaara.models.domain import NarrationCue, PipelineStage, VideoRecord
from ishaara.pipeline.chunking import ClipWindow, plan_clip_windows
from ishaara.pipeline.cue_merging import merge_visual_events, to_timed_events
from ishaara.services.video_service import validate_video_for_processing
from ishaara.settings import Settings

logger = structlog.get_logger()

RESOLVE_PERCENT = 1
ANALYZE_START_PERCENT = 5
ANALYZE_END_PERCENT = 70
MERGE_DONE_PERCENT = 75

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


class VideoPipeline:
    """Processes one video end to end and records progress as it goes.

    Args:
        videos: Where progress and results are written.
        metadata_source: Where YouTube metadata comes from.
        gateway: Where Gemini calls go.
        settings: Application settings.
        clock: Source of the current time.
    """

    def __init__(
        self,
        videos: VideoRepository,
        metadata_source: VideoMetadataSource,
        gateway: GeminiGateway,
        settings: Settings,
        clock: Clock = utc_now,
    ) -> None:
        """Store collaborators."""
        self._videos = videos
        self._metadata_source = metadata_source
        self._gateway = gateway
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
        """Run resolve, analyze and merge, then store the result."""
        video_key = record.video_key
        await self._videos.update_progress(video_key, PipelineStage.RESOLVING, RESOLVE_PERCENT)
        metadata = await self._metadata_source.fetch(record.youtube_video_id)
        validate_video_for_processing(metadata, self._settings.max_video_minutes)

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
            self._progress_reporter(video_key),
        )
        cues = merge_window_results(
            windows, results, self._settings.min_confidence, self._settings.min_cue_gap_seconds
        )
        await self._videos.update_progress(video_key, PipelineStage.WRITING, MERGE_DONE_PERCENT)
        # Narration writing and voicing arrive in M4; for now the merged cues are the result.
        await self._videos.mark_ready(
            video_key,
            cues,
            {
                "analysis": self._settings.analysis_model,
                "gap_judging": self._settings.analysis_model,
            },
            _sum_token_usage(results),
            None,
        )

    def _progress_reporter(self, video_key: str) -> ProgressReporter:
        """Build a callback that maps finished windows to 5-70% without going backwards."""
        lock = asyncio.Lock()
        highest_percent = ANALYZE_START_PERCENT

        async def report(windows_done: int, windows_total: int) -> None:
            nonlocal highest_percent
            span = ANALYZE_END_PERCENT - ANALYZE_START_PERCENT
            percent = ANALYZE_START_PERCENT + span * windows_done // windows_total
            async with lock:
                if percent > highest_percent:
                    highest_percent = percent
                    await self._videos.update_progress(video_key, PipelineStage.ANALYZING, percent)

        return report


def _sum_token_usage(results: Sequence[WindowResult]) -> dict[str, int]:
    """Total the billed tokens across both passes of every window."""
    return {
        "input_tokens": sum(r.analysis.input_tokens + r.gaps.input_tokens for r in results),
        "output_tokens": sum(r.analysis.output_tokens + r.gaps.output_tokens for r in results),
    }
