"""Factor registry for the rank-based multi-factor decision core (Aristos v2).

Grounding: Schwartz & Hanauer, "Do Simple Stock-Picking Formulas Still Work?"
(2024, evaluated 1963-2022; Piotroski / Greenblatt Magic Formula / Carlisle
Acquirer's Multiple / van Vliet-Blitz Conservative). All four earn significant
risk-adjusted returns by giving efficient exposure to the SAME established factors —
VALUE, PROFITABILITY (quality), MOMENTUM (and low-vol). The methods RANK and combine
ranks; they do NOT assign magic point-weights. This module computes those factors
from DETERMINISTIC market/fundamental data already fetched — NEVER from the (wobbly)
LLM specialists (rejected: their per-agent instability would re-poison the verdict).

Each factor returns a float or None (NOT-EVAL — missing/insufficient data). The
rank engine (rank_engine.py) does the ranking; this module only extracts values and
declares each factor's NATURAL direction (is higher or lower better?).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Callable, Optional

from .data.adapter import DataUnavailable, Fundamentals
from .etf_static import StaticFill, apply_static_fill, default_static_rows
from .fund_currency import (
    FUND_SIZE_CCY,
    FX_UNAVAILABLE_NOTE,
    UNVERIFIED_CCY_NOTE,
    FundSizeConversion,
    convert_fund_size,
    needs_conversion,
    normalize_currency_code,
)
from .tools.screening import (
    accrual_ratio,
    altman_z_score,
    net_debt_to_operating_income,
    payout_coverage_fcf,
    piotroski_f_score,
    revenue_cagr,
    through_cycle_roic,
)
from .tools.technical import (
    _TD_6M,
    _TD_12M,
    annualized_volatility,
    technical_snapshot,
    total_return,
)
from .tools.price_context import PriceContext, price_context
from .tools.reversion import ReversionValue, reversion_value
from .tools.valuation_band import BAND_YEARS, ValuationBand, valuation_band

_ROIC_WINDOW = 4   # through-cycle window (matches the screen's ROIC window intent)


@dataclass(frozen=True)
class CurrencyConversion:
    """A frozen FX conversion from the accounts' currency to the price currency, fetched
    through the SAME adapter (so the rate is cached, frozen into run records, and
    replayable). ``rate`` multiplies an accounts-currency amount to yield the price
    currency (e.g. DKK amount x 0.1452 = USD)."""

    rate: float
    from_ccy: str          # financialCurrency (accounts)
    to_ccy: str            # currency (price/listing)
    as_of: str             # 'YYYY-MM-DD'

    @property
    def tag(self) -> str:
        return f"{self.from_ccy}→{self.to_ccy} @ {self.rate:.4g} ({self.as_of})"


@dataclass
class FactorInputs:
    """The deterministic data a factor may read, per ticker — assembled from the
    SAME adapter the council uses (fundamentals + price-derived returns/vol)."""

    ticker: str
    fundamentals: Optional[Fundamentals] = None
    return_6m: Optional[float] = None
    return_12m: Optional[float] = None
    annualized_volatility: Optional[float] = None
    last_close: Optional[float] = None     # for the screen-as-prefilter (yield etc.)
    # Accounts->price FX (VERIFY-2 ITEM 1): set when financialCurrency != currency and the
    # rate was fetched. ``fx_failed`` is True when the currencies differ but the rate
    # could NOT be fetched — the EV route then ABSTAINS (never mixes currencies).
    fx: Optional[CurrencyConversion] = None
    fx_failed: bool = False
    # ETF static layer (ETF-STATIC-1): the outcome of filling slow ETF fields from the
    # committed static CSV for THIS name. ``filled`` maps a Fundamentals field to its
    # ``static: <as_of>, <source>`` provenance tag; ``stale`` maps a field withheld
    # because its entry is >90 days old to the staleness note. None / empty for a stock
    # or an ETF the layer didn't touch, so the ETF factors' source tags degrade to the
    # generic computed/abstained exactly as before.
    static: Optional[StaticFill] = None
    # fund_size base-currency normalisation (DATA-HYGIENE-1). The vendor reports an ETF's
    # total assets in the FUND'S base currency, so a cross-fund ranking must compare ONE
    # currency. Exactly one of these is set for a served fund_size:
    #   fund_size_fx                 — converted to EUR; carries the dated receipt.
    #   fund_size_fx_failed          — source currency known, rate unavailable -> the value
    #                                  is WITHHELD (total_assets set to None) and the
    #                                  factor abstains; never a mixed-currency number.
    #   fund_size_currency_unverified — no source currency known (a static row written
    #                                  before the currency column, or a vendor value): the
    #                                  amount is served UNCONVERTED exactly as before and
    #                                  FLAGGED, never relabelled EUR.
    # A fund_size already in EUR needs none of them (nothing to convert).
    fund_size_fx: Optional[FundSizeConversion] = None
    fund_size_fx_failed: bool = False
    fund_size_currency_unverified: bool = False
    # ABSOLUTE valuation context (VALBAND-1): where today's EV/EBIT (or the labelled
    # P/E fallback) sits in this name's OWN 5-year monthly distribution. Every other
    # field here is a RELATIVE measure — ranked within a cohort — so this is the one
    # input that can say "expensive against its own past" when the whole cohort is hot.
    # None when the 5-year price fetch failed or was not requested; an ABSTAINED band
    # is a ValuationBand with percentile None carrying its reason, never None here.
    valuation_band: Optional[ValuationBand] = None
    # SHARE PRICE + 52-WEEK POSITION (PRICE-1) — ALWAYS ON, never gated by the band flag.
    # Read off the SAME 400-day bars the momentum/volatility legs already consume (no new
    # fetch, no re-windowing), so it costs nothing and cannot move a ranking. Carries the
    # currency and the close's as-of DATE, so a stale cache is visible rather than silent.
    # Distinct from ``last_close`` above on purpose: that one is the ADJUSTED close the
    # screen consumes; this is the traded ``close``, which is what "the share price"
    # means (see tools/price_context.py).
    price_context: Optional[PriceContext] = None


# --- factor functions (pure; None == NOT-EVAL) ---------------------------- #
def enterprise_value(f, fx: "Optional[CurrencyConversion]" = None) -> Optional[float]:
    """EV = market cap + total debt − cash & short-term investments.

    None unless ALL of market_cap, total_debt, total_cash are present (a partial EV is
    misleading, so we abstain and let the earnings-yield factor fall back to
    EBIT/market_cap). A NEGATIVE or zero EV (cash & investments exceed market cap + debt
    — only a deeply cash-rich small cap) is returned as-is here; the factor guards it —
    an EBIT/EV over a non-positive EV would be a nonsense negative/blow-up rank artifact.

    Caveat (refined, not exact): yfinance ``totalDebt`` includes operating leases and
    the figure carries no minority-interest / pension adjustments — a refined proxy for
    true EV, documented in CALCULATIONS.md §6."""
    if f is None:
        return None
    if f.market_cap is None or f.total_debt is None or f.total_cash is None:
        return None
    # market_cap is in the PRICE currency; total_debt/total_cash are in the ACCOUNTS
    # currency. On a mismatch, convert debt & cash into the price currency (VERIFY-2
    # ITEM 1) — never mix. rate == 1.0 when there is no conversion (same currency).
    r = fx.rate if fx is not None else 1.0
    return f.market_cap + f.total_debt * r - f.total_cash * r


# --- Factor SOURCE tags (ITEM 1: silent fallbacks become disclosed fallbacks) ------- #
# The exact computation path a factor took FOR ONE NAME, recorded at compute time so the
# report can say EV-or-proxy in plain text. "computed"/"abstained" are the defaults for
# factors with no fallback; the ones with fallbacks (earnings_yield, net_payout_yield)
# name their path.
SRC_COMPUTED = "computed"
SRC_ABSTAINED = "abstained"
SRC_EV = "ev"                              # earnings_yield on true EBIT/EV
SRC_EBIT_MCAP = "fallback:ebit_mcap"       # earnings_yield fell back to EBIT/market cap
SRC_PE = "fallback:pe"                     # earnings_yield fell back to 1/PE
SRC_DIVIDEND_YIELD = "fallback:dividend_yield"   # net_payout fell back to dividend yield


def _earnings_yield_outcome(fi: FactorInputs) -> tuple[Optional[float], str]:
    """(value, source) for the value leg — the SINGLE place the EBIT/EV vs proxy path is
    decided, so the disclosed source can never drift from the computed value.

    EBIT/EV when the balance-sheet components are available (ITEM 6), falling back to
    EBIT/market_cap when they are missing, then 1/PE. Negative-EV guard: only when cash &
    investments exceed market cap + debt (EV ≤ 0 — a deeply cash-rich small cap) is EBIT/EV
    a meaningless negative/huge value, so we ABSTAIN. A merely net-cash mega-cap (cash >
    debt but < market cap, e.g. NVDA/GOOGL) still has a large POSITIVE EV and ranks
    normally. Higher is cheaper/better."""
    f = fi.fundamentals
    if f is None:
        return None, SRC_ABSTAINED
    # Currencies differ but the FX rate was unavailable: ABSTAIN on the EV route (VERIFY-2
    # ITEM 1) — never silently compute a mixed-currency EV, never silently fall back.
    if fi.fx_failed:
        return None, SRC_ABSTAINED
    # EBIT is in the ACCOUNTS currency; convert to the price currency on a mismatch so
    # EBIT and EV share one currency. rate == 1.0 (no fx) leaves same-currency names
    # byte-for-byte unchanged.
    r = fi.fx.rate if fi.fx is not None else 1.0
    ev_src = f"{SRC_EV}, {fi.fx.tag}" if fi.fx is not None else SRC_EV
    ebit = f.ebit[0] * r if f.ebit else None
    if ebit is not None:
        ev = enterprise_value(f, fi.fx)
        if ev is not None:
            return (ebit / ev, ev_src) if ev > 0 else (None, SRC_ABSTAINED)
        if f.market_cap and f.market_cap > 0:
            proxy_src = (f"{SRC_EBIT_MCAP}, {fi.fx.tag}" if fi.fx is not None
                         else SRC_EBIT_MCAP)
            return ebit / f.market_cap, proxy_src         # EV components missing -> proxy
    if f.pe_ratio and f.pe_ratio > 0:
        return 1.0 / f.pe_ratio, SRC_PE
    return None, SRC_ABSTAINED


def _earnings_yield(fi: FactorInputs) -> Optional[float]:
    return _earnings_yield_outcome(fi)[0]


def _earnings_yield_source(fi: FactorInputs) -> str:
    return _earnings_yield_outcome(fi)[1]


# --- Income-durability legs (CYCLICAL-INCOME-1) ---------------------------- #
# Both share their arithmetic with the like-named screen primitive, so a value that is
# ranked and a value that is screened can never diverge (the FORENSIC-1 pattern).
def _payout_coverage_fcf(fi: FactorInputs) -> Optional[float]:
    """How much of the cash it generates the company pays out. LOW is better.

    THE SAME helper ``max_payout_ratio_fcf`` screens on — not a second implementation
    that agrees today. Abstains where the criterion would fall back to its MARKED GAAP
    proxy: a screen floor may take a disclosed proxy, but a RANK COLUMN must be one
    measure, or names are ordered against different things without saying so.
    """
    return payout_coverage_fcf(fi.fundamentals)[0]


def _payout_coverage_source(fi: FactorInputs) -> str:
    value, note = payout_coverage_fcf(fi.fundamentals)
    return SRC_COMPUTED if value is not None else f"{SRC_ABSTAINED}: {note}"


def _net_debt_to_operating_income(fi: FactorInputs) -> Optional[float]:
    """Net debt measured against THROUGH-CYCLE operating profit. LOW is better; net cash
    is negative and ranks best. Generic — quality_v1 wants this same measure."""
    return net_debt_to_operating_income(fi.fundamentals)[0]


def _net_debt_to_oi_source(fi: FactorInputs) -> str:
    value, note = net_debt_to_operating_income(fi.fundamentals)
    return SRC_COMPUTED if value is not None else f"{SRC_ABSTAINED}: {note}"


def _return_on_capital(fi: FactorInputs) -> Optional[float]:
    """Greenblatt's quality leg — through-cycle ROIC off the PROVIDED invested
    capital (negative-equity-safe). Higher is better."""
    f = fi.fundamentals
    if f is None:
        return None
    roic, _ = through_cycle_roic(f.operating_income, f.tax_provision,
                                 f.pretax_income, f.invested_capital,
                                 window=_ROIC_WINDOW)
    return roic


def _momentum_12m(fi: FactorInputs) -> Optional[float]:
    return fi.return_12m


def _momentum_6m(fi: FactorInputs) -> Optional[float]:
    return fi.return_6m


def _low_volatility(fi: FactorInputs) -> Optional[float]:
    """Annualized volatility — direction LOW (lower vol ranks better). The
    Conservative-Formula leg that, with momentum, structurally avoids falling
    knives (a crashing name is high-vol AND negative-momentum)."""
    return fi.annualized_volatility


def _dividend_record_stale(fi: FactorInputs) -> str:
    """The staleness reason for this name's dividend record, or "" (YIELD-STALE-1).

    Shared with ``min_dividend_yield`` so the screen and the rank cannot disagree about
    whether a record still reaches the present."""
    from .tools.screening import _payment_dates, _today_for, dividend_record_staleness

    f = fi.fundamentals
    if f is None:
        return ""
    stale, reason = dividend_record_staleness(_payment_dates(f), today=_today_for(f))
    return reason if stale else ""


def _net_payout_yield(fi: FactorInputs) -> Optional[float]:
    """Net payout = dividends + buybacks / market cap. Buyback data isn't on free
    fundamentals, so this falls back to DIVIDEND YIELD (an under-count for big
    repurchasers — documented). Higher is better.

    YIELD-STALE-1: ABSTAINS when the provider's dividend record stops short. A trailing
    yield built from half a year of payments over a full year of price is roughly half the
    real one and looks entirely ordinary, so ranking on it would be worse than not ranking
    at all."""
    f = fi.fundamentals
    if f is None or _dividend_record_stale(fi):
        return None
    return f.dividend_yield


def _net_payout_source(fi: FactorInputs) -> str:
    f = fi.fundamentals
    stale = _dividend_record_stale(fi) if f is not None else ""
    if stale:
        return f"{SRC_ABSTAINED}: {stale}"
    if f is None or f.dividend_yield is None:
        return SRC_ABSTAINED
    return SRC_DIVIDEND_YIELD              # buybacks unavailable -> always the fallback


def _revenue_growth(fi: FactorInputs) -> Optional[float]:
    f = fi.fundamentals
    if f is None:
        return None
    cagr, _ = revenue_cagr(f.total_revenue, 3)
    return cagr


def _dividend_streak(fi: FactorInputs) -> Optional[float]:
    """Consecutive years of dividend increases (adapter-derived) — a durable-income
    quality signal; higher is better. None when the streak couldn't be derived."""
    f = fi.fundamentals
    s = f.dividend_streak_years if f is not None else None
    return float(s) if s is not None else None


