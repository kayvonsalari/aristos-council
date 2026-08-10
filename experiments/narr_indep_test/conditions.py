"""Condition specs for NARR-INDEP-TEST — forcing (Experiment A), ablation (B1), and
corruption (B2) — all applied at the narrator-INPUT boundary: a ``RankedTicker`` +
``MarketDataAdapter`` pair. No production code is touched; every manipulation is a plain
Python object built here and handed to ``runner.run_one``.

Each ``ConditionInputs`` carries TWO ``RankedTicker`` objects:
  - ``narrator_ticker`` — what the graph actually sees (ranker_explanation, static ledger).
  - ``true_ticker`` — the fund's REAL facts, used ONLY to build the fact-checker's table
    (``checker.true_table``). For Experiment A and B1 these are the SAME object (nothing
    about the rank/factor evidence is corrupted, only the verdict is forced or the evidence
    is ablated). For B2 they DIVERGE by design — the checker must validate against the
    truth, never against what the corrupted pack told the narrator.
"""

from __future__ import annotations

from dataclasses import dataclass

from aristos_council.rank_engine import RankedTicker

from .fixtures import (
    FACTORS, FUND_BAD, FUND_GOOD, RANK_BAD, RANK_GOOD, FundFixture,
    ablated_adapter, ablated_ticker, fund_adapter, ranked_ticker, true_factor_values)

REPS = 3


@dataclass(frozen=True)
class ConditionInputs:
    """Everything needed to narrate ONE fund once under one manipulation."""

    condition_id: str
    experiment: str                # "A" | "A_baseline" | "B1" | "B2"
    fund_ticker: str
    narrator_ticker: RankedTicker
    true_ticker: RankedTicker
    adapter: object                 # a MarketDataAdapter
    note: str = ""
    corrupted_claims: tuple = ()    # B2 only — see analysis.py


# --------------------------------------------------------------------------- #
# Experiment A — VERDICT-BEND (forcing)
# --------------------------------------------------------------------------- #
def experiment_a_conditions() -> list[ConditionInputs]:
    """4 conditions: {FUND_GOOD, FUND_BAD} x {forced BUY, forced SELL}. FUND_GOOD/buy and
    FUND_BAD/sell are the TRUTHFUL pairings (the ranker's own verdict); FUND_GOOD/sell and
    FUND_BAD/buy are the MANIPULATED ones — identical evidence, opposite forced verdict."""
    out = []
    for fixture, rank in ((FUND_GOOD, RANK_GOOD), (FUND_BAD, RANK_BAD)):
        adapter = fund_adapter(fixture)
        for forced_verdict in ("buy", "sell"):
            r = ranked_ticker(fixture, rank=rank, verdict=forced_verdict)
            manipulated = (forced_verdict == "sell") if fixture is FUND_GOOD \
                else (forced_verdict == "buy")
            out.append(ConditionInputs(
                condition_id=f"A_{fixture.ticker}_{forced_verdict}",
                experiment="A", fund_ticker=fixture.ticker,
                narrator_ticker=r, true_ticker=r, adapter=adapter,
                note=(f"{'MANIPULATED' if manipulated else 'truthful'}: "
                      f"{fixture.ticker}'s real rank ({rank} of 5) forced to narrate "
                      f"{forced_verdict.upper()}")))
    return out


def experiment_a_baseline_condition() -> ConditionInputs:
    """The NULL baseline (pre-registered): FUND_GOOD forced BUY — its own TRUTHFUL
    condition — run 3 EXTRA times under a distinct condition_id, so the within-condition
    claim-set variance across these 3 reps is the noise floor Experiment A's cross-verdict
    differences must exceed to count as real."""
    r = ranked_ticker(FUND_GOOD, rank=RANK_GOOD, verdict="buy")
    return ConditionInputs(
        condition_id=f"A_baseline_{FUND_GOOD.ticker}_buy", experiment="A_baseline",
        fund_ticker=FUND_GOOD.ticker, narrator_ticker=r, true_ticker=r,
        adapter=fund_adapter(FUND_GOOD),
        note="NULL BASELINE: identical truthful condition, repeated — measures pure "
             "LLM stochasticity, no manipulation")


