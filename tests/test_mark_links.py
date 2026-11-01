"""COUNCIL-INVENTED-LINK-1 (2026-10-31 sweep, KO): the council called the accrual ratio "the component behind the
MARK flagging the name as 'priced high' at the 97th percentile". The mark comes only from the valuation band."""
from aristos_council.narration_check import check_mark_links

KO = ("The accrual ratio is the component behind the MARK flagging the name as 'priced high' at the 97th "
      "percentile of its own five-year range.")


def test_the_ko_sentence_is_flagged():
    flags = check_mark_links(KO)
    assert len(flags) == 1 and "independent" in flags[0]


def test_a_forensic_mark_attributed_to_the_price_is_flagged():
    assert check_mark_links("Forensic doubts the name because the share price has run up.")


def test_side_by_side_facts_are_not_flagged():
    ok = ("Forensic doubts the accruals. Separately, the valuation band marks the share price as priced high "
          "at the 97th percentile of its own five-year range.")
    assert check_mark_links(ok) == []


def test_prompt_states_the_independence_rule():
    from aristos_council.agents.prompts import MARKS_INDEPENDENT_CONSTRAINT as C
    assert "valuation band" in C and "Forensic" in C and "never" in C.lower()
