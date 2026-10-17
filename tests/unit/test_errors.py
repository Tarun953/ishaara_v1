from fastapi import FastAPI
from fastapi.testclient import TestClient

from ishaara.errors import IshaaraError, problem_json_handler


def test_ishaara_error_renders_problem_json() -> None:
    app = FastAPI()
    app.add_exception_handler(IshaaraError, problem_json_handler)

    @app.get("/boom")
    async def boom() -> None:
        raise IshaaraError("video_too_long", 422, "Video is too long", "74 minutes; limit is 45.")

    response = TestClient(app).get("/boom")

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json() == {
        "type": "https://ishaara.app/errors/video_too_long",
        "title": "Video is too long",
        "status": 422,
        "detail": "74 minutes; limit is 45.",
        "code": "video_too_long",
    }


def _app_with_all_handlers() -> FastAPI:
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from ishaara.errors import (
        http_exception_handler,
        unexpected_error_handler,
        validation_error_handler,
    )

    app = FastAPI()
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)

    @app.get("/crash")
    async def crash() -> None:
        raise RuntimeError("secret internals")

    @app.get("/needs-int/{number}")
    async def needs_int(number: int) -> int:
        return number

    return app


def test_unknown_route_is_a_404_problem() -> None:
    response = TestClient(_app_with_all_handlers()).get("/nowhere")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_bad_path_parameter_is_a_422_problem() -> None:
    response = TestClient(_app_with_all_handlers()).get("/needs-int/abc")

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"


def test_unexpected_exception_is_a_500_that_hides_internals() -> None:
    client = TestClient(_app_with_all_handlers(), raise_server_exceptions=False)

    response = client.get("/crash")

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "secret" not in response.text
