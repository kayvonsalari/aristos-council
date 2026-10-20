"""AsOfAdapter - a market-data adapter that shows what was KNOWN on a past date (BACKTEST-1).

Wraps any adapter. Everything it returns is cut to ``as_of``:

* **Prices and dividends**: no bar and no ex-date after ``as_of`` - a future close cannot leak.
* **Accounts**: only fiscal periods whose END is on or before ``as_of - lag_days``. A year that closed
  ten days ago has not been filed yet (the 90-day default is the usual filing window), so it is not
  visible; one that closed 100 days ago is. Every ``*_annual`` list, the positional statement lists and
  the period-labelled ``aligned_annual`` dicts are cut by the fiscal-period DATES the provider gave -
  ``Fundamentals.period_ends`` - never by a guessed date. A provider that gives no dates cannot be
  cut, and the name abstains (see below).
* **Information timing (ASOF-FILING-1).** The default mode ``"lag"`` is the rule above. Mode
  ``"filing"`` makes a period visible when its FILING date (``Fundamentals.filing_dates``, from EODHD's
  ``filing_date``) is on or before ``as_of``; a period with no usable filing date (missing, or dated
  before its own period end) falls back to period end + ``lag_days`` and is COUNTED
  (``filing_date_fallbacks`` / ``filing_decisions``), so a run says how much of it rests on the fallback.
  A yfinance-only name has no filing dates, so every period of it falls back.
* **Scalars that describe "now"** - eps, free cash flow, debt, cash, dividends paid, operating cash
  flow, capex, shares - are re-derived from the newest period that SURVIVED the cut
  (``Fundamentals.period_scalars``). ``market_cap`` is that period's shares times the close on or
  before ``as_of``; ``dividend_yield``, ``dividend_per_share``, ``payout_ratio`` and ``pe_ratio`` come
  from that close and the dividends paid in the trailing year. The dividend-history signals (streak,
  last cut, year totals) are recomputed from the CUT dividend history.
* **Provenance**: ``Fundamentals.provenance`` reads "as-of 2019-06-28, lag 90 days, restated
  accounts ..." so every report can print it.

HONESTY, stated on the object so it cannot be missed:

* **RESTATED ACCOUNTS.** The figures are today's provider's record of that fiscal year - restated,
  corrected, re-classified - not the numbers as first published. The lag limits WHICH years are
  visible; it cannot un-restate them.
* **SPLIT-ADJUSTED PRICES.** Provider closes are adjusted to today's share count, while a period's
  shares are as reported then. When the share count moves by a split-sized jump after the surviving
  period, ``market_cap`` is WITHHELD (None: the size floor abstains, EV falls back) rather than
  multiplied wrongly.
* **ABSTENTION, NEVER ZEROS.** A name with no period surviving the cut - or whose provider dates its
  periods not at all - comes back with identity only (name, sector, currency): every lens abstains on
  it. Nothing is filled with a zero.

No model is imported or called here.
"""
from __future__ import annotations

from aristos_council.plurals import plural

import statistics
from dataclasses import fields, replace
from datetime import date, timedelta

from .adapter import DividendEvent, Fundamentals, MarketDataAdapter, PriceHistory

# A period-to-period change in shares outstanding this large (up or down) is read as a split or a
# reverse split, not a buyback. Outside it, market cap is withheld (see the module docstring).
SPLIT_RATIO_BAND = (0.6, 1.7)

# Fields that name WHAT a company is, not how it stood on a date: kept when the accounts are cut.
_IDENTITY = ("ticker", "name", "company_name", "sector", "currency", "financial_currency",
             "quote_type", "isin")
# Every scalar that describes "now" on Fundamentals: blanked, then re-derived from the newest
# surviving period (or dropped), so no present-day number survives into an as-of read.
_CURRENT_SCALARS = ("market_cap", "dividend_yield", "dividend_per_share", "payout_ratio", "eps",
                    "pe_ratio", "free_cash_flow", "dividends_paid", "operating_cash_flow",
                    "capital_expenditure", "years_dividend_growth", "dividend_streak_years",
                    "last_dividend_reduction_year", "dividend_year_totals", "dividend_year_stats",
                    "dividend_payment_dates", "total_debt", "debt_to_equity", "total_cash",
                    "price_to_book", "return_on_equity", "net_expense_ratio", "total_assets")


AS_OF_MODES = ("lag", "filing")


