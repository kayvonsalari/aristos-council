"""BACKTEST-1, commit 2 - the lens backtest loop, its verdict, its CSV and its CLI.

No network, no LLM. The rank pipeline is replaced by a stub that hands back a chosen BUY set for each
round (the ranking itself is tested elsewhere), so what is pinned here is the LOOP: which adapter each
round is given, how returns/costs/benchmark/excess are computed, how rounds are excluded, and how the
result is summarised, judged and written.
"""
from __future__ import annotations

import inspect
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import aristos_council.backtest as bt
from aristos_council.backtest import (BacktestResult, Round, add_months, from_csv, round_dates,
                                      run_lens_backtest, summarize_directory, summary_line, to_csv,
                                      verdict)
from aristos_council.data.adapter import (DataUnavailable, DividendEvent, Fundamentals,
                                          MarketDataAdapter, PriceBar, PriceHistory)
from aristos_council.data.asof_adapter import AsOfAdapter
from aristos_council.data.backtest_feed import BacktestFeed, MemoAdapter
from aristos_council.rank_engine import RankedTicker


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _daily(start: date, end: date, price_of):
    d, out = start, []
    while d <= end:
        if d.weekday() < 5:
            p = price_of(d)
            out.append(PriceBar(day=d, open=p, high=p, low=p, close=p, adj_close=p, volume=1))
        d += timedelta(days=1)
    return out


def _growth(annual: float, base=100.0):
    """Price = base * (1 + annual) ** years-since-2018-01-01 - a smooth, checkable series."""
    return lambda d: base * (1.0 + annual) ** ((d - date(2018, 1, 1)).days / 365.0)


class _Feed(MarketDataAdapter):
    name = "fake-feed"

    def __init__(self, growth: dict, dead=()):
        self._g, self._dead = growth, set(dead)
        self.price_calls = 0

    def get_price_history(self, ticker, *, start, end):
        self.price_calls += 1
        if ticker in self._dead:
            raise DataUnavailable(f"no prices for {ticker}")
        bars = _daily(date(2018, 1, 1), date(2023, 12, 31), _growth(self._g[ticker]))
        return PriceHistory(ticker=ticker, bars=[b for b in bars if start <= b.day <= end])

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker)

    def get_dividend_history(self, ticker, *, start, end):
        return []


GROWTH = {"A": 0.30, "B": 0.20, "C": 0.10, "D": 0.00, "E": -0.10}


def _stub_pipeline(monkeypatch, buys_for, calls=None):
    """Replace run_rank_pipeline: every round ranks all members, BUY = ``buys_for(as_of)``."""
    def fake(universe, strategy_id, **kw):
        if calls is not None:
            calls.append((universe, strategy_id, kw))
        as_of = kw["adapter"].as_of
        buys = set(buys_for(as_of))
        rows = [RankedTicker(ticker=t, factor_ranks={}, factor_values={}, combined_rank=i,
                             universe_size=len(universe),
                             verdict="buy" if t in buys else "hold")
                for i, t in enumerate(universe)]
        return SimpleNamespace(ranked=rows)
    monkeypatch.setattr("aristos_council.pipeline.run_rank_pipeline", fake)


def _px(ticker, d):
    return _growth(GROWTH[ticker])(d)


def _ret(ticker, d0, d1):
    # the loop reads the last close on or before each date, exactly like the series it is given
    def last(d):
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return _px(ticker, d)
    return last(d1) / last(d0) - 1.0


def _run(monkeypatch, *, buys=("A", "B", "C"), start=date(2019, 1, 31), end=date(2020, 6, 30),
         calls=None, feed=None, **kw):
    _stub_pipeline(monkeypatch, lambda d: buys, calls)
    return run_lens_backtest("Test Cohort", "some_lens_v1", start=start, end=end,
                             adapter=feed or _Feed(GROWTH), members=sorted(GROWTH), **kw)


