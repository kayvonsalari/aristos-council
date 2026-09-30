"""GAP-BACKFILL-1 — self-healing catch-up: detection, the per-day rebuild, the live-window
gate, the gateway-down path, and Todoist dedup. Recorded fixtures throughout; no network, no
LLM (nothing here even imports one).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from aristos_council.gap_ledger import backfill as bf
from aristos_council.gap_ledger.config import NY, at_ny
from aristos_council.gap_ledger.ibkr import IBKRUnavailable
from aristos_council.gap_ledger.ledger import (COMPLETE, GROUP_CANDIDATE, INCOMPLETE, PARTIAL,
                                               ledger_path, read_day, write_day)
from aristos_council.gap_ledger.nyse_calendar import is_trading_day, nyse_holidays
from aristos_council.gap_ledger.todoist import task_title

from .gap_ledger_fakes import (FakeBars, FakeIBKR, FakeTodoist, daily_series, intraday_window,
                               prior_sessions, regular_session)

DAY = date(2026, 9, 22)                              # a Tuesday


def _liquid_daily(day: date, close: float = 50.0):
    return daily_series(end=day, sessions=300, close=close, volume=3_000_000)


def _mover_bars(day: date, *, premarket_price: float = 55.0, premarket_volume: int = 10_000,
                baseline_volume: int = 1_000):
    """A name that will gap and clear relative volume, on ANY given day."""
    return (intraday_window(day, end=time(9, 0), price=premarket_price,
                            volume_per_bar=premarket_volume)
            + prior_sessions(before=day, count=20, premarket_volume_per_bar=baseline_volume))


def _world(day: date, tickers=("MOVE", "QUIET")):
    daily = {t: _liquid_daily(day) for t in tickers}
    intraday = {"MOVE": _mover_bars(day), "QUIET": _mover_bars(day, premarket_price=50.1,
                                                              premarket_volume=1_000)}
    return FakeBars(daily=daily, intraday={t: intraday[t] for t in tickers if t in intraday})


def _backfill_one(day, bars, *, root, ibkr=None, todoist=None, pool=("MOVE", "QUIET")):
    return bf.backfill_one_day(day, root=root, pool=list(pool), daily=bars, intraday=bars,
                               ibkr=ibkr, todoist=todoist, now=at_ny(day + timedelta(days=1),
                                                                    time(15, 0)))


# =========================================================================== #
# the NYSE calendar (used by detection)
# =========================================================================== #
def test_missing_day_detection_skips_weekends_and_nyse_holidays(tmp_path):
    write_day(date(2026, 9, 21), [], root=tmp_path)            # the only file: a Monday
    got = bf.missing_days(tmp_path, now=at_ny(date(2026, 9, 25), time(17, 0)))
    # 9/21 itself is logged (complete, empty day -> present); 9/22-9/25 are all weekdays with
    # no NYSE holiday in between, so all four are missing. The weekend either side never
    # appears at all.
    assert got == [date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 25)]
    assert all(is_trading_day(d) for d in got)


def test_missing_day_detection_skips_a_real_holiday(tmp_path):
    # Thanksgiving 2026 is 2026-11-26 (a Thursday) - confirmed against nyse_calendar itself.
    assert date(2026, 11, 26) in nyse_holidays(2026)
    write_day(date(2026, 11, 24), [], root=tmp_path)
    got = bf.missing_days(tmp_path, now=at_ny(date(2026, 11, 27), time(17, 0)))
    assert date(2026, 11, 26) not in got                        # Thanksgiving itself
    assert date(2026, 11, 28) not in got                        # the following Saturday
    assert got == [date(2026, 11, 25), date(2026, 11, 27)]      # the two real trading days


def test_no_logged_day_at_all_means_nothing_missing(tmp_path):
    assert bf.missing_days(tmp_path) == []


# =========================================================================== #
# incomplete/partial files count as missing
# =========================================================================== #
def test_a_complete_day_is_not_missing(tmp_path):
    write_day(DAY, [], root=tmp_path)                           # run_status blank -> COMPLETE
    assert DAY not in bf.missing_days(tmp_path, now=at_ny(DAY + timedelta(days=3), time(17, 0)))


def test_an_incomplete_or_partial_day_is_still_missing(tmp_path):
    from aristos_council.gap_ledger.ledger import status_row
    write_day(DAY, [status_row(DAY, run_at=at_ny(DAY, time(9, 0)), status=INCOMPLETE,
                               reason="ibkr unreachable")], root=tmp_path)
    later = DAY + timedelta(days=3)
    assert DAY in bf.missing_days(tmp_path, now=at_ny(later, time(17, 0)))
    write_day(DAY, [status_row(DAY, run_at=at_ny(DAY, time(9, 0)), status=PARTIAL,
                               reason="x")], root=tmp_path)
    assert DAY in bf.missing_days(tmp_path, now=at_ny(later, time(17, 0)))


def test_an_old_file_with_no_run_status_column_reads_as_complete(tmp_path):
    """Every row's run_status is "" (a file written before GAP-BACKFILL-1) -> COMPLETE, never
    mistaken for missing."""
    path = ledger_path(DAY, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("date,ticker,group\n2026-09-22,MOVE,candidate\n", encoding="utf-8")
    later = DAY + timedelta(days=3)
    assert DAY not in bf.missing_days(tmp_path, now=at_ny(later, time(17, 0)))


def test_only_the_last_completed_trading_day_bounds_the_window(tmp_path):
    write_day(DAY, [], root=tmp_path)
    # Before the close on DAY + 1: that day is not yet "completed" and is excluded.
    got = bf.missing_days(tmp_path, now=at_ny(DAY + timedelta(days=1), time(8, 0)))
    assert got == []
    got = bf.missing_days(tmp_path, now=at_ny(DAY + timedelta(days=1), time(16, 0)))
    assert got == [DAY + timedelta(days=1)]


# =========================================================================== #
# the cap, oldest first
# =========================================================================== #
def test_the_cap_is_respected_oldest_first(tmp_path, monkeypatch):
    write_day(date(2026, 9, 1), [], root=tmp_path)
    monkeypatch.setattr(bf, "missing_days",
                        lambda root, now=None: [date(2026, 9, d) for d in range(2, 25)])
    calls = []
    monkeypatch.setattr(bf, "backfill_one_day",
                        lambda day, **kw: (calls.append(day),
                                          bf.BackfillDayResult(day=day, status=COMPLETE))[1])
    report = bf.run_backfill(root=tmp_path, pool=["MOVE"], daily=FakeBars(), intraday=FakeBars(),
                             max_days=15, now=at_ny(date(2026, 9, 30), time(17, 0)))
    assert calls == [date(2026, 9, d) for d in range(2, 17)]     # exactly 15, oldest first
    assert len(report.filled) == 15
    assert report.remaining == [date(2026, 9, d) for d in range(17, 25)]


def test_dry_run_lists_everything_uncapped_and_fetches_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "missing_days",
                        lambda root, now=None: [date(2026, 9, d) for d in range(1, 20)])
    called = []
    monkeypatch.setattr(bf, "backfill_one_day", lambda *a, **k: called.append(1))
    report = bf.run_backfill(root=tmp_path, pool=["MOVE"], daily=FakeBars(), intraday=FakeBars(),
                             max_days=5, dry_run=True)
    assert not called
    assert len(report.remaining) == 19 and not report.filled


# =========================================================================== #
# backfilled flag, per-field sources, and honest fallback
# =========================================================================== #
def test_a_backfilled_day_carries_the_flag_and_status_on_every_row(tmp_path):
    result = _backfill_one(DAY, _world(DAY), root=tmp_path)
    assert result.status == COMPLETE and result.n_candidates == 1
    rows = read_day(DAY, tmp_path)
    assert rows and all(r.backfilled == "true" and r.backfilled_at for r in rows)
    assert all(r.run_status == COMPLETE for r in rows)


def test_gap_prefers_ibkr_history_when_ibkr_has_it(tmp_path):
    bars = _world(DAY)
    ibkr = FakeIBKR(bars={"MOVE": _mover_bars(DAY, premarket_price=60.0)})   # a DIFFERENT gap
    result = _backfill_one(DAY, bars, root=tmp_path, ibkr=ibkr)
    rows = {r.ticker: r for r in read_day(DAY, tmp_path) if r.group == GROUP_CANDIDATE}
    assert rows["MOVE"].backfill_gap_source == "ibkr-history"
    assert rows["MOVE"].backfill_volume_source == "ibkr-history"
    assert rows["MOVE"].source == "ibkr"
    assert rows["MOVE"].gap_pct == pytest.approx(0.20)            # IBKR's 60, not yfinance's 55
    assert result.status == COMPLETE


def test_gap_falls_back_to_yfinance_when_ibkr_has_no_history_for_that_day(tmp_path):
    bars = _world(DAY)
    ibkr = FakeIBKR(bars={})                            # IBKR: nothing for anyone
    result = _backfill_one(DAY, bars, root=tmp_path, ibkr=ibkr)
    rows = {r.ticker: r for r in read_day(DAY, tmp_path) if r.group == GROUP_CANDIDATE}
    assert rows["MOVE"].backfill_gap_source == "yfinance"
    assert rows["MOVE"].backfill_volume_source == "yfinance"
    assert rows["MOVE"].source == "yfinance"
    assert result.status == COMPLETE                     # yfinance supplied everything needed
    assert result.n_candidates == 1


def test_relative_volume_unavailable_from_either_source_is_marked_and_the_day_is_partial(
        tmp_path):
    # A real gap, but NO prior sessions at all -> relative volume cannot be computed from
    # either provider. Kept as a candidate on the gap alone (require_relative_volume=False,
    # the same honest policy a live run applies) and the day is flagged partial.
    thin = intraday_window(DAY, end=time(9, 0), price=55.0, volume_per_bar=10_000)
    bars = FakeBars(daily={"MOVE": _liquid_daily(DAY)}, intraday={"MOVE": thin})
    result = _backfill_one(DAY, bars, root=tmp_path, pool=["MOVE"])
    rows = read_day(DAY, tmp_path)
    move = next(r for r in rows if r.ticker == "MOVE")
    assert result.status == PARTIAL and move.screen_passed == "true"
    assert move.backfill_gap_source == "yfinance"
    assert move.backfill_volume_source == "unavailable"
    assert "field(s)" in " ".join(result.notes)


def test_a_gap_unavailable_name_gets_no_row_at_all_same_as_a_live_run(tmp_path):
    """No pre-market bars at all -> no direction -> not scoreable either way, exactly the
    existing live-run rule (never a candidate, never a comparable baseline member)."""
    bars = FakeBars(daily={"MOVE": _liquid_daily(DAY)}, intraday={})
    result = _backfill_one(DAY, bars, root=tmp_path, pool=["MOVE"])
    rows = read_day(DAY, tmp_path)
    assert not any(r.ticker == "MOVE" for r in rows)
    assert result.n_candidates == 0 and result.n_baseline == 0


def test_never_fabricates_a_value_gap_is_none_not_zero(tmp_path):
    bars = FakeBars(daily={"MOVE": _liquid_daily(DAY)}, intraday={})
    _backfill_one(DAY, bars, root=tmp_path, pool=["MOVE"])
    rows = [r for r in read_day(DAY, tmp_path) if r.ticker == "MOVE"]
    if rows:
        assert rows[0].gap_pct is None                   # never a fabricated 0.0


def test_spread_stays_n_a_on_a_backfilled_day(tmp_path):
    result = _backfill_one(DAY, _world(DAY), root=tmp_path)
    assert result.n_candidates == 1
    row = next(r for r in read_day(DAY, tmp_path) if r.group == GROUP_CANDIDATE)
    assert row.spread_pct is None
    assert "historical run" in row.spread_note


def test_news_is_restricted_to_items_before_the_historical_cutoff(tmp_path):
    from aristos_council.gap_ledger.news import Headline
    from .gap_ledger_fakes import FakeNews
    cutoff = at_ny(DAY, time(9, 0))
    early = Headline(title="early", source="x", link="l1", symbols=("MOVE",),
                     published_at=cutoff - timedelta(hours=1))
    late = Headline(title="late", source="x", link="l2", symbols=("MOVE",),
                    published_at=cutoff + timedelta(hours=1))
    news = FakeNews(by_ticker={"MOVE": [early, late]})
    result = bf.backfill_one_day(DAY, root=tmp_path, pool=["MOVE", "QUIET"], daily=_world(DAY),
                                 intraday=_world(DAY), news_source=news,
                                 run_at=cutoff, now=at_ny(DAY + timedelta(days=1), time(15, 0)))
    assert result.n_candidates == 1
    since, until = news.asked[0][1], news.asked[0][2]
    assert until == cutoff and since < cutoff


# =========================================================================== #
# the live-window gate
# =========================================================================== #
def test_within_live_window_true_only_inside_the_tolerance():
    scheduled_start = at_ny(DAY, time(9, 0))
    assert bf.within_live_window(scheduled_start)
    assert bf.within_live_window(scheduled_start + timedelta(minutes=44))
    assert not bf.within_live_window(scheduled_start + timedelta(minutes=46))
    assert not bf.within_live_window(scheduled_start - timedelta(minutes=1))


# =========================================================================== #
# the gateway-down path (7a)
# =========================================================================== #
def test_ensure_gateway_retries_and_then_succeeds():
    ibkr = FakeIBKR(unreachable_after=0, bars={})
    ibkr.unreachable = True
    calls = {"n": 0}

    def flaky_ping():
        calls["n"] += 1
        if calls["n"] < 3:
            raise IBKRUnavailable("down")
    ibkr.ping = flaky_ping
    slept = []
    ok = bf.ensure_gateway(ibkr, timeout_seconds=300, interval_seconds=60,
                           sleep=slept.append)
    assert ok is True and slept == [60, 60]                 # two waits before the third try


def test_ensure_gateway_gives_up_after_the_timeout():
    ibkr = FakeIBKR(unreachable=True)
    slept = []
    ok = bf.ensure_gateway(ibkr, timeout_seconds=180, interval_seconds=60, sleep=slept.append)
    assert ok is False
    assert ibkr.ping_calls == 4                             # 0, 60, 120, 180 -> 4 attempts
    assert slept == [60, 60, 60]


def test_gateway_down_writes_a_stub_and_posts_one_alert_never_a_live_scan(tmp_path):
    todoist = FakeTodoist()
    row = bf.write_gateway_down_stub(DAY, root=tmp_path, run_at=at_ny(DAY, time(9, 0)),
                                     todoist=todoist)
    assert row.run_status == INCOMPLETE and row.status_reason == "ibkr unreachable"
    rows = read_day(DAY, tmp_path)
    assert len(rows) == 1 and rows[0].group == "status"
    assert len(todoist.tasks) == 1
    assert "NOT RUN" in todoist.tasks[0]["content"] and "IB Gateway" in todoist.tasks[0]["content"]
    # eligible for backfill on the next run (point 1)
    assert DAY in bf.missing_days(tmp_path, now=at_ny(DAY + timedelta(days=3), time(17, 0)))


def test_a_gateway_down_alert_is_never_duplicated_and_upgrades_into_the_real_task(tmp_path):
    todoist = FakeTodoist()
    bf.write_gateway_down_stub(DAY, root=tmp_path, run_at=at_ny(DAY, time(9, 0)), todoist=todoist)
    assert len(todoist.tasks) == 1
    first_id = todoist.tasks[0]["id"]
    # a SECOND gateway-down run on the same day updates, not duplicates
    bf.write_gateway_down_stub(DAY, root=tmp_path, run_at=at_ny(DAY, time(9, 0)), todoist=todoist)
    assert len(todoist.tasks) == 1 and todoist.tasks[0]["id"] == first_id
    # and a later successful backfill for the same day UPGRADES the alert into a real task,
    # rather than leaving a stale "NOT RUN" beside a new one (7c)
    _backfill_one(DAY, _world(DAY), root=tmp_path, todoist=todoist)
    assert len(todoist.tasks) == 1 and todoist.tasks[0]["id"] == first_id
    assert "NOT RUN" not in todoist.tasks[0]["content"]
    assert "backfilled on" in todoist.tasks[0]["content"]


# =========================================================================== #
# a partial day is redone when IBKR is back (7b)
# =========================================================================== #
def test_a_partial_day_is_redone_and_the_old_one_kept_for_audit(tmp_path):
    bars = _world(DAY)
    # first pass: IBKR unreachable entirely -> yfinance only, still COMPLETE here since
    # yfinance supplied everything MOVE needed; force a partial by starving relative volume:
    starved = FakeBars(daily={"MOVE": _liquid_daily(DAY), "QUIET": _liquid_daily(DAY)},
                       intraday={"MOVE": intraday_window(DAY, end=time(9, 0), price=55.0,
                                                          volume_per_bar=10_000)})  # no baseline
    first = _backfill_one(DAY, starved, root=tmp_path)
    assert first.status == PARTIAL
    original_text = ledger_path(DAY, tmp_path).read_text(encoding="utf-8")

    # second pass: IBKR now has the full baseline history -> redo, complete
    ibkr = FakeIBKR(bars={"MOVE": _mover_bars(DAY)})
    second = _backfill_one(DAY, bars, root=tmp_path, ibkr=ibkr)
    assert second.status == COMPLETE
    partial_copy = ledger_path(DAY, tmp_path).parent / f"{DAY.isoformat()}.partial.csv"
    assert partial_copy.exists() and partial_copy.read_text(encoding="utf-8") == original_text
    redone = [r for r in read_day(DAY, tmp_path) if r.group == GROUP_CANDIDATE]
    assert redone and redone[0].backfill_gap_source == "ibkr-history"


def test_a_complete_day_is_never_silently_overwritten(tmp_path):
    write_day(DAY, [], root=tmp_path)                        # a genuinely complete (empty) day
    before = ledger_path(DAY, tmp_path).read_text(encoding="utf-8")
    result = _backfill_one(DAY, _world(DAY), root=tmp_path)
    assert result.status == COMPLETE and "already complete" in " ".join(result.notes)
    assert ledger_path(DAY, tmp_path).read_text(encoding="utf-8") == before


# =========================================================================== #
# nothing missing behaves exactly as today
# =========================================================================== #
def test_a_run_with_nothing_missing_does_not_touch_the_ledger(tmp_path):
    write_day(DAY, [], root=tmp_path)
    before = ledger_path(DAY, tmp_path).read_text(encoding="utf-8")
    report = bf.run_backfill(root=tmp_path, pool=["MOVE"], daily=FakeBars(), intraday=FakeBars(),
                             now=at_ny(DAY, time(9, 30)))
    assert report.filled == [] and report.remaining == []
    assert ledger_path(DAY, tmp_path).read_text(encoding="utf-8") == before


# =========================================================================== #
# no duplicate Todoist tasks
# =========================================================================== #
def test_re_backfilling_the_same_day_updates_not_duplicates(tmp_path):
    todoist = FakeTodoist()
    bars = _world(DAY)
    _backfill_one(DAY, bars, root=tmp_path, todoist=todoist)
    assert len(todoist.tasks) == 1
    first_id = todoist.tasks[0]["id"]
    # force it partial so it is eligible again, then redo it
    from aristos_council.gap_ledger.ledger import stamp_backfill
    rows = read_day(DAY, tmp_path)
    stamp_backfill(rows, status=PARTIAL, reason="test", at=at_ny(DAY, time(9, 0)))
    write_day(DAY, rows, root=tmp_path)
    _backfill_one(DAY, bars, root=tmp_path, todoist=todoist)
    assert len(todoist.tasks) == 1 and todoist.tasks[0]["id"] == first_id
    assert len(todoist.updated) == 1


def test_the_title_and_body_say_backfilled_in_the_first_line(tmp_path):
    todoist = FakeTodoist()
    _backfill_one(DAY, _world(DAY), root=tmp_path, todoist=todoist)
    task = todoist.tasks[0]
    assert task["content"].startswith(f"Gap Ledger {DAY.isoformat()} (backfilled on ")
    assert task["description"].splitlines()[0].startswith("_Backfilled on ")


# =========================================================================== #
# score() split by origin
# =========================================================================== #
def test_score_splits_live_from_backfilled(tmp_path):
    from aristos_council.gap_ledger.score import score
    from aristos_council.gap_ledger.ledger import read_all
    _backfill_one(DAY, _world(DAY), root=tmp_path)
    write_day(DAY + timedelta(days=1), [], root=tmp_path)     # a plain "live" empty day
    days = read_all(tmp_path)
    combined = score(days)
    live = score(days, origin="live")
    backfilled = score(days, origin="backfilled")
    assert combined.candidates == live.candidates + backfilled.candidates
    assert backfilled.candidates == 1 and live.candidates == 0
    with pytest.raises(ValueError):
        score(days, origin="bogus")


# =========================================================================== #
# cmd_run wiring: the gateway-down path and the live-window gate, end to end
# =========================================================================== #
def _patch_cli(monkeypatch, tmp_path, *, ibkr, bars=None, todoist=None):
    from aristos_council.gap_ledger import __main__ as cli
    bars = bars or FakeBars()
    monkeypatch.setattr(cli, "pool_from_index", lambda config_path: ["MOVE", "QUIET"])
    monkeypatch.setattr(cli, "names_from_index", lambda config_path: {})
    monkeypatch.setattr(cli, "YFinanceBars", lambda config: bars)
    monkeypatch.setattr(cli, "IBKRBars", lambda: ibkr)
    monkeypatch.setattr(cli, "EODHDNews", lambda: None)
    monkeypatch.setattr(cli, "RestTodoist", lambda: todoist or FakeTodoist())
    return cli


def test_cmd_run_writes_a_stub_and_alert_and_never_screens_when_the_gateway_stays_down(
        monkeypatch, tmp_path, capsys):
    import time as _time
    ibkr = FakeIBKR(unreachable=True)
    todoist = FakeTodoist()
    cli = _patch_cli(monkeypatch, tmp_path, ibkr=ibkr, todoist=todoist)
    monkeypatch.setattr(_time, "sleep", lambda seconds: None)     # 10 real minutes -> instant
    called_run_screen = []
    monkeypatch.setattr(cli, "run_screen", lambda **kw: called_run_screen.append(1))
    today = at_ny(DAY, time(9, 0))
    monkeypatch.setattr(cli, "now_ny", lambda: today)

    code = cli.main(["--root", str(tmp_path), "run"])
    out = capsys.readouterr().out
    assert code == 3 and not called_run_screen
    assert "NOT RUN" in out and "IB Gateway" in out
    rows = read_day(DAY, tmp_path)
    assert len(rows) == 1 and rows[0].run_status == INCOMPLETE
    assert len(todoist.tasks) == 1


def test_cmd_run_stops_after_backfilling_when_outside_the_live_window(monkeypatch, tmp_path,
                                                                       capsys):
    ibkr = FakeIBKR(bars={})
    bars = FakeBars()
    cli = _patch_cli(monkeypatch, tmp_path, ibkr=ibkr, bars=bars)
    called_run_screen = []
    monkeypatch.setattr(cli, "run_screen", lambda **kw: called_run_screen.append(1))
    late = at_ny(DAY, time(11, 0))                       # well past the 45-minute tolerance
    monkeypatch.setattr(cli, "now_ny", lambda: late)
    monkeypatch.setattr(bf, "now_ny", lambda: late)

    code = cli.main(["--root", str(tmp_path), "run"])
    out = capsys.readouterr().out
    assert code == 0 and not called_run_screen
    assert "outside live window, backfill only" in out


def test_cmd_run_screens_live_as_before_inside_the_window_with_nothing_missing(
        monkeypatch, tmp_path, capsys):
    from aristos_council.gap_ledger.run import RunResult
    write_day(DAY, [], root=tmp_path)                    # today already logged -> nothing to backfill
    ibkr = FakeIBKR(bars={})
    cli = _patch_cli(monkeypatch, tmp_path, ibkr=ibkr)
    called_run_screen = []
    monkeypatch.setattr(cli, "run_screen",
                        lambda **kw: (called_run_screen.append(kw),
                                     RunResult(day=DAY, run_at=at_ny(DAY, time(9, 0))))[1])
    on_time = at_ny(DAY, time(9, 10))
    monkeypatch.setattr(cli, "now_ny", lambda: on_time)
    monkeypatch.setattr(bf, "now_ny", lambda: on_time)

    code = cli.main(["--root", str(tmp_path), "run"])
    assert code == 0 and len(called_run_screen) == 1     # unchanged behaviour: it screens
