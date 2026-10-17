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
    pipeline_version: int = 2
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
    database_backend: Literal["memory", "firestore", "supabase"] = "memory"
    supabase_db_url: str = ""
    storage_backend: Literal["local", "gcs", "supabase"] = "local"
    local_storage_dir: str = ".local_storage"
    public_base_url: str = "http://localhost:8000"
    supabase_url: str = ""
    supabase_service_key: str = ""
    log_level: str = "INFO"

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, raw_origins: object) -> object:
        """Split a comma-separated origins string into a list."""
        if isinstance(raw_origins, str):
            return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
        return raw_origins

    @model_validator(mode="after")
    def _refuse_unsafe_production_settings(self) -> "Settings":
        """Reject settings that are only safe for local use when in production.

        Raises:
            ValueError: If ``environment`` is ``production`` with an auth bypass uid, the
                in-memory database or local file storage.
        """
        if self.environment == "production" and self.auth_bypass_uid:
            raise ValueError("AUTH_BYPASS_UID must not be set when ENVIRONMENT=production")
        if self.environment == "production" and self.database_backend == "memory":
            raise ValueError("DATABASE_BACKEND=memory is not allowed when ENVIRONMENT=production")
        if self.environment == "production" and self.storage_backend == "local":
            raise ValueError("STORAGE_BACKEND=local is not allowed when ENVIRONMENT=production")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings.

    Returns:
        The process-wide ``Settings`` instance.
    """
    return Settings()
