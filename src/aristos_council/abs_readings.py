"""ABS-READINGS-1 — two readings that depend on no comparison group.

Every lens in this repo is RELATIVE: a BUY means "near the top of the list this ran on".
That is the right answer to "which of these forty" and no answer at all to "what is this
company like", which is why a portfolio run ranking a chip maker against a utility says
nothing about either. These two readings say something about one company on its own.

They are not lenses. They do not vote, they do not enter the shortlist, and no strategy
selects them. They are facts about a balance sheet and a track record, stated in the
units they were reported in.

The contract is the repo's usual one: a pure function over adapter fields, ABSTAINING
with a stated reason rather than returning a number it cannot stand behind. Infinity is
never reported (a company with no interest bill does not have infinite cover — it has no
ratio), a compound rate is never taken across a negative start, and every figure says how
many years it actually used rather than how many it wanted.
"""
from __future__ import annotations

from aristos_council.plurals import plural, verb

import logging
import statistics
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .tools.price_context import format_money, round_half_up

_log = logging.getLogger(__name__)

# Batch 8 - a statement that names no currency is said to, never assumed. "owes 27.4bn GBp net of
# cash" printed the LISTING's currency (pence) beside AstraZeneca's dollar accounts; the accounts'
# currency is the one the figure is in.
CURRENCY_NOT_STATED = "currency not stated by the source"

# A reading needs at least this many years before a compound rate means anything. Two
# points is a line, not a trend.
MIN_GROWTH_YEARS = 3
GROWTH_WINDOWS = (5, 10)

# ABS-READINGS-2 — above this, interest cover stops being information. The difference
# between 60x and 500x is not a difference a reader can act on; both mean the interest
# bill does not matter. The exact value stays on the Reading for anything reading it
# programmatically — only the SENTENCE changes.
INTEREST_IMMATERIAL_ABOVE = 50.0


@dataclass(frozen=True)
class Reading:
    """One figure, or an honest absence.

    ``value is None`` and ``note`` non-empty is the abstention; the two are never both
    set. ``label`` is the plain-English sentence the report prints — the arithmetic is in
    the value, the meaning is in the label, and neither is left for a reader to infer.
    """

    value: Optional[float] = None
    note: str = ""
    label: str = ""
    unit: str = ""
    # ABS-READINGS-2 — how many years this figure actually spanned. Carried so the
    # renderer can tell that the 5-year and 10-year windows collapsed onto the same
    # history and print ONE line instead of two identical ones.
    span: Optional[int] = None
    # GROWTH-SANITY-1 (Batch 15 item 3a) — a visible caveat on an otherwise-plain figure
    # (e.g. "CAUTION: starting year unusually low"). A SEPARATE field, not baked into
    # ``label``'s text, so a renderer that reconstructs ``label`` for its own reasons
    # (GrowthLeg.lines()'s window-collapse branch below) cannot silently lose it.
    caution: str = ""
    # CAGR-CRASH-1 — set only when computing the reading RAISED. Distinct from ``note`` (an
    # honest abstention: the figure is undefined) because a crash is a defect to be seen,
    # not a finding about the company; it renders as "not available: <reason>".
    failure: str = ""
    # BANK-PAGE-1 - set when the measure does not describe this kind of company at all (debt and
    # free cash flow for a bank or insurer). Neither a figure nor an abstention for missing data:
    # the sentence itself is printed, with no "not stated" in front of it.
    not_meaningful: str = ""

    @property
    def available(self) -> bool:
        return self.value is not None

    def text(self) -> str:
        if self.not_meaningful:
            return self.not_meaningful
        if self.failure:
            return f"not available: {self.failure}"
        if not self.available:
            return f"not stated — {self.note}"
        return f"{self.label}{self.caution}" if self.caution else self.label


def dedupe_lines(lines) -> list[str]:
    """The same sentence, said once. ABS-READINGS-3 item 2.

    Two real cases: AT&T's "earnings per share was not positive 3 years ago" appeared for
    both the 5- and the 10-year window, and NVIDIA's "has no net debt to repay" appeared
    for both the operating-cash-flow and the free-cash-flow line. Repeating a sentence
    does not make it truer; it makes the page look like it is padding.

    Order is preserved and every Reading underneath is untouched - this is a rendering
    decision, exactly like the span collapse.
    """
    seen: set = set()
    out: list[str] = []
    for line in lines:
        key = " ".join(str(line).split()).casefold()
        if key and key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out


NOT_MEANINGFUL_FOR_FINANCIALS = ("not meaningful for banks and insurers (deposits and loans are "
                                 "the business)")


def is_financial_sector(f) -> bool:
    """True only for a CONFIRMED financial-sector company (a missing sector is never one)."""
    from .factors import is_sector_excluded
    return f is not None and is_sector_excluded(getattr(f, "sector", None),
                                                ["Financial Services", "Financials"])


# BANK-LABEL-1 (19B B9): the replaced line says WHICH measure it replaces. A bare "not meaningful for
# banks and insurers" under a block heading reads as a sentence about nothing in particular.
FREE_CASH_FLOW_NOT_MEANINGFUL = f"free cash flow: {NOT_MEANINGFUL_FOR_FINANCIALS}"
DEBT_AND_CASH_NOT_MEANINGFUL = f"debt and cash: {NOT_MEANINGFUL_FOR_FINANCIALS}"


def _not_meaningful(measure_line: str = NOT_MEANINGFUL_FOR_FINANCIALS) -> Reading:
    return Reading(not_meaningful=measure_line)


def _abstain(note: str) -> Reading:
    return Reading(value=None, note=note)


def _failed(exc: BaseException) -> Reading:
    """A reading whose computation raised: shown as "not available: <reason>", never a crash."""
    reason = " ".join(f"{type(exc).__name__}: {exc}".split())
    return Reading(value=None, failure=reason[:200])


