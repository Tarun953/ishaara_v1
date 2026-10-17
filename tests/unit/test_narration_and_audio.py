import pytest

from ishaara.models.domain import NarrationCue, NarrationLanguage, ReferentKind
from ishaara.models.gemini_schemas import NarrationBatch, NarrationLine
from ishaara.pipeline.audio import wav_duration_seconds
from ishaara.pipeline.narration import lines_by_cue_id
from ishaara.pipeline.prompts import build_narration_prompt
from tests.conftest import make_wav


def _batch(*pairs: tuple[str, str]) -> NarrationBatch:
    return NarrationBatch(lines=[NarrationLine(cue_id=c, narration_text=t) for c, t in pairs])


def test_complete_batch_is_accepted_and_trimmed() -> None:
    batch = _batch(("cue-0001", " A "), ("cue-0002", "B"))

    assert lines_by_cue_id(batch, ["cue-0001", "cue-0002"]) == {"cue-0001": "A", "cue-0002": "B"}


@pytest.mark.parametrize(
    "pairs",
    [
        [("cue-0001", "A")],  # missing cue-0002
        [("cue-0001", "A"), ("cue-0002", "B"), ("cue-0002", "B again")],  # repeated
        [("cue-0001", "A"), ("cue-0009", "B")],  # unexpected id
        [("cue-0001", "A"), ("cue-0002", "   ")],  # empty text
    ],
)
def test_incomplete_or_wrong_batches_are_rejected(pairs: list[tuple[str, str]]) -> None:
    assert lines_by_cue_id(_batch(*pairs), ["cue-0001", "cue-0002"]) is None


def test_wav_duration() -> None:
    assert wav_duration_seconds(make_wav(1.5)) == pytest.approx(1.5)


def test_invalid_wav_raises() -> None:
    with pytest.raises(ValueError):
        wav_duration_seconds(b"not a wav file")


def test_narration_prompt_lists_cues_and_language_rule() -> None:
    cue = NarrationCue(
        cue_id="cue-0001",
        start_seconds=65.0,
        spoken_phrase="this",
        referent_kind=ReferentKind.DIAGRAM,
        board_description="A circle with a tick mark.",
        gap_summary="A tick mark is drawn at the bottom.",
        confidence=0.9,
    )

    english = build_narration_prompt([cue], NarrationLanguage.ENGLISH)
    hindi = build_narration_prompt([cue], NarrationLanguage.HINDI)

    assert "cue-0001 [01:05] (diagram)" in english
    assert "A tick mark is drawn at the bottom." in english
    assert "in English" in english and "On the board:" in english
    assert "in Hindi" in hindi and "Devanagari" in hindi
