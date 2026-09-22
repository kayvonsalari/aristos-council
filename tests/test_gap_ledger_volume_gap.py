"""GAP-LEDGER-1 — what happens when the provider does not publish pre-market volume.

**The live finding, 2026-09-22.** The first real run of this screener produced ZERO
candidates out of 50 liquid US names, with all seven gappers abstaining on the identical
message "pre-market baseline is zero over 20 sessions". Probing the provider settled why:
yfinance serves pre-market PRICES but never pre-market VOLUME. Every extended-hours bar
comes back with ``Volume == 0`` — confirmed on AMD and TSLA, at 5-minute and 1-minute
intervals, through both ``yf.download`` and ``Ticker.history``. The prices are real (AMD's
last pre-market print on 2026-09-21 was 583.89 and the 09:30 open was 583.94, and the
+4.30% gap it implies is correct against the 559.82 previous adjusted close).

Left alone, that is the VALBAND-1 failure repeated exactly: a green suite, a registered
feature, and an empty list every single day, for a reason no report ever stated.

So the consequence is handled, and these tests pin the handling:

* the ratio is a NOT-EVALUATED reading, and rule 3 governs — it may not act as a confirmed
  fail. The name is KEPT and MARKED (the brief's own treatment of a missing spread and a
  missing headline).
* a ratio that CAN be computed and falls short still FAILS. The filter is in full force
  wherever the data exists.
* the absence is reported ONCE at the run, not as N identical per-name faults, and it
  reaches every surface the owner reads: the report, the CSV row, the Todoist task, the
  viewer.
* ``require_relative_volume=True`` restores the brief's literal reading for anyone who
  would rather have an empty list than a gap-only one.
"""
from __future__ import annotations

from datetime import date, time, timedelta

import pytest

from aristos_council.gap_ledger.config import GapConfig
from aristos_council.gap_ledger.ledger import GROUP_CANDIDATE, LedgerRow, read_day
from aristos_council.gap_ledger.run import (format_report, premarket_volume_unavailable,
                                            run_screen)
from aristos_council.gap_ledger.screen import ScreenRow, screen_one
from aristos_council.gap_ledger.todoist import task_body

from aristos_council.gap_ledger.bars import Quote
from .gap_ledger_fakes import (FakeBars, FakeTodoist, daily_series, intraday_window,
                               prior_sessions)
from aristos_council.gap_ledger.config import at_ny

DAY = date(2026, 9, 22)
RUN_AT = at_ny(DAY, time(9, 0))


def _priced_but_volumeless() -> list:
    """The real yfinance shape: pre-market bars with true prices and zero volume."""
    return (intraday_window(DAY, end=time(9, 0), price=55.0, volume_per_bar=0)
            + prior_sessions(before=DAY, count=20, premarket_volume_per_bar=0))


# --------------------------------------------------------------------------- #
# the screen
# --------------------------------------------------------------------------- #
def test_a_gapper_with_no_volume_data_is_kept_and_marked():
    row = screen_one("AMD", bars=_priced_but_volumeless(), previous_close=50.0, as_of=DAY,
                     run_at=RUN_AT)
    assert row.passed is True
    assert row.relative_volume is None
    assert "baseline is zero" in row.relative_volume_note
    assert row.reason == ""                  # kept, so there is no rejection to state


def test_the_gap_is_still_measured_exactly():
    """The prices are real even where the volume is not — that is the whole reason the name
    is worth keeping."""
    row = screen_one("AMD", bars=_priced_but_volumeless(), previous_close=50.0, as_of=DAY,
                     run_at=RUN_AT)
    assert row.gap == pytest.approx(0.10)
    assert row.premarket_price == pytest.approx(55.0)


def test_requiring_the_reading_abstains_instead():
    """The brief's literal wording, available to anyone who wants it."""
    row = screen_one("AMD", bars=_priced_but_volumeless(), previous_close=50.0, as_of=DAY,
                     run_at=RUN_AT, config=GapConfig(require_relative_volume=True))
    assert row.passed is None
    assert "baseline is zero" in row.reason


def test_a_computable_ratio_below_the_bar_still_fails():
    """Abstention is only for silence. Where the data exists the filter is untouched."""
    bars = (intraday_window(DAY, end=time(9, 0), price=55.0, volume_per_bar=1_100)
            + prior_sessions(before=DAY, count=20, premarket_volume_per_bar=1_000))
    row = screen_one("AMD", bars=bars, previous_close=50.0, as_of=DAY, run_at=RUN_AT)
    assert row.passed is False
    assert "below 3.0x" in row.reason
    assert row.relative_volume_note == ""


