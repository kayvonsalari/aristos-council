"""The price test and the return range (ABSOLUTE-VALUE-1, docs/DESIGN_absolute.md sections d to g).

``build_absolute(...) -> AbsoluteValue``. Orchestration only: every number comes from
``tools.absolute_math`` (the equation and its two solvers), ``tools.gate_readings`` (the readings the
gate already made), ``abs_readings.growth_record`` (the existing growth record) and
``data.bond_yield`` (the hurdle's risk-free leg). No model, no vote.

Null is not-evaluated, never a failure (house rule 3): every missing input names itself and stops
only what it must. The result states are: excluded / not covered (both from the gate), not enough
data, qualifies below hurdle, qualifies clears hurdle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Optional

from ..gate import engine as gate_engine
from ..tools import absolute_math as am
from ..tools import gate_readings as gr
from ..tools.price_context import format_money
from ..tools.screening import period_matched
from .definition import ValueDefinition, ValueDefinitionError, load_definition

EXCLUDED, NOT_COVERED = gate_engine.EXCLUDED, gate_engine.NOT_COVERED
NOT_ENOUGH_DATA = gate_engine.NOT_ENOUGH_DATA
BELOW_HURDLE, CLEARS_HURDLE = "qualifies_below_hurdle", "qualifies_clears_hurdle"

STATE_WORDS = {EXCLUDED: "Excluded", NOT_COVERED: "Not covered", NOT_ENOUGH_DATA: "Not enough data",
               BELOW_HURDLE: "Qualifies, below hurdle", CLEARS_HURDLE: "Qualifies, clears hurdle"}

OWN_5, OWN_10, CONSENSUS, DOWNSIDE = "own_5_year", "own_10_year", "consensus", "downside"
CASE_LABELS = {OWN_5: "Own 5-year earnings growth", OWN_10: "Own 10-year earnings growth",
               CONSENSUS: "Analyst consensus (not testable)", DOWNSIDE: "Downside"}
NO_FCF = "not valued: no positive free cash flow"
INVESTMENT_WARNING = "investment well above its own history"
HIGH_GROWTH_NOTE = "few companies have grown faster than 18% a year for ten years"


# --------------------------------------------------------------------------- #
# the result types
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class YearFcf:
    label: str
    fcf_per_share: Optional[float] = None
    note: str = ""

    def as_dict(self) -> dict:
        return {"label": self.label, "fcf_per_share": self.fcf_per_share, "note": self.note}


@dataclass(frozen=True)
class BaseReading:
    """Free cash flow per share: the three years, the two candidates and the chosen base."""

    years: tuple = ()                       # YearFcf, newest first
    latest: Optional[float] = None
    average: Optional[float] = None
    base: Optional[float] = None
    chosen: str = ""                        # "the latest year" / "the three-year average"
    share_basis: str = ""                   # "diluted shares" / "basic shares"
    currency: str = ""
    note: str = ""                          # why there is no base, or a caution
    not_valued: bool = False                # base at or below zero

    @property
    def usable(self) -> bool:
        return self.base is not None and self.base > 0

    def as_dict(self) -> dict:
        return {"years": [y.as_dict() for y in self.years], "latest": self.latest, "average": self.average,
                "base": self.base, "chosen": self.chosen, "share_basis": self.share_basis,
                "currency": self.currency, "note": self.note, "not_valued": self.not_valued}


@dataclass(frozen=True)
class PriceReading:
    """The price per share in the REPORTING currency, with the conversion shown when there is one."""

    price: Optional[float] = None           # in the reporting currency
    currency: str = ""                      # the reporting currency
    quote_price: Optional[float] = None
    quote_currency: str = ""
    rate: Optional[float] = None            # quote-currency units per 1 reporting-currency unit
    rate_line: str = ""                     # "at 1 CNY = 1.1703 HKD, Oct 2026"
    note: str = ""

    @property
    def available(self) -> bool:
        return self.price is not None and self.price > 0

    def as_dict(self) -> dict:
        return {"price": self.price, "currency": self.currency, "quote_price": self.quote_price,
                "quote_currency": self.quote_currency, "rate": self.rate, "rate_line": self.rate_line,
                "note": self.note}


@dataclass(frozen=True)
class HurdleReading:
    currency: str = ""
    bond_name: str = ""
    bond_symbol: str = ""
    bond_date: str = ""
    bond_yield: Optional[float] = None
    margin: float = 0.05
    hurdle: Optional[float] = None
    note: str = ""                          # why there is no hurdle, or a peg note

    @property
    def available(self) -> bool:
        return self.hurdle is not None

    def as_dict(self) -> dict:
        return {"currency": self.currency, "bond_name": self.bond_name, "bond_symbol": self.bond_symbol,
                "bond_date": self.bond_date, "bond_yield": self.bond_yield, "margin": self.margin,
                "hurdle": self.hurdle, "note": self.note}


@dataclass(frozen=True)
class CaseReading:
    key: str
    label: str
    growth: Optional[float] = None          # the (first-years) growth rate; None when unavailable
    growth_text: str = ""
    ret: Optional[float] = None
    ret_bound: str = ""                     # "above 60%" / "below 0.01%" when the solver hit its range
    note: str = ""                          # how the growth rate was built (a detail, not a reason)
    reason: str = ""                        # why the return is unavailable
    not_testable: bool = False
    high_growth: bool = False
    path: tuple = ()                        # the ten yearly rates behind the return

    @property
    def available(self) -> bool:
        return self.ret is not None

    def clears(self, hurdle: float) -> bool:
        return self.ret is not None and self.ret >= hurdle

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "growth": self.growth, "growth_text": self.growth_text,
                "return": self.ret, "return_bound": self.ret_bound, "note": self.note, "reason": self.reason,
                "not_testable": self.not_testable, "high_growth": self.high_growth,
                "path": list(self.path)}


@dataclass(frozen=True)
class Verdict:
    state: str
    reason: str = ""
    clearing: int = 0
    of: int = 0
    low: Optional[float] = None
    high: Optional[float] = None

    @property
    def word(self) -> str:
        return STATE_WORDS.get(self.state, self.state)

    def count_text(self) -> str:
        return f"{self.clearing} of {self.of} cases clear" if self.of else ""

    def as_dict(self) -> dict:
        return {"state": self.state, "reason": self.reason, "clearing": self.clearing, "of": self.of,
                "low": self.low, "high": self.high}


@dataclass(frozen=True)
class AbsoluteValue:
    definition_id: str = ""
    gate_state: str = ""
    base: BaseReading = field(default_factory=BaseReading)
    price: PriceReading = field(default_factory=PriceReading)
    hurdle: HurdleReading = field(default_factory=HurdleReading)
    yield_on_base: Optional[float] = None
    implied_growth: Optional[float] = None
    implied_note: str = ""
    cases: tuple = ()                       # the four CaseReading
    backtest_cases: tuple = ()              # own 5, own 10, downside with own-5 fallback
    verdict: Verdict = field(default_factory=lambda: Verdict(NOT_ENOUGH_DATA))
    backtest_verdict: Optional[Verdict] = None
    context: dict = field(default_factory=dict)
    debt_line: str = ""
    shown: bool = True                      # False for a bank or insurer: no figures, just the line

    def as_dict(self) -> dict:
        return {"definition": self.definition_id, "gate_state": self.gate_state, "shown": self.shown,
                "base": self.base.as_dict(), "price": self.price.as_dict(), "hurdle": self.hurdle.as_dict(),
                "yield_on_base": self.yield_on_base, "implied_growth": self.implied_growth,
                "implied_note": self.implied_note, "cases": [c.as_dict() for c in self.cases],
                "backtest_cases": [c.as_dict() for c in self.backtest_cases],
                "verdict": self.verdict.as_dict(),
                "backtest_verdict": (self.backtest_verdict.as_dict() if self.backtest_verdict else None),
                "context": self.context, "debt_line": self.debt_line}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _num(x) -> Optional[float]:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and x == x else None


def _month_label(iso: str) -> str:
    try:
        return datetime.strptime(str(iso)[:10], "%Y-%m-%d").strftime("%b %Y")
    except ValueError:
        return ""


def _period_ends(f) -> list[str]:
    ends = (getattr(f, "aligned_period_ends", None) or {}).get("operating_cash_flow")
    if ends:
        return list(ends)
    return list((getattr(f, "period_ends", None) or {}).get("operating_cash_flow_annual") or [])


def _year_labels(f, n: int) -> list[str]:
    ends = _period_ends(f)
    fallback = ["the latest year", "the year before", "two years before"]
    return [(f"the year to {_month_label(ends[i])}" if i < len(ends) and _month_label(ends[i]) else fallback[i])
            for i in range(n)]


# --------------------------------------------------------------------------- #
# the base
# --------------------------------------------------------------------------- #
def free_cash_flow_base(f, definition: ValueDefinition) -> BaseReading:
    """Free cash flow per share for up to ``average_years`` fiscal years, and the lower of the latest
    year and their average. Each year's three lines are read at the SAME fiscal year end
    (``period_matched``); a year missing any line is a hole, never a guess. Diluted weighted-average
    shares when the provider carries them, else the basic count, said so."""
    ccy = (getattr(f, "financial_currency", None) or getattr(f, "currency", None) or "").strip()
    if f is None:
        return BaseReading(currency=ccy, note="no accounts")
    if getattr(f, "statements_currency_note", ""):
        return BaseReading(currency=ccy, note=f"statements not in one currency ({f.statements_currency_note})")
    n = definition.average_years
    diluted = period_matched(f, ("operating_cash_flow", "capital_expenditure", "diluted_shares"), periods=n)
    basic = period_matched(f, ("operating_cash_flow", "capital_expenditure", "shares_outstanding"), periods=n)
    use_diluted = diluted[0].get("diluted_shares") is not None
    rows, share_key, basis = ((diluted, "diluted_shares", "diluted shares") if use_diluted
                              else (basic, "shares_outstanding", "basic shares"))
    labels = _year_labels(f, n)
    years, values = [], []
    for i, row in enumerate(rows):
        ocf, capex, sh = row.get("operating_cash_flow"), row.get("capital_expenditure"), row.get(share_key)
        v = am.free_cash_flow_per_share(ocf, capex, sh)
        why = ""
        if v is None:
            missing = [name for name, x in (("operating cash flow", ocf), ("capital spending", capex),
                                            ("share count", sh)) if x is None]
            why = (f"{', '.join(missing)} missing" if missing else "share count not positive")
        years.append(YearFcf(labels[i], v, why))
        values.append(v)
    latest = values[0]
    if latest is None:
        # Capital spending can only lower free cash flow: a latest year whose operating cash flow was
        # already negative has no positive free cash flow whatever the missing line says.
        ocf0, capex0, sh0 = (rows[0].get("operating_cash_flow"), rows[0].get("capital_expenditure"),
                             rows[0].get(share_key))
        if ocf0 is not None and ocf0 <= 0 and capex0 is None and sh0 is not None and sh0 > 0:
            return BaseReading(years=tuple(years), share_basis=basis, currency=ccy, note=NO_FCF,
                               not_valued=True)
        return BaseReading(years=tuple(years), share_basis=basis, currency=ccy,
                           note=f"{years[0].note} for {labels[0]}")
    average = sum(values) / n if all(v is not None for v in values) else None
    if average is None:
        have = sum(1 for v in values if v is not None)
        base, chosen = latest, "the latest year"
        note = f"the three-year average needs three years of accounts; {have} of {n} are usable"
    elif average < latest:
        base, chosen, note = average, "the three-year average", ""
    else:
        base, chosen, note = latest, "the latest year", ""
    if base <= 0:
        return BaseReading(years=tuple(years), latest=latest, average=average, base=base, chosen=chosen,
                           share_basis=basis, currency=ccy, note=NO_FCF, not_valued=True)
    return BaseReading(years=tuple(years), latest=latest, average=average, base=base, chosen=chosen,
                       share_basis=basis, currency=ccy, note=note)


# --------------------------------------------------------------------------- #
# the price
# --------------------------------------------------------------------------- #
def price_in_reporting_currency(f, last_close: Optional[float], quote_fx) -> PriceReading:
    """The quoted price put into the currency of the statements. Same currency: as is. Pence and
    pounds: exact. Two different currencies: the dated month-end rate the page already fetches, shown;
    no rate is "no exchange rate", never a guess."""
    from ..abs_readings import currency_relation
    q = (getattr(f, "currency", None) or "").strip()
    a = (getattr(f, "financial_currency", None) or "").strip()
    px = _num(last_close)
    if px is None or px <= 0:
        return PriceReading(quote_currency=q, currency=a or q, note="no price")
    if getattr(f, "statements_currency_note", ""):
        return PriceReading(quote_price=px, quote_currency=q, currency=a,
                            note=f"statements not in one currency ({f.statements_currency_note})")
    rel, unit = currency_relation(q, a)
    if rel == "same":
        return PriceReading(price=px, currency=a or q, quote_price=px, quote_currency=q or a)
    fx = unit if rel == "unit" else quote_fx
    rate = getattr(fx, "rate", None)
    if fx is None or not rate or rate <= 0:
        return PriceReading(quote_price=px, quote_currency=q, currency=a, note="no exchange rate")
    return PriceReading(price=px / rate, currency=a, quote_price=px, quote_currency=q, rate=rate,
                        rate_line=(fx.tag() if rel == "mixed" else "pence and pounds"))


# --------------------------------------------------------------------------- #
# the hurdle
# --------------------------------------------------------------------------- #
def hurdle_for(currency: str, definition: ValueDefinition, *, today: date,
               bond_source: Optional[Callable] = None) -> HurdleReading:
    from ..data.bond_yield import bond_yield as _bond_yield, major_currency
    ccy = major_currency(currency)
    fetch = bond_source or _bond_yield
    try:
        res = fetch(ccy, today=today)
    except Exception:                                    # noqa: BLE001 - no hurdle, never a crash
        return HurdleReading(currency=ccy, margin=definition.margin, note="the bond yield could not be read")
    if not res.available:
        return HurdleReading(currency=ccy, margin=definition.margin, note=res.reason)
    y = res.yield_
    return HurdleReading(currency=ccy, bond_name=y.name, bond_symbol=y.symbol, bond_date=y.as_of,
                         bond_yield=y.value, margin=definition.margin, hurdle=y.value + definition.margin,
                         note=y.note)


# --------------------------------------------------------------------------- #
# the growth cases
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Growth:
    rate: Optional[float] = None
    note: str = ""
    detail: str = ""


def _own_growth(record, window: int) -> _Growth:
    leg = getattr(getattr(record, "eps", None), "cagr", None) or {}
    r = leg.get(window)
    if r is None:
        return _Growth(note="no growth record")
    if r.value is None:
        return _Growth(note=r.note or "earnings per share growth not available")
    if r.span is not None and r.span < window:
        return _Growth(note=f"only {r.span} year{'s' if r.span != 1 else ''} of accounts on file, "
                            f"so a {window}-year rate cannot be formed")
    return _Growth(rate=float(r.value), detail=r.caution.strip(" ()") if r.caution else "")


def last_reported_eps(f, history) -> tuple[Optional[float], str, str]:
    """``(eps, fiscal year end as ISO date or year, basis)``: the last reported fiscal year's diluted
    earnings per share (the statement figure). The provider's own diluted line first; else net income
    over the share count from the long history, said so."""
    aligned = (getattr(f, "aligned_annual", None) or {}).get("diluted_eps") or []
    ends = (getattr(f, "aligned_period_ends", None) or {}).get("diluted_eps") or []
    for v, e in zip(aligned, ends):
        if _num(v) is not None:
            return float(v), str(e)[:10], "diluted earnings per share"
        break                                            # the newest year is the one that counts
    if history is not None and getattr(history, "available", False):
        eps, years = list(history.eps), list(history.years)
        if eps and _num(eps[0]) is not None and years:
            return float(eps[0]), str(years[0]), "net income over the share count"
    return None, "", ""


def _steps(last_end: str, next_end: str) -> Optional[int]:
    try:
        n = date.fromisoformat(next_end[:10])
        if len(last_end) >= 10:
            return round((n - date.fromisoformat(last_end[:10])).days / 365.25)
        return n.year - int(last_end)
    except (ValueError, TypeError):
        return None


def consensus_growth(f, history, trend) -> tuple[_Growth, str]:
    """Two steps from the last reported fiscal year's diluted EPS to the second forecast year's
    consensus EPS, as a compound yearly rate; both years named. Unavailable without both forecast years."""
    if trend is None:
        return _Growth(note="analyst data was not requested for this page"), ""
    cur, nxt = getattr(trend, "current", None), getattr(trend, "next_year", None)
    if cur is None or nxt is None or _num(cur.now) is None or _num(nxt.now) is None:
        return _Growth(note=getattr(trend, "note", "") or "analysts' forecasts for the next two fiscal years "
                                                          "are not both available"), ""
    start, last_end, basis = last_reported_eps(f, history)
    if start is None:
        return _Growth(note="the last reported year's earnings per share is not available"), ""
    steps = _steps(last_end, nxt.period_end)
    if steps is None or not 1 <= steps <= 3:
        return _Growth(note="the forecast years do not follow the last reported year"), ""
    rate = am.compound_rate(start, nxt.now, steps)
    ccy = (getattr(f, "financial_currency", None) or "").strip() or None
    last_name = (f"the year to {_month_label(last_end)}" if len(last_end) >= 10 else f"{last_end}")
    words = {1: "one step", 2: "two steps", 3: "three steps"}[steps]
    detail = (f"{words} from {last_name} ({format_money(start, ccy)}) to the year to "
              f"{_month_label(nxt.period_end)} ({format_money(nxt.now, ccy)})")
    if rate is None:
        return _Growth(note=f"consensus growth is not defined, earnings per share is at or below zero: {detail}"), detail
    return _Growth(rate=rate, detail=detail), detail


def _case(key: str, growth: _Growth, path: list, base: float, price: float, definition: ValueDefinition,
          *, growth_text: str, not_testable: bool = False) -> CaseReading:
    mark = definition.high_growth_mark
    high = growth.rate is not None and growth.rate > mark
    if not path:
        return CaseReading(key, CASE_LABELS[key], growth.rate, "", note=growth.detail, reason=growth.note,
                           not_testable=not_testable, high_growth=high)
    r = am.path_return(base, price, path, bounds=definition.return_range, iterations=definition.iterations)
    bound = ""
    if r is None:
        # outside the solver's range: say which side, and let it count as the bound it hit
        if am.path_value(base, path, definition.return_range[1]) > price:
            r, bound = definition.return_range[1], f"above {definition.return_range[1]:.0%}"
        else:
            r, bound = definition.return_range[0], f"below {definition.return_range[0]:.2%}"
    return CaseReading(key, CASE_LABELS[key], growth.rate, growth_text, r, bound,
                       note=growth.detail, not_testable=not_testable, high_growth=high, path=tuple(path))


def _pct(v: float) -> str:
    return f"{100.0 * v:.1f}%"


def build_cases(base: BaseReading, price: PriceReading, hurdle: HurdleReading, record, f, history, trend,
                definition: ValueDefinition) -> tuple[tuple, tuple]:
    """The four growth cases and the backtest-form three. Without a usable base or price, the cases
    are listed as unavailable with the reason; without a bond yield only the downside case is."""
    own5, own10 = _own_growth(record, definition.own_windows[0]), _own_growth(record, definition.own_windows[1])
    cons, _ = consensus_growth(f, history, trend)
    ready = base.usable and price.available
    why_not = (NO_FCF if base.not_valued else base.note or "no free cash flow per share") if not base.usable \
        else (price.note or "no price")

    def unavailable(key, g: _Growth, nt=False):
        return CaseReading(key, CASE_LABELS[key], g.rate, "", note=g.detail, reason=(g.note or why_not),
                           not_testable=nt, high_growth=g.rate is not None and g.rate > definition.high_growth_mark)

    def constant(key, g: _Growth, nt=False):
        if not ready or g.rate is None:
            return unavailable(key, g, nt)
        return _case(key, g, am.constant_path(g.rate, definition.horizon_years), base.base, price.price,
                     definition, growth_text=f"{_pct(g.rate)} a year", not_testable=nt)

    def downside(fast: _Growth, fast_name: str):
        key = DOWNSIDE
        if fast.rate is None:
            return CaseReading(key, CASE_LABELS[key], reason=f"no {fast_name} rate to start from")
        if hurdle.bond_yield is None or not ready:
            return CaseReading(key, CASE_LABELS[key], fast.rate,
                               reason=(f"no bond yield: {hurdle.note}" if hurdle.bond_yield is None else why_not))
        path = am.downside_path(fast.rate, hurdle.bond_yield, horizon=definition.horizon_years,
                                fast_years=definition.fast_years)
        text = (f"{_pct(fast.rate)} for {definition.fast_years} years ({fast_name}), "
                f"then falling to {100.0 * hurdle.bond_yield:.2f}%")
        return _case(key, _Growth(fast.rate), path, base.base, price.price, definition, growth_text=text)

    cases = (constant(OWN_5, own5), constant(OWN_10, own10), constant(CONSENSUS, cons, True),
             downside(cons if cons.rate is not None else own5,
                      "consensus" if cons.rate is not None else "own 5-year"))
    backtest = (cases[0], cases[1], downside(own5, "own 5-year"))
    return cases, backtest


def decide(cases, hurdle: HurdleReading, *, minimum_cases: int = 2) -> Verdict:
    """Qualifies below / clears the hurdle from the available cases. Fewer than two available, or no
    hurdle: not enough data."""
    have = [c for c in cases if c.available]
    if not hurdle.available:
        return Verdict(NOT_ENOUGH_DATA, f"no hurdle: {hurdle.note}", of=len(have))
    if len(have) < minimum_cases:
        return Verdict(NOT_ENOUGH_DATA, f"fewer than {minimum_cases} growth cases could be worked out",
                       of=len(have))
    clearing = sum(1 for c in have if c.clears(hurdle.hurdle))
    rets = [c.ret for c in have]
    state = CLEARS_HURDLE if clearing == len(have) else BELOW_HURDLE
    return Verdict(state, "", clearing, len(have), min(rets), max(rets))


# --------------------------------------------------------------------------- #
# the context readings
# --------------------------------------------------------------------------- #
def investment_context(f, warn_above: Optional[float]) -> dict:
    out: dict = {}
    (row,) = period_matched(f, ("capital_expenditure", "depreciation_amortization"), periods=1)
    capex, da = _num(row.get("capital_expenditure")), _num(row.get("depreciation_amortization"))
    if capex is not None and da is not None and abs(da) > 0:
        out["capex_over_depreciation"] = abs(capex) / abs(da)
    ab = gr.abnormal_investment(f)
    out["abnormal_investment"] = ab.value
    out["abnormal_investment_note"] = ab.note
    out["investment_warning"] = (INVESTMENT_WARNING if ab.value is not None and warn_above is not None
                                 and ab.value > warn_above else "")
    out["investment_boundary"] = warn_above
    return out


# --------------------------------------------------------------------------- #
# the whole section
# --------------------------------------------------------------------------- #
def build_absolute(f, *, gate, last_close: Optional[float], quote_fx=None, growth_record=None, history=None,
                   trend=None, today: Optional[date] = None, context: Optional[dict] = None,
                   definition: Optional[ValueDefinition] = None,
                   bond_source: Optional[Callable] = None) -> Optional[AbsoluteValue]:
    """The price-and-return section for one company, or None when the definition cannot be loaded (a
    context section, never a crash). ``context`` carries the readings the caller already holds
    (EBIT over enterprise value, earnings power value) so this module need not rebuild factor inputs."""
    today = today or date.today()
    if definition is None:
        try:
            definition = load_definition()
        except (ValueDefinitionError, OSError):
            return None
    gate_state = getattr(gate, "state", "") if gate is not None else ""
    debt_line = gr.debt_definition_line(f) if f is not None else ""
    if gate_state == NOT_COVERED:
        return AbsoluteValue(definition.id, gate_state, shown=False, debt_line=debt_line,
                             verdict=Verdict(NOT_COVERED, getattr(gate, "reason", "")))
    if f is None:
        return AbsoluteValue(definition.id, gate_state, shown=False,
                             verdict=Verdict(NOT_ENOUGH_DATA, "no accounts were available"))

    base = free_cash_flow_base(f, definition)
    price = price_in_reporting_currency(f, last_close, quote_fx)
    hurdle = hurdle_for(base.currency or price.currency, definition, today=today, bond_source=bond_source)
    ready = base.usable and price.available
    yield_on_base = (base.base / price.price) if ready else None
    implied, implied_note = None, ""
    if not ready:
        implied_note = NO_FCF if base.not_valued else f"not enough data: {base.note or price.note or 'no base'}"
    elif not hurdle.available:
        implied_note = f"not enough data: no hurdle ({hurdle.note})"
    else:
        implied = am.implied_growth(base.base, price.price, hurdle.hurdle,
                                    horizon=definition.horizon_years, bounds=definition.implied_growth_range,
                                    iterations=definition.iterations)
        if implied is None:
            implied_note = (f"not solvable: the price needs growth outside {definition.implied_growth_range[0]:.0%} "
                            f"to {definition.implied_growth_range[1]:+.0%} a year")

    cases, backtest = build_cases(base, price, hurdle, growth_record, f, history, trend, definition)
    try:
        from ..gate.definition import load_gate
        boundary = load_gate().intensity_warn_above
    except Exception:                                     # noqa: BLE001 - no boundary, no warning
        boundary = None
    ctx = {**(context or {}), **investment_context(f, boundary)}

    bt_verdict = decide(backtest, hurdle, minimum_cases=2)
    verdict = decide(cases, hurdle, minimum_cases=2)
    if gate_state == EXCLUDED:
        verdict = Verdict(EXCLUDED, getattr(gate, "reason", ""))
        bt_verdict = Verdict(EXCLUDED, getattr(gate, "reason", ""))
    elif gate_state != gate_engine.QUALIFIES:
        reason = getattr(gate, "reason", "") if gate is not None else "the gate could not be run"
        verdict = Verdict(NOT_ENOUGH_DATA, reason, of=verdict.of)
        bt_verdict = Verdict(NOT_ENOUGH_DATA, reason, of=bt_verdict.of)
    elif not base.usable:
        reason = NO_FCF if base.not_valued else (base.note or "no free cash flow per share")
        verdict = Verdict(NOT_ENOUGH_DATA, reason)
        bt_verdict = Verdict(NOT_ENOUGH_DATA, reason)
    elif not price.available:
        verdict = Verdict(NOT_ENOUGH_DATA, f"not enough data: {price.note}")
        bt_verdict = verdict
    return AbsoluteValue(definition.id, gate_state, base, price, hurdle, yield_on_base, implied, implied_note,
                         cases, backtest, verdict, bt_verdict, ctx, debt_line)
