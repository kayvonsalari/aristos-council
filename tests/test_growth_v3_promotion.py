"""GROWTH-V3-PROMOTION-1 (2026-10-21): v3 replaces v2 in the picker by FLAG-ONLY edits.

CLAUDE.md rule 7: display and status fields (``ui``, ``status``, ``asks``, ``display_name``,
``description``) are not part of a strategy's immutable definition; changing them is allowed when a test
pins every verdict-affecting field unchanged. This is that test: the fixture is both strategy files and the
two screen lenses they reference, captured from main before the promotion; everything except those fields
must be identical. v3's caption was then corrected by an owner-approved text edit (GROWTH-V3-CAPTION-1).
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FLAGS = {"ui", "status", "asks", "display_name", "description"}
PINNED = json.loads((ROOT / "tests" / "fixtures" / "growth_v2_v3_definitions_pre_promotion.json")
                    .read_text(encoding="utf-8"))


def _load(name: str) -> dict:
    return yaml.safe_load((ROOT / "strategies" / f"{name}.yaml").read_text(encoding="utf-8"))


def _without_flags(doc: dict) -> dict:
    return json.loads(json.dumps({k: v for k, v in doc.items() if k not in FLAGS}, default=str,
                                 sort_keys=True))


def test_every_field_but_the_display_and_status_fields_is_identical_to_main_before_the_promotion():
    assert set(PINNED) == {"growth_garp_v2", "growth_screen_v2", "growth_garp_v3", "growth_screen_v3"}
    for name, before in PINNED.items():
        assert _without_flags(_load(name)) == _without_flags(before), name


def test_the_verdict_affecting_fields_are_named_and_unchanged():
    v2, v3 = _load("growth_garp_v2"), _load("growth_garp_v3")
    for doc, name in ((v2, "growth_garp_v2"), (v3, "growth_garp_v3")):
        for key in ("factors", "council_screen_strategy"):
            assert doc.get(key) == PINNED[name].get(key), (name, key)
    # v3's gate and floors: a BUY cap on revenue growth, and the screen's absolute floors
    assert v3["buy_requires"] == PINNED["growth_garp_v3"]["buy_requires"] == {"revenue_growth": "top_half"}
    assert _load("growth_screen_v3")["criteria"] == PINNED["growth_screen_v3"]["criteria"]
    assert _load("growth_screen_v2")["criteria"] == PINNED["growth_screen_v2"]["criteria"]


def test_only_the_flags_moved_v3_is_offered_v2_is_hidden():
    v2, v3 = _load("growth_garp_v2"), _load("growth_garp_v3")
    assert v3.get("ui") != "hidden" and not v3.get("status")
    assert v2.get("ui") == "hidden" and not v2.get("status")
    import sys
    sys.path.insert(0, str(ROOT))
    from app import list_rank_strategy_options
    from aristos_council.strategy.picker import strategy_choices
    offered = [c.id for c in strategy_choices([o[2] for o in list_rank_strategy_options(ROOT / "strategies")])]
    assert "growth_garp_v3" in offered and "growth_garp_v2" not in offered


def test_v3_badges_now_come_from_its_committed_csvs():
    from aristos_council.backtest import _lens_status, track_record
    assert _lens_status("growth_garp_v3") == ""
    badges = {slug: track_record(slug, "growth_garp_v3") for slug in (
        "comms_media_entertainment", "industrials_aerospace_defense_europe_us", "tech_it_services")}
    assert badges["comms_media_entertainment"].label == "passed the test here"
    assert badges["industrials_aerospace_defense_europe_us"].label == "passed the test here"
    assert badges["tech_it_services"].label != "under test here"
    assert all(b.label != "under test here" for b in badges.values())


V3_CAPTION = (
    "Growing faster than most of its industry at a fair price: companies worth at least $5bn whose sales are "
    "still growing (above 0% a year) and that earn a positive return on capital, not banks or insurers; "
    "ranked on sales growth, return on capital, price and trend; a BUY needs sales growth above the "
    "median of the companies it is ranked against (the list it runs on, or its peer group).")


def test_v3_caption_is_the_approved_text_and_v2_display_text_is_untouched():
    """GROWTH-V3-CAPTION-1 (2026-10-21) and GROWTH-CAP-WORDING-1 (2026-10-22, owner-approved on PR #193: the
    median is of the list or the peer group, not "the industry"): only v3's ``asks`` changed; every verdict-affecting
    field is pinned above, and v2's display text is byte-for-byte what it was."""
    assert " ".join(_load("growth_garp_v3")["asks"].split()) == V3_CAPTION
    v2 = _load("growth_garp_v2")
    for key in ("asks", "display_name", "description"):
        assert json.loads(json.dumps(v2.get(key), default=str)) == PINNED["growth_garp_v2"].get(key), key
