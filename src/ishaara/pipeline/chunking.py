"""Pure planning of overlapping clip windows."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ClipWindow:
    """A slice of the video analyzed in one Gemini call.

    Attributes:
        start_seconds: Window start, in absolute video time.
        end_seconds: Window end, in absolute video time.
    """

    start_seconds: int
    end_seconds: int

    @property
    def length_seconds(self) -> int:
        """Return the window length in seconds."""
        return self.end_seconds - self.start_seconds


def plan_clip_windows(
    duration_seconds: int, window_seconds: int, overlap_seconds: int
) -> list[ClipWindow]:
    """Split a video into overlapping windows.

    Args:
        duration_seconds: Total video length.
        window_seconds: Length of each window.
        overlap_seconds: Overlap between consecutive windows, so a pointing word on a
            boundary is seen whole.

    Returns:
        Windows in time order; the last one ends at ``duration_seconds``. Empty if the
        duration is not positive.

    Raises:
        ValueError: If the overlap is not smaller than the window.
    """
    if overlap_seconds >= window_seconds:
        raise ValueError("overlap_seconds must be smaller than window_seconds")
    windows: list[ClipWindow] = []
    start_seconds = 0
    while start_seconds < duration_seconds:
        end_seconds = min(start_seconds + window_seconds, duration_seconds)
        windows.append(ClipWindow(start_seconds, end_seconds))
        if end_seconds >= duration_seconds:
            break
        start_seconds = end_seconds - overlap_seconds
    return windows
