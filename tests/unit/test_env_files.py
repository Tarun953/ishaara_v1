from pathlib import Path

import pytest

from ishaara.settings import ENV_NAME_VARIABLE, Settings, env_files


def test_default_is_the_shared_file_then_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_NAME_VARIABLE, raising=False)

    assert env_files() == (".env", ".env.local")


def test_the_environment_variable_picks_the_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_NAME_VARIABLE, "prod")

    assert env_files() == (".env", ".env.prod")
    assert env_files("staging") == (".env", ".env.staging")


@pytest.mark.parametrize("bad_name", ["", "../secrets", "a b", "prod;rm", "x" * 40, "a/b"])
def test_unsafe_environment_names_are_rejected(bad_name: str) -> None:
    with pytest.raises(ValueError, match=ENV_NAME_VARIABLE):
        env_files(bad_name)


def test_the_environment_file_overrides_the_shared_one_and_missing_files_are_skipped(
    tmp_path: Path,
) -> None:
    shared = tmp_path / ".env"
    shared.write_text("TTS_VOICE=Kore\nLOG_LEVEL=INFO\n")
    prod = tmp_path / ".env.prod"
    prod.write_text("LOG_LEVEL=WARNING\n")

    merged = Settings(_env_file=(str(shared), str(prod), str(tmp_path / ".env.absent")))

    assert merged.log_level == "WARNING"  # the later file wins
    assert merged.tts_voice == "Kore"  # the shared value still applies


def test_real_environment_variables_beat_the_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = tmp_path / ".env"
    shared.write_text("LOG_LEVEL=INFO\n")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    assert Settings(_env_file=str(shared)).log_level == "DEBUG"
