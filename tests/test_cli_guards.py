"""CLI input guards — the paste-slip lesson (ITEM 4).

A doubled paste once put a shell path into the ticker argv, which silently became an
UNRATEABLE row in the PERMANENT snapshot record. The guards reject such input LOUDLY
(naming the offending token) before any adapter runs, and make positional tickers and
--universe-id mutually exclusive. Pure functions tested directly; the two CLIs tested
via their argparse error path (which exits before any network).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from aristos_council.cli_guards import implausible_ticker_reason, universe_args_error

ROOT = Path(__file__).resolve().parents[1]
_PASTE = "examples/snapshot_consensus.py"           # the 2026-07-06 doubled-paste token


# --------------------------------------------------------------------------- #
# Pure guards
# --------------------------------------------------------------------------- #
def test_implausible_flags_paths_scripts_and_flags():
    assert implausible_ticker_reason(_PASTE)                     # path separator + .py
    assert implausible_ticker_reason("foo.py")                  # .py tail
    assert implausible_ticker_reason("a/b")                     # path separator
    assert implausible_ticker_reason("--rank-strategy")         # a flag, not a ticker


def test_real_tickers_are_not_flagged():
    for good in ("AAPL", "MU", "BRK.B", "BRK-B", "000660.KS", "aapl"):
        assert implausible_ticker_reason(good) is None


def test_doubled_paste_argv_errors_naming_the_token():
    err = universe_args_error(["AAPL", "MSFT", _PASTE], None)
    assert err is not None and _PASTE in err


def test_universe_id_alone_is_fine():
    assert universe_args_error([], "growth_40_v1") is None


def test_positional_and_universe_id_together_error():
    err = universe_args_error(["AAPL"], "growth_40_v1")
    assert err is not None and "mutually exclusive" in err


# --------------------------------------------------------------------------- #
# The CLIs reject the paste-slip at the argparse layer (SystemExit, before network)
# --------------------------------------------------------------------------- #
def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_snapshot_cli_rejects_pasted_path(monkeypatch, capsys):
    mod = _load("_snap_cli", "examples/snapshot_consensus.py")
    monkeypatch.setattr(sys, "argv",
                        ["snap", "AAPL", _PASTE, "--rank-strategy", "magic_formula_v1"])
    with pytest.raises(SystemExit):
        mod.main()
    assert _PASTE in capsys.readouterr().err


def test_snapshot_cli_rejects_positional_and_universe_id_together(monkeypatch, capsys):
    mod = _load("_snap_cli2", "examples/snapshot_consensus.py")
    monkeypatch.setattr(sys, "argv",
                        ["snap", "AAPL", "--universe-id", "growth_40_v1",
                         "--rank-strategy", "magic_formula_v1"])
    with pytest.raises(SystemExit):
        mod.main()
    assert "mutually exclusive" in capsys.readouterr().err


def test_company_check_cli_rejects_pasted_path(monkeypatch, capsys):
    mod = _load("_cc_cli", "examples/company_check.py")
    monkeypatch.setattr(sys, "argv",
                        ["cc", _PASTE, "--strategy", "magic_formula_momentum_v1"])
    with pytest.raises(SystemExit):
        mod.main()
    assert _PASTE in capsys.readouterr().err
