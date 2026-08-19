"""ONE strategy picker (FUND-UI-2).

Council Station had grown two independent picker implementations — the Universe Run
tab's and Company Check's — each re-doing the same three steps with its own ordering and
its own default: drop the ``ui: hidden`` configs, order what is left, map the chosen
LABEL back to a strategy. STRAT-PICKER-1 touched only the first of them (it added a
cohort-scope caption to the Universe Run picker and left Company Check's untouched),
which is exactly the drift this module ends: one implementation, used by every surface
that offers strategies.

The label->strategy mapping is the part that was quietly WRONG. Two strategies may share
a ``display_name`` (``growth_garp_v1`` and ``growth_garp_v2`` both read "Growth at a
Reasonable Price (GARP)"), and the old ``labels.index(choice)`` lookup resolves both to
the FIRST match — picking GARP v2 silently ran v1. A colliding label is disambiguated
here with the strategy's id, so a label always names exactly one strategy.

What this module deliberately does NOT do is filter by "relevance": every strategy the
caller hands it is offered for any ticker list (FUND-UI-2 — one picker, all compatible
strategies). Visibility (``ui: hidden``, revealed by the validation toggle) is the only
filter, and that is a property of the CONFIG, not of the list being graded.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from ..demo_surface import is_hidden_strategy, strategy_label

# Presentation order for the ids we have an opinion about: the flagship first, then the
# defensive lens, then the audited baseline; every other strategy follows in id order.
# Same key the Universe Run tab used inline before this module existed — moved, not
# changed, so the dropdown a user knows keeps its order.
PRIORITY_IDS: tuple[str, ...] = (
    "magic_formula_momentum_v1", "conservative_plus_v1", "magic_formula_v1")

# The strategy a surface pre-selects when it has no better idea (the flagship).
DEFAULT_ID = "magic_formula_momentum_v1"


@dataclass(frozen=True)
class StrategyChoice:
    """One offered strategy: the label the user sees and the strategy it resolves to."""

    label: str
    strategy: object

    @property
    def id(self) -> str:
        return getattr(self.strategy, "id", "")


def strategy_choices(strategies: Iterable,
                     *, show_validation: bool = False) -> list[StrategyChoice]:
    """Every strategy a picker should offer, ordered, with unambiguous labels.

    ``show_validation`` is the app's validation/legacy toggle: OFF hides the ``ui:
    hidden`` configs (superseded/baseline), ON reveals them. Nothing else is ever
    filtered out — a ticker list does not make a strategy unofferable.
    """
    visible = [s for s in strategies if show_validation or not is_hidden_strategy(s)]
    ordered = sorted(visible, key=_order_key)
    return [StrategyChoice(label=label, strategy=s)
            for s, label in zip(ordered, _unique_labels(ordered))]


def choice_labels(choices: Sequence[StrategyChoice]) -> list[str]:
    """The labels, in offer order — what a selectbox/multiselect is fed."""
    return [c.label for c in choices]


def resolve(choices: Sequence[StrategyChoice], label: str):
    """The strategy behind a chosen label, or ``None`` when the label is unknown (a
    stale widget value after the offered set changed — never a crash, never a silent
    wrong strategy)."""
    for c in choices:
        if c.label == label:
            return c.strategy
    return None


def resolve_all(choices: Sequence[StrategyChoice], labels: Iterable[str]) -> list:
    """Several chosen labels -> strategies, in OFFER order (not click order), so a
    multi-strategy run is reproducible regardless of the order the boxes were ticked.
    Unknown labels are dropped."""
    chosen = set(labels)
    return [c.strategy for c in choices if c.label in chosen]


def selected_labels(primary: Optional[str],
                    extras: Sequence[tuple[str, bool]] = ()) -> list[str]:
    """The labels a run grades: the PRIMARY (narrated) one first, then every ticked extra.

    FUND-UI-2 item 5 splits the Run tab's one multiselect into a required primary dropdown
    (narration is single-strategy, so the narrated strategy must be explicit rather than
    "whichever selection came first in offer order") plus one checkbox per extra lens. This
    is the pure join of those two widgets, and it returns exactly what the multiselect's
    value used to be for the same chosen set — ``resolve_all`` still puts the strategies in
    offer order, so the combined grid is unchanged. The primary is never double-counted,
    even if its own (now hidden) box is stale-ticked.
    """
    picked = [primary] if primary else []
    picked += [label for label, on in extras if on and label != primary]
    return picked


def default_index(choices: Sequence[StrategyChoice],
                  preferred_id: Optional[str] = DEFAULT_ID) -> int:
    """Index of the strategy a surface should pre-select: ``preferred_id`` when it is
    offered, else 0 (never out of range, so an empty/rearranged set cannot raise)."""
    for i, c in enumerate(choices):
        if c.id == preferred_id:
            return i
    return 0


def _order_key(strategy) -> tuple[int, str]:
    sid = getattr(strategy, "id", "")
    rank = PRIORITY_IDS.index(sid) if sid in PRIORITY_IDS else len(PRIORITY_IDS)
    return (rank, sid)


def _unique_labels(strategies: Sequence) -> list[str]:
    """Friendly labels, with the id appended ONLY where two strategies would otherwise
    share one. The id is the stable record key, so the disambiguated label still says
    exactly which config runs."""
    base = [strategy_label(s) for s in strategies]
    times = Counter(base)
    return [b if times[b] == 1 else f"{b} ({getattr(s, 'id', '')})"
            for s, b in zip(strategies, base)]
