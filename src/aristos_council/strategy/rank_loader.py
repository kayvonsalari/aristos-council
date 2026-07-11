"""Rank-strategy loader — a strategy that RANKS a universe on proven factors.

Distinct from the screen ``Strategy`` (criteria + thresholds): a rank strategy
declares a FACTOR LIST (from the factor registry), the rank direction per factor,
universe exclusions, and a BUY cut method — and NO point weights (the whole point
of the rank rebuild). Validated against the factor registry at load.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from ..factors import FACTOR_REGISTRY


_MISSING_MODES = ("worst", "exclude", "neutral")


class RankFactorSpec(BaseModel):
    name: str
    direction: str | None = None        # "high" | "low"; None -> registry default
    # Per-factor missing-mode override (None -> strategy-level default). 'neutral'
    # judges a name on the factors it HAS rather than dumping it for a gap (e.g.
    # net_payout_yield on a buyback-only company with no dividend).
    missing: str | None = None

    @field_validator("direction")
    @classmethod
    def _dir_valid(cls, v):
        if v is not None and v not in ("high", "low"):
            raise ValueError(f"direction must be 'high' or 'low', got {v!r}")
        return v

    @field_validator("missing")
    @classmethod
    def _missing_valid(cls, v):
        if v is not None and v not in _MISSING_MODES:
            raise ValueError(f"missing must be one of {_MISSING_MODES}, got {v!r}")
        return v


class RankStrategy(BaseModel):
    id: str
    name: str
    version: int = Field(ge=1)
    # Friendly, user-facing name for dropdowns/captions (e.g. "Value + Momentum
    # (flagship)"). The `id` stays the STABLE record key (never renamed); this is
    # display-only, falling back to `name` then `id` when absent.
    display_name: str = ""
    # Optional one-line role caption shown under the selected entry. Display-only.
    role: str = ""
    # UI visibility (Sprint 4C): "hidden" -> not listed in the dropdowns by default
    # (legacy/superseded configs). Still fully loadable via the loader/CLI — hidden means
    # not listed, not removed. Default "" == visible. Presentation only.
    ui: str = ""
    created: str = ""           # optional 'YYYY-MM-DD' provenance date (display-only)
    description: str = ""
    rationale: str = ""
    # Optional SUGGESTED universes (UNI-1): universe ids this strategy is naturally run
    # on, surfaced FIRST in both universe selectors under a "Suggested" group. A
    # HIERARCHY, never a lock — every other universe stays one-click selectable (cross-
    # lens runs are a deliberate capability). Absent -> the selectors render as before.
    # Display-only: never read by the rank/screen logic; ids are not validated (a missing
    # id is simply skipped in the group).
    suggested_universes: list[str] = Field(default_factory=list)
    # The proven factors to rank on (>=1), validated against the factor registry.
    factors: list[RankFactorSpec] = Field(min_length=1)
    # Verdict cut over the ranked universe.
    cut: str = "quintile"               # quintile | top_k | top_percentile
    k: int = Field(default=6, ge=1)
    percentile: float = Field(default=0.2, gt=0.0, le=1.0)
    # Strategy-level default for a NOT-EVAL factor (per-factor override above).
    missing: str = "worst"              # worst | exclude | neutral
    # Universe exclusions (applied by the caller before ranking).
    min_market_cap: float | None = None
    exclude_sectors: list[str] = Field(default_factory=list)
    # Optional human rationale for the sector exclusion, surfaced by Company Check after
    # the sector gate line (display-only — never read by the rank/screen logic). Empty
    # -> the gate line renders bare, exactly as before.
    sector_exclusion_rationale: str = ""
    # Sector INCLUSION gate (FIN-1): the MIRROR of exclude_sectors. When non-empty, a
    # name whose sector is NOT among these is gated OUT OF SCOPE (financials_v1 admits
    # only financials, since P/B and ROE — not EBIT/EV — are their yardstick). CONFIRMED-
    # ONLY like the exclusion gate: a missing/None sector is never gated. The existing
    # exclude gate is untouched; the two are independent (a strategy sets one or neither).
    include_sectors: list[str] = Field(default_factory=list)
    # Display-only rationale for the inclusion gate, rendered by Company Check exactly
    # like sector_exclusion_rationale. Empty -> the gate line renders bare.
    sector_inclusion_rationale: str = ""
    # Payout-coverage gate (subsumed by prefilter_screen when that's on): exclude a
    # name whose payout_ratio EXCEEDS this. None -> no standalone payout gate.
    max_payout_ratio: float | None = None
    # SCREEN-AS-PREFILTER: when true, the rank stage runs the council_screen_strategy's
    # criteria on each name and EXCLUDES any CONFIRMED fail BEFORE ranking — so the
    # ranker orders only names that already PASS the (one) defensive definition the
    # council uses. Enforces the FLOORS (real yield, covered payout, cap, momentum)
    # that ranking-and-combining cannot. None/False -> rank the full universe as now.
    prefilter_screen: bool = False
    # Integrated-pipeline config. council_runs_on gates which ranked names proceed to
    # the (costly) LLM council; council_mode is the A/B toggle for the Decision agent.
    council_runs_on: str = "buy_quintile"   # buy_quintile | top_k | all
    # DEFAULT narrator (Option A): the controlled ranker-vs-council experiment (arms
    # D/G1/G2) returned 0 AGREEs in 17 councils and G2 proved the dissent is
    # pick-independent — the council's INDEPENDENT verdict carried no information. So
    # the ranker is the verdict-of-record and the LLM narrates. 'second_opinion' (B)
    # stays available behind the flag (it's a toggle by design; the code path lives).
    council_mode: str = "narrator"          # narrator (A, default) | second_opinion (B)
    # The SCREEN strategy whose criteria encode the SAME philosophy this rank strategy
    # ranks for — so the council judges a pick as a candidate for THIS philosophy, not
    # an unrelated screen. Without it the pipeline ran every defensive pick against the
    # GARP growth_v1 screen and got a meaningless ~100% DISAGREE. None -> caller default.
    council_screen_strategy: str | None = None

    @field_validator("council_runs_on")
    @classmethod
    def _runs_on_valid(cls, v):
        if v not in ("buy_quintile", "top_k", "all"):
            raise ValueError(f"council_runs_on must be buy_quintile|top_k|all, got {v!r}")
        return v

    @field_validator("council_mode")
    @classmethod
    def _mode_valid(cls, v):
        if v not in ("second_opinion", "narrator"):
            raise ValueError(f"council_mode must be second_opinion|narrator, got {v!r}")
        return v

    @field_validator("id")
    @classmethod
    def _id_carries_version(cls, v: str) -> str:
        if "_v" not in v:
            raise ValueError(f"rank-strategy id '{v}' must encode a version, e.g. '..._v1'")
        return v

    @field_validator("cut")
    @classmethod
    def _cut_valid(cls, v):
        if v not in ("quintile", "top_k", "top_percentile"):
            raise ValueError(f"cut must be quintile|top_k|top_percentile, got {v!r}")
        return v

    @field_validator("missing")
    @classmethod
    def _missing_valid(cls, v):
        if v not in _MISSING_MODES:
            raise ValueError(f"missing must be one of {_MISSING_MODES}, got {v!r}")
        return v

    @model_validator(mode="after")
    def _factors_resolve(self) -> "RankStrategy":
        unknown = [f.name for f in self.factors if f.name not in FACTOR_REGISTRY]
        if unknown:
            raise ValueError("unknown factors: " + ", ".join(unknown))
        return self


def load_rank_strategy(path: str | Path) -> RankStrategy:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"rank-strategy file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"rank-strategy file {p} did not parse to a mapping")
    return RankStrategy.model_validate(raw)
