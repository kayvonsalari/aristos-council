"""NARR-CONTEXT-1 — the narrator sees the agreement row before it writes.

A name reaches the narrator because the voting lenses agreed on it, and it often arrives
carrying a mark: "doubted by Forensic", or "priced high: 99th percentile of its own 5-year
range". Those are the two facts a reader of the shortlist arrives with — and until this
item the writer was handed neither, so the prose could not open on why the name is there
and could not meet the doubt sitting beside it. Worse, it read as an unqualified case for
a name the run had qualified.

The cross-lens block (NARR-UNION-1) gives every lens's VERDICT; this gives what the run
CONCLUDED from them. Neither is derivable from the other.

No live LLM call anywhere in this file (CLAUDE.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pytest

from aristos_council.narration_check import mark_annotation, unaddressed_marks
from aristos_council.pipeline import agreement_row_for
from aristos_council.tools.valuation_band import ValuationBand


# --------------------------------------------------------------------------- #
# A run shaped exactly as much as the block reads
# --------------------------------------------------------------------------- #
@dataclass
class _Strat:
    kind: str = "selector"
    factors: list = field(default_factory=list)


@dataclass
class _Row:
    ticker: str
    verdict: str = "hold"
    cohort_position: Optional[int] = None
    valuation_band: Optional[ValuationBand] = None
    excluded: bool = False
    factor_ranks: dict = field(default_factory=dict)
    imputed_factors: tuple = ()


@dataclass
class _Res:
    ranked: list
    rank_strategy: _Strat = field(default_factory=_Strat)
    names: dict = field(default_factory=dict)
    excluded: list = field(default_factory=list)
    unrateable: list = field(default_factory=list)


LABELS = {"a_v1": "Cyclical Income", "b_v1": "Magic Formula RAW", "chk_v1": "Forensic"}


def _band(pct=50.0):
    return ValuationBand(percentile=pct, basis="ev_ebit", current=12.0,
                         median_multiple=10.0, months_covered=61, months_total=61,
                         window_years=5)


def _run(**rows_by_lens):
    from aristos_council.pipeline import MultiStrategyResult, lens_agreement

    results = {}
    for sid, rows in rows_by_lens.items():
        results[sid] = _Res(list(rows),
                            _Strat("check" if sid == "chk_v1" else "selector"))
    built = MultiStrategyResult(
        strategy_ids=list(results), strategy_names={k: LABELS[k] for k in results},
        results=results, rows=[], meta={})
    return MultiStrategyResult(
        strategy_ids=built.strategy_ids, strategy_names=built.strategy_names,
        results=built.results, rows=[], meta={},
        lens_agreement=lens_agreement(built))


# --------------------------------------------------------------------------- #
# The pack carries the row
# --------------------------------------------------------------------------- #
def test_the_pack_carries_the_votes_and_who_cast_them():
    result = _run(a_v1=[_Row("SU", "buy", 1)], b_v1=[_Row("SU", "buy", 2)])
    row = agreement_row_for(result, "SU")
    assert row["buy_votes"] == 2 and row["n_voting"] == 2
    assert row["buy_lenses"] == ["Cyclical Income", "Magic Formula RAW"]


def test_a_check_reads_in_its_OWN_words():
    """"Forensic: doubted", never "Forensic: SELL" — the second is the sentence
    CHECK-WORDS-1 spent a whole item removing from the report, and it would be worse in a
    model's prompt than on a page."""
    result = _run(a_v1=[_Row("SU", "buy", 1)], chk_v1=[_Row("SU", "sell", 9)])
    row = agreement_row_for(result, "SU")
    assert row["checks"] == [{"lens": "Forensic", "reading": "doubted"}]
    assert "SELL" not in str(row["checks"])


def test_the_marks_are_in_the_pack():
    result = _run(a_v1=[_Row("SU", "buy", 1, _band(99))],
                  chk_v1=[_Row("SU", "sell", 9)])
    marks = agreement_row_for(result, "SU")["marks"]
    assert "doubted by Forensic" in marks
    assert any("priced high: 99th percentile" in m for m in marks)


def test_the_band_s_ARITHMETIC_never_reaches_the_pack():
    """Only the mark TEXT goes to a model. An implied price comes back quoted as a target
    however it is labelled, which is why the reversion value has never been shown to one."""
    result = _run(a_v1=[_Row("SU", "buy", 1, _band(99))])
    blob = str(agreement_row_for(result, "SU"))
    for forbidden in ("reversion", "implied", "median_multiple", "12.0"):
        assert forbidden not in blob


