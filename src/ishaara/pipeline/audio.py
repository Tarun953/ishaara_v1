"""Pure helpers for WAV audio."""

import io
import wave


def wav_duration_seconds(wav_bytes: bytes) -> float:
    """Return the length of a WAV file in seconds.

    Args:
        wav_bytes: The complete WAV file.

    Returns:
        Duration in seconds.

    Raises:
        ValueError: If the bytes are not a readable WAV file.
    """
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
            return wav_file.getnframes() / wav_file.getframerate()
    except (wave.Error, EOFError, ZeroDivisionError) as error:
        raise ValueError("Not a valid WAV file") from error
