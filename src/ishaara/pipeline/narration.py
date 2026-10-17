"""Pure validation of Gemini's narration output."""

from collections.abc import Sequence

from ishaara.models.gemini_schemas import NarrationBatch


def lines_by_cue_id(
    batch: NarrationBatch, expected_cue_ids: Sequence[str]
) -> dict[str, str] | None:
    """Return narration text keyed by cue id if the batch is complete and exact.

    Args:
        batch: Gemini's narration output.
        expected_cue_ids: The cue ids that were sent in.

    Returns:
        The text for each cue, or ``None`` if any expected id is missing, repeated,
        unexpected, or has empty text.
    """
    returned_ids = [line.cue_id for line in batch.lines]
    if sorted(returned_ids) != sorted(expected_cue_ids):
        return None
    lines = {line.cue_id: line.narration_text.strip() for line in batch.lines}
    if len(lines) != len(expected_cue_ids) or not all(lines.values()):
        return None
    return lines
