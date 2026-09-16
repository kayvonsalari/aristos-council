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
    # REPORT-2: the CELL reads "no data" — short enough to scan across a wide grid —
    # while the RAW reason is kept on the cell and rendered in that lens's detail section.
    assert dead.cells[SCREENED].render() == "no data"
    assert "no data" in dead.cells[SCREENED].reason
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
# FUND-UI-2 item 5 — the Run tab's lens picker became a primary dropdown + one checkbox
# per extra lens. Presentation only: the SAME set of lenses must produce the SAME grid it
# produced when both came out of one multiselect.
# --------------------------------------------------------------------------- #
def test_checkbox_lens_selection_yields_the_same_combined_grid_as_the_multiselect():
    from dataclasses import dataclass

    from aristos_council.strategy.picker import (
        resolve_all,
        selected_labels,
        strategy_choices,
    )

    @dataclass
    class _Stub:                        # only id + display_name reach the picker
        id: str
        display_name: str

    choices = strategy_choices([_Stub(SCREENED, "Screened"), _Stub(RAW, "Raw")])
    # OLD: one multiselect, both lenses in it (click order, as the widget recorded it).
    from_multiselect = resolve_all(choices, ["Raw", "Screened"])
    # NEW: "Screened" is the narrated primary, "Raw" is a ticked extra-lens checkbox.
    from_checkboxes = resolve_all(choices, selected_labels("Screened", [("Raw", True)]))
    assert [s.id for s in from_checkboxes] == [s.id for s in from_multiselect]

    old = _multi([s.id for s in from_multiselect])
    new = _multi([s.id for s in from_checkboxes])
    assert new.strategy_ids == old.strategy_ids               # same columns, same order
    assert format_multi_strategy_grid(new) == format_multi_strategy_grid(old)
    assert {t: format_cli_report(r) for t, r in new.results.items()} == \
        {t: format_cli_report(r) for t, r in old.results.items()}


# --------------------------------------------------------------------------- #
# The rendered grid
# --------------------------------------------------------------------------- #
def test_grid_text_carries_every_lens_column_and_keeps_its_order():
    """GRID-COLS-1: the rank-sum column and its "fewer lenses" marker are gone from the
    rendered grid — they were incomparable on most rows of a real cohort. What the column
    PRODUCED survives and is what this now pins: the row ORDER, which is computed in
    combine_rank_results and merely rendered here."""
    result = _multi([SCREENED, RAW])
    text = format_multi_strategy_grid(result)

    for sid in result.strategy_ids:                 # one column per lens, still
        assert sid in text
    assert "rank-sum" not in text                   # ...and no incomparable column
    assert "\u2021" not in text                        # ...nor its footnote marker

    # the ORDER is the grid's own, unchanged. Read the NAME COLUMN of the data rows
    # rather than searching the whole text: the fixture's names are single letters and
    # would match inside the header.
    lines = text.splitlines()
    head = next(i for i, l in enumerate(lines) if l.strip().startswith("name"))
    rendered = [l[2:26].strip() for l in lines[head + 1:] if l.startswith("  ") and l.strip()]
    assert rendered[:len(result.rows)] == [row.display for row in result.rows]

def test_combine_is_pure_and_orders_comparable_names_first():
    res = _multi([SCREENED, RAW])
    rows = combine_rank_results(res.results, [SCREENED, RAW])
    assert [r.ticker for r in rows] == [r.ticker for r in res.rows]
    graded = [r.graded for r in rows]
    assert graded == sorted(graded, reverse=True)      # fully-graded names first
    sums = [r.rank_sum for r in rows if r.graded == 2]
    assert sums == sorted(sums)                        # then best rank-sum first


# --------------------------------------------------------------------------- #
# BAND-2 — the valuation band covers every ranked name, not the first lens's
# --------------------------------------------------------------------------- #
# The band is per-NAME and display-only: it says where a price sits against that name's
# OWN history, which no lens has a view on. But a lens attaches a band only to names IT
# ranked, so computing it on the first lens alone made the section's size an accident of
# lens ORDER. Live, 2026-09-15, the 121-name USD-listed cohort: Defensive Income first
# (its 10-year dividend-streak rule ranked 2 of 121) produced a 2-row band section
# (PSX, SOBO); re-running with Magic Formula RAW first produced 81 rows.
class _Row:
    """The two attributes the band table reads off a ranked row, plus the ticker."""

    def __init__(self, ticker, band=None, price=None, excluded=False):
        self.ticker = ticker
        self.valuation_band = band
        self.price = price
        self.reversion = None
        self.excluded = excluded


class _Res:
    def __init__(self, ranked, names=None):
        self.ranked = ranked
        self.names = names or {}


class _Multi:
    def __init__(self, strategy_ids, results):
        self.strategy_ids = list(strategy_ids)
        self.results = results


def _band(current=12.0):
    from aristos_council.tools.valuation_band import ValuationBand
    return ValuationBand(basis="ev_ebit", current=current, median_multiple=10.0,
                         percentile=55.0, months_covered=61, months_total=61,
                         window_years=5, net_debt_basis="asof")


def test_union_band_table_covers_every_name_any_lens_ranked():
    """Lens A ranks {X}; lens B ranks {X, Y}. The union has a row for X AND Y."""
    from aristos_council.pipeline import union_valuation_band_table

    a = _Res([_Row("X", _band(11.0))])
    b = _Res([_Row("X", _band(11.0)), _Row("Y", _band(22.0))])

    forward = union_valuation_band_table(_Multi(["A", "B"], {"A": a, "B": b}))
    reverse = union_valuation_band_table(_Multi(["B", "A"], {"A": a, "B": b}))

    names = lambda t: [r["Name"] for r in t.rows]          # noqa: E731
    assert set(names(forward)) == {"X", "Y"} == set(names(reverse))
    # ...and X appears ONCE even though two lenses ranked it.
    assert len(forward.rows) == 2 and len(reverse.rows) == 2


