"""ABSOLUTE-BACKTEST-1 (docs/DESIGN_absolute.md section j): the backtest of the absolute method, three forms.

Pinned here: the three forms' selection on fake rounds; the own-record rule in the backtest form; the hurdle on a past date
and the no-series case; the as-of base (accounts by filing date); hold, cost and annualising; the checkpoint, resume and
budget stop; the summary file shape; the page sentence with and without a result. Nothing reaches a real adapter: the
accounts, prices, bond rows and benchmarks are fakes built below.
"""
from __future__ import annotations

import csv
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from aristos_council.absolute_backtest import (CASH_TICKER, FORMS, HOLDS, INDEX_TICKER, MIN_HELD, SURVIVORSHIP,
                                               evaluate, lookup, runner, summarise)
from aristos_council.absolute_backtest.market import HistoricalBonds, Tape, annualise, cash_return
from aristos_council.absolute_backtest.scoring import RoundInput, bar_verdict, metrics_for, score_rounds
from aristos_council.absolute_backtest.selection import NameReading, cheapest_third, select_forms
from aristos_council.absolute_value.definition import load_definition
from aristos_council.absolute_value.engine import (BELOW_HURDLE, CLEARS_HURDLE, NOT_ENOUGH_DATA, build_absolute,
                                                   free_cash_flow_base)
from aristos_council.data.adapter import (DataUnavailable, Fundamentals, MarketDataAdapter, PriceBar, PriceHistory)
from aristos_council.data.asof_adapter import AsOfAdapter
from aristos_council.data.backtest_feed import MemoAdapter
from aristos_council.gate.definition import load_gate
from aristos_council.gate.engine import GateResult

DEF = load_definition()
GATE_OK = GateResult(state="qualifies", reason="Qualifies.")
M = 1e6


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
def synth_fundamentals(ticker: str, *, growth: float = 0.10, ccy: str = "USD") -> Fundamentals:
    """Annual accounts 2004 to 2019, newest first, filed on 28 February of the next year. Cash from operations grows by
    ``growth`` a year; capital spending is 20% of it; the change in working capital is +5m; ten million shares."""
    years = list(range(2019, 2003, -1))
    ends = [f"{y}-12-31" for y in years]
    filed = [f"{y + 1}-02-28" for y in years]
    ocf = [100 * M * (1 + growth) ** (y - 2004) for y in years]
    capex = [-0.2 * v for v in ocf]
    wc = [5 * M for _ in years]
    sh = [10 * M for _ in years]
    ni = [0.5 * v for v in ocf]
    rev = [3 * v for v in ocf]
    lists = {"operating_cash_flow_annual": ocf, "capital_expenditure_annual": capex,
             "change_in_working_capital_annual": wc, "shares_outstanding_annual": sh, "net_income": ni,
             "total_revenue": rev}
    return Fundamentals(
        ticker=ticker, name=ticker, currency=ccy, financial_currency=ccy, sector="Industrials",
        period_ends={k: list(ends) for k in lists}, filing_dates={k: list(filed) for k in lists},
        period_scalars={e: {"shares_outstanding": 10 * M, "eps": n / (10 * M)} for e, n in zip(ends, ni)},
        **lists)


def weekly_bars(base: float, drift: float, *, start=date(2007, 1, 1), end=date(2021, 2, 1)) -> list[PriceBar]:
    out, d, i = [], start, 0
    while d <= end:
        p = base * (1 + drift) ** (i / 52.0)
        out.append(PriceBar(day=d, open=p, high=p, low=p, close=p, adj_close=p, volume=1_000_000))
        d += timedelta(days=7)
        i += 1
    return out


