"""Dividend-growth streak (from payment history) + leverage — free-data yield-trap
separators. Deterministic: no network. Proves T/VZ/MMM separate from PG/KO/JNJ/MCD."""

from __future__ import annotations

from aristos_council.data.adapter import Fundamentals
from aristos_council.tools.criteria.registry import REGISTRY, Evidence
from aristos_council.tools.screening import dividend_streak


# --------------------------------------------------------------------------- #
# BUILD 2: dividend_streak — flat != cut, current year excluded, short -> None
# --------------------------------------------------------------------------- #
def test_streak_counts_consecutive_increases():
    annual = {2020: 1.0, 2021: 1.1, 2022: 1.2, 2023: 1.3}
    streak, cut = dividend_streak(annual, as_of_year=2024)
    assert streak == 3 and cut is None


def test_flat_year_ends_streak_but_is_not_a_cut():
    annual = {2020: 1.0, 2021: 1.10, 2022: 1.10, 2023: 1.20}   # 2022 flat
    streak, cut = dividend_streak(annual, as_of_year=2024)
    assert streak == 1                      # only 2023>2022; 2022==2021 ends it
    assert cut is None                      # flat is NOT a cut


def test_reduction_year_is_recorded_as_cut():
    # T-shape: raised, CUT in 2022, flat since -> streak 0, last cut 2022
    annual = {2019: 2.0, 2020: 2.05, 2021: 2.08, 2022: 1.11, 2023: 1.11}
    streak, cut = dividend_streak(annual, as_of_year=2024)
    assert streak == 0 and cut == 2022


def test_current_partial_year_excluded_and_short_history_none():
    # 2024 (as_of) excluded; only 2 complete years -> abstain
    assert dividend_streak({2022: 1.0, 2023: 1.1, 2024: 0.3}, as_of_year=2024) \
        == (None, None)


# --------------------------------------------------------------------------- #
# BUILD 3: criteria — streak floor + leverage (negative-equity robust)
# --------------------------------------------------------------------------- #
def _streak(streak_years):
    return REGISTRY["min_dividend_streak"].fn(
        Evidence(fundamentals=Fundamentals(ticker="X",
                                           dividend_streak_years=streak_years)), 10)


def _leverage(total_debt, market_cap):
    return REGISTRY["max_debt_to_market_cap"].fn(
        Evidence(fundamentals=Fundamentals(ticker="X", total_debt=total_debt,
                                           market_cap=market_cap)), 1.0)


def test_streak_criterion_fails_cut_history_passes_sound():
    assert _streak(0).passed is False        # T / MMM (cut -> streak 0)
    assert _streak(22).passed is True        # PG
    assert _streak(63).passed is True        # JNJ
    assert _streak(None).passed is None      # no history -> ABSTAIN (not excluded)
    assert "unavailable" in _streak(None).note


def test_leverage_criterion_fails_high_debt_passes_low():
    # VZ-shape: $201B debt vs ~$170B cap -> ~1.18 > 1.0 -> FAIL
    assert _leverage(201e9, 170e9).passed is False
    # PG-shape: low debt vs large cap -> pass
    assert _leverage(35e9, 350e9).passed is True
    assert _leverage(None, 350e9).passed is None      # missing debt -> abstain


def test_leverage_uses_market_cap_not_equity_so_negative_equity_never_excludes():
    # MCD-shape: negative book equity -> d/e UNDEFINED (None), but modest debt vs a
    # large market cap. The criterion uses debt/market_cap, so it PASSES (never
    # excluded on a None d/e).
    mcd = Fundamentals(ticker="MCD", total_debt=50e9, market_cap=200e9,
                       debt_to_equity=None)      # negative equity -> d/e undefined
    r = REGISTRY["max_debt_to_market_cap"].fn(Evidence(fundamentals=mcd), 1.0)
    assert r.passed is True                  # 0.25 <= 1.0
    assert "robust to negative-equity" in r.note


# --------------------------------------------------------------------------- #
# The milestone: T/VZ/MMM separable from sound defensives ON FREE DATA
# --------------------------------------------------------------------------- #
def test_conservative_screen_separates_traps_from_sound_on_free_data():
    from pathlib import Path

    from aristos_council.strategy.loader import load_strategy
    from aristos_council.tools.criteria.registry import run_screen

    screen = load_strategy(Path(__file__).resolve().parents[1]
                           / "strategies" / "conservative_screen_v1.yaml")

    def _fails(fund, *, last_close=100.0, ret=0.03):
        by = {c.name: c for c in run_screen(
            screen.criteria,
            Evidence(fundamentals=fund, last_close=last_close, return_12m=ret),
            ticker=fund.ticker).criteria}
        return {n for n, c in by.items() if c.passed is False}

    # T: streak 0 -> fails the streak floor
    t = Fundamentals(ticker="T", market_cap=160e9, dividend_per_share=1.11,
                     payout_ratio=0.6, dividend_streak_years=0, total_debt=160e9)
    assert "min_dividend_streak" in _fails(t)
    # VZ: long streak but over-levered -> fails leverage, NOT streak
    vz = Fundamentals(ticker="VZ", market_cap=170e9, dividend_per_share=2.71,
                      payout_ratio=0.6, dividend_streak_years=21, total_debt=201e9)
    vz_fails = _fails(vz)
    assert "max_debt_to_market_cap" in vz_fails and "min_dividend_streak" not in vz_fails
    # PG: sound -> fails nothing
    pg = Fundamentals(ticker="PG", market_cap=350e9, dividend_per_share=4.0,
                      payout_ratio=0.6, dividend_streak_years=22, total_debt=35e9)
    assert _fails(pg) == set()
