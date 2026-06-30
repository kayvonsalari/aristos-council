"""EODHD implementation of MarketDataAdapter (Phase 2 provider).

Status: get_dividend_history and get_fundamentals are IMPLEMENTED. The dividend
history is the reason EODHD exists — its long, clean, ADJUSTED series is what
makes the multi-decade aristocrat streak verifiable (yfinance can't). Fundamentals
map onto the same provider-neutral DTO, carrying currency through verbatim (no FX)
so USD-threshold criteria abstain honestly on non-USD listings. get_price_history
remains NotImplementedError — deferred rather than half-faked.

Dividend endpoint (confirmed live)
----------------------------------
    GET https://eodhd.com/api/div/{SYMBOL}?api_token={KEY}&fmt=json
Returns a JSON array, oldest-first, each row:
    {date (ex-date, YYYY-MM-DD), value (ADJUSTED), unadjustedValue (raw),
     currency, period ("Final"/"Interim"/null), ...}
Symbol format carries the exchange suffix: US = KO.US, Swiss = NESN.SW,
Korea = 000660.KS (no trailing dot — normalize_ticker enforces that).

Why the ADJUSTED ``value`` (not ``unadjustedValue``)
----------------------------------------------------
Raw values jump at splits (Nestlé 2002: value 0.64 vs unadjustedValue 6.40) and
would manufacture false streak breaks. The adjusted ``value`` is continuous
through splits, so it is the only correct input to the year-over-year streak.

Key handling: read from EODHD_API_KEY env var (or constructor); never hard-code.
HTTP errors / empty arrays map to DataUnavailable — never a silent zero.

Uses urllib from the stdlib (same choice as finnhub_adapter): one GET does not
justify another dependency.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime

from .adapter import (
    DataUnavailable,
    DividendEvent,
    Fundamentals,
    MarketDataAdapter,
    PriceHistory,
    normalize_ticker,
)

_BASE_URL = "https://eodhd.com/api"

_NOT_READY = (
    "EODHDAdapter implements get_dividend_history and get_fundamentals; "
    "get_price_history is deferred to the next step."
)


def _parse_ex_date(raw: object) -> date | None:
    """Parse an EODHD ``date`` (YYYY-MM-DD) to a date; None if absent/malformed."""
    if not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _adjusted_amount(row: dict) -> float | None:
    """The ADJUSTED dividend amount from a row's ``value`` field.

    Deliberately reads ``value`` (split-adjusted), NEVER ``unadjustedValue``.
    Returns None for a missing/unparseable/non-positive amount so the caller can
    skip it rather than fabricate a zero (a phantom cut).
    """
    if not isinstance(row, dict):
        return None
    try:
        amount = float(row.get("value"))
    except (TypeError, ValueError):
        return None
    if amount != amount or amount <= 0:   # NaN or non-positive -> not a real payment
        return None
    return amount


def _coerce_float(value: object) -> float | None:
    """EODHD returns numbers as strings, numbers, or null. Coerce to float;
    None on missing/unparseable/NaN so Fundamentals fields stay Optional."""
    if value is None:
        return None
    try:
        f = float(value)   # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _annual_series(yearly: object, key: str) -> list[float]:
    """A NEWEST-FIRST list of one statement line across the yearly columns.

    EODHD's ``Financials::*::yearly`` is a dict keyed by fiscal-period date
    (``"2023-09-30"``). We sort the date keys DESCENDING so the series is
    newest-first — matching the yfinance adapter's ``_annual_series`` ordering
    exactly (ROIC / revenue-CAGR depend on revenue[0] being the latest year) —
    coerce each value to float, and drop missing cells.
    """
    if not isinstance(yearly, dict):
        return []
    out: list[float] = []
    for period in sorted(yearly.keys(), reverse=True):   # newest-first
        row = yearly.get(period) or {}
        v = _coerce_float(row.get(key)) if isinstance(row, dict) else None
        if v is not None:
            out.append(v)
    return out


def fundamentals_from_payload(ticker: str, data: dict) -> Fundamentals:
    """Map an EODHD ``/fundamentals`` payload onto the Fundamentals DTO.

    Pure (no network) so tests drive it from a recorded fixture. Missing fields
    stay None / empty (Optional-by-convention). CURRENCY HONESTY: ``currency`` and
    ``financial_currency`` are carried through verbatim and NEVER converted — a
    non-USD listing must let the USD-denominated ``min_market_cap`` criterion
    abstain (NOT-EVAL), exactly as the SK Hynix run required.
    """
    general = data.get("General") or {}
    highlights = data.get("Highlights") or {}
    financials = data.get("Financials") or {}
    income = financials.get("Income_Statement") or {}
    balance = financials.get("Balance_Sheet") or {}
    cashflow = financials.get("Cash_Flow") or {}
    income_yearly = income.get("yearly") or {}
    balance_yearly = balance.get("yearly") or {}
    cashflow_yearly = cashflow.get("yearly") or {}

    # free_cash_flow is a single (latest) scalar in the DTO, mirroring yfinance's
    # info["freeCashflow"]; take the newest yearly value.
    fcf_series = _annual_series(cashflow_yearly, "freeCashFlow")

    return Fundamentals(
        ticker=ticker,
        name=(general.get("Name") or None),
        market_cap=_coerce_float(highlights.get("MarketCapitalization")),
        sector=(general.get("Sector") or None),   # rank-engine sector exclusions
        # Listing/price currency (General) and statements currency (Income stmt).
        currency=(general.get("CurrencyCode") or None),
        financial_currency=(income.get("currency_symbol")
                            or general.get("CurrencyCode") or None),
        dividend_yield=_coerce_float(highlights.get("DividendYield")),
        dividend_per_share=_coerce_float(highlights.get("DividendShare")),
        payout_ratio=_coerce_float(highlights.get("PayoutRatio")),
        eps=_coerce_float(highlights.get("EarningsShare")),
        pe_ratio=_coerce_float(highlights.get("PERatio")),
        free_cash_flow=(fcf_series[0] if fcf_series else None),
        # The streak is computed from the dividend series, not this field.
        years_dividend_growth=None,
        # Annual series, NEWEST-FIRST (matches yfinance adapter ordering).
        total_revenue=_annual_series(income_yearly, "totalRevenue"),
        operating_income=_annual_series(income_yearly, "operatingIncome"),
        ebit=_annual_series(income_yearly, "ebit"),
        tax_provision=_annual_series(income_yearly, "incomeTaxExpense"),
        pretax_income=_annual_series(income_yearly, "incomeBeforeTax"),
        invested_capital=_annual_series(balance_yearly, "netInvestedCapital"),
    )


def dividend_events_from_rows(
    rows: object, *, start: date, end: date
) -> list[DividendEvent]:
    """Pure parser: EODHD div JSON rows -> normalized DividendEvents.

    Reads the ADJUSTED ``value``, keeps events within [start, end], and returns
    them OLDEST-FIRST (the screen's streak counter expects ascending order; we do
    not trust the provider's order and sort explicitly). Malformed rows are
    skipped, not fatal. Factored out of the HTTP call so tests drive it with
    recorded fixture JSON and never touch the network.
    """
    if not isinstance(rows, list):
        return []
    events: list[DividendEvent] = []
    for row in rows:
        ex_date = _parse_ex_date(row.get("date") if isinstance(row, dict) else None)
        amount = _adjusted_amount(row)
        if ex_date is None or amount is None:
            continue
        if start <= ex_date <= end:
            events.append(DividendEvent(ex_date=ex_date, amount=amount))
    events.sort(key=lambda e: e.ex_date)
    return events


class EODHDAdapter(MarketDataAdapter):
    name = "eodhd"
    # EODHD ships clean split-ADJUSTED dividend values where the hazard is cadence
    # change (annual -> Interim+Final), so the calendar-year SUM method is correct
    # (see screening.streak_by_method). Declarative only — screening owns the math.
    dividend_streak_method = "calendar_year_sum"

    def __init__(self, api_key: str | None = None, timeout: float = 15.0) -> None:
        # .strip(): stray whitespace in an env var / notebook secret must not be
        # able to cause a silent HTTP 401 (mirrors the finnhub adapter).
        raw = api_key if api_key is not None else os.environ.get("EODHD_API_KEY")
        self._api_key = (raw or "").strip() or None
        self._timeout = timeout

    # ------------------------------------------------------------------ #
    def get_price_history(
        self, ticker: str, *, start: date, end: date
    ) -> PriceHistory:
        raise NotImplementedError(_NOT_READY)

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        symbol = normalize_ticker(ticker)
        data = self._get_json(f"/fundamentals/{urllib.parse.quote(symbol)}")
        if not isinstance(data, dict) or not data:
            raise DataUnavailable(f"EODHD returned no fundamentals for {symbol}")
        return fundamentals_from_payload(symbol, data)

    # ------------------------------------------------------------------ #
    def get_dividend_history(
        self, ticker: str, *, start: date, end: date
    ) -> list[DividendEvent]:
        symbol = normalize_ticker(ticker)
        rows = self._get_json(f"/div/{urllib.parse.quote(symbol)}")
        if not isinstance(rows, list) or not rows:
            # Empty array is NOT a zero-dividend fact here — it's an absence of
            # data for this symbol/range; surface it as DataUnavailable so the
            # data-quality veto sees it, never a silent empty pass.
            raise DataUnavailable(
                f"EODHD returned no dividend history for {symbol}"
            )
        return dividend_events_from_rows(rows, start=start, end=end)

    # ------------------------------------------------------------------ #
    def _get_json(self, path: str) -> object:
        """GET {BASE}{path} with the api_token + fmt=json; errors -> DataUnavailable."""
        key = self._require_key()
        params = urllib.parse.urlencode({"api_token": key, "fmt": "json"})
        url = f"{_BASE_URL}{path}?{params}"
        try:
            with urllib.request.urlopen(url, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise DataUnavailable(f"EODHD {path} HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise DataUnavailable(f"EODHD {path}: {exc}") from exc

    def _require_key(self) -> str:
        if not self._api_key:
            raise DataUnavailable("EODHD_API_KEY is not set")
        return self._api_key