class FakeFeed(MarketDataAdapter):
    name = "fake"
    dividend_streak_method = "per_payment_median"

    def __init__(self, funds: dict, bars: dict) -> None:
        self.funds, self.bars = funds, bars

    def get_fundamentals(self, ticker):
        runner.CountingFeed.fundamentals_calls += 1          # what a provider call would count
        if ticker not in self.funds:
            raise DataUnavailable(f"no accounts for {ticker}")
        return self.funds[ticker]

    def get_price_history(self, ticker, *, start, end):
        if ticker not in self.bars:
            raise DataUnavailable(f"no prices for {ticker}")
        return PriceHistory(ticker=ticker, bars=[b for b in self.bars[ticker] if start <= b.day <= end])

    def get_dividend_history(self, ticker, *, start, end):
        return []

    def provider_for(self, data_kind):
        return "fake"


def monthly_rows(value: float, first=date(2008, 1, 2), last=date(2021, 1, 4)):
    """A yield row every three days (the real series is daily on business days)."""
    rows, d = [], first
    while d <= last:
        rows.append({"date": d.isoformat(), "close": value})
        d += timedelta(days=3)
    return rows


def fake_bonds(value: float = 3.0, series=None) -> HistoricalBonds:
    return HistoricalBonds(series=series if series is not None else {"USD": {"symbol": "US10Y", "name": "US ten-year"}},
                           rows_fetcher=lambda sym, since: monthly_rows(value), today=date(2026, 11, 4))


def fake_benchmarks() -> runner.Benchmarks:
    return runner.Benchmarks(Tape(weekly_bars(50, 0.06)), Tape(weekly_bars(2.0, 0.0)))


TICKERS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
BASE_PRICES = [100, 150, 200, 300, 600, 900]        # cheap to dear against the same cash flows


def feed() -> FakeFeed:
    return FakeFeed({t: synth_fundamentals(t) for t in TICKERS},
                    {t: weekly_bars(p, 0.07 + 0.01 * i) for i, (t, p) in enumerate(zip(TICKERS, BASE_PRICES))})


@pytest.fixture(autouse=True)
def _gate_always_qualifies(monkeypatch):
    """The gate has its own tests (test_absolute_gate); here every name passes it so the VALUATION and the forms are what
    is measured on the synthetic accounts."""
    monkeypatch.setattr(evaluate, "evaluate_gate", lambda f, d: GATE_OK)


# --------------------------------------------------------------------------- #
# the three forms
# --------------------------------------------------------------------------- #
def reading(t, *, gate="qualifies", bt="qualifies_below_hurdle", y=0.05, eligible=True):
    return NameReading(t, eligible=eligible, gate_state=gate, backtest_state=bt, base_yield=y)


def test_form_one_is_every_eligible_name_that_passes_the_gate():
    rs = [reading("A"), reading("B", gate="excluded"), reading("C", eligible=False), reading("D", gate="not_enough_data"),
          reading("E")]
    assert select_forms(rs)[1] == ["A", "E"]


def test_form_two_is_the_cheapest_third_of_the_gate_passers_on_the_base_yield():
    rs = [reading(t, y=y) for t, y in zip("ABCDEFG", (0.01, 0.07, 0.03, 0.09, 0.02, 0.05, 0.04))]
    rs.append(reading("X", gate="excluded", y=0.50))          # a cheap name the gate removed is never picked
    assert select_forms(rs)[2] == ["B", "D"]                  # 7 gate-passers -> a third is 2; the two highest yields


def test_form_two_takes_at_least_one_name_and_skips_names_without_a_yield():
    assert cheapest_third([reading("A", y=0.02), reading("B", y=0.06)]) == ["B"]
    assert cheapest_third([reading("A", y=None), reading("B", y=None)]) == []


def test_form_three_is_the_gate_passers_that_clear_the_hurdle_on_the_backtest_form():
    rs = [reading("A", bt=CLEARS_HURDLE), reading("B", bt=BELOW_HURDLE), reading("C", bt=NOT_ENOUGH_DATA),
          reading("D", bt=CLEARS_HURDLE, gate="excluded"), reading("E", bt=CLEARS_HURDLE, eligible=False)]
    assert select_forms(rs)[3] == ["A"]


