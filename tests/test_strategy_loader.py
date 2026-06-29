"""Tests for the strategy loader and the shipped dividend_aristocrats_v1.yaml.

A strategy selects criteria from the registry by name + threshold; the loader
validates those selections up front (unknown name / out-of-range / empty).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aristos_council.strategy.loader import Strategy, load_strategy

STRATEGY_DIR = Path(__file__).resolve().parents[1] / "strategies"

_VALID_CRITERIA = (
    "criteria:\n"
    "  - name: min_dividend_yield\n"
    "    threshold: 0.025\n"
)


def test_loads_shipped_v1():
    s = load_strategy(STRATEGY_DIR / "dividend_aristocrats_v1.yaml")
    assert isinstance(s, Strategy)
    assert s.id == "dividend_aristocrats_v1"
    assert s.version == 1
    # criteria are an ordered list selected by registry name
    assert [c.name for c in s.criteria] == [
        "min_dividend_yield", "max_payout_ratio",
        "min_market_cap", "min_dividend_growth_streak",
    ]
    by = {c.name: c for c in s.criteria}
    assert by["min_dividend_yield"].threshold == 0.025
    assert by["min_market_cap"].threshold == 10_000_000_000
    assert by["min_dividend_growth_streak"].threshold == 20   # lowered 25->20 (EODHD migration)
    assert by["min_dividend_growth_streak"].unverifiable_blocks is True
    assert s.policy.partial_pass_allows_hold is True
    assert s.veto.min_confidence == 0.6


def test_loads_growth_v1():
    s = load_strategy(STRATEGY_DIR / "growth_v1.yaml")
    assert s.id == "growth_v1"
    assert [c.name for c in s.criteria] == [
        "min_revenue_cagr", "min_roic", "max_peg_ratio", "min_market_cap",
        "min_price_momentum",
    ]
    by = {c.name: c.threshold for c in s.criteria}
    assert by["min_revenue_cagr"] == 0.10
    assert by["min_roic"] == 0.12
    assert by["max_peg_ratio"] == 2.0
    assert by["min_market_cap"] == 5_000_000_000
    assert by["min_price_momentum"] == 0.0
    assert s.policy.partial_pass_allows_hold is True


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_strategy(STRATEGY_DIR / "does_not_exist.yaml")


def test_id_must_encode_version(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "id: dividend_aristocrats\n"
        "name: X\n"
        "version: 1\n" + _VALID_CRITERIA,
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must encode a version"):
        load_strategy(bad)


def test_out_of_range_threshold_rejected(tmp_path):
    bad = tmp_path / "bad_v1.yaml"
    bad.write_text(
        "id: bad_v1\nname: X\nversion: 1\n"
        "criteria:\n"
        "  - name: min_dividend_yield\n"
        "    threshold: 1.5\n",          # >1.0 invalid for a decimal yield
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="out of range"):
        load_strategy(bad)


def test_unknown_criterion_rejected(tmp_path):
    bad = tmp_path / "bad_v1.yaml"
    bad.write_text(
        "id: bad_v1\nname: X\nversion: 1\n"
        "criteria:\n"
        "  - name: ebitda_coverage\n"    # not in the registry
        "    threshold: 3.0\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="unknown criterion"):
        load_strategy(bad)


def test_empty_criteria_rejected(tmp_path):
    bad = tmp_path / "bad_v1.yaml"
    bad.write_text(
        "id: bad_v1\nname: X\nversion: 1\ncriteria: []\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception):
        load_strategy(bad)
