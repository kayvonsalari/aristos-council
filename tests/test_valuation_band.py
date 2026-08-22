"""VALBAND-1 — the absolute valuation band (is the price high or low vs its OWN past?).

Every other value measure in this system is RELATIVE (earnings yield ranked within a
cohort), so in a hot cohort the least-expensive of ten overpriced names still ranks #1.
These tests pin the absolute answer: today's EV/EBIT (or the LABELLED P/E fallback)
placed in the stock's own 5-year monthly distribution.

The honesty properties are the point, so they are what is pinned:
- a FLAT history puts today at exactly the 50th percentile (no drift, no interpolation);
- a name at its own historical peak reads near the 100th;
- under 3 years of computable history ABSTAINS with the span in the reason (a recent
  IPO must abstain, never be assigned a fabricated middle);
- the P/E fallback is LABELLED, never silent;
- negative-earnings months DROP OUT and are COUNTED ("band from 41 of 60 months");
- same inputs -> same percentile, in any input order;
- and NO existing strategy selects the new factor or criterion, so no ranking moves.
"""

from __future__ import annotations

import calendar
from datetime import date

from aristos_council.data.adapter import Fundamentals, PriceBar
from aristos_council.factors import (
    FACTOR_REGISTRY,
    FactorInputs,
    compute_factor_outcomes,
    valuation_band_display,
)
from aristos_council.pipeline import format_valuation_bands, valuation_band_rows
from aristos_council.rank_engine import RankedTicker
from aristos_council.tools.criteria.registry import REGISTRY, Evidence, run_screen
from aristos_council.tools.valuation_band import ValuationBand, valuation_band

TODAY = date(2026, 6, 30)


# --------------------------------------------------------------------------- #
# Fixtures — monthly bars + DATED annual statements (the point-in-time source)
# --------------------------------------------------------------------------- #
def _month_ends(n: int, end: date = TODAY) -> list[date]:
    """``n`` month-end dates, oldest-first, ending in ``end``'s month."""
    out: list[date] = []
    y, m = end.year, end.month
    for _ in range(n):
        out.append(date(y, m, calendar.monthrange(y, m)[1]))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def _bars(closes: list[float], end: date = TODAY) -> list[PriceBar]:
    """One bar per month-end, oldest-first, the newest at ``end`` itself (so the band's
    'current' point is not stale)."""
    days = _month_ends(len(closes), end)
    days[-1] = end
    return [PriceBar(day=d, open=c, high=c, low=c, close=c, adj_close=c * 0.9, volume=1)
            for d, c in zip(days, closes)]


def _fiscal_years(n: int, last: int = 2025) -> list[str]:
    """``n`` fiscal-year ends, NEWEST-FIRST (the adapter's convention)."""
    return [f"{last - i}-12-31" for i in range(n)]


def _fundamentals(*, ebit=None, net_income=None, debt=None, cash=None,
                  market_cap=1_000.0, years=8, **kw) -> Fundamentals:
    """A Fundamentals carrying DATED statement series — the only shape the band reads
    (the positional series drop NaN cells and lose the fiscal-year correspondence)."""
    ends = _fiscal_years(years)
    aligned: dict[str, list] = {}
    period_ends: dict[str, list[str]] = {}
    for name, values in (("ebit", ebit), ("net_income", net_income),
                         ("total_debt", debt), ("cash", cash)):
        if values is None:
            continue
        series = values if isinstance(values, list) else [values] * years
        aligned[name] = list(series)
        period_ends[name] = ends[:len(series)]
    return Fundamentals(ticker="X", market_cap=market_cap, aligned_annual=aligned,
                        aligned_period_ends=period_ends, **kw)


def _flat_ev_ebit(closes: list[float]) -> tuple[list[PriceBar], Fundamentals]:
    """Constant EBIT / debt / cash — so the band moves with PRICE alone."""
    return _bars(closes), _fundamentals(ebit=100.0, debt=200.0, cash=50.0)