# --------------------------------------------------------------------------- #
# dates
# --------------------------------------------------------------------------- #
def test_add_months_lands_on_month_ends_and_round_dates_stop_before_the_last_hold():
    assert add_months(date(2020, 1, 31), 1) == date(2020, 2, 29)
    assert add_months(date(2020, 11, 30), 3) == date(2021, 2, 28)
    ds = round_dates(date(2019, 1, 15), date(2020, 6, 30), 12, 1)
    assert ds[0] == date(2019, 1, 31) and ds[-1] == date(2019, 6, 30)     # 6/2019 + 12m = 6/2020
    assert all(add_months(d, 12) <= date(2020, 6, 30) for d in ds) and len(ds) == 6
    assert round_dates(date(2019, 1, 31), date(2019, 6, 30), 12, 1) == []
    assert round_dates(date(2019, 1, 31), date(2020, 12, 31), 6, 3)[:3] == [
        date(2019, 1, 31), date(2019, 4, 30), date(2019, 7, 31)]


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #
def test_each_round_ranks_ranker_only_behind_an_as_of_adapter_dated_that_round(monkeypatch):
    calls = []
    result = _run(monkeypatch, calls=calls, lag_days=45)
    assert len(calls) == len(result.rounds) == 6
    for (universe, lens, kw), r in zip(calls, result.rounds):
        assert lens == "some_lens_v1" and universe == sorted(GROWTH)
        assert kw["ranker_only"] is True and kw["use_cache"] is True
        assert isinstance(kw["adapter"], AsOfAdapter)
        assert kw["adapter"].as_of == r.date == kw["today"] and kw["adapter"].lag_days == 45


def test_returns_costs_benchmark_and_excess(monkeypatch):
    result = _run(monkeypatch, cost_bps=50)
    r = result.rounds[0]                                    # 2019-01-31 -> 2020-01-31
    exit_date = date(2020, 1, 31)
    buys = [_ret(t, r.date, exit_date) for t in ("A", "B", "C")]
    every = [_ret(t, r.date, exit_date) for t in GROWTH]
    assert r.exit_date == exit_date and r.n_buys == 3 and r.n_ranked == 5
    assert r.tickers == ("A", "B", "C")
    assert r.buy_return == pytest.approx(sum(buys) / 3 - 0.005)        # cost ONCE, not per name
    assert r.bench_return == pytest.approx(sum(every) / 5)             # every ranked member
    assert r.excess == pytest.approx(r.buy_return - r.bench_return)
    assert r.excess > 0                                                # A, B, C outgrow D, E


def test_cost_is_a_parameter_and_zero_cost_is_the_gross_return(monkeypatch):
    gross = _run(monkeypatch, cost_bps=0).rounds[0]
    net = _run(monkeypatch, cost_bps=100).rounds[0]
    assert gross.buy_return - net.buy_return == pytest.approx(0.01)
    assert gross.bench_return == net.bench_return                      # the benchmark pays nothing


def test_fewer_than_three_buys_is_no_position_counted_and_excluded_from_averages(monkeypatch):
    result = _run(monkeypatch, buys=("A", "B"))
    assert all(not r.has_position and r.excess is None and r.buy_return is None for r in result.rounds)
    assert all(r.n_buys == 2 and r.bench_return is not None for r in result.rounds)
    s = result.summary
    assert (s.n_rounds, s.n_positions, s.n_no_position) == (6, 0, 6)
    assert s.mean_annual_excess is None and s.hit_rate is None and s.years_measured == 0
    assert any("6 of 6 rounds had fewer than 3" in c for c in result.caveats)


def test_a_mixed_run_averages_only_the_rounds_that_held_a_position(monkeypatch):
    _stub_pipeline(monkeypatch, lambda d: ("A", "B", "C") if d.month % 2 else ("A",))
    result = run_lens_backtest("Test Cohort", "some_lens_v1", start=date(2019, 1, 31),
                               end=date(2020, 6, 30), adapter=_Feed(GROWTH), members=sorted(GROWTH))
    held = [r for r in result.rounds if r.has_position]
    assert 0 < len(held) < len(result.rounds)
    assert result.summary.n_positions == len(held)
    assert result.year_excess == {2019: pytest.approx(sum(r.excess for r in held) / len(held))}


def test_a_name_with_no_price_is_left_out_of_both_baskets_and_counted(monkeypatch):
    result = _run(monkeypatch, feed=_Feed(GROWTH, dead=("C",)), buys=("A", "B", "C", "D"))
    r = result.rounds[0]
    assert "C" not in r.tickers and r.tickers == ("A", "B", "D") and r.n_buys == 4
    assert r.bench_return == pytest.approx(
        sum(_ret(t, r.date, r.exit_date) for t in ("A", "B", "D", "E")) / 4)
    assert any("name-round(s) had no usable price" in c for c in result.caveats)