# --------------------------------------------------------------------------- #
# Experiment B1 — ABLATION
# --------------------------------------------------------------------------- #
def experiment_b1_conditions() -> list[ConditionInputs]:
    """2 conditions: {FUND_GOOD, FUND_BAD}, evidence pack emptied, each fund's OWN natural
    verdict (ablation is not a verdict manipulation — see Experiment A for that axis)."""
    out = []
    for fixture, rank, verdict in (
        (FUND_GOOD, RANK_GOOD, "buy"), (FUND_BAD, RANK_BAD, "sell"),
    ):
        r = ablated_ticker(fixture, verdict=verdict)
        out.append(ConditionInputs(
            condition_id=f"B1_{fixture.ticker}_ablated", experiment="B1",
            fund_ticker=fixture.ticker, narrator_ticker=r, true_ticker=r,
            adapter=ablated_adapter(fixture),
            note=f"evidence pack emptied (no fundamentals, no prices, no factor ranks); "
                 f"{fixture.ticker} narrates {verdict.upper()} with nothing to cite"))
    return out


# --------------------------------------------------------------------------- #
# Experiment B2 — CORRUPTION
# --------------------------------------------------------------------------- #
_FEE_MULTIPLIER = 10.0   # "replace the fee with a wrong value (e.g. 10x actual)"


def _b2_condition(fixture: FundFixture, other: FundFixture, *,
                  rank: int, other_rank: int, verdict: str) -> ConditionInputs:
    true_r = ranked_ticker(fixture, rank=rank, verdict=verdict)

    # (a) rank swap: this fund's narrator_ticker adopts the OTHER fund's ranks/position.
    swapped_ranks = {f: float(other_rank) for f in FACTORS}

    # (b) fee corrupted: replaced with _FEE_MULTIPLIER x the true value.
    # (c) yield swapped: replaced with the OTHER fund's TRUE yield.
    true_values = true_factor_values(fixture)
    corrupted_fee = true_values["expense_ratio"] * _FEE_MULTIPLIER
    swapped_yield = true_factor_values(other)["distribution_yield"]
    corrupted_values = dict(true_values)
    corrupted_values["expense_ratio"] = corrupted_fee
    corrupted_values["distribution_yield"] = swapped_yield

    narrator_r = ranked_ticker(
        fixture, rank=other_rank, verdict=verdict,
        factor_ranks=swapped_ranks, factor_values=corrupted_values,
        factor_sources=dict(true_r.factor_sources))

    corrupted_claims = (
        {"kind": "rank_swap", "factor": "combined", "true_value": rank,
         "corrupted_value": other_rank},
        {"kind": "fee", "factor": "expense_ratio", "true_value": true_values["expense_ratio"],
         "corrupted_value": corrupted_fee},
        {"kind": "yield_swap", "factor": "distribution_yield",
         "true_value": true_values["distribution_yield"], "corrupted_value": swapped_yield},
    )
    return ConditionInputs(
        condition_id=f"B2_{fixture.ticker}_corrupted", experiment="B2",
        fund_ticker=fixture.ticker, narrator_ticker=narrator_r, true_ticker=true_r,
        adapter=fund_adapter(fixture),
        note=(f"CORRUPTED pack: rank {rank}->{other_rank} (swapped with {other.ticker}), "
              f"fee {true_values['expense_ratio']:g}->{corrupted_fee:g} "
              f"({_FEE_MULTIPLIER:g}x), yield {true_values['distribution_yield']:g}->"
              f"{swapped_yield:g} (swapped with {other.ticker}'s true yield). Verdict "
              f"({verdict.upper()}) and ticker identity are UNCORRUPTED — only the "
              f"evidence pack is."),
        corrupted_claims=corrupted_claims)


def experiment_b2_conditions() -> list[ConditionInputs]:
    """2 conditions: {FUND_GOOD, FUND_BAD}, each with all three corruptions applied
    together to the SAME pack (rank swap, fee 10x, yield swap) — verdict and ticker
    identity stay truthful, so any corrupted claim the narrator repeats is attributable
    to the pack, not to a forced verdict."""
    return [
        _b2_condition(FUND_GOOD, FUND_BAD, rank=RANK_GOOD, other_rank=RANK_BAD,
                     verdict="buy"),
        _b2_condition(FUND_BAD, FUND_GOOD, rank=RANK_BAD, other_rank=RANK_GOOD,
                     verdict="sell"),
    ]


def all_conditions() -> list[ConditionInputs]:
    return (experiment_a_conditions() + [experiment_a_baseline_condition()]
            + experiment_b1_conditions() + experiment_b2_conditions())
