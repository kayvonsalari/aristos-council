"""GAP-LEDGER-1 — the run end to end, the day cache, and the report the CLI prints.

The end-to-end tests exist because the units passing proves the parts work and not that the
morning does. Four properties are pinned that no unit test can see:

* the TWO-PASS fetch — today's bars for everyone, twenty sessions for the gappers only —
  and that it produces the same candidates a single pass would;
* the control group comes from the right pool: step-1 survivors that are not candidates;
* the CSV holds candidates AND control group, with the thresholds that screened them;
* the day cache honours the requested WINDOW (the VALBAND-1 rule) rather than serving
  whatever series it happens to hold.
"""
from __future__ import annotations

from datetime import date, time, timedelta

import pytest

from aristos_council.gap_ledger.bars import DailyCache, lookback_start
from aristos_council.gap_ledger.config import GapConfig, at_ny
from aristos_council.gap_ledger.ledger import GROUP_BASELINE, GROUP_CANDIDATE, read_day
from aristos_council.gap_ledger.run import CACHE_DIR, format_report, run_screen

from .gap_ledger_fakes import (FakeBars, FakeNews, FakeTodoist, daily_series,
                               intraday_window, prior_sessions, regular_session)
from aristos_council.gap_ledger.news import Headline

DAY = date(2026, 9, 22)                      # a Tuesday
RUN_AT = at_ny(DAY, time(9, 0))


def _liquid_daily(close=50.0):
    return daily_series(end=DAY, sessions=300, close=close, volume=3_000_000)


def _mover(*, premarket_price: float, premarket_volume: int, baseline_volume: int = 1_000):
    """Today's pre-market plus twenty prior sessions — a name that will gap and run hot."""
    return (intraday_window(DAY, end=time(9, 0), price=premarket_price,
                            volume_per_bar=premarket_volume)
            + regular_session(DAY - timedelta(days=0)) * 0      # no session yet today
            + prior_sessions(before=DAY, count=20,
                             premarket_volume_per_bar=baseline_volume))


def _world(**extra) -> FakeBars:
    """Three names: a mover, a quiet name, and one the provider has no bars for."""
    daily = {"MOVE": _liquid_daily(), "QUIET": _liquid_daily(), "CHEAP": _liquid_daily(4.0)}
    intraday = {"MOVE": _mover(premarket_price=55.0, premarket_volume=10_000),
                "QUIET": _mover(premarket_price=50.1, premarket_volume=1_000)}
    daily.update(extra.pop("daily", {}))
    intraday.update(extra.pop("intraday", {}))
    return FakeBars(daily=daily, intraday=intraday, **extra)


def _run(bars: FakeBars, tmp_path, **kwargs):
    pool = kwargs.pop("pool", ["MOVE", "QUIET", "CHEAP", "NODATA"])
    return run_screen(pool=pool, pool_source="a test list", daily=bars, intraday=bars,
                      day=DAY, run_at=RUN_AT, root=tmp_path, **kwargs)


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #
def test_the_mover_is_the_only_candidate(tmp_path):
    result = _run(_world(), tmp_path)
    assert [row.ticker for row in result.candidates] == ["MOVE"]
    assert result.candidates[0].gap == pytest.approx(0.10)
    assert result.candidates[0].relative_volume == pytest.approx(10.0)


def test_every_name_that_fell_out_is_named_with_a_reason(tmp_path):
    result = _run(_world(), tmp_path)
    reasons = dict(result.excluded)
    assert "below $10.00" in reasons["CHEAP"]
    assert reasons["NODATA"] == "no daily bars from the provider"
    assert "inside" in reasons["QUIET"]
    assert set(reasons) | {"MOVE"} == {"MOVE", "QUIET", "CHEAP", "NODATA"}


