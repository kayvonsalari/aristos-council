"""GAP-REPORT-CLARITY-1 — three things the first IBKR run said badly.

From the 2026-09-24 run: 17 candidates, 16 IBKR-verified.

(a) The "!! DATA GAP … selected on the GAP ALONE" banner printed anyway, although only DRI was
    yfinance-only. A banner that says "these names" while pointing at a page of verified names
    teaches the reader that banners are noise. It is now SCOPED and NAMES the rows it is about,
    and omitted entirely when there are none.

(b) The IBKR spread-subscription sentence repeated on all seventeen rows. It is a fact about
    the ACCOUNT, so it belongs in the header once; the row keeps "spread n/a".

(c) "25 gapped at least 3.0%" followed by "asking IBKR about 40 gappers" read as a
    contradiction. Both numbers were true — the first counted the gappers the trust tests
    vouched for, the second counted every gapper — and printing them as two unrelated lines
    was the fault. One sentence now: "N trusted + M untrusted, all sent to IBKR".
"""
from __future__ import annotations

from datetime import date, time, timedelta

import pytest

from aristos_council.gap_ledger.bars import Quote
from aristos_council.gap_ledger.config import at_ny
from aristos_council.gap_ledger.ledger import GROUP_CANDIDATE, LedgerRow
from aristos_council.gap_ledger.run import _short_spread, format_report, run_screen
from aristos_council.gap_ledger.todoist import task_body
from aristos_council.gap_ledger.verify import IBKR_UNAVAILABLE

from .gap_ledger_fakes import FakeBars, daily_series, intraday_window, prior_sessions
from .test_gap_ledger_verify import FakeIBKR, _ib_history, _traded

DAY = date(2026, 9, 22)
RUN_AT = at_ny(DAY, time(9, 0))
SUBSCRIPTION = ("spread unknown — IBKR market-data subscription does not cover API "
                "streaming quotes")


def _dense(price: float):
    return intraday_window(DAY, end=time(9, 0), price=price, volume_per_bar=0)


def _world(names) -> FakeBars:
    daily = {t: daily_series(end=DAY, sessions=300, close=100.0, volume=3_000_000)
             for t in names}
    intraday = {t: _dense(110.0) + prior_sessions(before=DAY, count=20,
                                                 premarket_volume_per_bar=0)
                for t in names}
    return FakeBars(daily=daily, intraday=intraday)


def _run(tmp_path, names, *, ibkr=None, **kwargs):
    bars = _world(names)
    return bars, run_screen(pool=list(names), pool_source="t", daily=bars, intraday=bars,
                            day=DAY, run_at=RUN_AT, root=tmp_path, ibkr=ibkr, **kwargs)


# --------------------------------------------------------------------------- #
# (a) the banner is scoped, and omitted when it is about nobody
# --------------------------------------------------------------------------- #
def test_the_banner_names_the_one_yfinance_only_candidate(tmp_path):
    """The DRI case: one name of seventeen, so the banner says one name and which."""
    # DRI survives IB's check 1 but IB has no baseline for it, so check 2 ABSTAINS and the
    # yfinance row stands — which is exactly how a name ends up selected on the gap alone.
    ib = FakeIBKR(today={"VERIFIED": _traded(110.0), "DRI": _traded(110.0)},
                  history={"VERIFIED": _ib_history()})
    _bars, result = _run(tmp_path, ["VERIFIED", "DRI"], ibkr=ib)
    assert result.gap_only == ["DRI"]
    assert "1 name selected on the gap alone" in result.volume_note
    assert "DRI" in result.volume_note
    text = format_report(result)
    assert "!! DATA GAP" in text
    assert text.count("selected on the gap alone") == 1


def test_the_banner_is_omitted_when_every_candidate_was_measured(tmp_path):
    """Nothing to warn about, so nothing is said. This is the half that was broken."""
    ib = FakeIBKR(today={t: _traded(110.0) for t in ("A", "B")},
                  history={t: _ib_history() for t in ("A", "B")})
    _bars, result = _run(tmp_path, ["A", "B"], ibkr=ib)
    assert [r.ticker for r in result.candidates]
    assert result.gap_only == []
    assert result.volume_note == ""
    assert "!! DATA GAP" not in format_report(result)
    assert "selected on the gap alone" not in format_report(result)


def test_the_banner_pluralises_honestly(tmp_path):
    _bars, result = _run(tmp_path, ["AAA", "BBB"], ibkr=None)
    assert len(result.gap_only) == 2
    assert "2 names selected on the gap alone" in result.volume_note


def test_a_day_with_no_candidates_says_nothing_about_the_gap_alone(tmp_path):
    bars = FakeBars(daily={"QUIET": daily_series(end=DAY, sessions=300, close=100.0,
                                                 volume=3_000_000)},
                    intraday={"QUIET": _dense(100.2)
                              + prior_sessions(before=DAY, count=20,
                                               premarket_volume_per_bar=0)})
    result = run_screen(pool=["QUIET"], pool_source="t", daily=bars, intraday=bars,
                        day=DAY, run_at=RUN_AT, root=tmp_path)
    assert result.candidates == []
    assert result.volume_note == ""


