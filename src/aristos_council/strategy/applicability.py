"""Strategy applicability — which rank strategies a COHORT can honestly be graded by
(STRAT-PICKER-1).

The picker's job is to offer every lens that CAN grade the cohort in front of you and to
hide only the ones that provably cannot. Two facts already live in the YAMLs, so nothing
here is hardcoded:

- a rank strategy declares its ``asset_kinds`` (``equity`` for a stock lens, ``etf`` for a
  fund lens) — the same declaration the run-time asset-kind gate reads
  (``factors.is_asset_kind_out_of_scope``);
- a rank strategy declares its ``suggested_universes`` — the cohorts it was built for.

So a NAMED cohort's asset class is DERIVED by inversion: the kinds of the strategies that
suggest it. ``etf_core_ucits_v1`` is suggested only by ETF lenses -> an ETF cohort;
``growth_40_v1`` only by stock lenses -> an equity cohort. Add a lens that suggests a new
cohort and the classification follows with zero code change here — the same
derived-not-hardcoded contract ``discovery`` uses for the lens set.

An AD-HOC cohort (``adhoc:<hex8>`` — a pasted list, a Universe-Editor "run once") declares
NOTHING. That is the bug this module closes: an undeclared cohort is UNKNOWN, and UNKNOWN
must never hide a strategy. The live 2026-08-10 ad-hoc cohort (21 stock names) was offered
a single lens while RAW / flagship / GARP v2 / Defensive / Financials all sat in the repo,
unreachable. Three-valued, exactly like ``passed`` on a screen criterion and like the
asset-kind gate itself: ``etf`` / ``equity`` / ``None``, where ``None`` is
NOT-EVALUATED — never a silent exclusion.

Hidden (``ui: hidden``) strategies are NOT this module's business: the caller filters those
with ``demo_surface.is_hidden_strategy`` (the validation toggle) BEFORE asking what applies,
so a superseded config stays hidden here too.
"""

from __future__ import annotations

from typing import Iterable, Optional

# An ad-hoc cohort id (``universe.adhoc_universe_id``) — a fingerprint, not a declaration.
ADHOC_PREFIX = "adhoc:"


def strategy_asset_kinds(strategy) -> frozenset[str]:
    """A strategy's declared asset kinds, normalized lowercase. EMPTY means the strategy
    scopes nothing (every kind in scope) — the same reading the run-time gate gives an
    absent ``asset_kinds``."""
    raw = getattr(strategy, "asset_kinds", None) or []
    return frozenset(str(k).strip().lower() for k in raw if str(k).strip())


def cohort_asset_kind(universe_id: Optional[str], strategies: Iterable) -> Optional[str]:
    """The cohort's asset class DERIVED from the strategies that suggest it, or ``None``.

    ``None`` (NOT EVALUATED) for an ad-hoc cohort, an unknown/blank id, a cohort no
    strategy suggests, or a cohort suggested by strategies of DIFFERENT kinds (ambiguous —
    never guessed). Otherwise the single kind those strategies declare (``etf``/``equity``).
    """
    uid = (universe_id or "").strip()
    if not uid or uid.startswith(ADHOC_PREFIX):
        return None                      # undeclared cohort -> UNKNOWN, hide nothing
    kinds: set[str] = set()
    for s in strategies:
        suggested = [str(x).strip()
                     for x in (getattr(s, "suggested_universes", None) or [])]
        if uid in suggested:
            kinds |= strategy_asset_kinds(s)
    return next(iter(kinds)) if len(kinds) == 1 else None


def is_applicable(strategy, cohort_kind: Optional[str]) -> bool:
    """Can ``strategy`` grade a cohort of ``cohort_kind``? An UNKNOWN cohort kind
    (``None``) admits every strategy; a strategy that declares no ``asset_kinds`` scopes
    nothing and is always applicable. Only a CONFIRMED mismatch (both sides declared, kind
    not among them) is out of scope — the confirmed-only discipline of the run-time gate."""
    if not cohort_kind:
        return True
    kinds = strategy_asset_kinds(strategy)
    return not kinds or cohort_kind in kinds


def applicable_rank_strategies(strategies: Iterable,
                               cohort_kind: Optional[str]) -> list:
    """``strategies`` filtered to the ones that can grade this cohort, order preserved.
    Pass an already-visibility-filtered list (see the module docstring): hidden configs are
    the caller's concern, asset class is this module's."""
    return [s for s in strategies if is_applicable(s, cohort_kind)]


def cohort_scope_note(cohort_kind: Optional[str], n_applicable: int,
                      *, adhoc: bool = False) -> str:
    """One honest caption for the picker: what was derived and what it costs the user. An
    UNKNOWN cohort says so explicitly (nothing is being filtered out) rather than implying
    the list was curated."""
    if cohort_kind:
        return (f"Cohort asset class: **{cohort_kind}** (derived from the lenses that "
                f"declare it) · {n_applicable} strategy(ies) apply.")
    if adhoc:
        return (f"Ad-hoc cohort — no declared asset class, so nothing is filtered out: "
                f"all {n_applicable} strategy(ies) stay offered.")
    return (f"Cohort asset class undeclared — nothing is filtered out: all "
            f"{n_applicable} strategy(ies) stay offered.")


def out_of_scope_note(strategy, cohort_kind: Optional[str]) -> str:
    """A warning for a CONFIRMED mismatch between the picked strategy and the cohort, or
    ``""``. The run is not blocked — the run-time asset-kind gate excludes the individual
    names honestly; this only makes the mismatch visible BEFORE the run."""
    if is_applicable(strategy, cohort_kind):
        return ""
    kinds = ", ".join(sorted(strategy_asset_kinds(strategy)))
    label = (getattr(strategy, "display_name", "") or getattr(strategy, "name", "")
             or getattr(strategy, "id", "this strategy"))
    return (f"{label} grades {kinds} names, but this cohort is {cohort_kind} — every name "
            f"would be excluded by the asset-kind gate. Pick a {cohort_kind} lens.")