def provenance_text(as_of: date, lag_days: int, note: str = "", mode: str = "lag") -> str:
    """"as-of 2019-06-28, lag 90 days, restated accounts" (+ what happened on this name). In
    "filing" mode: "as-of 2019-06-28, filing dates (lag 90 days where a date is missing), restated
    accounts"."""
    if mode == "filing":
        base = (f"as-of {as_of.isoformat()}, filing dates (lag {plural(lag_days, 'day')} where a date "
                f"is missing), restated accounts")
    else:
        base = f"as-of {as_of.isoformat()}, lag {plural(lag_days, 'day')}, restated accounts"
    return f"{base}; {note}" if note else base


class AsOfAdapter(MarketDataAdapter):
    """See the module docstring. ``inner`` may be any adapter; it must hand back ``period_ends`` /
    ``period_scalars`` (the EODHD mapper with ``with_periods=True``) for accounts to survive the cut."""

    def __init__(self, inner: MarketDataAdapter, as_of: date, lag_days: int = 90,
                 mode: str = "lag") -> None:
        if mode not in AS_OF_MODES:
            raise ValueError(f"as-of mode must be one of {AS_OF_MODES}, got {mode!r}")
        self._inner = inner
        self.as_of = as_of
        self.mode = mode
        # (ticker, period end) -> True when that period's visibility rested on the lag FALLBACK
        # (filing mode only). A dict, so a period decided twice in one round counts once.
        self._decisions: dict[tuple[str, str], bool] = {}
        self.lag_days = int(lag_days)
        self.cutoff = as_of - timedelta(days=self.lag_days)     # last fiscal-period end that is visible
        self.name = getattr(inner, "name", "asof")
        self.dividend_streak_method = getattr(inner, "dividend_streak_method",
                                              "per_payment_median")

    @property
    def filing_decisions(self) -> int:
        """Distinct (name, fiscal period) visibility decisions made in filing mode."""
        return len(self._decisions)

    @property
    def filing_date_fallbacks(self) -> int:
        """Of those, how many had no usable filing date and fell back to period end + lag."""
        return sum(1 for fell_back in self._decisions.values() if fell_back)

    def _visible(self, ticker: str, period: str, filing: str) -> bool:
        """Is the fiscal period ending ``period`` (ISO) visible at ``as_of``? Lag mode: its end is on or
        before ``cutoff`` (unchanged). Filing mode: its filing date is on or before ``as_of``; a missing
        or impossible (before the period's own end) filing date falls back to the lag rule, counted."""
        if self.mode != "filing":
            return period <= self.cutoff.isoformat()
        usable = bool(filing) and filing >= period
        self._decisions[(ticker, period)] = not usable or self._decisions.get((ticker, period), False)
        if usable:
            return filing <= self.as_of.isoformat()
        return period <= self.cutoff.isoformat()

    @staticmethod
    def _period_filing(f: Fundamentals) -> dict:
        """{period end -> the LATEST filing date any dated list carries for it}: a period is filed once
        every statement is, so a scalar (built from all three) uses the last."""
        out: dict[str, str] = {}
        for name, dates in (f.period_ends or {}).items():
            filed = (f.filing_dates or {}).get(name) or []
            if len(filed) != len(dates):
                continue
            for d, fd in zip(dates, filed):
                if fd and fd >= d:
                    out[d] = max(out.get(d, ""), fd)
        return out

    def provider_for(self, data_kind: str) -> str:
        return self._inner.provider_for(data_kind)

    # ------------------------------------------------------------------ prices / dividends
    def get_price_history(self, ticker: str, *, start: date, end: date) -> PriceHistory:
        """The inner series with no bar after ``as_of``."""
        end = min(end, self.as_of)
        if start > end:
            return PriceHistory(ticker=ticker, bars=[])
        history = self._inner.get_price_history(ticker, start=start, end=end)
        return replace(history, bars=[b for b in history.bars if b.day <= self.as_of])

    def get_dividend_history(self, ticker: str, *, start: date, end: date) -> list[DividendEvent]:
        """The inner dividends with no ex-date after ``as_of``."""
        end = min(end, self.as_of)
        if start > end:
            return []
        events = self._inner.get_dividend_history(ticker, start=start, end=end)
        return [e for e in events if e.ex_date <= self.as_of]

    def _close_on_or_before(self, ticker: str):
        """The last traded close on or before ``as_of`` (None when there is none)."""
        try:
            bars = self.get_price_history(ticker, start=self.as_of - timedelta(days=20),
                                          end=self.as_of).bars
        except Exception:
            return None
        return next((b.close for b in reversed(bars) if b.close and b.close > 0), None)

    # ------------------------------------------------------------------ accounts
    def get_fundamentals(self, ticker: str) -> Fundamentals:
        f = self._inner.get_fundamentals(ticker)
        cutoff = self.cutoff.isoformat()
        dated = f.period_ends or {}
        pfiling = self._period_filing(f) if self.mode == "filing" else {}
        scalars = {p: s for p, s in (f.period_scalars or {}).items()
                   if self._visible(ticker, p, pfiling.get(p, ""))}
        if not dated or not scalars:
            if not dated:
                why = "its provider gives no dated fiscal periods, so the accounts cannot be cut"
            elif self.mode == "filing":
                why = f"no fiscal period had been filed on or before {self.as_of.isoformat()}"
            else:
                why = (f"no fiscal period had ended on or before {cutoff} (as-of minus the "
                       f"{self.lag_days}-day lag)")
            return self._abstain(f, why)

        cut = self._cut_lists(f, cutoff, ticker)
        newest = max(scalars)                                   # the newest SURVIVING period
        s = scalars[newest]
        close = self._close_on_or_before(ticker)
        notes = [f"newest period used {newest}"]

        market_cap = None
        shares = s.get("shares_outstanding")
        if close and shares and shares > 0:
            jump = self._split_after(f, newest)
            if jump is None:
                market_cap = shares * close
            else:
                notes.append(f"market cap withheld: shares moved x{jump:.2f} after {newest} "
                             f"(a probable split), so the period's share count is not comparable "
                             f"with split-adjusted prices")
        elif close is None:
            notes.append("no close on or before the as-of date, so no market cap")

        dividends = self._dividend_signals(ticker)
        eps = s.get("eps")
        dps = dividends.pop("dividend_per_share")
        derived = dict(
            market_cap=market_cap, eps=eps,
            pe_ratio=(close / eps) if (close and eps and eps > 0) else None,
            free_cash_flow=s.get("free_cash_flow"), total_debt=s.get("total_debt"),
            total_cash=s.get("total_cash"), dividends_paid=s.get("dividends_paid"),
            operating_cash_flow=s.get("operating_cash_flow"),
            capital_expenditure=s.get("capital_expenditure"),
            dividend_per_share=dps,
            dividend_yield=(dps / close) if (dps is not None and close) else None,
            payout_ratio=(dps / eps) if (dps is not None and eps and eps > 0) else None,
            **dividends)
        blank = {name: None for name in _CURRENT_SCALARS}
        return replace(f, **{**blank, **derived, **cut},
                       period_scalars=dict(scalars), period_ends=self._cut_dates(f, cutoff, ticker),
                       filing_dates=self._cut_filing(f, cutoff, ticker),
                       provenance=provenance_text(self.as_of, self.lag_days, "; ".join(notes),
                                                  self.mode))

    # -- helpers
    def _keep(self, f: Fundamentals, name: str, ticker: str) -> list:
        """Index mask of the entries of the dated list ``name`` that are visible (filing mode reads the
        entry's own filing date; lag mode compares its period end, exactly as before)."""
        dates = (f.period_ends or {}).get(name, [])
        filed = (f.filing_dates or {}).get(name) or []
        if len(filed) != len(dates):
            filed = [""] * len(dates)
        return [self._visible(ticker, d, fd) for d, fd in zip(dates, filed)]

    def _cut_dates(self, f: Fundamentals, cutoff: str, ticker: str = "") -> dict:
        if self.mode != "filing":
            return {name: [d for d in dates if d <= cutoff] for name, dates in f.period_ends.items()}
        return {name: [d for d, k in zip(dates, self._keep(f, name, ticker)) if k]
                for name, dates in f.period_ends.items()}

    def _cut_filing(self, f: Fundamentals, cutoff: str, ticker: str = "") -> dict:
        """``filing_dates`` cut in step with ``period_ends`` so the two stay index-parallel."""
        out = {}
        for name, dates in (f.period_ends or {}).items():
            filed = (f.filing_dates or {}).get(name) or []
            if len(filed) != len(dates):
                continue
            keep = self._keep(f, name, ticker)
            out[name] = [fd for fd, k in zip(filed, keep) if k]
        return out

    def _cut_lists(self, f: Fundamentals, cutoff: str, ticker: str = "") -> dict:
        """Every dated positional list, cut to the periods on or before ``cutoff``; plus the
        period-labelled ``aligned_annual`` dicts, cut by their own dates (holes stay in place)."""
        out: dict = {}
        valid = {fld.name for fld in fields(Fundamentals)}
        for name, dates in (f.period_ends or {}).items():
            values = getattr(f, name, None) if name in valid else None
            if not isinstance(values, list) or len(values) != len(dates):
                continue                     # a list whose dates do not line up is never trusted
            if self.mode == "filing":
                out[name] = [v for v, k in zip(values, self._keep(f, name, ticker)) if k]
            else:
                out[name] = [v for v, d in zip(values, dates) if d <= cutoff]
        aligned, aligned_ends = {}, {}
        for name, dates in (f.aligned_period_ends or {}).items():
            values = (f.aligned_annual or {}).get(name) or []
            if len(values) != len(dates):
                continue
            # the aligned (period-labelled) dicts carry no filing dates: filing mode falls back to the lag
            keep = [i for i, d in enumerate(dates) if self._visible(ticker, d, "")]
            aligned[name] = [values[i] for i in keep]
            aligned_ends[name] = [dates[i] for i in keep]
        out["aligned_annual"], out["aligned_period_ends"] = aligned, aligned_ends
        # lists the provider gave WITHOUT dates cannot be cut, so they are dropped, not trusted
        for fld in fields(Fundamentals):
            if fld.name in out or fld.name in (f.period_ends or {}):
                continue
            if fld.name.endswith("_annual") and isinstance(getattr(f, fld.name), list):
                out[fld.name] = []
        for name in ("total_revenue", "operating_income", "ebit", "tax_provision", "pretax_income",
                     "invested_capital", "shareholders_equity", "net_income"):
            if name not in out:
                out[name] = []
        return out

    def _split_after(self, f: Fundamentals, newest: str):
        """The first period-to-period share-count jump after ``newest`` that is split-sized, or None."""
        later = sorted(p for p in (f.period_scalars or {}) if p >= newest)
        counts = [(f.period_scalars[p] or {}).get("shares_outstanding") for p in later]
        counts = [c for c in counts if c and c > 0]
        for a, b in zip(counts, counts[1:]):
            ratio = b / a
            if not SPLIT_RATIO_BAND[0] <= ratio <= SPLIT_RATIO_BAND[1]:
                return ratio
        return None

    def _dividend_signals(self, ticker: str) -> dict:
        """The dividend fields, recomputed from the dividend history CUT at ``as_of``. Any failure or
        an empty history abstains (None), exactly as the live adapters do."""
        from ..tools.screening import dividend_streak

        empty = dict(dividend_per_share=None, dividend_streak_years=None,
                     last_dividend_reduction_year=None, dividend_year_totals=None,
                     dividend_year_stats=None, dividend_payment_dates=None)
        try:
            events = self.get_dividend_history(ticker, start=date(1970, 1, 1), end=self.as_of)
        except Exception:
            return empty
        if not events:
            return empty
        annual: dict[int, float] = {}
        per_year: dict[int, list[float]] = {}
        for e in events:
            annual[e.ex_date.year] = annual.get(e.ex_date.year, 0.0) + float(e.amount)
            per_year.setdefault(e.ex_date.year, []).append(float(e.amount))
        streak, last_cut = dividend_streak(annual, self.as_of.year)
        trailing = sum(e.amount for e in events if e.ex_date > self.as_of - timedelta(days=365))
        return dict(
            dividend_per_share=float(trailing),
            dividend_streak_years=streak, last_dividend_reduction_year=last_cut,
            dividend_year_totals=[[float(y), float(annual[y])] for y in sorted(annual)],
            dividend_year_stats=[[float(y), float(sum(per_year[y])),
                                  float(statistics.median(per_year[y])), float(len(per_year[y]))]
                                 for y in sorted(per_year)],
            dividend_payment_dates=sorted(e.ex_date.isoformat() for e in events))

    def _abstain(self, f: Fundamentals, why: str) -> Fundamentals:
        """Identity only: every lens abstains on it, and the reason is in the provenance."""
        keep = {name: getattr(f, name) for name in _IDENTITY}
        return Fundamentals(**keep, provenance=provenance_text(self.as_of, self.lag_days, why, self.mode))
