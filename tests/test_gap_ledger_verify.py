"""GAP-IBKR-1 items 2-5 — verifying a yfinance gap against IB, and what overrides what.

yfinance says a name gapped; IB says whether anyone traded it. The tests here are about the
three decisions that follow from that:

* **who gets asked** — every yfinance gapper, INCLUDING the ones Batch 3's trust tests
  abstained on, because those are precisely the cases IB can settle;
* **what IB's answer means** — check 1 rejecting a name is a REJECTION (IB looked and there
  was no move there), while IB being unable to answer leaves the yfinance row standing;
* **what overrides what** — a verified name takes IB's price, volume and spread, and the
  trust tests do not apply to it.

Plus item 3's unavailable path (never fatal, said once, on every surface) and item 5's licence
boundary (nothing in Aristos may import this).

No test reaches a gateway: the fake records what it was asked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time, timedelta

import pytest

from aristos_council.gap_ledger.bars import IntradayBar, Quote
from aristos_council.gap_ledger.config import DEFAULT_CONFIG, GapConfig, at_ny
from aristos_council.gap_ledger.ledger import GROUP_CANDIDATE, read_day
from aristos_council.gap_ledger.run import format_report, run_screen
from aristos_council.gap_ledger.screen import (PRICE_NOT_CONFIRMED, ScreenRow,
                                               premarket_window, screen_one)
from aristos_council.gap_ledger.verify import (IBKR_UNAVAILABLE, NO_REAL_MOVE, SOURCE_IBKR,
                                               SOURCE_YFINANCE, IBKRReading, apply_reading,
                                               check_one, check_two, needs_verifying)

from .gap_ledger_fakes import (FakeBars, daily_series, intraday_window, prior_sessions,
                               sparse_window)

DAY = date(2026, 9, 22)
RUN_AT = at_ny(DAY, time(9, 0))
START, END = premarket_window(DAY, RUN_AT)


def _bar(moment: time, price: float, volume: int = 1_000) -> IntradayBar:
    return IntradayBar(start=at_ny(DAY, moment), open=price, high=price, low=price,
                       close=price, volume=volume)


def _traded(price: float, *, volume_per_bar: int = 10_000, day: date = DAY,
            end: time = time(9, 0)):
    """A tape with REAL volume — what IB serves and yfinance does not."""
    return intraday_window(day, end=end, price=price, volume_per_bar=volume_per_bar)


def _ib_history(*, today_volume: int = 10_000, baseline_volume: int = 1_000,
                price: float = 110.0):
    """Today plus twenty prior sessions, all with volume. IB's single baseline request."""
    return (_traded(price, volume_per_bar=today_volume)
            + prior_sessions(before=DAY, count=20,
                             premarket_volume_per_bar=baseline_volume))


# --------------------------------------------------------------------------- #
# a fake gateway
# --------------------------------------------------------------------------- #
@dataclass
class FakeIBKR:
    """Answers from dicts and records every request — the shape is what matters."""

    today: dict = field(default_factory=dict)
    history: dict = field(default_factory=dict)
    quote_map: dict = field(default_factory=dict)
    quote_note: str = ""
    unavailable_after: int = -1                # raise IBKRUnavailable once this many calls
    bar_calls: list = field(default_factory=list)
    quote_calls: list = field(default_factory=list)

    def bars_for(self, ticker, *, start, end, bar_size="5 mins", duration=None):
        from aristos_council.gap_ledger.ibkr import IBKRUnavailable

        self.bar_calls.append((ticker, bar_size, duration))
        if 0 <= self.unavailable_after <= len(self.bar_calls) - 1:
            raise IBKRUnavailable("gateway went away")
        source = self.history if duration else self.today
        return list(source.get(ticker, []))

    def quote_for(self, ticker):
        self.quote_calls.append(ticker)
        return self.quote_map.get(ticker, Quote())

    def disconnect(self):
        pass