def _safe(fn, *args, **kwargs) -> Reading:
    """Run one reading; any exception becomes a visible "not available" Reading so the rest
    of the page still renders (CAGR-CRASH-1)."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:                       # noqa: BLE001 - the point is to contain it
        return _failed(exc)


def _num(x) -> Optional[float]:
    """A float, or None for anything that is not a real number (NaN included)."""
    if x is None or isinstance(x, bool):
        return None
    try:
        value = float(x)
    except (TypeError, ValueError):
        return None
    return None if value != value else value          # NaN is not a reading


def _series(f, name: str) -> list[Optional[float]]:
    """A dated annual series off ``Fundamentals``, newest-first, holes preserved.

    ``aligned_annual`` is the period-labelled source (PIOTROSKI-2) — the positional
    fields drop NaN cells and lose the value-to-year correspondence, which is exactly what
    a growth record must not do.
    """
    aligned = getattr(f, "aligned_annual", None) or {}
    if name in aligned:
        return [_num(v) for v in (aligned.get(name) or [])]
    return [_num(v) for v in (getattr(f, name, None) or [])]


# --------------------------------------------------------------------------- #
# A. debt and cash
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DebtAndCash:
    net_debt: Reading = field(default_factory=Reading)
    net_debt_to_ocf: Reading = field(default_factory=Reading)
    interest_cover: Reading = field(default_factory=Reading)
    years_to_repay: Reading = field(default_factory=Reading)
    currency: str = ""
    # CASH-RUNWAY-1: how long the net cash lasts at last year's spending - only for a company that spent
    # more cash than it made. Empty (no line anywhere) for positive free cash flow, a bank or a gap.
    cash_runway: Reading = field(default_factory=Reading)
    # FINANCE-ARM-1: set for a company whose debt is mostly a captive finance arm's (data/finance_arms.yaml)
    finance_arm_note: str = ""

    def notes(self) -> list[str]:
        """The muted line(s) under the debt readings, said ONCE (like the growth record's notes)."""
        return [self.finance_arm_note] if self.finance_arm_note else []

    def lines(self) -> list[str]:
        # ABS-READINGS-3 - NVIDIA printed "has no net debt to repay" twice, once for the
        # operating-cash-flow reading and once for the free-cash-flow one.
        readings = [self.net_debt, self.net_debt_to_ocf, self.interest_cover, self.years_to_repay]
        if self.cash_runway.available:
            readings.append(self.cash_runway)
        return dedupe_lines([r.text() for r in readings])


def _money(value: float, currency: str) -> str:
    """One amount through the repo's ONE money formatter (``$27.4bn``, ``CHF 5.2bn``). With no
    currency it says so in words rather than printing a bare number that reads as dollars."""
    text = format_money(value, currency or None, abbreviate=True)
    return text if currency else f"{text} ({CURRENCY_NOT_STATED})"


def _balance_sheet_date(f) -> str:
    """"balance sheet of Jun 2026" - or "latest balance sheet" when the provider gave no period end."""
    end = str(getattr(f, "trailing_period_end", "") or "")
    try:
        from datetime import datetime
        return f"balance sheet of {datetime.strptime(end[:10], '%Y-%m-%d').strftime('%b %Y')}"
    except ValueError:
        return "latest balance sheet"


def cash_runway(f, *, debt, cash, net, fcf, currency: str) -> Reading:
    """CASH-RUNWAY-1 (approved 2026-10-04): a fact, never a vote.

    * free cash flow negative AND net cash positive: how long that cash lasts at last year's spending;
    * free cash flow negative AND net debt: "no cash cushion: spending is funded by debt";
    * free cash flow positive, missing, or the company a bank: no reading at all.
    A missing debt or cash figure also gives no reading (null is not false: it cannot be said)."""
    if fcf is None or fcf >= 0 or net is None or debt is None or cash is None:
        return Reading()
    if net > 0:
        return Reading(value=0.0, unit="years",
                       label="no cash cushion: spending is funded by debt")
    years = (-net) / (-fcf)
    return Reading(value=years, unit="years",
                   label=(f"at last year's spending ({_money(-fcf, currency)}) that lasts about "
                          f"{years:.1f} years ({_balance_sheet_date(f)})"))


def debt_and_cash(f) -> DebtAndCash:
    """What the balance sheet owes and how long the cash flow would take to clear it."""
    if f is None:
        return DebtAndCash(net_debt=_abstain("no fundamentals"),
                           net_debt_to_ocf=_abstain("no fundamentals"),
                           interest_cover=_abstain("no fundamentals"),
                           years_to_repay=_abstain("no fundamentals"))

    # BANK-PAGE-1: a bank's or insurer's "debt" is its funding and its "cash" is customers' money, so
    # "holds $183bn more cash than debt" says nothing. Say that instead of printing it.
    if is_financial_sector(f):
        nm = _not_meaningful(DEBT_AND_CASH_NOT_MEANINGFUL)
        return DebtAndCash(net_debt=nm, net_debt_to_ocf=nm, interest_cover=nm, years_to_repay=nm,
                           currency=str(getattr(f, "financial_currency", "") or "").strip())

    # The currency of the ACCOUNTS, never the listing's: AstraZeneca is quoted in pence and reports
    # in dollars, and Novo's kroner reached a report as "USD 128.3bn" the same way. Absent ->
    # "currency not stated by the source"; it is never guessed from the quote currency.
    currency = str(getattr(f, "financial_currency", "") or "").strip()
    debt, cash = _num(getattr(f, "total_debt", None)), _num(getattr(f, "total_cash", None))

    # -- net debt ---------------------------------------------------------- #
    if debt is None:
        net = None
        net_reading = _abstain("total debt is not reported")
    else:
        # Cash absent is NOT cash zero (house rule 3): say so rather than overstating the
        # net position by the whole cash pile.
        net = debt - (cash or 0.0)
        gross = "" if cash is not None else " (cash not reported, so this is gross debt)"
        if net <= 0:
            net_reading = Reading(
                value=net, unit=currency,
                label=f"holds {_money(-net, currency)} more cash than debt{gross}")
        else:
            net_reading = Reading(
                value=net, unit=currency,
                label=f"owes {_money(net, currency)} net of cash{gross}")

    # -- net debt to operating cash flow ------------------------------------ #
    ocf = _num(getattr(f, "operating_cash_flow", None))
    if ocf is None:
        series = [v for v in _series(f, "operating_cash_flow") if v is not None]
        ocf = series[0] if series else None
    if net is None:
        ratio = _abstain("total debt is not reported")
    elif net <= 0:
        ratio = Reading(value=0.0, unit="years",
                        label="has no net debt to repay")
    elif ocf is None:
        ratio = _abstain("operating cash flow is not reported")
    elif ocf <= 0:
        ratio = _abstain("operating cash flow is not positive, so there is no number of "
                         "years that would repay the debt")
    else:
        years = net / ocf
        ratio = Reading(value=years, unit="years",
                        label=f"would take {years:.1f} years of operating cash flow to "
                              f"repay its debt")

    # -- interest cover ----------------------------------------------------- #
    operating = _num(getattr(f, "operating_income", None))
    if operating is None:
        op_series = [v for v in _series(f, "operating_income") if v is not None]
        operating = op_series[0] if op_series else None
    interest_series = [v for v in _series(f, "interest_expense") if v is not None]
    interest = abs(interest_series[0]) if interest_series else None
    if operating is None:
        cover = _abstain("operating income is not reported")
    elif interest is None:
        cover = _abstain("interest expense is not reported")
    elif interest == 0:
        # NOT infinity. A company with no interest bill has no ratio, and printing "inf"
        # or a huge number would read as a strength measured on the same scale as a real
        # one. The absence is the statement.
        cover = _abstain("it reports no interest expense, so there is no ratio to state")
    else:
        times = operating / interest
        if times > INTEREST_IMMATERIAL_ABOVE:
            # NVIDIA reads 503.4x. "Earns 503.4 times its interest bill" invites a reader
            # to compare it with a company at 60x as though that were a ranking; it is
            # not. Both have no interest problem. The value is kept.
            label = (f"interest is immaterial (covered more than "
                     f"{INTEREST_IMMATERIAL_ABOVE:.0f} times over)")
        else:
            label = f"earns {times:.1f} times its interest bill"
        cover = Reading(value=times, unit="x", label=label)

    # -- years to repay from free cash flow --------------------------------- #
    fcf = _num(getattr(f, "free_cash_flow", None))
    if fcf is None:
        fcf_series = [v for v in _series(f, "free_cash_flow_annual") if v is not None]
        fcf = fcf_series[0] if fcf_series else None
    if net is None:
        repay = _abstain("total debt is not reported")
    elif net <= 0:
        repay = Reading(value=0.0, unit="years", label="has no net debt to repay")
    elif fcf is None:
        repay = _abstain("free cash flow is not reported")
    elif ocf is not None and fcf > ocf:
        # ABS-READINGS-2. Free cash flow is operating cash flow minus capital spending, so
        # it is never larger and the repayment period from it is never shorter. Netflix
        # printed 0.7 years from operating cash flow and 0.3 from free cash flow on the
        # same page. The adapter now takes the statement figure, which fixes the cause —
        # this guard is here because an impossible number must never be printed whatever
        # the cause, including a provider we have not met yet.
        repay = _abstain("the reported free cash flow is larger than operating cash "
                         "flow, so the two figures disagree and neither is used here")
    elif fcf <= 0:
        repay = _abstain("free cash flow is not positive, so debt is not being repaid "
                         "out of it at all")
    else:
        years = net / fcf
        repay = Reading(value=years, unit="years",
                        label=f"would take {years:.1f} years of free cash flow to repay "
                              f"its debt")

    runway = cash_runway(f, debt=debt, cash=cash, net=net, fcf=fcf, currency=currency)
    from .finance_arms import finance_arm_note
    return DebtAndCash(net_debt=net_reading, net_debt_to_ocf=ratio,
                       interest_cover=cover, years_to_repay=repay, currency=currency,
                       cash_runway=runway,
                       finance_arm_note=finance_arm_note(getattr(f, "ticker", "")))


# --------------------------------------------------------------------------- #
# B. the growth record
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GrowthLeg:
    """One measure's record: compound rates over the windows, and how often it rose."""

    name: str = ""
    # ABS-READINGS-3 - which feed supplied the series. yfinance returns four annual
    # periods; EODHD returned 41 for Coca-Cola. A reader comparing a 10-year rate with a
    # 3-year one needs to know which they are looking at.
    source_tag: str = ""
    cagr: dict = field(default_factory=dict)        # window -> Reading
    grew_in: Reading = field(default_factory=Reading)
    years_available: int = 0

    def lines(self) -> list[str]:
        """One line per DISTINCT span, then the growth count.

        ABS-READINGS-2: with four annual periods on file, the 5-year and 10-year windows
        both fall back to a 3-year span and printed the identical sentence twice. Saying
        it once, and naming both windows that could not be filled, is the same information
        without the stutter.
        """
        out: list[str] = []
        windows = sorted(self.cagr)
        available = [self.cagr[w] for w in windows if self.cagr[w].available]
        spans = {r.span for r in available}
        if len(available) == len(windows) and len(spans) == 1 and len(windows) > 1:
            reading = available[0]
            span = reading.span
            # "5- nor the 10-" + "year" -> "5- nor the 10-year". The rstrip that was
            # here ate the second hyphen and printed "10year".
            asked = " nor the ".join(f"{w}-" for w in windows)
            # GROWTH-SANITY-1: reading.label.split(" (only ")[0] drops everything AFTER
            # "(only ...)" too, which would silently eat reading.caution if it were baked
            # into label — it isn't (caution is its own field), so it is re-appended here.
            head = reading.label.split(" (only ")[0] + reading.caution
            if span in windows:
                out.append(head)                     # a window was genuinely filled
            else:
                out.append(f"{head} — only {plural(span, 'year')} of accounts are "
                           f"available, so neither the {asked}year window could be "
                           f"filled")
        else:
            out.extend(self.cagr[w].text() for w in windows)
        out.append(self.grew_in.text())
        # ...and AT&T's identical EPS abstention for both windows.
        return dedupe_lines(out)


@dataclass(frozen=True)
class GrowthRecord:
    revenue: GrowthLeg = field(default_factory=GrowthLeg)
    eps: GrowthLeg = field(default_factory=GrowthLeg)
    # Where the series came from ("source: EODHD, 36 annual reports"). Batch 8: this is a SOURCE,
    # so it lives in the page's one Sources block, not beside the figures it describes.
    source_tag: str = ""
    # Reports on file / the window the record uses, and how EPS was obtained.
    years_on_file: int = 0
    eps_derived: bool = False

    def lines(self) -> list[str]:
        return dedupe_lines(self.revenue.lines() + self.eps.lines())

    def notes(self) -> list[str]:
        """The two things a reader needs to know about the lines above, said ONCE under them:
        how much history stands behind the record and how EPS was obtained. (Before, "source:
        EODHD, 36 annual reports" sat beside "grew in 8 of the 10 years", which read as a
        contradiction, and "(derived from net income and share count)" was printed on three
        lines.)"""
        out: list[str] = []
        if self.years_on_file:
            used = max(GROWTH_WINDOWS)
            out.append(f"{plural(self.years_on_file, 'year')} on file, last {used} used"
                       if self.years_on_file > used
                       else f"{self.years_on_file} year{'s' if self.years_on_file != 1 else ''} "
                            f"on file, all used")
        if self.eps_derived:
            out.append("earnings per share is derived from net income and share count")
        return out


# GROWTH-SANITY-1 (Batch 15 item 3a) — live, EL.PA 2026-10-03: EPS compounded +90.9%/yr
# over 5 years against +3.5%/yr over 10 — almost certainly a low starting year (a
# Covid-era or post-merger trough), not a durable growth rate, and the council read it
# plain. Like VALBAND-1's own sanity bound on an implausible band reading, this never
# WITHHOLDS the figure (the rate is what the math says) — it makes the READING carry a
# visible caution instead of reading as a plain, durable number. Two independent
# triggers, either sufficient, because both point at the same risk (a trough-year base)
# from different angles: the annualised rate itself is implausibly fast, OR the start
# point is an outlier against the series' own typical level.
_EXTREME_CAGR = 0.50          # 50%/yr — durable organic growth this fast is rare
_LOW_BASE_RATIO = 0.30        # the start point is < 30% of the series' own median

_BASE_YEAR_CAUTION = (" (CAUTION: starting year unusually low — this compound rate may "
                      "reflect a trough base rather than a durable trend)")


def _needs_base_year_caution(present: list[float], start: float, rate: float) -> bool:
    if abs(rate) > _EXTREME_CAGR:
        return True
    typical = statistics.median(present)
    return typical > 0 and start < _LOW_BASE_RATIO * typical


def _cagr(series: Sequence[Optional[float]], window: int, label: str) -> Reading:
    """Compound annual rate over at most ``window`` years, stating the span it used.

    ``series`` is NEWEST-FIRST. Holes are dropped, which is why the span is reported: a
    company with 2018 and 2024 but nothing between has six years of span on two points,
    and saying "over 6 years" is honest where "10-year CAGR" would not be.
    """
    present = [v for v in series if v is not None]
    if len(present) < MIN_GROWTH_YEARS:
        return _abstain(f"only {plural(len(present), 'year')} of {label} reported; a compound "
                        f"rate needs at least {MIN_GROWTH_YEARS}")
    end = present[0]
    span = min(window, len(present) - 1)
    start = present[span]
    if start is None or start <= 0:
        # A root of a negative ratio is not a number, and a rate off a negative base is
        # not a growth rate. This is the PEG/CAGR discipline the criteria already use.
        return _abstain(f"{label} was not positive {plural(span, 'year')} ago, so a compound rate "
                        f"is not defined")
    if end is None or end <= 0 or end != end:
        # CAGR-CRASH-1 (live: Novocure, Ford): a positive start and a loss in the latest
        # year gave (negative / positive) ** (1/span) — a COMPLEX number in Python — and
        # ``f"{rate:+.1%}"`` raised. A rate that ends at or below zero is not a growth rate.
        return _abstain(f"{label} was not positive in the latest year, so a compound rate "
                        f"is not defined")
    rate = (end / start) ** (1.0 / span) - 1.0
    if isinstance(rate, complex) or rate != rate:
        return _abstain(f"{label} compound rate is not defined for this series")
    used = "" if span == window else f" (only {span} of {plural(window, 'year')} available)"
    caution = _BASE_YEAR_CAUTION if _needs_base_year_caution(present, start, rate) else ""
    return Reading(value=rate, unit="/yr", span=span, caution=caution,
                   label=f"{label} compounded {rate:+.1%} a year over {plural(span, 'year')}{used}")


def _grew_in(series: Sequence[Optional[float]], label: str, window: int = 10) -> Reading:
    """"grew in 7 of the 10 years reported" — counted on consecutive reported pairs."""
    present = [v for v in series if v is not None][:window + 1]
    if len(present) < 2:
        return _abstain(f"not enough {label} history to count growth years")
    pairs = list(zip(present, present[1:]))          # (newer, older)
    rose = sum(1 for newer, older in pairs if newer > older)
    return Reading(value=float(rose), unit="years",
                   label=f"{label} grew in {rose} of the {plural(len(pairs), 'year')} reported")


def _eps_series(f) -> tuple[list[Optional[float]], str]:
    """Earnings per share, newest-first, and where it came from.

    Preferred: the reported EPS line. Fallback: net income over the share count, PERIOD
    MATCHED through the dated series — derived rather than trusted, and disclosed, which
    is the same fallback discipline ``earnings_yield`` uses when EV is unavailable.
    """
    reported = _series(f, "diluted_eps")
    if any(v is not None for v in reported):
        return reported, "reported"
    income = _series(f, "net_income")
    shares = _series(f, "shares_outstanding")
    if not income or not shares:
        return [], "unavailable"
    out: list[Optional[float]] = []
    for profit, count in zip(income, shares):
        out.append(profit / count if profit is not None and count else None)
    return out, "derived from net income and share count"


def growth_record(f, history=None) -> GrowthRecord:
    """Revenue and earnings per share over 5 and 10 years, plus how often each rose.

    ABS-READINGS-3: ``history`` is a ``growth_history.GrowthHistory`` - EODHD's long
    annual record - and is PREFERRED when it carries one. yfinance returns four annual
    periods, which made Coca-Cola report a three-year compound rate; EODHD returned 41.
    Without a history (no key, no coverage, a refused call) the yfinance series on
    ``Fundamentals`` is used exactly as before, and the tag says which it was.
    """
    if f is None:
        empty = GrowthLeg(cagr={w: _abstain("no fundamentals") for w in GROWTH_WINDOWS},
                          grew_in=_abstain("no fundamentals"))
        return GrowthRecord(revenue=empty, eps=empty)  # no source to name

    if history is not None and getattr(history, "available", False):
        revenue = list(history.revenue)
        eps, eps_source = list(history.eps), "derived from net income and share count"
        tag = history.tag()
    else:
        revenue = _series(f, "total_revenue")
        eps, eps_source = _eps_series(f)
        reports = len([v for v in revenue if v is not None])
        tag = (f"source: yfinance, {reports} annual report"
               f"{'s' if reports != 1 else ''}") if reports else ""
    eps_derived = eps_source not in ("reported", "unavailable")
    eps_label = "earnings per share"           # how it was derived is said once, in ``notes()``

    return GrowthRecord(
        source_tag=tag,
        years_on_file=len([v for v in revenue if v is not None]),
        eps_derived=eps_derived,
        revenue=GrowthLeg(
            name="revenue",
            cagr={w: _safe(_cagr, revenue, w, "revenue") for w in GROWTH_WINDOWS},
            grew_in=_safe(_grew_in, revenue, "revenue"),
            years_available=len([v for v in revenue if v is not None])),
        eps=GrowthLeg(
            name="eps",
            cagr={w: _safe(_cagr, eps, w, eps_label) for w in GROWTH_WINDOWS},
            grew_in=_safe(_grew_in, eps, eps_label),
            years_available=len([v for v in eps if v is not None])))


# --------------------------------------------------------------------------- #
# C. analyst forecast direction (ANALYST-TREND-1)
# --------------------------------------------------------------------------- #
# A MARK, not a lens: it does not vote, enters no shortlist, removes no name and changes no verdict.
# It says which way the analysts' consensus EPS for the CURRENT fiscal year has moved over about
# 90 days. The arithmetic is here, in a pure function over what the provider served, and nowhere
# near an agent. EPS is quoted in whatever currency the estimates are in; only a RATIO of two
# figures in that one currency is used, so the currency cancels.
ANALYST_FLAT_BAND = 0.02          # a move of at most +/-2% of |the 90-days-ago figure| is "flat"
ANALYST_MIN_ANALYSTS = 3          # fewer estimates than this is not a consensus
ANALYST_MIN_BASE = 0.01           # a 90-days-ago figure within one cent of zero has no percentage

MARK_RISING = "forecasts rising"
MARK_FLAT = "forecasts flat"
MARK_FALLING = "forecasts falling"


def _direction(now: float, ago: float) -> tuple[str, float]:
    """``(mark, fractional change)``; the change is relative to ``abs(ago)`` so a forecast that
    rises from -0.50 to -0.20 counts as rising, not falling. The band is inclusive, with a hair of
    tolerance so a move of exactly 2% is not tipped over by floating-point noise."""
    move = now - ago
    limit = ANALYST_FLAT_BAND * abs(ago)
    if abs(move) <= limit + 1e-12:
        mark = MARK_FLAT
    else:
        mark = MARK_RISING if move > 0 else MARK_FALLING
    return mark, move / abs(ago)


def _eps(value: float) -> str:
    return f"{value:.2f}"


HEADLINES = {
    MARK_RISING: "Analysts are raising their profit forecasts",
    MARK_FLAT: "Analysts are holding their profit forecasts steady",
    MARK_FALLING: "Analysts are cutting their profit forecasts",
}

# ANALYST-RATINGS-1 - the forecast wording, by the same rule as the mark (rising / flat within +/-2% /
# falling), said as a plain clause after "so ...".
_VIEW_CLAUSE = {
    MARK_RISING: "analysts have raised their forecasts",
    MARK_FLAT: "their view has barely changed",
    MARK_FALLING: "analysts have cut their forecasts",
}

RATINGS_HEADINGS = ("Strong buy", "Buy", "Hold", "Sell", "Strong sell", "Total")

# Quote codes for a MINOR unit (pence, cents, agorot) and the major unit they are a hundredth of.
_MINOR_UNITS = {"GBX": "GBP", "GBp": "GBP", "GBPENCE": "GBP", "GBP_PENCE": "GBP", "ZAc": "ZAR",
                "ZAC": "ZAR", "ILA": "ILS"}
# A target and a price in the SAME unit differ by a small factor; a hundredfold gap means one of them
# is in pence and the other in pounds. Outside this band the two are not compared.
_SAME_UNIT_RATIO = (0.25, 4.0)


def plain_company_name(name: str) -> str:
    """"AstraZeneca PLC" -> "AstraZeneca", "Micron Technology, Inc." -> "Micron Technology": the
    corporate suffix is not how a person says the name. Empty in, empty out.

    SHORT-NAME-1 (19B B8): cut at the first COMMA or at a legal suffix, never inside a name. "JPMorgan
    Chase & Co." lost its "Co." and read "JPMorgan Chase &" - a name cannot end on "&". When removing
    the suffixes would leave a dangling connector, the suffix stays: "JPMorgan Chase & Co."."""
    import re

    text = re.sub(r"\s*\(.*?\)\s*$", "", (name or "").strip())
    head = text.split(",", 1)[0].strip()
    text = head or text
    suffixes = {"inc", "inc.", "corp", "corp.", "corporation", "plc", "ltd", "ltd.", "limited", "sa",
                "s.a.", "ag", "nv", "n.v.", "se", "co", "co.", "company", "spa", "s.p.a.", "ab",
                "asa", "oyj", "as", "a/s", "kgaa", "sab", "holdings", "holding", "group"}
    words = text.replace(",", " ").split()
    kept = list(words)
    while len(kept) > 1 and kept[-1].lower() in suffixes:
        kept.pop()
    if kept and kept[-1].lower() in {"&", "and", "+", "/"}:       # never end on a connector
        return " ".join(words)
    return " ".join(kept)


@dataclass(frozen=True)
class RatingsView:
    """The analysts' rating counts and average price target, as the page says them.

    ``note`` is the reason when there is nothing to show; a target that could not be compared with
    the price says why in ``target_note`` and the counts still stand."""

    counts: tuple = ()                # strong buy, buy, hold, sell, strong sell
    total: int = 0
    target: Optional[float] = None    # in ``currency``, after any pence -> pounds step
    currency: str = ""
    price: Optional[float] = None     # today's price in the SAME currency and unit as ``target``
    target_note: str = ""
    note: str = ""
    listing: str = ""                 # the EODHD symbol these ratings describe
    own_listing: bool = True          # False when they are the same company's US line

    @property
    def available(self) -> bool:
        return bool(self.total)

    def summary_line(self) -> str:
        """"24 analysts: 9 strong buy, 8 buy, 6 hold, 1 sell, 0 strong sell"."""
        sb, b, h, s, ss = self.counts
        return (f"{plural(self.total, 'analyst')}: {sb} strong buy, {b} buy, {h} hold, {s} sell, "
                f"{ss} strong sell")

    def table(self) -> tuple[list[str], list[str]]:
        return list(RATINGS_HEADINGS), [str(n) for n in (*self.counts, self.total)]

    @property
    def target_sentence(self) -> str:
        """"Average price target £135.20, 12% above today's price" - or why there is none."""
        if self.target is None:
            return self.target_note or "No average price target is given."
        money = format_money(self.target, self.currency or None)
        if self.price is None or self.price <= 0:
            return (f"Average price target {money}"
                    + (f" ({self.target_note})" if self.target_note else ""))
        move = (self.target - self.price) / self.price
        if abs(move) < 0.005:
            gap = "about the same as today's price"
        else:
            gap = f"{abs(move):.0%} {'above' if move > 0 else 'below'} today's price"
        return f"Average price target {money}, {gap}"

    def lines(self) -> list[str]:
        if not self.available:
            return [f"Analyst ratings are not shown: {self.note}"]
        out = [self.summary_line()]
        if self.listing and not self.own_listing:
            out.append(f"(these are the ratings for the US listing {self.listing}; the company's "
                       f"own listing has none)")
        out.append(self.target_sentence)
        return out


def _minor(code: str) -> Optional[str]:
    return _MINOR_UNITS.get((code or "").strip())


def ratings_view(ratings, note: str, *, price: Optional[float] = None,
                 price_currency: str = "", own_listing: bool = True) -> RatingsView:
    """The ratings and the target against today's price - in ONE unit, or not at all.

    ``price`` is the latest close of the listing the RATINGS describe (the company's own, or its US
    line's), in ``price_currency`` as the price feed quotes it. The target has the currency of its
    listing. They are compared only when they are provably the same currency: equal codes, or a
    pence code on one side and its pound on the other (converted, both stated in pounds). A target
    whose currency is unknown, or that disagrees with the price's, is shown WITHOUT a comparison and
    says why - never converted, never guessed."""
    if ratings is None:
        return RatingsView(note=note or "no analyst ratings are available")
    counts = (ratings.strong_buy, ratings.buy, ratings.hold, ratings.sell, ratings.strong_sell)
    base = dict(counts=counts, total=ratings.total, listing=ratings.symbol, own_listing=own_listing)
    target, tcur = ratings.target_price, (ratings.currency or "").strip()
    if target is None:
        return RatingsView(**base, target_note="No average price target is given.")
    if not tcur:
        return RatingsView(**base, target_note=(
            "The currency of the average price target is not stated by the source, so it is not "
            "compared with the price."))
    # like with like: put target and price in one currency AND one unit, or abstain
    pcur = (price_currency or "").strip()
    t_major, t_scale = (_minor(tcur) or tcur), (100.0 if _minor(tcur) else 1.0)
    p_major, p_scale = (_minor(pcur) or pcur), (100.0 if _minor(pcur) else 1.0)
    money_target = target / t_scale
    if price is None or not pcur:
        return RatingsView(**base, target=money_target, currency=t_major,
                           target_note="today's price is not available to compare with")
    if t_major.upper() != p_major.upper():
        return RatingsView(**base, target=target, currency=tcur, target_note=(
            f"the target is in {tcur} but the price is in {pcur}, so they are not compared"))
    money_price = price / p_scale
    ratio = money_target / money_price if money_price else 0.0
    if not _SAME_UNIT_RATIO[0] <= ratio <= _SAME_UNIT_RATIO[1]:
        return RatingsView(**base, target=money_target, currency=t_major, target_note=(
            f"the target ({format_money(money_target, t_major)}) and the price "
            f"({format_money(money_price, p_major)}) disagree by a factor no target explains, so "
            f"they are not compared"))
    return RatingsView(**base, target=money_target, currency=t_major, price=money_price)


@dataclass(frozen=True)
class ForecastRow:
    """One fiscal year of the analysts' EPS forecast: ``label`` is "this year (to Dec 2026)"."""

    label: str
    now: Optional[float] = None
    ago_90d: Optional[float] = None
    analysts: Optional[int] = None
    currency: str = ""

    @property
    def change(self) -> Optional[float]:
        if self.now is None or self.ago_90d is None or abs(self.ago_90d) < ANALYST_MIN_BASE:
            return None
        return (self.now - self.ago_90d) / abs(self.ago_90d)

    @property
    def mark(self) -> str:
        if self.change is None:
            return ""
        return _direction(self.now, self.ago_90d)[0]

    def sentence(self, company: str, when: str) -> str:
        """"Analysts expect AstraZeneca to earn $10.25 per share this year. Three months ago they
        expected $10.30, so their view has barely changed." """
        who = company or "the company"
        money = lambda v: format_money(v, self.currency or None)
        head = f"Analysts expect {who} to earn {money(self.now)} per share {when}."
        if self.ago_90d is None or self.change is None:
            return (f"{head} There is no usable figure from three months ago to compare with.")
        return (f"{head} Three months ago they expected {money(self.ago_90d)}, so "
                f"{_VIEW_CLAUSE[self.mark]}.")


def _period_label(prefix: str, period_end: str) -> str:
    """"this year (to Dec 2026)" from a fiscal year-end date. Unparseable -> the bare prefix."""
    try:
        from datetime import date
        end = date.fromisoformat(str(period_end)[:10])
    except ValueError:
        return prefix
    return f"{prefix} (to {end.strftime('%b %Y')})"


@dataclass(frozen=True)
class AnalystTrend:
    """WHAT ANALYSTS SAY: the ratings first (counts and the average target against today's price),
    then the forecasts as plain sentences. A MARK - it does not vote and changes no verdict.

    The mark (rising / flat / falling for the current year), its +/-2% band and its abstentions are
    the earlier rule, unchanged; how many units the request cost is logged, not printed."""

    mark: str = ""                                    # one of MARK_*, or "" when abstaining
    direction: Reading = field(default_factory=Reading)
    source: str = ""
    as_of: str = ""
    units_charged: int = 0
    cached: bool = False
    rows: tuple = ()                                  # ForecastRow, this year then next year
    currency: str = ""                                # of the accounts (the forecasts' currency)
    company: str = ""                                 # plain name for the sentences
    ratings: Optional[RatingsView] = None

    @property
    def available(self) -> bool:
        return bool(self.mark)

    def cost_line(self) -> str:
        if self.units_charged:
            return f"{self.units_charged} EODHD units charged"
        return "0 EODHD units (cached for today)" if self.cached else "0 EODHD units (no request made)"

    def tag(self) -> str:
        stamp = f", as of {self.as_of}" if self.as_of else ""
        return f"source: {self.source}{stamp}"

    @property
    def headline(self) -> str:
        """One sentence: what the analysts are doing, or WHY we cannot say."""
        if self.mark:
            return HEADLINES[self.mark]
        return self.direction.note or self.direction.label or "no analyst estimate is available"

    def currency_note(self) -> str:
        """Said once when the accounts' currency is unknown - never a guess."""
        return "" if self.currency else f"({CURRENCY_NOT_STATED})"

    def forecast_sentences(self) -> list[str]:
        """This year's and next year's forecast, each a plain sentence. When this year cannot be
        read (too few analysts, no figure from three months ago) the reason stands in its place and
        next year is still said."""
        out: list[str] = []
        whens = ("this year", "next year")
        for i, row in enumerate(self.rows[:2]):
            if i == 0 and not self.mark:
                out.append(f"Analyst forecasts for this year are not shown: {self.direction.note}.")
                continue
            out.append(row.sentence(self.company, whens[0 if row.label.lower().startswith("this")
                                                        else 1]))
        if not self.rows:
            out.append(f"Analyst forecasts are not shown: {self.direction.note or 'no estimate'}.")
        if self.rows and self.currency_note():
            out.append(self.currency_note())
        return out

    def lines(self) -> list[str]:
        """Everything the text export prints: the ratings, then the forecasts."""
        out = list(self.ratings.lines()) if self.ratings is not None else []
        out += self.forecast_sentences()
        return out


def analyst_trend(data, currency: Optional[str] = None, *, company: str = "",
                  price: Optional[float] = None, price_currency: str = "",
                  own_listing: bool = True) -> AnalystTrend:
    """WHAT ANALYSTS SAY from a ``data.analyst_trend.TrendData``.

    ``currency`` is the currency of the ACCOUNTS (``Fundamentals.financial_currency``): the estimates
    are per-share profits in it, and with no currency the figures print bare and say so - never a
    guess. ``price`` / ``price_currency`` are the latest close and its quote code for the listing the
    RATINGS describe, so the target is compared with the price it can honestly be compared with.

    The forecast mark abstains, with the reason shown, when: there is no current-year estimate (no
    block, or a stale one - expect gaps outside the US); fewer than ``ANALYST_MIN_ANALYSTS`` analysts,
    or an unstated count; the current or the 90-days-ago estimate is missing; or the 90-days-ago
    figure is within ``ANALYST_MIN_BASE`` of zero, where a percentage change means nothing. A missing
    input is never treated as a zero. The next year is shown whenever it exists."""
    ccy = (currency or "").strip()
    ratings = getattr(data, "ratings", None)
    base = dict(source=getattr(data, "source", ""), as_of=getattr(data, "as_of", ""),
                units_charged=getattr(data, "units_charged", 0),
                cached=getattr(data, "cached", False), currency=ccy,
                company=plain_company_name(company),
                ratings=ratings_view(
                    ratings, getattr(data, "ratings_note", ""), price=price,
                    price_currency=price_currency, own_listing=own_listing))
    # What the request cost is for the LOG, not the page.
    _log.info("analyst trend: %s", AnalystTrend(**{k: v for k, v in base.items()
                                                    if k in ("source", "as_of", "units_charged",
                                                             "cached")}).cost_line())

    current = getattr(data, "current", None)
    following = getattr(data, "next_year", None)

    def row(label: str, period) -> ForecastRow:
        return ForecastRow(label=_period_label(label, period.period_end), now=period.now,
                           ago_90d=period.ago_90d, analysts=period.analysts, currency=ccy)

    rows: list[ForecastRow] = []
    if current is not None and current.now is not None:
        rows.append(row("This year", current))
    if following is not None and following.now is not None:
        rows.append(row("Next year", following))
    table = tuple(rows)

    def abstain(reason: str) -> AnalystTrend:
        return AnalystTrend(direction=_abstain(reason), rows=table, **base)

    if current is None:
        return abstain(getattr(data, "note", "") or "no analyst estimate is available")
    if current.analysts is None:
        return abstain("the number of analysts is not stated, so there is no consensus to read")
    if current.analysts < ANALYST_MIN_ANALYSTS:
        return abstain(f"only {plural(current.analysts, 'analyst')} {verb(current.analysts, 'covers', 'cover')} the current year; a consensus "
                       f"needs at least {ANALYST_MIN_ANALYSTS}")
    if current.now is None:
        return abstain("no current consensus EPS estimate")
    if current.ago_90d is None:
        return abstain("no 90-days-ago estimate to compare with")
    if abs(current.ago_90d) < ANALYST_MIN_BASE:
        return abstain(f"the estimate 90 days ago was {_eps(current.ago_90d)}, within one cent of "
                       f"zero, so a percentage change means nothing")

    mark, change = _direction(current.now, current.ago_90d)
    return AnalystTrend(
        mark=mark, direction=Reading(value=change, unit="fraction", label=HEADLINES[mark]),
        rows=table, **base)


# --------------------------------------------------------------------------- #
# COMPANY-FACTS-TABLE-1 (Batch 15 item 1) — price and cash facts the council's own
# evidence pack already fetches (EL.PA, 2026-10-03: last close, 50/200-day averages,
# distance from the 52-week high, 6/12-month return, volatility, the FCF series, trailing
# EPS/PE), that Company Check's OWN page never showed — a reader had to tick the council
# (six model calls, minutes) to see numbers the ranker's own deterministic tools already
# held. This function is PURE, like every other reading in this file: the price leg is
# read off an ALREADY-fetched TechnicalSnapshot (``factors.gather_factor_inputs`` computes
# one from the SAME 400-day bars the ranker fetches for momentum/volatility — nothing new
# is fetched for it), and news/analyst data are passed in already-fetched, exactly like
# ``analyst_trend`` above takes an already-fetched ``TrendData``. FORWARD P/E is new
# arithmetic over two numbers already on the page (today's close, the analyst consensus
# EPS already shown in "What analysts say") — not a new fetch.
# --------------------------------------------------------------------------- #
def _pct(value: Optional[float]) -> str:
    return f"{value:+.1%}" if value is not None else ""


@dataclass(frozen=True)
class AccountsFx:
    """FX-PRICECASH-1 — how an ACCOUNTS-currency figure (EPS, consensus EPS) is put into the
    PRICE (trading) currency before it is compared with the price. ``rate`` is price-currency
    units per 1 accounts-currency unit. ``source`` names the feed ("" for a pure unit change
    such as pounds to pence, which needs no feed)."""

    from_ccy: str
    to_ccy: str
    rate: float
    as_of: str = ""
    source: str = ""

    def month(self) -> str:
        """"Oct 2026" from the rate's "2026-10" (the raw text when it is not that shape)."""
        try:
            from datetime import datetime
            return datetime.strptime(self.as_of[:7], "%Y-%m").strftime("%b %Y")
        except ValueError:
            return self.as_of

    def tag(self) -> str:
        """B22-B2: "at 1 CNY = 1.1703 HKD, Oct 2026" - read straight, no arrow and no @."""
        when = f", {self.month()}" if self.as_of else ""
        return f"at 1 {self.from_ccy} = {self.rate:.4f} {self.to_ccy}{when}"


def currency_relation(price_ccy: str, acct_ccy: str):
    """``("same", None)`` when no conversion is needed, ``("unit", AccountsFx)`` when the two
    codes are the same money in different units (pounds / pence: exact, no feed), else
    ``("mixed", None)`` — two different currencies, which need an exchange rate. Either code
    missing is "same": nothing is known to mix (never manufacture a conversion from a gap)."""
    p, a = (price_ccy or "").strip(), (acct_ccy or "").strip()
    if not p or not a or p == a:
        return "same", None
    p_major, a_major = (_minor(p) or p), (_minor(a) or a)
    if p_major.upper() == a_major.upper():
        rate = 100.0 if (_minor(p) and not _minor(a)) else 0.01 if (_minor(a) and not _minor(p)) else 1.0
        return "unit", AccountsFx(from_ccy=a, to_ccy=p, rate=rate)
    return "mixed", None


def latest_accounts_fx(adapter, from_ccy: str, to_ccy: str, today) -> Optional[AccountsFx]:
    """The most recent month-end ``from_ccy -> to_ccy`` rate, through the SAME monthly-FX path the
    valuation band uses (``tools.fx``: direct pair, else the reverse inverted). None when no rate
    could be had — the caller then abstains rather than guess."""
    from datetime import timedelta
    from datetime import date as _date
    from .tools.fx import monthly_fx_series
    try:
        fx = monthly_fx_series(adapter, from_ccy, to_ccy, start=today - timedelta(days=100),
                               end=today)
    except Exception:                                  # noqa: BLE001 - abstain, don't crash
        return None
    if not fx.available:
        return None
    key = max(fx.rates)
    day = _date(key[0], key[1], 1)
    pair = fx.reverse_pair if fx.source_for(day) == "inverted" else fx.direct_pair
    return AccountsFx(from_ccy=from_ccy, to_ccy=to_ccy, rate=fx.rates[key],
                      as_of=f"{key[0]}-{key[1]:02d}",
                      source=f"yfinance {pair}" + (" inverted" if fx.source_for(day) == "inverted"
                                                   else ""))


@dataclass(frozen=True)
class PriceAndCash:
    last_close: Reading = field(default_factory=Reading)
    sma_50: Reading = field(default_factory=Reading)
    sma_200: Reading = field(default_factory=Reading)
    pct_off_high: Reading = field(default_factory=Reading)
    return_6m: Reading = field(default_factory=Reading)
    return_12m: Reading = field(default_factory=Reading)
    volatility: Reading = field(default_factory=Reading)
    fcf_series: Reading = field(default_factory=Reading)
    trailing_eps: Reading = field(default_factory=Reading)
    trailing_pe: Reading = field(default_factory=Reading)
    forward_pe_this_year: Reading = field(default_factory=Reading)
    forward_pe_next_year: Reading = field(default_factory=Reading)
    news: tuple = ()           # tuple[NewsItem, ...], newest first
    news_note: str = ""        # why ``news`` is empty, when it is ("" if genuinely quiet)
    news_source: str = ""      # which source answered ("EODHD news" / "yfinance news")
    fx: Optional["AccountsFx"] = None   # the accounts->price conversion used, when one was

    def lines(self) -> list[str]:
        readings = [self.last_close, self.sma_50, self.sma_200, self.pct_off_high,
                   self.return_6m, self.return_12m, self.volatility, self.fcf_series,
                   self.trailing_eps, self.trailing_pe, self.forward_pe_this_year,
                   self.forward_pe_next_year]
        out = dedupe_lines([r.text() for r in readings
                            if r.available or r.note or r.failure or r.not_meaningful])
        if self.news:
            out.append(f"Recent news ({self.news_source}):")
            out.extend(f"  - {item.published.isoformat()}: {item.headline}"
                      for item in self.news)
        elif self.news_note == "no headlines about this company in the window":
            out.append(f"Recent news: {self.news_note}")      # NEWS-SUBJECT-1: a finding, not a fault
        elif self.news_note:
            out.append(f"Recent news: not shown — {self.news_note}")
        return out


def _ttm_basis(f) -> str:
    """PE-BASIS-2: the earnings window a trailing P/E rests on, so the two P/Es on one page (the band's,
    on the latest annual accounts; this one, on the last twelve months) explain themselves."""
    end = str(getattr(f, "trailing_period_end", "") or "") if f is not None else ""
    try:
        from datetime import datetime
        when = datetime.strptime(end[:10], "%Y-%m-%d").strftime("%b %Y")
    except ValueError:
        return ", twelve months to the latest reported quarter"
    return f", twelve months to {when}"


def price_and_cash(technical, f, trend=None, news=None, *, max_news: int = 5,
                   fx: Optional[AccountsFx] = None) -> PriceAndCash:
    """``technical`` is a ``tools.technical.TechnicalSnapshot`` (already computed from the
    400-day bars the ranker fetched — ``None`` when there were no price bars at all).
    ``f`` is ``Fundamentals``. ``trend`` is an already-built ``AnalystTrend`` (or None —
    forward P/E then abstains, never guesses an estimate). ``news`` is an already-fetched
    ``data.news_fallback.NewsFetchResult`` (or None — the news line abstains)."""
    currency = str(getattr(f, "currency", "") or "").strip() if f is not None else ""
    # FX-PRICECASH-1 — the ACCOUNTS (EPS, free cash flow, consensus EPS) and the PRICE can be in
    # different currencies (BYD: accounts CNY, share price HKD). Each figure is labelled with its
    # own currency, and every price / EPS ratio is formed only AFTER the EPS is put in the price
    # currency; with no rate it abstains rather than divide HKD by CNY.
    acct_ccy = (str(getattr(f, "financial_currency", "") or "").strip() if f is not None else "")
    relation, unit_fx = currency_relation(currency, acct_ccy)
    mixed = relation == "mixed"
    if relation == "unit":
        fx = unit_fx
    elif relation == "same":
        fx = None
    acct_label = acct_ccy or currency            # unknown accounts currency: as before

    def money(v):
        # B22-U2: a price or per-share figure always shows two decimals ("HKD 75.00", not "HKD 75" when
        # the close happens to be a whole number); only large amounts abbreviate.
        if v is None:
            return None
        if currency and abs(v) < 1e6:
            from .tools.price_context import format_money
            return format_money(v, currency)
        return _money(v, currency)

    def acct_money(v):
        return _money(v, acct_label) if v is not None else None

    def to_price(v):
        """An accounts-currency amount in the price currency, or None when it cannot be."""
        if relation == "same":
            return v
        return v * fx.rate if fx is not None and v is not None else None

    def no_rate() -> str:
        return (f"accounts are in {acct_ccy} but the price is in {currency}, and no exchange "
                f"rate was available to put them in one currency")

    if technical is None:
        last_close = _abstain("no price history")
        sma_50 = _abstain("no price history")
        sma_200 = _abstain("no price history")
        pct_off_high = _abstain("no price history")
        return_6m = _abstain("no price history")
        return_12m = _abstain("no price history")
        volatility = _abstain("no price history")
    else:
        last_close = (Reading(value=technical.last_close, unit=currency,
                              label=f"last close {money(technical.last_close)}")
                     if technical.last_close is not None
                     else _abstain("no price history"))
        # CI-FLOAT-1: a moving average, rounded explicitly with ROUND_HALF_UP on its
        # exact value — not left to the formatter's own (binary, round-half-to-even)
        # default. Paired with ``sma``'s own ``math.fsum`` fix (tools/technical.py), so
        # neither the value nor its rounding depends on which Python computed it.
        # ``Reading.value`` stays the raw figure; only the rendered LABEL is rounded.
        sma_50 = (Reading(value=technical.sma_50, unit=currency,
                          label=f"50-day average price "
                                f"{money(round_half_up(technical.sma_50))}")
                 if technical.sma_50 is not None
                 else _abstain("sma_50 unavailable: fewer than 50 closes"))
        sma_200 = (Reading(value=technical.sma_200, unit=currency,
                           label=f"200-day average price "
                                 f"{money(round_half_up(technical.sma_200))}")
                  if technical.sma_200 is not None
                  else _abstain("sma_200 unavailable: fewer than 200 closes"))
        pct_off_high = (Reading(value=technical.pct_off_52w_high, unit="fraction",
                                label=f"{abs(technical.pct_off_52w_high):.1%} "
                                      f"{'below' if technical.pct_off_52w_high < 0 else 'above'} "
                                      "its 52-week high")
                       if technical.pct_off_52w_high is not None
                       else _abstain("52-week high unavailable: insufficient price history"))
        return_6m = (Reading(value=technical.return_6m, unit="fraction",
                             label=f"6-month return {_pct(technical.return_6m)}")
                    if technical.return_6m is not None
                    else _abstain("return_6m unavailable: insufficient price history"))
        return_12m = (Reading(value=technical.return_12m, unit="fraction",
                              label=f"12-month return {_pct(technical.return_12m)}")
                     if technical.return_12m is not None
                     else _abstain("return_12m unavailable: insufficient price history"))
        # CI-FLOAT-1: same pairing as the moving averages above — annualized_volatility
        # is itself a mean-of-squares (tools/technical.py, also fsum'd now), and its
        # displayed percentage is rounded explicitly rather than left to ``:.1%}``'s own
        # binary round-half-to-even.
        volatility = (Reading(
            value=technical.annualized_volatility, unit="fraction",
            label=f"annualised volatility "
                  f"{round_half_up(technical.annualized_volatility * 100, 1):.1f}%")
                     if technical.annualized_volatility is not None
                     else _abstain("volatility unavailable: insufficient price history"))

    # Free cash flow by year, oldest first — the SAME packer the council's own evidence
    # block uses (series_pack.pack_series), so this can never read a different order or a
    # different set of years than the figures a narration already cited.
    from .series_pack import ORDER_OLDEST_FIRST, packed_ok, pack_series

    if f is None:
        fcf_series = _abstain("no fundamentals")
    elif is_financial_sector(f):
        fcf_series = _not_meaningful(FREE_CASH_FLOW_NOT_MEANINGFUL)          # BANK-PAGE-1: operating cash flow of a bank is noise
    else:
        packed = pack_series(f, "free_cash_flow_annual", label="Free cash flow",
                             order=ORDER_OLDEST_FIRST)
        if packed_ok(packed):
            pairs = " ".join(f"{y} {acct_money(v)}"
                             for y, v in zip(packed["years"], packed["values"]))
            fcf_series = Reading(value=packed["values"][-1], unit=acct_label,
                                 label=f"free cash flow, oldest first: {pairs}")
        else:
            fcf_series = _abstain(packed.get("note") or "free cash flow series unavailable")

    last_close_v = technical.last_close if technical is not None else None
    if f is not None and f.eps is not None:
        said = (f" ({money(to_price(f.eps))}, {fx.tag()})"
                if relation == "mixed" and fx is not None else "")
        trailing_eps = Reading(value=f.eps, unit=acct_label,
                               label=f"trailing EPS {acct_money(f.eps)}{said}")
    else:
        trailing_eps = _abstain("trailing EPS not reported")
    ttm = _ttm_basis(f)
    if relation == "same":
        trailing_pe = (Reading(value=f.pe_ratio, unit="x", label=f"trailing P/E {f.pe_ratio:.1f}{ttm}")
                       if f is not None and f.pe_ratio is not None
                       else _abstain("trailing P/E not reported"))
    elif f is None or f.eps is None:
        trailing_pe = _abstain("trailing P/E not reported")
    elif fx is None:
        trailing_pe = _abstain(f"trailing P/E not computed: {no_rate()}")
    elif last_close_v is None:
        trailing_pe = _abstain("no current price")
    elif f.eps <= 0:
        trailing_pe = _abstain("trailing EPS is not positive; a P/E is undefined")
    else:
        eps_p = f.eps * fx.rate
        pe = last_close_v / eps_p
        # the vendor's own trailing P/E is NOT used here: it divides the price by the EPS in
        # whatever currency each was reported in (BYD: HKD price / CNY EPS)
        trailing_pe = Reading(
            value=pe, unit="x",
            label=(f"trailing P/E {pe:.1f}{ttm} ({money(last_close_v)} / {money(eps_p)} EPS, "
                   f"{acct_money(f.eps)} converted {fx.tag()})" if relation == "mixed"
                   else f"trailing P/E {pe:.1f}{ttm}"))

    # Forward P/E = today's close / analyst consensus EPS — arithmetic over two numbers
    # already shown elsewhere on the page (the price above; the consensus estimate in
    # "What analysts say"). Abstains rather than guesses when either side is missing, or
    # when the estimate is non-positive (a negative-earnings forward multiple is not a
    # number a reader can use the way a P/E is used).

    def forward_pe(row, when: str) -> Reading:
        if last_close_v is None:
            return _abstain("no current price")
        if row is None or row.now is None:
            return _abstain(f"no analyst consensus EPS for {when}")
        if row.now <= 0:
            return _abstain(f"{when}'s consensus EPS is not positive; a forward P/E is undefined")
        est = row.now
        converted_from = ""
        if relation != "same":
            # the consensus is per-share profit in ITS currency (the accounts' by the feed's
            # convention): put it in the price currency first, or abstain
            cc = (getattr(row, "currency", "") or "").strip()
            if cc and cc == currency:
                pass
            elif cc and cc == acct_ccy and fx is not None:
                est = row.now * fx.rate
                converted_from = (f", {_money(row.now, cc)} converted {fx.tag()}"
                                  if relation == "mixed" else "")
            elif cc and cc == acct_ccy:
                return _abstain(f"forward P/E ({when}) not computed: {no_rate()}")
            else:
                return _abstain(f"forward P/E ({when}) not computed: the consensus EPS "
                                f"currency ({cc or 'not stated'}) cannot be matched to the "
                                f"price currency ({currency})")
        return Reading(value=last_close_v / est, unit="x",
                      label=f"forward P/E ({when}) {last_close_v / est:.1f}x "
                            f"({money(last_close_v)} / {money(est)} consensus EPS"
                            f"{converted_from})")

    rows = tuple(getattr(trend, "rows", ()) or ())
    fwd_this = forward_pe(rows[0] if len(rows) > 0 else None, "this year")
    fwd_next = forward_pe(rows[1] if len(rows) > 1 else None, "next year")

    news_items: tuple = ()
    news_note = ""
    news_source = ""
    if news is not None:
        items = list(getattr(news, "items", ()) or ())
        if items:
            # NEWS-SUBJECT-1: a feed matches on any mention (JPM's five headlines were all
            # about other companies naming J.P. Morgan as analyst or arranger). Keep a
            # headline only when this company is its subject; under two, say so.
            from .news_subject import subject_news
            items, news_note = subject_news(
                items, name=(getattr(f, "name", "") or ""), ticker=(getattr(f, "ticker", "") or ""))
        if items:
            items.sort(key=lambda it: it.published, reverse=True)
            news_items = tuple(items[:max_news])
            news_source = getattr(news, "source", "") or ""
        elif news_note:
            pass                                     # the subject filter's own sentence
        else:
            tried = "; ".join(getattr(news, "tried", ()) or ())
            news_note = tried or "no recent news found"
    else:
        news_note = "news was not requested for this run"

    return PriceAndCash(
        last_close=last_close, sma_50=sma_50, sma_200=sma_200, pct_off_high=pct_off_high,
        return_6m=return_6m, return_12m=return_12m, volatility=volatility,
        fcf_series=fcf_series, trailing_eps=trailing_eps, trailing_pe=trailing_pe,
        forward_pe_this_year=fwd_this, forward_pe_next_year=fwd_next,
        news=news_items, news_note=news_note, news_source=news_source,
        fx=(fx if relation == "mixed" else None))


# --------------------------------------------------------------------------- #
# CAGR-CRASH-1 — no single absolute reading may take the page down
# --------------------------------------------------------------------------- #
# Each section builder is called through ``guard``; if it raises, the section is replaced by a
# placeholder of the SAME type whose text reads "not available: <reason>", so every consumer
# (page, export, report) renders it with no special case and the other sections are untouched.
def _unavailable(kind: str, exc: BaseException):
    bad = _failed(exc)
    if kind == "debt_and_cash":
        return DebtAndCash(net_debt=bad)
    if kind == "growth_record":
        leg = GrowthLeg(cagr={w: bad for w in GROWTH_WINDOWS}, grew_in=bad)
        return GrowthRecord(revenue=leg, eps=leg)
    if kind == "analyst_trend":
        return AnalystTrend(direction=Reading(value=None, note=f"not available: {bad.failure}"))
    if kind == "price_and_cash":
        return PriceAndCash(last_close=bad)
    raise ValueError(kind)


def accounts_context(f) -> dict:
    """FORENSIC-PACK-1 - which accounts every absolute reading rests on, and their currency.

    The council's own evidence carried ``total_debt`` and ``free_cash_flow`` bare, so the narrator
    wrote "(no currency stated in the evidence field)" beside a dollar figure and could not say how
    old the accounts were. Both facts are in the data we already hold; this states them ONCE.
    The date is the period END of the newest annual statement the provider dated (never guessed);
    no adapter carries a filing date, so none is printed - the basis says so rather than inventing
    one. Empty dict when there are no fundamentals."""
    if f is None:
        return {}
    from .operating_profit import operating_profit_basis

    basis = operating_profit_basis(f)
    if basis.startswith("fiscal year to "):
        text = f"annual accounts for the {basis} (the filing date is not in the data)"
    else:
        text = "the latest annual accounts (their period end date is not in the data)"
    return {"currency": str(getattr(f, "financial_currency", "") or "").strip(),
            "listing_currency": str(getattr(f, "currency", "") or "").strip(), "basis": text}


def guard(kind: str, fn, *args, **kwargs):
    """Call a section builder; on any exception return its "not available" placeholder."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:                       # noqa: BLE001
        return _unavailable(kind, exc)
