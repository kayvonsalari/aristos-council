"""GAP-LEDGER-1 — the CLI's three verbs and the read-only viewer.

The viewer's contract is the important one: it NEVER starts a screen. A screen costs news
calls, optionally an LLM call, and posts a Todoist task; a browser button that fires all
three on a stray click is not a thing this version offers. The test asserts the absence
structurally — the module must not import the run entry at all — rather than by hoping
nobody adds a button later.
"""
from __future__ import annotations

from datetime import date, time, timedelta
from pathlib import Path

import pytest

from aristos_council.gap_ledger import __main__ as cli
from aristos_council.gap_ledger.config import at_ny
from aristos_council.gap_ledger.ledger import (GROUP_BASELINE, GROUP_CANDIDATE, LedgerRow,
                                              write_day)

ROOT = Path(__file__).resolve().parents[1]
_APP = ROOT / "gap_ledger_app.py"

DAY = date(2026, 9, 22)


def _candidate(ticker="AAA", **kwargs) -> LedgerRow:
    base = dict(date=DAY.isoformat(), ticker=ticker, group=GROUP_CANDIDATE, gap_pct=0.082,
                relative_volume=6.4, previous_close=50.0, premarket_price=54.1,
                spread_pct=0.0004, spread_note="spread ok", news_found="news found",
                headline="Alpha beats", news_source="a.com",
                news_link="https://a.com/1", run_at_et="2026-09-22T09:00-04:00",
                reason="Alpha reported results above expectations (https://a.com/1)")
    base.update(kwargs)
    return LedgerRow(**base)


def _control(ticker="BBB", **kwargs) -> LedgerRow:
    base = dict(date=DAY.isoformat(), ticker=ticker, group=GROUP_BASELINE, gap_pct=0.004,
                relative_volume=1.1, screen_note="gap +0.40% inside ±3.0%")
    base.update(kwargs)
    return LedgerRow(**base)


# --------------------------------------------------------------------------- #
# the CLI
# --------------------------------------------------------------------------- #
def test_the_parser_offers_exactly_the_three_verbs():
    parser = cli.build_parser()
    actions = [a for a in parser._actions if getattr(a, "choices", None)
               and "run" in getattr(a, "choices", {})]
    assert sorted(actions[0].choices) == ["outcomes", "run", "score"]


def test_a_verb_is_required():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])


def test_explain_is_off_unless_asked_for():
    assert cli.build_parser().parse_args(["run"]).explain is False


def test_news_and_todoist_are_on_unless_switched_off():
    args = cli.build_parser().parse_args(["run"])
    assert args.no_news is False and args.no_todoist is False


def test_a_bad_date_stops_with_a_message_not_a_traceback():
    with pytest.raises(SystemExit) as caught:
        cli._parse_date("22.09.2026")
    assert "YYYY-MM-DD" in str(caught.value)


def test_a_bad_run_time_stops_with_a_message():
    with pytest.raises(SystemExit) as caught:
        cli._parse_time("9am")
    assert "HH:MM in New York time" in str(caught.value)


def test_run_with_no_index_and_no_ticker_file_exits_two_with_advice(tmp_path, capsys):
    missing = tmp_path / "market_index.yaml"
    missing.write_text(f"exchanges: [US]\nroot: {tmp_path.as_posix()}/nothing\n",
                       encoding="utf-8")
    code = cli.main(["--root", str(tmp_path), "run", "--index-config", str(missing)])
    assert code == 2
    printed = capsys.readouterr().out
    assert "market_index build" in printed and "--tickers" in printed


def test_score_on_an_empty_root_says_so_rather_than_failing(tmp_path, capsys):
    assert cli.main(["--root", str(tmp_path), "score"]) == 0
    assert "No days logged" in capsys.readouterr().out


def test_score_prints_the_day_floor_verdict(tmp_path, capsys):
    write_day(DAY, [_candidate(open_price=50.0, price_1000=51.0, price_1130=52.0,
                               close_price=53.0)], root=tmp_path)
    assert cli.main(["--root", str(tmp_path), "score"]) == 0
    printed = capsys.readouterr().out
    assert "Not enough days: 1 scored of 40" in printed
    assert "10:00 ET" in printed


def test_outcomes_with_nothing_to_fill_says_so(tmp_path, capsys):
    assert cli.main(["--root", str(tmp_path), "outcomes"]) == 0
    assert "Nothing to fill" in capsys.readouterr().out


def test_unfilled_days_lists_only_days_with_a_gap(tmp_path):
    write_day(DAY, [_candidate(open_price=50.0, price_1000=51.0, price_1130=52.0,
                               close_price=53.0)], root=tmp_path)
    earlier = DAY - timedelta(days=1)
    write_day(earlier, [_candidate(date=earlier.isoformat())], root=tmp_path)
    assert cli._unfilled_days(str(tmp_path)) == [earlier]


def test_outcomes_refusing_an_open_session_is_reported_and_returns_nonzero(tmp_path, capsys,
                                                                          monkeypatch):
    """The bar source is injected: TEST-ISOLATION-1 refuses to construct the real one, and
    this test is about the CLI's refusal, not about a provider."""
    from .gap_ledger_fakes import FakeBars

    today = at_ny(DAY, time(11, 0))
    monkeypatch.setattr("aristos_council.gap_ledger.outcomes.now_ny", lambda: today)
    monkeypatch.setattr(cli, "YFinanceBars", lambda config: FakeBars())
    write_day(DAY, [_candidate()], root=tmp_path)
    code = cli.main(["--root", str(tmp_path), "outcomes", "--date", DAY.isoformat()])
    assert code == 1
    assert "has not closed yet" in capsys.readouterr().out