# --------------------------------------------------------------------------- #
# 1. Flat history -> exactly the 50th percentile
# --------------------------------------------------------------------------- #
def test_flat_history_puts_today_at_the_50th_percentile():
    bars, f = _flat_ev_ebit([100.0] * 61)
    band = valuation_band(bars, f, asof=TODAY)

    assert band.available
    assert band.percentile == 50.0            # exact: mid-rank over an all-tied series
    assert band.basis == "ev_ebit"
    assert band.months_covered >= 36


def test_the_ev_route_prices_in_net_debt_not_just_market_cap():
    """EV/EBIT is (market cap + net debt) / EBIT. A leveraged name's multiple must be
    HIGHER than the same name's bare market-cap multiple — otherwise 'EV' is a label
    on an equity ratio."""
    bars, f = _flat_ev_ebit([100.0] * 61)
    band = valuation_band(bars, f, asof=TODAY)
    assert band.current == (1_000.0 + 200.0 - 50.0) / 100.0
    assert band.net_debt_basis == "asof"


# --------------------------------------------------------------------------- #
# 2. At its own extreme -> a high percentile
# --------------------------------------------------------------------------- #
def test_a_name_at_its_own_peak_reads_near_the_100th_percentile():
    rising = [50.0 + i for i in range(61)]            # every month a new high
    bars, f = _flat_ev_ebit(rising)
    band = valuation_band(bars, f, asof=TODAY)

    assert band.available
    assert band.percentile > 95
    assert "percentile of own 5-year EV/EBIT band" in band.display


def test_a_name_at_its_own_floor_reads_near_the_1st_percentile():
    falling = [110.0 - i for i in range(61)]          # every month a new low
    bars, f = _flat_ev_ebit(falling)
    band = valuation_band(bars, f, asof=TODAY)

    assert band.available
    assert band.percentile < 5


# --------------------------------------------------------------------------- #
# 3. Short history -> abstention with the span in the reason (the IPO case)
# --------------------------------------------------------------------------- #
def test_short_history_abstains_with_the_years_in_the_reason():
    bars, f = _flat_ev_ebit([100.0] * 18)             # ~1.4 years of months
    band = valuation_band(bars, f, asof=TODAY)

    assert not band.available
    assert band.percentile is None                    # never a fabricated middle
    assert "insufficient history" in band.note
    assert band.note.rstrip().endswith("y")           # e.g. "insufficient history: 1.4y"
    assert band.display.startswith("not evaluated — ")


def test_no_dated_statements_abstains_rather_than_guessing_a_denominator():
    """The positional series (Fundamentals.ebit) cannot be placed in time — NaN cells
    are dropped per series, so index [0] is not a known fiscal year. A provider that
    supplies only those gets an abstention, not a band built on a guessed date."""
    bars = _bars([100.0] * 61)
    f = Fundamentals(ticker="X", market_cap=1_000.0, ebit=[100.0] * 5,
                     total_debt=200.0, total_cash=50.0)
    band = valuation_band(bars, f, asof=TODAY)

    assert not band.available
    assert "no dated statement history" in band.note


def test_confirmed_currency_mismatch_abstains_never_mixes():
    """House rule 8: market cap is in the price currency, EBIT and debt in the accounts
    currency. A confirmed mismatch abstains — no FX, no mixed multiple."""
    bars, _ = _flat_ev_ebit([100.0] * 61)
    f = _fundamentals(ebit=100.0, debt=200.0, cash=50.0,
                      currency="USD", financial_currency="KRW")
    band = valuation_band(bars, f, asof=TODAY)

    assert not band.available
    assert "no FX conversion" in band.note


# --------------------------------------------------------------------------- #
# 4. The fallback basis is LABELLED
# --------------------------------------------------------------------------- #
def test_pe_fallback_is_used_and_labelled_when_ev_components_are_absent():
    bars = _bars([100.0] * 61)
    f = _fundamentals(net_income=80.0)                # no EBIT, no debt/cash anywhere
    band = valuation_band(bars, f, asof=TODAY)

    assert band.available
    assert band.basis == "pe"
    assert "P/E band (fallback)" in band.display
    assert band.current == 1_000.0 / 80.0


