"""Reproducibility harness — make LLM verdict (in)stability VISIBLE.

Temperature 0 reduced but did NOT eliminate verdict non-determinism: borderline,
screen-PASSING names still flip BUY<->HOLD because the FINAL verdict is an LLM
sample (live: GOOGL returned BUY 0.62 on one run and HOLD 0.62 on another with
identical passing screen inputs). A single run is therefore not a trustworthy
verdict for such names.

HONEST SCOPE: this harness does NOT make verdicts stable. It REPORTS their
stability. "GOOGL: BUY 6 / HOLD 4, conf 0.60±0.04 [BORDERLINE]" replaces a single
misleading "BUY"; the split itself is the most useful signal. Reducing the wobble
(prompt calibration) is SEPARATE later work, measured THROUGH this harness.

GATED outcomes are deterministic and need no resampling: a SELL produced by the
disposition gate (gate_override_applied) and an INSUFFICIENT_EVIDENCE verdict are
fixed by CODE, not an LLM sample. So the first run short-circuits the rest when it
is gated — we don't burn API calls re-confirming a deterministic result.

This module is pure/aggregation + a thin council wrapper. The aggregation is
unit-tested with FAKE run results (no LLM, no network); the wrapper is what the
CLI / Colab cell calls to actually spend the runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

# Rough per-run cost (Haiku specialists + Sonnet critic/decision), for the cost
# guard only — printed before a batch so n is a deliberate spend, never a surprise.
_COST_PER_RUN_USD = 0.19


@dataclass(frozen=True)
class RunOutcome:
    """One council run, reduced to what stability analysis needs."""

    verdict: str                       # "buy" / "hold" / "sell" / "insufficient_evidence"
    confidence: float
    vetoes: tuple[str, ...] = ()       # veto trigger values that fired this run
    gated: bool = False                # the deterministic disposition gate decided it


@dataclass(frozen=True)
class StabilityReport:
    """Aggregate of n runs for one name — the honest replacement for a lone verdict."""

    ticker: str
    n_requested: int
    n_run: int
    distribution: dict[str, int]       # verdict -> count
    modal_verdict: str
    confidence_mean: float
    confidence_stdev: float            # population stdev across the runs
    confidence_min: float
    confidence_max: float
    stability: str                     # "stable" | "BORDERLINE" | "deterministic"
    gated: bool
    veto_union: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# Extraction — a finished run-state -> a RunOutcome
# --------------------------------------------------------------------------- #
def outcome_from_state(state) -> RunOutcome:
    """Reduce a completed ResearchState to a RunOutcome.

    ``gated`` is True when the deterministic disposition gate fixed the verdict —
    a SELL cap (``gate_override_applied``) or an INSUFFICIENT_EVIDENCE
    short-circuit (``insufficient_evidence``). Those are reproducible by CODE, so
    the harness need not resample them.
    """
    d = getattr(state, "decision", None)
    if d is None:
        return RunOutcome(verdict="unknown", confidence=0.0)
    verdict = d.recommendation.value if d.recommendation is not None else "unknown"
    vetoes = tuple(f.trigger.value for f in getattr(state, "veto_flags", []))
    gated = bool(getattr(d, "gate_override_applied", False)
                 or getattr(d, "insufficient_evidence", False))
    return RunOutcome(verdict=verdict, confidence=float(d.confidence),
                      vetoes=vetoes, gated=gated)


# --------------------------------------------------------------------------- #
# Aggregation — PURE, unit-tested with fakes
# --------------------------------------------------------------------------- #
def aggregate_outcomes(
    ticker: str, outcomes: list[RunOutcome], *,
    n_requested: int, deterministic: bool = False,
) -> StabilityReport:
    """Summarise n run outcomes. ``deterministic`` marks a gated short-circuit
    (verdict fixed by the gate, only one run spent)."""
    if not outcomes:
        raise ValueError("aggregate_outcomes requires at least one outcome")
    confs = [o.confidence for o in outcomes]
    dist: dict[str, int] = {}
    for o in outcomes:
        dist[o.verdict] = dist.get(o.verdict, 0) + 1
    # Modal verdict: highest count, ties broken by verdict name for determinism.
    modal = sorted(dist.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    mean = sum(confs) / len(confs)
    stdev = (sum((c - mean) ** 2 for c in confs) / len(confs)) ** 0.5
    if deterministic:
        stability = "deterministic"
    elif len(dist) == 1:
        stability = "stable"
    else:
        stability = "BORDERLINE"
    veto_union = tuple(sorted({v for o in outcomes for v in o.vetoes}))
    return StabilityReport(
        ticker=ticker, n_requested=n_requested, n_run=len(outcomes),
        distribution=dist, modal_verdict=modal,
        confidence_mean=mean, confidence_stdev=stdev,
        confidence_min=min(confs), confidence_max=max(confs),
        stability=stability, gated=deterministic, veto_union=veto_union)


def run_council_n(
    run_one: Callable[[], Optional[RunOutcome]], *,
    ticker: str, n: int = 5,
) -> StabilityReport:
    """Run a council ``n`` times and report verdict-distribution stability.

    ``run_one`` is a zero-arg callable returning ONE RunOutcome (the council wrapper
    from ``build_run_one``, or a fake in tests). DETERMINISTIC SHORT-CIRCUIT: if the
    FIRST run is gated (SELL cap / INSUFFICIENT_EVIDENCE), the verdict is fixed by
    the gate, so we stop after one run and do not spend the remaining n-1.
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    first = run_one()
    if first is None:
        raise RuntimeError(f"first council run for {ticker} produced no outcome")
    if first.gated:
        return aggregate_outcomes(ticker, [first], n_requested=n, deterministic=True)
    outcomes = [first]
    for _ in range(n - 1):
        o = run_one()
        if o is not None:
            outcomes.append(o)
    return aggregate_outcomes(ticker, outcomes, n_requested=n, deterministic=False)


