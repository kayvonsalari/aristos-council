"""FACTOR-MARK-3 — the factor table and the marker must agree.

Three surfaces answer the same question — *how many of this lens's factors did this name
actually carry a measured value for?* — and each worked it out for itself:

  * the grid cell's " · ranked on 2 of 3 factors" marker,
  * the agreement table's note,
  * the per-lens factor table's cell.

Same arithmetic in three places is a contradiction waiting to be reported, and one was:
a name shown as "ranked on 2 of 3" sat beside a table whose third cell looked like a
plain number, and Suncor carried the marker in its grid cell and nothing in the agreement
table. The count now lives in ``rank_engine.factor_measurement`` and nowhere else, and
the table says the WORD rather than a footnote symbol so the two agree in language too.

Display only. Nothing here changes a rank, a verdict or a score.
"""

from __future__ import annotations

import pytest

from aristos_council.pipeline import _factor_note_for, lens_agreement
from aristos_council.rank_engine import (RankedTicker, factor_column_label,
                                         factor_is_imputed, factor_measurement,
                                         format_factor_cell, ranked_table_rows)


def _row(imputed=("roic",), **over):
    kw = dict(ticker="SU", factor_ranks={"earnings_yield": 1.0, "roic": 2.0,
                                         "momentum_12m": 3.0},
              factor_values={"earnings_yield": 0.12, "roic": None,
                             "momentum_12m": 0.30},
              combined_rank=6.0, universe_size=3, verdict="buy",
              imputed_factors=list(imputed))
    kw.update(over)
    return RankedTicker(**kw)


# =========================================================================== #
# 1. one count, and it is the run's own record
# =========================================================================== #
def test_the_count_comes_out_as_measured_of_total():
    assert factor_measurement(_row()) == (2, 3)
    assert factor_measurement(_row(imputed=())) == (3, 3)
    assert factor_measurement(_row(imputed=("roic", "momentum_12m"))) == (1, 3)


def test_a_name_with_no_factors_at_all_counts_nothing_rather_than_dividing_by_zero():
    assert factor_measurement(_row(factor_ranks={}, imputed=())) == (0, 0)


def test_imputed_is_read_from_the_runs_record_not_from_a_missing_value():
    """``factor_values[f] is None`` is ALSO true of a name sent to the WORST rank under a
    different missing-mode. Deriving "imputed" from it would stamp a deliberate penalty as
    an imputation, which is the opposite of what happened."""
    worst_ranked = _row(imputed=())          # roic value is still None
    assert worst_ranked.factor_values["roic"] is None
    assert factor_is_imputed(worst_ranked, "roic") is False
    assert factor_measurement(worst_ranked) == (3, 3)
    assert _factor_note_for(worst_ranked) == ""


# =========================================================================== #
# 2. all three renderings, from that one count
# =========================================================================== #
def test_the_marker_the_note_and_the_cell_all_agree_on_one_row():
    row = _row()
    measured, total = factor_measurement(row)

    # (a) the agreement-table note
    assert _factor_note_for(row) == f"ranked on {measured} of {total} factors"

    # (b) the per-lens factor table: exactly ``total`` columns, exactly
    #     ``total - measured`` of them saying "imputed"
    rendered, factor_ids = ranked_table_rows([row])
    cells = {f: rendered[0][factor_column_label(f)] for f in factor_ids}
    assert len(cells) == total
    imputed_cells = [f for f, cell in cells.items() if "imputed" in cell]
    assert len(imputed_cells) == total - measured
    assert imputed_cells == ["roic"]

    # (c) ...and the measured ones carry their value, not the word
    assert "imputed" not in cells["earnings_yield"]
    assert "0.12" in cells["earnings_yield"] or "12" in cells["earnings_yield"]


def test_the_grid_cell_marker_reads_from_the_same_count():
    from aristos_council.pipeline import MultiStrategyCell

    measured, total = factor_measurement(_row())
    cell = MultiStrategyCell(strategy_id="s", status="ranked", position=1,
                             cohort_size=3, verdict="buy", score=6.0,
                             factors_measured=measured, factors_total=total)
    assert cell.factor_note == " · ranked on 2 of 3 factors"


def test_a_fully_measured_row_shows_no_marker_anywhere():
    row = _row(imputed=())
    assert _factor_note_for(row) == ""
    rendered, factor_ids = ranked_table_rows([row])
    assert not any("imputed" in rendered[0][factor_column_label(f)] for f in factor_ids)


# =========================================================================== #
# 3. the cell says the word
# =========================================================================== #
def test_an_imputed_cell_says_imputed_rather_than_carrying_a_bare_number():
    """"2*" is a plain number to a reader who has not found the legend — and this cell
    sits directly beside a marker that says "ranked on 2 of 3 factors"."""
    assert format_factor_cell(2.0, None, "roic", True) == "2 · imputed"
    assert format_factor_cell(2.0, 0.18, "roic", True) == "2 · imputed"   # never a value


def test_a_measured_cell_is_unchanged():
    cell = format_factor_cell(1.0, 0.12, "earnings_yield", False)
    assert cell.startswith("1 · ") and "imputed" not in cell


def test_the_legend_explains_the_word_the_cell_actually_prints():
    """The legend key and the cell moved together. For one commit they did not, and the
    legend silently stopped explaining imputation at all."""
    from aristos_council.report_language import IMPUTED_NOTE_KEY, SYMBOL_NOTES

    entry = dict(SYMBOL_NOTES)[IMPUTED_NOTE_KEY]
    assert IMPUTED_NOTE_KEY in format_factor_cell(2.0, None, "roic", True)
    assert "ranked on 2 of 3 factors" in entry      # it names the marker it agrees with