# --------------------------------------------------------------------------- #
# who gets asked
# --------------------------------------------------------------------------- #
def test_every_yfinance_gapper_is_sent_to_ib():
    rows = [ScreenRow("BIG", True, gap=0.10), ScreenRow("SMALL", False, gap=0.01),
            ScreenRow("NONE", None, gap=None)]
    assert needs_verifying(rows) == ["BIG"]


def test_a_name_the_trust_tests_abstained_on_is_still_sent():
    """The inclusion is the point: Batch 3 cannot tell a stray print from a real move, and IB
    can. Sending only the names that already passed would leave the uncertain ones unresolved
    forever."""
    untrusted = ScreenRow("XEL", None, PRICE_NOT_CONFIRMED, gap=0.11)
    assert needs_verifying([untrusted]) == ["XEL"]


def test_the_gap_threshold_comes_from_the_config():
    rows = [ScreenRow("AAA", None, gap=0.05)]
    assert needs_verifying(rows, config=GapConfig(min_abs_gap=0.10)) == []


# --------------------------------------------------------------------------- #
# check 1 — did it really move?
# --------------------------------------------------------------------------- #
def test_check_one_reads_ibs_own_price_volume_and_gap():
    reading = check_one("AAA", _traded(110.0, volume_per_bar=5_000), previous_close=100.0,
                        as_of=DAY, run_at=RUN_AT)
    assert reading.last_price == pytest.approx(110.0)
    assert reading.gap == pytest.approx(0.10)
    assert reading.premarket_volume == 5_000 * 60
    assert reading.passed is None                       # survives; check 2 decides


def test_no_pre_market_trades_is_a_rejection_not_an_abstention():
    """IB looked at the tape and there was nothing there. That is a reading."""
    reading = check_one("AAA", [], previous_close=100.0, as_of=DAY, run_at=RUN_AT)
    assert reading.passed is False
    assert reading.reason == NO_REAL_MOVE


def test_bars_with_no_traded_shares_are_also_no_real_move():
    """IB's grid can carry a slot with nothing in it."""
    reading = check_one("AAA", _traded(110.0, volume_per_bar=0), previous_close=100.0,
                        as_of=DAY, run_at=RUN_AT)
    assert reading.passed is False
    assert reading.reason == NO_REAL_MOVE
    assert reading.premarket_volume == 0


def test_a_gap_ib_does_not_confirm_is_a_rejection():
    """yfinance said 11%; IB's tape says the name is where it closed. XEL, in one test."""
    reading = check_one("XEL", _traded(100.2, volume_per_bar=500), previous_close=100.0,
                        as_of=DAY, run_at=RUN_AT)
    assert reading.passed is False
    assert reading.reason == NO_REAL_MOVE
    assert reading.gap == pytest.approx(0.002)


def test_check_one_uses_the_adjusted_previous_close_already_held():
    """The dividend- and split-adjusted close from yfinance, not a second opinion from IB."""
    reading = check_one("AAA", _traded(110.0), previous_close=55.0, as_of=DAY, run_at=RUN_AT)
    assert reading.gap == pytest.approx(1.0)


def test_check_one_only_counts_the_pre_market_window():
    bars = _traded(110.0, volume_per_bar=1_000) + [_bar(time(10, 0), 130.0, volume=99_999)]
    reading = check_one("AAA", bars, previous_close=100.0, as_of=DAY, run_at=RUN_AT)
    assert reading.premarket_volume == 60_000


# --------------------------------------------------------------------------- #
# check 2 — was anyone there?
# --------------------------------------------------------------------------- #
def _survivor(volume: int = 600_000) -> IBKRReading:
    return IBKRReading("AAA", None, "", last_price=110.0, gap=0.10,
                       premarket_volume=volume)


def test_relative_volume_is_finally_evaluated_and_can_pass():
    reading = check_two(_survivor(600_000), _ib_history(baseline_volume=1_000),
                        as_of=DAY, run_at=RUN_AT)
    assert reading.passed is True
    assert reading.baseline_sessions == 20
    assert reading.relative_volume == pytest.approx(600_000 / 60_000)
    assert reading.reason == ""


