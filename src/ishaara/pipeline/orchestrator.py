"""The video processing pipeline (stage wiring and progress arrive in M3 and M4)."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from ishaara.clients.gemini_gateway import AnalyzeClipResult, GeminiGateway, JudgeGapsResult
from ishaara.models.domain import NarrationCue
from ishaara.pipeline.chunking import ClipWindow
from ishaara.pipeline.cue_merging import merge_visual_events, to_timed_events


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
) -> list[WindowResult]:
    """Run both passes on every window, at most ``concurrency`` windows at a time.

    Args:
        gateway: Where Gemini calls go.
        youtube_video_id: The 11-character video id.
        windows: Windows to analyze.
        concurrency: Maximum windows processed in parallel.

    Returns:
        One result per window, in window order.
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def analyze_one(window: ClipWindow) -> WindowResult:
        async with semaphore:
            analysis = await gateway.analyze_clip(youtube_video_id, window)
            gaps = await gateway.judge_gaps(window, analysis.analysis)
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