def test_ev_route_discloses_when_net_debt_is_held_at_the_latest_reported():
    """Dated EBIT but no dated debt/cash: the current scalars are held constant across
    the window — a real assumption, so it rides in the display string."""
    bars = _bars([100.0] * 61)
    f = _fundamentals(ebit=100.0, total_debt=200.0, total_cash=50.0)
    band = valuation_band(bars, f, asof=TODAY)

    assert band.basis == "ev_ebit"
    assert band.net_debt_basis == "latest"
    assert "net debt held at latest reported" in band.display


# --------------------------------------------------------------------------- #
# 5. Loss years drop out and are COUNTED, never faked
# --------------------------------------------------------------------------- #
def test_loss_years_drop_out_of_a_pe_band_and_the_coverage_is_reported():
    bars = _bars([100.0] * 61)
    # Newest-first: the three most recent years earn, the older ones lost money.
    f = _fundamentals(net_income=[80.0, 80.0, 80.0, 80.0, -10.0, -10.0, -10.0, -10.0])
    band = valuation_band(bars, f, asof=TODAY)

    assert band.available
    assert band.months_covered < band.months_total     # loss months are NOT in the band
    assert f"band from {band.months_covered}/{band.months_total} months" in band.display


def test_coverage_below_half_the_span_abstains_with_the_count():
    """Four loss years through the MIDDLE: the computable months still SPAN five years
    (a handful at each end), but they are a quarter of it. A percentile over a series
    with a four-year hole is not a band, so it abstains rather than quietly reporting
    one from 14 months."""
    bars = _bars([100.0] * 61)
    # Newest-first: FY2025 earns, FY2021-FY2024 lost money, FY2020 and older earned.
    f = _fundamentals(net_income=[80.0, -10.0, -10.0, -10.0, -10.0, 80.0, 80.0, 80.0])
    band = valuation_band(bars, f, asof=TODAY)

    assert not band.available
    assert "insufficient coverage" in band.note
    assert 0 < band.months_covered < 31                # under half of the 61 months


# --------------------------------------------------------------------------- #
# 6. Determinism
# --------------------------------------------------------------------------- #
def test_same_inputs_give_the_same_percentile_in_any_order():
    closes = [100.0 + (i * 7) % 23 for i in range(61)]
    bars, f = _flat_ev_ebit(closes)
    first = valuation_band(bars, f, asof=TODAY)
    again = valuation_band(bars, f, asof=TODAY)
    shuffled = valuation_band(list(reversed(bars)), f, asof=TODAY)

    assert first == again == shuffled
    assert first.available


def test_the_band_never_raises_on_absent_inputs():
    assert valuation_band([], None, asof=TODAY).percentile is None
    assert valuation_band([], Fundamentals(ticker="X"), asof=TODAY).percentile is None
    assert valuation_band(_bars([100.0] * 61), Fundamentals(ticker="X"),
                          asof=TODAY).percentile is None


# --------------------------------------------------------------------------- #
# 7. Registry wiring — ONE computation, two registries, NO strategy selects it
# --------------------------------------------------------------------------- #
def test_the_factor_ranks_the_percentile_with_low_as_better():
    fdef = FACTOR_REGISTRY["valuation_band_percentile"]
    assert fdef.direction == "low"          # cheap vs own history = a better rank

    bars, f = _flat_ev_ebit([100.0] * 61)
    fi = FactorInputs(ticker="X", fundamentals=f,
                      valuation_band=valuation_band(bars, f, asof=TODAY))
    value, source = compute_factor_outcomes(fi, ["valuation_band_percentile"])[
        "valuation_band_percentile"]
    assert value == 50.0
    assert source == "computed"


def test_the_factor_abstains_when_the_band_did_and_when_it_was_never_computed():
    for band in (None, ValuationBand(note="insufficient history: 1.4y")):
        fi = FactorInputs(ticker="X", valuation_band=band)
        value, source = compute_factor_outcomes(fi, ["valuation_band_percentile"])[
            "valuation_band_percentile"]
        assert value is None
        assert source == "abstained"


