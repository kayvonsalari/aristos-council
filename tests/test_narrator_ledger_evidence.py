"""NARR-LEDGER-1 (absorbs NARR-STATIC-2) — absolute values + provenance into the
narrator's evidence ledger.

Two halves of the same gap:
  - Narration prose repeatedly said "worth checking the actual fee/AUM" for numbers the
    pipeline already held — the evidence pack lacked the ABSOLUTE factor values.
  - NARR-STATIC-1 plumbed the fee/size/yield RANK into the narrator but only the raw
    NUMBER for the STATIC-served subset (``src.startswith("static:")``). Since the static
    layer is a FALLBACK — the vendor value wins when present and plausible
    (``etf_static.apply_static_fill``) — most real names have these factors tagged
    ``computed``, not ``static:...``, so the core narration honestly said it couldn't audit
    a 0.07% fee it never actually saw.

Fix: ``pipeline._static_factor_evidence`` now surfaces the fee/size/yield triad from ANY
source with a real value, each with its OWN actual provenance tag (verbatim, currency
receipt included when one applies — see ``factors._with_fx_receipt``). This file covers
the broadened fixture and the end-to-end citation; the original static-only plumbing
mechanics stay pinned in ``test_narrator_static_evidence.py``.
"""

from __future__ import annotations

from aristos_council.agents.nodes import _evidence_block, make_gather_node
from aristos_council.agents.schemas import CriticOutput, DecisionOutput, SpecialistOutput
from aristos_council.data.adapter import Fundamentals, MarketDataAdapter, PriceBar, PriceHistory
from aristos_council.factors import _with_fx_receipt
from aristos_council.pipeline import (
    _council_stage, _screenless_frame, _static_factor_evidence,
    load_rank_strategy_from_id)
from aristos_council.rank_engine import RankedTicker
from aristos_council.state import Recommendation, ResearchState, Stance
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRAT_DIR = ROOT / "strategies"

_STATIC_TAG = "static: 2026-08-04, EODHD fundamentals API"
_FUND_SIZE_TAG = _with_fx_receipt("computed", "4.17bn USD @ 0.86 EUR/USD, 2026-08-04")


def _frame():
    return _screenless_frame(load_rank_strategy_from_id("etf_dividend_v1", STRAT_DIR))


def _ranked(sources: dict[str, str], values: dict) -> RankedTicker:
    ranks = {n: float(i + 1) for i, n in enumerate(sources)}
    return RankedTicker(
        ticker="EUDF", factor_ranks=ranks, factor_values=values,
        combined_rank=sum(ranks.values()), universe_size=5, verdict="buy",
        rank_position=1, factor_sources=sources)


# --------------------------------------------------------------------------- #
# The ledger carries the raw value + provenance tag (currency embedded in the tag
# where it applies) for EACH of the three absolute-value factors, mixed sources.
# --------------------------------------------------------------------------- #
def test_ledger_carries_value_and_provenance_for_each_absolute_factor():
    r = _ranked(
        sources={"expense_ratio": "computed",           # vendor-computed
                 "fund_size": _FUND_SIZE_TAG,            # computed + FX receipt
                 "distribution_yield": _STATIC_TAG},     # served from static
        values={"expense_ratio": 0.29, "fund_size": 4.85e9, "distribution_yield": 0.021})

    ev = _static_factor_evidence(r)
    by_name = {e["factor"]: e for e in ev}

    assert by_name["expense_ratio"] == {
        "factor": "expense_ratio", "value": 0.29, "provenance": "computed"}
    assert by_name["fund_size"] == {
        "factor": "fund_size", "value": 4.85e9, "provenance": _FUND_SIZE_TAG}
    assert "USD @ 0.86 EUR/USD" in by_name["fund_size"]["provenance"]   # currency + FX rate
    assert by_name["distribution_yield"] == {
        "factor": "distribution_yield", "value": 0.021, "provenance": _STATIC_TAG}


# --------------------------------------------------------------------------- #
# End-to-end: the narrator's evidence block carries the concrete fee number and its
# provenance, so a narration citing it is well-founded (no more "couldn't audit").
# --------------------------------------------------------------------------- #
class _EtfAdapter(MarketDataAdapter):
    name = "fake"

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, name=f"{ticker} Fund", quote_type="ETF",
                            market_cap=2e10)

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=10)
            for i in range(220)])

    def get_dividend_history(self, ticker, *, start, end):
        return []


class _SpecialistRunner:
    def invoke(self, system, user):
        return SpecialistOutput(stance=Stance.ABSTAIN, confidence=0.0, thesis="n/a")


class _Fixed:
    def __init__(self, out):
        self._out = out

    def invoke(self, system, user):
        return self._out


class _CapturingDecision:
    def __init__(self):
        self.user = ""
        self.rationale = ""

    def invoke(self, system, user):
        self.user = user
        # A narration that CITES the concrete fee number instead of disclaiming it —
        # only honest if the evidence pack actually carries "0.29" + its provenance.
        self.rationale = "EUDF's 0.29% expense ratio (computed) ranks mid-pack on cost."
        return DecisionOutput(
            recommendation=Recommendation.BUY, confidence=0.7,
            rationale=self.rationale)


def test_narrator_cites_the_computed_fee_number_instead_of_disclaiming_it():
    frame = _frame()
    decision = _CapturingDecision()
    runners = {"specialist": _SpecialistRunner(),
               "critic": _Fixed(CriticOutput(counter_thesis="c")),
               "decision": decision}
    r = _ranked(
        sources={"expense_ratio": "computed", "distribution_yield": _STATIC_TAG},
        values={"expense_ratio": 0.29, "distribution_yield": 0.021})

    _council_stage([r], frame, _EtfAdapter(), runners, "narrator")

    # the concrete number and its actual provenance reached the narrator's prompt —
    # the writer had a real figure to audit, not an absence to disclaim.
    assert "0.29" in decision.user
    assert '"provenance": "computed"' in decision.user
    # and the resulting narration honestly cites it rather than deferring.
    assert "0.29%" in decision.rationale
    assert "worth checking the actual" not in decision.rationale.lower()
    assert "couldn't audit" not in decision.rationale.lower()


def test_gather_evidence_block_shows_computed_and_static_side_by_side():
    frame = _frame()
    gather = make_gather_node(_EtfAdapter(), frame)
    state = ResearchState(
        ticker="EUDF", strategy_id=frame.id,
        static_factor_evidence=[
            {"factor": "expense_ratio", "value": 0.29, "provenance": "computed"},
            {"factor": "distribution_yield", "value": 0.021, "provenance": _STATIC_TAG}])
    out = gather(state)
    block = _evidence_block(out, frame, narrator=True)

    assert "0.29" in block and '"provenance": "computed"' in block
    assert "0.021" in block and _STATIC_TAG in block