def test_a_computable_ratio_above_the_bar_carries_no_mark():
    bars = (intraday_window(DAY, end=time(9, 0), price=55.0, volume_per_bar=10_000)
            + prior_sessions(before=DAY, count=20, premarket_volume_per_bar=1_000))
    row = screen_one("AMD", bars=bars, previous_close=50.0, as_of=DAY, run_at=RUN_AT)
    assert row.passed is True
    assert row.relative_volume == pytest.approx(10.0)
    assert row.relative_volume_note == ""


def test_a_small_gap_is_still_rejected_when_volume_is_unavailable():
    """The gap leg does all the work now, so it had better still do it."""
    bars = (intraday_window(DAY, end=time(9, 0), price=50.5, volume_per_bar=0)
            + prior_sessions(before=DAY, count=20, premarket_volume_per_bar=0))
    row = screen_one("AMD", bars=bars, previous_close=50.0, as_of=DAY, run_at=RUN_AT)
    assert row.passed is False
    assert "inside" in row.reason


# --------------------------------------------------------------------------- #
# the run-level reading
# --------------------------------------------------------------------------- #
def test_provider_silence_is_detected_across_every_measured_name():
    rows = [ScreenRow("A", True, premarket_volume=0, baseline_median_volume=0.0),
            ScreenRow("B", True, premarket_volume=0, baseline_median_volume=0.0)]
    assert premarket_volume_unavailable(rows)


def test_one_busy_name_anywhere_disproves_provider_silence():
    """One quiet morning is not a dead provider, and must not be reported as one."""
    rows = [ScreenRow("A", True, premarket_volume=0, baseline_median_volume=0.0),
            ScreenRow("B", True, premarket_volume=90_000, baseline_median_volume=1_000.0)]
    assert not premarket_volume_unavailable(rows)


def test_a_nonzero_baseline_with_a_quiet_today_is_not_provider_silence():
    rows = [ScreenRow("A", False, premarket_volume=0, baseline_median_volume=5_000.0)]
    assert not premarket_volume_unavailable(rows)


def test_nothing_measured_is_not_a_claim_either_way():
    assert not premarket_volume_unavailable([ScreenRow("A", None)])
    assert not premarket_volume_unavailable([])


# --------------------------------------------------------------------------- #
# every surface says so
# --------------------------------------------------------------------------- #
def _run(tmp_path, **kwargs):
    bars = FakeBars(daily={"AMD": daily_series(end=DAY, sessions=300, volume=3_000_000),
                           "QUIET": daily_series(end=DAY, sessions=300, volume=3_000_000)},
                    intraday={"AMD": _priced_but_volumeless(),
                              "QUIET": intraday_window(DAY, end=time(9, 0), price=50.1,
                                                       volume_per_bar=0)
                                       + prior_sessions(before=DAY, count=20,
                                                        premarket_volume_per_bar=0)})
    return run_screen(pool=["AMD", "QUIET"], pool_source="a test list", daily=bars,
                      intraday=bars, day=DAY, run_at=RUN_AT, root=tmp_path, **kwargs)


def test_the_run_sets_the_note_and_still_produces_the_list(tmp_path):
    result = _run(tmp_path)
    assert [r.ticker for r in result.candidates] == ["AMD"]
    assert "served NO pre-market volume" in result.volume_note
    assert "GAP ALONE" in result.volume_note


def test_the_report_says_it_once_at_the_top(tmp_path):
    text = format_report(_run(tmp_path))
    assert text.count("served NO pre-market volume") == 1
    assert "!! DATA GAP" in text
    assert "rel.vol unavailable" in text


def test_the_report_never_prints_a_dash_that_could_read_as_about_zero(tmp_path):
    """"—" beside a volume threshold reads as a small number. "unavailable" does not."""
    candidates = format_report(_run(tmp_path)).split("CANDIDATES", 1)[1]
    assert "rel.vol" in candidates
    assert "rel.vol           —" not in candidates


def test_the_csv_row_carries_the_mark_and_the_policy_it_was_screened_under(tmp_path):
    _run(tmp_path)
    row = read_day(DAY, root=tmp_path)[0]
    assert row.relative_volume is None
    assert "baseline is zero" in row.relative_volume_note
    assert row.cfg_require_relative_volume == "false"


