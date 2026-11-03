"""The gate definition: ``gates/absolute_gate_v2.yaml`` loaded into a frozen object.

The file is versioned data, immutable once frozen (a change is a new ``absolute_gate_v<n+1>.yaml``, the
same rule as strategy files). ``absolute_gate_v1.yaml`` stays on disk unchanged as the record of what
version 1 measured; this loader reads version 2 only and refuses version 1 by name.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

_ROOT = Path(__file__).resolve().parents[3]
GATES_DIR = _ROOT / "gates"
DEFAULT_GATE_ID = "absolute_gate_v2"
SUPPORTED_VERSION = 2


class GateDefinitionError(ValueError):
    """The gate file is missing a required threshold, holds a value of the wrong kind, or is a version
    this engine does not evaluate."""


@dataclass(frozen=True)
class GateDefinition:
    id: str
    version: int
    status: str                         # "frozen"
    accrual_exclude_above: float
    accrual_boundary_status: str
    solvency_exclude_below: float       # cash flow before working capital / total debt
    f_exclude_at_or_below: int
    f_required_checks: int
    runway_exclude_below_months: float
    runway_warn_below_months: float
    leverage_warn_above: float          # net debt / EBITDA
    leverage_cash_flow_over_debt_below: float
    z_warn_below: float
    z_grey_to: float
    cover_not_covered_below: float
    cover_thin_below: float
    cover_years: int
    intensity_warn_above: Optional[float] = None
    not_covered_industries: tuple = ()
    calibration: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def frozen(self) -> bool:
        return self.status == "frozen"


def _need(block: dict, key: str, where: str, kind=float):
    if not isinstance(block, dict) or key not in block or block[key] is None:
        raise GateDefinitionError(f"gate file: {where}.{key} is required")
    v = block[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise GateDefinitionError(f"gate file: {where}.{key} must be a number, got {v!r}")
    return kind(v)


def parse_gate(doc: dict) -> GateDefinition:
    if not isinstance(doc, dict):
        raise GateDefinitionError("gate file is not a mapping")
    version = int(doc.get("version") or 0)
    if version != SUPPORTED_VERSION:
        raise GateDefinitionError(
            f"gate file {doc.get('id')!r} is version {version}; this engine evaluates version "
            f"{SUPPORTED_VERSION} (version 1 is kept as a record and is superseded)")
    rules, warns = doc.get("rules") or {}, doc.get("warnings") or {}
    eq, sol = rules.get("earnings_quality") or {}, rules.get("solvency") or {}
    acc, run = rules.get("accounting_health") or {}, rules.get("cash_runway") or {}
    lev, dist = warns.get("highly_leveraged") or {}, warns.get("distress_score") or {}
    cov = warns.get("dividend_cover") or {}
    intensity = (doc.get("investment_intensity") or {}).get("warn_above")
    return GateDefinition(
        id=str(doc.get("id") or ""), version=version, status=str(doc.get("status") or ""),
        accrual_exclude_above=_need(eq, "exclude_above", "rules.earnings_quality"),
        accrual_boundary_status=str(eq.get("boundary_status") or ""),
        solvency_exclude_below=_need(sol, "exclude_below", "rules.solvency"),
        f_exclude_at_or_below=_need(acc, "exclude_at_or_below", "rules.accounting_health", int),
        f_required_checks=_need(acc, "required_checks", "rules.accounting_health", int),
        runway_exclude_below_months=_need(run, "exclude_below_months", "rules.cash_runway"),
        runway_warn_below_months=_need(run, "warn_below_months", "rules.cash_runway"),
        leverage_warn_above=_need(lev, "net_debt_to_ebitda_above", "warnings.highly_leveraged"),
        leverage_cash_flow_over_debt_below=_need(lev, "cash_flow_over_debt_below", "warnings.highly_leveraged"),
        z_warn_below=_need(dist, "warn_below", "warnings.distress_score"),
        z_grey_to=_need(dist, "grey_to", "warnings.distress_score"),
        cover_not_covered_below=_need(cov, "not_covered_below", "warnings.dividend_cover"),
        cover_thin_below=_need(cov, "thin_below", "warnings.dividend_cover"),
        cover_years=_need(cov, "years", "warnings.dividend_cover", int),
        intensity_warn_above=(float(intensity) if isinstance(intensity, (int, float))
                              and not isinstance(intensity, bool) else None),
        not_covered_industries=tuple(doc.get("not_covered_industries") or ()),
        calibration=dict(doc.get("calibration") or {}), raw=doc)


def load_gate(gate_id: str = DEFAULT_GATE_ID, gates_dir: Path | str | None = None) -> GateDefinition:
    path = Path(gates_dir or GATES_DIR) / f"{gate_id}.yaml"
    if not path.exists():
        raise GateDefinitionError(f"gate file not found: {path}")
    return parse_gate(yaml.safe_load(path.read_text(encoding="utf-8")))
