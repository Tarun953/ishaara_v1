"""The gateway must understand the exceptions the real Interactions SDK raises.

These tests build the SDK's own exception classes. Stand-in exceptions hid a production bug:
the SDK's errors are not subclasses of ``google.genai.errors.APIError``, so rate limits and
timeouts were neither retried nor reported cleanly.
"""

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from google.genai import interactions
from google.genai._gaos.lib import compat_errors as sdk

from ishaara.clients.gemini_errors import (
    is_retryable,
    retry_delay_hint_seconds,
    sdk_status_code,
)
from ishaara.clients.gemini_gateway import GoogleGeminiGateway, _gemini_errors
from ishaara.errors import GeminiError
from ishaara.models.domain import NarrationLanguage
from ishaara.pipeline.chunking import ClipWindow
from ishaara.settings import Settings

REQUEST = httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/interactions")
RATE_LIMIT_MESSAGE = (
    "Error code: 429 - Rate limit exceeded for model gemini-3.8-flash-lite-tts "
    "(limit: 3 requests per minute on Free Tier). Please retry in 59s or upgrade your tier."
)


def status_error(
    error_class: type[sdk.APIStatusError], status: int, message: str = "boom", **headers: str
) -> sdk.APIStatusError:
    response = httpx.Response(status, request=REQUEST, headers=headers)
    return error_class(message, response=response, body=None)


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (status_error(sdk.RateLimitError, 429), True),
        (status_error(sdk.InternalServerError, 500), True),
        (status_error(sdk.InternalServerError, 503), True),
        (status_error(sdk.APIStatusError, 408), True),
        (sdk.APITimeoutError(REQUEST), True),
        (sdk.APIConnectionError(request=REQUEST), True),
        (httpx.ConnectError("no route"), True),
        (TimeoutError(), True),
        (status_error(sdk.BadRequestError, 400), False),
        (status_error(sdk.AuthenticationError, 401), False),
        (status_error(sdk.PermissionDeniedError, 403), False),
        (status_error(sdk.NotFoundError, 404), False),
        (status_error(sdk.UnprocessableEntityError, 422), False),
        (ValueError("not a Gemini error"), False),
    ],
)
def test_which_real_sdk_errors_are_retried(error: BaseException, retryable: bool) -> None:
    assert is_retryable(error) is retryable


def test_status_codes_are_read_from_the_sdk_errors() -> None:
    assert sdk_status_code(status_error(sdk.RateLimitError, 429)) == 429
    assert sdk_status_code(sdk.APITimeoutError(REQUEST)) is None
    assert sdk_status_code(ValueError("x")) is None


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (status_error(sdk.RateLimitError, 429, RATE_LIMIT_MESSAGE), 59.0),
        (status_error(sdk.RateLimitError, 429, "retry in 300s"), 70.0),  # capped
        (status_error(sdk.RateLimitError, 429, "slow down", **{"retry-after": "12"}), 12.0),
        (status_error(sdk.RateLimitError, 429, "no hint at all"), None),
        (ValueError("retry in 5s"), 5.0),
    ],
)
def test_the_wait_gemini_asks_for_is_read_and_capped(
    error: BaseException, expected: float | None
) -> None:
    assert retry_delay_hint_seconds(error) == expected


@pytest.mark.parametrize(
    ("error", "fragment"),
    [
        (status_error(sdk.RateLimitError, 429), "status 429"),
        (status_error(sdk.BadRequestError, 400), "status 400"),
        (status_error(sdk.InternalServerError, 503), "status 503"),
        (sdk.APITimeoutError(REQUEST), "in time"),
        (sdk.APIConnectionError(request=REQUEST), "Could not reach"),
    ],
)
def test_real_sdk_errors_become_a_clean_gemini_error(error: Exception, fragment: str) -> None:
    with pytest.raises(GeminiError) as caught, _gemini_errors():
        raise error

    assert caught.value.code == "gemini_error"
    assert fragment in caught.value.detail
    assert caught.value.status == 502


