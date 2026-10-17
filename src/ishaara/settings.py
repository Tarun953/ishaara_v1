"""Typed application settings loaded from environment variables."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration for the Ishaara backend.

    Values come from environment variables (and a local ``.env`` file). Secrets and
    resource names default to empty strings so the app can start without them; code
    that needs them must check for emptiness.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["local", "production"] = "local"
    gemini_api_key: str = ""
    youtube_api_key: str = ""
    gcp_project_id: str = ""
    audio_bucket: str = ""
    transcript_bucket: str = ""
    analysis_model: str = "gemini-3.8-flash"
    text_model: str = "gemini-3.5-flash-lite"
    tts_model: str = "gemini-3.8-flash-lite-tts"
    tts_voice: str = "Kore"
    pipeline_version: int = 1
    max_video_minutes: int = 45
    clip_window_seconds: int = 600
    clip_overlap_seconds: int = 10
    analysis_concurrency: int = 3
    tts_concurrency: int = 4
    min_confidence: float = 0.5
    min_cue_gap_seconds: float = 6.0
    daily_video_limit: int = 10
    daily_question_limit: int = 50
    allowed_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]
    auth_bypass_uid: str = ""
    log_level: str = "INFO"

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, raw_origins: object) -> object:
        """Split a comma-separated origins string into a list."""
        if isinstance(raw_origins, str):
            return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
        return raw_origins

    @model_validator(mode="after")
    def _refuse_bypass_in_production(self) -> "Settings":
        """Reject ``AUTH_BYPASS_UID`` when running in production.

        Raises:
            ValueError: If a bypass uid is set while ``environment`` is ``production``.
        """
        if self.environment == "production" and self.auth_bypass_uid:
            raise ValueError("AUTH_BYPASS_UID must not be set when ENVIRONMENT=production")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings.

    Returns:
        The process-wide ``Settings`` instance.
    """
    return Settings()
