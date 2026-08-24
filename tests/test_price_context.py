"""PRICE-1 — the share price, its 52-week position, and the reversion value.

The reports could say a name sat at the 70th percentile of its own five-year valuation
band and still never say what one share COST. These tests pin the three plain facts that
close that gap, and — more importantly — the honesty properties around them:

- the price is read off the EXISTING 400-day series (no new fetch, no re-windowing of the
  window ``low_volatility`` consumes), carries its CURRENCY unconverted and the AS-OF DATE
  of the close, so a stale cache is visible rather than silent;
- the 52-week range uses ONLY the trailing 52 weeks of that series and ABSTAINS with the
  REAL span below 40 weeks — a partial range is never dressed as a full one;
- the reversion value is ARITHMETIC on the band's own series (median multiple x today's
  earnings, less today's net debt, over today's shares) and abstains — separately, with
  its own reason — on every input it cannot get;
- price and 52-week position are ALWAYS ON; the reversion value rides with the valuation
  band's flag;
- and none of it moves a verdict, a rank or a factor value.

The reversion arithmetic is checked against numbers worked by hand in the PR description,
not against the implementation restating itself.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from pathlib import Path

import pytest

from aristos_council.data.adapter import (
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
)
from aristos_council.factors import FactorInputs, price_display, reversion_value_display
from aristos_council.pipeline import (
    format_cli_report,
    format_price_lines,
    price_rows,
    run_rank_pipeline,
    valuation_band_rows,
)
from aristos_council.rank_engine import RankedTicker
from aristos_council.tools.price_context import (
    MIN_52W_WEEKS,
    PriceContext,
    format_money,
    price_context,
)
from aristos_council.tools.reversion import ReversionValue, reversion_value
from aristos_council.tools.valuation_band import ValuationBand, valuation_band

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"
TODAY = date(2026, 6, 30)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _weekly(closes: list[float], end: date = TODAY) -> list[PriceBar]:
    """One bar per week, NEWEST LAST: ``closes[0]`` is the oldest. ``closes[-1]`` lands on
    ``end`` itself, so the anchor day is known exactly."""
    n = len(closes)
    return [PriceBar(day=end - timedelta(weeks=(n - 1 - i)), open=c, high=c, low=c,
                     close=c, adj_close=c * 0.9, volume=1)
            for i, c in enumerate(closes)]


def _month_ends(n: int, end: date = TODAY) -> list[date]:
    out: list[date] = []
    y, m = end.year, end.month
    for _ in range(n):
        out.append(date(y, m, calendar.monthrange(y, m)[1]))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def _monthly(closes: list[float], end: date = TODAY) -> list[PriceBar]:
    days = _month_ends(len(closes), end)
    days[-1] = end
    return [PriceBar(day=d, open=c, high=c, low=c, close=c, adj_close=c * 0.9, volume=1)
            for d, c in zip(days, closes)]


def _fundamentals(*, years: int = 8, market_cap: float = 1_000.0, **series) -> Fundamentals:
    """Fundamentals carrying DATED statement series (the only shape the band reads).
    ``series`` maps a series name to a scalar (held constant) or an explicit list."""
    ends = [f"{2025 - i}-12-31" for i in range(years)]
    aligned: dict[str, list] = {}
    period_ends: dict[str, list[str]] = {}
    passthrough = {}
    for name, value in series.items():
        if name in ("eps", "currency", "financial_currency", "ticker"):
            passthrough[name] = value
            continue
        values = value if isinstance(value, list) else [value] * years
        aligned[name] = list(values)
        period_ends[name] = ends[:len(values)]
    passthrough.setdefault("ticker", "X")
    return Fundamentals(market_cap=market_cap, aligned_annual=aligned,
                        aligned_period_ends=period_ends, **passthrough)


# The HAND-CHECKED reversion fixture (worked in the PR description):
#   market cap now = 1,000 · last close = 100  -> the price relative scales mcap by 10x
#   EBIT = 100, total debt = 200, cash = 50    -> net debt = 150 at every month
#   multiple_t = (10 x close_t + 150) / 100    -> close 50 -> 6.5 ; close 100 -> 11.5
#   61 months: 31 at close 50, then 30 at close 100 (the newest)
#     -> sorted: 31 x 6.5 then 30 x 11.5 -> MEDIAN (31st of 61) = 6.5 ; current = 11.5
#   implied EV     = 100 x 6.5 = 650
#   implied equity = 650 - 150  = 500
#   shares         = 10         -> reversion price = 500 / 10 = 50.00
#   gap            = 50 / 100 - 1 = -0.50 -> "-50%"
_HAND_CLOSES = [50.0] * 31 + [100.0] * 30


def _hand_band() -> tuple[ValuationBand, Fundamentals]:
    f = _fundamentals(ebit=100.0, total_debt=200.0, cash=50.0, shares_outstanding=10.0,
                      currency="USD")
    return valuation_band(_monthly(_HAND_CLOSES), f, asof=TODAY), f


# --------------------------------------------------------------------------- #
# 1. The share price — currency stated, never converted; as-of date always shown
# --------------------------------------------------------------------------- #
def test_the_price_line_states_the_close_its_date_and_its_currency():
    ctx = price_context(_weekly([100.0] * 60), currency="USD")

    assert ctx.available
    assert ctx.last_close == 100.0
    assert ctx.as_of == TODAY
    assert ctx.price_display == "$100.00 (as of 2026-06-30)"


def test_the_price_is_the_traded_close_not_the_dividend_adjusted_series():
    """``adj_close`` is a TOTAL-RETURN level, not a share price — for a dividend payer it
    sits below the traded price in the past, which would drag the 52-week low down and
    flatter the position. The fixture's adj_close is deliberately 0.9x the close."""
    bars = _weekly([100.0] * 60)
    assert bars[-1].adj_close == pytest.approx(90.0)          # the trap
    assert price_context(bars, currency="USD").last_close == 100.0


