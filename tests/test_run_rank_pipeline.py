"""``run_rank_pipeline`` — the ONE entry the CLI and the Universe Run tab share.

Deterministic (fake adapter + fake runners, no network/LLM): the structured result
carries exactly what the CLI prints, and ``format_cli_report`` renders it — so the UI
and CLI show the same thing. UNRATEABLE is split from Excluded; ranker-only spends no
council; narrator narrates the shortlist.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from aristos_council.agents.schemas import CriticOutput, DecisionOutput, SpecialistOutput
from aristos_council.data.adapter import (
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
)
from aristos_council.pipeline import format_cli_report, run_rank_pipeline
from aristos_council.state import Recommendation, Stance

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"

# A clear ranking from fundamentals alone (magic_formula_v1 is fundamentals-only):
# A best earnings-yield + ROIC, then B; C's through-cycle ROIC (~7.7%) fails the
# magic_value_screen prefilter's 12% floor, so C is EXCLUDED pre-rank.
_FUND = {
    "A": dict(market_cap=2e10, sector="Technology", ebit=[3000.0], pe_ratio=10.0,
              operating_income=[3000.0, 2800, 2600, 2400], tax_provision=[600.0, 560, 520, 480],
              pretax_income=[2900.0, 2700, 2500, 2300], invested_capital=[5000.0] * 4,
              total_revenue=[200.0, 170, 150, 120]),
    "B": dict(market_cap=2e10, sector="Technology", ebit=[1500.0], pe_ratio=20.0,
              operating_income=[1500.0, 1450, 1400, 1350], tax_provision=[300.0, 290, 280, 270],
              pretax_income=[1450.0, 1400, 1350, 1300], invested_capital=[5000.0] * 4,
              total_revenue=[150.0, 140, 130, 120]),
    "C": dict(market_cap=2e10, sector="Technology", ebit=[500.0], pe_ratio=40.0,
              operating_income=[500.0, 490, 480, 470], tax_provision=[100.0, 98, 96, 94],
              pretax_income=[480.0, 470, 460, 450], invested_capital=[5000.0] * 4,
              total_revenue=[125.0, 120, 115, 110]),
}


class _Adapter(MarketDataAdapter):
    """A/B/C are healthy; DEAD is a delisted shell (blank fundamentals, price raises)."""

    name = "fake"

    def get_fundamentals(self, ticker):
        if ticker == "DEAD":
            return Fundamentals(ticker="DEAD")                # shell — all None/empty
        return Fundamentals(ticker=ticker, name=ticker, **_FUND[ticker])

    def get_price_history(self, ticker, *, start, end):
        if ticker == "DEAD":
            raise RuntimeError("no timezone found, symbol may be delisted")
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=10)
            for i in range(220)])

    def get_dividend_history(self, ticker, *, start, end):
        return []


class _SpecialistRunner:
    def invoke(self, system, user):
        if "SENTIMENT specialist" in system:
            return SpecialistOutput(stance=Stance.ABSTAIN, confidence=0.0,
                                    thesis="no sentiment data")
        return SpecialistOutput(stance=Stance.BULLISH, confidence=0.8, thesis="up")


class _Fixed:
    def __init__(self, out):
        self._out = out

    def invoke(self, system, user):
        return self._out


def _runners(decision_out):
    return {"specialist": _SpecialistRunner(),
            "critic": _Fixed(CriticOutput(counter_thesis="c")),
            "decision": _Fixed(decision_out)}


UNIVERSE = ["A", "B", "C", "DEAD"]


# --------------------------------------------------------------------------- #
# Ranker-only — deterministic STAGE 1, no council, no key needed
# --------------------------------------------------------------------------- #
def test_ranker_only_ranks_splits_unrateable_and_spends_no_council():
    result = run_rank_pipeline(
        UNIVERSE, "magic_formula_v1", ranker_only=True,
        strategies_dir=STRAT_DIR, adapter=_Adapter(), today=date(2026, 6, 30))

    # A, B survive the quality-value prefilter; A ranks best.
    assert [r.ticker for r in result.ranked] == ["A", "B"]
    assert result.ranked[0].verdict == "buy"

    # C excluded by the prefilter (ROIC below the 12% floor), named.
    assert any(t == "C" and "min_roic" in why for t, why in result.excluded)
    # DEAD is UNRATEABLE — its OWN axis, not mixed into excluded.
    assert any(t == "DEAD" for t, _ in result.unrateable)
    assert all(t != "DEAD" for t, _ in result.excluded)

    # ranker-only: no council, no narratives.
    assert result.council == [] and result.narratives == {}
    assert result.meta["rank_strategy_id"] == "magic_formula_v1"
    assert result.meta["screen_strategy_id"] == "magic_value_screen_v1"
    assert result.meta["ranked_count"] == 2


def test_format_cli_report_reflects_the_result():
    result = run_rank_pipeline(
        UNIVERSE, "magic_formula_v1", ranker_only=True,
        strategies_dir=STRAT_DIR, adapter=_Adapter(), today=date(2026, 6, 30))
    text = format_cli_report(result)
    assert result.header in text
    assert "=== RANKED (magic_formula_v1)" in text
    for t in ("A", "B"):
        assert t in text
    assert "UNRATEABLE" in text and "DEAD" in text
    assert "min_roic" in text                                  # the excluded reason


# --------------------------------------------------------------------------- #
# Narrator — the council narrates the shortlist (BUY names)
# --------------------------------------------------------------------------- #
def test_narrator_narrates_the_shortlist():
    runners = _runners(DecisionOutput(recommendation=Recommendation.BUY,
                                      confidence=0.8,
                                      rationale="ranked #1 on ROIC + earnings yield."))
    result = run_rank_pipeline(
        UNIVERSE, "magic_formula_v1", council_mode="narrator",
        strategies_dir=STRAT_DIR, adapter=_Adapter(), runners=runners,
        today=date(2026, 6, 30))

    assert result.council_mode == "narrator"
    assert result.meta["shortlist"] == ["A"]                   # buy quintile of {A,B}
    assert "A" in result.narratives
    assert "ROIC" in result.narratives["A"]
    # header + narrative both render through the CLI formatter
    text = format_cli_report(result)
    assert "non-judging" in text and "ranked #1 on ROIC" in text


def test_mode_stamp_tells_the_truth_on_both_paths():
    # ranker-only: NO LLM ran -> the stamp must say so (ITEM 3), not leak "narrator".
    ranker = run_rank_pipeline(
        UNIVERSE, "magic_formula_v1", ranker_only=True,
        strategies_dir=STRAT_DIR, adapter=_Adapter(), today=date(2026, 6, 30))
    assert ranker.meta["council_mode"] == "ranker-only"
    assert ranker.council_mode == "ranker-only"
    assert ranker.header == \
        "Verdict: deterministic ranker.  Narrative: none (ranker-only — no LLM ran)."

    # narrator: an LLM ran -> the stamp is "narrator".
    narrator = run_rank_pipeline(
        UNIVERSE, "magic_formula_v1", council_mode="narrator",
        strategies_dir=STRAT_DIR, adapter=_Adapter(), runners=_runners(
            DecisionOutput(recommendation=Recommendation.BUY, confidence=0.8,
                           rationale="r")), today=date(2026, 6, 30))
    assert narrator.meta["council_mode"] == "narrator"
    assert narrator.header == \
        "Verdict: deterministic ranker.  Narrative: LLM (non-judging)."
