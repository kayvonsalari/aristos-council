"""Measures behind the Quality and Earnings Power Value lenses (LENS-EXPAND-1a).

Pure, deterministic, no I/O: ``Fundamentals`` in, ``(value, note)`` out. A value of None
is NOT-EVALUATED and the note says why — never a zero, never a pass or a fail (hard
rule 3). All of the arithmetic of both lenses lives here (hard rule 1).

Every ratio below divides two figures in the SAME (accounts) currency, so none of them
needs FX. The one exception is the EPV margin of safety, which sets an accounts-currency
profit against a price-currency enterprise value; that function takes the same dated
``CurrencyConversion`` rate the earnings-yield factor uses (factors.enterprise_value).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .screening import nopat_roic, period_matched

# --- the named constants (mirrored, and pinned by a test, in strategies/epv_v1.yaml) ----- #
EPV_TAX_RATE = 0.25            # flat tax on operating profit, unless a reliable effective rate exists
EPV_COST_OF_CAPITAL = 0.09     # flat required return — ONE assumption for the whole universe
EPV_MARGIN_YEARS = 5           # fiscal years averaged for the normalised EBIT margin
EPV_MIN_YEARS = 3              # fewer paired years than this -> abstain (a margin of 1-2 years is a guess)
# A through-cycle effective rate is "reliable" only inside this band; outside it (a tax
# credit year, a one-off charge) the flat rate is used instead.
EPV_RELIABLE_TAX_BAND = (0.05, 0.40)

QUALITY_ROIC_YEARS = 5         # fiscal years the worst-year ROIC looks across
QUALITY_ROIC_MIN_YEARS = 3     # fewer ROIC years than this -> abstain


def _clean(series) -> list[float]:
    return [float(v) for v in (series or [])
            if isinstance(v, (int, float)) and not isinstance(v, bool)]


# --------------------------------------------------------------------------- #
# Quality lens
# --------------------------------------------------------------------------- #
def gross_profitability(f) -> tuple[Optional[float], str]:
    """Gross profit ÷ total assets for the latest fiscal year (Novy-Marx). HIGH is better.

    Both lines are read period-matched (``period_matched``), so a gross-profit figure from
    one fiscal year is never divided by the assets of another."""
    if f is None:
        return None, "no fundamentals"
    row = period_matched(f, ("gross_profit", "total_assets"), periods=1)[0]
    gp, ta = row.get("gross_profit"), row.get("total_assets")
    if gp is None:
        return None, "gross profit unavailable (no gross-profit line — common for banks and some industrials)"
    if ta is None:
        return None, "total assets unavailable"
    if ta <= 0:
        return None, "total assets not positive"
    return gp / ta, "gross profit / total assets, latest fiscal year"


def worst_year_roic(f, *, years: int = QUALITY_ROIC_YEARS,
                    min_years: int = QUALITY_ROIC_MIN_YEARS) -> tuple[Optional[float], str]:
    """The LOWEST single-year ROIC across the last ``years`` fiscal years. HIGH is better.

    Each year is the house ROIC (``nopat_roic``): operating income × (1 − that year's
    effective tax rate) ÷ the PROVIDED invested capital of that year. It is a minimum, not
    a mean: the lens asks for a return that holds in the company's worst recent year. A
    year whose ROIC is undefined (no invested capital) is skipped, and fewer than
    ``min_years`` usable years abstains — a "worst year" over one or two data points would
    be the only year."""
    if f is None:
        return None, "no fundamentals"
    oi, ic = _clean(f.operating_income), _clean(f.invested_capital)
    tax, pretax = _clean(f.tax_provision), _clean(f.pretax_income)
    n = min(years, len(oi), len(ic))
    # Tax is read by position only while the two tax series line up; otherwise nopat_roic
    # falls back to its own disclosed "no usable tax data" path for that year.
    tax_ok = len(tax) == len(pretax)
    per_year: list[float] = []
    for i in range(n):
        roic, _ = nopat_roic(oi[i], tax[i] if tax_ok and i < len(tax) else None,
                             pretax[i] if tax_ok and i < len(pretax) else None, ic[i])
        if roic is not None:
            per_year.append(roic)
    if len(per_year) < min_years:
        return None, (f"only {len(per_year)} fiscal year(s) of return on capital available "
                      f"(needs {min_years})")
    return min(per_year), f"lowest of {len(per_year)} fiscal years' return on invested capital"


def net_debt_to_ebit(f) -> tuple[Optional[float], str]:
    """Net debt ÷ latest-year operating profit (EBIT). LOW is better; net cash is NEGATIVE
    and ranks best. Abstains — and so ranks last under the lens's ``missing: worst`` — when
    EBIT is zero or negative, because the ratio would invert (more debt would read better)."""
    if f is None:
        return None, "no fundamentals"
    if f.total_debt is None or f.total_cash is None:
        return None, f"{'total debt' if f.total_debt is None else 'cash'} unavailable"
    ebit = _clean(f.ebit)[:1] or _clean(f.operating_income)[:1]
    if not ebit:
        return None, "no operating-profit (EBIT) history"
    if ebit[0] <= 0:
        return None, "operating profit (EBIT) is zero or negative, so debt cannot be measured against it"
    return (f.total_debt - f.total_cash) / ebit[0], "net debt / latest-year EBIT"


# --------------------------------------------------------------------------- #
# Earnings Power Value lens
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class EPVReading:
    """Everything the EPV lens computed for one company, so a report can show the working."""

    margin_of_safety: Optional[float]    # EPV / EV - 1 (None when not computable)
    epv: Optional[float] = None          # price currency
    enterprise_value: Optional[float] = None
    normalised_ebit: Optional[float] = None      # accounts currency
    avg_margin: Optional[float] = None
    margin_years: int = 0
    tax_rate: Optional[float] = None
    tax_source: str = ""                 # "flat 25%" | "effective 21.3% (5y)"
    note: str = ""                       # why margin_of_safety is None, or how it was built


def _effective_tax(f, window: int) -> tuple[float, str]:
    tax, pretax = _clean(f.tax_provision)[:window], _clean(f.pretax_income)[:window]
    if len(tax) == len(pretax) and len(tax) >= EPV_MIN_YEARS and sum(pretax) > 0:
        rate = sum(tax) / sum(pretax)
        lo, hi = EPV_RELIABLE_TAX_BAND
        if lo <= rate <= hi:
            return rate, f"effective {rate:.1%} ({len(tax)}y)"
    return EPV_TAX_RATE, f"flat {EPV_TAX_RATE:.0%}"


def earnings_power_value(f, ev: Optional[float], fx_rate: float = 1.0) -> EPVReading:
    """EPV = normalised after-tax operating profit ÷ cost of capital, and the margin of
    safety ``EPV / EV − 1`` (HIGH is better).

    Normalised operating profit = the AVERAGE EBIT margin over the last
    ``EPV_MARGIN_YEARS`` fiscal years × the LATEST revenue — what today's sales earn at the
    margin the business has held through the cycle. No growth is assumed. ``ev`` is the
    price-currency enterprise value (None -> abstain, no market-cap stand-in: EPV is an
    enterprise value and is compared to one). ``fx_rate`` converts the accounts-currency
    profit into that currency."""
    if f is None:
        return EPVReading(None, note="no fundamentals")
    rev = _clean(f.total_revenue)
    ebit_series = _clean(f.ebit) or _clean(f.operating_income)
    # Pair revenue and EBIT by position (newest-first), as the other through-cycle
    # measures do; years where revenue is not positive carry no margin.
    n = min(EPV_MARGIN_YEARS, len(rev), len(ebit_series))
    margins = [ebit_series[i] / rev[i] for i in range(n) if rev[i] > 0]
    if len(margins) < EPV_MIN_YEARS:
        return EPVReading(None, margin_years=len(margins),
                          note=(f"only {len(margins)} fiscal year(s) of operating margin "
                                f"available (needs {EPV_MIN_YEARS})"))
    avg_margin = sum(margins) / len(margins)
    normalised = avg_margin * rev[0]
    tax_rate, tax_source = _effective_tax(f, EPV_MARGIN_YEARS)
    base = dict(avg_margin=avg_margin, margin_years=len(margins), tax_rate=tax_rate,
                tax_source=tax_source, normalised_ebit=normalised)
    if normalised <= 0:
        return EPVReading(None, note=(f"normalised operating profit is negative or zero "
                                      f"(average margin {avg_margin:.1%} over {len(margins)}y) "
                                      "— nothing to capitalise"), **base)
    if ev is None:
        return EPVReading(None, note="enterprise value unavailable (debt or cash missing)", **base)
    if ev <= 0:
        return EPVReading(None, enterprise_value=ev,
                          note="enterprise value not positive (cash exceeds market cap plus debt)", **base)
    epv = normalised * fx_rate * (1.0 - tax_rate) / EPV_COST_OF_CAPITAL
    return EPVReading(epv / ev - 1.0, epv=epv, enterprise_value=ev,
                      note=(f"EPV = {avg_margin:.1%} avg margin ({len(margins)}y) x latest revenue "
                            f"x (1 - tax, {tax_source}) / {EPV_COST_OF_CAPITAL:.0%}"), **base)
