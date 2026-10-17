"""Keeps .env.example and the README in step with the real settings."""

import re
from pathlib import Path

from ishaara.settings import Settings

ROOT = Path(__file__).parents[2]
SETTING_NAMES = {name.upper() for name in Settings.model_fields}
TEST_ONLY_NAMES = {"SUPABASE_TEST_DB_URL"}


def _env_example_names() -> set[str]:
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    return {line.split("=", 1)[0] for line in lines if re.match(r"^[A-Z_]+=", line)}


def test_env_example_lists_every_setting_and_nothing_unknown() -> None:
    names = _env_example_names()

    assert SETTING_NAMES - names == set(), "missing from .env.example"
    assert names - SETTING_NAMES - TEST_ONLY_NAMES == set(), "unknown name in .env.example"


def test_env_example_defaults_match_the_code() -> None:
    values = dict(
        line.split("=", 1)
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if re.match(r"^[A-Z_]+=", line)
    )
    defaults = Settings(_env_file=None)

    assert values["PIPELINE_VERSION"] == str(defaults.pipeline_version)
    assert values["MAX_VIDEO_MINUTES"] == str(defaults.max_video_minutes)
    assert values["DAILY_VIDEO_LIMIT"] == str(defaults.daily_video_limit)
    assert values["DAILY_QUESTION_LIMIT"] == str(defaults.daily_question_limit)
    assert values["MAX_REQUEST_BODY_BYTES"] == str(defaults.max_request_body_bytes)
    assert values["DATABASE_BACKEND"] == defaults.database_backend
    assert values["STORAGE_BACKEND"] == defaults.storage_backend


def test_readme_documents_every_setting() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    missing = sorted(name for name in SETTING_NAMES if f"`{name}`" not in readme)

    assert missing == []
