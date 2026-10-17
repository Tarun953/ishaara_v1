"""Video submission: validation, caching, idempotency, restarts and daily quotas."""

from dataclasses import dataclass

from ishaara.clients.repositories import DailyUsageField, UsageRepository, VideoRepository
from ishaara.clients.youtube_metadata import VideoMetadataSource
from ishaara.clock import Clock, utc_now
from ishaara.errors import (
    DailyLimitReachedError,
    VideoIsLiveError,
    VideoNotEmbeddableError,
    VideoNotPublicError,
    VideoTooLongError,
)
from ishaara.models.api_models import (
    AuthenticatedUser,
    VideoStatusResponse,
    YouTubeVideoMetadata,
)
from ishaara.models.domain import NarrationLanguage, VideoRecord, VideoStatus
from ishaara.pipeline.youtube_url import parse_youtube_video_id
from ishaara.services.job_runner import JobRunner
from ishaara.settings import Settings

SECONDS_PER_MINUTE = 60


def validate_video_for_processing(metadata: YouTubeVideoMetadata, max_video_minutes: int) -> None:
    """Check that a video can be analyzed and embedded.

    Args:
        metadata: Metadata fetched from the YouTube Data API.
        max_video_minutes: Longest accepted video, in minutes.

    Raises:
        VideoNotPublicError: If the video is private or unlisted.
        VideoNotEmbeddableError: If embedding is disabled.
        VideoIsLiveError: If the video is live or upcoming.
        VideoTooLongError: If the video is longer than ``max_video_minutes``.
    """
    if metadata.privacy_status != "public":
        raise VideoNotPublicError("Only public videos can be processed.")
    if not metadata.embeddable:
        raise VideoNotEmbeddableError("The video owner has disabled embedding.")
    if metadata.live_broadcast_content != "none":
        raise VideoIsLiveError("Live and upcoming broadcasts cannot be processed.")
    video_minutes = round(metadata.duration_seconds / SECONDS_PER_MINUTE)
    if metadata.duration_seconds > max_video_minutes * SECONDS_PER_MINUTE:
        raise VideoTooLongError(
            f"This video is {video_minutes} minutes; the limit is {max_video_minutes} minutes."
        )


@dataclass(frozen=True)
class SubmitResult:
    """The outcome of a submission.

    Attributes:
        video: Current status of the video.
        http_status: 200 if the result is ready, 202 if it is queued or processing.
    """

    video: VideoStatusResponse
    http_status: int


def build_video_key(
    youtube_video_id: str, narration_language: NarrationLanguage, pipeline_version: int
) -> str:
    """Build the key used as job id, cache key and document id.

    Args:
        youtube_video_id: The 11-character video id.
        narration_language: Language the narration is spoken in.
        pipeline_version: Bumped whenever prompts change, so stale results are not reused.

    Returns:
        ``{id}_{language}_v{pipeline_version}``.
    """
    return f"{youtube_video_id}_{narration_language.value}_v{pipeline_version}"


