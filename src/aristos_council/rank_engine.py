"""Rank-combine decision engine — RANK, don't weight (Aristos v2 decision core).

The disciplined break from weight-tuning: instead of summing tuned point-weights
(the trial-and-error problem), rank the candidate UNIVERSE on each proven factor
(best = rank 1), SUM the ranks, and the lowest combined rank wins — exactly
Greenblatt's Magic-Formula mechanic and van Vliet-Blitz's combine-the-ranks
Conservative Formula. There are NO point-weights left to guess: the ranking IS the
decision, relative to the universe and self-scaling.

The verdict is a QUINTILE cut (industry-standard): top 20% = BUY, middle 60% = HOLD,
bottom 20% = SELL — or a configurable top_k / top_percentile for small universes.
Pure function of the factor data: fully reproducible, no LLM in the ranking. Emits
per-ticker per-factor ranks + the combined rank, so a verdict is fully auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .factors import FACTOR_REGISTRY


@dataclass(frozen=True)
class FactorSpec:
    """A factor in a rank strategy: its registry name and the rank direction. The
    direction defaults to the factor's NATURAL direction (e.g. low_volatility ->
    'low') so a YAML can omit it; an explicit value overrides."""

    name: str
    direction: Optional[str] = None     # "high" | "low"; None -> registry default
    # Per-factor missing-mode override: "worst" | "exclude" | "neutral". None ->
    # use the strategy-level default passed to rank_universe.
    missing: Optional[str] = None

    def resolved_direction(self) -> str:
        return self.direction or FACTOR_REGISTRY[self.name].direction

    def resolved_missing(self, default: str) -> str:
        return self.missing or default


@dataclass
class RankedTicker:
    ticker: str
    factor_ranks: dict[str, float]      # factor -> rank (1 = best; ties averaged)
    factor_values: dict[str, Optional[float]]
    combined_rank: float                # sum of factor ranks (lower = better)
    universe_size: int
    verdict: str = "hold"               # buy / hold / sell
    # 1-based position among the ranked (kept) names, best-first — RECORDED from the
    # existing sort (no effect on ordering/cut/tie-break). Used by the narration
    # rank-semantics post-check. None until assigned; never set on excluded names.
    rank_position: Optional[int] = None
    excluded: bool = False
    reason: str = ""
    # Factors whose value was NOT-EVAL under 'neutral' mode — imputed with the
    # ticker's mean present-rank (judged on what it has, not punished for the gap).
    imputed_factors: list[str] = field(default_factory=list)
    # Per-factor SOURCE tag (ITEM 1): which computation path produced each value
    # (e.g. earnings_yield -> "ev" | "fallback:ebit_mcap" | "abstained"). Attached by
    # the rank stage after ranking; empty for a bare rank_universe call.
    factor_sources: dict[str, str] = field(default_factory=dict)
    # Screen criteria that ABSTAINED for this (ranked) name -> note. A name that passed
    # the screen while a dividend-safety criterion could not be evaluated is legitimate
    # but must be VISIBLE (footnote in the ranked table).
    screen_abstentions: dict[str, str] = field(default_factory=dict)
    # DISPLAY-ONLY cohort position (RANK-DISPLAY-1): 1-based, ties SHARING a position
    # (competition ranking), so a bare rank-SUM is never misread as an ordinal. Kept at
    # the END of the field list so the positional constructor order is unchanged. Distinct
    # from rank_position (which stays sequential for the narration check). Never set on
    # excluded names; cohort_tied is True when another ranked name shares this combined rank.
    cohort_position: Optional[int] = None
    cohort_tied: bool = False

    def score_bounds(self) -> tuple[int, int]:
        """(best, worst) possible combined rank-sum for this name's cohort: best = number
        of factors (rank 1 on every factor), worst = factors × cohort size. Display-only
        context so the rank-SUM reads against its scale (RANK-DISPLAY-1)."""
        f = len(self.factor_ranks)
        return f, f * self.universe_size

    def explain(self) -> str:
        n = self.universe_size
        bits = ", ".join(
            f"{f} rank {r:.0f}/{n}" + ("*" if f in self.imputed_factors else "")
            for f, r in self.factor_ranks.items())
        if self.excluded:
            return f"{self.ticker}: EXCLUDED ({self.reason})"
        tail = "  (* = imputed, factor absent)" if self.imputed_factors else ""
        # "combined rank-sum N across an M-name cohort" — the ranker verdict IS a
        # cohort statement (a rank-sum over the universe), phrased so the narrator that
        # echoes this text reads cleanly (never "combined rank of N/M-name cohort").
        core = f"combined rank-sum {self.combined_rank:.0f} across a {n}-name cohort"
        # RANK-DISPLAY-1: lead with the ORDINAL position (tie-shared) so the rank-sum is
        # never misread as a position, and disclose the best/worst bounds. When the
        # position was not assigned (a bare RankedTicker), the core phrasing stands.
        if self.cohort_position is not None:
            best, worst = self.score_bounds()
            tie = " (tied)" if self.cohort_tied else ""
            core = (f"#{self.cohort_position} of {n}{tie} — {core} "
                    f"(best {best}, worst {worst})")
        return (f"{self.ticker}: {bits} -> {core} -> {self.verdict.upper()}{tail}")


def _rank_one_factor(values: list[tuple[int, Optional[float]]], direction: str,
                     n: int, mode: str) -> dict[int, float]:
    """1-based ranks for one factor across the universe (best = 1; ties averaged).

    A missing value is handled per ``mode``:
    - "worst": gets the worst rank (n) — a NOT-EVAL is treated as maximally bad.
    - "neutral": gets NO rank here (omitted from this factor); the caller imputes it
      from the ticker's other ranks, so a name without this datum (e.g. a buyback-
      only payer with no dividend) is judged on the factors it HAS, not punished.
    - "exclude": handled upstream (the ticker is removed before ranking).
    """
    present = sorted([(i, v) for i, v in values if v is not None],
                     key=lambda iv: iv[1], reverse=(direction == "high"))
    ranks: dict[int, float] = {}
    i = 0
    while i < len(present):
        j = i
        while j + 1 < len(present) and present[j + 1][1] == present[i][1]:
            j += 1
        avg = (i + 1 + j + 1) / 2.0          # average of 1-based positions i+1..j+1
        for k in range(i, j + 1):
            ranks[present[k][0]] = avg
        i = j + 1
    if mode == "worst":
        for idx, v in values:
            if v is None:
                ranks[idx] = float(n)        # worst rank for a missing factor
    # "neutral": leave missing idx unranked (imputed by the combine step).
    return ranks


def _verdict_for_position(i: int, n: int, cut: str, k: int, percentile: float) -> str:
    if cut == "top_k":
        return "buy" if i < k else "hold"
    if cut == "top_percentile":
        return "buy" if i < max(1, round(n * percentile)) else "hold"
    # quintile (default): top 20% buy, bottom 20% sell, middle hold
    if i < n / 5.0:
        return "buy"
    if i >= n * 4.0 / 5.0:
        return "sell"
    return "hold"


def rank_universe(
    rows: list[tuple[str, dict[str, Optional[float]]]],
    factors: list[FactorSpec], *,
    cut: str = "quintile", k: int = 6, percentile: float = 0.2,
    missing: str = "worst",
) -> list[RankedTicker]:
    """Rank a universe and assign verdicts. ``rows`` is (ticker, {factor: value}).

    ``missing`` is the strategy-level default mode for a NOT-EVAL factor, overridable
    PER FACTOR via ``FactorSpec.missing``:
    - 'exclude': drop the ticker entirely BEFORE ranking (Greenblatt excludes names
      you can't score).
    - 'worst': keep the ticker at the worst rank for that factor.
    - 'neutral': omit the ticker from that factor's ranking and impute its rank from
      the MEAN of the ranks it DOES have — so a name lacking one datum (e.g. a
      buyback-only company with no dividend yield) is judged on its other factors,
      not dumped to the bottom for the gap.

    Returns the universe sorted best-first; excluded names are appended, flagged,
    never given a BUY. The combined rank is the SUM of per-factor ranks (Greenblatt);
    a neutral-imputed factor contributes the ticker's own mean rank, so the sum stays
    over a constant number of terms and names are comparable.
    """
    mode_of = {f.name: f.resolved_missing(missing) for f in factors}

    excluded: list[RankedTicker] = []
    kept: list[tuple[int, str, dict]] = []
    for idx, (ticker, vals) in enumerate(rows):
        excl_factors = [f.name for f in factors
                        if mode_of[f.name] == "exclude" and vals.get(f.name) is None]
        if excl_factors:
            excluded.append(RankedTicker(
                ticker=ticker, factor_ranks={}, factor_values=dict(vals),
                combined_rank=float("inf"), universe_size=len(rows),
                verdict="hold", excluded=True,
                reason="missing factor(s): " + ", ".join(excl_factors)))
        else:
            kept.append((idx, ticker, vals))

    n = len(kept)
    # Per-factor ranks across the KEPT universe (re-indexed 0..n-1). 'worst' fills
    # the missing idx with rank n; 'neutral' leaves them unranked (imputed below).
    per_factor: dict[str, dict[int, float]] = {}
    for f in factors:
        vals = [(j, kept[j][2].get(f.name)) for j in range(n)]
        per_factor[f.name] = _rank_one_factor(
            vals, f.resolved_direction(), n, mode_of[f.name])

    ranked: list[RankedTicker] = []
    for j in range(n):
        present: dict[str, float] = {}
        neutral_missing: list[str] = []
        for f in factors:
            r = per_factor[f.name].get(j)
            if r is not None:
                present[f.name] = r
            else:
                neutral_missing.append(f.name)        # value absent under 'neutral'
        # Impute each neutral-missing factor with the ticker's mean present rank, so
        # it neither helps nor hurts relative to the factors it actually has.
        impute = (sum(present.values()) / len(present)) if present else float(n)
        franks = dict(present)
        for name in neutral_missing:
            franks[name] = impute
        ranked.append(RankedTicker(
            ticker=kept[j][1], factor_ranks=franks,
            factor_values=dict(kept[j][2]),
            combined_rank=sum(franks.values()), universe_size=n,
            imputed_factors=neutral_missing))

    # Sort best-first; tie-break by ticker for determinism.
    ranked.sort(key=lambda r: (r.combined_rank, r.ticker))
    for i, r in enumerate(ranked):
        r.verdict = _verdict_for_position(i, n, cut, k, percentile)
        r.rank_position = i + 1          # record position (no effect on the sort/cut)
    assign_cohort_positions(ranked)      # display-only #N of M (RANK-DISPLAY-1)
    return ranked + excluded


# --------------------------------------------------------------------------- #
# Cohort position — display-only ordinal (RANK-DISPLAY-1)
# --------------------------------------------------------------------------- #
def cohort_positions(ranked: list[RankedTicker]) -> dict[str, tuple[int, bool]]:
    """``{ticker: (position, tied)}`` over the LIVE ranked names (excluded/unrateable
    dropped — they never inflate the cohort). Position is assigned after sorting by
    combined rank, with TIES SHARING a position (competition ranking): a name's position
    is 1 + the count of names with a strictly SMALLER combined rank. ``tied`` is True when
    another ranked name shares this combined rank. Presentation only — combined_rank,
    verdict, and rank_position are untouched."""
    order = sorted((r for r in ranked if not r.excluded),
                   key=lambda r: r.combined_rank)
    out: dict[str, tuple[int, bool]] = {}
    i = 0
    while i < len(order):
        j = i
        while (j + 1 < len(order)
               and order[j + 1].combined_rank == order[i].combined_rank):
            j += 1
        tied = j > i
        for k in range(i, j + 1):
            out[order[k].ticker] = (i + 1, tied)
        i = j + 1
    return out


def assign_cohort_positions(ranked: list[RankedTicker]) -> None:
    """Write the display-only ``cohort_position`` / ``cohort_tied`` onto the live ranked
    names (see ``cohort_positions``). Excluded names keep ``cohort_position = None``."""
    pos = cohort_positions(ranked)
    for r in ranked:
        if r.ticker in pos:
            r.cohort_position, r.cohort_tied = pos[r.ticker]


def format_score(v: float) -> str:
    """A combined rank-sum for display: whole numbers bare (``11``), otherwise one
    decimal (``11.5``) — the averaged-tie ranks are the only non-integers."""
    return f"{v:.0f}" if float(v).is_integer() else f"{v:.1f}"


def format_position_cell(position: Optional[int], cohort_size: int, tied: bool,
                         combined_rank: float, n_factors: int) -> str:
    """The ONE shared cohort-position string every rank-sum display uses (CLI ranked
    table, Universe Run table, markdown download, Company Check): the ORDINAL first with
    the rank-SUM as detail, e.g. ``#1 of 9 · score 11 (best 3 · worst 27)``. ``(tied)``
    marks a shared position; ``best`` = number of factors, ``worst`` = factors × cohort
    size. Falls back to a bare ``score S`` when no position is known (e.g. an excluded
    name). Presentation only."""
    score = format_score(combined_rank)
    if position is None:
        return f"score {score}"
    best, worst = n_factors, n_factors * cohort_size
    tie = " (tied)" if tied else ""
    return (f"#{position} of {cohort_size}{tie} · score {score} "
            f"(best {best} · worst {worst})")