def test_the_legend_is_offered_when_a_run_actually_imputed_something():
    from aristos_council.pipeline import RankPipelineResult, used_symbol_notes
    from aristos_council.report_language import IMPUTED_NOTE_KEY

    imputing = RankPipelineResult(ranked=[_row()], excluded=[], unrateable=[],
                                  narratives={}, header="", meta={})
    clean = RankPipelineResult(ranked=[_row(imputed=())], excluded=[], unrateable=[],
                               narratives={}, header="", meta={})
    assert IMPUTED_NOTE_KEY in dict(used_symbol_notes(imputing))
    assert IMPUTED_NOTE_KEY not in dict(used_symbol_notes(clean))


# =========================================================================== #
# 4. the agreement table stopped losing the check lens's marker
# =========================================================================== #
def _multi_with_check():
    """A two-lens run: one voting lens, one CHECK lens that imputed a factor.

    This is the Suncor shape. Forensic ranked it without an Altman Z, so its cell carried
    "ranked on 1 of 2 factors" — and the agreement table, which gathered notes inside its
    `voting` branch, showed nothing at all.
    """
    from aristos_council.pipeline import MultiStrategyResult, RankPipelineResult

    voting_row = RankedTicker(
        ticker="SU", factor_ranks={"earnings_yield": 1.0, "roic": 1.0},
        factor_values={"earnings_yield": 0.1, "roic": 0.2}, combined_rank=2.0,
        universe_size=1, verdict="buy", cohort_position=1)
    check_row = RankedTicker(
        ticker="SU", factor_ranks={"altman_z": 1.0, "accruals": 1.0},
        factor_values={"altman_z": None, "accruals": 0.02}, combined_rank=2.0,
        universe_size=1, verdict="buy", imputed_factors=["altman_z"],
        cohort_position=1)

    # ``lens_agreement`` reads the lens's KIND off ``result.rank_strategy.kind`` — the
    # strategy file's own word. Setting it is the whole point of this fixture: with it
    # missing, Forensic classifies as a VOTING lens and the test passes against the bug.
    class _Strategy:
        def __init__(self, kind):
            self.kind = kind

    def _result(rows, sid, kind):
        out = RankPipelineResult(
            ranked=rows, excluded=[], unrateable=[], narratives={}, header="",
            meta={"rank_strategy_id": sid})
        out.rank_strategy = _Strategy(kind)
        return out

    return MultiStrategyResult(
        strategy_ids=["value_v1", "forensic_v1"],
        strategy_names={"value_v1": "Value", "forensic_v1": "Forensic"},
        results={"value_v1": _result([voting_row], "value_v1", "selector"),
                 "forensic_v1": _result([check_row], "forensic_v1", "check")},
        rows=[], meta={})


def test_the_fixture_really_does_carry_a_CHECK_lens():
    """Guards the guard. Without ``rank_strategy.kind``, Forensic classifies as voting and
    every assertion below passes against the bug it is supposed to catch."""
    agreement = lens_agreement(_multi_with_check())
    assert agreement.check_ids == ["forensic_v1"]
    assert agreement.voting_ids == ["value_v1"]


def test_a_check_lenss_marker_reaches_the_agreement_table():
    multi = _multi_with_check()
    agreement = lens_agreement(multi)
    row = next(r for r in agreement.rows if r.ticker == "SU")

    notes = " ".join(row.factor_notes)
    assert "ranked on 1 of 2 factors" in notes, row.factor_notes
    assert "Forensic" in notes, "the note must name the lens whose reading is short"


def test_the_voting_lenss_marker_still_reaches_it_too():
    """The fix widened the collection; it must not have moved it."""
    from aristos_council.pipeline import MultiStrategyResult, RankPipelineResult

    multi = _multi_with_check()
    short_voter = RankedTicker(
        ticker="SU", factor_ranks={"earnings_yield": 1.0, "roic": 1.0},
        factor_values={"earnings_yield": 0.1, "roic": None}, combined_rank=2.0,
        universe_size=1, verdict="buy", imputed_factors=["roic"], cohort_position=1)
    replacement = RankPipelineResult(
        ranked=[short_voter], excluded=[], unrateable=[], narratives={}, header="",
        meta={"rank_strategy_id": "value_v1"})
    replacement.rank_strategy = multi.results["value_v1"].rank_strategy
    multi.results["value_v1"] = replacement

    notes = " ".join(next(r for r in lens_agreement(multi).rows
                          if r.ticker == "SU").factor_notes)
    assert "Value: ranked on 1 of 2 factors" in notes
    assert "Forensic: ranked on 1 of 2 factors" in notes


def test_a_fully_measured_name_carries_no_note_in_the_agreement_table():
    multi = _multi_with_check()
    from aristos_council.pipeline import RankPipelineResult
    full = RankedTicker(
        ticker="SU", factor_ranks={"altman_z": 1.0, "accruals": 1.0},
        factor_values={"altman_z": 3.1, "accruals": 0.02}, combined_rank=2.0,
        universe_size=1, verdict="buy", cohort_position=1)
    replacement = RankPipelineResult(
        ranked=[full], excluded=[], unrateable=[], narratives={}, header="",
        meta={"rank_strategy_id": "forensic_v1"})
    replacement.rank_strategy = multi.results["forensic_v1"].rank_strategy
    multi.results["forensic_v1"] = replacement
    row = next(r for r in lens_agreement(multi).rows if r.ticker == "SU")
    assert row.factor_notes == ()