def test_the_criterion_reads_the_same_band_and_abstains_when_it_is_absent():
    sel = [type("Sel", (), {"name": "valuation_band_percentile", "threshold": 80.0})()]
    bars, f = _flat_ev_ebit([100.0] * 61)

    passing = run_screen(sel, Evidence(fundamentals=f, valuation_band=valuation_band(
        bars, f, asof=TODAY)), ticker="X").criteria[0]
    assert passing.passed is True and passing.observed == 50.0

    absent = run_screen(sel, Evidence(fundamentals=f), ticker="X").criteria[0]
    assert absent.passed is None                  # NOT-EVAL, never a phantom fail
    assert "not computed on this path" in absent.note

    tight = [type("Sel", (), {"name": "valuation_band_percentile",
                              "threshold": 20.0})()]
    failing = run_screen(tight, Evidence(fundamentals=f, valuation_band=valuation_band(
        bars, f, asof=TODAY)), ticker="X").criteria[0]
    assert failing.passed is False                # 50th percentile vs a 20th ceiling


def test_the_criterion_requires_no_evidence_kind_so_any_strategy_can_load_it():
    """It rides on Evidence.valuation_band, which is NOT one of the gathered evidence
    KINDS validate_selections knows about. Declaring one would either block every
    strategy that selects this criterion or lie about the council path."""
    from aristos_council.tools.criteria.registry import validate_selections
    sel = [type("Sel", (), {"name": "valuation_band_percentile", "threshold": 80.0})()]
    assert validate_selections(sel) == []
    assert REGISTRY["valuation_band_percentile"].requires == ()


def test_no_shipped_strategy_selects_the_new_factor_or_criterion():
    """Role (b): registered, ranked by NOTHING in this PR. A threshold or a rank leg
    needs documented rationale first (house rules), so every existing strategy's output
    is untouched by VALBAND-1."""
    from pathlib import Path
    strategies = Path(__file__).resolve().parents[1] / "strategies"
    users = [p.name for p in sorted(strategies.glob("*.yaml"))
             if "valuation_band_percentile" in p.read_text(encoding="utf-8")]
    assert users == []


# --------------------------------------------------------------------------- #
# 8. Display — role (a): the column every rateable name carries
# --------------------------------------------------------------------------- #
def test_the_display_string_states_the_percentile_the_basis_and_the_coverage():
    bars, f = _flat_ev_ebit([100.0] * 61)
    fi = FactorInputs(ticker="X", fundamentals=f,
                      valuation_band=valuation_band(bars, f, asof=TODAY))
    text = valuation_band_display(fi)

    assert text.startswith("50th percentile of own 5-year EV/EBIT band")
    assert "band from " in text and " months" in text


def test_an_uncomputed_band_renders_as_an_em_dash_not_a_number():
    assert valuation_band_display(FactorInputs(ticker="X")) == "—"


class _Result:
    """The two attributes the report block reads off a RankPipelineResult."""

    def __init__(self, ranked):
        self.ranked = ranked
        self.names = {"A": "Alpha Corp"}


def _row(ticker: str, band: str, excluded: bool = False) -> RankedTicker:
    return RankedTicker(ticker=ticker, factor_ranks={}, factor_values={},
                        combined_rank=1.0, universe_size=2, excluded=excluded,
                        valuation_band=band)


def test_the_report_block_names_every_rateable_name_and_skips_the_excluded():
    rows = valuation_band_rows(_Result([
        _row("A", "78th percentile of own 5-year EV/EBIT band (band from 55/60 months)"),
        _row("B", "not evaluated — insufficient history: 1.4y"),
        _row("C", "92nd percentile of own 5-year P/E band (fallback)", excluded=True),
    ]))

    assert [n for n, _ in rows] == ["Alpha Corp (A)", "B"]   # excluded name is not rated
    assert "insufficient history" in rows[1][1]              # abstention stays VISIBLE

    text = "\n".join(format_valuation_bands(_Result([_row("A", "78th percentile")])))
    assert "VALUATION BAND (absolute" in text
    assert "not ranked, not screened" in text