def test_a_price_series_that_stops_before_the_exit_is_unpriced_not_flat(monkeypatch):
    class _Delisted(_Feed):
        def get_price_history(self, ticker, *, start, end):
            history = super().get_price_history(ticker, start=start, end=end)
            if ticker != "E":
                return history
            return PriceHistory(ticker, [b for b in history.bars if b.day <= date(2019, 6, 30)])

    result = _run(monkeypatch, feed=_Delisted(GROWTH))
    early = result.rounds[0]                                 # exit 2020-01-31, E's last bar 2019-06-30
    assert early.bench_return == pytest.approx(
        sum(_ret(t, early.date, early.exit_date) for t in "ABCD") / 4)


def test_prices_are_fetched_once_per_company_not_once_per_round(monkeypatch):
    feed = _Feed(GROWTH)
    _run(monkeypatch, feed=feed)
    assert feed.price_calls == len(GROWTH)                   # 6 rounds x 5 names -> 5 requests


def test_window_shorter_than_one_hold_returns_an_empty_result_with_a_reason(monkeypatch):
    result = _run(monkeypatch, start=date(2019, 1, 31), end=date(2019, 6, 30))
    assert result.rounds == [] and verdict(result) == "insufficient"
    assert any("shorter than one 12-month hold" in c for c in result.caveats)


def test_progress_is_called_once_per_round(monkeypatch):
    seen = []
    _run(monkeypatch, progress=seen.append)
    assert len(seen) == 6 and "excess" in seen[0] and "(1/6)" in seen[0]


def test_the_result_states_both_honesty_limits(monkeypatch):
    text = " ".join(_run(monkeypatch).caveats)
    assert "RESTATED ACCOUNTS" in text and "90-day filing lag" in text
    assert "SURVIVORSHIP" in text and "ADJUSTED closes" in text and "overlap" in text


# --------------------------------------------------------------------------- #
# the years, the summary, the verdict
# --------------------------------------------------------------------------- #
def _rounds(excess_by_year: dict, per_year=12, buy=0.10):
    out = []
    for year, ex in excess_by_year.items():
        for m in range(1, per_year + 1):
            d = date(year, m, 28)
            out.append(Round(d, 4, buy, buy - ex, ex, ("A", "B", "C"), add_months(d, 12), 20)
                       if ex is not None else Round(d, 1, None, 0.05, None, (), add_months(d, 12), 20))
    return out


def _result(rounds):
    return BacktestResult("Test Cohort", "some_lens_v1", date(2010, 1, 31), date(2021, 12, 31),
                          rounds=rounds, caveats=["c1"])


def test_overlapping_holds_starting_in_a_year_are_averaged_within_that_year():
    rounds = [Round(date(2019, 1, 31), 4, 0.1, 0.0, 0.10, ("A",), None, 9),
              Round(date(2019, 2, 28), 4, 0.1, 0.0, 0.20, ("A",), None, 9),
              Round(date(2019, 3, 31), 4, 0.1, 0.0, -0.06, ("A",), None, 9),
              Round(date(2020, 1, 31), 4, 0.1, 0.0, 0.03, ("A",), None, 9)]
    assert _result(rounds).year_excess == {2019: pytest.approx(0.08), 2020: pytest.approx(0.03)}


def test_summary_numbers():
    rounds = _rounds({2015: 0.05, 2016: -0.02, 2017: None, 2018: 0.03})
    s = _result(rounds).summary
    assert (s.n_rounds, s.n_positions, s.n_no_position) == (48, 36, 12)
    assert (s.years_measured, s.years_positive) == (3, 2)
    assert s.mean_annual_excess == pytest.approx((0.05 - 0.02 + 0.03) / 3)
    assert s.hit_rate == pytest.approx(24 / 36)
    assert s.worst_round_excess == pytest.approx(-0.02) and s.worst_round_date == date(2016, 1, 28)
    assert s.caveats == ("c1",)