# --------------------------------------------------------------------------- #
# the own-record rule, in the backtest form
# --------------------------------------------------------------------------- #
def _fund_ev():
    return synth_fundamentals("AAA")


def _record(own5, own10):
    from aristos_council.abs_readings import Reading

    def leg(rate, span):
        return Reading(value=rate, span=span, label="x") if rate is not None else Reading(note="not enough history")
    return SimpleNamespace(eps=SimpleNamespace(cagr={5: leg(own5, 5), 10: leg(own10, 10)}))


def _bt(record, price=100.0):
    from aristos_council.data import bond_yield as by

    def bond(ccy, *, today):
        return by.BondResult(ccy, by.BondYield(ccy, "US10Y", "US", 0.03, "2013-01-02", 1))
    return build_absolute(_fund_ev(), gate=GATE_OK, last_close=price, growth_record=record, today=date(2013, 1, 31),
                          bond_source=bond).backtest_verdict


def test_no_record_of_own_growth_cannot_clear_in_the_backtest_form():
    v = _bt(_record(None, None))
    assert v.state == NOT_ENOUGH_DATA          # no own 5-year, no own 10-year, so the downside has no start either


def test_an_own_record_that_clears_in_every_case_clears_in_the_backtest_form():
    v = _bt(_record(0.10, 0.12), price=100.0)  # cash yield about 12%, hurdle 8%: every case returns above it
    assert v.state == CLEARS_HURDLE and v.of == 3


def test_one_case_below_the_hurdle_keeps_the_name_below():
    v = _bt(_record(0.10, 0.12), price=2000.0)
    assert v.state == BELOW_HURDLE


# --------------------------------------------------------------------------- #
# the hurdle on a past date
# --------------------------------------------------------------------------- #
def test_the_hurdle_is_the_yield_on_the_round_date_not_the_latest():
    rows = [{"date": "2012-01-02", "close": 2.0}, {"date": "2012-02-01", "close": 2.5},
            {"date": "2012-03-01", "close": 3.0}]
    b = HistoricalBonds(series={"USD": {"symbol": "US10Y", "name": "US"}}, rows_fetcher=lambda s, since: rows)
    r = b("USD", today=date(2012, 2, 6))
    assert r.available and r.yield_.value == pytest.approx(0.025) and r.yield_.as_of == "2012-02-01"
    assert b("USD", today=date(2012, 3, 4)).yield_.value == pytest.approx(0.03)


def test_a_yield_older_than_seven_days_has_lapsed_and_one_before_the_series_starts_is_absent():
    rows = [{"date": "2012-01-02", "close": 2.0}]
    b = HistoricalBonds(series={"USD": {"symbol": "US10Y", "name": "US"}}, rows_fetcher=lambda s, since: rows)
    assert not b("USD", today=date(2012, 1, 20)).available
    assert "no row on or before" in b("USD", today=date(2011, 12, 1)).reason


def test_a_currency_with_no_series_has_no_hurdle_and_is_not_borrowed_from_a_neighbour():
    b = fake_bonds()
    res = b("JPY", today=date(2012, 6, 1))
    assert not res.available and "no government bond series is mapped for JPY" in res.reason
    f = synth_fundamentals("JPN", ccy="JPY")
    asof = AsOfAdapter(MemoAdapter(FakeFeed({"JPN": f}, {"JPN": weekly_bars(100, 0.05)}), start=date(2007, 1, 1),
                                   end=date(2021, 1, 31)), date(2012, 6, 30), 90, "filing")
    r = evaluate.evaluate_name("JPN", date(2012, 6, 30), asof, f, Tape(weekly_bars(100, 0.05)), cap_today_usd=None,
                               size_floor=None, gate_def=load_gate(), value_def=DEF, bonds=b)
    assert r.no_hurdle and r.backtest_state == NOT_ENOUGH_DATA and r not in select_forms([r])[3]


