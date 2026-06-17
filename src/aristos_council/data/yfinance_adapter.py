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
)


class YFinanceAdapter(MarketDataAdapter):
    name = "yfinance"

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

        return Fundamentals(
            ticker=ticker,
            name=info.get("longName") or info.get("shortName"),
            market_cap=_as_float(info.get("marketCap")),
            # Currencies drive honest abstention on USD-denominated thresholds
            # (a non-USD listing makes min_market_cap meaningless). Strings, not
            # floats; normalize empty/missing to None.
            currency=(info.get("currency") or None),
            financial_currency=(info.get("financialCurrency") or None),
            dividend_yield=_as_float(info.get("dividendYield")),
            dividend_per_share=_dividend_per_share(info),
            payout_ratio=_as_float(info.get("payoutRatio")),
            eps=_as_float(info.get("trailingEps")),
            pe_ratio=_as_float(info.get("trailingPE")),
            free_cash_flow=_as_float(info.get("freeCashflow")),
            # yfinance does not expose consecutive-growth-years. Left None on
            # purpose; the screen estimates it from dividend history and flags.
            years_dividend_growth=None,
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


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    # yfinance uses NaN for missing cells; treat NaN as absent.
    return None if f != f else f


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