# --------------------------------------------------------------------------- #
# Cost guard + reporting
# --------------------------------------------------------------------------- #
def estimate_cost(n: int, per_run: float = _COST_PER_RUN_USD) -> float:
    return n * per_run


def cost_guard_line(n: int, per_run: float = _COST_PER_RUN_USD) -> str:
    return (f"Estimated cost: up to {n} runs x ${per_run:.2f} = "
            f"${estimate_cost(n, per_run):.2f} (gated names short-circuit to 1 run)")


def format_stability(report: StabilityReport) -> str:
    """A one-line human summary — the honest replacement for a single verdict."""
    if report.stability == "deterministic":
        return (f"{report.ticker}: {report.modal_verdict.upper()} "
                f"(deterministic — gated, 1 run; {report.n_requested - 1} extra "
                f"runs skipped)")
    dist = " / ".join(f"{v.upper()} {c}"
                      for v, c in sorted(report.distribution.items(),
                                         key=lambda kv: (-kv[1], kv[0])))
    tag = "BORDERLINE" if report.stability == "BORDERLINE" else "stable"
    return (f"{report.ticker}: {dist}, modal {report.modal_verdict.upper()}, "
            f"conf {report.confidence_mean:.2f}±{report.confidence_stdev:.2f} "
            f"[{tag}] (n={report.n_run})")


def stability_csv_row(report: StabilityReport) -> dict:
    """A flat, Drive-CSV-friendly row. The split is preserved in `distribution` —
    never collapsed to a single verdict that hides the wobble."""
    return {
        "ticker": report.ticker,
        "modal_verdict": report.modal_verdict,
        "stability": report.stability,
        "n_run": report.n_run,
        "distribution": ";".join(
            f"{v}:{c}" for v, c in sorted(report.distribution.items())),
        "confidence_mean": round(report.confidence_mean, 4),
        "confidence_stdev": round(report.confidence_stdev, 4),
        "gated": report.gated,
        "vetoes": ";".join(report.veto_union),
    }


# --------------------------------------------------------------------------- #
# Council wrapper — the thin layer the CLI / Colab cell uses to spend real runs.
# Not unit-tested (needs an LLM); the aggregation above is what the tests pin.
# --------------------------------------------------------------------------- #
def build_run_one(
    *, ticker: str, strategy, adapter, runners,
    sentiment_adapter=None, sentiment_missing_key: bool = False,
    prior_recommendation=None,
) -> Callable[[], RunOutcome]:
    """Compile the council once and return a run_one() that invokes a fresh run
    each call (each call is an independent LLM sample of the same config)."""
    from .graph import build_council
    from .state import ResearchState

    app = build_council(adapter, strategy, runners,
                        sentiment_adapter=sentiment_adapter,
                        sentiment_missing_key=sentiment_missing_key)

    def run_one() -> RunOutcome:
        result = ResearchState.model_validate(app.invoke(ResearchState(
            ticker=ticker, strategy_id=strategy.id,
            prior_recommendation=prior_recommendation)))
        return outcome_from_state(result)

    return run_one


