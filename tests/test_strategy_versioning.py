"""Tests for strategy versioning — the "edit as a new version" save path.

The hard invariant: a published strategy file is NEVER mutated, because recorded
verdicts and run reports reference their strategy_id and must stay reproducible.
Editing produces a brand-new versioned file; the edited values are validated
through the same contract the loader enforces, BEFORE anything touches disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from aristos_council.strategy.loader import Strategy, load_strategy
from aristos_council.strategy.versioning import (
    bump_version,
    make_new_version,
    save_strategy,
)

STRATEGY_DIR = Path(__file__).resolve().parents[1] / "strategies"


def _base() -> Strategy:
    return load_strategy(STRATEGY_DIR / "dividend_aristocrats_v1.yaml")


def test_bump_version_increments_id_and_number():
    new_id, new_version = bump_version(_base())
    assert new_id == "dividend_aristocrats_v2"
    assert new_version == 2


def test_make_new_version_applies_edits_and_bumps():
    new = make_new_version(
        _base(),
        {
            "criteria": {"min_dividend_yield": 0.03},
            "policy": {"partial_pass_allows_hold": False},
            "veto": {"min_confidence": 0.7},
        },
    )
    assert new.id == "dividend_aristocrats_v2"
    assert new.version == 2
    # edited fields changed...
    assert new.criteria.min_dividend_yield == 0.03
    assert new.policy.partial_pass_allows_hold is False
    assert new.veto.min_confidence == 0.7
    # ...untouched fields preserved from the base
    assert new.criteria.min_dividend_growth_years == 25
    assert new.policy.unverifiable_streak_is_blocking is True


def test_make_new_version_ignores_attempts_to_set_id_or_version():
    new = make_new_version(_base(), {"id": "evil_v9", "version": 99})
    assert new.id == "dividend_aristocrats_v2"
    assert new.version == 2


def test_make_new_version_rejects_out_of_range_edit():
    # min_dividend_yield must be <= 1.0 (it's a decimal) — validated up front,
    # before any file is written.
    with pytest.raises(ValidationError):
        make_new_version(_base(), {"criteria": {"min_dividend_yield": 1.5}})


def test_save_writes_new_versioned_file_loadable_by_loader(tmp_path):
    new = make_new_version(_base(), {"criteria": {"min_dividend_yield": 0.03}})
    path = save_strategy(new, tmp_path)
    assert path == tmp_path / "dividend_aristocrats_v2.yaml"
    assert path.exists()
    # the canonical proof: the existing loader accepts it and round-trips
    reloaded = load_strategy(path)
    assert reloaded == new
    assert reloaded.criteria.min_dividend_yield == 0.03


def test_save_refuses_to_overwrite_existing_file(tmp_path):
    new = make_new_version(_base(), {})
    save_strategy(new, tmp_path)
    # a second save to the same id must refuse rather than mutate history
    sentinel = (tmp_path / "dividend_aristocrats_v2.yaml").read_text(
        encoding="utf-8")
    with pytest.raises(FileExistsError):
        save_strategy(new, tmp_path)
    # untouched
    assert (tmp_path / "dividend_aristocrats_v2.yaml").read_text(
        encoding="utf-8") == sentinel


def test_save_never_mutates_the_base_file(tmp_path):
    # copy the shipped v1 into the tmp dir, then save a v2 next to it
    base_text = (STRATEGY_DIR / "dividend_aristocrats_v1.yaml").read_text(
        encoding="utf-8")
    (tmp_path / "dividend_aristocrats_v1.yaml").write_text(
        base_text, encoding="utf-8")
    base = load_strategy(tmp_path / "dividend_aristocrats_v1.yaml")

    save_strategy(make_new_version(base, {"criteria": {"min_dividend_yield": 0.04}}),
                  tmp_path)

    # v1 on disk is byte-for-byte unchanged
    assert (tmp_path / "dividend_aristocrats_v1.yaml").read_text(
        encoding="utf-8") == base_text


def test_saved_yaml_is_plain_mapping(tmp_path):
    new = make_new_version(_base(), {})
    path = save_strategy(new, tmp_path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    assert raw["id"] == "dividend_aristocrats_v2"
    assert raw["version"] == 2
