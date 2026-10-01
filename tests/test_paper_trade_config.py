"""PAPER-TRADE-1 — config: the kill switch and path derivation."""
from __future__ import annotations

from datetime import date

from aristos_council.paper_trade import config as pt_config


def test_stopped_is_false_with_no_stop_file(tmp_path):
    assert pt_config.stopped(tmp_path) is False


def test_stopped_is_true_once_the_stop_file_exists(tmp_path):
    (tmp_path / "STOP").touch()
    assert pt_config.stopped(tmp_path) is True


def test_paths_derive_from_one_day_and_root(tmp_path):
    day = date(2026, 9, 29)
    paths = pt_config.paths_for(day, tmp_path)
    assert paths.entries_json == tmp_path / "2026-09-29_entries.json"
    assert paths.exits_json == tmp_path / "2026-09-29_exits.json"
    assert paths.record_csv == tmp_path / "2026-09-29.csv"
    # Gap Ledger's own CSV is read from ITS root, never this package's output root.
    assert paths.gap_ledger_csv == pt_config.GAP_LEDGER_ROOT / "2026-09-29.csv"


def test_default_port_is_paper_never_live():
    assert pt_config.DEFAULT_PORT == 4002
    assert pt_config.LIVE_PORT_FORBIDDEN == 4001


def test_client_id_is_distinct_from_gap_ledgers_own():
    # Gap Ledger's own IBKR_CLIENT_ID defaults to 17 (gap_ledger/ibkr.py) — never imported
    # here, but the two must never collide if run at the same moment.
    assert pt_config.DEFAULT_CLIENT_ID != 17
