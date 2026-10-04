"""JARGON-UI-1 (Batch 17 item 7): record keys are for the record, not for a reader."""
from __future__ import annotations

from aristos_council.ui_text import reader_text


def test_strategy_ids_in_brackets_and_backticks_and_adhoc_fingerprints_are_removed():
    assert reader_text("Value + Momentum (magic_formula_momentum_v1)") == "Value + Momentum"
    assert reader_text("Lenses: Value + Momentum (magic_formula_momentum_v1); "
                       "Magic Formula RAW (magic_formula_raw_v1)") == (
        "Lenses: Value + Momentum; Magic Formula RAW")
    assert reader_text("`forensic_v1` · earnings quality") == " · earnings quality"
    assert reader_text("adhoc:2523dc81 - 2 lenses x 6 names") == "your list - 2 lenses x 6 names"
    assert reader_text("Running Growth on adhoc:ab12cd34 in ranker-only.") == (
        "Running Growth on your list in ranker-only.")


def test_ordinary_text_and_parentheses_are_left_alone():
    for text in ("Ford Motor Company (F)", "5 names (2 excluded)", "ranked 3rd of 14",
                 "growth_garp is not an id without a version", ""):
        assert reader_text(text) == text


def test_the_validation_toggle_keeps_everything():
    text = "Value + Momentum (magic_formula_momentum_v1) on adhoc:2523dc81"
    assert reader_text(text, show_ids=True) == text
    assert reader_text(None) is None
