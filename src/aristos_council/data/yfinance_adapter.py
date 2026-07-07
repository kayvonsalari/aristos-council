"""yfinance implementation of MarketDataAdapter (Phase 1 dev provider).

Honesty notes baked into the code
---------------------------------
yfinance is fine for development but has real gaps the council must not paper
over:

1. `years_dividend_growth` is NOT reliably available from yfinance. We compute a
   best-effort estimate from the dividend series and flag it. The dividend-
   aristocrat screen's headline criterion (25 consecutive years of increases)
   therefore CANNOT be fully verified on yfinance alone — that's an honest
   Phase 1 limitation, resolved when EODHD (with longer, cleaner dividend
   history) comes online. The screening tool surfaces this as a caveat rather
   than asserting a number it can't stand behind.

2. yfinance field names drift between versions. All mapping is localized here so
   a breakage is contained to this file.

3. yfinance raises a grab-bag of exceptions and sometimes returns empty frames
   for valid-looking tickers. We translate all of that into DataUnavailable.
"""

from __future__ import annotations

from datetime import date

from .adapter import (
    DataUnavailable,
    DividendEvent,
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
    StreetConsensus,
    sane_dividend_yield,
)


class YFinanceAdapter(MarketDataAdapter):
    name = "yfinance"
    # Explicit (== the base default): yfinance's quarterly history carries ex-date
    # timing noise, so the per-payment MEDIAN method is the correct one.
    dividend_streak_method = "per_payment_median"

    def __init__(self) -> None:
        try:
            import yfinance  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise DataUnavailable(
                "yfinance is not installed; `pip install yfinance`"
            ) from exc

    # ------------------------------------------------------------------ #
    def get_price_history(
        self, ticker: str, *, start: date, end: date
    ) -> PriceHistory:
        import yfinance as yf

        try:
            df = yf.Ticker(ticker).history(
                start=start.isoformat(),
                end=end.isoformat(),
                auto_adjust=False,
            )
        except Exception as exc:  # yfinance throws many types
            raise DataUnavailable(
                f"yfinance price history failed for {ticker}: {exc}"
            ) from exc

        if df is None or df.empty:
            raise DataUnavailable(f"No price history for {ticker} in range")

        # Drop incomplete bars. yfinance includes the CURRENT day's row during
        # (pre-)market hours with NaN prices; a NaN close then silently poisons
        # every downstream average. (Live-run regression, 2026-06-11: the
        # entire technical snapshot came back NaN and the Technical specialist
        # had to abstain.)
        df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
        if df.empty:
            raise DataUnavailable(
                f"Price history for {ticker} contained only incomplete bars"
            )

        bars: list[PriceBar] = []
        for idx, row in df.iterrows():
            bars.append(
                PriceBar(
                    day=idx.date(),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    adj_close=float(row.get("Adj Close", row["Close"])),
                    volume=int(row["Volume"]),
                )
            )
        return PriceHistory(ticker=ticker, bars=bars)

    # ------------------------------------------------------------------ #
    def get_fundamentals(self, ticker: str) -> Fundamentals:
        import yfinance as yf

        tk = yf.Ticker(ticker)
        try:
            info = tk.info
        except Exception as exc:
            raise DataUnavailable(
                f"yfinance fundamentals failed for {ticker}: {exc}"
            ) from exc

        if not info:
            raise DataUnavailable(f"No fundamentals for {ticker}")

        # Annual statements are best-effort: absence/parse failure -> empty
        # series -> the growth criteria return NOT-EVAL, never a crash. A
        # missing statement must NOT fail the whole fundamentals fetch.
        try:
            income = tk.financials
        except Exception:
            income = None
        try:
            balance = tk.balance_sheet
        except Exception:
            balance = None
        # Cash-flow statement for the FCF-basis payout criterion (best-effort, like the
        # others): absence -> None fields -> the criterion falls back to the EPS basis.
        try:
            cashflow = tk.cashflow
        except Exception:
            cashflow = None

        # Recovered DPS, reused for the payout derivation below (same source the
        # screen sees), so dividend yield AND payout survive the summaryDetail gap.
        dps = _dividend_per_share(info)
        # Dividend-growth streak + last cut, DERIVED from the payment history (a
        # yield-trap separator that's free but yfinance never surfaces as a scalar).
        streak_years, last_cut = _dividend_streak_from_ticker(tk)
        return Fundamentals(
            ticker=ticker,
            name=info.get("longName") or info.get("shortName"),
            company_name=(info.get("longName") or None),   # display label (None-guarded)
            market_cap=_as_float(info.get("marketCap")),
            sector=(info.get("sector") or None),   # rank-engine sector exclusions
            # Currencies drive honest abstention on USD-denominated thresholds
            # (a non-USD listing makes min_market_cap meaningless). Strings, not
            # floats; normalize empty/missing to None.
            currency=(info.get("currency") or None),
            financial_currency=(info.get("financialCurrency") or None),
            dividend_yield=_dividend_yield(info),   # normalised to DECIMAL
            dividend_per_share=dps,
            payout_ratio=_payout_ratio(info, dps),
            eps=_as_float(info.get("trailingEps")),
            pe_ratio=_as_float(info.get("trailingPE")),
            free_cash_flow=_as_float(info.get("freeCashflow")),
            # yfinance does not expose consecutive-growth-years. Left None on
            # purpose; the screen estimates it from dividend history and flags.
            years_dividend_growth=None,
            # Defensive-risk signals (free-data yield-trap separators).
            dividend_streak_years=streak_years,
            last_dividend_reduction_year=last_cut,
            total_debt=_as_float(info.get("totalDebt")),
            debt_to_equity=_as_float(info.get("debtToEquity")),
            total_cash=_as_float(info.get("totalCash")),   # EV = mcap + debt − cash
            # Cash-flow-statement lines for the FCF payout basis (newest column). Cash
            # dividends paid is a NEGATIVE outflow -> stored as its absolute value.
            dividends_paid=_abs_or_none(_latest_cashflow(
                cashflow, "Cash Dividends Paid", "Common Stock Dividend Paid")),
            operating_cash_flow=_latest_cashflow(
                cashflow, "Operating Cash Flow", "Total Cash From Operating Activities"),
            capital_expenditure=_latest_cashflow(
                cashflow, "Capital Expenditure", "Capital Expenditures"),
            # Newest-first annual series for the THROUGH-CYCLE mean FCF denominator.
            free_cash_flow_annual=_cashflow_series(cashflow, "Free Cash Flow"),
            operating_cash_flow_annual=_cashflow_series(
                cashflow, "Operating Cash Flow", "Total Cash From Operating Activities"),
            capital_expenditure_annual=_cashflow_series(
                cashflow, "Capital Expenditure", "Capital Expenditures"),
            # Annual series, newest-first, NaN/empty dropped (Sprint 4B).
            total_revenue=_annual_series(income, "Total Revenue"),
            operating_income=_annual_series(income, "Operating Income"),
            ebit=_annual_series(income, "EBIT"),
            tax_provision=_annual_series(income, "Tax Provision"),
            pretax_income=_annual_series(income, "Pretax Income"),
            invested_capital=_annual_series(balance, "Invested Capital"),
        )

    # ------------------------------------------------------------------ #
    def get_dividend_history(
        self, ticker: str, *, start: date, end: date
    ) -> list[DividendEvent]:
        import yfinance as yf

        try:
            series = yf.Ticker(ticker).dividends
        except Exception as exc:
            raise DataUnavailable(
                f"yfinance dividends failed for {ticker}: {exc}"
            ) from exc

        if series is None or series.empty:
            return []

        events: list[DividendEvent] = []
        for ts, amount in series.items():
            d = ts.date()
            if start <= d <= end:
                events.append(DividendEvent(ex_date=d, amount=float(amount)))
        return events

    # ------------------------------------------------------------------ #
    def get_street_consensus(self, ticker: str) -> StreetConsensus:
        """Sell-side consensus from yfinance ``info``. Any missing field -> None
        (abstain-not-guess); a failed/empty ``info`` -> an all-null consensus, never
        an exception — the scoreboard records the abstention, it does not crash."""
        import yfinance as yf

        try:
            info = yf.Ticker(ticker).info
        except Exception:
            return StreetConsensus(ticker=ticker)
        if not info:
            return StreetConsensus(ticker=ticker)
        return StreetConsensus(
            ticker=ticker,
            recommendation_mean=_as_float(info.get("recommendationMean")),
            n_analysts=_as_int(info.get("numberOfAnalystOpinions")),
            target_mean_price=_as_float(info.get("targetMeanPrice")),
            current_price=_as_float(info.get("currentPrice")
                                    or info.get("regularMarketPrice")),
        )