def test_a_single_lens_run_carries_NO_block_so_that_prompt_is_unchanged():
    from aristos_council.pipeline import MultiStrategyResult

    assert agreement_row_for(MultiStrategyResult(
        strategy_ids=[], strategy_names={}, results={}, rows=[], meta={}), "SU") == {}


def test_a_name_NOT_on_the_agreement_table_carries_no_block():
    result = _run(a_v1=[_Row("OTHER", "buy", 1)])
    assert agreement_row_for(result, "SU") == {}


# --------------------------------------------------------------------------- #
# The block, as the narrator receives it
# --------------------------------------------------------------------------- #
def test_the_block_opens_with_what_the_run_concluded():
    from aristos_council.agents.nodes import _agreement_block

    class _State:
        agreement_row = {"buy_votes": 2, "n_voting": 2,
                         "buy_lenses": ["Cyclical Income", "Magic Formula RAW"],
                         "sell_lenses": [],
                         "checks": [{"lens": "Forensic", "reading": "doubted"}],
                         "marks": ["doubted by Forensic"]}

    block = _agreement_block(_State())
    assert "WHAT THE RUN CONCLUDED ABOUT THIS NAME" in block
    assert "BUY votes: 2 of 2 (Cyclical Income, Magic Formula RAW)" in block
    assert "Forensic: doubted" in block
    assert "MARK: doubted by Forensic" in block
    assert "ADDRESS EVERY MARK ABOVE" in block


def test_an_unmarked_name_gets_no_address_the_marks_instruction():
    """An instruction about nothing is noise, and noise in a prompt costs attention."""
    from aristos_council.agents.nodes import _agreement_block

    class _State:
        agreement_row = {"buy_votes": 1, "n_voting": 2, "buy_lenses": ["Cyclical Income"],
                         "sell_lenses": [], "checks": [], "marks": []}

    block = _agreement_block(_State())
    assert "BUY votes: 1 of 2" in block
    assert "ADDRESS EVERY MARK" not in block


def test_no_agreement_row_renders_NOTHING():
    from aristos_council.agents.nodes import _agreement_block

    class _State:
        agreement_row = {}

    assert _agreement_block(_State()) == ""


def test_the_block_is_placed_BEFORE_the_cross_lens_verdicts():
    """It is the reading the report puts on those verdicts, so it frames them rather than
    following them."""
    import inspect

    from aristos_council.agents import nodes

    src = inspect.getsource(nodes)
    line = next(l for l in src.splitlines() if "_cross_lens_block(state)" in l
                and "def " not in l)
    assert line.index("_agreement_block") < line.index("_cross_lens_block")


# --------------------------------------------------------------------------- #
# A mark must be addressed
# --------------------------------------------------------------------------- #
MARKS = ["doubted by Forensic", "priced high: 99th percentile of its own 5-year range"]


def test_a_narration_that_IGNORES_a_mark_is_flagged():
    missing = unaddressed_marks(
        "Suncor ranks well on both lenses and the dividend is covered.", MARKS)
    assert missing == MARKS
    assert mark_annotation(missing).startswith("[⚠ mark not addressed:")


def test_a_narration_that_ADDRESSES_them_passes():
    text = ("Forensic doubts this name: its accrual ratio is high. It is also priced "
            "high against its own five years.")
    assert unaddressed_marks(text, MARKS) == []
    assert mark_annotation([]) == ""


def test_addressing_ONE_mark_does_not_excuse_the_other():
    text = "Forensic doubts the accruals, which is the one thing to weigh here."
    assert unaddressed_marks(text, MARKS) == [MARKS[1]]


def test_the_test_is_generous_about_WORDS_and_strict_about_SUBSTANCE():
    """A writer who says "Forensic doubts the accruals" has addressed it; one who never
    types "Forensic" has not. The rule is about whether the reader is told, not about
    matching a phrase."""
    assert unaddressed_marks("Forensic is unconvinced by the cash conversion.",
                             ["doubted by Forensic"]) == []
    assert unaddressed_marks("One check was unconvinced.",
                             ["doubted by Forensic"]) == ["doubted by Forensic"]


def test_no_marks_means_nothing_to_address():
    assert unaddressed_marks("Anything at all.", []) == []
    assert unaddressed_marks("Anything at all.", None) == []


def test_a_mark_the_rule_does_not_police_is_not_demanded():
    """"band not evaluated" and the factor-coverage note are disclosures about the RUN,
    not doubts about the company — a narration is not wrong for leaving them out."""
    assert unaddressed_marks("Nothing about the band here.", [
        "band not evaluated — insufficient history: 1.1y",
        "Cyclical Income: ranked on 2 of 3 factors"]) == []