def test_a_run_where_nothing_computed_a_band_renders_no_block_at_all():
    """A thin/fake adapter (or a cohort of recent listings) must print exactly what it
    printed before VALBAND-1 — an empty section header is noise, not information."""
    assert valuation_band_rows(_Result([_row("A", "—"), _row("B", "—")])) == []
    assert format_valuation_bands(_Result([_row("A", "—")])) == []
    assert format_valuation_bands(_Result([])) == []


# --------------------------------------------------------------------------- #
# 9. UI toggle (VALBAND-1 A4) — the band is OPT-IN at the pipeline level.
#
# The band costs a second (5-year) price fetch per name, so run_rank_pipeline /
# run_multi_strategy_pipeline compute it ONLY when with_valuation_band=True. Off (the
# default) there is no band section and no extra fetch — byte-identical to a pre-VALBAND
# run; on, the context column appears and every ranker verdict/rank is unchanged (the band
# never re-grades). These pin exactly the A4 acceptance criteria.
# --------------------------------------------------------------------------- #
from pathlib import Path  # noqa: E402

from aristos_council.data.adapter import MarketDataAdapter, PriceHistory  # noqa: E402
from aristos_council.pipeline import (  # noqa: E402
    run_multi_strategy_pipeline, run_rank_pipeline)

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"

# magic_formula_v1 ranks on ROIC + earnings_yield and prefilters at ROIC >= 12% with a
# $5B cap floor; both names clear it. Dated ebit/debt/cash feed the band (the SAME
# fundamentals the ranking reads), so a real percentile computes for each.
_BAND_FUND = {
    "P": dict(market_cap=2e10, sector="Technology", ebit=[3000.0], pe_ratio=10.0,
              operating_income=[3000.0, 2800, 2600, 2400], tax_provision=[600.0] * 4,
              pretax_income=[2900.0, 2700, 2500, 2300], invested_capital=[5000.0] * 4,
              total_revenue=[200.0, 170, 150, 120]),
    "Q": dict(market_cap=2e10, sector="Technology", ebit=[1500.0], pe_ratio=20.0,
              operating_income=[1500.0, 1450, 1400, 1350], tax_provision=[300.0] * 4,
              pretax_income=[1450.0, 1400, 1350, 1300], invested_capital=[5000.0] * 4,
              total_revenue=[150.0, 140, 130, 120]),
}
_BAND_TRENDS = {"P": [50.0 + i for i in range(60)],       # rising -> near its own peak
                "Q": [110.0 - i for i in range(60)]}      # falling -> near its own floor


class _BandAdapter(MarketDataAdapter):
    """Two rateable stocks with DATED statements (so the band computes) and a 5-year
    monthly price series. Counts get_price_history calls so a test can prove the band's
    extra fetch runs ONLY when requested."""

    name = "fake-band"

    def __init__(self):
        self.price_calls = 0

    def get_fundamentals(self, ticker):
        ends = _fiscal_years(6)
        aligned = {"ebit": [_BAND_FUND[ticker]["ebit"][0]] * 6,
                   "total_debt": [200.0] * 6, "cash": [50.0] * 6}
        period_ends = {k: ends[:6] for k in aligned}
        return Fundamentals(ticker=ticker, name=ticker,
                            aligned_annual=aligned, aligned_period_ends=period_ends,
                            **_BAND_FUND[ticker])

    def get_price_history(self, ticker, *, start, end):
        self.price_calls += 1
        return PriceHistory(ticker=ticker, bars=_bars(_BAND_TRENDS[ticker], end=TODAY))

    def get_dividend_history(self, ticker, *, start, end):
        return []


def _verdict_snapshot(result):
    return [(r.ticker, r.verdict, round(r.combined_rank, 6))
            for r in result.ranked if not r.excluded]


def test_toggle_off_produces_no_band_and_is_the_default():
    result = run_rank_pipeline(
        ["P", "Q"], "magic_formula_v1", ranker_only=True,
        strategies_dir=STRAT_DIR, adapter=_BandAdapter(), today=TODAY)
    assert result.meta["with_valuation_band"] is False        # default OFF
    assert valuation_band_rows(result) == []                  # no band section
    assert all(getattr(r, "valuation_band", "—") in ("", "—") for r in result.ranked)