def test_the_two_passes_ask_for_today_first_and_history_only_for_the_gappers(tmp_path):
    bars = _world()
    _run(bars, tmp_path)
    first, second = bars.intraday_calls[0], bars.intraday_calls[1]
    assert first[1] == DAY and first[2] == DAY + timedelta(days=1)
    assert set(first[0]) == {"MOVE", "QUIET"}            # every step-1 survivor
    assert set(second[0]) == {"MOVE"}                     # the gapper alone
    assert second[1] == lookback_start(DAY, 20)


def test_history_is_not_fetched_at_all_when_nothing_gapped(tmp_path):
    bars = FakeBars(daily={"QUIET": _liquid_daily()},
                    intraday={"QUIET": _mover(premarket_price=50.1,
                                              premarket_volume=1_000)})
    result = _run(bars, tmp_path, pool=["QUIET"])
    assert result.candidates == []
    assert len(bars.intraday_calls) == 1
    assert bars.quote_calls == []


def test_quotes_are_only_asked_for_the_gappers(tmp_path):
    bars = _world()
    _run(bars, tmp_path)
    assert bars.quote_calls == [("MOVE",)]


def test_the_control_group_is_drawn_from_step_one_survivors_that_are_not_candidates(tmp_path):
    result = _run(_world(), tmp_path)
    assert result.baseline == ["QUIET"]
    assert result.baseline_pool == 1


def test_a_name_with_no_readable_gap_is_not_used_as_a_control(tmp_path):
    """A name that did not trade pre-market has no direction, so it can never be scored
    either way — including it would pad the file with unscoreable rows."""
    bars = _world(intraday={"DARK": regular_session(DAY - timedelta(days=1))},
                  daily={"DARK": _liquid_daily()})
    result = _run(bars, tmp_path, pool=["MOVE", "DARK"])
    assert result.baseline == []
    assert ("DARK", "no intraday bars from the provider") in result.not_evaluated


def test_the_control_group_matches_the_candidate_count(tmp_path):
    quiet = {f"Q{n}": _liquid_daily() for n in range(6)}
    intraday = {f"Q{n}": _mover(premarket_price=50.1, premarket_volume=1_000)
                for n in range(6)}
    bars = _world(daily=quiet, intraday=intraday)
    result = _run(bars, tmp_path, pool=["MOVE"] + list(quiet))
    assert len(result.candidates) == 1
    assert len(result.baseline) == 1


# --------------------------------------------------------------------------- #
# the record
# --------------------------------------------------------------------------- #
def test_the_csv_holds_candidates_and_the_control_group_with_their_thresholds(tmp_path):
    result = _run(_world(), tmp_path)
    rows = read_day(DAY, root=tmp_path)
    assert [r.ticker for r in rows] == ["MOVE", "QUIET"]
    assert [r.group for r in rows] == [GROUP_CANDIDATE, GROUP_BASELINE]
    assert rows[0].cfg_min_abs_gap == pytest.approx(0.03)
    assert rows[0].cfg_min_relative_volume == pytest.approx(3.0)
    assert "T04:00" in rows[0].window_start_et
    assert "T09:00" in rows[0].window_end_et
    assert result.csv_path == tmp_path / f"{DAY.isoformat()}.csv"


def test_the_control_row_records_why_it_did_not_qualify(tmp_path):
    _run(_world(), tmp_path)
    control = [r for r in read_day(DAY, root=tmp_path) if r.group == GROUP_BASELINE][0]
    assert control.screen_passed == "false"
    assert "inside" in control.screen_note
    assert control.previous_close == pytest.approx(50.0)


def test_a_dry_run_writes_nothing(tmp_path):
    result = _run(_world(), tmp_path, write=False)
    assert result.csv_path is None
    assert read_day(DAY, root=tmp_path) == []


def test_no_news_source_marks_every_candidate_news_not_fetched(tmp_path):
    _run(_world(), tmp_path)
    row = read_day(DAY, root=tmp_path)[0]
    assert row.news_found == "news not fetched"


