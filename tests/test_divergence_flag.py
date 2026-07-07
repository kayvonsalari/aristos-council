"""Diverging-exclusions flag (ITEM 2).

`price_divergence_flag(fi, screen_criteria)` annotates an excluded name whose price has
run up hard (12m momentum >= +0.30) while a FUNDAMENTAL screen criterion confirmed-fails
— the cyclical-inflection / mania shape. It never alters a verdict or an exclusion;
abstention is not a fail; the actual momentum value is carried in the note. Wired into
the rank stage so it reaches every exclusion-line render site.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from aristos_council.data.adapter import Fundamentals, MarketDataAdapter, PriceBar, PriceHistory
from aristos_council.factors import FactorInputs, price_divergence_flag
from aristos_council.pipeline import run_rank_pipeline
from aristos_council.tools.criteria.registry import CriterionSelection

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"

# A market_cap well BELOW a 10-trillion floor -> min_market_cap confirmed-FAILS
# (a fundamental criterion). Currency None -> evaluated normally (no FX abstention).
_SMALL = Fundamentals(ticker="X", market_cap=1e10)
_HUGE_CAP_FLOOR = [CriterionSelection("min_market_cap", 1e13)]

# No income series -> min_roic is NOT-EVAL (abstains), never a confirmed fail.
_NO_ROIC = Fundamentals(ticker="X", market_cap=1e10)
_ROIC_ONLY = [CriterionSelection("min_roic", 0.12)]


def _fi(fund, mom):
    return FactorInputs(ticker="X", fundamentals=fund, return_12m=mom,
                        return_6m=mom, last_close=100.0)


def test_confirmed_fail_and_high_momentum_flags():
    flag = price_divergence_flag(_fi(_SMALL, 0.35), _HUGE_CAP_FLOOR)
    assert flag is not None
    assert "price diverging" in flag and "12m" in flag and "human review" in flag


def test_low_momentum_does_not_flag():
    # Same fundamental fail, but the price hasn't run up -> no divergence.
    assert price_divergence_flag(_fi(_SMALL, 0.10), _HUGE_CAP_FLOOR) is None


def test_abstained_criterion_with_high_momentum_does_not_flag():
    # An ABSTENTION (passed is None) is not a fail (rule 3) — no fundamental fail, no flag.
    assert price_divergence_flag(_fi(_NO_ROIC, 0.40), _ROIC_ONLY) is None


def test_flag_text_carries_the_actual_momentum_value():
    assert "+35%" in price_divergence_flag(_fi(_SMALL, 0.35), _HUGE_CAP_FLOOR)
    assert "+42%" in price_divergence_flag(_fi(_SMALL, 0.423), _HUGE_CAP_FLOOR)


def test_missing_momentum_does_not_flag():
    assert price_divergence_flag(_fi(_SMALL, None), _HUGE_CAP_FLOOR) is None


# --------------------------------------------------------------------------- #
# Integration: the flag reaches the exclusion line via the rank stage
# --------------------------------------------------------------------------- #
class _ManiaAdapter(MarketDataAdapter):
    """MANIA fails the ROIC floor (a fundamental fail) but its price has run up ~+50%
    over 12m — the exact cyclical-inflection shape the flag exists for."""

    name = "fake"

    def get_fundamentals(self, ticker):
        return Fundamentals(
            ticker=ticker, name=ticker, market_cap=2e10, sector="Technology",
            ebit=[500.0], pe_ratio=15.0,
            operating_income=[500.0] * 4, tax_provision=[100.0] * 4,
            pretax_income=[480.0] * 4, invested_capital=[8000.0] * 4,   # ROIC ~4.9% < 12%
            total_revenue=[150.0, 140, 130, 120])

    def get_price_history(self, ticker, *, start, end):
        # 260 rising closes -> return_12m = closes[-1]/closes[-253]-1 well above +0.30.
        closes = [100.0 * (1 + 0.002 * i) for i in range(260)]
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=c, high=c, low=c, close=c,
                     adj_close=c, volume=10) for c in closes])

    def get_dividend_history(self, ticker, *, start, end):
        return []


def test_flag_appears_on_the_exclusion_line():
    result = run_rank_pipeline(
        ["MANIA"], "magic_formula_v1", ranker_only=True,
        strategies_dir=STRAT_DIR, adapter=_ManiaAdapter(), today=date(2026, 6, 30))
    reason = dict(result.excluded)["MANIA"]
    assert "min_roic" in reason                          # the fundamental fail, still named
    assert "[⚠ price diverging:" in reason               # decorated, not altered
    assert "12m" in reason
