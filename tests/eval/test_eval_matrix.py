"""Tier 1 deterministic eval matrix — the override-matrix guarantees, frozen.

Run with -s to print the guarantee matrix as a table (living documentation):
    python -m pytest tests/eval -s

Each scenario is a named case over a hand-built ScreenResult / Decision; the
asserted output is the deterministic disposition or veto behaviour. No LLM, no
network — the same matrix that was once exercised by live councils, now free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from aristos_council.agents.disposition import (
    _RANK,
    disposition_ceiling,
    exceeds_ceiling,
    failed_gating_criteria,
    insufficient_evidence,
    not_evaluated_gating_criteria,
)
from aristos_council.agents.veto import make_veto_node
from aristos_council.state import (
    Decision,
    Recommendation,
    ResearchState,
    SpecialistName,
    SpecialistOpinion,
    Stance,
    ToolCall,
    VetoTrigger,
)
from aristos_council.strategy.loader import load_strategy
from aristos_council.tools.screening import CriterionResult, ScreenResult

STREAK = "min_dividend_growth_streak"
YIELD = "min_dividend_yield"
PAYOUT = "max_payout_ratio"
MCAP = "min_market_cap"

# v1: streak gates; min_confidence 0.6 (drives the veto thresholds).
STRATEGY = load_strategy(
    Path(__file__).resolve().parents[2]
    / "strategies" / "dividend_aristocrats_v1.yaml")


# --------------------------------------------------------------------------- #
# Tiny fixture builders — construct frozen ScreenResult/CriterionResult by hand.
# --------------------------------------------------------------------------- #
def crit(name: str, passed: bool | None, *, obs=None, thr=None, note="") -> CriterionResult:
    return CriterionResult(name=name, passed=passed, observed=obs,
                           threshold=thr, note=note)


def screen(*crits: CriterionResult, ticker="EVAL") -> ScreenResult:
    flags = [f"unverifiable:{c.name}:{c.note or 'not evaluated'}"
             for c in crits if c.passed is None]
    return ScreenResult(ticker=ticker, criteria=list(crits), flags=flags)


# --------------------------------------------------------------------------- #
# DISPOSITION cases (EVAL-01..05) — pure (screen_criteria, gating) -> outcome.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DispCase:
    name: str
    desc: str
    crits: tuple
    gating: frozenset
    expect_ceiling: object              # Recommendation | None
    expect_insufficient: bool
    expect_failed: tuple = ()
    expect_not_eval: tuple = ()


DISP_CASES = [
    DispCase(
        "EVAL-01", "clean pass (KO-like)",
        (crit(YIELD, True), crit(PAYOUT, True), crit(MCAP, True), crit(STREAK, True)),
        frozenset({STREAK}),
        expect_ceiling=None, expect_insufficient=False),
    DispCase(
        "EVAL-02", "confirmed streak FAIL -> SELL (MMM-like)",
        (crit(YIELD, True), crit(STREAK, False)),
        frozenset({STREAK}),
        expect_ceiling=Recommendation.SELL, expect_insufficient=False,
        expect_failed=(STREAK,)),
    DispCase(
        "EVAL-03", "NOT-EVAL gating streak -> INSUFFICIENT_EVIDENCE (NESN@thr25)",
        (crit(YIELD, True), crit(STREAK, None)),
        frozenset({STREAK}),
        expect_ceiling=None, expect_insufficient=True,
        expect_not_eval=(STREAK,)),
    DispCase(
        "EVAL-04", "confirmed-fail BEATS not-eval (precedence)",
        (crit(YIELD, False), crit(STREAK, None)),
        frozenset({YIELD, STREAK}),
        # disposition_ceiling SELL (confirmed fail) AND insufficient True both hold;
        # the DECISION NODE resolves this as SELL — confirmed-fail precedence over
        # INSUFFICIENT_EVIDENCE (agents/nodes.py decide(): ceiling checked first).
        expect_ceiling=Recommendation.SELL, expect_insufficient=True,
        expect_failed=(YIELD,), expect_not_eval=(STREAK,)),
    DispCase(
        "EVAL-05", "NON-gating NOT-EVAL trips nothing",
        (crit(YIELD, None), crit(STREAK, True)),
        frozenset({STREAK}),
        expect_ceiling=None, expect_insufficient=False),
]


@pytest.mark.parametrize("case", DISP_CASES, ids=[c.name for c in DISP_CASES])
def test_disposition_guarantee(case: DispCase):
    crits = list(case.crits)
    assert disposition_ceiling(crits, case.gating) == case.expect_ceiling
    assert insufficient_evidence(crits, case.gating) is case.expect_insufficient
    assert tuple(failed_gating_criteria(crits, case.gating)) == case.expect_failed
    assert tuple(not_evaluated_gating_criteria(crits, case.gating)) == case.expect_not_eval


def test_eval_02_exceeds_ceiling_caps_buy_not_sell():
    # A BUY above the SELL cap must be capped; a SELL already at the cap stands.
    assert exceeds_ceiling(Recommendation.BUY, Recommendation.SELL) is True
    assert exceeds_ceiling(Recommendation.HOLD, Recommendation.SELL) is True
    assert exceeds_ceiling(Recommendation.SELL, Recommendation.SELL) is False


def test_eval_06_off_ladder_guard():
    # INSUFFICIENT_EVIDENCE is off _RANK and can never be ranked / mis-ordered.
    assert Recommendation.INSUFFICIENT_EVIDENCE not in _RANK
    with pytest.raises(ValueError):
        exceeds_ceiling(Recommendation.INSUFFICIENT_EVIDENCE, Recommendation.SELL)
    with pytest.raises(ValueError):
        exceeds_ceiling(Recommendation.BUY, Recommendation.INSUFFICIENT_EVIDENCE)


# --------------------------------------------------------------------------- #
# VETO cases (EVAL-07..10) — build a ResearchState, run make_veto_node.
# --------------------------------------------------------------------------- #
def _dec(rec, conf=0.9, **kw) -> Decision:
    return Decision(recommendation=rec, confidence=conf, rationale="r", **kw)


def _capped(original, conf=0.9) -> Decision:
    return _dec(Recommendation.SELL, conf, original_recommendation=original,
               gate_override_applied=True, gating_criterion_fired=STREAK)


_INSUFFICIENT = _dec(
    Recommendation.INSUFFICIENT_EVIDENCE, 0.9,
    original_recommendation=Recommendation.BUY, gate_override_applied=True,
    insufficient_evidence=True, gating_criterion_fired=STREAK)


def run_veto(decision, *, errors=(), abstain=(), screen_flags=()):
    s = ResearchState(ticker="EVAL", strategy_id=STRATEGY.id)
    s.decision = decision
    s.errors = list(errors)
    for who in abstain:
        s.specialist_opinions.append(SpecialistOpinion(
            specialist=who, stance=Stance.ABSTAIN, confidence=0.0, thesis="t"))
    if screen_flags:
        s.tool_calls.append(ToolCall(
            call_id="s", tool_name="run_strategy_screen", ok=True,
            output={"criteria": [], "flags": list(screen_flags)}))
    make_veto_node(STRATEGY)(s)
    return {f.trigger for f in s.veto_flags}, s.requires_human_review


@dataclass(frozen=True)
class VetoCase:
    name: str
    desc: str
    decision: Decision
    present: tuple = ()                 # triggers that MUST fire
    absent: tuple = ()                  # triggers that must NOT fire
    review: object = None               # expected requires_human_review (bool|None)
    errors: tuple = ()
    abstain: tuple = ()
    screen_flags: tuple = ()


VETO_CASES = [
    VetoCase(
        "EVAL-07a", "data_quality MINOR: lone abstention -> silent",
        _dec(Recommendation.HOLD), absent=(VetoTrigger.DATA_QUALITY,), review=False,
        abstain=(SpecialistName.SENTIMENT,)),
    VetoCase(
        "EVAL-07b", "data_quality MINOR: optional sentiment 403 -> silent",
        _dec(Recommendation.HOLD), absent=(VetoTrigger.DATA_QUALITY,), review=False,
        errors=("get_company_news: Finnhub /company-news HTTP 403",)),
    VetoCase(
        "EVAL-08a", "data_quality MATERIAL: adapter error -> fires",
        _dec(Recommendation.HOLD), present=(VetoTrigger.DATA_QUALITY,), review=True,
        errors=("get_fundamentals: provider timeout",)),
    VetoCase(
        "EVAL-08b", "data_quality MATERIAL: 2+ NOT-EVAL criteria -> fires",
        _dec(Recommendation.HOLD), present=(VetoTrigger.DATA_QUALITY,), review=True,
        screen_flags=("unverifiable:min_dividend_yield:no last_close",
                      "unverifiable:max_payout_ratio:no eps")),
    VetoCase(
        "EVAL-09a", "gate-cap MATERIAL: BUY -> SELL escalates",
        _capped(Recommendation.BUY),
        present=(VetoTrigger.GATE_OVERRIDE_MATERIAL,), review=True),
    VetoCase(
        "EVAL-09b", "gate-cap routine: HOLD -> SELL does NOT escalate",
        _capped(Recommendation.HOLD),
        absent=(VetoTrigger.GATE_OVERRIDE_MATERIAL,), review=False),
    VetoCase(
        "EVAL-10", "INSUFFICIENT_EVIDENCE -> human review ALWAYS",
        _INSUFFICIENT, present=(VetoTrigger.INSUFFICIENT_EVIDENCE,), review=True),
]


@pytest.mark.parametrize("case", VETO_CASES, ids=[c.name for c in VETO_CASES])
def test_veto_guarantee(case: VetoCase):
    fired, review = run_veto(case.decision, errors=case.errors,
                             abstain=case.abstain, screen_flags=case.screen_flags)
    for t in case.present:
        assert t in fired, f"{case.name}: expected {t.value} to fire; got {fired}"
    for t in case.absent:
        assert t not in fired, f"{case.name}: {t.value} fired but should be silent; got {fired}"
    if case.review is not None:
        assert review is case.review, f"{case.name}: requires_human_review"


# --------------------------------------------------------------------------- #
# Living documentation: print the matrix (visible with -s); also a global check.
# --------------------------------------------------------------------------- #
def test_eval_matrix_summary():
    rows: list[str] = []
    rows.append("")
    rows.append("ARISTOS COUNCIL — TIER 1 EVAL MATRIX (deterministic process guarantees)")
    rows.append("=" * 78)
    rows.append("DISPOSITION GATE")
    rows.append(f"  {'ID':<9} {'scenario':<46} {'ceiling':<8} {'insuff':<7} {'':<4}")
    for c in DISP_CASES:
        cs = list(c.crits)
        ceil = disposition_ceiling(cs, c.gating)
        ins = insufficient_evidence(cs, c.gating)
        ok = (ceil == c.expect_ceiling and ins is c.expect_insufficient
              and tuple(failed_gating_criteria(cs, c.gating)) == c.expect_failed
              and tuple(not_evaluated_gating_criteria(cs, c.gating)) == c.expect_not_eval)
        rows.append(f"  {c.name:<9} {c.desc:<46} "
                    f"{(ceil.value if ceil else '—'):<8} {str(ins):<7} "
                    f"{'PASS' if ok else 'FAIL'}")
        assert ok, c.name
    rows.append("")
    rows.append("VETO GATE")
    rows.append(f"  {'ID':<9} {'scenario':<52} {'':<4}")
    for c in VETO_CASES:
        fired, review = run_veto(c.decision, errors=c.errors,
                                 abstain=c.abstain, screen_flags=c.screen_flags)
        ok = (all(t in fired for t in c.present)
              and all(t not in fired for t in c.absent)
              and (c.review is None or review is c.review))
        rows.append(f"  {c.name:<9} {c.desc:<52} {'PASS' if ok else 'FAIL'}")
        assert ok, c.name
    rows.append("=" * 78)
    print("\n".join(rows))