def _distribution_yield(fi: FactorInputs) -> Optional[float]:
    """ETF distribution yield — the fund's trailing distribution/dividend yield
    (``dividend_yield``, a DECIMAL). Higher is better. The income leg of the dividend-ETF
    lens. None when the provider omits it (the lens abstains, never excludes)."""
    f = fi.fundamentals
    return f.dividend_yield if f is not None else None


def _expense_ratio(fi: FactorInputs) -> Optional[float]:
    """ETF expense ratio (``net_expense_ratio``) — the ongoing cost that compounds
    against the holder forever, so direction is LOW (cheaper ranks better). Vendor value
    as-is; the lens ranks it RELATIVELY, so its unit convention doesn't affect the rank.
    None when absent -> the lens abstains.

    UNIT (ETFCHK-3): the vendor value is a PERCENT, not a fraction — SCHD's 0.06% arrives
    as ``0.06``. Ranking is unit-invariant so this factor is untouched, but any absolute
    presentation of the number (e.g. the fee gloss in company_check._expense_ratio_gloss)
    MUST divide by 100 first."""
    f = fi.fundamentals
    return f.net_expense_ratio if f is not None else None


def _fund_size(fi: FactorInputs) -> Optional[float]:
    """ETF fund size (``total_assets``) — net assets, a liquidity + closure-risk proxy;
    higher ranks better. None when absent -> the lens abstains.

    CURRENCY (DATA-HYGIENE-1): the value read here is already normalised — the fetch edge
    (``gather_factor_inputs``) converted it to EUR at a dated rate, withheld it (None) when
    its currency was known but the rate wasn't, or left it unconverted-and-FLAGGED when no
    source currency is known. The receipt rides on ``_fund_size_source``."""
    f = fi.fundamentals
    return f.total_assets if f is not None else None