def test_relative_volume_below_the_bar_is_a_real_failure():
    """Not an abstention any more: the number exists, so the verdict is a verdict."""
    reading = check_two(_survivor(60_000), _ib_history(baseline_volume=1_000), as_of=DAY,
                        run_at=RUN_AT)
    assert reading.passed is False
    assert "below 3.0x" in reading.reason
    assert reading.relative_volume == pytest.approx(1.0)


def test_the_median_rule_is_the_same_as_the_yfinance_path():
    """One busy morning in the baseline must not raise the bar for the next month."""
    history = _ib_history(baseline_volume=1_000)
    history += [_bar(time(5, 0), 110.0, volume=10_000_000)]   # one outlier, one session
    reading = check_two(_survivor(600_000), history, as_of=DAY, run_at=RUN_AT)
    assert reading.passed is True


def test_no_baseline_history_abstains_so_the_yfinance_row_stands():
    """IB has the volume but nothing to compare it against — a genuine NOT-EVALUATED, not a
    silent pass."""
    reading = check_two(_survivor(600_000), _traded(110.0), as_of=DAY, run_at=RUN_AT)
    assert reading.passed is None
    assert "IBKR:" in reading.reason
    assert not reading.verified


def test_the_relative_volume_threshold_comes_from_the_config():
    reading = check_two(_survivor(600_000), _ib_history(baseline_volume=1_000), as_of=DAY,
                        run_at=RUN_AT, config=GapConfig(min_relative_volume=50.0))
    assert reading.passed is False
    assert "below 50.0x" in reading.reason


# --------------------------------------------------------------------------- #
# the override
# --------------------------------------------------------------------------- #
def _yf_row(**kwargs) -> ScreenRow:
    base = dict(ticker="AAA", passed=None, reason=PRICE_NOT_CONFIRMED, gap=0.11,
                premarket_price=111.0, premarket_volume=0, relative_volume=None,
                relative_volume_note="pre-market baseline is zero", premarket_prints=5,
                confirm_prints=1, confirm_average=111.0, previous_close=100.0)
    base.update(kwargs)
    return ScreenRow(**base)


def test_a_verified_name_takes_ibs_numbers():
    reading = IBKRReading("AAA", True, "", last_price=110.0, gap=0.10,
                          premarket_volume=600_000, baseline_median=60_000.0,
                          baseline_sessions=20, relative_volume=10.0, bid=109.9, ask=110.1)
    row = apply_reading(_yf_row(), reading, spread_unknown_note="spread unknown")
    assert row.passed is True
    assert row.premarket_price == pytest.approx(110.0)
    assert row.gap == pytest.approx(0.10)
    assert row.premarket_volume == 600_000
    assert row.relative_volume == pytest.approx(10.0)
    assert row.spread == pytest.approx((110.1 - 109.9) / 110.0)


def test_the_trust_readings_are_cleared_not_carried():
    """They are an inference about participation. Where the participation is MEASURED the
    inference is not evidence, and leaving it on the row would invite applying both."""
    reading = IBKRReading("AAA", True, "", last_price=110.0, gap=0.10,
                          premarket_volume=600_000, relative_volume=10.0)
    row = apply_reading(_yf_row(), reading, spread_unknown_note="spread unknown")
    assert row.premarket_prints is None
    assert row.confirm_prints is None
    assert row.confirm_average is None
    assert row.relative_volume_note == ""


def test_a_name_the_trust_tests_rejected_can_be_rescued_by_ib():
    """The whole reason NOT-EVALUATED names are sent."""
    reading = IBKRReading("XEL", True, "", last_price=110.0, gap=0.10,
                          premarket_volume=600_000, relative_volume=10.0)
    row = apply_reading(_yf_row(ticker="XEL"), reading, spread_unknown_note="x")
    assert row.passed is True
    assert row.reason == ""


