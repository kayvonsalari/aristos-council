"""Tests for the run_council.py CLI strategy selection.

The module guards its run under ``if __name__ == "__main__"``, so importing it
exercises only the (pure) argument parsing + strategy resolution — no council
run, no network, no API key.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from aristos_council.strategy.loader import load_strategy
from aristos_council.strategy.overrides import applied_overrides, effective_strategy

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES_DIR = ROOT / "strategies"
STREAK = "min_dividend_growth_streak"


def _v1():
    return load_strategy(STRATEGIES_DIR / "dividend_aristocrats_v1.yaml")


def _run_council():
    spec = importlib.util.spec_from_file_location(
        "_run_council_cli", ROOT / "examples" / "run_council.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_growth_v1_id_resolves_and_loads_growth_strategy():
    m = _run_council()
    path = m.resolve_strategy_path("growth_v1", STRATEGIES_DIR)
    assert path == STRATEGIES_DIR / "growth_v1.yaml"
    s = load_strategy(path)
    assert s.id == "growth_v1"
    assert [c.name for c in s.criteria][0] == "min_revenue_cagr"


def test_defaults_to_dividend_when_strategy_omitted():
    m = _run_council()
    path = m.resolve_strategy_path(None, STRATEGIES_DIR)
    assert path == STRATEGIES_DIR / "dividend_aristocrats_v1.yaml"
    assert load_strategy(path).id == "dividend_aristocrats_v1"


def test_yaml_path_argument_is_used_directly():
    m = _run_council()
    p = STRATEGIES_DIR / "growth_v1.yaml"
    assert m.resolve_strategy_path(str(p), STRATEGIES_DIR) == p


def test_strategy_via_positional_or_flag():
    m = _run_council()
    # second positional
    a = m.parse_args(["MO", "growth_v1"])
    assert a.ticker == "MO"
    assert (a.strategy_opt or a.strategy) == "growth_v1"
    # --strategy flag (overrides; positional omitted)
    b = m.parse_args(["AAPL", "--strategy", "growth_v1"])
    assert b.ticker == "AAPL"
    assert (b.strategy_opt or b.strategy) == "growth_v1"
    # flag wins over positional when both given
    c = m.parse_args(["AAPL", "dividend_aristocrats_v1", "-s", "growth_v1"])
    assert (c.strategy_opt or c.strategy) == "growth_v1"


def test_ticker_defaults_to_jnj():
    m = _run_council()
    assert m.parse_args([]).ticker == "JNJ"


# --- per-run override flags (scriptable override matrix) -------------------- #
def test_parse_gating_and_threshold_flags_build_override_dict():
    m = _run_council()
    a = m.parse_args(["JNJ", "--gating", STREAK, "--threshold", f"{STREAK}=25"])
    ov = m.build_override_kwargs(a)
    assert ov["is_gating"] == {STREAK: True}
    assert ov["thresholds"] == {STREAK: 25.0}
    assert ov["partial_pass_allows_hold"] is None


def test_parse_no_gating_and_partial_pass_flags():
    m = _run_council()
    a = m.parse_args(["JNJ", "--no-gating", STREAK, "--no-partial-pass"])
    ov = m.build_override_kwargs(a)
    assert ov["is_gating"] == {STREAK: False}
    assert ov["partial_pass_allows_hold"] is False


def test_baseline_run_has_empty_overrides_and_delta():
    m = _run_council()
    ov = m.build_override_kwargs(m.parse_args(["JNJ"]))
    assert ov == {"partial_pass_allows_hold": None, "is_gating": None,
                  "thresholds": None}
    # a baseline run records an EMPTY delta -> stays a valid flip baseline
    base = _v1()
    assert applied_overrides(base, effective_strategy(base, **ov)) == {}


def test_cli_override_run_records_nonempty_delta():
    m = _run_council()
    a = m.parse_args(["JNJ", "--no-gating", STREAK, "--threshold", f"{STREAK}=25"])
    base = _v1()
    eff = effective_strategy(base, **m.build_override_kwargs(a))
    delta = applied_overrides(base, eff)
    # v1 streak gates by default, so --no-gating IS a real change; threshold too.
    assert delta == {f"criteria.{STREAK}.is_gating": False,
                     f"criteria.{STREAK}.threshold": 25.0}


def test_malformed_threshold_is_rejected_with_clear_error():
    m = _run_council()
    with pytest.raises(SystemExit):
        m.parse_args(["JNJ", "--threshold", STREAK])            # no '='
    with pytest.raises(SystemExit):
        m.parse_args(["JNJ", "--threshold", f"{STREAK}=abc"])   # non-numeric
