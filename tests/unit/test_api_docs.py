"""Guards against docs/API.md and the Postman collection drifting from the real routes."""

import json
import re
from pathlib import Path

from ishaara.main import create_app

DOCS = Path(__file__).parents[2] / "docs"
FRAMEWORK_ROUTES = {"/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"}


def _implemented_routes() -> set[tuple[str, str]]:
    schema = create_app().openapi()
    return {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
        if path not in FRAMEWORK_ROUTES
    }


def test_every_route_is_documented_in_api_md() -> None:
    markdown = (DOCS / "API.md").read_text(encoding="utf-8")

    missing = [
        f"{method} {path}"
        for method, path in sorted(_implemented_routes())
        if f"`{method} {path}`" not in markdown
    ]

    assert missing == []


def test_every_route_is_in_the_postman_collection() -> None:
    collection = json.loads((DOCS / "postman" / "ishaara.postman_collection.json").read_text())
    requests = [
        item["request"]
        for folder in collection["item"]
        for item in folder["item"]
        if isinstance(item["request"]["url"], dict)  # string URLs are absolute (e.g. cue audio)
    ]
    in_collection = {
        (
            request["method"],
            re.sub(r"\{\{(\w+)\}\}", r"{\1}", "/" + "/".join(request["url"]["path"])),
        )
        for request in requests
    }
    normalised = {(method, re.sub(r"\{\w+\}", "{}", path)) for method, path in in_collection}

    for method, path in _implemented_routes():
        assert (method, re.sub(r"\{\w+\}", "{}", path)) in normalised, f"{method} {path}"
