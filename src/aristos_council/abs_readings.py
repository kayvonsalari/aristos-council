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

from dataclasses import dataclass, field
from typing import Optional, Sequence

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

    @property
    def available(self) -> bool:
        return self.value is not None

    def text(self) -> str:
        return self.label if self.available else f"not stated — {self.note}"


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


def _abstain(note: str) -> Reading:
    return Reading(value=None, note=note)


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

    def lines(self) -> list[str]:
        # ABS-READINGS-3 - NVIDIA printed "has no net debt to repay" twice, once for the
        # operating-cash-flow reading and once for the free-cash-flow one.
        return dedupe_lines([r.text() for r in (self.net_debt, self.net_debt_to_ocf,
                                                self.interest_cover,
                                                self.years_to_repay)])


def _money(value: float, currency: str) -> str:
    unit, scaled = "", value
    for cut, suffix in ((1e12, "tn"), (1e9, "bn"), (1e6, "m")):
        if abs(value) >= cut:
            unit, scaled = suffix, value / cut
            break
    body = f"{scaled:,.1f}{unit}" if unit else f"{scaled:,.0f}"
    return f"{body} {currency}".strip()


def debt_and_cash(f) -> DebtAndCash:
    """What the balance sheet owes and how long the cash flow would take to clear it."""
    if f is None:
        return DebtAndCash(net_debt=_abstain("no fundamentals"),
                           net_debt_to_ocf=_abstain("no fundamentals"),
                           interest_cover=_abstain("no fundamentals"),
                           years_to_repay=_abstain("no fundamentals"))

    currency = str(getattr(f, "currency", "") or "")
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

    return DebtAndCash(net_debt=net_reading, net_debt_to_ocf=ratio,
                       interest_cover=cover, years_to_repay=repay, currency=currency)


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
            head = reading.label.split(" (only ")[0]
            if span in windows:
                out.append(head)                     # a window was genuinely filled
            else:
                out.append(f"{head} — only {span} years of accounts are "
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
    source_tag: str = ""

    def lines(self) -> list[str]:
        out = dedupe_lines(self.revenue.lines() + self.eps.lines())
        return out + ([self.source_tag] if self.source_tag else [])


def _cagr(series: Sequence[Optional[float]], window: int, label: str) -> Reading:
    """Compound annual rate over at most ``window`` years, stating the span it used.

    ``series`` is NEWEST-FIRST. Holes are dropped, which is why the span is reported: a
    company with 2018 and 2024 but nothing between has six years of span on two points,
    and saying "over 6 years" is honest where "10-year CAGR" would not be.
    """
    present = [v for v in series if v is not None]
    if len(present) < MIN_GROWTH_YEARS:
        return _abstain(f"only {len(present)} year(s) of {label} reported; a compound "
                        f"rate needs at least {MIN_GROWTH_YEARS}")
    end = present[0]
    span = min(window, len(present) - 1)
    start = present[span]
    if start is None or start <= 0:
        # A root of a negative ratio is not a number, and a rate off a negative base is
        # not a growth rate. This is the PEG/CAGR discipline the criteria already use.
        return _abstain(f"{label} was not positive {span} years ago, so a compound rate "
                        f"is not defined")
    rate = (end / start) ** (1.0 / span) - 1.0
    used = "" if span == window else f" (only {span} of {window} years available)"
    return Reading(value=rate, unit="/yr", span=span,
                   label=f"{label} compounded {rate:+.1%} a year over {span} years{used}")


def _grew_in(series: Sequence[Optional[float]], label: str, window: int = 10) -> Reading:
    """"grew in 7 of the 10 years reported" — counted on consecutive reported pairs."""
    present = [v for v in series if v is not None][:window + 1]
    if len(present) < 2:
        return _abstain(f"not enough {label} history to count growth years")
    pairs = list(zip(present, present[1:]))          # (newer, older)
    rose = sum(1 for newer, older in pairs if newer > older)
    return Reading(value=float(rose), unit="years",
                   label=f"{label} grew in {rose} of the {len(pairs)} years reported")


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
    eps_label = "earnings per share" + ("" if eps_source in ("reported", "unavailable")
                                        else f" ({eps_source})")

    return GrowthRecord(
        source_tag=tag,
        revenue=GrowthLeg(
            name="revenue",
            cagr={w: _cagr(revenue, w, "revenue") for w in GROWTH_WINDOWS},
            grew_in=_grew_in(revenue, "revenue"),
            years_available=len([v for v in revenue if v is not None])),
        eps=GrowthLeg(
            name="eps",
            cagr={w: _cagr(eps, w, eps_label) for w in GROWTH_WINDOWS},
            grew_in=_grew_in(eps, eps_label),
            years_available=len([v for v in eps if v is not None])))