def _dividend_per_share(info: dict) -> float | None:
    """Annual dividend per share, resilient to yfinance's flaky `info`.

    The forward `dividendRate` lives in the summaryDetail block, which yfinance
    frequently returns EMPTY for genuine payers (PG/JNJ/MO/T/MMM observed as
    None in a single call while KO/MSFT/ASML came back populated). When it's
    missing, fall back to `trailingAnnualDividendRate`, which the same calls
    populated for all five. A true non-payer's trailing rate is an explicit 0
    (INTC, post-suspension), preserved as 0.0 — a real determination, distinct
    from None (no figure at all, which the screen treats as NOT-EVAL, not FAIL).
    """
    forward = _as_float(info.get("dividendRate"))
    if forward is not None:
        return forward
    return _as_float(info.get("trailingAnnualDividendRate"))


def _dividend_streak_from_ticker(tk) -> tuple[int | None, int | None]:
    """(streak_years, last_reduction_year) from a yfinance Ticker's dividend history.

    Reads ``tk.dividends`` (already split-adjusted), sums per calendar year, and
    delegates to ``screening.dividend_streak`` (which excludes the current partial
    year and distinguishes FLAT from a CUT). Any failure/empty history -> (None,
    None), a clean abstain — this must never fail the whole fundamentals fetch."""
    from ..tools.screening import dividend_streak
    try:
        divs = tk.dividends
        if divs is None or len(divs) == 0:
            return None, None
        annual: dict[int, float] = {}
        for ts, amt in divs.items():
            annual[ts.year] = annual.get(ts.year, 0.0) + float(amt)
    except Exception:
        return None, None
    return dividend_streak(annual, date.today().year)


