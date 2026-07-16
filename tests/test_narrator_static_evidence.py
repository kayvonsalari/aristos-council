"""Static-layer factor values must reach the narrator's evidence ledger (NARR-STATIC-1).

Finding (2026-07-15 dividend-ETF narrator run): the ranker consumed static-layer values
(ETF-STATIC-1: expense_ratio / fund_size / distribution_yield served from
``data/etf_static.csv`` with provenance tags), but the narrator's evidence ledger did NOT
receive them — both narratives reported the yield/fee raw values as "not present anywhere
in the ledger" and could not audit the ranks. The writer flew blind on the lens's two
defining numbers and honestly said so.

Fix (plumbing, not the writer): when a factor value was SERVED FROM THE STATIC LAYER, the
narrator/council evidence assembly includes (a) the raw value, (b) its provenance tag
verbatim (``static: <as_of>, <source>``), (c) the as_of date (it rides inside the tag) —
exactly as the report's factor-integrity block discloses it. Vendor-computed values flow
as before; abstained (incl. stale-withheld) fields stay abstentions and are never filled;
a stock lens (no static-sourced factor) leaves the ledger byte-unchanged.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from aristos_council.agents.nodes import (
    _STATIC_LAYER_LEDGER_TOOL, _evidence_block, make_gather_node)
from aristos_council.agents.schemas import (
    CriticOutput, DecisionOutput, SpecialistOutput)
from aristos_council.data.adapter import (
    Fundamentals, MarketDataAdapter, PriceBar, PriceHistory)
from aristos_council.etf_static import STALE_NOTE
from aristos_council.pipeline import (
    _council_stage, _screenless_frame, _static_factor_evidence,
    load_rank_strategy_from_id)
from aristos_council.rank_engine import RankedTicker
from aristos_council.state import Recommendation, ResearchState, Stance
from aristos_council.strategy.loader import Strategy

ROOT = Path(__file__).resolve().parents[1]
STRAT_DIR = ROOT / "strategies"

# The verbatim provenance receipt from the finding's committed record.
_TAG = "static: 2026-07-15, EODHD fundamentals API"


class _EtfAdapter(MarketDataAdapter):
    """An ETF-kind name; the fundamentals the vendor DOES serve (the static fields are
    filled by the ranker upstream, not here — the narrator gather never re-derives them)."""

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


def _frame() -> Strategy:
    """The screen-less council frame for the dividend-ETF rank strategy (no criteria)."""
    return _screenless_frame(load_rank_strategy_from_id("etf_dividend_v1", STRAT_DIR))


def _ranked(sources: dict[str, str], values: dict) -> RankedTicker:
    ranks = {n: float(i + 1) for i, n in enumerate(sources)}
    return RankedTicker(
        ticker="SCHD", factor_ranks=ranks, factor_values=values,
        combined_rank=sum(ranks.values()), universe_size=3, verdict="buy",
        rank_position=1, factor_sources=sources)


# --------------------------------------------------------------------------- #
# _static_factor_evidence — selects ONLY the static-sourced factors
# --------------------------------------------------------------------------- #
def test_static_factor_evidence_selects_only_static_sourced_factors():
    r = _ranked(
        sources={"distribution_yield": _TAG,          # served from static
                 "expense_ratio": "computed",          # vendor-computed
                 "fund_size": "abstained",             # abstained (no value)
                 "momentum_12m": STALE_NOTE},          # static entry stale -> withheld
        values={"distribution_yield": 0.035, "expense_ratio": 0.06,
                "fund_size": None, "momentum_12m": None})

    ev = _static_factor_evidence(r)

    # ONLY the static-served factor, with its raw value + verbatim provenance receipt.
    assert ev == [{"factor": "distribution_yield", "value": 0.035, "provenance": _TAG}]
    # vendor-computed, abstained, and stale-withheld are OMITTED (never a phantom fill).
    named = {e["factor"] for e in ev}
    assert "expense_ratio" not in named
    assert "fund_size" not in named
    assert "momentum_12m" not in named


def test_static_factor_evidence_empty_for_a_stock_lens():
    # A stock run's factors are all vendor-computed / abstained — nothing from static.
    r = _ranked(sources={"earnings_yield": "ev", "roic": "computed",
                         "net_payout_yield": "abstained"},
                values={"earnings_yield": 0.08, "roic": 0.2, "net_payout_yield": None})
    assert _static_factor_evidence(r) == []


# --------------------------------------------------------------------------- #
# gather -> the static-sourced value + tag land in the evidence ledger
# --------------------------------------------------------------------------- #
def test_static_sourced_factor_reaches_the_narrator_evidence_ledger():
    frame = _frame()
    gather = make_gather_node(_EtfAdapter(), frame)
    state = ResearchState(
        ticker="SCHD", strategy_id=frame.id,
        static_factor_evidence=[{"factor": "distribution_yield", "value": 0.035,
                                 "provenance": _TAG}])
    out = gather(state)

    # (1) the ledger carries a static_layer tool call with the value + verbatim tag.
    tc = next(t for t in out.tool_calls if t.tool_name == _STATIC_LAYER_LEDGER_TOOL)
    assert tc.output["factors"] == [
        {"factor": "distribution_yield", "value": 0.035, "provenance": _TAG}]

    # (2) the agent-facing evidence block (what the narrator reads) renders the raw
    # value AND the verbatim provenance receipt (as_of rides inside the tag).
    block = _evidence_block(out, frame)
    assert '"tool": "static_layer"' in block
    assert "0.035" in block
    assert _TAG in block                                   # provenance tag verbatim
    assert "2026-07-15" in block                           # the as_of date


# --------------------------------------------------------------------------- #
# stock lens / no static-sourced factor -> ledger byte-unchanged
# --------------------------------------------------------------------------- #
def _strip_ids(block: str) -> str:
    # call_id is a random hex per ToolCall; strip it so two runs compare by content.
    return re.sub(r'"call_id": "[0-9a-f]+"', '"call_id": "X"', block)


def test_stock_lens_evidence_is_byte_unchanged():
    frame = _frame()
    gather = make_gather_node(_EtfAdapter(), frame)

    # A run with NO static-sourced factor (a stock lens, or an ETF the static layer did
    # not touch): the plumbing emits nothing extra.
    plain = gather(ResearchState(ticker="SCHD", strategy_id=frame.id))
    assert all(t.tool_name != _STATIC_LAYER_LEDGER_TOOL for t in plain.tool_calls)
    plain_block = _strip_ids(_evidence_block(plain, frame))
    assert "static_layer" not in plain_block

    # A run WITH a static-sourced factor differs ONLY by the added static_layer line —
    # every other ledger entry (fundamentals, prices, technical) is byte-identical.
    with_static = gather(ResearchState(
        ticker="SCHD", strategy_id=frame.id,
        static_factor_evidence=[{"factor": "distribution_yield", "value": 0.035,
                                 "provenance": _TAG}]))
    static_lines = _strip_ids(_evidence_block(with_static, frame)).splitlines()
    assert [ln for ln in static_lines if "static_layer" not in ln] == \
        plain_block.splitlines()


# --------------------------------------------------------------------------- #
# End-to-end: the mocked ETF narrator run carries the value + tag to the writer
# --------------------------------------------------------------------------- #
class _SpecialistRunner:
    def invoke(self, system, user):
        return SpecialistOutput(stance=Stance.ABSTAIN, confidence=0.0, thesis="n/a")


class _CapturingDecision:
    """Captures the user message the narrator (Decision agent) actually receives."""

    def __init__(self):
        self.user = ""

    def invoke(self, system, user):
        self.user = user
        return DecisionOutput(recommendation=Recommendation.BUY, confidence=0.8,
                              rationale="ranked #1 on distribution yield.")


def test_narrator_sees_static_value_and_tag_end_to_end():
    frame = _frame()
    decision = _CapturingDecision()
    runners = {"specialist": _SpecialistRunner(),
               "critic": _Fixed(CriticOutput(counter_thesis="c")),
               "decision": decision}
    r = _ranked(
        sources={"distribution_yield": _TAG, "expense_ratio": "computed",
                 "fund_size": "abstained", "momentum_12m": "computed"},
        values={"distribution_yield": 0.035, "expense_ratio": 0.06,
                "fund_size": None, "momentum_12m": 0.12})

    _council_stage([r], frame, _EtfAdapter(), runners, "narrator")

    # The static-served value + verbatim provenance receipt are in the narrator's prompt.
    assert "0.035" in decision.user
    assert _TAG in decision.user
    # The static_layer ledger entry names ONLY the static-served factor — the vendor-
    # computed (expense_ratio, momentum_12m) and abstained (fund_size) factors are not
    # injected there.
    static_line = next(ln for ln in decision.user.splitlines()
                       if '"tool": "static_layer"' in ln)
    assert "distribution_yield" in static_line
    for other in ("expense_ratio", "fund_size", "momentum_12m"):
        assert other not in static_line


class _Fixed:
    def __init__(self, out):
        self._out = out

    def invoke(self, system, user):
        return self._out
