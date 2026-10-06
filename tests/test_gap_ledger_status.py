"""GAP-STATUS-1 - every weekday run leaves one status task in Todoist, including failed ones.

Found 2026-10-06: Todoist had no Gap Ledger task for 09-30, 10-01, 10-02 and 10-05. The scheduled
run had been started and then killed every day at the task's one-hour limit (GAP-BACKFILL-1's catch-up
tried to rebuild 2026-09-23 through ~1,300 paced IBKR history requests before the live scan), and
because Todoist is only posted to when there ARE candidates, a run that never finished looked like a
quiet day. Nothing here touches the screen or the score; no test reaches Todoist or the network.
"""
from __future__ import annotations

from datetime import date, time

from aristos_council.gap_ledger import status as st
from aristos_council.gap_ledger.ledger import (GROUP_BASELINE, GROUP_CANDIDATE, LedgerRow,
                                               status_row, write_day)
from aristos_council.gap_ledger.config import at_ny

DAY = date(2026, 10, 6)                       # a Tuesday


class _Todoist:
    def __init__(self, fail=False):
        self.tasks, self.fail = {}, fail

    def find_project(self, name):
        if self.fail:
            raise ConnectionResetError(10054, "reset")
        return "p1"

    def create_project(self, name):
        return "p1"

    def find_task(self, project_id, *, title_prefix):
        return next((k for k, (c, _d) in self.tasks.items() if c.startswith(title_prefix)), None)

    def create_task(self, *, content, description, project_id):
        key = f"t{len(self.tasks) + 1}"
        self.tasks[key] = (content, description)
        return key

    def update_task(self, task_id, *, content, description):
        self.tasks[task_id] = (content, description)


def _write(root, day, n_ibkr=0, n_yf=0, banner=""):
    rows = [LedgerRow(date=day.isoformat(), ticker=f"I{i}", group=GROUP_CANDIDATE, source="ibkr",
                      ibkr_note=banner) for i in range(n_ibkr)]
    rows += [LedgerRow(date=day.isoformat(), ticker=f"Y{i}", group=GROUP_CANDIDATE,
                       source="yfinance", ibkr_note=banner) for i in range(n_yf)]
    rows += [LedgerRow(date=day.isoformat(), ticker="CTRL", group=GROUP_BASELINE)]
    write_day(day, rows, root=root)


def _post(tmp_path, **kw):
    client = kw.pop("client", _Todoist())
    facts = st.RunFacts(day=kw.pop("day", DAY), **kw)
    title, outcome = st.post_status(facts, client=client, root=tmp_path / "ledger",
                                    state_path=tmp_path / "state.json", sleep=lambda s: None)
    return client, title, outcome


def test_a_good_day_says_ran_candidates_and_ibkr_verified(tmp_path):
    _write(tmp_path / "ledger", DAY, n_ibkr=15, n_yf=1)
    client, title, outcome = _post(tmp_path, exit_code=0)
    assert outcome.sent
    assert title.startswith("Gap Ledger STATUS 2026-10-06 - RAN: 16 candidate(s) found")
    assert "IBKR verified 15 of 16" in title and "MISSED" not in title


def test_an_empty_day_is_reported_not_silent(tmp_path):
    _write(tmp_path / "ledger", DAY)
    _, title, _ = _post(tmp_path, exit_code=0, log_text="IBKR reached a verdict on 12 of 12: 0")
    assert "RAN: 0 candidate(s) found" in title and "IBKR reached" in title


def test_ibkr_banner_reads_ibkr_unavailable(tmp_path):
    _write(tmp_path / "ledger", DAY, n_yf=3, banner="!! IBKR UNAVAILABLE: volume not checked")
    _, title, _ = _post(tmp_path, exit_code=0)
    assert "RAN: 3 candidate(s) found" in title and "IBKR UNAVAILABLE" in title


def test_a_killed_run_is_reported_failed_with_the_reason_and_the_log_tail(tmp_path):
    """The incident: started, never wrote a ledger file, killed at the time limit."""
    log = "backfilling 2026-09-23\n2026-09-23: 1312 of 5764 cleared price, volume and history\n"
    client, title, _ = _post(tmp_path, exit_code=124, timed_out=True, timeout_minutes=50,
                             log_text=log)
    assert "FAILED" in title and "killed after 50 min" in title
    body = next(iter(client.tasks.values()))[1]
    assert "1312 of 5764 cleared" in body and "Result: FAILED" in body


def test_a_crash_exit_code_is_named(tmp_path):
    _, title, _ = _post(tmp_path, exit_code=1, log_text="Traceback ...\nValueError: boom")
    assert "FAILED" in title and "exited with code 1" in title