def test_headlines_land_on_the_candidate_row(tmp_path):
    news = FakeNews(by_ticker={"MOVE": [Headline(title="Move beats", link="https://m.com/1",
                                                 published_at=RUN_AT - timedelta(hours=2),
                                                 source="m.com")]})
    _run(_world(), tmp_path, news_source=news)
    row = read_day(DAY, root=tmp_path)[0]
    assert row.news_found == "news found"
    assert row.headline == "Move beats" and row.news_link == "https://m.com/1"
    assert row.news_source == "m.com"


def test_a_candidate_with_no_news_is_marked_and_kept(tmp_path):
    """The case where the reason is not public yet is exactly the one worth seeing."""
    result = _run(_world(), tmp_path, news_source=FakeNews())
    assert [r.ticker for r in result.candidates] == ["MOVE"]
    assert read_day(DAY, root=tmp_path)[0].news_found == "no news found"


def test_news_is_only_fetched_for_candidates(tmp_path):
    news = FakeNews()
    _run(_world(), tmp_path, news_source=news)
    assert [t for t, _, _ in news.asked] == ["MOVE"]


def test_todoist_gets_one_task_for_the_days_candidates(tmp_path):
    client = FakeTodoist()
    result = _run(_world(), tmp_path, todoist=client)
    assert result.delivery.sent
    assert len(client.tasks) == 1
    assert "MOVE" in client.tasks[0]["content"]


def test_nothing_is_delivered_on_a_day_with_no_candidates(tmp_path):
    client = FakeTodoist()
    bars = FakeBars(daily={"QUIET": _liquid_daily()},
                    intraday={"QUIET": _mover(premarket_price=50.1,
                                              premarket_volume=1_000)})
    result = _run(bars, tmp_path, pool=["QUIET"], todoist=client)
    assert client.tasks == []
    assert not result.delivery.sent


def test_an_empty_pre_filter_stops_early_without_touching_intraday(tmp_path):
    bars = FakeBars(daily={"CHEAP": _liquid_daily(4.0)})
    result = _run(bars, tmp_path, pool=["CHEAP"])
    assert result.candidates == []
    assert bars.intraday_calls == []
    assert read_day(DAY, root=tmp_path) == []


# --------------------------------------------------------------------------- #
# the report
# --------------------------------------------------------------------------- #
def test_the_report_states_the_pool_the_steps_and_the_candidate(tmp_path):
    text = format_report(_run(_world(), tmp_path))
    assert "Gap Ledger — 2026-09-22" in text
    assert "a test list" in text
    assert "MOVE" in text and "+10.00%" in text and "10.00x" in text
    assert "CONTROL GROUP: 1 name(s) drawn with seed 20260922" in text
    assert "No recommendation" in text


def test_the_report_separates_not_evaluated_from_excluded(tmp_path):
    """Rule 3 at the surface: a missing reading is not a rejection, and the report may not
    print them in one list."""
    bars = _world(intraday={"DARK": regular_session(DAY - timedelta(days=1))},
                  daily={"DARK": _liquid_daily()})
    text = format_report(_run(bars, tmp_path, pool=["MOVE", "DARK", "CHEAP"]))
    assert "NOT EVALUATED (1)" in text
    assert "a missing reading, not a rejection" in text
    body = text.split("NOT EVALUATED", 1)[1]
    assert "DARK" in body.split("EXCLUDED", 1)[0]
    assert "CHEAP" in body.split("EXCLUDED", 1)[1]


def test_an_empty_day_says_so_rather_than_printing_an_empty_section(tmp_path):
    bars = FakeBars(daily={"QUIET": _liquid_daily()},
                    intraday={"QUIET": _mover(premarket_price=50.1,
                                              premarket_volume=1_000)})
    text = format_report(_run(bars, tmp_path, pool=["QUIET"]))
    assert "CANDIDATES: none today." in text


def test_the_report_says_whether_a_model_wrote_the_reason_line(tmp_path):
    text = format_report(_run(_world(), tmp_path))
    assert "Reason line: off" in text


