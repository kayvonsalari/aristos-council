"""B25-2 - the saved MSFT council text (2026-10-08 11:59, Kayvon's paid run) replayed offline.

The live run stamped 7 flags; 4 were false. The Batch 24 heading rule never saw a heading on live output because
the checker reads ``narration_prose`` (flattened), where the page's "<Lens> - why" headings did not exist. The
replay therefore goes through ``narration_prose`` exactly as the live path does."""
import json
from pathlib import Path

from aristos_council.agents.schemas import FactorRank, LensAttributionItem, Narration
from aristos_council.narration_check import (check_cross_lens, check_rank_attribution, check_would_rank)
from aristos_council.narration_render import narration_prose

FIX = json.loads((Path(__file__).parent / "fixtures" / "narration" / "msft_20261008.json").read_text(encoding="utf-8"))
VERDICTS = FIX["verdicts"]


def _flags(text, verdicts=None):
    v = verdicts or VERDICTS
    return check_cross_lens(text, v) + check_rank_attribution(text, v) + check_would_rank(text, v)


def _narration():
    return Narration(
        lens_attribution=[LensAttributionItem(
            lens=a["lens"], reasoning=a["reasoning"],
            factor_ranks=[FactorRank(factor=f["factor"], rank=f["rank"], cohort_size=f["cohort_size"])
                          for f in a["factor_ranks"]]) for a in FIX["lens_attribution"]],
        disagreement_note=FIX["disagreement_note"])


def test_the_live_run_stamped_seven_flags_four_of_them_false():
    stamps = FIX["original_stamps"]
    assert len(stamps) == 7
    assert sum("near-verbatim in 2 specialists" in s for s in stamps) == 3


def test_the_live_prose_replays_with_no_false_flags():
    assert _flags(narration_prose(_narration())) == []


def test_the_prose_carries_each_lens_heading_the_page_shows():
    prose = narration_prose(_narration())
    for lens in ("Earnings Power Value", "Forensic", "Quality"):
        assert f"**{lens} — why**" in prose


def test_the_rendered_text_replays_clean_too():
    assert _flags(FIX["narrative_without_flags"]) == []


def test_true_positive_a_rank_with_no_lens_and_no_heading_is_still_flagged():
    assert check_rank_attribution("MSFT's reading placed it 2nd of 13 on that measure.", VERDICTS)


def test_true_positive_the_would_rank_lens_in_its_own_section_is_still_flagged():
    text = "**Cyclical Income — why**\n\nMSFT ranked 10th of 13 here and was bought."
    assert check_would_rank(text, VERDICTS)


def test_true_positive_a_rank_with_no_factor_and_no_section_matches_the_would_rank():
    assert check_would_rank("MSFT came 4th of 14 overall.", VERDICTS)


def test_a_factor_rank_is_not_another_lenss_would_rank_even_with_no_heading():
    sentence = "Net debt relative to operating profit at 0.3x ranked 10th of 13, a weaker position."
    assert check_would_rank(sentence, VERDICTS) == []
    other = "Something else entirely ranked 10th of 13."
    assert check_would_rank(other, VERDICTS)