def test_outcomes_fills_a_closed_session_through_the_cli(tmp_path, capsys, monkeypatch):
    from .gap_ledger_fakes import FakeBars, daily_series, regular_session

    bars = FakeBars(daily={"AAA": daily_series(end=DAY + timedelta(days=1), sessions=1,
                                               close=52.0)},
                    intraday={"AAA": regular_session(DAY, price=51.0)})
    monkeypatch.setattr("aristos_council.gap_ledger.outcomes.now_ny",
                        lambda: at_ny(DAY, time(16, 30)))
    monkeypatch.setattr(cli, "YFinanceBars", lambda config: bars)
    write_day(DAY, [_candidate()], root=tmp_path)
    assert cli.main(["--root", str(tmp_path), "outcomes"]) == 0
    assert "filled outcomes for 1 of 1" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# the viewer
# --------------------------------------------------------------------------- #
def test_the_viewer_cannot_start_a_screen():
    """Structural, not hopeful: the module does not import the run entry, so no button in
    it can call one."""
    source = _APP.read_text(encoding="utf-8")
    assert "run_screen" not in source
    assert "import run" not in source
    assert "gap_ledger.run" not in source


def test_the_viewer_does_not_import_council_station():
    """They share a repo, adapters and a .env — and nothing else."""
    source = _APP.read_text(encoding="utf-8")
    assert "import app" not in source
    assert "from app " not in source


def test_council_station_does_not_link_to_the_gap_ledger():
    """The brief: nothing in the Aristos UI imports or links to it."""
    assert "gap_ledger" not in (ROOT / "app.py").read_text(encoding="utf-8")


def _app_test(tmp_path, monkeypatch, timeout=60):
    """Drive the viewer over a FIXTURE ledger, never over whatever is on the disk."""
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("GAP_LEDGER_ROOT", str(tmp_path))
    return AppTest.from_file(str(_APP), default_timeout=timeout).run()


def _said(at) -> str:
    return " ".join(str(getattr(e, "value", "")) for e in
                    list(at.info) + list(at.caption) + list(at.warning)
                    + list(at.subheader) + list(at.markdown))


def test_the_viewer_renders_with_no_days_logged(monkeypatch, tmp_path):
    pytest.importorskip("streamlit")
    at = _app_test(tmp_path, monkeypatch)
    assert not at.exception
    assert "0 day(s) logged" in _said(at)
    assert "No days logged" in _said(at) or "No screen logged" in _said(at)


def test_the_viewer_says_it_is_read_only(monkeypatch, tmp_path):
    pytest.importorskip("streamlit")
    at = _app_test(tmp_path, monkeypatch)
    assert not at.exception
    assert "read-only" in _said(at)


def test_the_viewer_renders_a_logged_day_with_its_candidate_and_control_group(monkeypatch,
                                                                             tmp_path):
    pytest.importorskip("streamlit")
    write_day(DAY, [_candidate(), _control()], root=tmp_path)
    at = _app_test(tmp_path, monkeypatch)
    assert not at.exception
    said = _said(at)
    assert "1 day(s) logged" in said
    assert "1 candidate(s)" in said                 # the Past days tab renders the day
    assert "Alpha reported results above expectations" in said


def test_the_viewer_scorecard_holds_the_day_floor_back(monkeypatch, tmp_path):
    pytest.importorskip("streamlit")
    write_day(DAY, [_candidate(open_price=50.0, price_1000=51.0, price_1130=52.0,
                               close_price=53.0)], root=tmp_path)
    at = _app_test(tmp_path, monkeypatch)
    assert not at.exception
    warnings = " ".join(str(getattr(e, "value", "")) for e in at.warning)
    assert "Not enough days: 1 scored of 40" in warnings


# --------------------------------------------------------------------------- #
# the viewer's pure render helpers — tested without Streamlit at all
# --------------------------------------------------------------------------- #
def test_the_candidate_table_renders_every_figure_from_the_row():
    pytest.importorskip("streamlit")
    import gap_ledger_app as viewer

    table = viewer.candidate_table([_candidate()])
    assert table[0]["Gap"] == "+8.20%"
    assert table[0]["Rel. pre-market volume"] == "6.40x"
    assert table[0]["Spread"] == "0.04%"
    assert table[0]["Link"] == "https://a.com/1"


def test_an_unknown_spread_renders_as_unknown_not_as_zero():
    pytest.importorskip("streamlit")
    import gap_ledger_app as viewer

    assert viewer.candidate_table([_candidate(spread_pct=None)])[0]["Spread"] == "unknown"


def test_an_unscoreable_continuation_cell_is_blank_not_a_no():
    """Blank means NOT SCOREABLE. A "no" there would be a claim the record cannot make."""
    pytest.importorskip("streamlit")
    import gap_ledger_app as viewer

    rows = viewer.outcome_table([_candidate(open_price=50.0, price_1000=None)])
    assert rows[0]["Carried on @ 10:00 ET"] == ""


def test_a_filled_continuation_cell_reads_yes_or_no():
    pytest.importorskip("streamlit")
    import gap_ledger_app as viewer

    rows = viewer.outcome_table([_candidate(open_price=50.0, price_1000=51.0,
                                            price_1130=49.0, close_price=49.0)])
    assert rows[0]["Carried on @ 10:00 ET"] == "yes"
    assert rows[0]["Carried on @ 11:30 ET"] == "no"


def test_the_split_separates_candidates_from_the_control_group():
    pytest.importorskip("streamlit")
    import gap_ledger_app as viewer

    candidates, control = viewer.split([_candidate(), _control()])
    assert [r.ticker for r in candidates] == ["AAA"]
    assert [r.ticker for r in control] == ["BBB"]
