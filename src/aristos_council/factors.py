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

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Optional

from .data.adapter import DataUnavailable, Fundamentals
from .tools.screening import revenue_cagr, through_cycle_roic
from .tools.technical import (
    _TD_6M,
    _TD_12M,
    annualized_volatility,
    technical_snapshot,
    total_return,
)

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


def _net_payout_yield(fi: FactorInputs) -> Optional[float]:
    """Net payout = dividends + buybacks / market cap. Buyback data isn't on free
    fundamentals, so this falls back to DIVIDEND YIELD (an under-count for big
    repurchasers — documented). Higher is better."""
    f = fi.fundamentals
    return f.dividend_yield if f is not None else None


def _net_payout_source(fi: FactorInputs) -> str:
    f = fi.fundamentals
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


@dataclass(frozen=True)
class FactorDef:
    name: str
    fn: Callable[[FactorInputs], Optional[float]]
    direction: str        # "high" = higher is better, "low" = lower is better
    label: str
    fallback_note: str = ""
    # Optional per-name SOURCE tag (ITEM 1). None -> the source is derived generically
    # as "computed"/"abstained" from the value; set it for factors WITH fallbacks so the
    # report discloses which path was taken per ticker.
    source_fn: Optional[Callable[[FactorInputs], str]] = None


FACTOR_REGISTRY: dict[str, FactorDef] = {
    "earnings_yield": FactorDef(
        "earnings_yield", _earnings_yield, "high", "Earnings yield (EBIT/EV)",
        "EBIT / (market cap + total debt − cash); EBIT/market_cap fallback when EV "
        "components missing, then 1/PE; net-cash (EV≤0) abstains",
        source_fn=_earnings_yield_source),
    "roic": FactorDef(
        "roic", _return_on_capital, "high", "Return on invested capital"),
    "momentum_12m": FactorDef(
        "momentum_12m", _momentum_12m, "high", "12-month price momentum"),
    "momentum_6m": FactorDef(
        "momentum_6m", _momentum_6m, "high", "6-month price momentum"),
    "low_volatility": FactorDef(
        "low_volatility", _low_volatility, "low", "Annualized volatility (low best)"),
    "net_payout_yield": FactorDef(
        "net_payout_yield", _net_payout_yield, "high", "Net payout yield",
        "dividend-yield fallback (buybacks unavailable on free fundamentals)",
        source_fn=_net_payout_source),
    "revenue_growth": FactorDef(
        "revenue_growth", _revenue_growth, "high", "Revenue CAGR (3y)"),
    "dividend_streak": FactorDef(
        "dividend_streak", _dividend_streak, "high", "Dividend-growth streak (years)"),
    # Financials lens (FIN-1): the measures banks & insurers are actually priced by,
    # since EBIT/EV and ROIC are not computable for them (the Greenblatt exclusion,
    # inverted). Vendor value primary, derived fallback, abstain on non-positive book.
    "price_to_book": FactorDef(
        "price_to_book", _price_to_book, "low", "Price / book (low best)",
        "vendor priceToBook; fallback market_cap / closing equity; abstains on book ≤ 0"),
    "return_on_equity": FactorDef(
        "return_on_equity", _return_on_equity, "high", "Return on equity",
        "vendor returnOnEquity (TTM); fallback net_income / mean(opening+closing equity); "
        "abstains on equity ≤ 0"),
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


def gather_factor_inputs(adapter, ticker: str, *, today: date) -> FactorInputs:
    """Fetch the deterministic inputs one ticker needs for factor ranking — the same
    adapter the council uses. Per-source DataUnavailable is swallowed (partial inputs
    -> NOT-EVAL factors), so one flaky name never aborts a universe ranking."""
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
    try:
        prices = adapter.get_price_history(
            ticker, start=today - timedelta(days=400), end=today)
        closes = prices.closes if prices and prices.closes else []
    except TransientFetchError:
        raise                                         # transient -> fetch-error, not
                                                      # UNRATEABLE
    except Exception:
        pass    # a delisted name can raise a RAW yfinance error ("no timezone found")
                # rather than DataUnavailable — degrade to no-data, never crash the run
    snap = technical_snapshot(closes) if closes else None

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

    return FactorInputs(
        ticker=ticker, fundamentals=fundamentals,
        return_6m=total_return(closes, _TD_6M) if closes else None,
        return_12m=total_return(closes, _TD_12M) if closes else None,
        annualized_volatility=snap.annualized_volatility if snap else None,
        last_close=closes[-1] if closes else None, fx=fx, fx_failed=fx_failed)


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
    return screen_evaluate(screen_criteria, fi)[0]


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
                  return_6m=fi.return_6m, return_12m=fi.return_12m, dividends=[])
    res = run_screen(screen_criteria, ev, ticker=fi.ticker)
    if not any(c.passed is False and c.name != PRICE_MOMENTUM_CRITERION
               for c in res.criteria):
        return None
    return (f"[⚠ price diverging: {mom:+.0%} 12m — cyclical inflection or mania; "
            f"human review]")


def screen_evaluate(screen_criteria, fi: FactorInputs):
    """Run a screen ONCE and return ``(first_confirmed_fail_reason | None, bases,
    abstentions)``:
    - ``bases`` maps each criterion reporting a measurement basis to it (e.g.
      ``{"max_payout_ratio_fcf": "fcf"}``, incl. ``"abstained"``);
    - ``abstentions`` maps a criterion that ABSTAINED on a per-name data condition
      (basis == "abstained") to its note — a PASSED name whose dividend-safety check
      could not be evaluated is legitimate (abstention never excludes) but must be
      VISIBLE (ITEM 3). The fail reason NAMES the basis and carries the borderline tag.
    All three read from the SAME single evaluation."""
    from .tools.criteria.registry import Evidence, run_screen
    if fi.fundamentals is None:
        return None, {}, {}
    ev = Evidence(fundamentals=fi.fundamentals, last_close=fi.last_close,
                  return_6m=fi.return_6m, return_12m=fi.return_12m, dividends=[])
    reason = None
    bases: dict[str, str] = {}
    abstentions: dict[str, str] = {}
    for c in run_screen(screen_criteria, ev, ticker=fi.ticker).criteria:
        basis = getattr(c, "basis", "") or ""
        if basis:
            bases[c.name] = basis
        if basis == "abstained" and c.passed is None:
            abstentions[c.name] = c.note
        if reason is None and c.passed is False:      # first confirmed fail (None abstains)
            obs = (f"{c.observed:.4g}" if isinstance(c.observed, (int, float))
                   else "n/a")
            basis_tag = f" [{_BASIS_LABEL.get(basis, basis)}]" if basis else ""
            border = " [borderline]" if is_borderline_fail(c.observed, c.threshold) else ""
            reason = (f"screen: {c.name} (observed {obs} vs threshold "
                      f"{c.threshold}){basis_tag}{border}")
    return reason, bases, abstentions
