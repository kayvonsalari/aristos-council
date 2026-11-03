"""The exclusion gate engine (ABSOLUTE-GATE-2, docs/DESIGN_absolute.md section b as amended by the owner).

Pure: ``evaluate_gate(fundamentals, definition) -> GateResult``. No model, no network, no vote. All
arithmetic is in ``tools.gate_readings``; this module only compares each reading with the thresholds of
the gate definition and combines the results.

Four exclusions: earnings quality (accrual ratio), solvency (cash flow before working capital over total
debt), accounting health (F-score), cash runway. Four warnings: highly leveraged (net debt / EBITDA or
cash flow over debt), distress score low (Altman Z''), dividend cover, runway between 12 and 24 months.

Result vocabulary for one rule:
  pass           the rule was evaluated and the company is inside the limit
  warn           a warning fired; never changes the state
  fail           an EXCLUSION rule was evaluated and the company is outside the limit: excludes
  not_evaluated  an input was missing, a denominator unusable, or (solvency) a loss-maker whose runway decides
  not_applicable the rule does not apply to this company (no cash burn, no dividend)

State: excluded / qualifies / not_enough_data / not_covered.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..tools import gate_readings as gr
from .definition import GateDefinition

EXCLUDED, QUALIFIES = "excluded", "qualifies"
NOT_ENOUGH_DATA, NOT_COVERED = "not_enough_data", "not_covered"

PASS, WARN, FAIL = "pass", "warn", "fail"
NOT_EVALUATED, NOT_APPLICABLE = "not_evaluated", "not_applicable"

FINANCIAL_SECTORS = ("Financial Services", "Financials")      # abs_readings.is_financial_sector
NOT_COVERED_FIELD = "Fundamentals.sector"
COVER_CONTEXT = ("below 1.0 the dividend is not covered; 1.5 to 2.0 is the range sources call thin "
                 "to comfortable")
LOSS_MAKER_NOTE = "loss-maker, see runway"
# runway notes that mean an input is missing (not evaluated), as opposed to "does not apply"
_RUNWAY_MISSING = ("operating cash flow missing", "change in working capital missing", "no accounts")


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
    warnings: tuple = ()               # the four warnings
    missing: tuple = ()                # inputs that stopped a "qualifies"
    debt_line: str = ""
    definition_id: str = ""

    def as_dict(self) -> dict:
        return {"definition": self.definition_id, "state": self.state, "reason": self.reason,
                "rules": [r.as_dict() for r in self.rules],
                "warnings": [w.as_dict() for w in self.warnings],
                "missing": list(self.missing), "debt_line": self.debt_line}


def _times(v: float) -> str:
    return f"{v:.1f} times"


def _pct(v: float) -> str:
    return f"{100.0 * v:.1f}%"


def is_financial(f) -> bool:
    """The applicability rule the other absolute readings use (``abs_readings.is_financial_sector``):
    only a CONFIRMED financial sector counts, a missing sector never does."""
    from ..abs_readings import is_financial_sector
    return is_financial_sector(f)


# --------------------------------------------------------------------------- #
# the four exclusions
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


def _solvency(f, d: GateDefinition) -> RuleResult:
    s = gr.solvency(f)
    limit = f"excluded below {_pct(d.solvency_exclude_below)}"
    label = "Solvency"
    if s.state == "net_cash":
        return RuleResult("solvency", label, "net cash", limit, PASS)
    if s.state == "no_debt":
        return RuleResult("solvency", label, "no debt", limit, PASS)
    if s.state == "loss_maker":
        return RuleResult("solvency", label, "not evaluated", limit, NOT_EVALUATED, LOSS_MAKER_NOTE)
    if s.state == "missing":
        return RuleResult("solvency", label, "not evaluated", limit, NOT_EVALUATED, s.note)
    reading = f"cash flow is {_pct(s.ratio)} of debt"
    verdict = FAIL if s.ratio < d.solvency_exclude_below else PASS
    return RuleResult("solvency", label, reading, limit, verdict, "", s.ratio)


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
    limit = f"excluded under {d.runway_exclude_below_months:.0f} months"
    if not r.applies:
        if r.note in _RUNWAY_MISSING:
            return RuleResult("cash_runway", "Cash runway", "not evaluated", limit, NOT_EVALUATED, r.note)
        return RuleResult("cash_runway", "Cash runway", "does not apply", limit, NOT_APPLICABLE, r.note)
    if r.funded_by_wc:
        return RuleResult("cash_runway", "Cash runway", "not burning cash", limit, PASS, r.note)
    if r.months is None:
        return RuleResult("cash_runway", "Cash runway", "not evaluated", limit, NOT_EVALUATED, r.note)
    verdict = FAIL if r.months < d.runway_exclude_below_months else PASS
    return RuleResult("cash_runway", "Cash runway", f"{r.months:.0f} months", limit, verdict, "", r.months)


# --------------------------------------------------------------------------- #
# the four warnings
# --------------------------------------------------------------------------- #
def _highly_leveraged(f, d: GateDefinition) -> RuleResult:
    """Fires when net debt / EBITDA is above the line OR cash flow over debt is below its line. Both
    readings are printed, evaluated or not."""
    lev = gr.leverage(f)
    sol = gr.solvency(f)
    limit = (f"warning above {d.leverage_warn_above:.0f} times net debt / EBITDA, "
             f"or cash flow under {_pct(d.leverage_cash_flow_over_debt_below)} of debt")
    if lev.value is None:
        a = f"net debt / EBITDA not evaluated ({lev.note})"
    elif lev.value <= 0:
        a = "net debt / EBITDA: net cash"
    else:
        a = f"net debt / EBITDA {_times(lev.value)}"
    if sol.state == "ratio":
        b = f"cash flow over debt {_pct(sol.ratio)}"
    elif sol.state in ("net_cash", "no_debt"):
        b = f"cash flow over debt: {sol.note}"
    elif sol.state == "loss_maker":
        b = "cash flow over debt not evaluated (loss-maker)"
    else:
        b = f"cash flow over debt not evaluated ({sol.note})"
    reading = f"{a}; {b}"
    fires = ((lev.value is not None and lev.value > d.leverage_warn_above)
             or (sol.state == "ratio" and sol.ratio < d.leverage_cash_flow_over_debt_below))
    evaluated = lev.value is not None or sol.state in ("ratio", "net_cash", "no_debt")
    result = WARN if fires else (PASS if evaluated else NOT_EVALUATED)
    return RuleResult("highly_leveraged", "Highly leveraged", reading, limit, result, "", lev.value)


def _distress_score(f, d: GateDefinition) -> RuleResult:
    r = gr.altman_z_double_prime(f)
    limit = f"warning below {d.z_warn_below:.2f}; grey zone to {d.z_grey_to:.2f}"
    if r.value is None:
        return RuleResult("distress_score", "Distress score", "not evaluated", limit, NOT_EVALUATED, r.note)
    if r.value < d.z_warn_below:
        verdict, note = WARN, "distress zone"
    elif r.value <= d.z_grey_to:
        verdict, note = PASS, "grey zone"
    else:
        verdict, note = PASS, ""
    return RuleResult("distress_score", "Distress score", f"{r.value:.2f}", limit, verdict, note, r.value)


def _dividend_cover(f, d: GateDefinition) -> RuleResult:
    r = gr.dividend_cover(f, years=d.cover_years)
    limit = COVER_CONTEXT
    if not r.applies:
        if r.note in ("dividends paid missing", "no accounts"):
            return RuleResult("dividend_cover", "Dividend cover", "not evaluated", limit,
                              NOT_EVALUATED, r.note)
        return RuleResult("dividend_cover", "Dividend cover", "no dividend paid", limit,
                          NOT_APPLICABLE, r.note)
    if r.cover is None:
        return RuleResult("dividend_cover", "Dividend cover", "not evaluated", limit, NOT_EVALUATED, r.note)
    if r.cover < d.cover_not_covered_below:
        return RuleResult("dividend_cover", "Dividend cover", f"{_times(r.cover)} (not covered)", limit,
                          WARN, r.note, r.cover)
    return RuleResult("dividend_cover", "Dividend cover", _times(r.cover), limit, PASS, r.note, r.cover)


def _runway_warning(f, d: GateDefinition) -> RuleResult:
    r = gr.cash_runway(f)
    limit = f"warning under {d.runway_warn_below_months:.0f} months"
    label = "Cash runway"
    if not r.applies:
        if r.note in _RUNWAY_MISSING:
            return RuleResult("runway_warning", label, "not evaluated", limit, NOT_EVALUATED, r.note)
        return RuleResult("runway_warning", label, "does not apply", limit, NOT_APPLICABLE, r.note)
    if r.funded_by_wc:
        return RuleResult("runway_warning", label, "not burning cash", limit, PASS, r.note)
    if r.months is None:
        return RuleResult("runway_warning", label, "not evaluated", limit, NOT_EVALUATED, r.note)
    if r.months < d.runway_warn_below_months:
        return RuleResult("runway_warning", label, f"{r.months:.0f} months", limit, WARN,
                          "will need to raise money within the holding period", r.months)
    return RuleResult("runway_warning", label, f"{r.months:.0f} months", limit, PASS, "", r.months)


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
    """Run the four exclusions and the four warnings, then combine them.

    Any failed exclusion excludes, with the rule and its figure named. "Qualifies" needs the accrual ratio
    evaluated and solvency settled: solvency evaluated, or a loss-maker (cash flow before working capital
    at or below zero) whose runway is evaluated, because the runway rule decides for a loss-maker. The
    F-score counts when nine checks exist. Otherwise: not enough data, with the missing inputs listed."""
    did = definition.id
    if f is None:
        return GateResult(NOT_ENOUGH_DATA, "Not enough data: no accounts were available.",
                          missing=("accounts",), definition_id=did)
    if is_financial(f):
        sector = getattr(f, "sector", None) or "financial sector"
        return GateResult(NOT_COVERED,
                          f"Not covered: banks and insurers are outside this gate ({sector}).",
                          definition_id=did)
    rules = (_earnings_quality(f, definition), _solvency(f, definition),
             _accounting_health(f, definition), _cash_runway(f, definition))
    warnings = (_highly_leveraged(f, definition), _distress_score(f, definition),
                _dividend_cover(f, definition), _runway_warning(f, definition))
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
    sol = by_key["solvency"]
    if sol.result == NOT_EVALUATED:
        if sol.note == LOSS_MAKER_NOTE:
            run = by_key["cash_runway"]
            if run.result != PASS:                          # the runway decides, and it cannot
                missing.append(f"solvency and cash runway (a loss-maker before working capital; "
                               f"the runway is {run.result.replace('_', ' ')}: {run.note})")
        else:
            missing.append(f"solvency ({sol.note})")
    if missing:
        return GateResult(NOT_ENOUGH_DATA, "Not enough data: " + "; ".join(missing) + ".",
                          rules, warnings, tuple(missing), debt_line, did)
    fired = [w for w in warnings if w.result == WARN]
    tail = (" Warnings: " + ", ".join(w.label.lower() for w in fired) + ".") if fired else " No warnings."
    return GateResult(QUALIFIES, "Qualifies: no exclusion rule failed." + tail, rules, warnings,
                      debt_line=debt_line, definition_id=did)