# --------------------------------------------------------------------------- #
# the day cache — the VALBAND-1 rule
# --------------------------------------------------------------------------- #
def test_the_cache_serves_a_second_call_without_refetching(tmp_path):
    source = FakeBars(daily={"AAA": _liquid_daily()})
    cache = DailyCache(tmp_path, source=source)
    window = dict(start=DAY - timedelta(days=400), end=DAY + timedelta(days=1), as_of=DAY)
    first = cache.daily_bars(["AAA"], **window)
    second = cache.daily_bars(["AAA"], **window)
    assert len(source.daily_calls) == 1
    assert [b.day for b in first["AAA"]] == [b.day for b in second["AAA"]]


def test_the_cache_refetches_when_the_requested_window_is_wider_than_the_cached_one(tmp_path):
    """The VALBAND-1 scar: a cache that ignores the requested window serves a shorter series
    than was asked for, and every downstream reading says "insufficient history"."""
    source = FakeBars(daily={"AAA": _liquid_daily()})
    cache = DailyCache(tmp_path, source=source)
    cache.daily_bars(["AAA"], start=DAY - timedelta(days=30), end=DAY + timedelta(days=1),
                     as_of=DAY)
    cache.daily_bars(["AAA"], start=DAY - timedelta(days=400), end=DAY + timedelta(days=1),
                     as_of=DAY)
    assert len(source.daily_calls) == 2


def test_the_cache_does_not_serve_yesterdays_entry_today(tmp_path):
    source = FakeBars(daily={"AAA": _liquid_daily()})
    cache = DailyCache(tmp_path, source=source)
    window = dict(start=DAY - timedelta(days=400), end=DAY + timedelta(days=1))
    cache.daily_bars(["AAA"], as_of=DAY - timedelta(days=1), **window)
    cache.daily_bars(["AAA"], as_of=DAY, **window)
    assert len(source.daily_calls) == 2


def test_refresh_bypasses_the_cache(tmp_path):
    source = FakeBars(daily={"AAA": _liquid_daily()})
    cache = DailyCache(tmp_path, source=source)
    window = dict(start=DAY - timedelta(days=400), end=DAY + timedelta(days=1), as_of=DAY)
    cache.daily_bars(["AAA"], **window)
    cache.daily_bars(["AAA"], refresh=True, **window)
    assert len(source.daily_calls) == 2


def test_an_unreadable_cache_file_is_refetched_rather_than_raising(tmp_path):
    source = FakeBars(daily={"AAA": _liquid_daily()})
    cache = DailyCache(tmp_path, source=source)
    cache.path_for("AAA").parent.mkdir(parents=True, exist_ok=True)
    cache.path_for("AAA").write_text("{not json", encoding="utf-8")
    out = cache.daily_bars(["AAA"], start=DAY - timedelta(days=400),
                           end=DAY + timedelta(days=1), as_of=DAY)
    assert out["AAA"]


def test_a_ticker_with_punctuation_gets_a_windows_safe_cache_filename(tmp_path):
    cache = DailyCache(tmp_path, source=FakeBars())
    assert cache.path_for("BRK-B").name == "BRK-B.json"
    assert cache.path_for("BF.B").name == "BF_B.json"


def test_the_run_caches_under_the_ledger_root(tmp_path):
    _run(_world(), tmp_path)
    assert (tmp_path / CACHE_DIR / "MOVE.json").exists()


# --------------------------------------------------------------------------- #
# progress
# --------------------------------------------------------------------------- #
def test_the_run_reports_each_phase_so_a_long_fetch_cannot_look_like_a_hang(tmp_path):
    said: list[str] = []
    _run(_world(), tmp_path, progress=said.append)
    joined = "\n".join(said)
    assert "pre-filtering" in joined
    assert "today's pre-market bars" in joined
    assert "sessions of pre-market history" in joined
    assert "candidate(s) after relative volume" in joined
    assert "wrote" in joined


def test_thresholds_flow_from_the_config_into_the_run(tmp_path):
    strict = GapConfig(min_relative_volume=50.0)
    result = _run(_world(), tmp_path, config=strict)
    assert result.candidates == []
    assert "below 50.0x" in dict(result.excluded)["MOVE"]
