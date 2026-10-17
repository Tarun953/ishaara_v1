import pytest

from ishaara.deploy_config import build_cloud_run_config

GOOGLE_VALUES = {
    "ENVIRONMENT": "production",
    "GCP_PROJECT_ID": "my-project",
    "DATABASE_BACKEND": "firestore",
    "STORAGE_BACKEND": "gcs",
    "AUDIO_BUCKET": "my-audio",
    "TRANSCRIPT_BUCKET": "my-transcripts",
    "ALLOWED_ORIGINS": "https://my-project.web.app,http://localhost:5173",
    "GEMINI_API_KEY": "SECRET-GEMINI-VALUE",
    "YOUTUBE_API_KEY": "SECRET-YOUTUBE-VALUE",
}


def test_plain_settings_become_env_vars_with_a_custom_delimiter() -> None:
    config = build_cloud_run_config(GOOGLE_VALUES)

    assert config.project_id == "my-project"
    assert config.env_vars_argument.startswith("^@^")
    parts = config.env_vars_argument[3:].split("@")
    assert "ALLOWED_ORIGINS=https://my-project.web.app,http://localhost:5173" in parts
    assert "DATABASE_BACKEND=firestore" in parts and "STORAGE_BACKEND=gcs" in parts
    assert "AUDIO_BUCKET=my-audio" in parts and "ENVIRONMENT=production" in parts


def test_secret_values_never_appear_in_either_argument() -> None:
    config = build_cloud_run_config(GOOGLE_VALUES)
    printed = config.env_vars_argument + config.secrets_argument

    assert "SECRET-GEMINI-VALUE" not in printed and "SECRET-YOUTUBE-VALUE" not in printed
    assert "GEMINI_API_KEY=" not in config.env_vars_argument
    assert config.secrets_argument == (
        "GEMINI_API_KEY=gemini-api-key:latest,YOUTUBE_API_KEY=youtube-api-key:latest"
    )


def test_supabase_backends_add_their_secrets() -> None:
    values = {
        **GOOGLE_VALUES,
        "DATABASE_BACKEND": "supabase",
        "STORAGE_BACKEND": "supabase",
        "SUPABASE_URL": "https://abc.supabase.co",
        "SUPABASE_DB_URL": "postgresql://user:PASSWORD-VALUE@host/db",
        "SUPABASE_SERVICE_KEY": "KEY-VALUE",
    }

    config = build_cloud_run_config(values)

    assert "SUPABASE_DB_URL=supabase-db-url:latest" in config.secrets_argument
    assert "SUPABASE_SERVICE_KEY=supabase-service-key:latest" in config.secrets_argument
    assert "SUPABASE_URL=https://abc.supabase.co" in config.env_vars_argument
    assert "PASSWORD-VALUE" not in config.env_vars_argument + config.secrets_argument
    assert "KEY-VALUE" not in config.env_vars_argument + config.secrets_argument


def test_local_only_and_unknown_variables_are_left_out() -> None:
    values = {
        **GOOGLE_VALUES,
        "LOCAL_STORAGE_DIR": ".local_storage",
        "PUBLIC_BASE_URL": "http://localhost:8000",
        "OPENROUTER_API_KEY": "SHOULD-NOT-LEAK",
        "SUPABASE_TEST_DB_URL": "postgresql://test",
        "NOT_A_SETTING": "x",
    }

    config = build_cloud_run_config(values)

    for name in (
        "LOCAL_STORAGE_DIR",
        "PUBLIC_BASE_URL",
        "OPENROUTER",
        "SUPABASE_TEST",
        "NOT_A_SETTING",
    ):
        assert name not in config.env_vars_argument + config.secrets_argument
    assert "SHOULD-NOT-LEAK" not in config.env_vars_argument


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"ENVIRONMENT": "local"}, "ENVIRONMENT must be 'production'"),
        ({"DATABASE_BACKEND": "memory"}, "would not start in production"),
        ({"STORAGE_BACKEND": "local"}, "would not start in production"),
        ({"AUTH_BYPASS_UID": "dev-user"}, "would not start in production"),
        ({"GCP_PROJECT_ID": ""}, "GCP_PROJECT_ID must be set"),
    ],
)
def test_values_that_the_app_would_refuse_are_rejected_before_deploying(
    overrides: dict[str, str], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        build_cloud_run_config({**GOOGLE_VALUES, **overrides})


def test_the_delimiter_changes_when_a_value_contains_the_default() -> None:
    config = build_cloud_run_config({**GOOGLE_VALUES, "ALLOWED_ORIGINS": "https://a.example/@x"})

    assert config.env_vars_argument.startswith("^#^")
