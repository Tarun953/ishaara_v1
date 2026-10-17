import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from ishaara.main import create_app


@pytest.mark.parametrize("path", ["/health", "/healthz"])
def test_both_health_paths_return_ok(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "version" in response.json()


def test_the_startup_log_names_the_backends_and_the_env_files_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # configure_logging would replace the capture that capture_logs installs
    monkeypatch.setattr("ishaara.main.configure_logging", lambda *args, **kwargs: None)

    with capture_logs() as logs, TestClient(create_app()):
        pass

    loaded = [entry for entry in logs if entry["event"] == "settings_loaded"]
    assert len(loaded) == 1
    assert {"environment", "database_backend", "storage_backend", "env_files"} <= set(loaded[0])
    assert "env_name" not in loaded[0]
    assert isinstance(loaded[0]["env_files"], list)
