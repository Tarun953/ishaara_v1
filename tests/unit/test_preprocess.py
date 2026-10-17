import pytest

from ishaara.cli import build_parser
from ishaara.container import AppContainer
from ishaara.models.domain import NarrationLanguage
from ishaara.preprocess import PREPROCESS_USER, process_videos
from ishaara.services.question_service import QuestionService
from ishaara.settings import Settings
from tests.api.test_videos_api import _container
from tests.unit.test_pipeline import FailingAnalysisGateway

URL_ONE = "https://youtu.be/dQw4w9WgXcQ"
URL_TWO = "https://youtu.be/abcdefghijk"
ENGLISH, HINDI = NarrationLanguage.ENGLISH, NarrationLanguage.HINDI


def _settings() -> Settings:
    return Settings(_env_file=None)


async def test_every_url_and_language_is_processed_to_ready() -> None:
    container = _container(_settings())

    outcomes = await process_videos(container, [URL_ONE, URL_TWO], [ENGLISH, HINDI])

    assert [(o.youtube_url, o.narration_language, o.status) for o in outcomes] == [
        (URL_ONE, "en", "ready"),
        (URL_ONE, "hi", "ready"),
        (URL_TWO, "en", "ready"),
        (URL_TWO, "hi", "ready"),
    ]
    assert all(o.cues == 1 and o.voiced_cues == 1 and o.error_code is None for o in outcomes)
    assert outcomes[0].video_key == "dQw4w9WgXcQ_en_v2"


async def test_a_second_run_reports_cached_and_changes_nothing() -> None:
    container = _container(_settings())
    await process_videos(container, [URL_ONE], [ENGLISH])

    outcomes = await process_videos(container, [URL_ONE], [ENGLISH])

    assert outcomes[0].status == "cached"
    assert outcomes[0].cues == 1


async def test_one_bad_video_does_not_stop_the_others() -> None:
    container = _container(_settings())

    outcomes = await process_videos(container, ["https://example.com/nope", URL_ONE], [ENGLISH])

    assert (outcomes[0].status, outcomes[0].error_code, outcomes[0].video_key) == (
        "failed",
        "invalid_youtube_url",
        None,
    )
    assert outcomes[1].status == "ready"


async def test_a_private_video_is_reported_as_failed_with_its_code() -> None:
    container = _container(_settings(), privacy_status="private")

    outcomes = await process_videos(container, [URL_ONE], [ENGLISH])

    assert (outcomes[0].status, outcomes[0].error_code) == ("failed", "video_not_public")


async def test_a_pipeline_failure_is_reported_with_the_stored_error_code() -> None:
    base = _container(_settings())
    settings = _settings()
    from ishaara.pipeline.orchestrator import VideoPipeline
    from ishaara.services.job_runner import JobRunner
    from ishaara.services.video_service import VideoService
    from tests.conftest import FakeBlobStorage, FakeMetadataSource

    gateway, metadata, storage = FailingAnalysisGateway(), FakeMetadataSource(), FakeBlobStorage()
    pipeline = VideoPipeline(base.videos, metadata, gateway, storage, settings)
    runner = JobRunner(base.videos, pipeline.run)
    container = AppContainer(
        VideoService(base.videos, base.usage, metadata, runner, settings),
        base.videos,
        base.usage,
        runner,
        storage,
        QuestionService(base.usage, gateway, storage, settings),
    )

    outcomes = await process_videos(container, [URL_ONE], [ENGLISH])

    assert (outcomes[0].status, outcomes[0].error_code) == ("failed", "gemini_error")
    assert outcomes[0].video_key == "dQw4w9WgXcQ_en_v2"


async def test_pre_processing_has_its_own_quota_and_leaves_a_students_untouched() -> None:
    from ishaara.clients.repositories import DailyUsageField

    container = _container(Settings(_env_file=None, daily_video_limit=1))

    first = await process_videos(container, [URL_ONE], [ENGLISH])
    second = await process_videos(container, [URL_TWO], [ENGLISH])

    assert PREPROCESS_USER.uid == "cli-preprocess"
    assert first[0].status == "ready"
    assert second[0].error_code == "daily_limit_reached"  # the tool's own limit applies to it
    student_can_still_submit = await container.usage.increment_and_check(
        "a-student", DailyUsageField.VIDEOS_SUBMITTED, 1
    )
    assert student_can_still_submit is True


def test_the_process_command_takes_urls_and_languages() -> None:
    args = build_parser().parse_args(["process", URL_ONE, URL_TWO, "--languages", "en", "hi"])

    assert (args.command, args.youtube_urls, args.languages) == (
        "process",
        [URL_ONE, URL_TWO],
        ["en", "hi"],
    )


def test_the_process_command_defaults_to_english() -> None:
    assert build_parser().parse_args(["process", URL_ONE]).languages == ["en"]


def test_an_unknown_language_is_rejected_by_the_parser() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["process", URL_ONE, "--languages", "fr"])
