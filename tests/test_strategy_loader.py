"""Tests for the strategy loader and the shipped dividend_aristocrats_v1.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest

from aristos_council.strategy.loader import Strategy, load_strategy

STRATEGY_DIR = Path(__file__).resolve().parents[1] / "strategies"


def test_loads_shipped_v1():
    s = load_strategy(STRATEGY_DIR / "dividend_aristocrats_v1.yaml")
    assert isinstance(s, Strategy)
    assert s.id == "dividend_aristocrats_v1"
    assert s.version == 1
    assert s.criteria.min_dividend_growth_years == 25
    assert s.criteria.min_dividend_yield == 0.025
    assert s.policy.unverifiable_streak_is_blocking is True


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_strategy(STRATEGY_DIR / "does_not_exist.yaml")


def test_id_must_encode_version(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "id: dividend_aristocrats\n"
        "name: X\n"
        "version: 1\n"
        "criteria:\n"
        "  min_dividend_yield: 0.02\n"
        "  max_payout_ratio: 0.75\n"
        "  min_market_cap: 1\n"
        "  min_dividend_growth_years: 25\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must encode a version"):
        load_strategy(bad)


def test_out_of_range_yield_rejected(tmp_path):
    bad = tmp_path / "bad_v1.yaml"
    bad.write_text(
        "id: bad_v1\n"
        "name: X\n"
        "version: 1\n"
        "criteria:\n"
        "  min_dividend_yield: 1.5\n"  # >1.0 invalid for a decimal yield
        "  max_payout_ratio: 0.75\n"
        "  min_market_cap: 1\n"
        "  min_dividend_growth_years: 25\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception):
        load_strategy(bad)