def test_a_pound_in_pence_and_a_missing_rate_are_not_evaluated_not_failed():
    f = synth_fundamentals("MIX", ccy="USD")
    f = Fundamentals(**{**f.__dict__, "currency": "EUR"})        # price in euros, accounts in dollars, no rate to hand
    memo = MemoAdapter(FakeFeed({"MIX": f}, {"MIX": weekly_bars(100, 0.05)}), start=date(2007, 1, 1), end=date(2021, 1, 31))
    asof = AsOfAdapter(memo, date(2012, 6, 30), 90, "filing")
    r = evaluate.evaluate_name("MIX", date(2012, 6, 30), asof, f, Tape(weekly_bars(100, 0.05)), cap_today_usd=None,
                               size_floor=None, gate_def=load_gate(), value_def=DEF, bonds=fake_bonds())
    assert r.no_fx and r.backtest_state == NOT_ENOUGH_DATA and r.base_yield is None


# --------------------------------------------------------------------------- #
# the as-of base
# --------------------------------------------------------------------------- #
def test_the_base_is_read_from_the_accounts_filed_by_the_round_date():
    f = synth_fundamentals("AAA")
    memo = MemoAdapter(FakeFeed({"AAA": f}, {}), start=date(2007, 1, 1), end=date(2021, 1, 31))

    def latest(d):
        base = free_cash_flow_base(AsOfAdapter(memo, d, 90, "filing").get_fundamentals("AAA"), DEF)
        return base.latest, base.pre_wc

    # FY2010 is filed on 2011-02-28: before that date FY2009 is the newest year, on it FY2010 is
    before, pre = latest(date(2011, 2, 27))
    after, _ = latest(date(2011, 2, 28))
    fy = lambda y: (100 * M * 1.1 ** (y - 2004) * 0.8 - 5 * M) / (10 * M)       # (cash flow - capex - working capital) / shares
    assert pre is True
    assert before == pytest.approx(fy(2009)) and after == pytest.approx(fy(2010))


def test_filing_dates_are_used_where_they_exist_and_counted_where_they_do_not():
    f = synth_fundamentals("AAA")
    memo = MemoAdapter(FakeFeed({"AAA": f}, {}), start=date(2007, 1, 1), end=date(2021, 1, 31))
    asof = AsOfAdapter(memo, date(2012, 6, 30), 90, "filing")
    asof.get_fundamentals("AAA")
    assert asof.filing_decisions > 0 and asof.filing_date_fallbacks == 0
    assert runner.filing_share(f) == 1.0
    bare = Fundamentals(**{**f.__dict__, "filing_dates": {}})
    assert runner.filing_share(bare) == 0.0


def test_a_split_sized_jump_in_the_share_count_withholds_the_per_share_figures():
    f = synth_fundamentals("SPL")
    sh = list(f.shares_outstanding_annual)
    sh[1:] = [s * 4 for s in sh[1:]]                        # a 4-for-1 split between the newest and the second year
    g = Fundamentals(**{**f.__dict__, "shares_outstanding_annual": sh})
    assert evaluate.share_jumps(g) == [0]
    hist = evaluate.asof_growth_history(g)
    assert hist.eps[0] is not None and all(v is None for v in hist.eps[1:])


def _with_shares(f: Fundamentals, shares_by_year) -> Fundamentals:
    """``f`` with the provider's share count per fiscal year replaced (the list and the per-period scalar alike)."""
    ends = f.period_ends["shares_outstanding_annual"]
    sh = [shares_by_year(int(e[:4])) for e in ends]
    scalars = {e: {**s, "shares_outstanding": shares_by_year(int(e[:4]))} for e, s in f.period_scalars.items()}
    return Fundamentals(**{**f.__dict__, "shares_outstanding_annual": sh, "period_scalars": scalars})


def _read(f: Fundamentals, d: date) -> "NameReading":
    memo = MemoAdapter(FakeFeed({"X": f}, {"X": weekly_bars(100, 0.05)}), start=date(2007, 1, 1), end=date(2021, 1, 31))
    return evaluate.evaluate_name("X", d, AsOfAdapter(memo, d, 90, "filing"), f, Tape(weekly_bars(100, 0.05)),
                                  cap_today_usd=None, size_floor=None, gate_def=load_gate(), value_def=DEF, bonds=fake_bonds())