def test_max_drawdown_compounds_non_overlapping_chains_and_treats_no_position_as_cash():
    def rd(m, buy):
        d = date(2019, m, 28)
        return Round(d, 4, buy, 0.0, buy, ("A",), None, 9) if buy is not None else \
            Round(d, 1, None, 0.0, None, (), None, 9)
    # hold 3, step 3 -> one chain; +10%, then -20%, then -10%, then cash
    r = BacktestResult("c", "l", date(2019, 1, 31), date(2020, 1, 31), hold_months=3, step_months=3,
                       rounds=[rd(1, 0.10), rd(4, -0.20), rd(7, -0.10), rd(10, None)])
    assert r.summary.max_drawdown == pytest.approx(0.792 / 1.10 - 1.0)
    # monthly rounds, 3-month hold -> 3 chains; the WORST one is reported
    monthly = BacktestResult("c", "l", date(2019, 1, 31), date(2020, 1, 31), hold_months=3,
                             step_months=1, rounds=[rd(m, b) for m, b in
                                                    zip(range(1, 7), [0.1, 0.0, 0.0, -0.3, 0.0, 0.0])])
    assert monthly.summary.max_drawdown == pytest.approx(-0.3)


def test_verdict_insufficient_below_six_years_or_sixty_rounds():
    assert verdict(_result(_rounds({y: 0.10 for y in range(2010, 2015)}))) == "insufficient"   # 5 yrs
    six_years_short = _result(_rounds({y: 0.10 for y in range(2010, 2016)}, per_year=9))         # 54 rnds
    assert six_years_short.summary.years_measured == 6 and verdict(six_years_short) == "insufficient"
    assert verdict(_result([])) == "insufficient"


def test_verdict_proven_needs_two_points_of_excess_and_six_of_ten_years():
    good = {y: 0.05 for y in range(2010, 2020)}
    assert verdict(_result(_rounds(good))) == "proven"
    only_five = {**good, **{y: -0.001 for y in range(2015, 2020)}}          # 5 of 10 positive
    assert verdict(_result(_rounds(only_five))) == "not proven"
    six = {**good, **{y: -0.001 for y in range(2016, 2020)}}                # 6 of 10, mean 2.9%
    assert verdict(_result(_rounds(six))) == "proven"
    thin = {y: 0.019 for y in range(2010, 2020)}                              # all positive, mean 1.9%
    assert verdict(_result(_rounds(thin))) == "not proven"
    exactly = {y: 0.02 for y in range(2010, 2020)}
    assert verdict(_result(_rounds(exactly))) == "proven"                     # the bar is inclusive


def test_verdict_scales_the_year_test_when_fewer_than_ten_years_were_measured():
    eight = {y: 0.05 for y in range(2010, 2018)}
    assert verdict(_result(_rounds(eight))) == "proven"
    weak = {**eight, **{y: -0.001 for y in range(2013, 2018)}}              # 3 of 8 positive
    assert verdict(_result(_rounds(weak))) == "not proven"


def test_verdict_arguments_are_the_contract():
    params = inspect.signature(verdict).parameters
    assert list(params) == ["result", "min_excess", "min_years", "of_years"]
    assert (params["min_excess"].default, params["min_years"].default,
            params["of_years"].default) == (0.02, 6, 10)
    assert "2026-09-26" in inspect.getsource(bt)                              # the ruling is dated


def test_run_lens_backtest_signature_is_the_contract():
    params = inspect.signature(run_lens_backtest).parameters
    assert list(params)[:2] == ["cohort", "lens_id"]
    kw = {n: (p.default if p.default is not p.empty else "REQUIRED") for n, p in params.items()
          if p.kind is p.KEYWORD_ONLY}
    assert kw["start"] == kw["end"] == "REQUIRED"
    for name, default in [("hold_months", 12), ("step_months", 1), ("cost_bps", 50), ("lag_days", 90),
                          ("adapter", None), ("progress", None)]:
        assert kw[name] == default


# --------------------------------------------------------------------------- #
# the file
# --------------------------------------------------------------------------- #
def _real(monkeypatch):
    result = _run(monkeypatch)
    result.lens_commit, result.cohort_version, result.n_members = "abc1234", 1, 5
    return result


def test_csv_round_trips_and_the_summary_is_recomputed_from_the_rounds(monkeypatch, tmp_path):
    result = _real(monkeypatch)
    path = to_csv(result, tmp_path)
    assert path == tmp_path / "test_cohort" / "some_lens_v1.csv"
    back = from_csv(path)
    assert back.rounds == result.rounds                       # exact floats, dates, tickers
    assert back.summary == result.summary and verdict(back) == verdict(result)
    assert (back.cohort, back.lens_id, back.hold_months, back.cost_bps, back.lag_days,
            back.lens_commit, back.cohort_version, back.n_members) == (
        "Test Cohort", "some_lens_v1", 12, 50.0, 90, "abc1234", 1, 5)
    assert back.caveats == result.caveats