def test_a_name_ib_rejected_is_rejected_whatever_yfinance_thought():
    reading = IBKRReading("AAA", False, NO_REAL_MOVE, last_price=100.2, gap=0.002,
                          premarket_volume=100)
    row = apply_reading(_yf_row(passed=True, reason=""), reading,
                        spread_unknown_note="x")
    assert row.passed is False
    assert row.reason == NO_REAL_MOVE


def test_a_reading_ib_could_not_reach_leaves_the_row_exactly_as_it_was():
    original = _yf_row()
    assert apply_reading(original, IBKRReading("AAA", None), spread_unknown_note="x") is original


def test_ibs_spread_is_used_when_it_quoted_and_abstains_when_it_did_not():
    quoted = apply_reading(_yf_row(), IBKRReading("AAA", True, bid=109.9, ask=110.1),
                           spread_unknown_note="spread unknown")
    assert "spread" in quoted.spread_note and quoted.spread is not None
    silent = apply_reading(_yf_row(), IBKRReading("AAA", True),
                           spread_unknown_note="spread unknown — no subscription")
    assert silent.spread is None
    assert silent.spread_note == "spread unknown — no subscription"


# --------------------------------------------------------------------------- #
# the run: two requests per name, the cheap one first
# --------------------------------------------------------------------------- #
def _world() -> FakeBars:
    """yfinance's view: three names that all "gap", none with any volume."""
    daily = {t: daily_series(end=DAY, sessions=300, close=100.0, volume=3_000_000)
             for t in ("REAL", "FAKE", "THIN")}
    return FakeBars(daily=daily, intraday={
        "REAL": intraday_window(DAY, end=time(9, 0), price=110.0, volume_per_bar=0)
                + prior_sessions(before=DAY, count=20, premarket_volume_per_bar=0),
        "FAKE": intraday_window(DAY, end=time(9, 0), price=111.0, volume_per_bar=0)
                + prior_sessions(before=DAY, count=20, premarket_volume_per_bar=0),
        # a sparse tape: Batch 3's trust tests abstain on this one
        "THIN": sparse_window(DAY, price=112.0)
                + prior_sessions(before=DAY, count=20, premarket_volume_per_bar=0),
    })


def _ibkr() -> FakeIBKR:
    """IB's view: REAL and THIN genuinely traded; FAKE did not move."""
    return FakeIBKR(
        today={"REAL": _traded(110.0, volume_per_bar=10_000),
               "THIN": _traded(112.0, volume_per_bar=10_000),
               "FAKE": _traded(100.1, volume_per_bar=50)},
        history={"REAL": _ib_history(today_volume=10_000, baseline_volume=1_000),
                 "THIN": _ib_history(today_volume=10_000, baseline_volume=1_000,
                                     price=112.0)},
        quote_map={"REAL": Quote(bid=109.95, ask=110.05)})


def _run(tmp_path, *, ibkr=None, **kwargs):
    bars = _world()
    return bars, run_screen(pool=["REAL", "FAKE", "THIN"], pool_source="t", daily=bars,
                            intraday=bars, day=DAY, run_at=RUN_AT, root=tmp_path,
                            ibkr=ibkr, **kwargs)


def test_ib_settles_the_list(tmp_path):
    ib = _ibkr()
    _bars, result = _run(tmp_path, ibkr=ib)
    assert sorted(r.ticker for r in result.candidates) == ["REAL", "THIN"]
    assert result.ibkr_readings["FAKE"].reason == NO_REAL_MOVE
    assert result.ibkr_note == ""


def test_the_expensive_baseline_is_only_requested_for_survivors(tmp_path):
    ib = _ibkr()
    _run(tmp_path, ibkr=ib)
    today = [t for t, _size, duration in ib.bar_calls if duration is None]
    baseline = [t for t, _size, duration in ib.bar_calls if duration is not None]
    assert sorted(today) == ["FAKE", "REAL", "THIN"]     # check 1: everyone
    assert sorted(baseline) == ["REAL", "THIN"]          # check 2: survivors only


