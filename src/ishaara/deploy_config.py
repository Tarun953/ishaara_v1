"""Turning a production env file into the arguments of ``gcloud run deploy``.

Plain settings go to Cloud Run as environment variables; secrets are never read from the
env file but referenced by their Secret Manager names.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import ValidationError

from ishaara.settings import Settings

# Setting -> Secret Manager secret holding its value.
SECRET_NAMES = {
    "GEMINI_API_KEY": "gemini-api-key",
    "YOUTUBE_API_KEY": "youtube-api-key",
    "SUPABASE_DB_URL": "supabase-db-url",
    "SUPABASE_SERVICE_KEY": "supabase-service-key",
}
ALWAYS_SECRET = ("GEMINI_API_KEY", "YOUTUBE_API_KEY")
LOCAL_ONLY_SETTINGS = {"AUTH_BYPASS_UID", "LOCAL_STORAGE_DIR", "PUBLIC_BASE_URL"}
DELIMITER_CANDIDATES = "@#|~;"


@dataclass(frozen=True)
class CloudRunConfig:
    """What to pass to ``gcloud run deploy``.

    Attributes:
        project_id: The Google Cloud project.
        env_vars_argument: Value for ``--set-env-vars`` (with a custom delimiter, because
            ``ALLOWED_ORIGINS`` contains commas).
        secrets_argument: Value for ``--set-secrets``.
    """

    project_id: str
    env_vars_argument: str
    secrets_argument: str


def build_cloud_run_config(values: Mapping[str, str | None]) -> CloudRunConfig:
    """Build the deploy arguments from the merged production env values.

    Args:
        values: Variable names mapped to values, from ``.env`` overlaid by ``.env.prod``.

    Returns:
        The project id and the two arguments.

    Raises:
        ValueError: If the values are not safe or complete for production (the app would
            refuse to start), or no delimiter can be found for the variable list.
    """
    present = {
        name: value
        for name, value in values.items()
        if value and name.lower() in Settings.model_fields
    }
    _check_production_safe(present)
    secrets_needed = _secrets_needed(present)
    env_vars = {
        name: value
        for name, value in present.items()
        if name not in SECRET_NAMES and name not in LOCAL_ONLY_SETTINGS
    }
    delimiter = _pick_delimiter(env_vars)
    env_vars_argument = f"^{delimiter}^" + delimiter.join(
        f"{name}={value}" for name, value in sorted(env_vars.items())
    )
    secrets_argument = ",".join(f"{name}={SECRET_NAMES[name]}:latest" for name in secrets_needed)
    return CloudRunConfig(present["GCP_PROJECT_ID"], env_vars_argument, secrets_argument)


def _check_production_safe(present: Mapping[str, str]) -> None:
    """Validate the values exactly as the deployed app will, and require a project id."""
    try:
        Settings(_env_file=None, **{name.lower(): value for name, value in present.items()})  # type: ignore[arg-type]
    except ValidationError as error:
        reasons = "; ".join(item["msg"] for item in error.errors())
        raise ValueError(f"These settings would not start in production: {reasons}") from error
    if present.get("ENVIRONMENT") != "production":
        raise ValueError("ENVIRONMENT must be 'production' in the production env file")
    if not present.get("GCP_PROJECT_ID"):
        raise ValueError("GCP_PROJECT_ID must be set in the production env file")


def _secrets_needed(present: Mapping[str, str]) -> list[str]:
    """Return the settings that must come from Secret Manager for these backends."""
    needed = list(ALWAYS_SECRET)
    if present.get("DATABASE_BACKEND") == "supabase":
        needed.append("SUPABASE_DB_URL")
    if present.get("STORAGE_BACKEND") == "supabase":
        needed.append("SUPABASE_SERVICE_KEY")
    return needed


def _pick_delimiter(env_vars: Mapping[str, str]) -> str:
    """Pick a delimiter character that appears in no variable name or value."""
    text = "".join(f"{name}{value}" for name, value in env_vars.items())
    for candidate in DELIMITER_CANDIDATES:
        if candidate not in text:
            return candidate
    raise ValueError("No safe delimiter found for the environment variables")
