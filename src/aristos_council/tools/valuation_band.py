"""Absolute valuation band (VALBAND-1) — is the price high or low vs the company's
OWN past?

Why this exists
---------------
Every lens in this system judges value RELATIVELY: earnings yield ranked WITHIN a
cohort. In a hot cohort the least-expensive of ten overpriced names still ranks #1.
Nothing answered "is this stock expensive against its own history?" This module does,
deterministically and with no forecasting: it rebuilds the stock's own monthly
valuation series over the last 5 years and reports where TODAY sits in that
distribution as a percentile (92nd = near its own historical peak, 15th =
historically cheap).

What it computes
----------------
For each month-end in the window:

    EV/EBIT  = (market cap + net debt) / EBIT          (preferred)
    P/E      =  market cap / net income                 (fallback, labelled)

EV/EBIT is preferred because P/E breaks on loss years and is distorted by buybacks.
The fallback is never silent — the basis is part of every value's display.

Three construction decisions, made explicitly because each is the kind of thing that
quietly corrupts a "historical" series:

1. **Market cap is scaled by PRICE RELATIVES, not by a historical share count.**
   ``mcap_t = market_cap_now × (close_t / close_now)``. Provider closes are
   split-adjusted; provider balance-sheet share counts are AS-REPORTED (pre-split).
   Multiplying one by the other is off by the full split factor (4x for a 2:1, 100x
   for NVDA's 10:1) — a silent, enormous error. A ratio of two adjusted closes is
   split-safe. The cost is that share-count DRIFT (buybacks/issuance) is not modelled;
   that is a few percent a year, disclosed here rather than traded for a 10x error.
   Uses ``PriceBar.close`` (split-adjusted, NOT dividend-adjusted) on purpose:
   ``adj_close`` is a TOTAL-RETURN series, and a total-return relative is not a price.

2. **Statements are applied POINT-IN-TIME with a reporting lag.** A statement is only
   used from ``period_end + 90 days`` — the month a reader could actually have known
   it. The SAME rule produces today's value, so the current point and the history are
   built identically (an inconsistency there is how a band silently reports a fake
   extreme). This needs DATED statements: the band reads
   ``Fundamentals.aligned_annual`` / ``aligned_period_ends``, never the positional
   series (whose NaN-dropping loses the value <-> fiscal-year correspondence).

3. **Missing periods DROP OUT and are counted, never faked.** A negative-earnings month
   has no meaningful multiple; it is excluded and the coverage is reported ("band from
   41 of 60 months"). Below 3 years of computable span, or below half the months in
   that span, the band ABSTAINS with the reason — a recent IPO will and should abstain.

Currency: a CONFIRMED accounts-vs-price currency mismatch abstains (house rule 8 — no
FX conversion, honest abstention only); market cap is in the price currency while EBIT
and debt are in the accounts currency, so a mixed multiple would be meaningless.

Pure and deterministic: same inputs -> same percentile. No IO, no LLM, no forecasting.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Sequence

# --- Construction constants (documented above; changing one changes every band) ---- #
BAND_YEARS = 5              # window length
MIN_YEARS = 3.0             # minimum computable SPAN before the band is reportable
MIN_COVERAGE = 0.5          # minimum computable fraction of the months in that span
REPORTING_LAG_DAYS = 90     # a statement is usable only this long after its period end
STALE_CURRENT_DAYS = 45     # the newest computable point must be this recent to be "today"

_EV_EBIT = "ev_ebit"
_PE = "pe"

_BASIS_PHRASE = {_EV_EBIT: "EV/EBIT band", _PE: "P/E band (fallback)"}
_BASIS_LABEL = {_EV_EBIT: "EV/EBIT", _PE: "P/E (fallback)"}


# --------------------------------------------------------------------------- #
# Result shape
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ValuationBand:
    """Where today's valuation sits in the stock's own multi-year distribution.

    ``percentile`` is None when the band ABSTAINED — ``note`` then carries the reason
    ("insufficient history: 1.4y"). Never a fabricated 50th.
    """

    percentile: Optional[float] = None       # 0..100; higher = more expensive vs own past
    basis: Optional[str] = None              # "ev_ebit" | "pe" | None when abstained
    current: Optional[float] = None          # today's multiple on that basis
    months_covered: int = 0                  # months with a computable multiple
    months_total: int = 0                    # month-ends with a price in the window
    years_covered: float = 0.0               # span of the computable months, in years
    window_years: int = BAND_YEARS
    note: str = ""
    # Net-debt provenance for the EV route: "asof" (dated statements, per month),
    # "latest" (current scalars held constant — disclosed), "" when not applicable.
    net_debt_basis: str = ""
    # --- Reversion inputs (PRICE-1), recorded not recomputed -------------------- #
    # The SAME monthly multiple series this band took its percentile over, reduced to
    # its MEDIAN, plus the three point-in-time inputs the CURRENT point was built from
    # (the newest usable month). tools/reversion.py reads ONLY these, so the reversion
    # value and the percentile are two readings of one series by construction and can
    # never be built from different months, statements or share counts. Recording them
    # changes NO band arithmetic, threshold or abstention: an abstaining band returns
    # before they are set and leaves them None, exactly as before.
    median_multiple: Optional[float] = None
    current_point: Optional[date] = None     # the month-end the current multiple used
    current_earnings: Optional[float] = None  # EBIT (ev_ebit) / net income (pe), as-of
    current_net_debt: Optional[float] = None  # net debt at current_point, band's basis
    current_shares: Optional[float] = None    # shares outstanding as-of, same lag rule
    # --- VALBAND-2: FX provenance, when accounts and price are in different currencies.
    # Empty for a same-currency band, so those render byte-identically to before.
    fx_note: str = ""                        # "DKK accounts converted to USD, monthly …"
    fx_months_missing: int = 0               # months dropped for want of a rate
    # VALBAND-FX-1 — the CURRENT point's conversion, recorded so the reversion value can
    # be stated in the same currency as the price it sits beside. The percentile loop
    # already converted each month; these three make that conversion legible downstream
    # instead of leaving `current_earnings`/`current_net_debt` as raw home-currency
    # figures a reader would take for quote-currency ones.
    fx_rate_current: Optional[float] = None  # quote units per 1 accounts unit, at the
                                             # CURRENT month — 1.0 when same-currency
    fx_rate_asof: Optional[date] = None      # the month that rate belongs to
    fx_pair: str = ""                        # e.g. "DKKUSD=X", or "" same-currency
    # Quoted UNITS outstanding, implied by market cap / price. For a foreign listing the
    # quoted unit is an ADR that may bundle several ordinary shares, so this is NOT
    # `shares_outstanding`; dividing by it keeps the reversion price per QUOTED unit.
    quoted_units: Optional[float] = None

    @property
    def available(self) -> bool:
        return self.percentile is not None

    @property
    def basis_label(self) -> str:
        return _BASIS_LABEL.get(self.basis or "", "")

    @property
    def display(self) -> str:
        """The one display string every surface renders (report, Company Check).

        Computed (PRICE-1 item 4 / PRICE-2 — the VALUE plus a one-word plain-English
        gloss; same numbers, nothing recomputed):
        "EV/EBIT 21.9 — 70th percentile (dear) of its own 5-year range (based on 42 of 61
        months, the rest lack usable statements)".
        Abstained: "not evaluated — insufficient history: 1.4y".

        This is the SINGLE-LINE rendering, kept for surfaces that show ONE name (Company
        Check). The universe report renders the same numbers as a table instead — see
        ``pipeline.valuation_band_table``.
        """
        if not self.available:
            return f"not evaluated — {self.note}" if self.note else "not evaluated"
        pct = round(self.percentile)
        tail = "; net debt held at latest reported" \
            if self.net_debt_basis == "latest" else ""
        lead = f"{self.basis_label} {self.current:.1f} — " \
            if self.current is not None else ""
        # "42 of 61 months" was the unexplained half of the old line: say WHY the other
        # 19 are absent. When every month IS computable there is no "rest" to explain.
        gap = ", the rest lack usable statements" \
            if self.months_covered < self.months_total else ""
        # VALBAND-2: a converted band SAYS it was converted, and by which pair. A reader
        # comparing an ADR's band to a domestic name's must be able to see that one of
        # them crossed a currency to get there.
        fx = f"; {self.fx_note}" if self.fx_note else ""
        return (f"{lead}{ordinal(pct)} percentile ({percentile_gloss(self.percentile)}) "
                f"of its own {self.window_years}-year range "
                f"(based on {self.months_covered} of {self.months_total} months"
                f"{gap}{tail}{fx})")


# Plain-English gloss for a percentile (PRICE-2). FIXED cutoffs, documented in
# CALCULATIONS.md, no judgement in them — they replace the old "dearer than N% of the last
# five years" phrasing, which forced the reader through a double negative at the cheap end
# ("dearer than 1%" meaning "the cheapest it has been"). Applied to the ROUNDED percentile,
# the same number the ordinal beside it is built from, so the two can never disagree.
_GLOSS_CUTOFFS: tuple[tuple[int, str], ...] = (
    (10, "cheapest"), (35, "cheap"), (65, "mid"), (89, "dear"))
_GLOSS_TOP = "dearest"


def percentile_gloss(percentile: float) -> str:
    """``1 -> 'cheapest'``, ``11 -> 'cheap'``, ``50 -> 'mid'``, ``66 -> 'dear'``,
    ``90 -> 'dearest'``. Relative to the name's OWN history, never to a peer group."""
    p = round(percentile)
    for ceiling, word in _GLOSS_CUTOFFS:
        if p <= ceiling:
            return word
    return _GLOSS_TOP


def ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 11 -> '11th', 92 -> '92nd'."""
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}" + {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


# BAND-SANITY-1 — the plausible range for a valuation multiple. Above this, or at/below
# zero, the arithmetic has been fed something wrong: a thin or near-zero EBIT year, a share
# count in the wrong units, a currency mismatch. Live on 2026-09-16: MODEC EV/EBIT 1144x and
# PTTEP 0.6x, both rendered as percentiles beside sane rows. 200x is generous on purpose --
# a genuinely expensive company trades at 40-60x, so this catches faults, not opinions.
MULTIPLE_MAX = 200.0
# ...and the lower bound. The item's rule text said "above 200x or at/below 0", which does
# not reach the second case its own evidence names: PTTEP came back at 0.61x today against
# an own-history median of 0.06x — an enterprise costing six weeks of operating profit.
# No going concern with positive EBIT is priced below one year of it; a multiple under 1x
# is a unit or currency fault (THB statements against a USD enterprise value, or the
# reverse), which is exactly what this guard is for.
MULTIPLE_MIN = 1.0


def _abstain(note: str, *, covered: int = 0, total: int = 0,
             years: float = 0.0) -> ValuationBand:
    return ValuationBand(note=note, months_covered=covered, months_total=total,
                         years_covered=years)


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
def month_end_closes(bars: Sequence, start: date, end: date) -> list[tuple[date, float]]:
    """The last close of each calendar month in ``[start, end]``, oldest-first.

    Uses the SPLIT-ADJUSTED ``close`` (not ``adj_close`` — see the module docstring).
    Non-positive/absent closes are dropped. Deterministic regardless of input order."""
    per_month: dict[tuple[int, int], tuple[date, float]] = {}
    for b in bars:
        d = getattr(b, "day", None)
        c = getattr(b, "close", None)
        if d is None or c is None or c <= 0 or d < start or d > end:
            continue
        key = (d.year, d.month)
        prev = per_month.get(key)
        if prev is None or d > prev[0]:
            per_month[key] = (d, float(c))
    return [per_month[k] for k in sorted(per_month)]


def _dated_series(f, *names: str) -> tuple[list[date], list[Optional[float]]]:
    """The first present dated statement series among ``names``, newest-first.

    Reads ``aligned_period_ends`` / ``aligned_annual`` — the ONLY statement source with
    period-end dates, so the only one that can be applied point-in-time. Returns
    ``([], [])`` when the provider supplied none (the band then abstains, honestly)."""
    ends = getattr(f, "aligned_period_ends", None) or {}
    vals = getattr(f, "aligned_annual", None) or {}
    for name in names:
        raw_dates = ends.get(name) or []
        raw_vals = vals.get(name) or []
        if not raw_dates or len(raw_dates) != len(raw_vals):
            continue
        dates: list[date] = []
        values: list[Optional[float]] = []
        for d, v in zip(raw_dates, raw_vals):
            parsed = _parse_date(d)
            if parsed is None:
                continue
            dates.append(parsed)
            values.append(None if v is None else float(v))
        if any(v is not None for v in values):
            return dates, values
    return [], []


def _parse_date(value: object) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _asof(series: tuple[list[date], list[Optional[float]]], when: date
          ) -> Optional[float]:
    """The newest value whose period end + reporting lag is on or before ``when``.

    Point-in-time by construction: a statement is not used in the months before a
    reader could have had it."""
    dates, values = series
    best: Optional[tuple[date, float]] = None
    for d, v in zip(dates, values):
        if v is None or d + timedelta(days=REPORTING_LAG_DAYS) > when:
            continue
        if best is None or d > best[0]:
            best = (d, v)
    return None if best is None else best[1]


# --------------------------------------------------------------------------- #
# The band
# --------------------------------------------------------------------------- #
def _statement_note(f, earnings) -> str:
    """" (4 annual statements seen, fiscal year ends June)" — what the band actually had.

    BAND-STMT-1. Procter & Gamble read "insufficient history: 3.0y" on a company with
    decades of published statements, which reads as a fact about P&G and is a fact about
    the PROVIDER: it returned four annual periods, on a June fiscal year. The bare span
    sends a reader to check the company; naming what was seen sends them to check the feed,
    which is where the problem is.

    Empty when the dates are not available, so the reason degrades to what it said before
    rather than to a wrong claim about them."""
    dates = [d for d in (earnings[0] if earnings else []) if d is not None]
    if not dates:
        return ""
    months = {d.month for d in dates}
    month_note = ""
    if len(months) == 1:
        month_note = f", fiscal year ends {calendar.month_name[dates[0].month]}"
    return (f" ({len(dates)} annual statement{'s' if len(dates) != 1 else ''} seen"
            f"{month_note}; the band needs {BAND_YEARS} years)")


# BAND-STMT-2 — how close the first statement and the first price have to be before we
# call a company young rather than a feed short.
_LISTING_MATCH_DAYS = 365
# ...and how far inside the requested window the price history must genuinely BEGIN.
_WINDOW_EDGE_DAYS = 90


def _young_company_reason(earnings, bars, *, asof: date, years: int) -> str:
    """"only 3 years of statements exist for this company; the band needs 5, so it is not
    stated" — when the statements are short because the COMPANY is, or ``""``.

    BAND-STMT-2. BAND-STMT-1 made the abstention name what it saw ("4 annual statements
    seen, fiscal year ends June"), which sends a reader to check the FEED — right for
    Procter & Gamble, wrong for Secure Waste, where the same sentence reads like a data
    fault about a company that has simply not existed very long.

    The test is that the statements and the price history START TOGETHER. And it needs a
    second condition, which is the whole difficulty: we ask for ``years`` of bars, so for
    any company older than the window the earliest bar we HOLD is the window edge, and
    P&G's four statements would sit within a year of it and read as a young company. So
    the price history must also genuinely begin INSIDE the window rather than at its edge.
    A company older than the window therefore always falls through to BAND-STMT-1's
    wording, which is the safe direction: naming what the feed returned is never wrong,
    it is only less kind.
    """
    dates = sorted(d for d in (earnings[0] if earnings else []) if d is not None)
    days = sorted(getattr(b, "day", None) for b in (bars or [])
                  if getattr(b, "day", None) is not None)
    if not dates or not days:
        return ""
    window_start = date(asof.year - years, asof.month, asof.day)         if asof.day != 29 or asof.month != 2 else date(asof.year - years, 3, 1)
    first_price, first_statement = days[0], dates[0]
    if (first_price - window_start).days < _WINDOW_EDGE_DAYS:
        return ""                     # the series is window-clipped; it says nothing
    if abs((first_statement - first_price).days) > _LISTING_MATCH_DAYS:
        return ""                     # statements start well away from the listing: a gap
    n = len(dates)
    return (f"only {n} year{'s' if n != 1 else ''} of statements exist for this company; "
            f"the band needs {BAND_YEARS}, so it is not stated")


def valuation_band(bars: Sequence, fundamentals, *, asof: date,
                   years: int = BAND_YEARS,
                   min_years: float = MIN_YEARS,
                   fx=None) -> ValuationBand:
    """Today's valuation as a percentile of the stock's own ``years``-year band.

    ``bars`` is any sequence of ``PriceBar``-shaped objects (``.day``, ``.close``);
    ``fundamentals`` is a ``Fundamentals`` (its DATED statement series are what make
    the history point-in-time). Never raises on thin/absent data — it abstains with a
    reason."""
    f = fundamentals
    if f is None:
        return _abstain("no fundamentals")

    price_ccy = getattr(f, "currency", None)
    acct_ccy = getattr(f, "financial_currency", None)
    cross_currency = bool(price_ccy and acct_ccy and price_ccy != acct_ccy)
    if cross_currency and (fx is None or not getattr(fx, "available", False)):
        # VALBAND-2: the abstention NAMES what was missing, so "no FX" is actionable
        # instead of mysterious. v1 abstained here unconditionally; it now only abstains
        # when no rate could be had for the window at all.
        pairs = ", ".join(getattr(fx, "missing_pairs", ()) or ()) if fx is not None else ""
        detail = f" — no rates from {pairs}" if pairs else ""
        return _abstain(f"accounts currency {acct_ccy} vs price currency {price_ccy}"
                        f"{detail} — band not evaluated")

    start = asof - timedelta(days=round(365.25 * years))
    points = month_end_closes(bars, start, asof)
    total = len(points)
    if total < 2:
        return _abstain(f"no monthly price history in the {years}-year window",
                        total=total)

    mcap_now = getattr(f, "market_cap", None)
    price_now = points[-1][1]
    if mcap_now is None or mcap_now <= 0:
        return _abstain("market cap unavailable — the price history cannot be scaled "
                        "to a valuation", total=total)

    basis, earnings, net_debt_basis, debt, cash = _choose_basis(f)
    if basis is None:
        return _abstain("no dated statement history (EBIT or net income) — the band "
                        "cannot be placed in time", total=total)

    series: list[tuple[date, float]] = []
    fx_missing = 0
    for day, close in points:
        e = _asof(earnings, day)
        if e is None or e <= 0:
            continue                       # loss / pre-history month: drops out, counted
        # VALBAND-2: the price side is already in the PRICE currency; the statement side
        # is in the ACCOUNTS currency. Convert the statement side at THIS MONTH's rate —
        # never today's, which would rescale the whole history by a currency move and
        # reshape the band. A month with no rate is dropped and counted, never guessed.
        rate = 1.0
        if cross_currency:
            rate = fx.rate_for(day)
            if rate is None or rate <= 0:
                fx_missing += 1
                continue
        mcap = mcap_now * (close / price_now)
        if basis == _EV_EBIT:
            nd = _net_debt(f, debt, cash, net_debt_basis, day)
            if nd is None:
                continue
            value = (mcap + nd * rate) / (e * rate)
        else:
            value = mcap / (e * rate)
        if value <= 0:
            continue                       # net-cash EV <= 0: not a meaningful multiple
        series.append((day, value))

    covered = len(series)
    # BAND-STMT-2 — a genuinely young company gets its own sentence INSTEAD of BAND-STMT-1's
    # "what the feed returned", because for it the feed returned everything there is.
    young = _young_company_reason(earnings, bars, asof=asof, years=years)
    stmt = _statement_note(f, earnings)          # BAND-STMT-1
    if covered < 2:
        return _abstain(
            young or f"insufficient history: band from {covered} of {total} months{stmt}",
            covered=covered, total=total)

    span = (series[-1][0] - series[0][0]).days / 365.25
    if span < min_years:
        return _abstain(young or f"insufficient history: {span:.1f}y{stmt}",
                        covered=covered, total=total, years=span)
    months_in_span = sum(1 for d, _ in points if series[0][0] <= d <= series[-1][0])
    if months_in_span and covered / months_in_span < MIN_COVERAGE:
        return _abstain(f"insufficient coverage: band from {covered} of "
                        f"{months_in_span} months in span", covered=covered,
                        total=total, years=span)
    if (asof - series[-1][0]).days > STALE_CURRENT_DAYS:
        return _abstain("current valuation not computable "
                        f"(newest usable month {series[-1][0].isoformat()})",
                        covered=covered, total=total, years=span)

    values = [v for _, v in series]
    current = values[-1]
    # BAND-SANITY-1 — a multiple this far outside the plausible range is an input fault,
    # not a reading. MODEC came back at EV/EBIT 1144x and PTTEP at 0.6x, and both were
    # rendered as percentiles with exactly the authority of a sane row. Checked on the
    # MEDIAN too: a believable current multiple against an absurd own-history median gives
    # an equally absurd percentile.
    _median_now = _median(values)
    for _label, _value in (("", current), ("own 5-year median ", _median_now)):
        if _value is None:
            continue
        if _value > MULTIPLE_MAX or _value < MULTIPLE_MIN:
            # ".2f" below 10x: "0x" would hide which end of the range it failed.
            _shown = f"{_value:.2f}" if _value < 10 else f"{_value:.0f}"
            return _abstain(
                f"multiple implausible ({_label}{_shown}x); inputs suspect, "
                "not stated", covered=covered, total=total, years=span)
    # PRICE-1: record (never recompute) the reversion inputs — the median of THIS series
    # and the three point-in-time quantities the CURRENT point above was built from, all
    # taken at the same month `current_day`. Pure bookkeeping: no value below feeds the
    # percentile, the coverage counts or any abstention.
    current_day = series[-1][0]
    current_rate = (fx.rate_for(current_day) if cross_currency and fx is not None
                    else 1.0)
    return ValuationBand(
        fx_note=(fx.provenance() if cross_currency and fx is not None else ""),
        fx_months_missing=fx_missing,
        fx_rate_current=current_rate,
        fx_rate_asof=current_day,
        fx_pair=((fx.direct_pair if fx.source_for(current_day) != "inverted"
                  else fx.reverse_pair) if cross_currency and fx is not None else ""),
        quoted_units=(mcap_now / price_now if price_now else None),
        percentile=_percentile(values, current), basis=basis, current=current,
        months_covered=covered, months_total=total, years_covered=span,
        window_years=years, net_debt_basis=(net_debt_basis if basis == _EV_EBIT else ""),
        median_multiple=_median(values),
        current_point=current_day,
        current_earnings=_asof(earnings, current_day),
        current_net_debt=(_net_debt(f, debt, cash, net_debt_basis, current_day)
                          if basis == _EV_EBIT else None),
        current_shares=_asof(_dated_series(f, "shares_outstanding"), current_day),
        note=f"{_BASIS_PHRASE[basis]} over {span:.1f}y; "
             f"{covered} of {total} months computable")


def _choose_basis(f):
    """(basis, earnings_series, net_debt_basis, debt_series, cash_series).

    EV/EBIT wins whenever BOTH a dated EBIT series and a determinable net debt exist;
    otherwise the labelled P/E fallback; otherwise (None, …). Net debt is NEVER
    defaulted to zero — an undeterminable net debt sends the band to the P/E route
    rather than quietly pretending the company is debt-free."""
    ebit = _dated_series(f, "ebit", "operating_income")
    net_income = _dated_series(f, "net_income")
    debt = _dated_series(f, "total_debt")
    cash = _dated_series(f, "cash")
    if ebit[0]:
        if debt[0] and cash[0]:
            return _EV_EBIT, ebit, "asof", debt, cash
        if getattr(f, "total_debt", None) is not None and \
                getattr(f, "total_cash", None) is not None:
            return _EV_EBIT, ebit, "latest", debt, cash
    if net_income[0]:
        return _PE, net_income, "", debt, cash
    if ebit[0]:
        # EBIT but no usable net debt and no net-income series: the EV route would have
        # to invent a balance sheet. Abstain rather than fabricate.
        return None, ebit, "", debt, cash
    return None, ebit, "", debt, cash


def _net_debt(f, debt, cash, mode: str, when: date) -> Optional[float]:
    """Net debt (total debt − cash) as known at ``when``.

    "asof": from the dated statements, point-in-time. "latest": the current scalars
    held constant across the window — a DISCLOSED assumption (it rides into the display
    string), used only when the provider gives no dated debt/cash."""
    if mode == "asof":
        d = _asof(debt, when)
        c = _asof(cash, when)
        return None if d is None or c is None else d - c
    if mode == "latest":
        d = getattr(f, "total_debt", None)
        c = getattr(f, "total_cash", None)
        return None if d is None or c is None else d - c
    return None


def _median(values: list[float]) -> Optional[float]:
    """The MEDIAN of the band's own monthly multiples (PRICE-1).

    The plain statistical median — the middle value, or the mean of the two middle
    values on an even count. Chosen because the 50th percentile is the SAME distribution
    the band already reports today's multiple against, so "70th percentile" and
    "reversion to the median" are two readings of one series rather than two opinions.
    None on an empty series (unreachable from ``valuation_band``, which abstains below
    two computable months)."""
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def _percentile(values: list[float], current: float) -> float:
    """Mid-rank percentile of ``current`` within ``values`` (which INCLUDES it).

    below + half the ties, over n. A flat history puts today at exactly 50.0; a fresh
    all-time-high at just under 100. Deterministic — no interpolation, no sampling."""
    n = len(values)
    below = sum(1 for v in values if v < current)
    equal = sum(1 for v in values if v == current)
    return round(100.0 * (below + 0.5 * equal) / n, 1)
