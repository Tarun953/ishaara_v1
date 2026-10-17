import pytest
from pydantic import ValidationError

from ishaara.settings import Settings


def test_defaults_match_plan() -> None:
    settings = Settings(_env_file=None)

    assert settings.analysis_model == "gemini-3.8-flash"
    assert settings.allowed_origins == ["http://localhost:5173"]


def test_allowed_origins_split_on_commas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.web.app, http://localhost:5173")

    assert Settings(_env_file=None).allowed_origins == [
        "https://a.web.app",
        "http://localhost:5173",
    ]


def test_auth_bypass_refused_in_production() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production", auth_bypass_uid="dev-user")