def test_the_baseline_is_one_request_per_name(tmp_path):
    ib = _ibkr()
    _run(tmp_path, ibkr=ib)
    baseline = [t for t, _s, duration in ib.bar_calls if duration is not None]
    assert len(baseline) == len(set(baseline))


def test_a_quote_is_only_asked_for_names_that_passed(tmp_path):
    ib = _ibkr()
    _run(tmp_path, ibkr=ib)
    assert sorted(ib.quote_calls) == ["REAL", "THIN"]


def test_a_verified_name_skips_the_yfinance_baseline_fetch(tmp_path):
    """Its verdict stands on measured volume; fetching twenty sessions of zero-volume bars to
    second-guess it would be both wasted and misleading."""
    bars, _result = _run(tmp_path, ibkr=_ibkr())
    history_calls = [call for call in bars.intraday_calls if call[1] < DAY]
    assert history_calls == []


def test_the_csv_records_the_source_and_ibs_own_numbers(tmp_path):
    _bars, _result = _run(tmp_path, ibkr=_ibkr())
    rows = {r.ticker: r for r in read_day(DAY, root=tmp_path)}
    real = rows["REAL"]
    assert real.source == SOURCE_IBKR
    assert real.ib_last_price == pytest.approx(110.0)
    assert real.ib_gap_pct == pytest.approx(0.10)
    assert real.ib_premarket_volume == 600_000
    assert real.ib_baseline_median == pytest.approx(60_000.0)
    assert real.ib_relative_volume == pytest.approx(10.0)
    assert real.ib_bid == pytest.approx(109.95)
    assert real.ib_ask == pytest.approx(110.05)


def test_the_report_names_the_source_per_candidate(tmp_path):
    _bars, result = _run(tmp_path, ibkr=_ibkr())
    text = format_report(result)
    assert "[IBKR]" in text
    # "verified" is a verdict, not a pass: IB reached one on all three.
    assert "IBKR checked 3 of 3" in text
    assert "2 confirmed, 1 rejected" in text
    assert "IBKR REJECTED (1)" in text
    assert NO_REAL_MOVE in text


# --------------------------------------------------------------------------- #
# item 3 — the gateway is not there
# --------------------------------------------------------------------------- #
def test_no_gateway_falls_back_to_yfinance_and_is_never_fatal(tmp_path):
    _bars, result = _run(tmp_path, ibkr=None)
    assert result.ibkr_note == IBKR_UNAVAILABLE
    assert result.ibkr_readings == {}
    assert result.candidates                              # the run still produced a list


def test_an_unreachable_gateway_at_run_start_says_so_once(tmp_path):
    ib = FakeIBKR(unavailable_after=0)
    _bars, result = _run(tmp_path, ibkr=ib)
    assert result.ibkr_note == IBKR_UNAVAILABLE
    assert result.ibkr_readings == {}
    text = format_report(result)
    assert text.count(IBKR_UNAVAILABLE) == 1
    assert "relative volume was NOT measured" in text


def test_a_gateway_that_goes_away_mid_run_keeps_what_it_already_said(tmp_path):
    ib = _ibkr()
    ib.unavailable_after = 3                              # after check 1, during check 2
    _bars, result = _run(tmp_path, ibkr=ib)
    assert result.ibkr_note == IBKR_UNAVAILABLE
    assert result.ibkr_readings["FAKE"].passed is False   # check 1's verdict survives


def test_the_banner_reaches_the_csv(tmp_path):
    _bars, _result = _run(tmp_path, ibkr=None)
    rows = read_day(DAY, root=tmp_path)
    assert rows
    assert all(row.ibkr_note == IBKR_UNAVAILABLE for row in rows)
    assert all(row.source == SOURCE_YFINANCE for row in rows)


def test_the_banner_reaches_the_todoist_task(tmp_path):
    from .gap_ledger_fakes import FakeTodoist

    client = FakeTodoist()
    _run(tmp_path, ibkr=None, todoist=client)
    body = client.tasks[0]["description"]
    assert IBKR_UNAVAILABLE in body
    assert "yfinance only" in body


