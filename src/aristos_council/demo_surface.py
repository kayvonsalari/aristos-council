"""Demo-surface presentation — friendly labels for a clean, professional UI.

The technical `id` (``growth_40_v1``, ``magic_formula_momentum_v1``) is the STABLE
record key: it names persisted reports, snapshot-CSV rows, and verdict history, so it is
NEVER renamed. This module decides only what the USER SEES — a friendly `display_name`
in dropdowns, with the id relegated to a small caption — so no underscores or ``_v1``
leak into the presentation while the record stays byte-stable underneath.

Pure functions (no Streamlit), so the labelling is unit-tested directly.
"""

from __future__ import annotations


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
