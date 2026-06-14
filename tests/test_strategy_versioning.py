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


def _criteria_dicts(strategy: Strategy) -> list[dict]:
    return [{"name": c.name, "threshold": c.threshold,
             "unverifiable_blocks": c.unverifiable_blocks}
            for c in strategy.criteria]


def _with_yield(strategy: Strategy, threshold: float) -> list[dict]:
    crit = _criteria_dicts(strategy)
    for c in crit:
        if c["name"] == "min_dividend_yield":
            c["threshold"] = threshold
    return crit


def test_bump_version_increments_id_and_number():
    new_id, new_version = bump_version(_base())
    assert new_id == "dividend_aristocrats_v2"
    assert new_version == 2


def test_make_new_version_applies_edits_and_bumps():
    base = _base()
    new = make_new_version(
        base,
        {
            "criteria": _with_yield(base, 0.03),
            "policy": {"partial_pass_allows_hold": False},
            "veto": {"min_confidence": 0.7},
        },
    )
    assert new.id == "dividend_aristocrats_v2"
    assert new.version == 2
    by = {c.name: c for c in new.criteria}
    # edited fields changed...
    assert by["min_dividend_yield"].threshold == 0.03
    assert new.policy.partial_pass_allows_hold is False
    assert new.veto.min_confidence == 0.7
    # ...untouched criteria preserved
    assert by["min_dividend_growth_streak"].threshold == 25
    assert by["min_dividend_growth_streak"].unverifiable_blocks is True


def test_make_new_version_ignores_attempts_to_set_id_or_version():
    new = make_new_version(_base(), {"id": "evil_v9", "version": 99})
    assert new.id == "dividend_aristocrats_v2"
    assert new.version == 2


def test_make_new_version_rejects_out_of_range_edit():
    # min_dividend_yield must be <= 1.0 (registry bound) — validated up front,
    # before any file is written.
    base = _base()
    with pytest.raises(ValidationError):
        make_new_version(base, {"criteria": _with_yield(base, 1.5)})


def test_save_writes_new_versioned_file_loadable_by_loader(tmp_path):
    base = _base()
    new = make_new_version(base, {"criteria": _with_yield(base, 0.03)})
    path = save_strategy(new, tmp_path)
    assert path == tmp_path / "dividend_aristocrats_v2.yaml"
    assert path.exists()
    # the canonical proof: the existing loader accepts it and round-trips
    reloaded = load_strategy(path)
    assert reloaded == new
    assert {c.name: c.threshold for c in reloaded.criteria}[
        "min_dividend_yield"] == 0.03


def test_save_refuses_to_overwrite_existing_file(tmp_path):
    new = make_new_version(_base(), {})
    save_strategy(new, tmp_path)
    sentinel = (tmp_path / "dividend_aristocrats_v2.yaml").read_text(
        encoding="utf-8")
    with pytest.raises(FileExistsError):
        save_strategy(new, tmp_path)
    assert (tmp_path / "dividend_aristocrats_v2.yaml").read_text(
        encoding="utf-8") == sentinel


def test_save_never_mutates_the_base_file(tmp_path):
    base_text = (STRATEGY_DIR / "dividend_aristocrats_v1.yaml").read_text(
        encoding="utf-8")
    (tmp_path / "dividend_aristocrats_v1.yaml").write_text(
        base_text, encoding="utf-8")
    base = load_strategy(tmp_path / "dividend_aristocrats_v1.yaml")

    save_strategy(make_new_version(base, {"criteria": _with_yield(base, 0.04)}),
                  tmp_path)

    assert (tmp_path / "dividend_aristocrats_v1.yaml").read_text(
        encoding="utf-8") == base_text


def test_saved_yaml_is_plain_mapping(tmp_path):
    new = make_new_version(_base(), {})
    path = save_strategy(new, tmp_path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    assert raw["id"] == "dividend_aristocrats_v2"
    assert raw["version"] == 2
    assert isinstance(raw["criteria"], list)