def test_a_split_that_happens_after_the_round_date_does_not_remove_the_name_from_the_forms():
    """ABSOLUTE-BACKTEST-REVIEW. The provider's share counts are as reported, the closes are split-adjusted to today. The
    old rule withheld the valuation of every name that split AFTER the round date, in every earlier round: it used the
    future to take names out, and names that split are mostly names that went up (6% of the priced names, +7.8 points a
    year more than the rest, measured on the 58 industries). They could never enter form 2 or form 3, while the industry
    basket and the random baskets held them. The shares of the earlier periods are restated to today's basis instead."""
    d = date(2012, 6, 30)
    split = _read(_with_shares(synth_fundamentals("X"), lambda y: 10 * M if y < 2015 else 40 * M), d)      # 4-for-1 in 2015
    control = _read(_with_shares(synth_fundamentals("X"), lambda y: 40 * M), d)                           # always 40m
    assert not split.split_withheld and split.base_yield is not None
    assert split.base_yield == pytest.approx(control.base_yield)
    assert split.backtest_state == control.backtest_state


def test_a_split_inside_the_visible_accounts_still_withholds_and_an_odd_ratio_is_not_taken_for_a_split():
    d = date(2018, 6, 30)                                   # the 2015 split is now inside the visible record
    inside = _read(_with_shares(synth_fundamentals("X"), lambda y: 10 * M if y < 2015 else 40 * M), d)
    assert inside.split_withheld and inside.base_yield is None
    d = date(2012, 6, 30)
    odd = _read(_with_shares(synth_fundamentals("X"), lambda y: 10 * M if y < 2015 else 24.5 * M), d)     # x2.45: near no split ratio
    assert odd.split_withheld and odd.base_yield is None


# --------------------------------------------------------------------------- #
# holds, cost, comparisons, luck
# --------------------------------------------------------------------------- #
def _inputs(n=3, held=("A", "B", "C", "D")):
    out = []
    for i in range(n):
        d = date(2015, 1 + i, 28)
        pool = {"A": 0.30, "B": 0.20, "C": 0.10, "D": 0.00, "E": 0.05, "F": -0.05}
        out.append(RoundInput(d, date(2016, 1 + i, 28), pool, tuple(held), index_return=0.10, cash_return=0.01))
    return out


def test_a_twelve_month_hold_pays_the_cost_once_and_is_compared_three_ways():
    (s, *_), cost = score_rounds(_inputs(1), 12, 0.005), 0.005
    basket = (0.30 + 0.20 + 0.10 + 0.0 + 0.05 - 0.05) / 6
    assert s.positioned and s.n_held == 4
    assert s.form_ann == pytest.approx(0.15 - cost) and s.pool_ann == pytest.approx(basket)
    assert s.ex_pool == pytest.approx(0.15 - cost - basket)
    assert s.ex_index == pytest.approx(0.15 - cost - 0.10) and s.ex_cash == pytest.approx(0.15 - cost - 0.01)


def test_a_thirty_six_month_hold_is_annualised_after_the_cost():
    inputs = [RoundInput(date(2015, 1, 31), date(2018, 1, 31), {"A": 0.5, "B": 0.5, "C": 0.5}, ("A", "B", "C"), 0.3, 0.03)]
    (s,) = score_rounds(inputs, 36, 0.005)
    assert s.form_ann == pytest.approx(annualise(0.5 - 0.005, 3)) and s.index_ann == pytest.approx(annualise(0.3, 3))