def test_toggle_on_adds_the_column_and_leaves_verdicts_unchanged():
    off = run_rank_pipeline(
        ["P", "Q"], "magic_formula_v1", ranker_only=True,
        strategies_dir=STRAT_DIR, adapter=_BandAdapter(), today=TODAY)
    on = run_rank_pipeline(
        ["P", "Q"], "magic_formula_v1", ranker_only=True, with_valuation_band=True,
        strategies_dir=STRAT_DIR, adapter=_BandAdapter(), today=TODAY)

    assert on.meta["with_valuation_band"] is True
    band_rows = valuation_band_rows(on)
    assert {n for n, _ in band_rows} == {"P", "Q"}            # column present for each
    assert all("percentile of own 5-year" in b for _, b in band_rows)
    # the band NEVER re-grades: verdicts + ranks are byte-identical with it on or off.
    assert _verdict_snapshot(on) == _verdict_snapshot(off)


def test_toggle_off_makes_no_extra_price_fetch():
    off_adapter = _BandAdapter()
    run_rank_pipeline(["P", "Q"], "magic_formula_v1", ranker_only=True,
                      strategies_dir=STRAT_DIR, adapter=off_adapter, today=TODAY)
    on_adapter = _BandAdapter()
    run_rank_pipeline(["P", "Q"], "magic_formula_v1", ranker_only=True,
                      with_valuation_band=True, strategies_dir=STRAT_DIR,
                      adapter=on_adapter, today=TODAY)
    # ON fetches an extra 5-year window per name (its own separate fetch); OFF does not.
    assert on_adapter.price_calls > off_adapter.price_calls
    assert on_adapter.price_calls == off_adapter.price_calls + 2   # one per name


def test_toggle_on_with_extra_lenses_keeps_the_column_and_every_lens_verdict():
    ids = ["magic_formula_v1", "magic_formula_raw_v1"]
    off = run_multi_strategy_pipeline(
        ["P", "Q"], ids, strategies_dir=STRAT_DIR, adapter=_BandAdapter(), today=TODAY)
    on = run_multi_strategy_pipeline(
        ["P", "Q"], ids, strategies_dir=STRAT_DIR, adapter=_BandAdapter(),
        today=TODAY, with_valuation_band=True)

    # the band rides on the FIRST lens's result (computed once, per name).
    first_on = on.results[ids[0]]
    assert {n for n, _ in valuation_band_rows(first_on)} == {"P", "Q"}
    assert valuation_band_rows(off.results[ids[0]]) == []
    # every lens's verdict column is unchanged whether the band is on or off.
    for sid in ids:
        assert _verdict_snapshot(on.results[sid]) == _verdict_snapshot(off.results[sid])


# --------------------------------------------------------------------------- #
# 10. Silent-failure hole (VALBAND, 2026-08-22): a REQUESTED band whose fetch fails must
# render an honest, visible abstention with its reason — never collapse to "—" and get the
# whole section dropped (byte-identical to a run where the band was never requested).
# --------------------------------------------------------------------------- #
from aristos_council.factors import _gather_valuation_band   # noqa: E402


class _RaisingPriceAdapter(_BandAdapter):
    """Valid dated fundamentals (names still rank on roic + earnings_yield), but EVERY
    price fetch raises — so the band's 5-year fetch fails. The 400-day leg's failure is a
    plain (non-transient) error, swallowed by gather_factor_inputs, so the name stays
    rateable; only the band cannot compute."""

    def get_price_history(self, ticker, *, start, end):
        self.price_calls += 1
        raise RuntimeError("no timezone found, symbol may be delisted")


class _ZeroBarPriceAdapter(_BandAdapter):
    """Every price fetch returns zero bars — the band gets no history."""

    def get_price_history(self, ticker, *, start, end):
        self.price_calls += 1
        return PriceHistory(ticker=ticker, bars=[])


# --- the core: _gather_valuation_band records WHY, never collapses to None ---------- #
def _dated_f():
    return _fundamentals(ebit=100.0, debt=200.0, cash=50.0)


