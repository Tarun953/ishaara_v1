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
