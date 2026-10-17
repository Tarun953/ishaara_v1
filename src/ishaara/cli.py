"""Command-line tools: ``ishaara analyze`` (prompt tuning) and ``ishaara process`` (demo prep)."""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx
import structlog
from dotenv import dotenv_values

from ishaara.clients.gemini_gateway import (
    AnalyzeClipResult,
    AnswerResult,
    GeminiGateway,
    GoogleGeminiGateway,
    JudgeGapsResult,
    NarrationResult,
    SpeechResult,
)
from ishaara.clients.youtube_metadata import YouTubeMetadataClient
from ishaara.container import build_container
from ishaara.deploy_config import build_cloud_run_config
from ishaara.logging_config import configure_logging
from ishaara.models.domain import NarrationCue, NarrationLanguage
from ishaara.models.gemini_schemas import ChunkAnalysis
from ishaara.pipeline.chunking import ClipWindow, plan_clip_windows
from ishaara.pipeline.orchestrator import WindowResult, analyze_windows, merge_window_results
from ishaara.pipeline.youtube_url import parse_youtube_video_id
from ishaara.preprocess import process_videos
from ishaara.services.video_service import validate_video_for_processing
from ishaara.settings import Settings, env_files, get_settings

FIXTURES_DIRECTORY = Path("tests/fixtures")
SECONDS_PER_MINUTE = 60

logger = structlog.get_logger()


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns:
        A parser with the ``analyze`` sub-command.
    """
    parser = argparse.ArgumentParser(prog="ishaara")
    subcommands = parser.add_subparsers(dest="command", required=True)
    analyze = subcommands.add_parser("analyze", help="Analyze a video and print merged cues.")
    analyze.add_argument("youtube_url")
    analyze.add_argument("--language", choices=["en", "hi"], default="en")
    analyze.add_argument(
        "--save-fixtures",
        action="store_true",
        help=f"Write raw Gemini responses to {FIXTURES_DIRECTORY}/.",
    )
    analyze.add_argument(
        "--from-fixtures",
        action="store_true",
        help="Reuse saved pass 1 responses (no video call) and re-run only pass 2.",
    )
    analyze.add_argument(
        "--max-minutes", type=int, help="Only analyze the first N minutes (cheap trial runs)."
    )
    analyze.add_argument(
        "--duration-seconds",
        type=int,
        help="Video length; skips the YouTube metadata lookup (needed without YOUTUBE_API_KEY).",
    )
    process = subcommands.add_parser(
        "process",
        help="Fully process videos ahead of time (uses the configured database and storage).",
    )
    process.add_argument("youtube_urls", nargs="+", metavar="youtube_url")
    process.add_argument(
        "--languages",
        nargs="+",
        choices=["en", "hi"],
        default=["en"],
        help="Narration languages to produce for every link (default: en).",
    )
    deploy_env = subcommands.add_parser(
        "deploy-env",
        help="Print the project id and the Cloud Run --set-env-vars / --set-secrets values.",
    )
    deploy_env.add_argument("--env-name", default="prod", help="Env file suffix (default: prod).")
    return parser


async def _resolve_duration_seconds(
    args: argparse.Namespace, settings: Settings, youtube_video_id: str
) -> int:
    """Return the video duration from the flag or from YouTube (validating the video)."""
    if args.duration_seconds:
        return int(args.duration_seconds)
    if not settings.youtube_api_key:
        raise SystemExit("Set YOUTUBE_API_KEY or pass --duration-seconds.")
    async with httpx.AsyncClient() as http_client:
        metadata = await YouTubeMetadataClient(settings.youtube_api_key, http_client).fetch(
            youtube_video_id
        )
    validate_video_for_processing(metadata, settings.max_video_minutes)
    return metadata.duration_seconds


def _fixture_path(youtube_video_id: str, window: ClipWindow, suffix: str = "") -> Path:
    """Return the fixture file for a window (``_gaps`` suffix holds pass 2)."""
    return FIXTURES_DIRECTORY / f"{youtube_video_id}_w{window.start_seconds}{suffix}.json"


def _save_fixtures(
    youtube_video_id: str, windows: Sequence[ClipWindow], results: Sequence[WindowResult]
) -> None:
    """Write each window's raw pass 1 and pass 2 responses to the fixtures directory."""
    FIXTURES_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for window, result in zip(windows, results, strict=True):
        _fixture_path(youtube_video_id, window).write_text(
            result.analysis.raw_response_text, encoding="utf-8"
        )
        _fixture_path(youtube_video_id, window, "_gaps").write_text(
            result.gaps.raw_response_text, encoding="utf-8"
        )


