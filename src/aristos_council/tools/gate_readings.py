"""ABSOLUTE-GATE-1 - the arithmetic behind the exclusion gate (docs/DESIGN_absolute.md, section b).

Every number the gate prints is computed here and nowhere else (house rule 1). Each function is
pure, takes the ``Fundamentals`` the page already fetched, and returns a ``GateReading``: a value, or
an ABSTENTION with its reason. Null is not-evaluated and is never a failure (house rule 3), and a
ratio whose bottom number is zero or negative is never a pass.

Reused, not rewritten: the cash-flow accrual ratio (``screening.accrual_ratio``: net income minus
operating cash flow, over the mean of this and last year's total assets - the form the design
asks for) and the F-score (``screening.piotroski_f_score``). New here: Altman's Z'' (book equity,
no constant), EBITDA, net debt over EBITDA, cash runway, dividend cover and the abnormal-investment
measure of Titman, Wei and Xie. The 1968 Z-Score used by the Forensic lens is untouched.

Debt is "as the tool counts it today": the balance sheet's own Total Debt row (borrowings plus the
long-term operating lease line), not the larger headline figure a vendor adds leases to.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .screening import accrual_ratio, period_matched, piotroski_f_score

# Altman (2000, 2002): Z'' = 6.56 X1 + 3.26 X2 + 6.72 X3 + 1.05 X4, shown WITHOUT its constant (+3.25).
ZPP_X1, ZPP_X2, ZPP_X3, ZPP_X4 = 6.56, 3.26, 6.72, 1.05
_ZPP_KEYS = ("current_assets", "current_liabilities", "retained_earnings", "ebit",
             "total_assets", "total_liabilities", "shareholders_equity")


@dataclass(frozen=True)
class GateReading:
    """A value, or an abstention with its reason. ``detail`` carries a disclosed fallback."""

    value: Optional[float] = None
    note: str = ""
    detail: str = ""


def _num(x) -> Optional[float]:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return float(x)


def _first(series) -> Optional[float]:
    for v in series or []:
        n = _num(v)
        if n is not None:
            return n
    return None


# --------------------------------------------------------------------------- #
# earnings quality
# --------------------------------------------------------------------------- #
def earnings_quality(f) -> GateReading:
    """Cash-flow accrual ratio, via the one implementation the Forensic lens also uses."""
    r = accrual_ratio(f)
    if r.value is None:
        return GateReading(note=r.detail or r.note)
    return GateReading(value=r.value, note=r.note, detail=r.detail)


# --------------------------------------------------------------------------- #
# distress
# --------------------------------------------------------------------------- #
def altman_z_double_prime(f) -> GateReading:
    """Altman's Z'' for non-manufacturers, without the constant.

    X1 working capital / total assets, X2 retained earnings / total assets, X3 EBIT / total assets,
    X4 BOOK equity / total liabilities. Every input is a statement line, so the ratios cancel the
    currency and an ADR no longer abstains. Not evaluated on a missing line, on total assets or total
    liabilities at or below zero (both are denominators), and when the provider says the statements
    are not in one currency. NEGATIVE equity is allowed: liabilities above assets is real information
    and drags X4, and the score, down."""
    if f is None:
        return GateReading(note="no accounts")
    note = getattr(f, "statements_currency_note", "")
    if note:
        return GateReading(note=f"statements not in one currency ({note})")
    (now,) = period_matched(f, _ZPP_KEYS, periods=1)
    labels = {"current_assets": "current assets", "current_liabilities": "current liabilities",
              "retained_earnings": "retained earnings", "ebit": "EBIT", "total_assets": "total assets",
              "total_liabilities": "total liabilities", "shareholders_equity": "book equity"}
    ebit = now["ebit"]
    if ebit is None:                                     # operating income is the same line on some filers
        (alt,) = period_matched(f, ("operating_income",), periods=1)
        ebit = alt["operating_income"]
    values = {**now, "ebit": ebit}
    absent = [labels[k] for k in _ZPP_KEYS if values[k] is None]
    if absent:
        return GateReading(note=f"{', '.join(absent)} missing")
    ta, tl = values["total_assets"], values["total_liabilities"]
    if ta <= 0 or tl <= 0:
        return GateReading(note="total assets or total liabilities at or below zero")
    z = (ZPP_X1 * (values["current_assets"] - values["current_liabilities"]) / ta
         + ZPP_X2 * values["retained_earnings"] / ta
         + ZPP_X3 * ebit / ta
         + ZPP_X4 * values["shareholders_equity"] / tl)
    return GateReading(value=z, note=f"Z'' = {z:.2f}")


# --------------------------------------------------------------------------- #
# accounting health
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FScoreReading:
    score: Optional[int]          # the tally, only when all nine checks could be computed
    computable: int               # n of 9
    tally: int                    # points earned among those computable (for the note)


def accounting_health(f, *, required_checks: int = 9) -> FScoreReading:
    """The F-score, counted only when every one of the nine checks can be computed (Piotroski's
    sample design). With fewer, the score is withheld and ``computable`` says how many there were."""
    r = piotroski_f_score(f)
    if r.computed >= required_checks:
        return FScoreReading(score=int(r.points), computable=int(r.computed), tally=int(r.points))
    return FScoreReading(score=None, computable=int(r.computed), tally=int(r.points))


# --------------------------------------------------------------------------- #
# EBITDA, debt, leverage
# --------------------------------------------------------------------------- #
def ebitda(f) -> GateReading:
    """EBITDA = EBIT + depreciation and amortisation, both read at the SAME fiscal year-end."""
    if f is None:
        return GateReading(note="no accounts")
    (now,) = period_matched(f, ("ebit", "depreciation_amortization"), periods=1)
    ebit, da = now["ebit"], now["depreciation_amortization"]
    if ebit is None:
        (alt,) = period_matched(f, ("operating_income", "depreciation_amortization"), periods=1)
        ebit, da = alt["operating_income"], alt["depreciation_amortization"]
    if ebit is None:
        return GateReading(note="EBIT missing")
    if da is None:
        return GateReading(note="depreciation and amortisation missing")
    return GateReading(value=ebit + abs(da), note="EBIT + depreciation and amortisation")


def debt_and_cash_as_counted(f) -> tuple[Optional[float], Optional[float], str]:
    """``(debt, cash, source)``. Debt is the balance sheet's own Total Debt row (borrowings plus the
    long-term operating lease line); cash is cash plus short-term investments. The latest balance
    sheet's scalars when the provider gives them, else the newest period's scalars (EODHD)."""
    if f is None:
        return None, None, ""
    debt, cash = _num(getattr(f, "total_debt", None)), _num(getattr(f, "total_cash", None))
    if debt is not None and cash is not None:
        return debt, cash, "latest balance sheet"
    scalars = getattr(f, "period_scalars", None) or {}
    for period in sorted(scalars, reverse=True):
        row = scalars[period] or {}
        d, c = _num(row.get("total_debt")), _num(row.get("total_cash"))
        if d is not None and c is not None:
            return d, c, f"balance sheet of {period}"
    return debt, cash, ""


def leverage(f) -> GateReading:
    """Net debt over EBITDA: how many years of operating cash profit it would take to repay the
    borrowing. Net cash reads as a NEGATIVE value (no debt to cover). EBITDA at or below zero is not
    evaluated, never a pass."""
    debt, cash, _ = debt_and_cash_as_counted(f)
    if debt is None or cash is None:
        return GateReading(note="debt or cash missing")
    e = ebitda(f)
    if e.value is None:
        return GateReading(note=e.note)
    if e.value <= 0:
        return GateReading(note="EBITDA at or below zero")
    return GateReading(value=(debt - cash) / e.value, note="net debt / EBITDA")


# --------------------------------------------------------------------------- #
# solvency (ABSOLUTE-GATE-2): cash from operations before working-capital changes, over total debt
# --------------------------------------------------------------------------- #
def cash_flow_before_working_capital(f) -> GateReading:
    """Cash from operations BEFORE working-capital changes = operating cash flow minus the cash flow
    statement's change in working capital (positive = cash released), both at the same fiscal year end.
    The measure Moody's calls CFO pre-WC. Either line missing: not evaluated (never operating cash flow
    standing in for it)."""
    if f is None:
        return GateReading(note="no accounts")
    (now,) = period_matched(f, ("operating_cash_flow", "change_in_working_capital"), periods=1)
    ocf, dwc = now["operating_cash_flow"], now["change_in_working_capital"]
    if ocf is None:
        return GateReading(note="operating cash flow missing")
    if dwc is None:
        return GateReading(note="change in working capital missing")
    return GateReading(value=ocf - dwc, note="operating cash flow minus the change in working capital")


@dataclass(frozen=True)
class SolvencyReading:
    """``state`` is one of: ``ratio`` (value is cash flow / debt), ``net_cash`` and ``no_debt`` (both pass
    before any ratio is formed), ``loss_maker`` (cash flow before working capital at or below zero: not
    evaluated, the runway decides), ``missing`` (an input is absent: not evaluated)."""

    state: str
    ratio: Optional[float] = None
    note: str = ""


def solvency(f) -> SolvencyReading:
    """Cash flow before working-capital changes over TOTAL debt as the tool counts it (gross, as Moody's
    does). Net cash passes, and debt at or below zero passes, before the ratio is formed. Cash flow at or
    below zero is not evaluated ("loss-maker, see runway"): a negative ratio never excludes on its own.
    A missing debt, or a missing cash-flow line when a ratio is needed, is not evaluated."""
    debt, cash, _ = debt_and_cash_as_counted(f)
    if debt is None:
        return SolvencyReading("missing", note="debt missing")
    if debt <= 0:
        return SolvencyReading("no_debt", note="no debt")
    if cash is not None and debt - cash <= 0:
        return SolvencyReading("net_cash", note="net cash")
    cfo = cash_flow_before_working_capital(f)
    if cfo.value is None:
        return SolvencyReading("missing", note=cfo.note)
    if cfo.value <= 0:
        return SolvencyReading("loss_maker", note="loss-maker, see runway")
    return SolvencyReading("ratio", ratio=cfo.value / debt, note="cash flow before working capital / total debt")


# --------------------------------------------------------------------------- #
# cash runway
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RunwayReading:
    applies: bool                 # False when operating cash flow is not negative
    months: Optional[float] = None
    note: str = ""


def cash_runway(f) -> RunwayReading:
    """Months the cash lasts at last year's burn - only for a company whose latest annual operating cash
    flow is NEGATIVE. Burn is the negative free cash flow of that year (free cash flow = operating cash
    flow minus capital spending when the provider gives no figure). Cash is cash plus short-term
    investments. A company that makes cash, or whose free cash flow is not negative, has no burn."""
    if f is None:
        return RunwayReading(applies=False, note="no accounts")
    ocf = _first(getattr(f, "operating_cash_flow_annual", None))
    if ocf is None:
        return RunwayReading(applies=False, note="operating cash flow missing")
    if ocf >= 0:
        return RunwayReading(applies=False, note="operating cash flow is not negative")
    fcf = _first(getattr(f, "free_cash_flow_annual", None))
    if fcf is None:
        capex = _first(getattr(f, "capital_expenditure_annual", None))
        fcf = ocf - abs(capex) if capex is not None else None
    if fcf is None:
        return RunwayReading(applies=True, note="free cash flow missing")
    if fcf >= 0:
        return RunwayReading(applies=False, note="free cash flow is not negative")
    cash = _num(getattr(f, "total_cash", None))
    if cash is None:
        scalars = getattr(f, "period_scalars", None) or {}
        for period in sorted(scalars, reverse=True):
            cash = _num((scalars[period] or {}).get("total_cash"))
            if cash is not None:
                break
    if cash is None:
        return RunwayReading(applies=True, note="cash missing")
    return RunwayReading(applies=True, months=12.0 * cash / (-fcf), note="cash / yearly burn")


# --------------------------------------------------------------------------- #
# dividend cover
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CoverReading:
    applies: bool                 # False when no dividend is paid
    cover: Optional[float] = None  # average net income / dividends paid
    years: int = 0
    note: str = ""


def dividend_cover(f, *, years: int = 3) -> CoverReading:
    """Average net income of up to ``years`` fiscal years divided by the dividends paid (cash, the
    current year). No dividend means it does not apply. An average net income at or below zero with a
    dividend being paid is a cover of zero or less, which the gate reads as "not covered": it is never a
    pass and never an abstention."""
    if f is None:
        return CoverReading(applies=False, note="no accounts")
    paid = _num(getattr(f, "dividends_paid", None))
    if paid is None:
        # EODHD carries the cash dividend per fiscal period, not as a scalar: read the newest period's
        scalars = getattr(f, "period_scalars", None) or {}
        for period in sorted(scalars, reverse=True):
            paid = _num((scalars[period] or {}).get("dividends_paid"))
            if paid is not None:
                break
    if paid is None:
        # No cash-flow line. A company with no dividend on record has none to cover (providers leave the
        # line empty for non-payers); one that does pay has a per-share dividend or a yield, so a missing
        # line there is a real gap and stays not evaluated.
        dps, yld = _num(getattr(f, "dividend_per_share", None)), _num(getattr(f, "dividend_yield", None))
        if not ((dps or 0) > 0 or (yld or 0) > 0):
            return CoverReading(applies=False, note="no dividend paid")
        return CoverReading(applies=False, note="dividends paid missing")
    if paid <= 0:
        return CoverReading(applies=False, note="no dividend paid")
    ni = [n for n in (_num(v) for v in (getattr(f, "net_income", None) or [])[:years]) if n is not None]
    if not ni:
        return CoverReading(applies=True, note="net income missing")
    avg = sum(ni) / len(ni)
    return CoverReading(applies=True, cover=avg / paid, years=len(ni),
                        note=f"average net income of {len(ni)} year{'s' if len(ni) != 1 else ''} / dividends paid")


# --------------------------------------------------------------------------- #
# investment intensity (calibrated here, used by phase 2)
# --------------------------------------------------------------------------- #
def abnormal_investment(f) -> GateReading:
    """Titman, Wei and Xie: capital spending over sales this year, divided by the average of the same
    ratio over the previous three years, minus 1. Positional annual lists (newest first); needs four
    years of both, and a prior-three-year average above zero."""
    if f is None:
        return GateReading(note="no accounts")
    capex = [_num(v) for v in (getattr(f, "capital_expenditure_annual", None) or [])[:4]]
    sales = [_num(v) for v in (getattr(f, "total_revenue", None) or [])[:4]]
    if len(capex) < 4 or len(sales) < 4 or None in capex or None in sales:
        return GateReading(note="fewer than four years of capital spending and sales")
    if any(s <= 0 for s in sales):
        return GateReading(note="sales at or below zero")
    ratios = [abs(c) / s for c, s in zip(capex, sales)]
    prior = sum(ratios[1:]) / 3.0
    if prior <= 0:
        return GateReading(note="no capital spending in the prior three years")
    return GateReading(value=ratios[0] / prior - 1.0, note="capex/sales over its prior three-year average, minus 1")