def test_a_stale_cache_shows_in_the_as_of_date_rather_than_silently():
    stale_end = TODAY - timedelta(days=40)
    ctx = price_context(_weekly([100.0] * 60, end=stale_end), currency="USD")
    assert ctx.as_of == stale_end
    assert "(as of 2026-05-21)" in ctx.price_display


def test_no_price_bars_abstains_with_a_reason_and_drops_the_range_clause():
    ctx = price_context([], currency="USD")
    assert not ctx.available
    assert ctx.display == "price not available — no price history returned for this name"


# --------------------------------------------------------------------------- #
# 2. The 52-week position — a KNOWN fixture gives a KNOWN high/low/position
# --------------------------------------------------------------------------- #
def _range_bars(inside: list[float], outside: list[float]) -> list[PriceBar]:
    """``inside`` are the closes of the trailing 52 weeks (newest last, landing on TODAY);
    ``outside`` are OLDER closes that sit beyond the window and must be ignored."""
    return _weekly(list(outside) + list(inside))


def test_the_52_week_range_uses_only_the_trailing_52_weeks():
    """The 400-day fetch is ~13 months, so presenting all of it as "the 52-week range"
    would quietly widen it. The two closes outside the window are extreme on purpose."""
    inside = [90.0] * 20 + [120.0] + [80.0] + [90.0] * 31          # 53 weekly closes
    outside = [500.0, 1.0, 500.0, 1.0, 500.0, 1.0, 500.0]          # older — must not count
    ctx = price_context(_range_bars(inside, outside), currency="USD")

    assert ctx.high_52w == 120.0 and ctx.low_52w == 80.0
    # (90 - 80) / (120 - 80) = 0.25
    assert ctx.position_pct == 25.0
    assert ctx.range_display == ("25% of its 52-week range (low $80.00 · high $120.00)")
    assert ctx.display == ("$90.00 (as of 2026-06-30) — 25% of its 52-week range "
                           "(low $80.00 · high $120.00)")


