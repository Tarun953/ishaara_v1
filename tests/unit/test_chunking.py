import pytest

from ishaara.pipeline.chunking import ClipWindow, plan_clip_windows


def test_45_minute_video_gives_five_windows() -> None:
    windows = plan_clip_windows(45 * 60, 600, 10)

    assert len(windows) == 5
    assert windows[0] == ClipWindow(0, 600)
    assert windows[1] == ClipWindow(590, 1190)
    assert windows[-1].end_seconds == 45 * 60


def test_short_video_is_one_window() -> None:
    assert plan_clip_windows(120, 600, 10) == [ClipWindow(0, 120)]


def test_video_exactly_one_window_long() -> None:
    assert plan_clip_windows(600, 600, 10) == [ClipWindow(0, 600)]


def test_consecutive_windows_overlap() -> None:
    windows = plan_clip_windows(1500, 600, 10)

    for earlier, later in zip(windows, windows[1:], strict=False):
        assert earlier.end_seconds - later.start_seconds == 10


def test_non_positive_duration_gives_no_windows() -> None:
    assert plan_clip_windows(0, 600, 10) == []


def test_overlap_must_be_smaller_than_window() -> None:
    with pytest.raises(ValueError):
        plan_clip_windows(1000, 600, 600)