# ETF factor SOURCE tags (ETF-STATIC-1). When a field was filled from the committed
# static layer, disclose its provenance receipt (``static: <as_of>, <source>``) so the
# report renders ``[static: …]`` exactly like the FX receipt; when the field was WITHHELD
# because its static entry is stale, disclose the staleness note. Otherwise degrade to the
# generic computed/abstained — byte-identical to the pre-static behaviour.
def _etf_field_source(fi: FactorInputs, fund_field: str, value: Optional[float]) -> str:
    st = fi.static
    if st is not None:
        if fund_field in st.filled:
            return st.filled[fund_field]
        if fund_field in st.stale:
            return st.stale[fund_field]
    return SRC_COMPUTED if value is not None else SRC_ABSTAINED


def _distribution_yield_source(fi: FactorInputs) -> str:
    return _etf_field_source(fi, "dividend_yield", _distribution_yield(fi))


def _expense_ratio_source(fi: FactorInputs) -> str:
    return _etf_field_source(fi, "net_expense_ratio", _expense_ratio(fi))


def _with_fx_receipt(base: str, receipt: str) -> str:
    """Append a currency receipt to a factor's source tag (DATA-HYGIENE-1).

    ``computed`` takes the receipt as its detail (``computed: 4.17bn USD @ 0.86 EUR/USD,
    2026-07-29``); a receipt-bearing base like ``static: 2026-07-21, EODHD`` keeps its own
    receipt and the conversion is appended after an em dash, so the tag still STARTS with
    ``static:`` — the marker the narrator's static-evidence ledger and the report's
    provenance badge both match on (pipeline._static_factor_evidence)."""
    return f"{base}: {receipt}" if base == SRC_COMPUTED else f"{base} — {receipt}"


def _fund_size_source(fi: FactorInputs) -> str:
    """The fund_size source tag, extended with its currency disclosure (DATA-HYGIENE-1).

    Three currency outcomes ride on the tag: the dated conversion receipt, the
    unavailable-rate abstention note (the value was withheld), or the currency-unverified
    flag on a value served unconverted. Everything else is unchanged."""
    if fi.fund_size_fx_failed:
        return FX_UNAVAILABLE_NOTE                    # value withheld -> abstained
    base = _etf_field_source(fi, "total_assets", _fund_size(fi))
    if fi.fund_size_fx is not None:
        return _with_fx_receipt(base, fi.fund_size_fx.tag)
    if fi.fund_size_currency_unverified:
        return _with_fx_receipt(base, UNVERIFIED_CCY_NOTE)
    return base


def _price_to_book(fi: FactorInputs) -> Optional[float]:
    """Financials VALUE leg — price / book value, LOWER is better (cheaper).

    Prefers the vendor ``price_to_book`` (currency-consistent as the provider computes
    it); falls back to ``market_cap / closing shareholders' equity``. ABSTAINS (None,
    never excludes) when book equity is ≤ 0 or missing — a non-positive book makes P/B a
    meaningless negative, so a value trap can't masquerade as cheap. A vendor value ≤ 0
    (negative book) is likewise abstained rather than used.

    Currency note: the vendor scalar is currency-clean; the fallback divides a
    price-currency market cap by an accounts-currency equity, so it assumes the two
    match — guaranteed for financials_16 (all-US, by design). A foreign name would need
    the VERIFY-2 ITEM 1 FX layer, deferred to a later versioned universe (see the
    universe rationale)."""
    f = fi.fundamentals
    if f is None:
        return None
    if f.price_to_book is not None and f.price_to_book > 0:
        return f.price_to_book
    if f.market_cap is not None and f.shareholders_equity:
        eq = f.shareholders_equity[0]                  # closing (latest) book equity
        if eq is not None and eq > 0:
            return f.market_cap / eq
    return None                                        # book ≤ 0 or missing -> abstain


def _return_on_equity(fi: FactorInputs) -> Optional[float]:
    """Financials QUALITY leg — return on equity, HIGHER is better.

    Prefers the vendor ``return_on_equity`` (TTM). Falls back to the latest annual
    ``net_income`` over the MEAN of opening+closing equity (NOT through-cycle averaged —
    one convention for v1; smoothing is a possible v2 on scoreboard evidence). ABSTAINS
    (None, never excludes) when equity is ≤ 0 or income is missing. Same currency note as
    price_to_book — the fallback assumes accounts==price currency (true for
    financials_16)."""
    f = fi.fundamentals
    if f is None:
        return None
    if f.return_on_equity is not None:
        return f.return_on_equity
    if f.net_income and f.shareholders_equity:
        ni = f.net_income[0]                           # latest annual net income
        eqs = f.shareholders_equity
        avg_eq = (eqs[0] + eqs[1]) / 2.0 if len(eqs) >= 2 else eqs[0]
        if ni is not None and avg_eq is not None and avg_eq > 0:
            return ni / avg_eq
    return None                                        # equity ≤ 0 / no income -> abstain


def _piotroski_f_score(fi: FactorInputs) -> Optional[float]:
    """Piotroski F-Score (0-9) — nine binary accounting-quality checks, HIGHER better.

    Delegates ALL nine checks to ``tools.screening.piotroski_f_score``, the single
    implementation the ``min_f_score`` screen criterion also calls, so the ranked
    value and the screened value can never diverge. ABSTAINS (None, never excludes)
    when fewer than 5 of the 9 checks are computable — a partial tally is not a score.

    RANKING PROPERTY (PIOTROSKI-1, documented in CALCULATIONS.md): the F-Score is a
    COARSE 0-9 integer, so on a 20-40 name universe it produces large TIED BLOCKS
    that the rank engine resolves by averaging. That is a real argument for using it
    as a SCREEN rather than a rank leg — it is NOT to be "fixed" with a tiebreaker.
    Deliberately absent from PRICE_DERIVED_FACTORS (not computable point-in-time from
    closes).

    SELECTED BY ``forensic_v1`` (FORENSIC-1) — it was registered-and-unused until then.
    The tied-block property above is why it sits alongside two CONTINUOUS legs (the
    accrual ratio and the Z-Score) rather than carrying a lens on its own: they break
    the ties the coarse integer creates, so nothing had to be "fixed" in the score.
    """
    result = piotroski_f_score(fi.fundamentals)
    return None if result.score is None else float(result.score)


# --- Forensic legs (FORENSIC-1) — NON-GATING by every strategy that selects them --- #
def _forensic_source(result) -> str:
    """The per-name source tag for a forensic leg: ``computed`` (optionally with the
    path it took, e.g. ``computed: single-year assets``) or ``abstained: <reason>`` —
    so an abstention is never silent and a fallback is never undisclosed (ITEM 1)."""
    if result.value is None:
        return f"{SRC_ABSTAINED}: {result.detail}" if result.detail else SRC_ABSTAINED
    return f"{SRC_COMPUTED}: {result.detail}" if result.detail else SRC_COMPUTED


def _accrual_ratio(fi: FactorInputs) -> Optional[float]:
    """Sloan accrual ratio — direction LOW (less accrued profit ranks better).

    Delegates ALL arithmetic to ``tools.screening.accrual_ratio``, the SAME function the
    ``max_accrual_ratio`` screen criterion calls, so the ranked and screened values can
    never diverge. ABSTAINS (None, never excludes) on a missing input or a non-positive
    asset base."""
    return accrual_ratio(fi.fundamentals).value


def _accrual_ratio_source(fi: FactorInputs) -> str:
    return _forensic_source(accrual_ratio(fi.fundamentals))