def test_csv_header_states_the_rules_and_rows_carry_no_timestamps(monkeypatch, tmp_path):
    text = to_csv(_real(monkeypatch), tmp_path).read_text(encoding="utf-8")
    head = [l for l in text.splitlines() if l.startswith("#")]
    joined = "\n".join(head)
    for needle in ("# as_of_rule:", "# lag_days: 90", "# costs:", "# benchmark:", "# caveat:",
                   "# verdict: insufficient", "# lens_commit: abc1234", "# cohort: Test Cohort"):
        assert needle in joined, needle
    rows = [l for l in text.splitlines() if not l.startswith("#")]
    assert rows[0] == "date,exit_date,n_ranked,n_buys,buy_return,bench_return,excess,tickers"
    assert len(rows) == 1 + 6 and rows[1].startswith("2019-01-31,2020-01-31,5,3,")
    assert not any("T" in r.split(",")[0] or ":" in r for r in rows[1:])            # dates only, no clock


def test_csv_is_byte_deterministic(monkeypatch, tmp_path):
    a = to_csv(_real(monkeypatch), tmp_path / "one").read_bytes()
    b = to_csv(_real(monkeypatch), tmp_path / "two").read_bytes()
    assert a == b


def test_a_csv_path_is_used_as_the_file_itself(monkeypatch, tmp_path):
    path = to_csv(_real(monkeypatch), tmp_path / "custom.csv")
    assert path == tmp_path / "custom.csv" and path.exists()


def test_no_position_rows_round_trip_as_blanks(monkeypatch, tmp_path):
    _stub_pipeline(monkeypatch, lambda d: ("A",))
    result = run_lens_backtest("Test Cohort", "some_lens_v1", start=date(2019, 1, 31),
                               end=date(2020, 6, 30), adapter=_Feed(GROWTH), members=sorted(GROWTH))
    back = from_csv(to_csv(result, tmp_path))
    assert back.rounds == result.rounds and back.rounds[0].excess is None
    assert back.rounds[0].tickers == ()


def test_summary_line_and_directory_summary(monkeypatch, tmp_path):
    result = _real(monkeypatch)
    to_csv(result, tmp_path)
    line = summary_line(result)
    assert line.startswith("test_cohort x some_lens_v1: insufficient - mean annual excess +")
    assert "years positive" in line and "6 of 6 rounds held a position" in line
    assert summarize_directory(tmp_path) == [line]
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "broken.csv").write_text("not a backtest", encoding="utf-8")
    assert any("bad x broken: UNREADABLE" in l for l in summarize_directory(tmp_path))


def test_summary_line_of_an_empty_result_says_n_a():
    line = summary_line(_result([]))
    assert "insufficient" in line and "n/a" in line and "0 of 0 rounds" in line