def test_a_name_at_exactly_its_52_week_high_reads_100_percent():
    inside = [80.0] * 30 + [100.0] * 22 + [120.0]      # today's close IS the high
    ctx = price_context(_range_bars(inside, [500.0, 1.0]), currency="USD")
    assert ctx.high_52w == 120.0 and ctx.last_close == 120.0
    assert ctx.position_pct == 100.0


def test_a_name_at_exactly_its_52_week_low_reads_0_percent():
    inside = [120.0] * 30 + [100.0] * 22 + [80.0]      # today's close IS the low
    ctx = price_context(_range_bars(inside, [500.0, 1.0]), currency="USD")
    assert ctx.low_52w == 80.0 and ctx.last_close == 80.0
    assert ctx.position_pct == 0.0


def test_short_history_abstains_with_its_REAL_span_and_still_shows_the_price():
    """31 weekly bars span 30 weeks — under the 40-week floor. The RANGE abstains with the
    true span; the PRICE still renders, because the two facts fail independently."""
    ctx = price_context(_weekly([100.0] * 31), currency="USD")

    assert ctx.available and ctx.last_close == 100.0           # price survives
    assert not ctx.range_available
    assert ctx.position_pct is None                            # never a fabricated middle
    assert ctx.range_display == "52-week range not evaluated — only 30 weeks of closes"
    assert ctx.display.startswith("$100.00 (as of 2026-06-30) — 52-week range not "
                                  "evaluated")


def test_the_40_week_floor_is_the_documented_boundary_not_an_accident():
    just_under = price_context(_weekly([100.0] * MIN_52W_WEEKS), currency="USD")
    just_over = price_context(_weekly([100.0] * (MIN_52W_WEEKS + 1)), currency="USD")
    assert just_under.weeks_covered == float(MIN_52W_WEEKS - 1)
    assert not just_under.range_available                      # 39 weeks of span
    assert just_over.range_available                           # 40 weeks of span


def test_a_price_that_never_moved_reads_100_percent_at_its_own_flat_high():
    """A zero-width range is degenerate — today's close IS the high, and the stated rule
    "at the high reads 100%" governs rather than a division by zero."""
    ctx = price_context(_weekly([100.0] * 60), currency="USD")
    assert ctx.high_52w == ctx.low_52w == 100.0
    assert ctx.position_pct == 100.0


# --------------------------------------------------------------------------- #
# 3. Currency — stated, never converted, never assumed
# --------------------------------------------------------------------------- #
def test_a_non_usd_name_shows_its_own_currency_unconverted_everywhere():
    bars = _range_bars([90.0] * 20 + [120.0] + [80.0] + [90.0] * 31, [500.0])
    ctx = price_context(bars, currency="EUR")
    assert ctx.display == ("€90.00 (as of 2026-06-30) — 25% of its 52-week range "
                           "(low €80.00 · high €120.00)")
    assert "$" not in ctx.display                              # no silent USD anywhere

    f = _fundamentals(ebit=100.0, total_debt=200.0, cash=50.0, shares_outstanding=10.0,
                      currency="EUR", financial_currency="EUR")
    band = valuation_band(_monthly(_HAND_CLOSES), f, asof=TODAY)
    rev = reversion_value(band, f, last_close=100.0, currency="EUR")
    assert rev.display.startswith("reversion value €50.00 (-50%)")
    assert "$" not in rev.display


def test_an_unknown_currency_is_stated_as_unknown_and_never_assumed_usd():
    ctx = price_context(_weekly([100.0] * 60), currency=None)
    assert "$" not in ctx.display
    assert ctx.display.startswith("100.00 (as of 2026-06-30)")
    assert "currency not reported" in ctx.display
    # ...and stated ONCE per line, not after every amount.
    assert ctx.display.count("currency not reported") == 1