def _altman_z(fi: FactorInputs) -> Optional[float]:
    """Altman Z-Score — direction HIGH (further from distress ranks better).

    Delegates ALL arithmetic to ``tools.screening.altman_z_score``, shared with the
    ``min_altman_z`` criterion. ABSTAINS on a missing input, a non-positive denominator,
    and on the cross-currency case (rule 8): its fourth term mixes a quote-currency
    market cap with statement-currency liabilities, so an ADR or a non-USD reporter
    abstains rather than summing two currencies."""
    return altman_z_score(fi.fundamentals).value


def _altman_z_source(fi: FactorInputs) -> str:
    return _forensic_source(altman_z_score(fi.fundamentals))


def _valuation_band_percentile(fi: FactorInputs) -> Optional[float]:
    """Where today's valuation sits in this name's OWN 5-year band, 0-100. LOWER better
    (0 = its own historical floor, 100 = its own peak) — see ``FactorDef.direction``.

    The ONLY absolute-valuation leg in the registry: every other value factor
    (earnings_yield, price_to_book) is a cohort-relative statement, so in a uniformly
    expensive cohort they all still rank someone #1. This one can say "and all ten are
    near their own highs".

    Delegates ALL arithmetic to ``tools.valuation_band.valuation_band``, the single
    implementation the ``valuation_band_percentile`` SCREEN criterion also reads, so the
    ranked and screened values can never diverge. ABSTAINS (None) whenever the band
    abstains — short history (a recent IPO), no dated statements, a currency mismatch.

    Registered but selected by NO strategy in this PR (like piotroski_f_score): a
    threshold or a rank leg on it needs documented rationale first.
    """
    band = fi.valuation_band
    return None if band is None else band.percentile


def valuation_band_display(fi: FactorInputs) -> str:
    """The band's one-line display for this name — the report/Company Check column
    (VALBAND-1 role (a)). "—" when the band was never computed (no 5-year fetch)."""
    band = fi.valuation_band
    return "—" if band is None else band.display


def price_display(fi: FactorInputs) -> str:
    """The share-price + 52-week-position line for this name (PRICE-1) — the ONE string
    the CLI, the Run tab, the markdown record and the HTML export all render.

    ALWAYS available (it reads bars the run already fetched), so unlike the band there is
    no "never computed" case to render as "—": a name with no price bars gets an honest
    "price not available — …". "—" only for a FactorInputs assembled without the field at
    all (a hand-built test row)."""
    ctx = fi.price_context
    return "—" if ctx is None else ctx.display


def reversion_value_for(fi: FactorInputs) -> Optional[ReversionValue]:
    """This name's reversion value (PRICE-1) — today's earnings and net debt re-priced at
    its OWN median multiple from the band's series.

    Rides WITH the valuation band: ``None`` when the band was never computed on this run
    (band OFF -> no band section, so no reversion cell either); an ABSTAINING
    ``ReversionValue`` carrying its reason whenever the band ran but the arithmetic could
    not. The last close is the SAME number the price line shows, so the rendered gap
    always reconciles with the price beside it. Display only — see tools/reversion.py."""
    if fi.valuation_band is None:
        return None
    ctx = fi.price_context
    f = fi.fundamentals
    return reversion_value(fi.valuation_band, f,
                           last_close=(ctx.last_close if ctx else None),
                           currency=(getattr(f, "currency", None) if f else None))


def reversion_value_display(fi: FactorInputs) -> str:
    """``reversion_value_for`` rendered as one line — "" when the band was never computed
    on this run. Kept for single-name surfaces; the universe report renders the same
    numbers as table cells (``pipeline.valuation_band_table``)."""
    rev = reversion_value_for(fi)
    return "" if rev is None else rev.display


@dataclass(frozen=True)
class FactorDef:
    name: str
    fn: Callable[[FactorInputs], Optional[float]]
    direction: str        # "high" = higher is better, "low" = lower is better
    label: str
    # NOTE: ``label`` is NOT display-only. agents/prompts.lens_brief builds the LENS
    # EMPHASIS block from these strings, so every specialist/critic/narrator prompt
    # quotes them — rewording one is a NARRATOR change, not a report change. REPORT-1
    # therefore reads them as they are and adds only the unit below.
    #
    # WHAT THE NUMBER MEANS (REPORT-1) — one of report_language.UNITS. Reports format
    # the factor's VALUE from this declaration rather than guessing at the render site,
    # so a ranked table can show "1 · 14.2%" instead of a bare rank. ``currency`` names
    # the money a "currency" unit is denominated in (fund_size is normalised to EUR).
    # Every registered factor must declare one — a test fails the registry otherwise,
    # which is what keeps the next addition honest.
    unit: str = "ratio"
    currency: Optional[str] = None
    # GLOSSARY-1 — the PLAIN-ENGLISH definition, for the report's "What the terms mean"
    # section. Distinct from ``label``: the label is the term as a table shows it (and,
    # being load-bearing in the agent prompts, is not free to reword); this is the
    # sentence that explains it to a reader who does not know the term. No jargon inside
    # a definition. Every registered factor must declare one — a test fails the registry
    # otherwise, so the next factor added arrives explained rather than bare.
    glossary: str = ""
    fallback_note: str = ""
    # Optional per-name SOURCE tag (ITEM 1). None -> the source is derived generically
    # as "computed"/"abstained" from the value; set it for factors WITH fallbacks so the
    # report discloses which path was taken per ticker.
    source_fn: Optional[Callable[[FactorInputs], str]] = None


