"""Demo-surface presentation — friendly labels for a clean, professional UI.

The technical `id` (``growth_40_v1``, ``magic_formula_momentum_v1``) is the STABLE
record key: it names persisted reports, snapshot-CSV rows, and verdict history, so it is
NEVER renamed. This module decides only what the USER SEES — a friendly `display_name`
in dropdowns, with the id relegated to a small caption — so no underscores or ``_v1``
leak into the presentation while the record stays byte-stable underneath.

Pure functions (no Streamlit), so the labelling is unit-tested directly.
"""

from __future__ import annotations

# Validation / baseline assets — HIDDEN from the default demo surface, revealed only by
# the "Show validation & legacy tools" toggle. They remain fully functional (the baseline
# side-by-side is a demo exhibit one toggle-flip away, NOT deleted). Ids, not display
# names, so the gate keys off the stable record key.
_VALIDATION_UNIVERSE_IDS = frozenset({
    "defensive_16_v1",        # the known-trap bench
    "energy_watch_v1",        # cyclical-peak OBSERVATION universe (not a scoreboard one)
})
_VALIDATION_STRATEGY_IDS = frozenset({"magic_formula_v1"})    # the Classic Value baseline


def is_validation_universe(universe_id: str) -> bool:
    """True for a universe shown only behind the validation toggle (the trap bench)."""
    return universe_id in _VALIDATION_UNIVERSE_IDS


def is_validation_strategy(strategy_id: str) -> bool:
    """True for a strategy shown only behind the validation toggle (the baseline)."""
    return strategy_id in _VALIDATION_STRATEGY_IDS


def visible_universes(manifests, *, show_validation: bool):
    """The universe manifests a dropdown should offer: all of them when the validation
    toggle is ON, else only the non-validation (scoreboard) universes."""
    return [u for u in manifests
            if show_validation or not is_validation_universe(u.id)]


def visible_rank_strategies(strategies, *, show_validation: bool):
    """The rank strategies a dropdown should offer: all when the toggle is ON, else only
    the live (non-baseline) strategies. Accepts objects with a ``.id``."""
    return [s for s in strategies
            if show_validation or not is_validation_strategy(s.id)]


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