def test_format_money_prefixes_a_symbol_or_the_iso_code_and_never_converts():
    assert format_money(27.14, "USD") == "$27.14"
    assert format_money(61.3, "EUR") == "€61.30"
    assert format_money(84.2, "CHF") == "CHF 84.20"
    assert format_money(1234.5, "SGD") == "SGD 1,234.50"       # unknown symbol -> code
    assert format_money(27.14, None) == "27.14"                # bare; noted once per line


# --------------------------------------------------------------------------- #
# 4. Determinism
# --------------------------------------------------------------------------- #
def test_the_same_bars_in_any_order_give_the_same_price_and_range():
    bars = _range_bars([90.0 + (i * 7) % 23 for i in range(53)], [500.0, 1.0])
    first = price_context(bars, currency="USD")
    assert first == price_context(list(reversed(bars)), currency="USD")
    assert first == price_context(sorted(bars, key=lambda b: b.close), currency="USD")
    assert first.range_available


# --------------------------------------------------------------------------- #
# 5. The reversion value — HAND-CHECKED arithmetic (see the fixture comment above)
# --------------------------------------------------------------------------- #
def test_the_reversion_value_matches_the_hand_worked_arithmetic():
    band, f = _hand_band()

    assert band.available
    assert band.median_multiple == 6.5           # 31st of 61 sorted multiples
    assert band.current == 11.5                  # today's multiple
    assert band.current_earnings == 100.0        # EBIT, point-in-time at the last month
    assert band.current_net_debt == 150.0        # 200 debt - 50 cash, same month
    assert band.current_shares == 10.0

    rev = reversion_value(band, f, last_close=100.0, currency="USD")
    assert rev.available
    assert rev.price == pytest.approx(50.0)      # (100 x 6.5 - 150) / 10
    assert rev.gap == pytest.approx(-0.5)        # 50 / 100 - 1
    assert rev.median_multiple == 6.5
    assert rev.display == ("reversion value $50.00 (-50%) if EV/EBIT returned to its own "
                           "5-year median of 6.5x; 61 of 61 months usable")


def test_the_reversion_value_reconciles_with_the_price_at_the_current_multiple():
    """The consistency property that makes the number auditable: feed the band's CURRENT
    multiple in place of its median and the implied price must come back to the last
    close. Anything else means the reversion is built from different months, statements or
    share counts than the percentile above it."""
    band, f = _hand_band()
    at_current = reversion_value(
        ValuationBand(**{**band.__dict__, "median_multiple": band.current}),
        f, last_close=100.0, currency="USD")
    assert at_current.price == pytest.approx(100.0)
    assert at_current.gap == pytest.approx(0.0)


def test_the_pe_basis_reverts_on_eps_times_the_median_pe():
    """No EBIT and no dated debt/cash: the band falls back to the labelled P/E route, and
    the reversion value is simply EPS x the median P/E.

    HAND CHECK: mcap_t = 10 x close_t, net income = 80 -> multiple_t = close_t / 8, so the
    61 months are 31 x 6.25 then 30 x 12.5 and the MEDIAN is 6.25. EPS 4.00 x 6.25 = 25.00,
    and 25 / 100 - 1 = -0.75."""
    f = _fundamentals(net_income=80.0, shares_outstanding=10.0, eps=4.0, currency="USD")
    band = valuation_band(_monthly(_HAND_CLOSES), f, asof=TODAY)
    assert band.basis == "pe" and band.median_multiple == 6.25

    rev = reversion_value(band, f, last_close=100.0, currency="USD")
    assert rev.price == pytest.approx(25.0)
    assert rev.gap == pytest.approx(-0.75)
    assert rev.display == ("reversion value $25.00 (-75%) if P/E returned to its own "
                           "5-year median of 6.2x; 61 of 61 months usable")