# --------------------------------------------------------------------------- #
# Decision-node MICRO-harness ("Aristos v2" step 1) — replay ONLY the Decision
# node N times on cached post-Critic state. Same statistical distribution as
# run_council_n at a fraction of the cost: the screen + 4 specialists + Critic
# are empirically stable, so they run ONCE; only the wobbling Decision node repeats.
# --------------------------------------------------------------------------- #
def run_decision_n(
    *, ticker: str, strategy, adapter, runners, n: int = 5,
    sentiment_adapter=None, sentiment_missing_key: bool = False,
    prior_recommendation=None,
) -> StabilityReport:
    """Run the upstream council ONCE, then replay the Decision node ``n`` times on
    deep-copies of the post-Critic snapshot; label STABLE/BORDERLINE by the verdict
    distribution.

    Cost ≈ one full run + (n-1) Decision-node calls, NOT n full runs. The upstream
    (gather + specialists + critic) is invoked exactly once; each Decision replay
    gets its own deep-copy so no replay can see another's mutations. GATED outcomes
    short-circuit to n=1 (deterministic) — the Decision node is not replayed.
    """
    from .agents.nodes import make_decision_node
    from .graph import build_upstream_council
    from .state import ResearchState

    upstream = build_upstream_council(
        adapter, strategy, runners, sentiment_adapter=sentiment_adapter,
        sentiment_missing_key=sentiment_missing_key)
    snapshot = ResearchState.model_validate(upstream.invoke(ResearchState(
        ticker=ticker, strategy_id=strategy.id,
        prior_recommendation=prior_recommendation)))

    decide = make_decision_node(strategy, runners["decision"])

    def one_replay() -> RunOutcome:
        # Independent deep-copy per replay: the Decision node mutates only
        # `decision`, but copying the whole state guarantees isolation regardless.
        st = snapshot.model_copy(deep=True)
        decide(st)
        return outcome_from_state(st)

    # Reuse the shared short-circuit + aggregation (gated first -> n=1).
    return run_council_n(one_replay, ticker=ticker, n=n)


def decision_cost_guard_line(n: int, per_run: float = _COST_PER_RUN_USD) -> str:
    """Cost guard for the MICRO-harness: one full upstream pass + cheap replays."""
    return (f"Estimated cost: 1 full run (${per_run:.2f}) + up to {max(n - 1, 0)} "
            f"Decision-node replays (fractions of a cent each); gated names "
            f"short-circuit to the single run")


def decision_stability_label(report: StabilityReport) -> str:
    """One-token label for the micro-harness: STABLE <v> / STABLE (gated) <v> /
    BORDERLINE (leaning <modal>, k/n)."""
    if report.stability == "deterministic":
        return f"STABLE (gated) {report.modal_verdict.upper()}"
    if report.stability == "stable":
        return f"STABLE {report.modal_verdict.upper()}"
    k = report.distribution.get(report.modal_verdict, 0)
    return (f"BORDERLINE (leaning {report.modal_verdict.upper()}, "
            f"{k}/{report.n_run})")


def decision_stability_banner(report: StabilityReport) -> Optional[str]:
    """The one-line report banner for a BORDERLINE decision, or None when STABLE."""
    if report.stability != "BORDERLINE":
        return None
    dist = " / ".join(
        f"{v.upper()} {c}" for v, c in sorted(report.distribution.items(),
                                              key=lambda kv: (-kv[1], kv[0])))
    return (f"BORDERLINE — the Decision node returned {dist} over {report.n_run} "
            f"replays on identical evidence; treat as a lead and read the report.")


def decision_stability_summary(report: StabilityReport) -> dict:
    """Flat machine-readable summary to stamp onto a RunReport / CSV column set."""
    return {
        "verdict_distribution": dict(report.distribution),
        "modal_verdict": report.modal_verdict,
        "stability": "BORDERLINE" if report.stability == "BORDERLINE" else "STABLE",
        "gated": report.gated,
        "n": report.n_run,
        "confidence_mean": round(report.confidence_mean, 4),
        "confidence_stdev": round(report.confidence_stdev, 4),
    }
