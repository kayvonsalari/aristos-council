"""Fast SCREEN-ONLY ranking — sort a pool of tickers WITHOUT the LLM council.

The matrix verdict is ~screen-driven: ``decision_matrix``'s score = sum(criterion
margin x weight) + sum(stance x conf x SMALL weight). The criterion part is pure
deterministic (``run_screen``) and dominates; the specialist-stance part needs the
LLMs and contributes little. So a screen-only pass (one data fetch + arithmetic,
SECONDS per name) ranks the pool almost identically to the full matrix (6 LLM
calls, MINUTES per name) — ideal for SHORTLISTING. Run the full council only on the
final picks for the narrative report and the small stance adjustment.

CAVEAT (surfaced in the CLI output): the screen-only score EXCLUDES the small
specialist-stance contribution; ranks can differ from the full matrix only by that
nudge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from .agents.matrix import screen_only_matrix
from .data.adapter import DataUnavailable
from .tools.criteria.registry import Evidence, required_evidence, run_screen


@dataclass
class ScreenRanking:
    """One ticker's screen-only result."""

    ticker: str
    verdict: str                       # buy / hold / sell / insufficient_evidence
    score: Optional[float]             # None when gated
    gated: bool = False
    degraded: bool = False             # a source fetch failed (partial evidence)
    error: Optional[str] = None        # set when the name could not be screened
    criteria: list[dict] = field(default_factory=list)   # name/passed/observed rows


def gather_evidence(adapter, strategy, ticker: str, *, today: date):
    """Build the screen Evidence the same way the council's gather does — but with
    NO agents. Returns (evidence, errors): a per-source DataUnavailable is caught and
    recorded, not raised, so one flaky name never aborts a pool ranking."""
    needs = required_evidence(strategy.criteria)
    errors: list[tuple[str, str]] = []

    fundamentals = None
    try:
        fundamentals = adapter.get_fundamentals(ticker)
    except DataUnavailable as exc:
        errors.append(("fundamentals", str(exc)))

    last_close = None
    try:
        prices = adapter.get_price_history(
            ticker, start=today - timedelta(days=400), end=today)
        last_close = prices.closes[-1] if prices and prices.closes else None
    except DataUnavailable as exc:
        errors.append(("prices", str(exc)))

    dividends: list = []
    if "dividends" in needs:
        try:
            dividends = adapter.get_dividend_history(
                ticker, start=today - timedelta(days=365 * 40), end=today)
        except DataUnavailable as exc:
            errors.append(("dividends", str(exc)))

    evidence = Evidence(
        fundamentals=fundamentals, dividends=dividends or [], last_close=last_close,
        streak_method=adapter.dividend_streak_method)
    return evidence, errors


def rank_screen_only(strategy, evidence: Evidence, ticker: str) -> ScreenRanking:
    """The pure core: run_screen + the screen-only matrix score. No network, no LLM.
    Deterministic given the evidence."""
    screen = run_screen(strategy.criteria, evidence, ticker=ticker)
    m = screen_only_matrix(screen, strategy, ticker=ticker)
    criteria = [{"name": c.name, "passed": c.passed, "observed": c.observed}
                for c in screen.criteria]
    return ScreenRanking(
        ticker=ticker, verdict=m.verdict.value, score=m.score, gated=m.gated,
        criteria=criteria)


def rank_ticker(adapter, strategy, ticker: str, *, today: date) -> ScreenRanking:
    """Fetch evidence (catching per-source failures) and screen-rank one ticker."""
    evidence, errors = gather_evidence(adapter, strategy, ticker, today=today)
    if evidence.fundamentals is None:
        # No fundamentals at all -> nothing to screen; report as an error row.
        return ScreenRanking(
            ticker=ticker, verdict="unknown", score=None, degraded=True,
            error="; ".join(f"{s}: {m}" for s, m in errors) or "no fundamentals")
    ranking = rank_screen_only(strategy, evidence, ticker)
    ranking.degraded = bool(errors)
    if errors:
        ranking.error = "; ".join(f"{s}: {m}" for s, m in errors)
    return ranking


def split_and_sort(rankings: list[ScreenRanking]):
    """Partition into (scored desc, gated, other) so the TOP of `scored` is the pick
    list and gated/un-scorable names are flagged separately."""
    scored = [r for r in rankings
              if r.score is not None and not r.gated and not r.error]
    gated = [r for r in rankings if r.gated]
    other = [r for r in rankings
             if not r.gated and (r.error or r.score is None)]
    scored.sort(key=lambda r: r.score, reverse=True)
    return scored, gated, other