def test_an_extreme_reversion_value_is_shown_as_is_never_clamped():
    """A name whose own median multiple is far from today's produces a big number. It is
    rendered as-is BESIDE the median that produced it, so a reader can audit it — capping
    or smoothing would hide exactly the case worth looking at."""
    # 31 months at close 1000 (multiple 101.5), 30 at close 100 (11.5) -> median 101.5.
    f = _fundamentals(ebit=100.0, total_debt=200.0, cash=50.0, shares_outstanding=10.0,
                      currency="USD")
    band = valuation_band(_monthly([1000.0] * 31 + [100.0] * 30), f, asof=TODAY)
    rev = reversion_value(band, f, last_close=100.0, currency="USD")

    assert band.median_multiple == 101.5
    assert rev.price == pytest.approx((100.0 * 101.5 - 150.0) / 10.0)   # 1,000.00
    assert rev.gap == pytest.approx(9.0)                                # +900%
    assert "+900%" in rev.display and "median of 101.5x" in rev.display


# --------------------------------------------------------------------------- #
# 6. Every abstention trigger, each stating its own reason
# --------------------------------------------------------------------------- #
def test_abstains_when_the_band_itself_abstained():
    f = _fundamentals(ebit=100.0, total_debt=200.0, cash=50.0, shares_outstanding=10.0)
    short = valuation_band(_monthly([100.0] * 18), f, asof=TODAY)     # ~1.4y
    assert not short.available

    rev = reversion_value(short, f, last_close=100.0, currency="USD")
    assert not rev.available
    assert rev.note == "the valuation band abstained for this name"
    assert rev.display.startswith("reversion value not evaluated — ")


def test_abstains_when_the_band_was_never_computed_on_this_run():
    rev = reversion_value(None, _fundamentals(), last_close=100.0, currency="USD")
    assert not rev.available
    assert rev.note == "the valuation band was not computed on this run"


def test_abstains_when_ebit_is_not_positive_on_the_ev_basis():
    band, f = _hand_band()
    rev = reversion_value(ValuationBand(**{**band.__dict__, "current_earnings": -5.0}),
                          f, last_close=100.0, currency="USD")
    assert not rev.available
    assert rev.note == ("EBIT is not positive, so an EV/EBIT reversion value has no "
                        "meaning")


def test_abstains_when_eps_is_not_positive_on_the_pe_basis():
    f = _fundamentals(net_income=80.0, eps=-1.25, currency="USD")
    band = valuation_band(_monthly(_HAND_CLOSES), f, asof=TODAY)
    assert band.basis == "pe"

    rev = reversion_value(band, f, last_close=100.0, currency="USD")
    assert not rev.available
    assert rev.note == "EPS (TTM) is not positive, so a P/E reversion value has no meaning"


def test_abstains_when_shares_outstanding_are_missing():
    """No dated share count at all — the band still computes (it never reads one), so this
    is the abstention that has to be its OWN reason rather than the band's."""
    f = _fundamentals(ebit=100.0, total_debt=200.0, cash=50.0, currency="USD")
    band = valuation_band(_monthly(_HAND_CLOSES), f, asof=TODAY)
    assert band.available and band.current_shares is None

    rev = reversion_value(band, f, last_close=100.0, currency="USD")
    assert not rev.available
    assert rev.note == "shares outstanding unavailable"


def test_abstains_when_shares_outstanding_are_not_positive():
    band, f = _hand_band()
    rev = reversion_value(ValuationBand(**{**band.__dict__, "current_shares": 0.0}),
                          f, last_close=100.0, currency="USD")
    assert not rev.available and rev.note == "shares outstanding unavailable"


def test_abstains_when_net_debt_is_not_computable_on_the_bands_basis():
    band, f = _hand_band()
    rev = reversion_value(ValuationBand(**{**band.__dict__, "current_net_debt": None}),
                          f, last_close=100.0, currency="USD")
    assert not rev.available
    assert rev.note == "net debt is not computable on the band's basis"


def test_abstains_when_the_last_close_is_missing():
    band, f = _hand_band()
    rev = reversion_value(band, f, last_close=None, currency="USD")
    assert not rev.available
    assert rev.note == "last close unavailable — the gap cannot be stated"


