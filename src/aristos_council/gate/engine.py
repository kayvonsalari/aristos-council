"""The exclusion gate engine (ABSOLUTE-GATE-1, docs/DESIGN_absolute.md section b).

Pure: ``evaluate_gate(fundamentals, definition) -> GateResult``. No model, no network, no vote. All
arithmetic is in ``tools.gate_readings``; this module only compares each reading with the thresholds
of the gate definition and combines the results.

Result vocabulary for one rule:
  pass           the rule was evaluated and the company is inside the limit
  warn           a warning fired (the two warnings, and the runway band between 12 and 24 months);
                 never changes the state
  fail           an EXCLUSION rule was evaluated and the company is outside the limit: excludes
  not_evaluated  an input was missing or a denominator unusable: never a pass, never a fail
  not_applicable the rule does not apply to this company (no cash burn, no dividend)

State: excluded / qualifies / not_enough_data / not_covered.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..tools import gate_readings as gr
from .definition import GateDefinition

EXCLUDED, QUALIFIES = "excluded", "qualifies"
NOT_ENOUGH_DATA, NOT_COVERED = "not_enough_data", "not_covered"

PASS, WARN, FAIL = "pass", "warn", "fail"
NOT_EVALUATED, NOT_APPLICABLE = "not_evaluated", "not_applicable"

FINANCIAL_SECTORS = ("Financial Services", "Financials")      # abs_readings.is_financial_sector
NOT_COVERED_FIELD = "Fundamentals.sector"


@dataclass(frozen=True)
class RuleResult:
    key: str
    label: str
    reading: str           # what was measured, in words ("0.04", "6 of 9", "net cash")
    limit: str             # the threshold, in words
    result: str            # pass / warn / fail / not_evaluated / not_applicable
    note: str = ""
    value: Optional[float] = None      # the number behind the reading, for the JSON and the calibration

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "reading": self.reading, "limit": self.limit,
                "result": self.result, "note": self.note, "value": self.value}


@dataclass(frozen=True)
class GateResult:
    state: str
    reason: str
    rules: tuple = ()                  # the four exclusion rules
    warnings: tuple = ()               # the two warnings
    missing: tuple = ()                # inputs that stopped a "qualifies"
    debt_line: str = ""
    definition_id: str = ""

    def as_dict(self) -> dict:
        return {"definition": self.definition_id, "state": self.state, "reason": self.reason,
                "rules": [r.as_dict() for r in self.rules],
                "warnings": [w.as_dict() for w in self.warnings],
                "missing": list(self.missing), "debt_line": self.debt_line}


# --------------------------------------------------------------------------- #
# number formatting, kept in one place so every renderer says the same thing
# --------------------------------------------------------------------------- #
def _times(v: float) -> str:
    return f"{v:.1f} times"


def is_financial(f) -> bool:
    """The applicability rule the other absolute readings use (``abs_readings.is_financial_sector``):
    only a CONFIRMED financial sector counts, a missing sector never does."""
    from ..abs_readings import is_financial_sector
    return is_financial_sector(f)


# --------------------------------------------------------------------------- #
# the six rules
# --------------------------------------------------------------------------- #
def _earnings_quality(f, d: GateDefinition) -> RuleResult:
    r = gr.earnings_quality(f)
    limit = f"excluded above {d.accrual_exclude_above:g}"
    if r.value is None:
        return RuleResult("earnings_quality", "Earnings quality", "not evaluated", limit,
                          NOT_EVALUATED, r.note)
    verdict = FAIL if r.value > d.accrual_exclude_above else PASS
    return RuleResult("earnings_quality", "Earnings quality", f"{r.value:.2f} of assets", limit,
                      verdict, r.detail and f"{r.detail}", r.value)


def _distress(f, d: GateDefinition) -> RuleResult:
    r = gr.altman_z_double_prime(f)
    limit = f"excluded below {d.z_exclude_below:.2f}; grey zone to {d.z_grey_to:.2f}"
    if r.value is None:
        return RuleResult("distress", "Distress", "not evaluated", limit, NOT_EVALUATED, r.note)
    if r.value < d.z_exclude_below:
        verdict, note = FAIL, "distress zone"
    elif r.value <= d.z_grey_to:
        verdict, note = PASS, "grey zone: passes with a note"
    else:
        verdict, note = PASS, ""
    return RuleResult("distress", "Distress", f"{r.value:.2f}", limit, verdict, note, r.value)


def _accounting_health(f, d: GateDefinition) -> RuleResult:
    r = gr.accounting_health(f, required_checks=d.f_required_checks)
    limit = f"excluded at {d.f_exclude_at_or_below} or lower"
    if r.score is None:
        return RuleResult("accounting_health", "Accounting health",
                          f"{r.computable} of {d.f_required_checks} checks computable", limit,
                          NOT_EVALUATED,
                          f"counts only when all {d.f_required_checks} checks can be computed")
    verdict = FAIL if r.score <= d.f_exclude_at_or_below else PASS
    return RuleResult("accounting_health", "Accounting health", f"{r.score} of 9", limit, verdict,
                      "", float(r.score))


def _cash_runway(f, d: GateDefinition) -> RuleResult:
    r = gr.cash_runway(f)
    limit = (f"excluded under {d.runway_exclude_below_months:.0f} months; "
             f"warning to {d.runway_warn_below_months:.0f}")
    if not r.applies:
        why = "operating cash flow is not negative" if "not negative" in r.note else r.note
        if r.note in ("operating cash flow missing", "no accounts"):
            return RuleResult("cash_runway", "Cash runway", "not evaluated", limit, NOT_EVALUATED, r.note)
        return RuleResult("cash_runway", "Cash runway", "does not apply", limit, NOT_APPLICABLE, why)
    if r.months is None:
        return RuleResult("cash_runway", "Cash runway", "not evaluated", limit, NOT_EVALUATED, r.note)
    if r.months < d.runway_exclude_below_months:
        verdict, note = FAIL, ""
    elif r.months < d.runway_warn_below_months:
        verdict, note = WARN, "will need to raise money within the holding period"
    else:
        verdict, note = PASS, ""
    return RuleResult("cash_runway", "Cash runway", f"{r.months:.0f} months", limit, verdict, note,
                      r.months)


def _leverage(f, d: GateDefinition) -> RuleResult:
    r = gr.leverage(f)
    limit = f"warning above {d.leverage_warn_above:.0f} times"
    if r.value is None:
        return RuleResult("leverage", "Leverage", "not evaluated", limit, NOT_EVALUATED, r.note)
    if r.value <= 0:
        return RuleResult("leverage", "Leverage", "net cash (no debt to cover)", limit, PASS, "", r.value)
    verdict = WARN if r.value > d.leverage_warn_above else PASS
    return RuleResult("leverage", "Leverage", _times(r.value), limit, verdict, "", r.value)


def _dividend_cover(f, d: GateDefinition) -> RuleResult:
    r = gr.dividend_cover(f, years=d.cover_years)
    limit = f"not covered below {d.cover_not_covered_below:.1f}; thin to {d.cover_thin_below:.1f}"
    if not r.applies:
        if r.note in ("dividends paid missing", "no accounts"):
            return RuleResult("dividend_cover", "Dividend cover", "not evaluated", limit,
                              NOT_EVALUATED, r.note)
        return RuleResult("dividend_cover", "Dividend cover", "no dividend paid", limit,
                          NOT_APPLICABLE, r.note)
    if r.cover is None:
        return RuleResult("dividend_cover", "Dividend cover", "not evaluated", limit, NOT_EVALUATED, r.note)
    if r.cover < d.cover_not_covered_below:
        word = "not covered"
        verdict = WARN
    elif r.cover < d.cover_thin_below:
        word, verdict = "thin", WARN
    else:
        word, verdict = "covered", PASS
    return RuleResult("dividend_cover", "Dividend cover", f"{_times(r.cover)} ({word})", limit,
                      verdict, r.note, r.cover)


# --------------------------------------------------------------------------- #
# the combination
# --------------------------------------------------------------------------- #
def _debt_line(f) -> str:
    from ..abs_readings import _balance_sheet_date
    _, _, source = gr.debt_and_cash_as_counted(f)
    when = source or _balance_sheet_date(f)
    return ("Debt is counted as the tool counts it: the balance sheet's own Total Debt row "
            f"(borrowings plus the long-term operating lease line), from the {when}.")


def page_gate(f) -> Optional[GateResult]:
    """The gate for the company page: the shipped definition, evaluated over the accounts already
    fetched. None when the definition cannot be loaded (a context section, never a crash)."""
    from .definition import GateDefinitionError, load_gate
    try:
        return evaluate_gate(f, load_gate())
    except (GateDefinitionError, OSError):
        return None


def evaluate_gate(f, definition: GateDefinition) -> GateResult:
    """Run the four exclusion rules and the two warnings, then combine them.

    Any failed exclusion excludes, with the rule and its figure named. "Qualifies" needs the Z''
    score and the accrual ratio both evaluated; the F-score counts when nine checks exist and the
    runway when it applies. Otherwise: not enough data, with the missing inputs listed."""
    did = definition.id
    if f is None:
        return GateResult(NOT_ENOUGH_DATA, "Not enough data: no accounts were available.",
                          missing=("accounts",), definition_id=did)
    if is_financial(f):
        sector = getattr(f, "sector", None) or "financial sector"
        return GateResult(NOT_COVERED,
                          f"Not covered: banks and insurers are outside this gate ({sector}).",
                          definition_id=did)
    rules = (_earnings_quality(f, definition), _distress(f, definition),
             _accounting_health(f, definition), _cash_runway(f, definition))
    warnings = (_leverage(f, definition), _dividend_cover(f, definition))
    debt_line = _debt_line(f)

    failed = [r for r in rules if r.result == FAIL]
    if failed:
        parts = "; ".join(f"{r.label} ({r.reading}, {r.limit})" for r in failed)
        return GateResult(EXCLUDED, f"Excluded: {parts}.", rules, warnings, debt_line=debt_line,
                          definition_id=did)

    by_key = {r.key: r for r in rules}
    missing = []
    if by_key["earnings_quality"].result == NOT_EVALUATED:
        missing.append(f"accrual ratio ({by_key['earnings_quality'].note})")
    if by_key["distress"].result == NOT_EVALUATED:
        missing.append(f"Z'' score ({by_key['distress'].note})")
    if missing:
        return GateResult(NOT_ENOUGH_DATA, "Not enough data: " + "; ".join(missing) + ".",
                          rules, warnings, tuple(missing), debt_line, did)
    fired = [w for w in warnings if w.result == WARN] + [r for r in rules if r.result == WARN]
    tail = (" Warnings: " + ", ".join(w.label.lower() for w in fired) + ".") if fired else " No warnings."
    return GateResult(QUALIFIES, "Qualifies: no exclusion rule failed." + tail, rules, warnings,
                      debt_line=debt_line, definition_id=did)