def test_gather_band_fetch_raise_returns_a_reasoned_abstention_not_none():
    class _Boom:
        def get_price_history(self, ticker, *, start, end):
            raise RuntimeError("boom-net")
    band = _gather_valuation_band(_Boom(), "X", _dated_f(), today=TODAY)
    assert band is not None and not band.available            # NOT None, and abstained
    assert "price history unavailable" in band.note and "RuntimeError" in band.note
    assert band.display.startswith("not evaluated — price history unavailable")


def test_gather_band_zero_bars_returns_its_own_reason():
    class _Empty:
        def get_price_history(self, ticker, *, start, end):
            return PriceHistory(ticker=ticker, bars=[])
    band = _gather_valuation_band(_Empty(), "X", _dated_f(), today=TODAY)
    assert not band.available and "no price bars returned" in band.note


def test_gather_band_no_fundamentals_returns_its_own_reason():
    class _Empty:
        def get_price_history(self, ticker, *, start, end):
            return PriceHistory(ticker=ticker, bars=[])
    band = _gather_valuation_band(_Empty(), "X", None, today=TODAY)
    assert not band.available and "fundamentals unavailable" in band.note


def test_gather_band_never_raises_even_on_a_transient_error():
    from aristos_council.data.adapter import TransientFetchError
    class _Transient:
        def get_price_history(self, ticker, *, start, end):
            raise TransientFetchError("429 rate limited")
    band = _gather_valuation_band(_Transient(), "X", _dated_f(), today=TODAY)  # must NOT raise
    assert not band.available and "price history unavailable" in band.note


# --- the report: the section renders when requested-but-failed ---------------------- #
def test_band_requested_but_fetch_raises_renders_the_section_with_the_reason():
    result = run_rank_pipeline(
        ["P", "Q"], "magic_formula_v1", ranker_only=True, with_valuation_band=True,
        strategies_dir=STRAT_DIR, adapter=_RaisingPriceAdapter(), today=TODAY)
    rows = valuation_band_rows(result)
    assert {n for n, _ in rows} == {"P", "Q"}                 # section present, one row/name
    assert all("price history unavailable" in b for _, b in rows)
    assert all(b.startswith("not evaluated — ") for _, b in rows)
    # display-only: the names still ranked (fundamentals-only lens) — verdicts unaffected.
    assert all(r.verdict for r in result.ranked if not r.excluded)


def test_band_requested_but_zero_bars_renders_the_section_with_the_reason():
    result = run_rank_pipeline(
        ["P", "Q"], "magic_formula_v1", ranker_only=True, with_valuation_band=True,
        strategies_dir=STRAT_DIR, adapter=_ZeroBarPriceAdapter(), today=TODAY)
    rows = valuation_band_rows(result)
    assert {n for n, _ in rows} == {"P", "Q"}
    assert all("no price bars returned" in b for _, b in rows)


def test_band_not_requested_stays_silent_even_when_the_fetch_would_fail(tmp_path):
    # REGRESSION: band OFF must produce NO section, even with an adapter whose band fetch
    # would fail — the failure path is never entered, output byte-identical to a no-band run.
    result = run_rank_pipeline(
        ["P", "Q"], "magic_formula_v1", ranker_only=True,
        strategies_dir=STRAT_DIR, adapter=_RaisingPriceAdapter(), today=TODAY)
    assert result.meta["with_valuation_band"] is False
    assert valuation_band_rows(result) == []                  # no section at all


def test_band_requested_and_all_compute_is_unchanged_no_failure_text():
    result = run_rank_pipeline(
        ["P", "Q"], "magic_formula_v1", ranker_only=True, with_valuation_band=True,
        strategies_dir=STRAT_DIR, adapter=_BandAdapter(), today=TODAY)
    rows = valuation_band_rows(result)
    assert {n for n, _ in rows} == {"P", "Q"}
    assert all("percentile of own 5-year" in b for _, b in rows)
    assert not any("not evaluated" in b for _, b in rows)     # real bands, no abstention text
