"""FUND-RUN-1 — one cohort, N strategies, ONE combined grid.

Five lenses used to mean five manual runs and five reports to eyeball side by side (hit
2026-08-04 and 2026-08-10). ``run_multi_strategy_pipeline`` runs the SAME deterministic
stage once per strategy over the SAME cohort and combines the results; each column is
byte-identical to that strategy's own single run (asserted below), so no decision logic
moved — the grid only arranges verdicts of record.

Deterministic: a fake adapter, no network, no LLM (every per-strategy run is ranker-only).
The fixture is the ``run_rank_pipeline`` cohort shape: A/B are healthy, C fails the
magic_value prefilter's ROIC floor (so it is EXCLUDED under the screened lens but RANKED
under the canonical no-screen RAW lens — exactly the cross-lens disagreement the grid
exists to show), DEAD is a delisted shell (UNRATEABLE under both).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from aristos_council.data.adapter import (
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
)
from aristos_council.pipeline import (
    combine_rank_results,
    format_cli_report,
    format_multi_strategy_grid,
    run_multi_strategy_pipeline,
    run_rank_pipeline,
)

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"
SCREENED = "magic_formula_v1"          # prefilters on the magic_value quality/value screen
RAW = "magic_formula_raw_v1"           # canonical Greenblatt + momentum, NO screens

_FUND = {
    "A": dict(market_cap=2e10, sector="Technology", ebit=[3000.0], pe_ratio=10.0,
              operating_income=[3000.0, 2800, 2600, 2400],
              tax_provision=[600.0, 560, 520, 480],
              pretax_income=[2900.0, 2700, 2500, 2300], invested_capital=[5000.0] * 4,
              total_revenue=[200.0, 170, 150, 120]),
    "B": dict(market_cap=2e10, sector="Technology", ebit=[1500.0], pe_ratio=20.0,
              operating_income=[1500.0, 1450, 1400, 1350],
              tax_provision=[300.0, 290, 280, 270],
              pretax_income=[1450.0, 1400, 1350, 1300], invested_capital=[5000.0] * 4,
              total_revenue=[150.0, 140, 130, 120]),
    "C": dict(market_cap=2e10, sector="Technology", ebit=[500.0], pe_ratio=40.0,
              operating_income=[500.0, 490, 480, 470], tax_provision=[100.0, 98, 96, 94],
              pretax_income=[480.0, 470, 460, 450], invested_capital=[5000.0] * 4,
              total_revenue=[125.0, 120, 115, 110]),
}

UNIVERSE = ["A", "B", "C", "DEAD"]
TODAY = date(2026, 6, 30)


class _Adapter(MarketDataAdapter):
    """A/B/C are healthy; DEAD is a delisted shell (blank fundamentals, price raises)."""

    name = "fake"

    def get_fundamentals(self, ticker):
        if ticker == "DEAD":
            return Fundamentals(ticker="DEAD")
        return Fundamentals(ticker=ticker, name=ticker, **_FUND[ticker])

    def get_price_history(self, ticker, *, start, end):
        if ticker == "DEAD":
            raise RuntimeError("no timezone found, symbol may be delisted")
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=10)
            for i in range(220)])

    def get_dividend_history(self, ticker, *, start, end):
        return []


def _multi(ids):
    return run_multi_strategy_pipeline(UNIVERSE, ids, strategies_dir=STRAT_DIR,
                                       adapter=_Adapter(), today=TODAY)


# --------------------------------------------------------------------------- #
# Two strategies -> ONE grid
# --------------------------------------------------------------------------- #
def test_two_strategy_run_returns_one_combined_grid():
    res = _multi([SCREENED, RAW])
    assert res.strategy_ids == [SCREENED, RAW]           # given order = column order
    by = {row.ticker: row for row in res.rows}
    assert set(by) == {"A", "B", "C", "DEAD"}            # one row per name, once
    assert all(set(row.cells) == {SCREENED, RAW} for row in res.rows)

    # A is best under BOTH lenses -> rank-sum 2, graded by all, and heads the grid.
    a = by["A"]
    assert a.cells[SCREENED].status == "ranked" and a.cells[SCREENED].position == 1
    assert a.cells[RAW].status == "ranked" and a.cells[RAW].position == 1
    assert a.rank_sum == 2 and a.graded == 2 and a.comparable
    assert res.rows[0].ticker == "A"
    assert a.cells[SCREENED].render().endswith("BUY")


def test_grid_shows_the_cross_lens_disagreement_with_the_failed_rule():
    res = _multi([SCREENED, RAW])
    c = {row.ticker: row for row in res.rows}["C"]
    # excluded by the screened lens — the failed rule AND the observed value ride along
    assert c.cells[SCREENED].status == "excluded"
    assert "min_roic" in c.cells[SCREENED].reason
    assert "observed" in c.cells[SCREENED].reason
    assert "excluded — " in c.cells[SCREENED].render()
    # ranked by the canonical no-screen lens
    assert c.cells[RAW].status == "ranked" and c.cells[RAW].position is not None
    # graded by ONE lens: nothing is imputed for the exclusion, so the sum is NOT
    # comparable with a name graded by both (null≠false).
    assert c.graded == 1 and not c.comparable


def test_unrateable_keeps_its_own_axis_in_every_column():
    res = _multi([SCREENED, RAW])
    dead = {row.ticker: row for row in res.rows}["DEAD"]
    assert [cell.status for cell in dead.cells.values()] == ["unrateable", "unrateable"]
    assert dead.rank_sum is None and dead.graded == 0
    assert "UNRATEABLE" in dead.cells[SCREENED].render()
    assert dead.ticker == res.rows[-1].ticker            # never-ranked names sort last


def test_multi_run_is_deterministic_no_council_no_narratives():
    res = _multi([SCREENED, RAW])
    assert all(r.council == [] and r.narratives == {} for r in res.results.values())
    assert all(r.meta["ranker_only"] for r in res.results.values())
    assert res.meta["council_mode"] == "ranker-only" and res.meta["ranker_only"]
    assert res.meta["graded_by_all"] == 2                # A and B ranked by both lenses


# --------------------------------------------------------------------------- #
# Single-strategy runs unchanged
# --------------------------------------------------------------------------- #
def test_single_strategy_column_is_byte_identical_to_its_own_run():
    single = run_rank_pipeline(UNIVERSE, SCREENED, ranker_only=True,
                              strategies_dir=STRAT_DIR, adapter=_Adapter(), today=TODAY)
    got = _multi([SCREENED]).results[SCREENED]
    assert format_cli_report(got) == format_cli_report(single)
    assert [r.ticker for r in got.ranked] == [r.ticker for r in single.ranked]
    assert got.excluded == single.excluded
    assert got.unrateable == single.unrateable


def test_duplicate_ids_collapse_and_an_empty_selection_is_an_error():
    res = _multi([SCREENED, SCREENED])
    assert res.strategy_ids == [SCREENED]
    with pytest.raises(ValueError):
        _multi([])


# --------------------------------------------------------------------------- #
# The rendered grid
# --------------------------------------------------------------------------- #
def test_grid_text_carries_every_column_the_sums_and_the_incomparable_mark():
    res = _multi([SCREENED, RAW])
    text = format_multi_strategy_grid(res)
    assert "COMBINED GRID" in text and "2 strategies" in text
    assert SCREENED in text and RAW in text
    assert "deterministic ranker" in text
    assert "min_roic" in text                     # exclusion reason, verbatim
    assert "UNRATEABLE" in text                   # its own axis, distinct wording
    assert "‡" in text                            # C: ranked by fewer lenses


def test_combine_is_pure_and_orders_comparable_names_first():
    res = _multi([SCREENED, RAW])
    rows = combine_rank_results(res.results, [SCREENED, RAW])
    assert [r.ticker for r in rows] == [r.ticker for r in res.rows]
    graded = [r.graded for r in rows]
    assert graded == sorted(graded, reverse=True)      # fully-graded names first
    sums = [r.rank_sum for r in rows if r.graded == 2]
    assert sums == sorted(sums)                        # then best rank-sum first
