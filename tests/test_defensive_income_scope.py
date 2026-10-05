"""DEFINC-FLOOR-1 + DEFINC-BANKS-1 (Batch 19A, A9).

Floor: Defensive Income printed "Company size at least $5.0bn" (its screen rule) AND "Company size:
at least $1.0bn (applied by the ranker)". Both exist in the published files; the screen is a
prefilter and is the stricter, so $5bn is the floor that binds and the ranker's $1bn never decides
anything. It is now stated once.

Banks: the lens fails a name on "total debt no bigger than market value" and JPM was failed on it
while the bank page calls debt not meaningful for a bank. It now leaves financials out, as Cyclical
Income does.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from aristos_council.data.adapter import (
    Fundamentals, MarketDataAdapter, PriceBar, PriceHistory)
from aristos_council.pipeline import (
    _rank_stage, load_screen_from_id, rules_applied, screen_with_floor_override)
from aristos_council.strategy.rank_loader import load_rank_strategy

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"
DEFINC = load_rank_strategy(STRAT_DIR / "conservative_plus_v1.yaml")
CYCLICAL = load_rank_strategy(STRAT_DIR / "cyclical_income_v1.yaml")


class _Res:
    def __init__(self, rank, screen, prefilter=True):
        self.rank_strategy, self.screen_strategy = rank, screen
        self.screen_outcomes, self.screen_bases = {}, {}
        self.meta = {"prefilter_screen": prefilter}
        self.ranked = []


def _size_lines(block):
    return [ln for ln in block.ranker_lines if ln.startswith("Company size")]


def test_defensive_income_states_its_size_floor_once():
    screen = load_screen_from_id(DEFINC.council_screen_strategy, STRAT_DIR)
    block = rules_applied(_Res(DEFINC, screen))
    rows = [r for r in block.rules if r.criterion == "min_market_cap"]
    assert [r.threshold_phrase for r in rows] == ["at least $5.0bn"]      # the binding floor
    assert _size_lines(block) == []                                       # no second floor


def test_a_floor_override_still_prints_once_at_the_run_floor():
    screen = load_screen_from_id(DEFINC.council_screen_strategy, STRAT_DIR)
    effective, _ = screen_with_floor_override(screen, 1.0e9)
    rank = DEFINC.model_copy(update={"min_market_cap": 1.0e9})
    block = rules_applied(_Res(rank, effective))
    assert [r.threshold_phrase for r in block.rules if r.criterion == "min_market_cap"] \
        == ["at least $1.0bn"]
    assert _size_lines(block) == []


def test_a_ranker_floor_ABOVE_the_screens_is_still_printed():
    """The ranker's floor is only dropped when the screen's covers it."""
    screen = load_screen_from_id(DEFINC.council_screen_strategy, STRAT_DIR)
    rank = DEFINC.model_copy(update={"min_market_cap": 9.0e9})
    assert len(_size_lines(rules_applied(_Res(rank, screen)))) == 1


def test_a_non_prefilter_lens_keeps_the_ranker_floor_line():
    """Where the screen is commentary only, the ranker's floor is the one that binds."""
    screen = load_screen_from_id(DEFINC.council_screen_strategy, STRAT_DIR)
    assert len(_size_lines(rules_applied(_Res(DEFINC, screen, prefilter=False)))) == 1


def test_defensive_income_now_excludes_financials_like_cyclical_income():
    assert DEFINC.exclude_sectors == CYCLICAL.exclude_sectors
    assert "bank or an insurer" in DEFINC.sector_exclusion_rationale
    assert DEFINC.asks.endswith("(not banks or insurers).")


class _Bank(MarketDataAdapter):
    name = "fake"

    def get_fundamentals(self, t):
        return Fundamentals(
            ticker=t, market_cap=9.0e11, sector="Financial Services", quote_type="EQUITY",
            currency="USD", dividend_per_share=5.0, payout_ratio=0.3,
            dividend_streak_years=12, total_debt=1.4e12)       # debt 1.5x market value, as JPM

    def get_price_history(self, t, *, start, end):
        return PriceHistory(ticker=t, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.05 * i, adj_close=100 + 0.05 * i, volume=10)
            for i in range(260)])

    def get_dividend_history(self, t, *, start, end):
        return []


def test_a_bank_is_excluded_by_sector_not_failed_on_the_debt_rule():
    screen = load_screen_from_id(DEFINC.council_screen_strategy, STRAT_DIR)
    ranked, excluded, _, _, _ = _rank_stage(
        ["JPM"], DEFINC, _Bank(), today=date(2026, 6, 30), prefilter_criteria=screen.criteria)
    assert not ranked
    assert excluded == [("JPM", "sector excluded (Financial Services)")]