FACTOR_REGISTRY: dict[str, FactorDef] = {
    "earnings_yield": FactorDef(
        "earnings_yield", _earnings_yield, "high", "Earnings yield (EBIT/EV)",
        glossary=("Operating profit divided by what the whole company costs to buy — "
                  "shares plus debt. The inverse of how expensive it is: higher means "
                  "more profit per euro paid."),
        unit="percent",
        fallback_note="EBIT / (market cap + total debt − cash); EBIT/market_cap "
                      "fallback when EV components missing, then 1/PE; net-cash "
                      "(EV≤0) abstains",
        source_fn=_earnings_yield_source),
    "roic": FactorDef(
        "roic", _return_on_capital, "high", "Return on invested capital",
        glossary=("The profit the business earns on the money tied up in it. High "
                  "means every euro invested in the company works hard."),
        unit="percent"),
    "momentum_12m": FactorDef(
        "momentum_12m", _momentum_12m, "high", "12-month price momentum",
        glossary=("What buying the share twelve months ago would have returned by "
                  "today. Measures trend, not value."),
        unit="percent"),
    "momentum_6m": FactorDef(
        "momentum_6m", _momentum_6m, "high", "6-month price momentum",
        glossary=("What buying the share six months ago would have returned by today. "
                  "Measures trend, not value."), unit="percent"),
    "low_volatility": FactorDef(
        "low_volatility", _low_volatility, "low", "Annualized volatility (low best)",
        glossary=("How violently the share price swings in a typical year. Lower "
                  "means a calmer ride."),
        unit="percent"),
    "net_payout_yield": FactorDef(
        "net_payout_yield", _net_payout_yield, "high", "Net payout yield",
        glossary=("Dividends plus share buybacks, as a percentage of what the company "
                  "costs — the total cash returned to owners."),
        unit="percent",
        fallback_note="dividend-yield fallback (buybacks unavailable on free "
                      "fundamentals)",
        source_fn=_net_payout_source),
    # CYCLICAL-INCOME-1 — the two legs that ask whether an income stream can SURVIVE,
    # rather than how large it is. Both LOW-direction, both through-cycle.
    "payout_coverage_fcf": FactorDef(
        "payout_coverage_fcf", _payout_coverage_fcf, "low",
        "Dividend coverage (dividends / 4-year free cash flow)",
        glossary=("What share of the cash the business actually generated is paid out "
                  "as dividends, measured against a four-year average so one strong or "
                  "one weak year does not decide it. Lower means the dividend is paid "
                  "more comfortably; above 1.0 the company is paying out more than it "
                  "earned in cash."),
        unit="percent", source_fn=_payout_coverage_source),
    "net_debt_to_operating_income": FactorDef(
        "net_debt_to_operating_income", _net_debt_to_operating_income, "low",
        "Net debt to operating profit (4-year average)",
        glossary=("Borrowings less cash, measured against a four-year average of "
                  "operating profit — roughly how many average years of profit the debt "
                  "represents. Lower is safer, and a company holding more cash than debt "
                  "shows a negative figure."),
        unit="multiple", source_fn=_net_debt_to_oi_source),
    "revenue_growth": FactorDef(
        "revenue_growth", _revenue_growth, "high", "Revenue CAGR (3y)",
        glossary="Average yearly sales growth over the measured period.",
        unit="percent"),
    "dividend_streak": FactorDef(
        "dividend_streak", _dividend_streak, "high",
        "Dividend-growth streak (years)",
        glossary=("How many consecutive years the dividend per share has risen. A "
                  "floor, not a verified total — the price history only reaches so "
                  "far back."), unit="count"),
    # Financials lens (FIN-1): the measures banks & insurers are actually priced by,
    # since EBIT/EV and ROIC are not computable for them (the Greenblatt exclusion,
    # inverted). Vendor value primary, derived fallback, abstain on non-positive book.
    "price_to_book": FactorDef(
        "price_to_book", _price_to_book, "low", "Price / book (low best)",
        glossary=("Share price divided by the accounting value of what the company "
                  "owns outright."),
        unit="multiple",
        fallback_note="vendor priceToBook; fallback market_cap / closing equity; "
                      "abstains on book ≤ 0"),
    "return_on_equity": FactorDef(
        "return_on_equity", _return_on_equity, "high", "Return on equity",
        glossary=("Profit as a percentage of the shareholders' own money in the "
                  "business."),
        unit="percent",
        fallback_note="vendor returnOnEquity (TTM); fallback net_income / "
                      "mean(opening+closing equity); abstains on equity ≤ 0"),
    # ETF asset-class factors (ETF-1 ITEM 3). No fallbacks — abstain on a missing field
    # (the lens declares missing: neutral, so a gap judges on the factors present, never
    # excludes). expense_ratio is the only LOW-direction leg (cost compounds against the
    # holder). Field coverage confirmed 100% on both ITEM-4 universes (ITEM 1 probe).
    "distribution_yield": FactorDef(
        "distribution_yield", _distribution_yield, "high",
        "Distribution yield",
        glossary=("The income a fund pays out over a year, as a percentage of its "
                  "price."), unit="percent",
        fallback_note="ETF trailing distribution/dividend yield (decimal)",
        source_fn=_distribution_yield_source),
    # unit="ratio" DELIBERATELY, not "percent": the vendor's expense-ratio convention is
    # not guaranteed (0.07 vs 0.0007 across sources), and the lens ranks it RELATIVELY so
    # the convention never mattered. Declaring "percent" would put a unit on a number
    # whose unit is unknown — house rule: abstain rather than invent.
    "expense_ratio": FactorDef(
        "expense_ratio", _expense_ratio, "low",
        "Expense ratio (low best)",
        glossary=("The annual fee a fund charges, as a percentage of the money you "
                  "hold in it."), unit="ratio",
        fallback_note="ETF ongoing cost; ranked relatively, direction low; the vendor's "
                      "unit convention is not asserted here",
        source_fn=_expense_ratio_source),
    "fund_size": FactorDef(
        "fund_size", _fund_size, "high",
        "Fund size (total assets, EUR)",
        glossary="How much money the fund manages in total.", unit="currency", currency="EUR",
        fallback_note="ETF net assets — liquidity + closure-risk proxy; normalised to "
                      "EUR at a dated FX rate (DATA-HYGIENE-1), abstains when the rate "
                      "is unavailable, flagged when the fund's base currency is unknown",
        source_fn=_fund_size_source),
    # Forensic lens (FORENSIC-1): earnings quality + distress. Both share their
    # arithmetic with a screen criterion (max_accrual_ratio / min_altman_z) so a ranked
    # and a screened value can never diverge, and both are selected by forensic_v1,
    # which marks NOTHING is_gating — this lens adds a column and an argument, it never
    # lowers a verdict on its own.
    "accrual_ratio": FactorDef(
        "accrual_ratio", _accrual_ratio, "low", "Accrual ratio (low best)",
        glossary=("The share of last year's reported profit that has not yet arrived "
                  "as cash. A high figure means the earnings rest on accounting "
                  "entries rather than money actually collected."),
        unit="percent",
        fallback_note="(net income − operating cash flow) / average total assets, "
                      "period-matched; falls back to single-year assets when the prior "
                      "year is missing (labelled); abstains on a missing input or a "
                      "non-positive asset base",
        source_fn=_accrual_ratio_source),
    "altman_z": FactorDef(
        "altman_z", _altman_z, "high", "Altman Z-Score",
        glossary=("A single number combining five measures of working capital, "
                  "accumulated profit, earnings, market value and sales into one "
                  "distress signal. Higher means further from financial trouble."),
        unit="score",
        fallback_note="1.2·WC/TA + 1.4·RE/TA + 3.3·EBIT/TA + 0.6·market cap/total "
                      "liabilities + 1.0·sales/TA, statement lines period-matched; "
                      "abstains on a missing input, a non-positive denominator, or a "
                      "cross-currency name (market cap vs statements, rule 8)",
        source_fn=_altman_z_source),
    # Piotroski F-Score (PIOTROSKI-1) — a rankable QUALITY leg. Registered-and-unused
    # until FORENSIC-1 gave it a consumer: it is forensic_v1's third leg. Shares its
    # nine checks with the min_f_score screen criterion
    # (tools/screening.piotroski_f_score).
    "piotroski_f_score": FactorDef(
        "piotroski_f_score", _piotroski_f_score, "high",
        "Piotroski F-Score (0-9)",
        glossary=("A nine-point checklist of basic financial health: profitability, "
                  "debt and efficiency each score a point when improving."), unit="score",
        fallback_note="nine annual-statement checks; abstains below 5 computable "
                      "checks; coarse integer -> large tied blocks on a small universe, "
                      "which is why forensic_v1 pairs it with two continuous legs"),
    # Absolute valuation band (VALBAND-1) — a rankable leg registered but selected by
    # NO strategy in this PR. direction "low": the 15th percentile of its own history is
    # cheap, the 92nd is near its own peak.
    "valuation_band_percentile": FactorDef(
        "valuation_band_percentile", _valuation_band_percentile, "low",
        "Valuation vs own 5y band (percentile, low best)",
        glossary=("Where today's valuation multiple sits within this company's own "
                  "five-year history. The 10th percentile is cheaper than 90% of that "
                  "period; the 90th dearer than 90% of it."), unit="score",
        fallback_note="monthly EV/EBIT over 5y (P/E fallback, labelled); abstains below "
                      "3y of computable history or without dated statements"),
}


# Factors derivable from PRICE CLOSES ALONE — these are the only ones a free-data
# backtest can compute POINT-IN-TIME (historical prices are available as-of; historical
# point-in-time FUNDAMENTALS are not). The backtest validates this sleeve honestly and
# FLAGS the rest as data-limited (see backtest.py).
PRICE_DERIVED_FACTORS: frozenset[str] = frozenset(
    {"momentum_6m", "momentum_12m", "low_volatility"})


def price_factors_from_closes(closes: list[float], names) -> dict[str, Optional[float]]:
    """Compute the PRICE-DERIVED factors among ``names`` from a close series — the
    point-in-time-safe sleeve for backtesting. Non-price factors map to None (they
    cannot be computed point-in-time from free data)."""
    out: dict[str, Optional[float]] = {}
    for name in names:
        if name == "momentum_6m":
            out[name] = total_return(closes, _TD_6M)
        elif name == "momentum_12m":
            out[name] = total_return(closes, _TD_12M)
        elif name == "low_volatility":
            out[name] = annualized_volatility(closes)
        else:
            out[name] = None
    return out