class VideoService:
    """Accepts video submissions and starts, restarts or reuses processing jobs.

    Args:
        videos: Video and history storage.
        usage: Daily quota counters.
        metadata_source: Where YouTube metadata comes from.
        job_runner: Starts background jobs.
        settings: Application settings (limits and pipeline version).
        clock: Source of the current time.
    """

    def __init__(
        self,
        videos: VideoRepository,
        usage: UsageRepository,
        metadata_source: VideoMetadataSource,
        job_runner: JobRunner,
        settings: Settings,
        clock: Clock = utc_now,
    ) -> None:
        """Store collaborators."""
        self._videos = videos
        self._usage = usage
        self._metadata_source = metadata_source
        self._job_runner = job_runner
        self._settings = settings
        self._clock = clock

    async def submit(
        self, user: AuthenticatedUser, youtube_url: str, narration_language: NarrationLanguage
    ) -> SubmitResult:
        """Submit a video for processing, reusing earlier work when possible.

        A ready result is returned without using quota. A running job is joined. A failed
        job or one whose lease expired is restarted. A new video is validated, then queued.

        Args:
            user: The authenticated caller.
            youtube_url: A YouTube link or bare video id.
            narration_language: Language to narrate in.

        Returns:
            The video status and 200 (ready) or 202 (queued or processing).

        Raises:
            InvalidYouTubeUrlError: If the link is not a YouTube video.
            IshaaraError: A 422 subclass if the video cannot be processed.
            DailyLimitReachedError: If the user's daily video limit is used up.
        """
        youtube_video_id = parse_youtube_video_id(youtube_url)
        video_key = build_video_key(
            youtube_video_id, narration_language, self._settings.pipeline_version
        )
        existing = await self._videos.get(video_key)
        if existing is None:
            return await self._submit_new(user, youtube_video_id, narration_language, video_key)
        if existing.is_restartable(self._clock()):
            return await self._restart(user, existing)
        return await self._respond_with_existing(user, existing)

    async def _submit_new(
        self,
        user: AuthenticatedUser,
        youtube_video_id: str,
        narration_language: NarrationLanguage,
        video_key: str,
    ) -> SubmitResult:
        """Validate and queue a video nobody has submitted yet."""
        metadata = await self._metadata_source.fetch(youtube_video_id)
        validate_video_for_processing(metadata, self._settings.max_video_minutes)
        await self._spend_daily_quota(user)
        now = self._clock()
        record, created = await self._videos.create_if_absent(
            VideoRecord(
                video_key=video_key,
                youtube_video_id=youtube_video_id,
                narration_language=narration_language,
                pipeline_version=self._settings.pipeline_version,
                title=metadata.title,
                channel_title=metadata.channel_title,
                duration_seconds=metadata.duration_seconds,
                status=VideoStatus.QUEUED,
                lease_expires_at=self._job_runner.lease_expiry(),
                created_by_uid=user.uid,
                created_at=now,
                updated_at=now,
            )
        )
        if not created:  # lost a race with a concurrent first submission
            return await self._respond_with_existing(user, record)
        self._job_runner.start(video_key)
        return await self._accepted(user, record)

    async def _restart(self, user: AuthenticatedUser, record: VideoRecord) -> SubmitResult:
        """Restart a failed job or one whose lease expired."""
        await self._spend_daily_quota(user)
        restarted = await self._videos.claim_restart(
            record.video_key, self._job_runner.lease_expiry()
        )
        if restarted is None:  # someone else restarted it first
            current = await self._videos.get(record.video_key)
            return await self._respond_with_existing(user, current or record)
        self._job_runner.start(record.video_key)
        return await self._accepted(user, restarted)

    async def _respond_with_existing(
        self, user: AuthenticatedUser, record: VideoRecord
    ) -> SubmitResult:
        """Answer from a stored record: 200 if ready, otherwise 202 and join the job."""
        await self._videos.record_submission(
            user.uid, record.video_key, record.title, self._clock()
        )
        http_status = 200 if record.status == VideoStatus.READY else 202
        return SubmitResult(VideoStatusResponse.from_record(record), http_status)

    async def _accepted(self, user: AuthenticatedUser, record: VideoRecord) -> SubmitResult:
        """Record the submission and answer 202 for a job that was just started."""
        await self._videos.record_submission(
            user.uid, record.video_key, record.title, self._clock()
        )
        return SubmitResult(VideoStatusResponse.from_record(record), 202)

    async def _spend_daily_quota(self, user: AuthenticatedUser) -> None:
        """Count one video against the user's daily limit.

        Raises:
            DailyLimitReachedError: If the limit is already reached.
        """
        limit = self._settings.daily_video_limit
        if not await self._usage.increment_and_check(
            user.uid, DailyUsageField.VIDEOS_SUBMITTED, limit
        ):
            raise DailyLimitReachedError(f"You can process {limit} new videos per day.")