def test_the_todoist_task_says_the_volume_was_unavailable(tmp_path):
    client = FakeTodoist()
    _run(tmp_path, todoist=client)
    body = client.tasks[0]["description"]
    assert "UNAVAILABLE" in body
    assert "baseline is zero" in body


def test_requiring_the_reading_empties_the_list_and_says_why(tmp_path):
    """The literal-brief setting, and the cost of it, both visible."""
    result = _run(tmp_path, config=GapConfig(require_relative_volume=True))
    assert result.candidates == []
    assert ("AMD", "pre-market baseline is zero over 20 sessions — ratio not computable"
            ) in result.not_evaluated
    assert "NOT EVALUATED (1)" in format_report(result)


def test_the_viewer_warns_that_the_names_were_picked_on_the_gap_alone(tmp_path):
    pytest.importorskip("streamlit")
    import gap_ledger_app as viewer

    row = LedgerRow(ticker="AMD", group=GROUP_CANDIDATE, gap_pct=0.043,
                    relative_volume=None,
                    relative_volume_note="pre-market baseline is zero over 20 sessions")
    assert viewer.relative_volume_cell(row) == "unavailable"
    assert "baseline is zero" in viewer.candidate_table([row])[0]["Flags"]


# --------------------------------------------------------------------------- #
# the spread on a historical run
# --------------------------------------------------------------------------- #
def test_a_backfill_does_not_read_a_live_book(tmp_path):
    """Live, 2026-09-22: a backfill of 2026-09-21 marked RIOT "wide spread 52.42%" from a
    pre-dawn book that had nothing to do with the morning being screened. A book is a
    snapshot of now, so a past date cannot have one."""
    bars = FakeBars(daily={"AMD": daily_series(end=DAY, sessions=300, volume=3_000_000)},
                    intraday={"AMD": _priced_but_volumeless()},
                    quote_map={"AMD": Quote(bid=50.0, ask=80.0)})
    result = run_screen(pool=["AMD"], pool_source="a test list", daily=bars, intraday=bars,
                        day=DAY, run_at=RUN_AT, root=tmp_path, write=False, live=False)
    assert bars.quote_calls == []                    # not even asked for
    assert result.candidates[0].spread is None
    assert "historical run" in result.candidates[0].spread_note


def test_a_live_run_does_read_the_book(tmp_path):
    bars = FakeBars(daily={"AMD": daily_series(end=DAY, sessions=300, volume=3_000_000)},
                    intraday={"AMD": _priced_but_volumeless()},
                    quote_map={"AMD": Quote(bid=99.9, ask=100.1)})
    result = run_screen(pool=["AMD"], pool_source="a test list", daily=bars, intraday=bars,
                        day=DAY, run_at=RUN_AT, root=tmp_path, write=False, live=True)
    assert bars.quote_calls == [("AMD",)]
    assert result.candidates[0].spread == pytest.approx(0.002)
    assert "wide spread" in result.candidates[0].spread_note


def test_liveness_is_inferred_from_the_market_calendar(monkeypatch, tmp_path):
    """A scheduled 09:00 run screens today and reads the book; a --date backfill does not,
    without anyone having to remember to say so."""
    monkeypatch.setattr("aristos_council.gap_ledger.run.now_ny",
                        lambda: at_ny(DAY, time(9, 0)))
    bars = FakeBars(daily={"AMD": daily_series(end=DAY, sessions=300, volume=3_000_000)},
                    intraday={"AMD": _priced_but_volumeless()},
                    quote_map={"AMD": Quote(bid=99.9, ask=100.1)})
    today = run_screen(pool=["AMD"], pool_source="t", daily=bars, intraday=bars, day=DAY,
                       run_at=RUN_AT, root=tmp_path, write=False)
    assert today.candidates[0].spread is not None

    bars.quote_calls.clear()
    older = DAY - timedelta(days=1)
    stale = FakeBars(daily={"AMD": daily_series(end=older, sessions=300, volume=3_000_000)},
                     intraday={"AMD": intraday_window(older, end=time(9, 0), price=55.0,
                                                      volume_per_bar=0)
                                      + prior_sessions(before=older, count=20,
                                                       premarket_volume_per_bar=0)},
                     quote_map={"AMD": Quote(bid=99.9, ask=100.1)})
    back = run_screen(pool=["AMD"], pool_source="t", daily=stale, intraday=stale, day=older,
                      run_at=at_ny(older, time(9, 0)), root=tmp_path, write=False)
    assert stale.quote_calls == []
    assert "historical run" in back.candidates[0].spread_note
