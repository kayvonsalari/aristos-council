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

        try:
            info = yf.Ticker(ticker).info
        except Exception as exc:
            raise DataUnavailable(
                f"yfinance fundamentals failed for {ticker}: {exc}"
            ) from exc

        if not info:
            raise DataUnavailable(f"No fundamentals for {ticker}")

        return Fundamentals(
            ticker=ticker,
            name=info.get("longName") or info.get("shortName"),
            market_cap=_as_float(info.get("marketCap")),
            dividend_yield=_as_float(info.get("dividendYield")),
            dividend_per_share=_as_float(info.get("dividendRate")),
            payout_ratio=_as_float(info.get("payoutRatio")),
            eps=_as_float(info.get("trailingEps")),
            pe_ratio=_as_float(info.get("trailingPE")),
            free_cash_flow=_as_float(info.get("freeCashflow")),
            # yfinance does not expose consecutive-growth-years. Left None on
            # purpose; the screen estimates it from dividend history and flags.
            years_dividend_growth=None,
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


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
