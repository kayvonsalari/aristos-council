"""LENS-EXPAND-1a — the Quality and Earnings Power Value lenses.

Factor maths on hand-computable fixtures (net cash, negative EBIT, missing years), the named EPV
constants pinned against the lens file, and the two lenses as real voters in a company report (and
absent in ETF mode). That the EXISTING lenses did not move is ``test_lens_expand_golden.py``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from aristos_council.company_report import run_company_report
from aristos_council.data.adapter import Fundamentals, MarketDataAdapter, PriceBar, PriceHistory
from aristos_council.demo_surface import ETFS, STOCKS, asset_mode_filter
from aristos_council.factors import (CurrencyConversion, FactorInputs, compute_factor_outcomes,
                                     epv_reading_for)
from aristos_council.market_index import SOURCE_EODHD_LISTING, IndexRow
from aristos_council.strategy.discovery import visible_rank_strategies
from aristos_council.strategy.rank_loader import load_rank_strategy
from aristos_council.tools import quality_epv as q

ROOT = Path(__file__).resolve().parents[1]
STRAT_DIR = ROOT / "strategies"
UNIV_DIR = ROOT / "universes"
QUALITY, EPV = "quality_v1", "epv_v1"
TODAY = date(2026, 6, 30)


def _f(**kw) -> Fundamentals:
    return Fundamentals(ticker="X", name="X", **kw)


# --------------------------------------------------------------------------- #
# gross profit / total assets
# --------------------------------------------------------------------------- #
def test_gross_profitability_is_gross_profit_over_assets_of_the_latest_year():
    value, _ = q.gross_profitability(_f(gross_profit_annual=[400.0, 380.0],
                                        total_assets_annual=[1000.0, 900.0]))
    assert value == pytest.approx(0.40)


@pytest.mark.parametrize("kw, reason", [
    (dict(total_assets_annual=[1000.0]), "gross profit unavailable"),
    (dict(gross_profit_annual=[400.0]), "total assets unavailable"),
    (dict(gross_profit_annual=[400.0], total_assets_annual=[0.0]), "total assets not positive"),
])
def test_gross_profitability_abstains_with_a_reason_never_a_zero(kw, reason):
    value, note = q.gross_profitability(_f(**kw))
    assert value is None and reason in note


# --------------------------------------------------------------------------- #
# worst-year ROIC
# --------------------------------------------------------------------------- #
def _roic_f(oi, ic, tax=None, pretax=None):
    return _f(operating_income=oi, invested_capital=ic,
              tax_provision=tax if tax is not None else [0.0] * len(oi),
              pretax_income=pretax if pretax is not None else [o for o in oi])


def test_worst_year_roic_is_the_minimum_not_the_mean():
    # no tax -> ROIC per year = OI / IC = 0.20, 0.10, 0.30, 0.25, 0.15
    f = _roic_f([200.0, 100.0, 300.0, 250.0, 150.0], [1000.0] * 5)
    value, note = q.worst_year_roic(f)
    assert value == pytest.approx(0.10) and "5 fiscal years" in note


def test_worst_year_roic_looks_at_five_years_only():
    f = _roic_f([200.0] * 5 + [-500.0], [1000.0] * 6)
    assert q.worst_year_roic(f)[0] == pytest.approx(0.20)            # the 6th year is out of the window


def test_a_loss_year_is_a_negative_worst_year_not_an_abstention():
    f = _roic_f([200.0, -100.0, 200.0, 200.0], [1000.0] * 4)
    assert q.worst_year_roic(f)[0] == pytest.approx(-0.10)


def test_worst_year_roic_abstains_below_three_years():
    value, note = q.worst_year_roic(_roic_f([200.0, 210.0], [1000.0, 1000.0]))
    assert value is None and "only 2 fiscal year(s)" in note and "needs 3" in note
    assert q.worst_year_roic(_f())[0] is None                        # no series at all


def test_worst_year_roic_uses_each_years_effective_tax_rate():
    f = _roic_f([200.0] * 3, [1000.0] * 3, tax=[40.0, 100.0, 40.0], pretax=[200.0] * 3)
    # rates 20%, 50%, 20% -> NOPAT 160, 100, 160 -> worst 10%
    assert q.worst_year_roic(f)[0] == pytest.approx(0.10)


# --------------------------------------------------------------------------- #
# net debt / EBIT
# --------------------------------------------------------------------------- #
def test_net_debt_to_ebit_is_debt_less_cash_over_latest_ebit():
    value, _ = q.net_debt_to_ebit(_f(total_debt=900.0, total_cash=300.0, ebit=[200.0, 150.0]))
    assert value == pytest.approx(3.0)


def test_net_cash_is_negative_and_ranks_best_under_the_low_direction():
    value, _ = q.net_debt_to_ebit(_f(total_debt=100.0, total_cash=500.0, ebit=[200.0]))
    assert value == pytest.approx(-2.0)


def test_zero_or_negative_ebit_abstains_with_the_reason_shown():
    for ebit in (-50.0, 0.0):
        value, note = q.net_debt_to_ebit(_f(total_debt=100.0, total_cash=50.0, ebit=[ebit]))
        assert value is None and "zero or negative" in note


def test_net_debt_to_ebit_abstains_on_missing_balance_sheet_figures():
    assert "cash unavailable" in q.net_debt_to_ebit(_f(total_debt=1.0, ebit=[5.0]))[1]
    assert "total debt unavailable" in q.net_debt_to_ebit(_f(total_cash=1.0, ebit=[5.0]))[1]
    assert "no operating-profit" in q.net_debt_to_ebit(_f(total_debt=1.0, total_cash=1.0))[1]


# --------------------------------------------------------------------------- #
# Earnings Power Value
# --------------------------------------------------------------------------- #
def _epv_f(**kw):
    base = dict(total_revenue=[1000.0, 900.0, 900.0, 800.0, 800.0],
                ebit=[100.0, 90.0, 90.0, 80.0, 80.0])        # margin 10% every year
    base.update(kw)
    return _f(**base)


def test_epv_arithmetic_end_to_end_on_round_numbers():
    r = q.earnings_power_value(_epv_f(), ev=600.0)
    assert r.avg_margin == pytest.approx(0.10) and r.margin_years == 5
    assert r.normalised_ebit == pytest.approx(100.0)                       # 10% x latest revenue 1000
    assert r.tax_rate == 0.25 and r.tax_source == "flat 25%"
    assert r.epv == pytest.approx(100.0 * 0.75 / 0.09)                     # 833.33
    assert r.margin_of_safety == pytest.approx(100.0 * 0.75 / 0.09 / 600.0 - 1.0)   # +38.9%


def test_epv_uses_the_five_year_average_margin_not_the_latest_years():
    f = _epv_f(ebit=[300.0, 90.0, 90.0, 80.0, 80.0])                       # a peak year
    r = q.earnings_power_value(f, ev=1000.0)
    assert r.avg_margin == pytest.approx((0.30 + 0.10 + 0.10 + 0.10 + 0.10) / 5)


def test_a_reliable_effective_tax_rate_replaces_the_flat_one_and_says_so():
    f = _epv_f(tax_provision=[20.0] * 5, pretax_income=[100.0] * 5)        # 20% for five years
    r = q.earnings_power_value(f, ev=600.0)
    assert r.tax_rate == pytest.approx(0.20) and r.tax_source.startswith("effective 20.0%")


@pytest.mark.parametrize("tax, pretax", [
    ([-30.0] * 5, [100.0] * 5),            # tax credits: a negative rate is not reliable
    ([90.0] * 5, [100.0] * 5),             # 90%: a one-off, not a rate
    ([20.0] * 2, [100.0] * 2),             # two years is not enough to trust
    ([20.0] * 5, [100.0] * 4),             # lines that do not line up
])
def test_an_unreliable_effective_rate_falls_back_to_the_flat_rate(tax, pretax):
    r = q.earnings_power_value(_epv_f(tax_provision=tax, pretax_income=pretax), ev=600.0)
    assert r.tax_rate == q.EPV_TAX_RATE and r.tax_source == "flat 25%"


def test_negative_normalised_profit_has_no_value_and_a_reason():
    f = _epv_f(ebit=[-50.0, -40.0, -30.0, -30.0, -20.0])
    r = q.earnings_power_value(f, ev=600.0)
    assert r.margin_of_safety is None and "negative or zero" in r.note


def test_epv_abstains_without_enough_years_or_an_enterprise_value():
    short = q.earnings_power_value(_f(total_revenue=[1000.0, 900.0], ebit=[100.0, 90.0]), ev=600.0)
    assert short.margin_of_safety is None and "needs 3" in short.note
    assert "enterprise value unavailable" in q.earnings_power_value(_epv_f(), ev=None).note
    assert "not positive" in q.earnings_power_value(_epv_f(), ev=-5.0).note
    assert q.earnings_power_value(None, ev=1.0).margin_of_safety is None


def test_epv_converts_the_accounts_currency_profit_into_the_price_currency():
    r = q.earnings_power_value(_epv_f(), ev=600.0, fx_rate=0.5)
    assert r.epv == pytest.approx(100.0 * 0.5 * 0.75 / 0.09)


def test_the_constants_in_the_lens_file_are_the_constants_the_maths_uses():
    c = yaml.safe_load((STRAT_DIR / "epv_v1.yaml").read_text(encoding="utf-8"))["constants"]
    assert c == {"tax_rate": q.EPV_TAX_RATE, "cost_of_capital": q.EPV_COST_OF_CAPITAL,
                 "margin_years": q.EPV_MARGIN_YEARS, "min_years": q.EPV_MIN_YEARS}
    assert (q.EPV_TAX_RATE, q.EPV_COST_OF_CAPITAL) == (0.25, 0.09)


# --------------------------------------------------------------------------- #
# the factors, through the registry (the source tag carries the reason)
# --------------------------------------------------------------------------- #
def test_the_registry_factors_carry_their_reason_in_the_source_tag():
    fi = FactorInputs(ticker="X", fundamentals=_f(total_debt=10.0, total_cash=1.0, ebit=[-5.0]))
    out = compute_factor_outcomes(fi, ["net_debt_to_ebit", "gross_profitability",
                                       "worst_year_roic", "epv_margin_of_safety"])
    assert out["net_debt_to_ebit"][0] is None
    assert out["net_debt_to_ebit"][1].startswith("abstained: operating profit (EBIT) is zero or negative")
    assert out["gross_profitability"][1].startswith("abstained: gross profit unavailable")
    assert out["worst_year_roic"][1].startswith("abstained: only 0 fiscal year(s)")
    assert out["epv_margin_of_safety"][1].startswith("abstained:")


def test_the_epv_factor_uses_the_same_enterprise_value_as_earnings_yield_and_the_fx_rate():
    f = _epv_f(market_cap=500.0, total_debt=200.0, total_cash=100.0)       # EV = 600
    fi = FactorInputs(ticker="X", fundamentals=f)
    assert epv_reading_for(fi).margin_of_safety == pytest.approx(100.0 * 0.75 / 0.09 / 600.0 - 1.0)
    fx = FactorInputs(ticker="X", fundamentals=f,
                      fx=CurrencyConversion(rate=2.0, from_ccy="DKK", to_ccy="USD", as_of="2026-06-30"))
    # EV = 500 + (200-100)*2 = 700; profit converted at 2.0
    assert epv_reading_for(fx).margin_of_safety == pytest.approx(100.0 * 2.0 * 0.75 / 0.09 / 700.0 - 1.0)
    failed = FactorInputs(ticker="X", fundamentals=f, fx_failed=True)
    assert epv_reading_for(failed).margin_of_safety is None            # never a mixed-currency EV


# --------------------------------------------------------------------------- #
# the lenses: loaded, voting, equity-only, in the right mode
# --------------------------------------------------------------------------- #
def test_both_lenses_load_as_voting_equity_lenses_with_the_stated_gates_and_no_screen():
    for sid, factors in ((QUALITY, ["gross_profitability", "worst_year_roic", "net_debt_to_ebit"]),
                         (EPV, ["epv_margin_of_safety"])):
        s = load_rank_strategy(STRAT_DIR / f"{sid}.yaml")
        assert [f.name for f in s.factors] == factors
        assert s.kind == "selector" and (s.kind or "selector") != "check"      # votes
        assert s.asset_kinds == ["equity"] and s.min_market_cap == 5.0e9
        assert s.exclude_sectors == ["Financial Services", "Financials"]
        assert s.council_screen_strategy is None and s.prefilter_screen is False   # no entry rules
        assert s.cut == "quintile" and s.missing == "worst"
        assert "worth at least $5bn (not banks or insurers)" in s.asks
    assert load_rank_strategy(STRAT_DIR / f"{QUALITY}.yaml").asks.startswith(
        "A strong, durable business: high gross profit on its assets")
    assert load_rank_strategy(STRAT_DIR / f"{EPV}.yaml").asks.startswith(
        "Cheap for the profit it already makes")


def test_the_lenses_appear_in_stock_mode_and_never_in_etf_mode():
    stock_ok, _ = asset_mode_filter(STOCKS)
    etf_ok, _ = asset_mode_filter(ETFS)
    visible = {s.id: s for s in visible_rank_strategies(STRAT_DIR)}
    for sid in (QUALITY, EPV):
        assert sid in visible
        assert stock_ok(visible[sid]) and not etf_ok(visible[sid])


def _row(ticker, code, *, name=""):
    return IndexRow(
        ticker=ticker, yahoo_ticker=code, name=name or f"{code} Corp", exchange="US", market="US",
        currency="USD", sector="Industrials", industry="Machinery",
        gics_sector="Industrials", gics_industry="Machinery", gics_subindustry="Industrial Machinery",
        market_cap=2e10, market_cap_usd=2e10, market_cap_usd_source="computed", primary_ticker=ticker,
        isin=f"XX{abs(hash(ticker)) % 10**10:010d}", fetched_at="2026-09-26", source=SOURCE_EODHD_LISTING)


class _Store:
    def __init__(self, rows):
        self._rows = rows

    def load(self):
        return list(self._rows)


class _Adapter(MarketDataAdapter):
    """Peers P00..P12 improve with their number on every leg; CO is mid-table. ``CO``'s lines can be
    knocked out to test abstention."""

    name = "fake"

    def __init__(self, **company):
        self.company = company

    def get_fundamentals(self, ticker):
        n = 6 if ticker == "CO" else int(ticker[1:])
        kw = dict(
            market_cap=2e10, sector="Industrials", currency="USD", financial_currency="USD",
            ebit=[500.0 + 100 * n] * 5, operating_income=[500.0 + 100 * n] * 5, pe_ratio=20.0 - n,
            tax_provision=[100.0] * 5, pretax_income=[600.0] * 5,
            invested_capital=[4000.0 - 100.0 * n] * 5,
            total_revenue=[3000.0] * 5, gross_profit_annual=[900.0 + 80.0 * n] * 5,
            total_assets_annual=[5000.0] * 5, total_debt=2000.0 - 100.0 * n, total_cash=300.0)
        if ticker == "CO":
            kw.update(self.company)
        return Fundamentals(ticker=ticker, name=f"{ticker} Corp", company_name=f"{ticker} Corp", **kw)

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99, close=100 + 0.1 * i,
                     adj_close=100 + 0.1 * i, volume=10) for i in range(300)])

    def get_dividend_history(self, ticker, *, start, end):
        return []


def _no_news(ticker, *, today):
    from aristos_council.data.news_fallback import NewsFetchResult
    return NewsFetchResult(items=(), source="", tried=("test fixture: no news modelled",))


def _run(lens_ids, tmp_path, **company):
    store = _Store([_row("CO.US", "CO", name="Company Co")]
                   + [_row(f"P{i:02d}.US", f"P{i:02d}") for i in range(13)])
    return run_company_report("CO", lens_ids, adapter=_Adapter(**company), strategies_dir=STRAT_DIR,
                              universes_dir=UNIV_DIR, runs_dir=tmp_path / "runs", today=TODAY,
                              store=store, save=False, news_fetcher=_no_news)


def test_both_lenses_rank_the_company_and_count_as_equal_votes(tmp_path):
    report = _run([QUALITY, EPV, "magic_formula_raw_v1"], tmp_path)
    by_id = {v.strategy_id: v for v in report.votes}
    for sid in (QUALITY, EPV):
        v = by_id[sid]
        assert v.ranked and v.votes and v.verdict in ("buy", "hold", "sell")
        assert v.cohort_size == 14 and 1 <= v.position <= 14
    ag = report.agreement
    assert ag.n_ticked == 3 and ag.n_voted == 3 and ag.n_not_applying == 0
    assert ag.buy_votes + len(ag.hold) + len(ag.sell) == 3


def test_a_company_the_other_lenses_screen_out_still_gets_a_quality_and_an_epv_reading(tmp_path):
    """The reason these lenses exist: Growth wants 10% revenue growth; this company has none."""
    report = _run([QUALITY, EPV, "growth_garp_v2"], tmp_path)
    by_id = {v.strategy_id: v for v in report.votes}
    assert not by_id["growth_garp_v2"].ranked
    assert by_id[QUALITY].ranked and by_id[EPV].ranked
    assert report.agreement.n_voted == 2 and report.agreement.n_not_applying == 1


def test_a_missing_leg_ranks_last_on_it_with_the_reason_shown_never_zero(tmp_path):
    from aristos_council.pipeline import run_rank_pipeline
    adapter = _Adapter(gross_profit_annual=[], total_assets_annual=[])
    tickers = ["CO"] + [f"P{i:02d}" for i in range(13)]
    res = run_rank_pipeline(tickers, QUALITY, ranker_only=True, adapter=adapter, today=TODAY,
                            strategies_dir=STRAT_DIR, use_cache=False)
    row = next(r for r in res.ranked if r.ticker == "CO")
    assert row.factor_values["gross_profitability"] is None
    assert row.factor_ranks["gross_profitability"] == 14                      # last of 14, not neutral
    assert row.factor_sources["gross_profitability"].startswith("abstained: gross profit unavailable")


def test_negative_operating_profit_ranks_last_on_both_lenses_with_the_reason(tmp_path, monkeypatch):
    """The FACTOR-level handling of a zero-or-negative operating profit (the reason text, the worst
    rank). Since SMALLCAP-BAND-GAP-1's PROFIT GUARD the two lenses no longer reach it - they say
    "does not apply: no operating profit" first (pinned by the next test) - so the guard is switched
    off here to keep exercising the legs themselves."""
    from aristos_council import operating_profit
    from aristos_council.pipeline import run_rank_pipeline
    monkeypatch.setattr(operating_profit, "OPERATING_PROFIT_LENS_IDS", frozenset())
    adapter = _Adapter(ebit=[-400.0] * 5, operating_income=[-400.0] * 5)
    tickers = ["CO"] + [f"P{i:02d}" for i in range(13)]
    q_res = run_rank_pipeline(tickers, QUALITY, ranker_only=True, adapter=adapter, today=TODAY,
                              strategies_dir=STRAT_DIR, use_cache=False)
    e_res = run_rank_pipeline(tickers, EPV, ranker_only=True, adapter=adapter, today=TODAY,
                              strategies_dir=STRAT_DIR, use_cache=False)
    qr = next(r for r in q_res.ranked if r.ticker == "CO")
    er = next(r for r in e_res.ranked if r.ticker == "CO")
    assert qr.factor_ranks["net_debt_to_ebit"] == 14
    assert "zero or negative" in qr.factor_sources["net_debt_to_ebit"]
    assert er.verdict == "sell" and er.cohort_position == 14
    assert "negative or zero" in er.factor_sources["epv_margin_of_safety"]


def test_negative_operating_profit_now_does_not_apply_on_both_lenses(tmp_path):
    """PROFIT GUARD (SMALLCAP-BAND-GAP-1): neither lens ranks a company with no operating profit."""
    from aristos_council.pipeline import run_rank_pipeline
    adapter = _Adapter(ebit=[-400.0] * 5, operating_income=[-400.0] * 5)
    tickers = ["CO"] + [f"P{i:02d}" for i in range(13)]
    for sid in (QUALITY, EPV):
        res = run_rank_pipeline(tickers, sid, ranker_only=True, adapter=adapter, today=TODAY,
                                strategies_dir=STRAT_DIR, use_cache=False)
        assert ("CO", "no operating profit") in [(t, why) for t, why in res.excluded]
        assert all(r.ticker != "CO" for r in res.ranked)