# --------------------------------------------------------------------------- #
# the cohort, the lens, the feed
# --------------------------------------------------------------------------- #
def _write_cohort(root: Path, slug: str, versions=(1, 2)):
    from aristos_council.cohorts.freeze import MEMBER_COLUMNS
    for v in versions:
        d = root / slug / f"v{v}"
        d.mkdir(parents=True)
        rows = ["AAA.US", "BBB.US"] if v == 1 else ["AAA.US", "BBB.US", "CCC.LSE"]
        lines = [",".join(MEMBER_COLUMNS)]
        for t in rows:
            cells = {c: "" for c in MEMBER_COLUMNS}
            cells.update(ticker=t, exchange=t.split(".")[1], name=t, source="test")
            lines.append(",".join(cells[c] for c in MEMBER_COLUMNS))
        (d / "members.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_members_come_from_the_current_frozen_version_as_yahoo_symbols(tmp_path):
    _write_cohort(tmp_path, "tech_semiconductors")
    tickers, version = bt.load_cohort_members("Tech: Semiconductors", tmp_path)
    assert version == 2 and tickers == ["AAA", "BBB", "CCC.L"]
    assert bt.load_cohort_members("tech_semiconductors", tmp_path)[1] == 2     # the slug works too


def test_an_unbuilt_cohort_is_named_not_skipped(tmp_path):
    with pytest.raises(FileNotFoundError, match="no frozen cohort 'Nowhere'"):
        bt.load_cohort_members("Nowhere", tmp_path)


def test_the_cohort_version_is_stamped_and_the_survivorship_caveat_names_it(monkeypatch, tmp_path):
    _write_cohort(tmp_path, "test_cohort", versions=(1,))
    _stub_pipeline(monkeypatch, lambda d: ("A", "B", "C"))
    result = run_lens_backtest("Test Cohort", "some_lens_v1", start=date(2019, 1, 31),
                               end=date(2020, 6, 30), adapter=_Feed({"AAA": .1, "BBB": .1}),
                               cohorts_root=tmp_path)
    assert result.cohort_version == 1 and result.n_members == 2
    assert "(v1)" in " ".join(result.caveats)


def test_lens_commit_names_the_lens_file_in_a_git_checkout_and_is_unknown_elsewhere(tmp_path):
    real = bt.lens_commit("magic_formula_momentum_v1")
    assert real == "unknown" or real.split("+")[0].isalnum()
    assert bt.lens_commit("no_such_lens", tmp_path) == "unknown"


def test_memo_fetches_once_slices_and_remembers_failures():
    feed = _Feed(GROWTH, dead=("E",))
    memo = MemoAdapter(feed, start=date(2018, 6, 1), end=date(2019, 12, 31))
    a = memo.get_price_history("A", start=date(2019, 1, 1), end=date(2019, 1, 31))
    b = memo.get_price_history("A", start=date(2019, 2, 1), end=date(2019, 2, 28))
    assert feed.price_calls == 1 and a.bars[-1].day <= date(2019, 1, 31) < b.bars[0].day
    for _ in range(3):
        with pytest.raises(DataUnavailable):
            memo.get_price_history("E", start=date(2019, 1, 1), end=date(2019, 1, 31))
    assert feed.price_calls == 2                              # one attempt at E, remembered
    memo.get_price_history("A", start=date(2017, 1, 1), end=date(2017, 2, 1))   # outside: widened
    assert feed.price_calls == 3


def test_the_feed_keeps_its_dated_accounts_out_of_the_ordinary_cache_key():
    feed = BacktestFeed(fundamentals=_Feed(GROWTH), market=_Feed(GROWTH))
    assert feed.provider_for("fundamentals") == "eodhd-dated"
    assert feed.provider_for("prices") == "fake-feed"


def test_no_llm_is_reachable_from_the_backtest_package():
    from aristos_council.data import asof_adapter, backtest_feed
    for module in (bt, asof_adapter, backtest_feed):
        source = inspect.getsource(module).lower()
        for banned in ("anthropic", "langchain", "init_chat_model", "runners"):
            assert banned not in source, (module.__name__, banned)


# --------------------------------------------------------------------------- #
# the CLI
# --------------------------------------------------------------------------- #
def test_cli_run_writes_the_csv_and_prints_the_summary(monkeypatch, tmp_path, capsys):
    seen = {}

    def fake_run(cohort, lens, **kw):
        seen.update(cohort=cohort, lens=lens, **kw)
        return _result(_rounds({2015: 0.05}))
    monkeypatch.setattr(bt, "run_lens_backtest", fake_run)
    code = bt.main(["run", "--cohort", "Test Cohort", "--lens", "some_lens_v1", "--years", "2",
                    "--end", "2021-12-31", "--hold", "6", "--cost-bps", "25", "--lag-days", "60",
                    "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0 and (tmp_path / "test_cohort" / "some_lens_v1.csv").exists()
    assert seen["start"] == date(2019, 12, 1) and seen["end"] == date(2021, 12, 31)
    assert (seen["hold_months"], seen["cost_bps"], seen["lag_days"]) == (6, 25.0, 60)
    assert "wrote " in out and "test_cohort x some_lens_v1: insufficient" in out


def test_cli_summary_prints_one_line_per_file(monkeypatch, tmp_path, capsys):
    to_csv(_result(_rounds({2015: 0.05})), tmp_path)
    assert bt.main(["summary", str(tmp_path)]) == 0
    assert capsys.readouterr().out.count("\n") == 1
    empty = tmp_path / "nothing"
    empty.mkdir()
    bt.main(["summary", str(empty)])
    assert "no backtests under" in capsys.readouterr().out


def test_the_price_only_engine_is_still_there_under_its_new_result_name():
    from aristos_council.backtest import PriceBacktestResult, run_backtest
    assert PriceBacktestResult.__name__ == "PriceBacktestResult" and callable(run_backtest)
    assert BacktestResult.__module__ == "aristos_council.backtest" and "rounds" in {
        f for f in BacktestResult.__dataclass_fields__}
