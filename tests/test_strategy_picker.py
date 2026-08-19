"""ONE strategy picker (FUND-UI-2) — the pure selection logic every surface shares.

Pins the two things the duplicated inline pickers got wrong:
- a label shared by two strategies (GARP v1/v2) resolved to the FIRST one, so picking
  v2 silently ran v1;
- each surface had its own visibility filter, ordering and default, so a fix to one
  (STRAT-PICKER-1 touched only the Universe Run picker) left the other behind.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aristos_council.strategy.picker import (
    DEFAULT_ID,
    choice_labels,
    default_index,
    resolve,
    resolve_all,
    strategy_choices,
)
from aristos_council.strategy.rank_loader import load_rank_strategy

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"


@dataclass
class FakeStrategy:
    id: str
    display_name: str = ""
    name: str = ""
    ui: str = ""


def _live(sid: str):
    return load_rank_strategy(STRAT_DIR / f"{sid}.yaml")


# --------------------------------------------------------------------------- #
# Visibility — the ONLY filter (a ticker list never makes a strategy unofferable)
# --------------------------------------------------------------------------- #
def test_hidden_strategies_are_offered_only_under_the_validation_toggle():
    strategies = [FakeStrategy("live_v1", "Live"), FakeStrategy("old_v1", "Old", ui="hidden")]
    off = [c.id for c in strategy_choices(strategies, show_validation=False)]
    on = [c.id for c in strategy_choices(strategies, show_validation=True)]
    assert off == ["live_v1"]
    assert set(on) == {"live_v1", "old_v1"}


def test_every_visible_strategy_is_offered_regardless_of_the_ticker_list():
    # FUND-UI-2: no per-section "relevant strategies" filtering — the picker offers the
    # whole visible set, and the run-time gates exclude individual names honestly.
    strategies = [FakeStrategy(f"s{i}_v1", f"S{i}") for i in range(5)]
    assert len(strategy_choices(strategies)) == 5


# --------------------------------------------------------------------------- #
# Labels — a label names exactly ONE strategy
# --------------------------------------------------------------------------- #
def test_colliding_display_names_are_disambiguated_by_id():
    a = FakeStrategy("growth_garp_v1", "Growth at a Reasonable Price (GARP)")
    b = FakeStrategy("growth_garp_v2", "Growth at a Reasonable Price (GARP)")
    choices = strategy_choices([a, b], show_validation=True)
    labels = choice_labels(choices)
    assert len(set(labels)) == 2
    assert all("growth_garp_v" in lbl for lbl in labels)
    # ...and each label resolves to ITS OWN strategy (the old index() lookup returned
    # the first match for both, so picking v2 ran v1).
    assert resolve(choices, labels[0]).id != resolve(choices, labels[1]).id


def test_unique_display_names_are_left_alone():
    choices = strategy_choices([FakeStrategy("a_v1", "Alpha"), FakeStrategy("b_v1", "Beta")])
    assert choice_labels(choices) == ["Alpha", "Beta"]


def test_label_falls_back_to_name_then_id():
    choices = strategy_choices([FakeStrategy("only_id_v1"),
                                FakeStrategy("named_v1", name="Named")])
    assert set(choice_labels(choices)) == {"only_id_v1", "Named"}


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #
def test_resolve_returns_none_for_an_unknown_label():
    choices = strategy_choices([FakeStrategy("a_v1", "Alpha")])
    assert resolve(choices, "Gone") is None


def test_resolve_all_returns_offer_order_not_click_order():
    choices = strategy_choices([FakeStrategy("a_v1", "Alpha"), FakeStrategy("b_v1", "Beta")])
    picked = resolve_all(choices, ["Beta", "Alpha"])
    assert [s.id for s in picked] == ["a_v1", "b_v1"]        # offer order — reproducible


def test_resolve_all_drops_unknown_labels():
    choices = strategy_choices([FakeStrategy("a_v1", "Alpha")])
    assert [s.id for s in resolve_all(choices, ["Alpha", "Ghost"])] == ["a_v1"]


# --------------------------------------------------------------------------- #
# Ordering + default
# --------------------------------------------------------------------------- #
def test_priority_ids_lead_then_id_order():
    strategies = [FakeStrategy("zzz_v1", "Z"), FakeStrategy("conservative_plus_v1", "C"),
                  FakeStrategy("aaa_v1", "A"),
                  FakeStrategy("magic_formula_momentum_v1", "M")]
    assert [c.id for c in strategy_choices(strategies)] == [
        "magic_formula_momentum_v1", "conservative_plus_v1", "aaa_v1", "zzz_v1"]


def test_default_index_prefers_the_flagship_and_never_goes_out_of_range():
    choices = strategy_choices([FakeStrategy("aaa_v1", "A"),
                                FakeStrategy(DEFAULT_ID, "Flagship")])
    assert choices[default_index(choices)].id == DEFAULT_ID
    assert default_index(strategy_choices([FakeStrategy("aaa_v1", "A")])) == 0
    assert default_index([]) == 0


# --------------------------------------------------------------------------- #
# Over the LIVE strategies dir — the picker both surfaces actually get
# --------------------------------------------------------------------------- #
def test_live_rank_strategies_produce_unique_labels_under_the_validation_toggle():
    from aristos_council.strategy.discovery import rank_strategies

    live = [_live(info.id) for info in rank_strategies(STRAT_DIR)]
    labels = choice_labels(strategy_choices(live, show_validation=True))
    assert len(labels) == len(set(labels))          # incl. the GARP v1/v2 collision
    assert len(labels) == len(live)
