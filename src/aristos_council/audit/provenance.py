"""Deep provenance audit: verify that every cited figure MATCHES the ledger.

The shallow check (in agents/nodes.py, at parse time) verifies that each
figure's call_id resolves to a recorded ToolCall — numbers must be *traceable*.
This module closes the remaining gap, observed seven times in live runs: an
agent can attach a VALID call_id to a MISREAD value (the canonical case: citing
``criteria[3].passed: None`` when the ledger holds ``False`` — "could not be
evaluated" claimed where the truth is "evaluated and failed").

Design decisions (June 2026, after the six-ticker test battery):

- POST-RUN, NOT INLINE. The audit runs as a graph node between decision and
  veto. Figures are kept exactly as cited (the deliberation already happened on
  them); mismatches are appended to ``state.errors`` so the existing
  DATA_QUALITY veto fires and a human reviews. We deliberately do NOT
  auto-correct: silently repairing a figure would make the figure list diverge
  from the prose thesis that quoted the wrong number, hiding the model failure
  instead of surfacing it.

- ROUNDING IS NOT A LIE. Agents legitimately cite ``0.0225`` for a ledger value
  of ``0.022474736…``. A cited number is VERIFIED if it is consistent with
  rounding or truncating the ledger value at some precision (decimal places or
  significant figures) AND within a 5% relative-error ceiling. The ceiling
  stops degenerate "roundings" (citing 0.0 for 0.022 is technically a rounding
  to zero decimals — and information-destroying, so it stays a mismatch).

- BOOLEANS ARE NUMBERS. ``False ≡ 0.0``, ``True ≡ 1.0`` — FigureRef.value is a
  float, and agents citing ``0.0`` for a failed criterion are correct.
  Citing ``None`` for a field whose ledger value is ``False`` (or any number)
  is the documented misquote class and is flagged as a MISMATCH.

- STRINGS ARE UNVERIFIABLE, NOT VIOLATIONS. Agents sometimes anchor a number
  to a headline (``get_company_news → output[20].headline``: "…$32 Million
  Verdict…" cited as 32000000.0). A float cannot be compared to prose without
  fragile extraction heuristics; these are counted as ``unverifiable`` in the
  report and do NOT fire the veto. Same for citing None against a string field
  (FigureRef.value cannot hold a string, so None is the honest encoding).

- UNIT SCALING IS ITS OWN CATEGORY. A cited value exactly ×100 or ÷100 off the
  ledger (percent vs ratio confusion) is reported as ``unit_scaled`` — visible
  in the audit, not a veto-firing violation in v1.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..state import ResearchState

# A rounding-consistent citation must also be within this relative error of the
# ledger value. Stops "round to 0 decimals" from laundering wild misquotes.
REL_ERROR_CEILING = 0.05

# Statuses a single figure can land in.
VERIFIED = "verified"
MISMATCH = "mismatch"            # violation: value disagrees with the ledger
UNRESOLVABLE = "unresolvable"    # violation: field_path doesn't exist
UNVERIFIABLE = "unverifiable"    # non-numeric field (string/struct) — no claim
UNIT_SCALED = "unit_scaled"      # exactly ×100 / ÷100 off — reported, no veto


class PathUnresolvable(Exception):
    """The field_path does not resolve against the tool output."""


# --------------------------------------------------------------------------- #
# Path resolution
# --------------------------------------------------------------------------- #
_SEGMENT = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)?(?P<idx>(\[-?\d+\])*)$")
_MISSING = object()


def _step(current: object, name: str) -> object:
    """One name lookup: dict key first, then attribute. _MISSING if neither."""
    if isinstance(current, dict):
        return current.get(name, _MISSING)
    return getattr(current, name, _MISSING)


def resolve_field_path(output: object, field_path: str) -> object:
    """Resolve a dotted/indexed path against a tool output.

    Handles every shape observed in live runs:
      ``criteria[0].observed``        dict -> list -> dict
      ``output.eps``                  'output.' alias prefix, attribute access
      ``eps``                         bare field
      ``output[-1].amount``           alias + negative index into a list root
      ``metrics.dividend_yield``      nested dicts

    'output' is tried as a literal key/attribute first; only if that fails is
    it treated as an alias for the root (agents use both conventions).
    """
    path = (field_path or "").strip()
    if not path:
        raise PathUnresolvable("empty field_path")

    current: object = output
    segments = path.split(".")
    for i, seg in enumerate(segments):
        seg = seg.strip()
        m = _SEGMENT.match(seg)
        if not m:
            raise PathUnresolvable(f"malformed segment '{seg}' in '{path}'")
        name = m.group("name")
        if name:
            nxt = _step(current, name)
            if nxt is _MISSING:
                if i == 0 and name == "output":
                    nxt = current  # alias for the root
                else:
                    raise PathUnresolvable(
                        f"'{name}' not found at segment {i} of '{path}'"
                    )
            current = nxt
        for idx_str in re.findall(r"\[(-?\d+)\]", m.group("idx") or ""):
            idx = int(idx_str)
            if not isinstance(current, (list, tuple)):
                raise PathUnresolvable(
                    f"index [{idx}] applied to non-sequence at '{seg}' in '{path}'"
                )
            try:
                current = current[idx]
            except IndexError:
                raise PathUnresolvable(
                    f"index [{idx}] out of range at '{seg}' in '{path}'"
                )
    return current


# --------------------------------------------------------------------------- #
# Value comparison
# --------------------------------------------------------------------------- #
def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)


def _rounding_consistent(cited: float, actual: float) -> bool:
    """True if `cited` is `actual` rounded/truncated at SOME sane precision."""
    if _close(cited, actual):
        return True
    # decimal-place rounding and truncation
    for p in range(0, 13):
        scale = 10.0 ** p
        if _close(round(actual, p), cited):
            return True
        if _close(math.trunc(actual * scale) / scale, cited):
            return True
    # significant-figure rounding (covers 121.3e9 for 121,317,433,344)
    if actual != 0.0:
        exponent = math.floor(math.log10(abs(actual)))
        for k in range(1, 16):
            if _close(round(actual, -exponent + k - 1), cited):
                return True
    return False


def numbers_match(cited: float, actual: float) -> bool:
    """Rounding-consistent AND within the relative-error ceiling."""
    if _close(cited, actual):
        return True
    denom = max(abs(actual), 1e-12)
    if abs(cited - actual) / denom > REL_ERROR_CEILING:
        return False
    return _rounding_consistent(cited, actual)


def _normalize(actual: object) -> object:
    """bool -> float (False≡0.0, True≡1.0); int -> float; rest unchanged."""
    if isinstance(actual, bool):
        return float(actual)
    if isinstance(actual, (int, float)):
        return float(actual)
    return actual


def compare_cited(cited: float | None, actual: object) -> tuple[str, str]:
    """Classify a cited value against the resolved ledger value.

    Returns (status, note).
    """
    norm = _normalize(actual)

    if norm is None:
        if cited is None:
            return VERIFIED, "null cited for null field"
        return MISMATCH, f"cited {cited!r} but ledger field is null"

    if isinstance(norm, float):
        if cited is None:
            # The documented misquote class: None claimed where a value
            # (often False -> 0.0) exists in the ledger.
            return MISMATCH, f"cited None but ledger holds {actual!r}"
        if numbers_match(cited, norm):
            return VERIFIED, ""
        if norm != 0.0 and (
            numbers_match(cited, norm * 100.0)
            or numbers_match(cited, norm / 100.0)
        ):
            return UNIT_SCALED, (
                f"cited {cited!r} appears ×100/÷100 off ledger {actual!r}"
            )
        return MISMATCH, f"cited {cited!r} but ledger holds {actual!r}"

    # strings, dicts, lists, dates… — not comparable to a float
    return UNVERIFIABLE, f"ledger field is {type(actual).__name__}, not numeric"


# --------------------------------------------------------------------------- #
# The audit
# --------------------------------------------------------------------------- #
@dataclass
class FigureFinding:
    source: str          # "fundamental" … "risk", "critic"
    label: str
    cited: float | None
    field_path: str
    call_id: str
    tool_name: str
    status: str
    actual: object = None
    note: str = ""

    def violation_text(self) -> str:
        return (
            f"provenance value mismatch: {self.source} cited "
            f"'{self.label}'={self.cited!r} at {self.tool_name} → "
            f"{self.field_path} (call_id {self.call_id}) — {self.note}"
        )


@dataclass
class ProvenanceAuditReport:
    findings: list[FigureFinding] = field(default_factory=list)

    def _by_status(self, status: str) -> list[FigureFinding]:
        return [f for f in self.findings if f.status == status]

    @property
    def violations(self) -> list[FigureFinding]:
        return [
            f for f in self.findings if f.status in (MISMATCH, UNRESOLVABLE)
        ]

    def summary(self) -> dict:
        return {
            "figures_audited": len(self.findings),
            "verified": len(self._by_status(VERIFIED)),
            "mismatch": len(self._by_status(MISMATCH)),
            "unresolvable": len(self._by_status(UNRESOLVABLE)),
            "unverifiable": len(self._by_status(UNVERIFIABLE)),
            "unit_scaled": len(self._by_status(UNIT_SCALED)),
            "violations": [f.violation_text() for f in self.violations],
            "unit_scaled_notes": [
                f"{f.source}: '{f.label}' — {f.note}"
                for f in self._by_status(UNIT_SCALED)
            ],
        }


def audit_provenance(state: ResearchState) -> ProvenanceAuditReport:
    """Verify every figure on the council record against the ledger."""
    report = ProvenanceAuditReport()

    sources: list[tuple[str, list]] = [
        (op.specialist.value, op.figures) for op in state.specialist_opinions
    ]
    if state.critic_report is not None:
        sources.append(("critic", state.critic_report.figures))

    for source, figures in sources:
        for fig in figures:
            call_id = fig.provenance.call_id
            path = fig.provenance.field_path
            tc = state.tool_call_by_id(call_id)
            if tc is None:
                # Shallow check should have dropped these already; belt and
                # braces in case a figure arrived by another route.
                report.findings.append(FigureFinding(
                    source=source, label=fig.label, cited=fig.value,
                    field_path=path, call_id=call_id,
                    tool_name=fig.provenance.tool_name,
                    status=UNRESOLVABLE, note="unknown call_id",
                ))
                continue
            try:
                actual = resolve_field_path(tc.output, path)
            except PathUnresolvable as exc:
                report.findings.append(FigureFinding(
                    source=source, label=fig.label, cited=fig.value,
                    field_path=path, call_id=call_id, tool_name=tc.tool_name,
                    status=UNRESOLVABLE, note=str(exc),
                ))
                continue
            status, note = compare_cited(fig.value, actual)
            report.findings.append(FigureFinding(
                source=source, label=fig.label, cited=fig.value,
                field_path=path, call_id=call_id, tool_name=tc.tool_name,
                status=status, actual=actual, note=note,
            ))
    return report


def make_audit_node():
    """Graph node: run the deep audit, persist the summary, surface violations.

    Violations land in ``state.errors`` BEFORE the veto node runs, so the
    existing DATA_QUALITY trigger fires — no new veto category needed.
    """

    def audit(state: ResearchState) -> ResearchState:
        report = audit_provenance(state)
        state.provenance_audit = report.summary()
        for f in report.violations:
            state.errors.append(f.violation_text())
        return state

    return audit