def test_fewer_than_three_priced_names_hold_no_position_and_drop_best_needs_four():
    few = score_rounds([RoundInput(date(2015, 1, 31), date(2016, 1, 31), {"A": 0.1, "B": 0.2, "C": 0.3}, ("A", "B"))], 12, 0.005)
    assert not few[0].positioned and MIN_HELD == 3
    three = score_rounds(_inputs(1, held=("A", "B", "C")), 12, 0.005)[0]
    four = score_rounds(_inputs(1), 12, 0.005)[0]
    assert three.drop_best is None and four.drop_best is not None
    assert four.drop_best < four.ex_pool                       # without the 30% name the basket does worse


def test_a_name_with_no_exit_price_is_left_out_of_the_basket_and_counted():
    inputs = [RoundInput(date(2015, 1, 31), date(2016, 1, 31), {"A": 0.1, "B": 0.2, "C": 0.3}, ("A", "B", "C", "GONE"))]
    (s,) = score_rounds(inputs, 12, 0.005)
    assert s.n_selected == 4 and s.n_held == 3


def test_cash_compounds_the_bill_yield_and_a_missing_series_is_absent():
    irx = Tape(weekly_bars(4.0, 0.0))
    assert cash_return(irx, date(2012, 1, 31), date(2013, 1, 31)) == pytest.approx(0.04, abs=0.001)
    assert cash_return(None, date(2012, 1, 31), date(2013, 1, 31)) is None


def test_the_pass_bar_needs_two_points_and_luck_under_five_percent_and_never_says_proven():
    def metrics(ex, years=10, pos=100):
        m = metrics_for([])
        m.ex_pool, m.years_measured, m.n_positions = ex, years, pos
        return m
    assert bar_verdict(metrics(0.025), 0.03) == "passes the bar"
    assert bar_verdict(metrics(0.025), 0.07) == "does not pass the bar"
    assert bar_verdict(metrics(0.015), 0.01) == "does not pass the bar"
    assert bar_verdict(metrics(0.05, years=4), 0.0) == "insufficient"
    assert bar_verdict(metrics(0.05, pos=20), 0.0) == "insufficient"
    for v in ("passes the bar", "does not pass the bar", "insufficient"):
        assert "proven" not in v


# --------------------------------------------------------------------------- #
# one industry end to end, then the checkpoint, the resume, the budget and the files
# --------------------------------------------------------------------------- #
END = date(2020, 12, 31)


def _run(tmp_path, *, budget=None, only=None, monkeypatch=None):
    monkeypatch.setattr(runner, "load_cohort_members", lambda slug, root=None: (list(TICKERS), 1, {}))
    return runner.run_all(out_root=tmp_path, end=END, budget=budget, max_rounds=14, random_baskets=20,
                          adapter=feed(), bonds=fake_bonds(), benchmarks=fake_benchmarks(), progress=lambda m: None,
                          industries=[("ind_a", "Ind A"), ("ind_b", "Ind B")])


def test_one_industry_writes_its_files_with_the_survivorship_statement_on_top(tmp_path, monkeypatch):
    runner.CountingFeed.fundamentals_calls = 0
    monkeypatch.setattr(runner, "load_cohort_members", lambda slug, root=None: (list(TICKERS), 1, {}))
    res = runner.run_industry("ind_a", end=END, out_root=tmp_path, adapter=feed(), bonds=fake_bonds(),
                              benchmarks=fake_benchmarks(), max_rounds=14, random_baskets=20, name="Ind A")
    folder = tmp_path / "ind_a"
    for name in ("form1_gate.csv", "form2_cheapest_third.csv", "form3_clears_hurdle.csv", "counts.csv", "result.json",
                 "DONE.json", "luck.npz"):
        assert (folder / name).exists(), name
        if name.endswith(".csv"):
            assert (folder / name).read_text(encoding="utf-8").startswith("# " + SURVIVORSHIP)
    rows = runner.read_csv(folder / "form1_gate.csv")
    assert len(rows) == 14 and rows[0]["date"] == "2010-12-31"
    assert {"ex_basket_h12", "ex_index_h12", "ex_cash_h12", "drop_best_h12", "ex_basket_h36"} <= set(rows[0])
    assert res["gate"] == "absolute_gate_v2" and res["valuation"] == "absolute_value_v3"
    assert res["names_with_filing_dates"] == 1.0 and res["decisions_on_filing_date"] == 1.0
    assert {(r["form"], r["hold_months"]) for r in res["rows"]} == {(f, h) for f in FORMS for h in HOLDS}
    counts = runner.read_csv(folder / "counts.csv")
    assert int(counts[0]["gate_qualifies"]) == 6 and int(counts[0]["form1"]) == 6
    assert 0 < int(counts[-1]["form3"]) <= int(counts[-1]["form1"])
    assert all(int(c["form2"]) == 2 for c in counts)           # a third of six, every round