def test_the_todoist_task_badges_a_verified_name(tmp_path):
    from .gap_ledger_fakes import FakeTodoist

    client = FakeTodoist()
    _run(tmp_path, ibkr=_ibkr(), todoist=client)
    assert "IBKR-verified" in client.tasks[0]["description"]


def test_the_banner_reaches_the_viewer(tmp_path):
    pytest.importorskip("streamlit")
    import gap_ledger_app as viewer

    from aristos_council.gap_ledger.ledger import LedgerRow
    row = LedgerRow(ticker="AAA", group=GROUP_CANDIDATE, gap_pct=0.10,
                    source=SOURCE_YFINANCE, ibkr_note=IBKR_UNAVAILABLE)
    assert viewer.candidate_table([row])[0]["Source"] == "YFINANCE"
    assert "ibkr_note" in open("gap_ledger_app.py", encoding="utf-8").read()


def test_when_ib_answers_there_is_no_banner(tmp_path):
    _bars, result = _run(tmp_path, ibkr=_ibkr())
    assert result.ibkr_note == ""
    assert IBKR_UNAVAILABLE not in format_report(result)


def test_a_subscription_refusal_for_quotes_becomes_the_spread_mark(tmp_path):
    """IB can serve historical bars on this subscription but not API streaming quotes
    (error 10089, measured 2026-09-23). The reason travels as the row's spread mark, so the
    CSV explains it rather than just showing an empty spread."""
    ib = _ibkr()
    ib.quote_map = {}                                     # nothing quotes
    ib.quote_note = "spread unknown — IBKR market-data subscription does not cover API quotes"
    _bars, result = _run(tmp_path, ibkr=ib)
    assert result.ibkr_quote_note == ib.quote_note
    assert all(row.spread is None for row in result.candidates)
    assert all(row.spread_note == ib.quote_note for row in result.candidates)
    rows = [r for r in read_day(DAY, root=tmp_path) if r.group == GROUP_CANDIDATE]
    assert rows and all(r.spread_note == ib.quote_note for r in rows)


# --------------------------------------------------------------------------- #
# item 4 — old CSVs
# --------------------------------------------------------------------------- #
def test_a_csv_written_before_the_ibkr_columns_still_loads(tmp_path):
    path = tmp_path / f"{DAY.isoformat()}.csv"
    path.write_text("date,ticker,group,gap_pct,premarket_price\n"
                    f"{DAY.isoformat()},AAA,{GROUP_CANDIDATE},0.08,110.0\n",
                    encoding="utf-8")
    rows = read_day(DAY, root=tmp_path)
    assert len(rows) == 1
    assert rows[0].gap_pct == pytest.approx(0.08)
    assert rows[0].source == ""                 # absent, not guessed
    assert rows[0].ib_last_price is None
    assert rows[0].ib_premarket_volume is None
    assert rows[0].ibkr_note == ""


# --------------------------------------------------------------------------- #
# item 5 — the licence boundary
# --------------------------------------------------------------------------- #
def test_nothing_in_aristos_imports_the_ibkr_adapter():
    """IBKR data is licensed for the owner's personal, non-professional use. It stays inside
    gap_ledger/ — a lens that ranked on it would be redistributing it."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for path in (root / "src" / "aristos_council").rglob("*.py"):
        if "gap_ledger" in path.parts:
            continue
        if "ibkr" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(root)))
    for extra in ("app.py", "examples", "scripts"):
        target = root / extra
        paths = [target] if target.is_file() else list(target.rglob("*.py")) if target.exists() else []
        for path in paths:
            if "ibkr" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(root)))
    assert offenders == [], f"IBKR must stay inside gap_ledger/: {offenders}"


def test_the_ibkr_module_lives_inside_gap_ledger():
    from aristos_council.gap_ledger import ibkr

    assert ibkr.__name__.startswith("aristos_council.gap_ledger.")
