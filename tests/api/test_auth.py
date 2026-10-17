from collections.abc import Iterator
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from ishaara.api.dependencies import get_current_user
from ishaara.errors import IshaaraError, problem_json_handler
from ishaara.models.api_models import AuthenticatedUser
from ishaara.settings import Settings, get_settings


@pytest.fixture
def protected_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr("ishaara.api.dependencies._ensure_firebase_app", lambda: None)
    app = FastAPI()
    app.add_exception_handler(IshaaraError, problem_json_handler)

    @app.get("/protected")
    async def protected(
        user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    ) -> dict[str, Any]:
        return {"uid": user.uid, "email": user.email}

    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    yield TestClient(app)


def test_missing_token_returns_401(protected_client: TestClient) -> None:
    response = protected_client.get("/protected")

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_malformed_header_returns_401(protected_client: TestClient) -> None:
    response = protected_client.get("/protected", headers={"Authorization": "Token abc"})

    assert response.status_code == 401


def test_rejected_token_returns_401(
    protected_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject(_: str) -> dict[str, Any]:
        raise ValueError("bad token")

    monkeypatch.setattr("ishaara.api.dependencies.auth.verify_id_token", reject)

    response = protected_client.get("/protected", headers={"Authorization": "Bearer nope"})

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_valid_token_returns_user(
    protected_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "ishaara.api.dependencies.auth.verify_id_token",
        lambda _: {"uid": "user-1", "email": "a@example.com"},
    )

    response = protected_client.get("/protected", headers={"Authorization": "Bearer good"})

    assert response.status_code == 200
    assert response.json() == {"uid": "user-1", "email": "a@example.com"}


def test_bypass_uid_skips_verification(protected_client: TestClient) -> None:
    protected_client.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[attr-defined]
        _env_file=None, auth_bypass_uid="dev-user"
    )

    response = protected_client.get("/protected")

    assert response.status_code == 200
    assert response.json()["uid"] == "dev-user"