def _dividend_yield(info: dict) -> float | None:
    """Dividend yield as a DECIMAL (0.0289 == 2.89%), regardless of yfinance's shifting
    units. yfinance's ``dividendYield`` is now a PERCENT NUMBER (2.89 == 2.89%), while
    ``trailingAnnualDividendYield`` has stayed a DECIMAL (0.0289). PREFER the decimal
    field; fall back to the percent field / 100. The >100% backstop then catches any
    further drift. (Do NOT read the raw ``dividendYield`` untouched — that was the 100x
    bug: 2.89 compared as if it were 289%.)"""
    trailing = _as_float(info.get("trailingAnnualDividendYield"))
    if trailing is not None:
        return sane_dividend_yield(trailing)
    pct = _as_float(info.get("dividendYield"))
    if pct is not None:
        return sane_dividend_yield(pct / 100.0)   # percent -> decimal
    return None


def _payout_ratio(info: dict, dividend_per_share: float | None) -> float | None:
    """Payout ratio, resilient to yfinance's flaky `info`.

    The provider `payoutRatio` sits in the SAME summaryDetail block that drops
    `dividendRate` (None for PG/JNJ/MO/T/MMM in one call), so when it's missing
    we DERIVE it as dividend_per_share / trailingEps — the same recovered DPS the
    screen uses, over the populated trailing EPS. Honest NOT-EVAL (None) only on
    a true gap: no DPS, or non-positive EPS (negative/zero earnings -> the ratio
    is undefined/meaningless, so we abstain rather than fabricate). A genuine
    non-payer (DPS 0) derives to 0.0, but the payout criterion already short-
    circuits dps<=0 to NOT-EVAL, so that value is never the deciding figure.
    """
    provider = _as_float(info.get("payoutRatio"))
    if provider is not None:
        return provider
    eps = _as_float(info.get("trailingEps"))
    if dividend_per_share is None or eps is None or eps <= 0:
        return None
    return dividend_per_share / eps


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    # yfinance uses NaN for missing cells; treat NaN as absent.
    return None if f != f else f


def _as_int(value: object) -> int | None:
    f = _as_float(value)
    return None if f is None else int(f)


def _latest_cashflow(df: object, *labels: str) -> float | None:
    """The newest cash-flow-statement value for the first matching row label (yfinance
    renames these across versions, hence the aliases). None if the frame or every label
    is absent."""
    for label in labels:
        series = _annual_series(df, label)
        if series:
            return series[0]                          # _annual_series is newest-first
    return None


def _abs_or_none(v: float | None) -> float | None:
    return None if v is None else abs(v)


def _cashflow_series(df: object, *labels: str) -> list[float]:
    """Newest-first annual series for the first matching cash-flow row label."""
    for label in labels:
        series = _annual_series(df, label)
        if series:
            return series
    return []


def _annual_series(df: object, label: str) -> list[float]:
    """A NEWEST-FIRST list of clean annual values for one statement row.

    yfinance income-statement / balance-sheet frames are indexed by line-item
    label with columns per fiscal year. We pull the row, order columns
    newest-first, coerce to float, and DROP NaN cells (which also drops the
    trailing all-NaN column yfinance sometimes appends). Missing frame or label
    -> empty list. Pure and provider-shaped, so it's unit-testable without a
    network: pass any DataFrame-like with `.empty`, `.index`, `.loc`.
    """
    try:
        if df is None or df.empty or label not in df.index:
            return []
        row = df.loc[label]
        # Be defensive about column order: sort by column key (fiscal date)
        # descending so the series is newest-first regardless of provider order.
        try:
            row = row.sort_index(ascending=False)
        except Exception:
            pass
        out: list[float] = []
        for v in row.tolist():
            f = _as_float(v)
            if f is not None:
                out.append(f)
        return out
    except Exception:
        # Never let a statement-shape surprise break the fundamentals fetch.
        return []
