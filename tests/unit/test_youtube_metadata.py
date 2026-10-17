import httpx
import pytest
import respx

from ishaara.clients.youtube_metadata import YOUTUBE_VIDEOS_URL, YouTubeMetadataClient
from ishaara.errors import IshaaraError, VideoNotFoundError
from ishaara.models.api_models import YouTubeVideoMetadata

VIDEO_ID = "dQw4w9WgXcQ"


def _item(
    privacy: str = "public", live: str = "none", duration: str = "PT1H2M3S", embeddable: bool = True
) -> dict[str, object]:
    return {
        "items": [
            {
                "snippet": {
                    "title": "Lecture",
                    "channelTitle": "Prof",
                    "liveBroadcastContent": live,
                },
                "contentDetails": {"duration": duration},
                "status": {"privacyStatus": privacy, "embeddable": embeddable},
            }
        ]
    }


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("asyncio.sleep", instant_sleep)


async def _fetch() -> YouTubeVideoMetadata:
    async with httpx.AsyncClient() as http_client:
        return await YouTubeMetadataClient("key", http_client).fetch(VIDEO_ID)


@respx.mock
async def test_public_video() -> None:
    respx.get(YOUTUBE_VIDEOS_URL).mock(return_value=httpx.Response(200, json=_item()))

    metadata = await _fetch()

    assert metadata.duration_seconds == 3723
    assert metadata.privacy_status == "public"


@respx.mock
async def test_private_video() -> None:
    respx.get(YOUTUBE_VIDEOS_URL).mock(return_value=httpx.Response(200, json=_item("private")))

    assert (await _fetch()).privacy_status == "private"


@respx.mock
async def test_live_video() -> None:
    respx.get(YOUTUBE_VIDEOS_URL).mock(return_value=httpx.Response(200, json=_item(live="live")))

    assert (await _fetch()).live_broadcast_content == "live"


@respx.mock
async def test_retries_on_503_then_succeeds() -> None:
    route = respx.get(YOUTUBE_VIDEOS_URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=_item())]
    )

    await _fetch()

    assert route.call_count == 2


@respx.mock
async def test_does_not_retry_on_403() -> None:
    route = respx.get(YOUTUBE_VIDEOS_URL).mock(return_value=httpx.Response(403))

    with pytest.raises(IshaaraError) as error_info:
        await _fetch()

    assert error_info.value.code == "youtube_unavailable"
    assert route.call_count == 1


@respx.mock
async def test_video_not_found() -> None:
    respx.get(YOUTUBE_VIDEOS_URL).mock(return_value=httpx.Response(200, json={"items": []}))

    with pytest.raises(VideoNotFoundError):
        await _fetch()