def test_the_run_stops_cleanly_at_the_budget_and_resumes_where_it_stopped(tmp_path, monkeypatch):
    runner.CountingFeed.fundamentals_calls = 0
    first = _run(tmp_path, budget=100, monkeypatch=monkeypatch)       # one industry costs 60 units here; two would pass 100
    assert first["ran"] == ["ind_a"] and "budget" in first["stopped"] and first["units"] == 60
    assert runner.is_done(tmp_path, "ind_a") and not runner.is_done(tmp_path, "ind_b")
    stamp = (tmp_path / "ind_a" / "DONE.json").read_text(encoding="utf-8")
    assert "1 of 2 industries finished" in "\n".join(runner.status_lines(tmp_path, industries=[("ind_a", "A"), ("ind_b", "B")]))
    second = _run(tmp_path, monkeypatch=monkeypatch)
    assert second["skipped"] == ["ind_a"] and second["ran"] == ["ind_b"] and second["stopped"] == ""
    assert (tmp_path / "ind_a" / "DONE.json").read_text(encoding="utf-8") == stamp      # not redone
    assert "2 of 2 industries finished" in "\n".join(runner.status_lines(tmp_path, industries=[("ind_a", "A"), ("ind_b", "B")]))
    assert not (tmp_path / runner.PROGRESS_FILE).exists()


def test_one_failing_industry_does_not_stop_the_others_and_is_retried(tmp_path, monkeypatch):
    runner.CountingFeed.fundamentals_calls = 0
    monkeypatch.setattr(runner, "load_cohort_members", lambda slug, root=None: (list(TICKERS), 1, {}))
    real = runner.run_industry
    monkeypatch.setattr(runner, "run_industry", lambda slug, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
                        if slug == "ind_a" else real(slug, **kw))
    res = runner.run_all(out_root=tmp_path, end=END, max_rounds=14, random_baskets=20, adapter=feed(), bonds=fake_bonds(),
                         benchmarks=fake_benchmarks(), progress=lambda m: None,
                         industries=[("ind_a", "Ind A"), ("ind_b", "Ind B")])
    assert res["failed"] == ["ind_a"] and res["ran"] == ["ind_b"] and not runner.is_done(tmp_path, "ind_a")


