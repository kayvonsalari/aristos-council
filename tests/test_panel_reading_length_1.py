"""PANEL-READING-LENGTH-1 - the panel reading's length is guidance, never a veto.

Live (MSFT, 2026-10-22 sweep) the page rendered "_The reading was withheld: it is 90 words; the
limit is 80_". The prompt still asks for 80 words; a longer paragraph is shown, cut at a sentence
end within 80 words when one allows, else whole. The other withhold reasons still withhold."""
from __future__ import annotations

import pytest

from aristos_council.judges import narrate, panel, render
from tests.test_judge_panel import _R, _Runner, _result, _stable

pytestmark = pytest.mark.min_group(3)


def _words(n: int, start: int = 0) -> str:
    return " ".join(f"w{i}" for i in range(start, start + n))


# 50 + 30 = 80 words in two sentences, then a 10-word third: 90 words, like the MSFT reading
NINETY = f"{_words(49)} first. {_words(29, 100)} second. {_words(9, 200)} third."


def test_the_prompt_still_asks_for_80_words():
    assert "at most 80 words" in narrate.PROMPT_PATH.read_text(encoding="utf-8")
    assert narrate.MAX_WORDS == 80


def test_length_alone_is_no_longer_a_reason_to_withhold():
    assert len(NINETY.split()) == 90
    assert narrate.check_reading(NINETY, {"BUY", "HOLD"}) == []


def test_a_90_word_reading_is_shown_trimmed_at_a_sentence_end():
    runner = _Runner(NINETY)
    res = _result(panel.DISAGREES, _stable("HOLD", "HOLD"))
    out = narrate.write_panel_reading(res, _R(), runner=runner)
    assert out.paragraph == f"{_words(49)} first. {_words(29, 100)} second."
    assert len(out.paragraph.split()) == 80 and not out.note and out.calls == 1
    res.reading = out
    md = "\n".join(render.markdown_lines(res))
    assert out.paragraph in md and "withheld" not in md and "the limit is" not in md


def test_a_long_reading_with_no_sentence_end_inside_80_words_is_shown_whole():
    whole = _words(89) + " end."                 # one 90-word sentence: no cut keeps a sentence
    out = narrate.write_panel_reading(_result(panel.DISAGREES, _stable("HOLD")), _R(), runner=_Runner(whole))
    assert out.paragraph == whole and not out.note and out.calls == 1


def test_a_decimal_point_is_not_a_sentence_end():
    text = f"{_words(70)} cash flow was $5.3bn {_words(20, 300)}."
    assert narrate.fit_length(text) == text      # no sentence end inside 80 words: whole, never "$5."


def test_a_short_reading_is_unchanged():
    assert narrate.fit_length("Two judges differ.  On debt. ") == "Two judges differ. On debt."


@pytest.mark.parametrize("bad", ["You should buy this company now.",
                                 "The combined verdict of the panel is hold.",
                                 "The panel says SELL.",
                                 "First point.\n- second point"])
def test_the_other_withhold_reasons_still_withhold(bad):
    out = narrate.write_panel_reading(_result(panel.DISAGREES, _stable("HOLD", "HOLD")), _R(),
                                      runner=_Runner(bad))
    assert not out.paragraph and out.note.startswith("The reading was withheld") and out.calls == 2


def test_a_long_reading_that_recommends_is_still_withheld_and_not_for_its_length():
    bad = NINETY + " You should buy it."
    out = narrate.write_panel_reading(_result(panel.DISAGREES, _stable("HOLD")), _R(), runner=_Runner(bad))
    assert not out.paragraph and "recommendation" in out.note and "words" not in out.note