def is_unrateable(fi: "FactorInputs") -> bool:
    """No usable data at all — a delisted / all-404 ticker (PARA/WBA) that must NEVER
    be ranked or reach the council (a worst-rank SELL on it is a fake assessment, and
    the ghost also pads the bottom and skews every real name's quintile).

    Two real failure shapes on yfinance: (a) get_fundamentals RAISES -> fundamentals
    is None; (b) yfinance returns a NON-EMPTY-but-blank `info` -> the adapter builds a
    SHELL Fundamentals with every number None/empty. Both, combined with no usable
    price history, are UNRATEABLE. A name missing ONE input (has a market cap OR a
    price) is NOT unrateable — the abstention rule still ranks it."""
    if fi.last_close is not None or fi.return_12m is not None \
            or fi.annualized_volatility is not None:
        return False                                  # has usable price data -> rateable
    f = fi.fundamentals
    if f is None:
        return True
    # fundamentals present but a SHELL: no usable numbers at all.
    has_scalar = any(v is not None for v in (
        f.market_cap, f.pe_ratio, f.eps, f.dividend_per_share, f.free_cash_flow,
        f.payout_ratio, f.total_debt))
    has_series = bool(f.total_revenue or f.operating_income or f.ebit
                      or f.invested_capital)
    return not (has_scalar or has_series)


def is_sector_excluded(sector: Optional[str], exclude_sectors) -> bool:
    """Case-insensitive, CONFIRMED-ONLY sector exclusion. True only when ``sector``
    is PRESENT and matches an entry — a missing/None sector is NEVER excluded, so
    absent provider data can't silently drop a name (the rank engine's universe
    filter, e.g. Magic Formula dropping financials where ROIC is invalid)."""
    if not sector or not exclude_sectors:
        return False
    return sector.strip().lower() in {s.strip().lower() for s in exclude_sectors}


def is_sector_out_of_scope(sector: Optional[str], include_sectors) -> bool:
    """Case-insensitive, CONFIRMED-ONLY sector INCLUSION gate — the MIRROR of
    ``is_sector_excluded`` (FIN-1). True — the name is OUT OF SCOPE and must be gated —
    only when ``include_sectors`` is non-empty AND ``sector`` is PRESENT and NOT among
    them (financials_v1 admits only financials: P/B and ROE are their yardstick, EBIT/EV
    and ROIC are not). An empty include list scopes nothing (every name in scope). A
    missing/None sector is NEVER gated (same never-drop-on-unknown discipline as the
    exclusion gate): absent provider data can't silently drop a name."""
    if not include_sectors or not sector:
        return False
    return sector.strip().lower() not in {s.strip().lower() for s in include_sectors}


# Vendor quoteType -> normalized asset kind (ETF-1 ITEM 2). yfinance reports
# "EQUITY"/"ETF"/"MUTUALFUND"/"INDEX"/…; the gate reasons in lowercase kinds. An
# UNKNOWN quoteType maps to its own lowercased form (so a new vendor type gates
# honestly against an equity/etf lens rather than silently passing).
_QUOTE_TYPE_KIND = {
    "EQUITY": "equity",
    "ETF": "etf",
    "MUTUALFUND": "mutualfund",
    "INDEX": "index",
    "CURRENCY": "currency",
    "CRYPTOCURRENCY": "cryptocurrency",
}
# Display form for the gate message ("asset kind 'ETF' outside this strategy's scope").
_KIND_DISPLAY = {"equity": "Equity", "etf": "ETF", "mutualfund": "Mutual Fund",
                 "index": "Index", "currency": "Currency",
                 "cryptocurrency": "Cryptocurrency"}


def normalize_asset_kind(quote_type: Optional[str]) -> Optional[str]:
    """Vendor quoteType -> normalized lowercase asset kind, or None when absent."""
    if not quote_type or not quote_type.strip():
        return None
    q = quote_type.strip().upper()
    return _QUOTE_TYPE_KIND.get(q, q.lower())


def asset_kind_display(quote_type: Optional[str]) -> str:
    """The display form of a detected kind for the gate message (e.g. 'ETF')."""
    kind = normalize_asset_kind(quote_type)
    if kind is None:
        return ""
    return _KIND_DISPLAY.get(kind, kind.upper())


def is_asset_kind_out_of_scope(quote_type: Optional[str], asset_kinds) -> bool:
    """CONFIRMED-ONLY asset-kind gate (ETF-1 ITEM 2) — the wall between asset classes.

    True — the name is OUT OF SCOPE and must be gated — only when ``asset_kinds`` is
    non-empty AND the vendor ``quote_type`` is PRESENT and its normalized kind is NOT
    among them (an equity lens admits only ``equity``; an ETF lens only ``etf``). A
    missing/None quote_type is NEVER gated (same never-drop-on-unknown discipline as the
    sector gate — absent provider data can't silently drop a name). An empty asset_kinds
    scopes nothing (every kind in scope), so a strategy that omits it is unchanged.

    This must fire BEFORE any screen or factor path: the vendor serves look-through
    "fundamentals" for an index tracker happily, so an ETF leaking into a stock lens
    would produce quiet garbage rather than an honest exclusion."""
    if not asset_kinds or not quote_type:
        return False
    kind = normalize_asset_kind(quote_type)
    if kind is None:
        return False
    return kind not in {k.strip().lower() for k in asset_kinds}


def is_payout_uncovered(payout_ratio: Optional[float],
                        max_payout: Optional[float]) -> bool:
    """CONFIRMED-ONLY payout-coverage gate. True only when payout_ratio is PRESENT and
    EXCEEDS max_payout — a dividend the company can't afford is a coming cut, so it is
    DISQUALIFYING for a defensive income holding (the income-strategy analogue of the
    falling-knife guard). A missing/None payout (a non-dividend name has no payout to
    be uncovered) is NEVER excluded — same principle as the sector gate. No gate set
    (max_payout None) excludes nothing."""
    if max_payout is None or payout_ratio is None:
        return False
    return payout_ratio > max_payout


def compute_factor_outcomes(
    fi: FactorInputs, names) -> dict[str, tuple[Optional[float], str]]:
    """(value, source) per named factor for one ticker — the source-aware form. The
    source is the factor's own ``source_fn`` when it has one (disclosing which fallback
    path it took), else "computed"/"abstained" derived from the value (ITEM 1)."""
    out: dict[str, tuple[Optional[float], str]] = {}
    for name in names:
        fdef = FACTOR_REGISTRY.get(name)
        if fdef is None:
            raise KeyError(f"unknown factor '{name}'")
        value = fdef.fn(fi)
        if fdef.source_fn is not None:
            source = fdef.source_fn(fi)
        else:
            source = SRC_COMPUTED if value is not None else SRC_ABSTAINED
        out[name] = (value, source)
    return out


def compute_factors(fi: FactorInputs, names) -> dict[str, Optional[float]]:
    """The factor values for one ticker, for the named factors. Unknown names raise
    (the rank-strategy loader validates names up front)."""
    return {name: value for name, (value, _) in
            compute_factor_outcomes(fi, names).items()}


def _fetch_fx_rate(adapter, from_ccy: str, to_ccy: str, *, today: date
                   ) -> Optional[float]:
    """The latest FX rate (units of ``to_ccy`` per 1 ``from_ccy``) via the SAME adapter's
    price path — yfinance exposes it as the pair ticker ``<FROM><TO>=X`` (e.g. DKKUSD=X).
    Going through get_price_history means the rate is cached, frozen into the run record,
    and replayed offline exactly like every other input. None on any failure (VERIFY-2
    ITEM 1 -> the EV route abstains)."""
    from .data.adapter import TransientFetchError
    pair = f"{from_ccy}{to_ccy}=X"
    try:
        ph = adapter.get_price_history(pair, start=today - timedelta(days=10), end=today)
    except TransientFetchError:
        raise
    except Exception:
        return None
    closes = ph.closes if ph and ph.closes else []
    return closes[-1] if closes else None