class _Pass1ReplayGateway:
    """Serves pass 1 from saved fixtures and sends everything else to the live gateway."""

    def __init__(self, live_gateway: GeminiGateway) -> None:
        """Wrap the live gateway used for pass 2."""
        self._live_gateway = live_gateway

    async def analyze_clip(self, youtube_video_id: str, window: ClipWindow) -> AnalyzeClipResult:
        """Load the saved pass 1 response for the window."""
        raw_response_text = _fixture_path(youtube_video_id, window).read_text(encoding="utf-8")
        analysis = ChunkAnalysis.model_validate_json(raw_response_text)
        return AnalyzeClipResult(analysis, raw_response_text, 0, 0)

    async def judge_gaps(self, window: ClipWindow, analysis: ChunkAnalysis) -> JudgeGapsResult:
        """Run pass 2 live."""
        return await self._live_gateway.judge_gaps(window, analysis)

    async def write_narration(
        self, cues: Sequence[NarrationCue], narration_language: NarrationLanguage
    ) -> NarrationResult:
        """Delegate to the live gateway."""
        return await self._live_gateway.write_narration(cues, narration_language)

    async def synthesize_speech(
        self, narration_text: str, narration_language: NarrationLanguage
    ) -> SpeechResult:
        """Delegate to the live gateway."""
        return await self._live_gateway.synthesize_speech(narration_text, narration_language)

    async def answer_question(
        self, question: str, context_text: str, at_seconds: float | None
    ) -> AnswerResult:
        """Delegate to the live gateway."""
        return await self._live_gateway.answer_question(question, context_text, at_seconds)

    async def answer_with_video(
        self, youtube_video_id: str, question: str, window: ClipWindow, at_seconds: float
    ) -> AnswerResult:
        """Delegate to the live gateway."""
        return await self._live_gateway.answer_with_video(
            youtube_video_id, question, window, at_seconds
        )


async def _analyze(args: argparse.Namespace, settings: Settings) -> list[NarrationCue]:
    """Run the analysis stages for one video and return merged cues."""
    if not settings.gemini_api_key:
        raise SystemExit("Set GEMINI_API_KEY (for example in .env).")
    youtube_video_id = parse_youtube_video_id(args.youtube_url)
    duration_seconds = await _resolve_duration_seconds(args, settings, youtube_video_id)
    if args.max_minutes:
        duration_seconds = min(duration_seconds, args.max_minutes * SECONDS_PER_MINUTE)
    windows = plan_clip_windows(
        duration_seconds, settings.clip_window_seconds, settings.clip_overlap_seconds
    )
    logger.info("analysis_started", youtube_video_id=youtube_video_id, windows=len(windows))
    gateway: GeminiGateway = GoogleGeminiGateway(settings)
    if args.from_fixtures:
        gateway = _Pass1ReplayGateway(gateway)
    results = await analyze_windows(
        gateway, youtube_video_id, windows, settings.analysis_concurrency
    )
    if args.save_fixtures:
        _save_fixtures(youtube_video_id, windows, results)
    logger.info(
        "analysis_finished",
        input_tokens=sum(r.analysis.input_tokens + r.gaps.input_tokens for r in results),
        output_tokens=sum(r.analysis.output_tokens + r.gaps.output_tokens for r in results),
    )
    return merge_window_results(
        windows, results, settings.min_confidence, settings.min_cue_gap_seconds
    )


async def _process(args: argparse.Namespace, settings: Settings) -> int:
    """Pre-process the given videos for real and print one JSON line per outcome."""
    if settings.database_backend == "memory":
        raise SystemExit(
            "Set DATABASE_BACKEND to firestore or supabase: with memory nothing is kept."
        )
    if not (settings.gemini_api_key and settings.youtube_api_key):
        raise SystemExit("Set GEMINI_API_KEY and YOUTUBE_API_KEY (for example in .env).")
    if settings.storage_backend == "local":
        logger.warning("audio_stored_locally", note="a deployed service cannot play these files")
    languages = [NarrationLanguage(code) for code in args.languages]
    async with httpx.AsyncClient() as http_client:
        container = build_container(settings, http_client)
        try:
            outcomes = await process_videos(container, args.youtube_urls, languages)
        finally:
            await container.close()
    for outcome in outcomes:
        sys.stdout.write(json.dumps(outcome.to_dict(), ensure_ascii=False) + "\n")
    return 1 if any(outcome.status == "failed" for outcome in outcomes) else 0


def _print_deploy_env(env_name: str) -> None:
    """Print three lines: project id, ``--set-env-vars`` value, ``--set-secrets`` value."""
    values: dict[str, str | None] = {}
    for env_file in env_files(env_name):
        values.update(dotenv_values(env_file))
    try:
        config = build_cloud_run_config(values)
    except ValueError as error:
        raise SystemExit(f"Cannot build the deploy configuration: {error}") from error
    sys.stdout.write(
        f"{config.project_id}\n{config.env_vars_argument}\n{config.secrets_argument}\n"
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Run the ``ishaara`` command-line tool.

    Args:
        argv: Arguments to parse; defaults to ``sys.argv[1:]``.

    Raises:
        SystemExit: With status 1 if any video given to ``process`` failed.
    """
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, stream=sys.stderr)
    if args.command == "deploy-env":
        _print_deploy_env(args.env_name)
        return
    if args.command == "process":
        exit_code = asyncio.run(_process(args, settings))
        if exit_code:
            raise SystemExit(exit_code)
        return
    cues = asyncio.run(_analyze(args, settings))
    sys.stdout.write(json.dumps([cue.model_dump(mode="json") for cue in cues], indent=2) + "\n")