def test_abstains_when_the_implied_equity_value_is_not_positive():
    """Net debt swamping the implied enterprise value is not a share price of zero or a
    negative one — it is a calculation with no meaning, so it says so."""
    band, f = _hand_band()
    swamped = ValuationBand(**{**band.__dict__, "current_net_debt": 100_000.0})
    rev = reversion_value(swamped, f, last_close=100.0, currency="USD")
    assert not rev.available
    assert rev.note.startswith("implied equity value is not positive at the median "
                               "multiple")


# --------------------------------------------------------------------------- #
# 7. The band line reads in plain English (PRICE-1 item 4) — wording only
# --------------------------------------------------------------------------- #
def test_the_band_line_leads_with_the_multiple_and_explains_the_month_count():
    band, _ = _hand_band()
    # 31 months below today's 11.5 and 30 equal to it -> mid-rank 75.4 -> "75th".
    assert band.percentile == pytest.approx(75.4)
    assert band.display == (
        "EV/EBIT 11.5 — 75th percentile of its own 5-year range "
        "(dearer than 75% of the last five years; based on 61 of 61 months)")


def test_the_band_line_says_why_the_uncounted_months_are_missing():
    f = _fundamentals(net_income=[80.0, 80.0, 80.0, 80.0, -10.0, -10.0, -10.0, -10.0])
    band = valuation_band(_monthly([100.0] * 61), f, asof=TODAY)
    assert 0 < band.months_covered < band.months_total
    assert (f"based on {band.months_covered} of {band.months_total} months, "
            "the rest lack usable statements") in band.display


# --------------------------------------------------------------------------- #
# 8. Wiring — the four surfaces, from ONE source
# --------------------------------------------------------------------------- #
# P outearns Q on both magic_formula_v1 legs, so the cohort has a REAL order to pin (a
# tied cohort would make the regression snapshot prove nothing).
_PIPE_FUND = {
    "P": dict(market_cap=2e10, sector="Technology", ebit=[3000.0], pe_ratio=10.0,
              operating_income=[3000.0, 2800, 2600, 2400], tax_provision=[600.0] * 4,
              pretax_income=[2900.0, 2700, 2500, 2300], invested_capital=[5000.0] * 4,
              total_revenue=[200.0, 170, 150, 120]),
    "Q": dict(market_cap=2e10, sector="Technology", ebit=[1500.0], pe_ratio=20.0,
              operating_income=[1500.0, 1450, 1400, 1350], tax_provision=[300.0] * 4,
              pretax_income=[1450.0, 1400, 1350, 1300], invested_capital=[5000.0] * 4,
              total_revenue=[150.0, 140, 130, 120]),
}
_PIPE_TRENDS = {"P": [50.0 + i for i in range(60)],       # rising
                "Q": [110.0 - i for i in range(60)]}      # falling


class _PriceAdapter(MarketDataAdapter):
    """Two rateable stocks with dated statements (so the band and the reversion value
    compute) and a monthly close series (so the price and the 52-week range compute)."""

    name = "fake-price"

    def __init__(self, currency: str = "USD"):
        self.currency = currency

    def get_fundamentals(self, ticker):
        ends = [f"{2025 - i}-12-31" for i in range(6)]
        aligned = {"ebit": [_PIPE_FUND[ticker]["ebit"][0]] * 6,
                   "total_debt": [200.0] * 6, "cash": [50.0] * 6,
                   "shares_outstanding": [1e9] * 6}
        return Fundamentals(ticker=ticker, name=ticker, currency=self.currency,
                            aligned_annual=aligned,
                            aligned_period_ends={k: ends for k in aligned},
                            **_PIPE_FUND[ticker])

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=_monthly(_PIPE_TRENDS[ticker]))

    def get_dividend_history(self, ticker, *, start, end):
        return []


def _run(*, band: bool, adapter=None):
    return run_rank_pipeline(
        ["P", "Q"], "magic_formula_v1", ranker_only=True, with_valuation_band=band,
        strategies_dir=STRAT_DIR, adapter=adapter or _PriceAdapter(), today=TODAY)


