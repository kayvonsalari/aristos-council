"""ETF field-coverage probe (ETF-1 ITEM 1) — the DECISION LOGIC, pure and testable.

The probe answers ONE question before the ETF lenses are built: for each candidate
factor field, is it computable for ENOUGH of a universe's lines to be a v1 factor?
The rule (applied, never asked): a field is IN a lens if it is present for >= 80% of
that universe's lines; below that it is DROPPED for v1 and the gap listed in the PR.

The fetching lives in ``examples/etf_coverage_probe.py`` (it needs the network); this
module holds only the pure presence/decision math so it is unit-testable offline with
mocked rows — the SAME discipline as the rest of the codebase (arithmetic in a
deterministic, tested place, never ad-hoc in a script).
"""

from __future__ import annotations

from dataclasses import dataclass

# The candidate fields the probe measures, in report order. Each maps to a Fundamentals
# attribute (or, for price history, a derived count handled by the caller).
PROBE_FIELDS: tuple[str, ...] = (
    "net_expense_ratio",   # expense ratio (netExpenseRatio / annualReportExpenseRatio)
    "total_assets",        # fund size (totalAssets)
    "dividend_yield",      # distribution / dividend yield
    "quote_type",          # vendor quoteType (also the kind-gate signal)
    "price_history_12m",   # >= 12m of price closes
)

# A field is IN a lens when present for at least this fraction of the universe's lines.
COVERAGE_THRESHOLD = 0.80

# 12m of trading days is ~252; require a comfortable floor so a truncated history
# (a newly-listed fund) reads as absent rather than silently short.
MIN_12M_CLOSES = 200


@dataclass(frozen=True)
class FieldCoverage:
    field: str
    present: int
    total: int
    decision: str          # "IN" | "OUT"

    @property
    def fraction(self) -> float:
        return (self.present / self.total) if self.total else 0.0


def value_present(value) -> bool:
    """Is a probed scalar PRESENT? None is absent; a numeric 0.0 is PRESENT (a real
    determination — a genuine 0% expense ratio would count), and a non-empty string
    (quote_type) is present. Empty string / None are absent."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True


def price_history_present(n_closes: int) -> bool:
    """>= 12m of closes available (a truncated series reads as absent)."""
    return n_closes >= MIN_12M_CLOSES


def coverage_decision(
    rows: list[dict], *, threshold: float = COVERAGE_THRESHOLD,
) -> list[FieldCoverage]:
    """Per-field coverage + IN/OUT decision over a universe's probe rows.

    Each row is ``{field: value, ..., "price_history_12m": <n_closes:int>}``. A field
    is IN iff present for >= ``threshold`` of the rows; a universe of zero rows yields
    all-OUT (no coverage to stand on). Deterministic — the SAME ≥80% rule the PR reports.
    """
    total = len(rows)
    out: list[FieldCoverage] = []
    for field in PROBE_FIELDS:
        if field == "price_history_12m":
            present = sum(1 for r in rows if price_history_present(int(r.get(field, 0))))
        else:
            present = sum(1 for r in rows if value_present(r.get(field)))
        decision = "IN" if (total and present / total >= threshold) else "OUT"
        out.append(FieldCoverage(field=field, present=present, total=total,
                                 decision=decision))
    return out


def format_coverage_table(label: str, coverage: list[FieldCoverage]) -> str:
    """A markdown coverage table for one universe — the block the PR carries."""
    lines = [f"### {label}", "",
             "| field | present | total | coverage | decision |",
             "|---|---|---|---|---|"]
    for c in coverage:
        lines.append(f"| {c.field} | {c.present} | {c.total} | "
                     f"{c.fraction:.0%} | {c.decision} |")
    return "\n".join(lines)