# --------------------------------------------------------------------------- #
# (b) the subscription sentence, once
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("note, short", [
    (SUBSCRIPTION, "spread unknown"),
    ("wide spread 4.20%", "wide spread 4.20%"),
    ("spread unknown — historical run, a book cannot be read retroactively",
     "spread unknown"),
    ("spread ok", "spread ok"),
    ("", "spread n/a"),
])
def test_the_row_keeps_only_the_part_about_the_row(note, short):
    assert _short_spread(note) == short


def test_the_report_says_the_subscription_reason_once_and_the_rows_say_spread_na(tmp_path):
    ib = FakeIBKR(today={t: _traded(110.0) for t in ("A", "B", "C")},
                  history={t: _ib_history() for t in ("A", "B", "C")},
                  quote_note=SUBSCRIPTION)
    _bars, result = _run(tmp_path, ["A", "B", "C"], ibkr=ib)
    text = format_report(result)
    assert len(result.candidates) == 3
    # The explanation appears exactly once, in the header.
    assert text.count("does not cover API streaming quotes") == 1
    assert "Spread: spread unknown — IBKR market-data subscription" in text
    # And every candidate line carries the short mark instead.
    candidate_lines = [line for line in text.splitlines()
                       if line.startswith("  A ") or line.startswith("  B ")
                       or line.startswith("  C ")]
    assert candidate_lines
    for line in candidate_lines:
        assert "spread n/a" in line
        assert "subscription" not in line


def test_the_todoist_task_says_it_once_too(tmp_path):
    rows = [LedgerRow(ticker=t, group=GROUP_CANDIDATE, gap_pct=0.1, relative_volume=39.0,
                      source="ibkr", news_found="no news found", spread_note=SUBSCRIPTION)
            for t in ("A", "B", "C")]
    body = task_body(rows)
    assert body.count("does not cover API streaming quotes") == 1
    assert body.count("spread n/a") == 3


def test_a_wide_spread_still_reaches_the_row(tmp_path):
    """Scoping the run-level note must not swallow a mark that IS about the name."""
    rows = [LedgerRow(ticker="A", group=GROUP_CANDIDATE, gap_pct=0.1, relative_volume=39.0,
                      spread_note="wide spread 4.20%", news_found="no news found")]
    assert "wide spread 4.20%" in task_body(rows)


def test_no_subscription_note_means_no_header_line(tmp_path):
    ib = FakeIBKR(today={"A": _traded(110.0)}, history={"A": _ib_history()},
                  quote_map={"A": Quote(bid=109.9, ask=110.1)})
    _bars, result = _run(tmp_path, ["A"], ibkr=ib)
    assert result.ibkr_quote_note == ""
    assert "Spread:" not in format_report(result)


# --------------------------------------------------------------------------- #
# (c) the two counts, as one sentence
# --------------------------------------------------------------------------- #
def test_trusted_and_untrusted_gappers_are_reported_together(tmp_path):
    """"25 gapped" then "asking IBKR about 40" looked like a contradiction. Both were true."""
    from .gap_ledger_fakes import sparse_window

    bars = _world(["TRUSTED"])
    bars.daily["UNTRUSTED"] = daily_series(end=DAY, sessions=300, close=100.0,
                                           volume=3_000_000)
    bars.intraday["UNTRUSTED"] = (sparse_window(DAY, price=111.0)
                                  + prior_sessions(before=DAY, count=20,
                                                   premarket_volume_per_bar=0))
    said: list[str] = []
    ib = FakeIBKR(today={t: _traded(110.0) for t in ("TRUSTED", "UNTRUSTED")},
                  history={t: _ib_history() for t in ("TRUSTED", "UNTRUSTED")})
    result = run_screen(pool=["TRUSTED", "UNTRUSTED"], pool_source="t", daily=bars,
                        intraday=bars, day=DAY, run_at=RUN_AT, root=tmp_path, ibkr=ib,
                        progress=said.append)
    assert result.gapped == 1
    assert result.untrusted_gappers == 1
    line = next(l for l in said if "trusted gapper" in l)
    assert line == "1 trusted gapper(s) + 1 untrusted, all 2 sent to IBKR"


def test_the_old_contradictory_pair_of_lines_is_gone(tmp_path):
    said: list[str] = []
    _run(tmp_path, ["AAA"], progress=said.append)
    assert not any("gapped at least" in line for line in said)
    assert not any(line.startswith("asking IBKR about") for line in said)


def test_without_ibkr_the_sentence_does_not_claim_anything_was_sent(tmp_path):
    said: list[str] = []
    _run(tmp_path, ["AAA"], ibkr=None, progress=said.append)
    line = next(l for l in said if "trusted gapper" in l)
    assert "sent to IBKR" not in line
    assert line == "1 trusted gapper(s) + 0 untrusted"


def test_the_ibkr_unavailable_banner_is_unaffected(tmp_path):
    """Item 3 of GAP-IBKR-1 still holds: a missing gateway says so once, loudly."""
    _bars, result = _run(tmp_path, ["AAA"], ibkr=None)
    text = format_report(result)
    assert result.ibkr_note == IBKR_UNAVAILABLE
    assert text.count(IBKR_UNAVAILABLE) == 1