def test_an_unrelated_exception_is_not_swallowed() -> None:
    with pytest.raises(KeyError), _gemini_errors():
        raise KeyError("a genuine bug")


# ---- through the real retry layer ----


class _FakeInteraction:
    output_text = '{"transcript_segments": [], "visual_events": []}'
    usage = None


def _gateway(
    monkeypatch: pytest.MonkeyPatch, behaviour: Any
) -> tuple[GoogleGeminiGateway, list[float]]:
    delays: list[float] = []

    async def record_sleep(delay: float, *args: object) -> None:
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    monkeypatch.setattr(interactions, "Interaction", _FakeInteraction)
    gateway = GoogleGeminiGateway(Settings(_env_file=None, gemini_api_key="test-key"))
    gateway._sdk_client = SimpleNamespace(interactions=SimpleNamespace(create=behaviour))  # type: ignore[assignment]
    return gateway, delays


async def test_a_rate_limit_is_retried_after_the_wait_gemini_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def create(**kwargs: object) -> _FakeInteraction:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise status_error(sdk.RateLimitError, 429, RATE_LIMIT_MESSAGE)
        return _FakeInteraction()

    gateway, delays = _gateway(monkeypatch, create)

    result = await gateway.analyze_clip("dQw4w9WgXcQ", ClipWindow(0, 60))

    assert calls == 2
    assert result.analysis.visual_events == []
    assert 59.0 in delays  # waited as long as Gemini asked, not a second or two


async def test_a_rate_limit_that_never_clears_ends_as_a_clean_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def create(**kwargs: object) -> _FakeInteraction:
        nonlocal calls
        calls += 1
        raise status_error(sdk.RateLimitError, 429, RATE_LIMIT_MESSAGE)

    gateway, _ = _gateway(monkeypatch, create)

    with pytest.raises(GeminiError) as caught:
        await gateway.analyze_clip("dQw4w9WgXcQ", ClipWindow(0, 60))

    assert calls == 3
    assert caught.value.code == "gemini_error" and "status 429" in caught.value.detail


async def test_the_sdk_timeout_is_retried_then_reported_as_not_responding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def create(**kwargs: object) -> _FakeInteraction:
        nonlocal calls
        calls += 1
        raise sdk.APITimeoutError(REQUEST)

    gateway, _ = _gateway(monkeypatch, create)

    with pytest.raises(GeminiError) as caught:
        await gateway.analyze_clip("dQw4w9WgXcQ", ClipWindow(0, 60))

    assert calls == 3 and "in time" in caught.value.detail


async def test_a_bad_request_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def create(**kwargs: object) -> _FakeInteraction:
        nonlocal calls
        calls += 1
        raise status_error(sdk.BadRequestError, 400, "Audio delivery mode is not supported.")

    gateway, _ = _gateway(monkeypatch, create)

    with pytest.raises(GeminiError) as caught:
        await gateway.synthesize_speech("hello", NarrationLanguage.ENGLISH)

    assert calls == 1 and "status 400" in caught.value.detail


async def test_every_speech_attempt_takes_a_slot_from_the_rate_limiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    acquisitions = 0

    class SpyLimiter:
        async def acquire(self) -> None:
            nonlocal acquisitions
            acquisitions += 1

    def create(**kwargs: object) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise status_error(sdk.RateLimitError, 429, RATE_LIMIT_MESSAGE)
        audio = SimpleNamespace(data=b"RIFFdata")
        return type("Spoken", (_FakeInteraction,), {"output_audio": audio})()

    gateway, _ = _gateway(monkeypatch, create)
    gateway._speech_limiter = SpyLimiter()  # type: ignore[assignment]

    await gateway.synthesize_speech("hello", NarrationLanguage.ENGLISH)

    assert calls == 2 and acquisitions == 2  # the retry counts against the per-minute cap too
