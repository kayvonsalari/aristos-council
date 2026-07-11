"""Demo-surface presentation — friendly labels for a clean, professional UI.

The technical `id` (``growth_40_v1``, ``magic_formula_momentum_v1``) is the STABLE
record key: it names persisted reports, snapshot-CSV rows, and verdict history, so it is
NEVER renamed. This module decides only what the USER SEES — a friendly `display_name`
in dropdowns, with the id relegated to a small caption — so no underscores or ``_v1``
leak into the presentation while the record stays byte-stable underneath.

Pure functions (no Streamlit), so the labelling is unit-tested directly.
"""

from __future__ import annotations

# A universe is BACKSTAGE (hidden from the default demo surface, revealed by the "Show
# validation & legacy tools" toggle) when its ``role:`` marks it NEVER-GRADED — the
# observation/watch universes (energy_watch, the portfolio_watch pattern) and the
# known-trap control benches (defensive_16). FRONT-STAGE are the graded scoreboard
# universes and the exploratory lens cohorts (financials_16) — the ones you actually run
# to pick names. Role-DERIVED (UNI-1), never a hardcoded id list: a fresh universe yaml
# dropped into universes/ is classified by what its manifest declares, with zero code
# change here — the same dynamic-discovery contract 4C gave strategies.
_OBSERVATIONAL_ROLE_MARKER = "never graded"


def is_validation_universe(universe) -> bool:
    """True for a universe shown only behind the validation toggle. Role-derived: a role
    marked 'never graded' (an observation/watch universe or a known-trap control bench)
    is backstage; a missing/blank role, a graded scoreboard universe, or an exploratory
    lens cohort is front-stage. Accepts a manifest (reads ``.role``) or a bare role
    string."""
    role = universe if isinstance(universe, str) else (getattr(universe, "role", "") or "")
    return _OBSERVATIONAL_ROLE_MARKER in role.lower()


def is_hidden_strategy(strategy) -> bool:
    """True for a ``ui: hidden`` strategy (Sprint 4C — legacy/superseded configs). Hidden
    from the dropdown by default; revealed under the validation/legacy toggle. Accepts a
    loaded strategy OR a discovery ``StrategyInfo`` (a ``.hidden`` bool wins if present,
    else the ``.ui`` field)."""
    if hasattr(strategy, "ui"):                 # a loaded strategy carries the raw field
        return getattr(strategy, "ui", "") == "hidden"
    return bool(getattr(strategy, "hidden", False))    # a discovery StrategyInfo


def visible_universes(manifests, *, show_validation: bool):
    """The universe manifests a dropdown should offer: all of them when the validation
    toggle is ON, else only the non-validation (scoreboard) universes."""
    return [u for u in manifests
            if show_validation or not is_validation_universe(u)]


def visible_rank_strategies(strategies, *, show_validation: bool):
    """The rank strategies a dropdown should offer: all when the toggle is ON, else only
    the non-hidden (live) strategies. Accepts loaded strategies or StrategyInfos."""
    return [s for s in strategies
            if show_validation or not is_hidden_strategy(s)]


def universe_label(u) -> str:
    """Friendly dropdown label for a universe manifest — its ``display_name`` if set,
    else the bare id (a graceful fallback that never crashes on an un-named manifest)."""
    return (getattr(u, "display_name", "") or "").strip() or u.id


def universe_role(u) -> str:
    """Optional one-line role caption for a universe (empty when unset)."""
    return (getattr(u, "role", "") or "").strip()


def strategy_label(s) -> str:
    """Friendly dropdown label for a strategy — ``display_name`` if set, else the
    strategy's ``name``, else its id."""
    return ((getattr(s, "display_name", "") or "").strip()
            or getattr(s, "name", "") or s.id)


def strategy_role(s) -> str:
    """Optional one-line role caption for a strategy (empty when unset)."""
    return (getattr(s, "role", "") or "").strip()
