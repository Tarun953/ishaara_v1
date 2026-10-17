import pytest

from ishaara.pipeline.chunking import ClipWindow
from ishaara.pipeline.timestamps import normalize_clip_timestamps, parse_timestamp


@pytest.mark.parametrize(
    ("raw_timestamp", "expected_seconds"),
    [("00:00", 0), ("12:40", 760), ("1:02:03", 3723), (" 05:30 ", 330)],
)
def test_parse_valid_timestamps(raw_timestamp: str, expected_seconds: float) -> None:
    assert parse_timestamp(raw_timestamp) == expected_seconds


@pytest.mark.parametrize("raw_timestamp", ["", "12", "a:b", "1:2:3:4", "-1:00"])
def test_parse_invalid_timestamps(raw_timestamp: str) -> None:
    with pytest.raises(ValueError):
        parse_timestamp(raw_timestamp)


def test_absolute_timestamps_are_kept() -> None:
    window = ClipWindow(600, 1200)

    assert normalize_clip_timestamps([650.0, 900.0, 1190.0], window) == [650.0, 900.0, 1190.0]


def test_clip_relative_timestamps_get_window_start_added() -> None:
    window = ClipWindow(600, 1200)

    assert normalize_clip_timestamps([50.0, 300.0, 590.0], window) == [650.0, 900.0, 1190.0]


def test_first_window_is_never_shifted() -> None:
    assert normalize_clip_timestamps([10.0, 100.0], ClipWindow(0, 600)) == [10.0, 100.0]


def test_out_of_window_timestamps_are_dropped() -> None:
    window = ClipWindow(600, 1200)

    assert normalize_clip_timestamps([100.0, 650.0, 2000.0], window) == [None, 650.0, None]


def test_empty_input() -> None:
    assert normalize_clip_timestamps([], ClipWindow(600, 1200)) == []