def _visible_md(result) -> str:
    pytest.importorskip("streamlit")
    import app
    return app._universe_markdown(result)


def test_every_rateable_name_carries_a_price_line_from_one_source():
    result = _run(band=False)
    rows = price_rows(result)
    assert {n for n, _ in rows} == {"P", "Q"}
    assert all("(as of " in line and "52-week range" in line for _, line in rows)


def test_the_four_surfaces_render_the_same_price_lines_and_cannot_drift():
    """CLI, Run tab, markdown record and HTML export all read ``price_rows`` — the same
    discipline ``valuation_band_rows`` uses — so this pins that they agree VERBATIM."""
    from aristos_council.export.report_html import universe_report_html

    result = _run(band=False)
    rows = price_rows(result)
    assert rows                                                # the shared source

    cli = format_cli_report(result)                            # surface 1: CLI
    md = _visible_md(result)                                   # surface 2: markdown
    html_doc = universe_report_html(result)                    # surface 3: HTML
    # surface 4: the Run tab renders `price_rows` directly into its dataframe, so the
    # source IS the assertion target — the same tuples, name and line, in the same order.
    run_tab = [{"Name": n, "Price · 52-week position": line} for n, line in rows]

    import html as _html
    for i, (name, line) in enumerate(rows):
        assert f"{name} {line}" in cli                          # CLI: name then the line
        assert f"- **{name}** — {line}" in md                   # markdown bullet
        assert _html.escape(name, quote=False) in html_doc      # HTML: both, escaped
        assert _html.escape(line, quote=False) in html_doc
        assert run_tab[i] == {"Name": name, "Price · 52-week position": line}


def test_the_cli_block_names_the_section_and_states_it_is_display_only():
    text = "\n".join(format_price_lines(_run(band=False)))
    assert "SHARE PRICE & 52-WEEK POSITION" in text
    assert "not ranked, not screened" in text
    assert "never converted" in text


def test_a_result_with_no_price_lines_renders_no_block_at_all():
    """A hand-built or pre-PRICE-1 result must print exactly what it printed before — an
    empty section header is noise, not information."""
    class _Bare:
        ranked = [RankedTicker(ticker="A", factor_ranks={}, factor_values={},
                               combined_rank=1.0, universe_size=1)]
        names: dict = {}
    assert price_rows(_Bare()) == []
    assert format_price_lines(_Bare()) == []


# --------------------------------------------------------------------------- #
# 9. The flag: price is ALWAYS on, the reversion value rides with the band
# --------------------------------------------------------------------------- #
def test_band_off_keeps_the_price_and_the_52_week_position_and_drops_the_rest():
    off = _run(band=False)
    assert off.meta["with_valuation_band"] is False
    assert valuation_band_rows(off) == []                      # no band section
    assert all(not r.reversion_value for r in off.ranked)      # no reversion value
    rows = price_rows(off)                                     # ...but the price stays
    assert {n for n, _ in rows} == {"P", "Q"}
    assert all("52-week range" in line for _, line in rows)


def test_band_on_adds_the_band_and_the_reversion_value_to_the_same_section():
    on = _run(band=True)
    rows = valuation_band_rows(on)
    assert {n for n, _ in rows} == {"P", "Q"}
    for _, cell in rows:
        assert "percentile of its own 5-year range" in cell    # the band line
        assert "reversion value $" in cell                     # ...and the reversion value
        assert "if EV/EBIT returned to its own 5-year median of" in cell
    # the price section is unchanged by the toggle — it was never gated by it.
    assert price_rows(on) == price_rows(_run(band=False))