def test_the_summary_file_has_one_row_per_form_and_hold_with_overall_rows_first(tmp_path, monkeypatch):
    runner.CountingFeed.fundamentals_calls = 0
    _run(tmp_path, monkeypatch=monkeypatch)
    built = summarise.build(tmp_path)
    text = (tmp_path / "SUMMARY.csv").read_text(encoding="utf-8")
    assert text.startswith("# " + SURVIVORSHIP)
    with (tmp_path / "SUMMARY.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(line for line in fh if not line.startswith("#")))
    assert list(rows[0]) == summarise.SUMMARY_COLUMNS
    assert [r["industry"] for r in rows[:6]] == ["ALL INDUSTRIES"] * 6
    assert len(rows) == 6 + 2 * 6                                   # six overall rows, then 3 forms x 2 holds per industry
    assert {r["verdict"] for r in rows if r["hold_months"] == "36"} == {"reported only"}
    assert all("proven" not in r["verdict"] for r in rows)
    assert (tmp_path / "COUNTS.csv").exists() and set(built["passes"]) == set(FORMS)
    assert "gate alone" in "\n".join(summarise.text_table(built["overall"]))


# --------------------------------------------------------------------------- #
# the page
# --------------------------------------------------------------------------- #
def _write_result(root, slug, *, ex=0.031, luck=0.04, verdict="passes the bar"):
    folder = root / slug
    folder.mkdir(parents=True)
    rows = []
    for form in FORMS:
        for hold in HOLDS:
            rows.append({"form": form, "form_name": f"form {form}", "hold_months": hold, "verdict": verdict,
                         "n_rounds": 100, "n_positions": 90, "years_measured": 9, "years_positive": 6, "ex_basket": ex,
                         "ex_index": 0.01, "ex_cash": 0.08, "luck": luck, "drop_best": 0.012,
                         "share_rounds_under_4": 0.2})
    (folder / "result.json").write_text(json.dumps({"industry": "Ind", "window": ["2016-10-31", "2026-10-31"],
                                                    "gate": "absolute_gate_v2", "valuation": "absolute_value_v3",
                                                    "decisions_on_filing_date": 0.4, "rows": rows}), encoding="utf-8")
    (folder / "DONE.json").write_text("{}", encoding="utf-8")


def test_the_sentence_names_the_industry_result_and_the_old_one_stays_when_there_is_none(tmp_path, monkeypatch):
    from tests.test_absolute_page import fake_report, gate_ok, absolute
    from aristos_council import company_page as cp
    monkeypatch.setattr(lookup, "DEFAULT_ROOT", tmp_path)
    report = fake_report(gate_ok(), absolute(gate_ok()))
    report.cohort_slug = "ind"
    old = cp._cannot(report)
    assert "No backtest of this method exists yet" in old
    _write_result(tmp_path, "ind")
    new = cp._cannot(report)
    assert "No backtest of this method exists yet" not in new
    assert ("In this industry over ten years, names that passed the gate and cleared the hurdle returned +3.1 points a "
            "year against the industry basket; random baskets matched that 4% of the time.") in new
    assert "proven" not in new.lower() and "not proof" in new
    report.cohort_slug = "another_industry"
    assert "No backtest of this method exists yet" in cp._cannot(report)


def test_a_thin_result_says_so_and_an_empty_form_three_says_too_few_names(tmp_path):
    _write_result(tmp_path, "thin", verdict="insufficient")
    assert "Too few rounds to judge." in lookup.cannot_sentence("thin", tmp_path)
    folder = tmp_path / "empty"
    folder.mkdir()
    (folder / "result.json").write_text(json.dumps({"industry": "E", "rows": [
        {"form": 3, "hold_months": 12, "ex_basket": None, "n_positions": 0, "n_rounds": 100}]}), encoding="utf-8")
    (folder / "DONE.json").write_text("{}", encoding="utf-8")
    assert "too few names both passed the gate and cleared the hurdle" in lookup.cannot_sentence("empty", tmp_path)
    assert lookup.cannot_sentence("never_run", tmp_path) is None and lookup.cannot_sentence(None, tmp_path) is None


def test_the_workings_carry_all_three_forms_for_both_holds_with_the_statement_first(tmp_path):
    _write_result(tmp_path, "ind")
    lines = lookup.workings_lines("ind", tmp_path)
    assert lines[1] == SURVIVORSHIP
    assert "gate version 2, valuation version 3" in lines[0] and "absolute_gate" not in " ".join(lines)
    forms = [ln for ln in lines if ln.startswith("Form ")]
    assert len(forms) == 6 and sum("12-month hold" in ln for ln in forms) == 3
    assert all("against the industry basket" in ln and "world index fund" in ln and "against cash" in ln for ln in forms)
    assert "reported without pass or fail" in lines[-1]
    assert lookup.workings_lines("never_run", tmp_path) == []


def test_the_benchmarks_are_a_world_index_fund_and_the_us_three_month_bill():
    assert INDEX_TICKER == "VT" and CASH_TICKER == "^IRX"
