"""Pre-processing videos ahead of time, so a demo never waits for Gemini."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass

from ishaara.container import AppContainer
from ishaara.errors import IshaaraError
from ishaara.models.api_models import AuthenticatedUser
from ishaara.models.domain import NarrationLanguage, VideoStatus

PREPROCESS_USER = AuthenticatedUser(uid="cli-preprocess")


@dataclass(frozen=True)
class ProcessOutcome:
    """The result of pre-processing one video in one language.

    Attributes:
        youtube_url: The link as given.
        narration_language: ``en`` or ``hi``.
        video_key: Key of the stored video, or ``None`` if it could not be submitted.
        status: ``ready`` (processed now), ``cached`` (already ready) or ``failed``.
        cues: Number of cues in the result.
        voiced_cues: How many of those cues have audio.
        error_code: Why it failed, when it did.
    """

    youtube_url: str
    narration_language: str
    video_key: str | None
    status: str
    cues: int
    voiced_cues: int
    error_code: str | None

    def to_dict(self) -> dict[str, object]:
        """Return the outcome as plain JSON-ready values."""
        return asdict(self)


async def process_videos(
    container: AppContainer,
    youtube_urls: Sequence[str],
    languages: Sequence[NarrationLanguage],
) -> list[ProcessOutcome]:
    """Process every URL in every language, one at a time, and report each outcome.

    A video that fails never stops the others. A video that is already ``ready`` is
    reported as ``cached`` and costs nothing.

    Args:
        container: The app's collaborators, wired to the real database and storage.
        youtube_urls: Links to process.
        languages: Narration languages to produce for each link.

    Returns:
        One outcome per (URL, language), in order.
    """
    return [
        await _process_one(container, youtube_url, language)
        for youtube_url in youtube_urls
        for language in languages
    ]


async def _process_one(
    container: AppContainer, youtube_url: str, language: NarrationLanguage
) -> ProcessOutcome:
    """Submit one video, wait for its job, and summarize the stored result."""
    try:
        submitted = await container.video_service.submit(PREPROCESS_USER, youtube_url, language)
    except IshaaraError as error:
        return ProcessOutcome(youtube_url, language.value, None, "failed", 0, 0, error.code)
    await container.job_runner.wait_until_idle()
    record = await container.videos.get(submitted.video.video_key)
    video_key = submitted.video.video_key
    if record is None or record.status != VideoStatus.READY:
        code = record.error_code if record and record.error_code else "not_finished"
        return ProcessOutcome(youtube_url, language.value, video_key, "failed", 0, 0, code)
    status = "cached" if submitted.http_status == 200 else "ready"
    voiced = sum(1 for cue in record.cues if cue.audio_url)
    return ProcessOutcome(
        youtube_url, language.value, video_key, status, len(record.cues), voiced, None
    )
