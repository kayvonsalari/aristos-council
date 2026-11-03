"""The valuation definition: ``valuation/absolute_value_v1.yaml`` loaded into a frozen object.

Versioned data, immutable once merged (a change is ``absolute_value_v2.yaml``). The investment-intensity
boundary is NOT repeated here: it was measured at gate calibration and is read from the gate file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[3]
VALUATION_DIR = _ROOT / "valuation"
DEFAULT_ID = "absolute_value_v1"


class ValueDefinitionError(ValueError):
    """The valuation file is missing a number or holds a value of the wrong kind."""


@dataclass(frozen=True)
class ValueDefinition:
    id: str
    version: int
    status: str
    average_years: int
    margin_points: float
    staleness_days: int
    bond_series_file: str
    horizon_years: int
    implied_growth_range: tuple
    return_range: tuple
    iterations: int
    own_windows: tuple                   # (5, 10)
    fast_years: int
    high_growth_mark: float
    verdict_states: tuple
    raw: dict = field(default_factory=dict)

    @property
    def margin(self) -> float:
        """The hurdle margin as a fraction (5.0 points -> 0.05)."""
        return self.margin_points / 100.0


def _num(block, key, where, kind=float):
    if not isinstance(block, dict) or block.get(key) is None:
        raise ValueDefinitionError(f"valuation file: {where}.{key} is required")
    v = block[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueDefinitionError(f"valuation file: {where}.{key} must be a number, got {v!r}")
    return kind(v)


def _pair(block, key, where) -> tuple:
    v = (block or {}).get(key)
    if (not isinstance(v, (list, tuple)) or len(v) != 2
            or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in v) or v[0] >= v[1]):
        raise ValueDefinitionError(f"valuation file: {where}.{key} must be [low, high], got {v!r}")
    return (float(v[0]), float(v[1]))


def parse_definition(doc: dict) -> ValueDefinition:
    if not isinstance(doc, dict):
        raise ValueDefinitionError("valuation file is not a mapping")
    base, hurdle = doc.get("base") or {}, doc.get("hurdle") or {}
    solver, cases = doc.get("solver") or {}, doc.get("cases") or {}
    if base.get("rule") != "lower_of_latest_and_three_year_average":
        raise ValueDefinitionError("valuation file: base.rule must be lower_of_latest_and_three_year_average")
    return ValueDefinition(
        id=str(doc.get("id") or ""), version=int(doc.get("version") or 0), status=str(doc.get("status") or ""),
        average_years=_num(base, "average_years", "base", int),
        margin_points=_num(hurdle, "margin_points", "hurdle"),
        staleness_days=_num(hurdle, "staleness_days", "hurdle", int),
        bond_series_file=str(hurdle.get("bond_series_file") or ""),
        horizon_years=_num(solver, "horizon_years", "solver", int),
        implied_growth_range=_pair(solver, "implied_growth_range", "solver"),
        return_range=_pair(solver, "return_range", "solver"),
        iterations=_num(solver, "iterations", "solver", int),
        own_windows=(_num(cases.get("own_5_year"), "window", "cases.own_5_year", int),
                     _num(cases.get("own_10_year"), "window", "cases.own_10_year", int)),
        fast_years=_num(cases.get("downside"), "fast_years", "cases.downside", int),
        high_growth_mark=_num(doc, "high_growth_mark", "root"),
        verdict_states=tuple(doc.get("verdict_states") or ()), raw=doc)


def load_definition(definition_id: str = DEFAULT_ID, directory: Path | str | None = None) -> ValueDefinition:
    path = Path(directory or VALUATION_DIR) / f"{definition_id}.yaml"
    if not path.exists():
        raise ValueDefinitionError(f"valuation file not found: {path}")
    return parse_definition(yaml.safe_load(path.read_text(encoding="utf-8")))