def test_an_abstaining_reversion_value_never_hides_the_band_line():
    """No dated share count: the band computes, the reversion value abstains — and BOTH
    are visible, because a silent section is indistinguishable from a switched-off one."""
    class _NoShares(_PriceAdapter):
        def get_fundamentals(self, ticker):
            f = super().get_fundamentals(ticker)
            aligned = {k: v for k, v in f.aligned_annual.items()
                       if k != "shares_outstanding"}
            ends = {k: v for k, v in f.aligned_period_ends.items()
                    if k != "shares_outstanding"}
            return Fundamentals(**{**f.__dict__, "aligned_annual": aligned,
                                   "aligned_period_ends": ends})

    rows = valuation_band_rows(_run(band=True, adapter=_NoShares()))
    for _, cell in rows:
        assert "percentile of its own 5-year range" in cell
        assert "reversion value not evaluated — shares outstanding unavailable" in cell


def test_a_non_usd_cohort_renders_its_own_currency_end_to_end():
    on = _run(band=True, adapter=_PriceAdapter(currency="EUR"))
    for _, line in price_rows(on):
        assert "€" in line and "$" not in line
    for _, cell in valuation_band_rows(on):
        assert "reversion value €" in cell and "$" not in cell


# --------------------------------------------------------------------------- #
# 10. Display-only: nothing here moves a verdict, a rank or a factor value
# --------------------------------------------------------------------------- #
def _grade_snapshot(result):
    """Every number a verdict depends on, for a fixture cohort."""
    return [(r.ticker, r.verdict, r.combined_rank, r.cohort_position,
             tuple(sorted(r.factor_ranks.items())),
             tuple(sorted((k, v) for k, v in r.factor_values.items())))
            for r in result.ranked]


# Pinned from the fixture cohort BEFORE PRICE-1 (verified against the base commit): the
# ranker's output must be byte-identical with the price line, the 52-week position and the
# reversion value present.
_EXPECTED_GRADES = [
    ("P", "buy", 2.0, 1, (("earnings_yield", 1.0), ("roic", 1.0)),
     (("earnings_yield", 1.5e-07), ("roic", 0.41538461538461535))),
    ("Q", "hold", 4.0, 2, (("earnings_yield", 2.0), ("roic", 2.0)),
     (("earnings_yield", 7.5e-08), ("roic", 0.22281818181818186))),
]


def test_the_ranker_output_is_unchanged_by_everything_in_this_change():
    off, on = _run(band=False), _run(band=True)
    assert _grade_snapshot(off) == _grade_snapshot(on)
    assert _grade_snapshot(off) == _EXPECTED_GRADES


def test_the_price_and_the_reversion_value_are_absent_from_the_narrator_evidence():
    """Doctrine: display only. ``explain()`` is what the narrator is handed, so neither
    number may appear in it (the same fence VALBAND-1 put around the band)."""
    on = _run(band=True)
    for r in on.ranked:
        assert r.price_line and r.reversion_value                # both computed...
        assert "52-week" not in r.explain()                      # ...and both fenced out
        assert "reversion" not in r.explain()
        assert "as of" not in r.explain()


def test_no_registered_factor_or_criterion_reads_the_price_or_the_reversion_value():
    from aristos_council.factors import FACTOR_REGISTRY
    from aristos_council.tools.criteria.registry import REGISTRY
    for name in list(FACTOR_REGISTRY) + list(REGISTRY):
        assert "reversion" not in name and "52" not in name
    assert "price_context" not in FACTOR_REGISTRY
    assert "reversion_value" not in REGISTRY


# --------------------------------------------------------------------------- #
# 11. The FactorInputs-level helpers degrade honestly
# --------------------------------------------------------------------------- #
def test_a_factor_inputs_without_a_price_context_renders_an_em_dash():
    assert price_display(FactorInputs(ticker="X")) == "—"


def test_a_factor_inputs_without_a_band_renders_no_reversion_clause_at_all():
    ctx = price_context(_weekly([100.0] * 60), currency="USD")
    assert reversion_value_display(FactorInputs(ticker="X", price_context=ctx)) == ""


def test_the_reversion_value_never_raises_on_absent_inputs():
    assert isinstance(reversion_value(None, None, last_close=None), ReversionValue)
    assert isinstance(reversion_value(ValuationBand(), None, last_close=1.0),
                      ReversionValue)
    assert isinstance(price_context(None), PriceContext)