def _gather_valuation_band(adapter, ticker: str, fundamentals, *, today: date
                           ) -> ValuationBand:
    """The absolute valuation band for one name (VALBAND-1), from its OWN 5-year price
    history + dated statements.

    Fetched SEPARATELY from the 400-day window the momentum/volatility factors read —
    deliberately, not lazily. ``annualized_volatility`` consumes the WHOLE close list,
    so widening the shared fetch to 5 years would silently change the low_volatility
    factor and therefore every existing strategy's ranking. Two windows, one cached
    provider call each, zero drift in the legs that already exist.

    Best-effort: it NEVER raises and NEVER aborts a name the ranking legs could rate (a
    TRANSIENT error is swallowed here too — an absolute-context column must not abort a
    name). But a failure no longer collapses to ``None``: ``None`` rendered as "—", which
    the report dropped when EVERY band was absent, so a REQUESTED band whose fetch
    failed produced a report byte-identical to one where the band was never requested — a
    silent-failure hole that cost two live debugging rounds (2026-08-22). Instead it
    returns an ABSTAINING band carrying the reason, so the section renders an honest
    "not evaluated — price history unavailable: <reason>", matching the insufficient-history
    abstention. The not-requested case never reaches here — the caller passes
    ``with_valuation_band=False`` and leaves the band ``None`` (no section), unchanged."""
    if fundamentals is None:
        return ValuationBand(note="fundamentals unavailable for this name")
    try:
        prices = adapter.get_price_history(
            ticker, start=today - timedelta(days=round(365.25 * BAND_YEARS) + 10),
            end=today)
    except Exception as exc:
        return ValuationBand(
            note=f"price history unavailable: {type(exc).__name__}: {exc}")
    bars = getattr(prices, "bars", None) or []
    if not bars:
        return ValuationBand(note="price history unavailable: no price bars returned")

    # VALBAND-2 — an ADR reports in one currency and trades in another, which used to
    # darken the band entirely. Fetch MONTHLY rates across the same window so every
    # historical month converts at its OWN rate; today's rate never touches history.
    # Best-effort like the rest of this function: no rates means the band abstains with
    # the pair named, exactly as it abstained before.
    fx = None
    price_ccy = getattr(fundamentals, "currency", None)
    acct_ccy = getattr(fundamentals, "financial_currency", None)
    if price_ccy and acct_ccy and price_ccy != acct_ccy:
        from .tools.fx import monthly_fx_series

        try:
            fx = monthly_fx_series(
                adapter, acct_ccy, price_ccy,
                start=today - timedelta(days=round(365.25 * BAND_YEARS) + 10),
                end=today)
        except Exception:
            fx = None
    return valuation_band(bars, fundamentals, asof=today, fx=fx)


def gather_factor_inputs(adapter, ticker: str, *, today: date,
                         static_rows=None,
                         with_valuation_band: bool = False) -> FactorInputs:
    """Fetch the deterministic inputs one ticker needs for factor ranking — the same
    adapter the council uses. Per-source DataUnavailable is swallowed (partial inputs
    -> NOT-EVAL factors), so one flaky name never aborts a universe ranking.

    ``static_rows`` is the committed ETF static layer (``{TICKER: StaticRow}``); it
    defaults to the loaded ``data/etf_static.csv`` and is injected explicitly in tests.
    It fills slow ETF fields the vendor doesn't serve for ETF-kind names ONLY (see
    ``etf_static.apply_static_fill``).

    A served ``fund_size`` is then normalised to EUR (DATA-HYGIENE-1) — converted at a
    dated rate, withheld when the rate is unavailable, or flagged when its base currency
    is unknown. See the ``FactorInputs.fund_size_fx*`` fields.

    ``with_valuation_band`` (VALBAND-1, default False) gates the ABSOLUTE valuation band:
    it is the ONLY opt-in leg here because it is the only one that costs a SECOND price
    fetch (its own 5-year window, separate from the 400-day factor window). Off by default
    so a normal ranking makes no extra call and its output is byte-identical to a
    pre-VALBAND run; the UI's "Valuation band" checkbox and Company Check pass True."""
    from .data.adapter import TransientFetchError

    fundamentals = None
    try:
        fundamentals = adapter.get_fundamentals(ticker)
    except TransientFetchError:
        raise                                         # a live name was throttled, not
                                                      # absent — abort THIS name (ITEM 5)
    except Exception:
        pass                                          # DataUnavailable OR a raw error
    closes: list[float] = []
    # PRICE-1: the SAME fetch, read twice. ``closes`` (adjusted) keeps feeding the
    # momentum/volatility legs and the screen byte-for-byte; ``bars`` is kept so the
    # share price and 52-week position can be read off the TRADED close without a second
    # call. The window is NOT touched — 400 days is what low_volatility consumes.
    bars: list = []
    price_fail = ""
    try:
        prices = adapter.get_price_history(
            ticker, start=today - timedelta(days=400), end=today)
        closes = prices.closes if prices and prices.closes else []
        bars = list(getattr(prices, "bars", None) or [])
    except TransientFetchError:
        raise                                         # transient -> fetch-error, not
                                                      # UNRATEABLE
    except Exception as exc:
        # a delisted name can raise a RAW yfinance error ("no timezone found")
        # rather than DataUnavailable — degrade to no-data, never crash the run. The
        # REASON is kept so the price line abstains visibly instead of silently.
        price_fail = f"price history unavailable: {type(exc).__name__}: {exc}"
    snap = technical_snapshot(closes) if closes else None

    # ETF static layer (ETF-STATIC-1): fill slow ETF fields the vendor doesn't serve from
    # the committed CSV — ETF-kind names only, vendor value wins where present & plausible,
    # stale entries abstain. A stock never reads it (kind != "etf" -> unchanged). The
    # committed file makes this replay byte-identically in a frozen run.
    static_fill = None
    if fundamentals is not None:
        rows = static_rows if static_rows is not None else default_static_rows()
        fundamentals, static_fill = apply_static_fill(
            fundamentals, kind=normalize_asset_kind(fundamentals.quote_type),
            row=rows.get(ticker), today=today)

    # fund_size base-currency normalisation (DATA-HYGIENE-1): the vendor reports an ETF's
    # total assets in the FUND'S base currency (IQQH's 4.17bn is USD though it lists on
    # XETRA in EUR), so a cross-fund ranking must be brought into ONE currency. The source
    # currency comes from the static row that served the value; the rate comes through the
    # SAME cached/frozen adapter price path as the accounts->price FX receipt.
    fund_size_fx = None
    fund_size_fx_failed = False
    fund_size_currency_unverified = False
    if fundamentals is not None and fundamentals.total_assets is not None:
        fund_ccy = normalize_currency_code(
            static_fill.fund_size_currency if static_fill is not None else None)
        if fund_ccy is None:
            # No known base currency: serve the amount exactly as before, but FLAG it —
            # never relabel an unknown currency as EUR (the pre-column static rows).
            fund_size_currency_unverified = True
        elif needs_conversion(fund_ccy):
            rate = _fetch_fx_rate(adapter, fund_ccy, FUND_SIZE_CCY, today=today)
            fund_size_fx = convert_fund_size(fundamentals.total_assets, fund_ccy, rate,
                                             today.isoformat())
            if fund_size_fx is not None:
                fundamentals = replace(fundamentals, total_assets=fund_size_fx.value)
            else:
                # Currency known, rate unavailable -> WITHHOLD (abstain), never mix.
                fundamentals = replace(fundamentals, total_assets=None)
                fund_size_fx_failed = True
        # else: already EUR — nothing to convert, the static receipt stands alone.

    # Currency-consistent EV (VERIFY-2 ITEM 1): if the accounts' currency differs from the
    # price currency, fetch the FX rate (same adapter/cache/freeze path). On a mismatch
    # with a failed fetch, mark fx_failed so the EV route abstains rather than mix. A
    # single-currency name (all graded universes are US) never triggers this — byte-
    # unchanged.
    fx = None
    fx_failed = False
    if fundamentals is not None:
        price_ccy = fundamentals.currency
        acct_ccy = fundamentals.financial_currency
        if price_ccy and acct_ccy and price_ccy != acct_ccy:
            rate = _fetch_fx_rate(adapter, acct_ccy, price_ccy, today=today)
            if rate is not None and rate > 0:
                fx = CurrencyConversion(rate=rate, from_ccy=acct_ccy, to_ccy=price_ccy,
                                        as_of=today.isoformat())
            else:
                fx_failed = True

    # Absolute valuation band (VALBAND-1) — display column + registered factor. Computed
    # AFTER the static/FX layers so it reads the same fundamentals every other leg does.
    # OPT-IN: skipped (and its extra 5-year fetch never made) unless the caller asks — off
    # by default so a normal ranking is byte-identical to a pre-VALBAND run.
    band = (_gather_valuation_band(adapter, ticker, fundamentals, today=today)
            if with_valuation_band else None)

    # Share price + 52-week position (PRICE-1) — ALWAYS ON. Free: it reads the bars
    # fetched above, in the price currency the provider reports, with no conversion and
    # no assumption when that currency is absent.
    ctx = price_context(
        bars, currency=getattr(fundamentals, "currency", None) if fundamentals else None)
    if not ctx.available and price_fail:
        ctx = replace(ctx, note=price_fail)          # name the fetch failure, don't hide it

    return FactorInputs(
        ticker=ticker, fundamentals=fundamentals,
        return_6m=total_return(closes, _TD_6M) if closes else None,
        return_12m=total_return(closes, _TD_12M) if closes else None,
        annualized_volatility=snap.annualized_volatility if snap else None,
        last_close=closes[-1] if closes else None, fx=fx, fx_failed=fx_failed,
        static=static_fill, fund_size_fx=fund_size_fx,
        fund_size_fx_failed=fund_size_fx_failed,
        fund_size_currency_unverified=fund_size_currency_unverified,
        valuation_band=band, price_context=ctx)