def test_union_band_takes_the_first_band_seen_per_ticker_in_strategy_order():
    """Deterministic by construction: strategy order decides, not dict iteration."""
    from aristos_council.pipeline import union_valuation_band_table

    a = _Res([_Row("X", _band(11.0))])
    b = _Res([_Row("X", _band(99.0))])
    table = union_valuation_band_table(_Multi(["A", "B"], {"A": a, "B": b}))
    assert [r["EV/EBIT"] for r in table.rows] == ["11.0x"]
    flipped = union_valuation_band_table(_Multi(["B", "A"], {"A": a, "B": b}))
    assert [r["EV/EBIT"] for r in flipped.rows] == ["99.0x"]


def test_union_band_keeps_an_abstaining_row():
    """Existing doctrine: an abstention keeps its row and states its own reason."""
    from aristos_council.pipeline import union_valuation_band_table
    from aristos_council.tools.valuation_band import ValuationBand

    out = ValuationBand(note="insufficient history: 1.1y")
    table = union_valuation_band_table(
        _Multi(["A"], {"A": _Res([_Row("X", out)])}))
    assert len(table.rows) == 1
    assert "insufficient history" in table.rows[0]["Percentile"]


def test_union_band_ignores_excluded_rows_and_empty_runs():
    from aristos_council.pipeline import union_valuation_band_table

    excluded_only = _Res([_Row("X", _band(), excluded=True)])
    assert union_valuation_band_table(_Multi(["A"], {"A": excluded_only})) is None
    assert union_valuation_band_table(_Multi([], {})) is None


def test_single_lens_band_table_is_unchanged():
    """BAND-2 touches the multi-lens path only."""
    from aristos_council.pipeline import valuation_band_table

    table = valuation_band_table(_Res([_Row("X", _band(11.0)), _Row("Y", _band(22.0))]))
    assert [r["Name"] for r in table.rows] == ["X", "Y"]
    assert [r["EV/EBIT"] for r in table.rows] == ["11.0x", "22.0x"]


def test_band_is_computed_for_every_lens_not_just_the_first():
    """The end-to-end shape of the live failure: C is EXCLUDED by the screened lens and
    RANKED by the raw one, so ordering the screened lens first used to cost C its row."""
    res = run_multi_strategy_pipeline(UNIVERSE, [SCREENED, RAW], strategies_dir=STRAT_DIR,
                                      adapter=_Adapter(), today=TODAY,
                                      with_valuation_band=True)
    # EVERY lens now carries bands for what IT ranked — not just the first.
    for sid in (SCREENED, RAW):
        ranked = [r for r in res.results[sid].ranked if not r.excluded]
        assert ranked and all(getattr(r, "valuation_band", None) is not None
                              for r in ranked), sid

    from aristos_council.pipeline import union_valuation_band_table
    names = {r["Name"] for r in union_valuation_band_table(res).rows}
    assert "C" in names                       # the name only the RAW lens ranked
    # ...and lens order does not decide it.
    flipped = run_multi_strategy_pipeline(UNIVERSE, [RAW, SCREENED],
                                          strategies_dir=STRAT_DIR,
                                          adapter=_Adapter(), today=TODAY,
                                          with_valuation_band=True)
    assert {r["Name"] for r in union_valuation_band_table(flipped).rows} == names


def test_band_for_every_lens_costs_no_second_network_fetch(tmp_path):
    """The claim the change rests on: the per-day cache serves the later lenses, so
    banding EVERY lens re-reads the same 5-year fetch instead of issuing a new one.

    Stated as a marginal cost: adding a second lens adds ZERO provider calls for every
    ticker that can be cached. DEAD is the one exception and not a BAND-2 one — its fetch
    RAISES, an exception is not a cacheable result, so it is retried by each lens exactly
    as it was before this change."""
    from aristos_council.data.cache import CachingAdapter

    class _Counting(_Adapter):
        def __init__(self):
            self.price_calls = []

        def get_price_history(self, ticker, *, start, end):
            self.price_calls.append((ticker, start, end))
            return super().get_price_history(ticker, start=start, end=end)

    def _calls(ids, cache_dir):
        inner = _Counting()
        run_multi_strategy_pipeline(
            UNIVERSE, ids, strategies_dir=STRAT_DIR, today=TODAY,
            with_valuation_band=True,
            adapter=CachingAdapter(inner, cache_dir=cache_dir, today=TODAY))
        return inner.price_calls

    one = _calls([RAW], tmp_path / "one")
    two = _calls([SCREENED, RAW], tmp_path / "two")

    live = lambda calls: [c for c in calls if c[0] != "DEAD"]      # noqa: E731
    # Each (ticker, window) reaches the provider exactly once, however many lenses ran...
    assert len(live(two)) == len(set(live(two)))
    # ...so the SECOND lens costs nothing: same provider calls as a single-lens run.
    assert sorted(set(live(two))) == sorted(set(live(one)))
    assert len(live(two)) == len(live(one))

    # The 5-year band window is among them, fetched once per name (not once per lens).
    windows = {w for _, w, _ in ((t, s, e) for t, s, e in live(two))}
    assert len(windows) == 2                    # the 400-day legs window + the 5y band
    for ticker in ("A", "B", "C"):
        for start in windows:
            assert sum(1 for c in live(two) if c[0] == ticker and c[1] == start) == 1

    # DEAD is retried per lens because it raises; that is unchanged, and it is not a fetch
    # of anything (the provider has no data for it).
    assert [c[0] for c in two if c[0] == "DEAD"]
