"""Tests for the run_council.py CLI strategy selection.

The module guards its run under ``if __name__ == "__main__"``, so importing it
exercises only the (pure) argument parsing + strategy resolution — no council
run, no network, no API key.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from aristos_council.strategy.loader import load_strategy

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES_DIR = ROOT / "strategies"


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