BORDERLINE_TOL = 0.05    # within 5% (relative) of the threshold


def is_borderline_fail(observed, threshold, tol: float = BORDERLINE_TOL) -> bool:
    """Is a CONFIRMED-fail observation within ``tol`` (relative) of its threshold?

    Legibility only — the floor is unchanged; a borderline fail is still a fail. An
    excluded observation always sits on the failing side of the threshold (a min_*
    below it, a max_* above it), so the symmetric relative gap
    ``|observed - threshold| / |threshold|`` is direction-agnostic AND correct for both
    (PFE's ROIC 0.1198 vs a 0.12 floor -> 0.17% -> borderline; a max_payout 0.87 vs
    0.85 -> 2.4% -> borderline; 0.106 vs 0.12 -> 11.7% -> not). Non-numeric observed or
    a zero threshold -> not borderline (no meaningful margin)."""
    if not isinstance(observed, (int, float)) or not isinstance(threshold, (int, float)):
        return False
    if threshold == 0:
        return False
    return abs(observed - threshold) / abs(threshold) <= tol


def screen_prefilter_fail(screen_criteria, fi: FactorInputs) -> Optional[str]:
    """Run a SCREEN's criteria on one name; return the NAMED reason for the first
    CONFIRMED FAIL (passed is False), or None if it passes or only ABSTAINS.

    A fail whose observed value is within ``BORDERLINE_TOL`` (5% relative) of its
    threshold is tagged ``[borderline]`` in the reason — a legibility flag that flows
    unchanged to every render site (CLI, Universe Run tab, snapshot notes). The floor
    is NOT relaxed: a borderline fail is still an exclusion.

    This is the screen-as-prefilter: for a strategy defined by HARD REQUIREMENTS (a
    defensive holding MUST have covered, real income and intact trend), the screen
    says WHO QUALIFIES and the ranking orders what's left — ranking-and-combining
    alone can't enforce a floor. SAFETY: a criterion that ABSTAINS (passed is None,
    e.g. missing data) does NOT exclude — abstention != failure, never drop on a data
    gap. (Requiring income IS the strategy's intent here, so a genuine non-payer
    failing min_dividend_yield is CORRECT — distinct from the growth-factor rule that
    never punishes a non-dividend name.)"""
    return screen_evaluate(screen_criteria, fi)[0]      # (reason, bases, abstentions, …)


# Display labels for a criterion's measurement basis (payout-on-FCF, through-cycle).
_BASIS_LABEL = {"fcf": "FCF (4y mean)", "eps": "EPS fallback"}


# Price-vs-fundamentals divergence flag (ITEM 2). A STATED convention, not fitted:
# a 12m run-up of +30% or more while a fundamental floor confirmed-fails is the
# cyclical-inflection / mania shape worth a human's eye. Documented in CALCULATIONS.md §4.
_DIVERGENCE_MOMENTUM_THRESHOLD = 0.30


def price_divergence_flag(fi: FactorInputs, screen_criteria) -> Optional[str]:
    """Return the price-divergence disclosure note for an excluded name, else None.

    Fires when ANY *fundamental* screen criterion is a CONFIRMED FAIL (``passed is
    False``) AND the name's trailing 12m price momentum is >= +0.30 — a price that has
    run up hard while the business floor it's screened on is failing (a cyclical
    inflection, or a mania). The note is::

        [⚠ price diverging: +35% 12m — cyclical inflection or mania; human review]

    and carries the ACTUAL momentum value. It NEVER alters a verdict or an exclusion —
    it only annotates the reason so the reader sees the disagreement. Two disciplines:
    ABSTENTION is not a fail (``passed is None`` doesn't count — rule 3), and the
    price-momentum criterion itself is excluded from 'fundamental' (a price criterion
    can't be the price-divergence tell)."""
    mom = fi.return_12m
    if mom is None or mom < _DIVERGENCE_MOMENTUM_THRESHOLD:
        return None
    if fi.fundamentals is None:
        return None
    from .tools.criteria.registry import (
        Evidence, PRICE_MOMENTUM_CRITERION, run_screen)
    ev = Evidence(fundamentals=fi.fundamentals, last_close=fi.last_close,
                  return_6m=fi.return_6m, return_12m=fi.return_12m, dividends=[],
                  valuation_band=fi.valuation_band)
    res = run_screen(screen_criteria, ev, ticker=fi.ticker)
    if not any(c.passed is False and c.name != PRICE_MOMENTUM_CRITERION
               for c in res.criteria):
        return None
    return (f"[⚠ price diverging: {mom:+.0%} 12m — cyclical inflection or mania; "
            f"human review]")


def screen_evaluate(screen_criteria, fi: FactorInputs):
    """Run a screen ONCE and return ``(first_confirmed_fail_reason | None, bases,
    abstentions, outcomes)``:
    - ``bases`` maps each criterion reporting a measurement basis to it (e.g.
      ``{"max_payout_ratio_fcf": "fcf"}``, incl. ``"abstained"``);
    - ``abstentions`` maps a criterion that ABSTAINED on a per-name data condition
      (basis == "abstained") to its note — a PASSED name whose dividend-safety check
      could not be evaluated is legitimate (abstention never excludes) but must be
      VISIBLE (ITEM 3). The fail reason NAMES the basis and carries the borderline tag.
    - ``outcomes`` (REPORT-1) is the PER-CRITERION record for THIS name:
      ``{criterion: {"passed": True|False|None, "observed": …, "threshold": …,
      "note": …, "basis": …, "borderline": bool}}``. It is what lets a report say what
      EVERY rule did — the old output could only ever name the first rule a name failed,
      so a rule nothing failed was invisible and a reader could not tell what had been
      applied. Display data only: the fail REASON string below is byte-unchanged,
      because the scoreboard and the verdict publisher parse it.
    All four read from the SAME single evaluation — no criterion runs twice."""
    from .tools.criteria.registry import Evidence, run_screen
    if fi.fundamentals is None:
        return None, {}, {}, {}
    ev = Evidence(fundamentals=fi.fundamentals, last_close=fi.last_close,
                  return_6m=fi.return_6m, return_12m=fi.return_12m, dividends=[],
                  valuation_band=fi.valuation_band)
    reason = None
    bases: dict[str, str] = {}
    abstentions: dict[str, str] = {}
    outcomes: dict[str, dict] = {}
    for c in run_screen(screen_criteria, ev, ticker=fi.ticker).criteria:
        basis = getattr(c, "basis", "") or ""
        borderline = is_borderline_fail(c.observed, c.threshold)
        outcomes[c.name] = {"passed": c.passed, "observed": c.observed,
                            "threshold": c.threshold, "note": c.note or "",
                            "basis": basis, "borderline": borderline}
        if basis:
            bases[c.name] = basis
        if basis == "abstained" and c.passed is None:
            abstentions[c.name] = c.note
        if reason is None and c.passed is False:      # first confirmed fail (None abstains)
            obs = (f"{c.observed:.4g}" if isinstance(c.observed, (int, float))
                   else "n/a")
            basis_tag = f" [{_BASIS_LABEL.get(basis, basis)}]" if basis else ""
            border = " [borderline]" if borderline else ""
            reason = (f"screen: {c.name} (observed {obs} vs threshold "
                      f"{c.threshold}){basis_tag}{border}")
    return reason, bases, abstentions, outcomes