def test_gateway_down_stub_reads_failed_and_ibkr_unavailable(tmp_path):
    root = tmp_path / "ledger"
    write_day(DAY, [status_row(DAY, run_at=at_ny(DAY, time(9)),
                               status="incomplete", reason="ibkr unreachable")], root=root)
    _, title, _ = _post(tmp_path, exit_code=3,
                        log_text="Gap Ledger 2026-10-06 NOT RUN: IB Gateway unreachable after retrying")
    assert "FAILED" in title and "IBKR UNAVAILABLE" in title


def test_missed_days_are_listed_by_the_next_run(tmp_path):
    """Machine off Wed-Thu: the Friday run's status names both days."""
    root = tmp_path / "ledger"
    _write(root, date(2026, 9, 29))
    assert st.missed_days(date(2026, 10, 2), root=root) == [date(2026, 9, 30), date(2026, 10, 1)]
    _write(root, date(2026, 10, 2))
    _, title, _ = _post(tmp_path, day=date(2026, 10, 2), exit_code=0)
    assert "MISSED 2 day(s): 09-30, 10-01" in title


def test_status_posted_yesterday_is_not_reported_missed_again(tmp_path):
    root = tmp_path / "ledger"
    _write(root, date(2026, 9, 29))
    st.write_state(date(2026, 10, 1), tmp_path / "state.json")
    assert st.missed_days(date(2026, 10, 2), root=root,
                          last_status_day=date(2026, 10, 1)) == []


def test_weekends_and_holidays_are_not_missed(tmp_path):
    root = tmp_path / "ledger"
    _write(root, date(2026, 10, 2))                       # Friday
    assert st.missed_days(date(2026, 10, 5), root=root) == []


def test_no_anchor_means_nothing_is_called_missed(tmp_path):
    assert st.missed_days(DAY, root=tmp_path / "empty") == []


def test_same_day_status_is_updated_not_duplicated(tmp_path):
    client = _Todoist()
    _write(tmp_path / "ledger", DAY, n_ibkr=2)
    _post(tmp_path, client=client, exit_code=0)
    _post(tmp_path, client=client, exit_code=0)
    assert len(client.tasks) == 1


def test_status_prefix_cannot_collide_with_the_candidate_list(tmp_path):
    """The candidate task is titled 'Gap Ledger <date> ...'; dedup there is a prefix search."""
    assert not "Gap Ledger STATUS 2026-10-06".startswith("Gap Ledger 2026-10-06")
    assert not "Gap Ledger 2026-10-06 - 3 name(s)".startswith(st.title_prefix(DAY))


def test_a_todoist_outage_never_raises_and_the_day_is_not_remembered(tmp_path):
    _write(tmp_path / "ledger", DAY, n_ibkr=1)
    _, _, outcome = _post(tmp_path, client=_Todoist(fail=True), exit_code=0)
    assert not outcome.sent and outcome.error
    assert st.read_state(tmp_path / "state.json") is None


def test_a_delivered_status_is_remembered(tmp_path):
    _write(tmp_path / "ledger", DAY, n_ibkr=1)
    _post(tmp_path, exit_code=0)
    assert st.read_state(tmp_path / "state.json") == DAY


def test_log_slice_reads_only_this_runs_bytes_and_survives_utf16_debris(tmp_path):
    log = tmp_path / "run.log"
    log.write_bytes("old run\n".encode() + "b\x00a\x00d\x00\n\x00".encode("latin-1") + b"")
    start = log.stat().st_size
    with open(log, "ab") as f:
        f.write("new run — ok\n".encode("utf-8"))
    assert st.log_slice(log, start) == "new run — ok\n"
    assert "bad" in st.log_slice(log, 0)


def test_closed_market_day_is_not_called_a_failure(tmp_path):
    holiday = date(2026, 11, 26)                          # Thanksgiving
    _, title, _ = _post(tmp_path, day=holiday, exit_code=0)
    assert "NYSE CLOSED" in title and "FAILED" not in title


def test_cli_status_posts_and_prints(tmp_path, monkeypatch, capsys):
    from aristos_council.gap_ledger import __main__ as cli
    root = tmp_path / "ledger"
    _write(root, DAY, n_ibkr=4)
    monkeypatch.chdir(tmp_path)
    client = _Todoist()
    monkeypatch.setattr(cli, "RestTodoist", lambda: client)
    assert cli.main(["--root", str(root), "status", "--date", "2026-10-06", "--exit-code", "0"]) == 0
    assert "RAN: 4 candidate(s) found" in capsys.readouterr().out
    assert len(client.tasks) == 1
