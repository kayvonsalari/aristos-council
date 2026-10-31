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

import statistics
from datetime import date

from .adapter import (
    DataUnavailable,
    DividendEvent,
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
    StreetConsensus,
    listing_unit_dividend,
    minor_unit_scale,
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
        try:
            quarterly_balance = tk.quarterly_balance_sheet
        except Exception:
            quarterly_balance = None
        # Cash-flow statement for the FCF-basis payout criterion (best-effort, like the
        # others): absence -> None fields -> the criterion falls back to the EPS basis.
        try:
            cashflow = tk.cashflow
        except Exception:
            cashflow = None

        # Recovered DPS, reused for the payout derivation below (same source the
        # screen sees), so dividend yield AND payout survive the summaryDetail gap.
        dps = _dividend_per_share(info)
        # GBX-YIELD-1: on a pence-quoted line (London "GBp") the price is in pence while
        # the vendor's dividend rate is in pounds, so dividend / price read 100x too low
        # (SHEL.L 0.03%, GLEN.L 0.02%). Put the dividend in the price's unit; the payout
        # ratio below keeps the vendor's own unit, because EPS is in that unit too.
        dps_listing, yield_listing = dps, _dividend_yield(info)
        scale = minor_unit_scale(info.get("currency"))
        if scale != 1.0:
            dps_listing = listing_unit_dividend(dps, _trailing_year_dividends(tk), scale)
            if not _looks_like_fund(info):
                price = (_as_float(info.get("currentPrice"))
                         or _as_float(info.get("regularMarketPrice"))
                         or _as_float(info.get("previousClose")))
                yield_listing = (sane_dividend_yield(dps_listing / price)
                                 if dps_listing is not None and price else None)
        # Dividend-growth streak + last cut, DERIVED from the payment history (a
        # yield-trap separator that's free but yfinance never surfaces as a scalar).
        (streak_years, last_cut, year_totals, year_stats,
         pay_dates) = _dividend_streak_from_ticker(tk)
        streak_is_floor, history_start = _dividend_streak_floor(tk)
        # Period-labelled series for the F-Score (PIOTROSKI-2): same statement
        # lines as the positional series above, but WITH period-end dates and
        # None holes kept in place, so cross-statement checks can period-match
        # instead of trusting list position. The positional fields are built
        # exactly as before — byte-identical for every other consumer.
        aligned_annual: dict[str, list[float | None]] = {}
        aligned_period_ends: dict[str, list[str]] = {}

        def _record_aligned(name: str, frame: object, *labels: str) -> None:
            dates, values = _aligned_series(frame, *labels)
            if dates:
                aligned_period_ends[name] = dates
                aligned_annual[name] = values

        _record_aligned("net_income", income,
                        "Net Income", "Net Income Common Stockholders")
        _record_aligned("total_revenue", income, "Total Revenue")
        _record_aligned("gross_profit", income, "Gross Profit")
        _record_aligned("operating_cash_flow", cashflow,
                        "Operating Cash Flow", "Total Cash From Operating Activities")
        _record_aligned("total_assets", balance, "Total Assets")
        _record_aligned("long_term_debt", balance,
                        "Long Term Debt", "Long Term Debt And Capital Lease Obligation")
        _record_aligned("current_assets", balance,
                        "Current Assets", "Total Current Assets")
        _record_aligned("current_liabilities", balance,
                        "Current Liabilities", "Total Current Liabilities")
        _record_aligned("shares_outstanding", balance,
                        "Ordinary Shares Number", "Share Issued")
        # VALBAND-1: the absolute valuation band needs statements it can place IN TIME
        # (the positional series drop NaN cells and lose the value <-> fiscal-year
        # correspondence). EBIT/operating income is the band's preferred denominator;
        # total debt & cash make the numerator a real EV rather than a market cap.
        # Same best-effort contract as every series above — absent -> the band abstains.
        _record_aligned("ebit", income, "EBIT")
        _record_aligned("operating_income", income, "Operating Income")
        # ABS-READINGS-1 — interest cover and the per-share growth record. ADDITIVE: no
        # factor, criterion or strategy reads either series, so no ranking can move; they
        # exist for the absolute readings on the company page, which do not vote.
        # FACTS-ORDER-1 — the DATED free-cash-flow series. The positional
        # free_cash_flow_annual carries no years, which is why the narrator had to guess
        # its order and guessed wrong (TechnipFMC, 2026-09-18 18:34). Additive: no factor
        # or criterion reads the aligned key, so no ranking moves.
        _record_aligned("free_cash_flow", cashflow, "Free Cash Flow")
        _record_aligned("interest_expense", income,
                        "Interest Expense", "Interest Expense Non Operating")
        _record_aligned("diluted_eps", income, "Diluted EPS", "Basic EPS")
        # FORENSIC-1: the Altman Z-Score's X2 and X4 lines. Period-labelled like the
        # rest, because Z sums five ratios that must all describe the SAME fiscal year —
        # a positional read could take retained earnings from one year and assets from
        # another and still produce a plausible-looking score.
        _record_aligned("retained_earnings", balance, "Retained Earnings")
        _record_aligned("total_liabilities", balance,
                        "Total Liabilities Net Minority Interest", "Total Liab")
        _record_aligned("total_debt", balance, "Total Debt")
        _record_aligned("cash", balance, "Cash And Cash Equivalents",
                        "Cash Cash Equivalents And Short Term Investments")

        return Fundamentals(
            statements_currency_note=_statements_currency_note(
                _as_float(info.get("totalRevenue")), _annual_series(income, "Total Revenue"),
                (info.get("financialCurrency") or None)),
            ticker=ticker,
            name=info.get("longName") or info.get("shortName"),
            company_name=(info.get("longName") or None),   # display label (None-guarded)
            market_cap=_market_cap(info),
            sector=(info.get("sector") or None),   # rank-engine sector exclusions
            # Currencies drive honest abstention on USD-denominated thresholds
            # (a non-USD listing makes min_market_cap meaningless). Strings, not
            # floats; normalize empty/missing to None.
            currency=(info.get("currency") or None),
            financial_currency=(info.get("financialCurrency") or None),
            dividend_yield=yield_listing,   # normalised to DECIMAL
            dividend_per_share=dps_listing,   # in the price's unit (GBX-YIELD-1)
            payout_ratio=_payout_ratio(info, dps),
            eps=_as_float(info.get("trailingEps")),
            pe_ratio=_as_float(info.get("trailingPE")),
            trailing_period_end=_epoch_to_iso(info.get("mostRecentQuarter")),
            # ABS-READINGS-2 — the STATEMENT figure, not the info blob's.
            #
            # Netflix, 2026-09-20: info['freeCashflow'] = 25,387,552,768 against an
            # operating cash flow of 10,149,273,000. Free cash flow is operating cash flow
            # minus capital spending, so it can never be larger — the two numbers are on
            # different bases (Yahoo's headline is a TTM figure computed its own way). The
            # cash-flow statement's own row is 9,461,053,000, which is 10,149,273,000 -
            # 688,220,000 exactly.
            #
            # This is the same judgement VERIFY-2 ITEM 3 already made for narration ("the
            # annual FCF SERIES is the citable core field, NOT the headline TTM"), applied
            # one level lower so every reader gets the consistent figure. The info value
            # remains the fallback for a name with no cash-flow statement.
            #
            # Cannot move a ranking: no criterion or factor computes with this scalar. It
            # is read once as a presence test in `factors.is_unrateable` and listed in a
            # criterion's `fundamentals_fields` for DISPLAY scoping; the screens read
            # `free_cash_flow_annual` (screening.py) instead.
            free_cash_flow=_first_present(_cashflow_series(cashflow, "Free Cash Flow"),
                                          _as_float(info.get("freeCashflow"))),
            # yfinance does not expose consecutive-growth-years. Left None on
            # purpose; the screen estimates it from dividend history and flags.
            years_dividend_growth=None,
            # Defensive-risk signals (free-data yield-trap separators).
            dividend_streak_years=streak_years,
            dividend_streak_is_floor=streak_is_floor,
            dividend_history_start_year=history_start,
            last_dividend_reduction_year=last_cut,
            # CRIT-NOCUT-1 — the totals the two signals above came from, carried so a
            # criterion can ask its own question of them. No extra fetch.
            dividend_year_totals=year_totals,
            dividend_year_stats=year_stats,
            dividend_payment_dates=pay_dates,
            total_debt=_statement_total_debt(quarterly_balance, _as_float(info.get("totalDebt")))[0],
            lease_liabilities=_statement_total_debt(quarterly_balance, _as_float(info.get("totalDebt")))[1],
            debt_to_equity=_as_float(info.get("debtToEquity")),
            total_cash=_as_float(info.get("totalCash")),   # EV = mcap + debt − cash
            # Financials-lens vendor scalars (FIN-1): the measures banks are priced by.
            price_to_book=_as_float(info.get("priceToBook")),
            return_on_equity=_as_float(info.get("returnOnEquity")),   # TTM decimal
            # ETF asset-class fields (ETF-1). quote_type drives the confirmed-only
            # asset-kind gate; net_expense_ratio / total_assets are the ETF-lens factors.
            # netExpenseRatio is the current field; annualReportExpenseRatio is the older
            # alias yfinance drifts to — prefer the former, fall back to the latter.
            quote_type=(info.get("quoteType") or None),
            net_expense_ratio=_as_float(info.get("netExpenseRatio")
                                        or info.get("annualReportExpenseRatio")),
            total_assets=_as_float(info.get("totalAssets")),
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
            # Financials-lens derived series (FIN-1), newest-first with the label aliases
            # yfinance drifts between versions. These back the P/B and ROE fallbacks when
            # the vendor scalars are absent.
            shareholders_equity=_cashflow_series(
                balance, "Stockholders Equity", "Total Stockholder Equity"),
            net_income=_cashflow_series(
                income, "Net Income", "Net Income Common Stockholders"),
            # PIOTROSKI-2 period-labelled series (F-Score only; see adapter.py).
            aligned_annual=aligned_annual,
            aligned_period_ends=aligned_period_ends,
            # Piotroski F-Score series (PIOTROSKI-1), newest-first. Mapped through the
            # ALIAS-TOLERANT helper (not the single-label _annual_series) because
            # yfinance drifts between these labels across versions — a single label
            # would silently yield an empty series and make 7 of the 9 checks abstain.
            # Best-effort exactly like the series above: a missing statement yields an
            # empty list, never an exception and never a fabricated value.
            #
            # CACHE: these new fields need NO ADAPTER_SCHEMA_VERSION bump —
            # cache._schema_marker already folds the sorted Fundamentals field-name set
            # into the marker, so entries written before them are detected as stale and
            # refetched automatically. Do NOT bump the version "just in case".
            total_assets_annual=_cashflow_series(balance, "Total Assets"),
            long_term_debt_annual=_cashflow_series(
                balance, "Long Term Debt",
                "Long Term Debt And Capital Lease Obligation"),
            current_assets_annual=_cashflow_series(
                balance, "Current Assets", "Total Current Assets"),
            current_liabilities_annual=_cashflow_series(
                balance, "Current Liabilities", "Total Current Liabilities"),
            shares_outstanding_annual=_cashflow_series(
                balance, "Ordinary Shares Number", "Share Issued"),
            gross_profit_annual=_cashflow_series(income, "Gross Profit"),
            # Forensic-lens series (FORENSIC-1), newest-first, alias-tolerant for the
            # same reason as the F-Score lines: yfinance renames the total-liabilities
            # row between versions, and a single label would silently yield an empty
            # series and make the Z-Score abstain for every name.
            retained_earnings_annual=_cashflow_series(balance, "Retained Earnings"),
            total_liabilities_annual=_cashflow_series(
                balance, "Total Liabilities Net Minority Interest", "Total Liab"),
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


def _trailing_year_dividends(tk, today: date | None = None) -> float | None:
    """The sum of the payments in the last 365 days, from the payment history (GBX-YIELD-1).

    Yahoo's history for a London line is in the line's own unit (HSBA.L's 2025 payments sum to
    49.55, pence, for HSBC's $0.66), so this needs no currency guess. None when the history
    cannot be read or is empty; 0.0 when nothing was paid in the window."""
    try:
        divs = tk.dividends
        if divs is None or len(divs) == 0:
            return None
        end = today or date.today()
        start = date.fromordinal(end.toordinal() - 365)
        total = 0.0
        for ts, amt in divs.items():
            if start < ts.date() <= end:
                total += float(amt)
        return total
    except Exception:
        return None


def _dividend_streak_from_ticker(tk) -> tuple:
    """(streak_years, last_reduction_year, year_totals, year_stats) from a yfinance
    Ticker's dividend history.

    Reads ``tk.dividends`` (already split-adjusted), sums per calendar year, and
    delegates to ``screening.dividend_streak`` (which excludes the current partial
    year and distinguishes FLAT from a CUT). Any failure/empty history -> (None,
    None, None), a clean abstain — this must never fail the whole fundamentals fetch.

    CRIT-NOCUT-1 returns the per-year totals as well. They were already computed here
    and thrown away; carrying them lets a criterion ask its own question of the same
    history (how big was the cut, and was it inside MY window) without a second fetch
    and without the two ever disagreeing about what a year paid."""
    from ..tools.screening import dividend_streak, dividend_streak_detail
    try:
        divs = tk.dividends
        if divs is None or len(divs) == 0:
            return None, None, None, None, None
        annual: dict[int, float] = {}
        per_year: dict = {}
        pay_dates: list = []
        for ts, amt in divs.items():
            annual[ts.year] = annual.get(ts.year, 0.0) + float(amt)
            per_year.setdefault(ts.year, []).append(float(amt))
            # YIELD-STALE-1: the dates, for "does this record still reach the present".
            try:
                pay_dates.append(ts.date().isoformat())
            except Exception:
                pass
    except Exception:
        return None, None, None, None, None
    year_stats = {y: (sum(a), statistics.median(a), len(a)) for y, a in per_year.items() if a}
    streak, last_cut = dividend_streak(annual, date.today().year, year_stats=year_stats)
    if streak is not None:
        # DIV-STREAK-1: the count itself runs on the regular rate, not on calendar totals
        streak = dividend_streak_detail(per_year, date.today().year)[0]
    totals = [[float(y), float(annual[y])] for y in sorted(annual)]
    # CRIT-NOCUT-2: the per-year payments reduced to (total, median, count). Computed here
    # because this is where the individual payments already are; deriving them again
    # downstream would be a second reading of one history.
    stats = [[float(y), float(sum(per_year[y])), float(statistics.median(per_year[y])),
              float(len(per_year[y]))] for y in sorted(per_year)]
    return streak, last_cut, totals, stats, sorted(pay_dates)


def _dividend_streak_floor(tk) -> tuple:
    """``(is_floor, history_start_year)`` for the streak ``_dividend_streak_from_ticker`` returned
    (DIV-STREAK-1). Never raises; (None, None) when there is no history."""
    from ..tools.screening import dividend_streak_detail
    try:
        per_year: dict = {}
        for ts, amt in tk.dividends.items():
            per_year.setdefault(ts.year, []).append(float(amt))
        streak, floor, start = dividend_streak_detail(per_year, date.today().year)
        return (floor if streak is not None else None), start
    except Exception:
        return None, None


def _statement_total_debt(quarterly_balance, info_total_debt):
    """``(total_debt, lease_liabilities)`` - NET-DEBT-2. Debt is the balance sheet's OWN "Total Debt" row
    for the latest quarter (borrowings plus finance leases, the same row the statement prints); the
    provider's headline ``totalDebt`` also carries operating-lease liabilities (MSFT, Jun 2026: $128.8bn
    headline against a $56.8bn row), so the excess is returned separately as ``lease_liabilities`` and is
    never counted as debt. With no statement row the headline is used and no lease figure is claimed."""
    row = None
    try:
        if quarterly_balance is not None and "Total Debt" in quarterly_balance.index:
            col = quarterly_balance.columns[0]
            row = _as_float(quarterly_balance.loc["Total Debt", col])
    except Exception:                                   # noqa: BLE001 - a statement gap is not a crash
        row = None
    if row is None or row < 0:
        return info_total_debt, None
    excess = (info_total_debt - row) if info_total_debt is not None else None
    # a rounding-size gap (Ford: 163.296bn against 163.296bn) is no lease figure
    leases = excess if excess is not None and excess > max(1e6, 0.001 * row) else None
    return row, leases


# STATEMENTS-CCY-1. The headline "trailing revenue" and the latest annual statement revenue are the SAME
# business a few months apart, so they sit within a factor of two or so of each other. A factor of three or
# more means the two are not in one currency (Vale: 218bn vs 38.4bn = 5.7x, the BRL/USD rate - the statements
# are USD, yfinance labels them BRL). The figure is a test, never a conversion: nothing is rescaled.
STATEMENTS_CCY_RATIO = 3.0


def _statements_currency_note(headline_revenue, statement_revenue, currency=None) -> str:
    latest = next((v for v in (statement_revenue or []) if v), None)
    if not headline_revenue or not latest or headline_revenue <= 0 or latest <= 0:
        return ""
    ratio = max(headline_revenue / latest, latest / headline_revenue)
    if ratio < STATEMENTS_CCY_RATIO:
        return ""
    from ..tools.price_context import format_money
    return (f"headline revenue {format_money(headline_revenue, currency, abbreviate=True)} vs latest "
            f"statement {format_money(latest, currency, abbreviate=True)}, {ratio:.1f}x apart "
            f"(not in one currency)")


def _market_cap(info: dict) -> float | None:
    """Market capitalisation, from whichever field the provider actually filled.

    MCAP-NA-1. Six names in the oil cohort -- IMO, WDS, KEY.TO, IOC.NS, BPT.AX, STO.AX --
    came back with ``marketCap`` absent and were sized by nothing, so every lens's size
    floor abstained for them and the reports read "market cap unavailable" on companies
    worth tens of billions. The figure was there the whole time under a different key:

        IMO     nonDilutedMarketCap  65,486,230,831
        WDS                          43,886,829,885
        KEY.TO                       15,839,965,368
        IOC.NS                    1,864,427,103,707   (INR -- the currency guard still
                                                       abstains on the USD threshold)
        BPT.AX                        2,053,200,290
        STO.AX                       28,353,057,949

    Order: the diluted figure the vendor normally gives, then the non-diluted one, then
    shares x price as a last resort. All three are the vendor's own arithmetic on the
    vendor's own numbers -- nothing here estimates a share count. None when the provider
    filled none of them, which the size floor already treats as NOT TESTED rather than as
    a failure (a missing figure is never a phantom fail, house rule 3)."""
    direct = _as_float(info.get("marketCap")) or _as_float(info.get("nonDilutedMarketCap"))
    if direct:
        return direct
    shares = _as_float(info.get("sharesOutstanding"))
    price = (_as_float(info.get("regularMarketPrice"))
             or _as_float(info.get("currentPrice"))
             or _as_float(info.get("previousClose")))
    if shares and price:
        return shares * price
    return None


def _dividend_yield(info: dict) -> float | None:
    """Dividend yield as a DECIMAL (0.0289 == 2.89%), regardless of yfinance's shifting
    units. yfinance's ``dividendYield`` is now a PERCENT NUMBER (2.89 == 2.89%), while
    ``trailingAnnualDividendYield`` has stayed a DECIMAL (0.0289). PREFER the decimal
    field; fall back to the percent field / 100. The >100% backstop then catches any
    further drift. (Do NOT read the raw ``dividendYield`` untouched — that was the 100x
    bug: 2.89 compared as if it were 289%.)

    FUNDS (ETF / mutual fund) take a distinct path (ETFCHK-1), routed by ``_looks_like_fund``
    (quoteType OR the fund-only ``yield`` key — ETFCHK-3, robust to a dropped quoteType):
    their canonical distribution yield is the ``yield`` key (a DECIMAL), and the equity
    dividend fields are UNRELIABLE for a fund — yfinance frequently returns a spurious
    ``0.0`` in a flaky ``info`` block for a fund that truly distributes (the same
    rule-3 flakiness that drops
    ``dividendRate``). Reading that ``0.0`` fabricated a false ``0`` distribution yield
    (Company Check rendered ``0 [computed]`` for SCHD). ``_fund_distribution_yield`` reads
    the canonical field, prefers the first POSITIVE source, and NOT-EVALUATES (None) when
    no positive source exists — never a defaulted ``0`` (the ``-0 ROIC`` bug class)."""
    if _looks_like_fund(info):
        return _fund_distribution_yield(info)
    trailing = _as_float(info.get("trailingAnnualDividendYield"))
    if trailing is not None:
        return sane_dividend_yield(trailing)
    pct = _as_float(info.get("dividendYield"))
    if pct is not None:
        return sane_dividend_yield(pct / 100.0)   # percent -> decimal
    return None


def _is_fund_quote_type(quote_type: object) -> bool:
    """True for a pooled-fund vendor ``quoteType`` (ETF / mutual fund) — the kinds whose
    distribution yield must come from the fund ``yield`` field, not the equity dividend
    fields (ETFCHK-1). Missing/other kinds take the equity path unchanged."""
    return isinstance(quote_type, str) and quote_type.strip().upper() in {
        "ETF", "MUTUALFUND"}


def _looks_like_fund(info: dict) -> bool:
    """True when this ``info`` is a pooled fund, detected by EITHER signal (ETFCHK-3):

    1. a fund ``quoteType`` (ETF / mutual fund), OR
    2. the presence of the fund-only ``yield`` key.

    Relying on ``quoteType`` ALONE regressed the live path: yfinance's ``.info`` FLAKILY
    drops ``quoteType`` on a given fetch (the same rule-3 flakiness that empties the
    summaryDetail block), and the daily cache then freezes that ``quoteType``-less shape
    for the rest of the day. With no ``quoteType`` SCHD fell through to the equity path,
    read a spurious ``trailingAnnualDividendYield == 0.0``, and fabricated a ``0``
    distribution yield (``0 [computed]`` in Company Check) — while the universe run, whose
    batch fetch caught a populated ``quoteType``, ranked the field correctly. yfinance
    populates ``yield`` ONLY for pooled funds (an equity's ``info`` has no such key), so
    its presence is an independent, reliable fund signal that survives the drop. ETFCHK-1's
    mocks always set ``quoteType='ETF'``, so they never exercised the dropped-``quoteType``
    live shape — this closes that gap."""
    if _is_fund_quote_type(info.get("quoteType")):
        return True
    return "yield" in info and _as_float(info.get("yield")) is not None


def _fund_distribution_yield(info: dict) -> float | None:
    """A fund's trailing distribution yield as a DECIMAL (ETFCHK-1). The canonical source
    is the ``yield`` key; the equity dividend fields are consulted only as fallbacks and
    only when POSITIVE, since yfinance returns a spurious ``0.0`` there for funds that
    truly distribute. Returns the FIRST PRESENT, POSITIVE source; a genuinely absent yield
    (no positive source anywhere) is NOT-EVALUATED (None), NEVER a defaulted ``0`` — a
    fabricated zero is the same fault class as the ``-0 ROIC`` bug (absent != zero)."""
    pct = _as_float(info.get("dividendYield"))
    for v in (_as_float(info.get("yield")),
              _as_float(info.get("trailingAnnualDividendYield")),
              (pct / 100.0) if pct is not None else None):   # percent -> decimal
        if v is not None and v > 0:
            return sane_dividend_yield(v)
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


def _epoch_to_iso(value: object) -> str | None:
    """yfinance's ``mostRecentQuarter`` (epoch seconds) as an ISO date, or None."""
    secs = _as_float(value)
    if secs is None or secs <= 0:
        return None
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(secs, tz=timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


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


def _first_present(series, fallback):
    """The newest reported value of ``series``, else ``fallback``. ABS-READINGS-2."""
    for value in series or ():
        if value is not None:
            return value
    return fallback


def _cashflow_series(df: object, *labels: str) -> list[float]:
    """Newest-first annual series for the first matching cash-flow row label."""
    for label in labels:
        series = _annual_series(df, label)
        if series:
            return series
    return []


def _aligned_series(df: object, *labels: str) -> tuple[list[str], list[float | None]]:
    """A NEWEST-FIRST (period-end ISO dates, values) pair for one statement row,
    with NaN kept IN PLACE as None (PIOTROSKI-2).

    Unlike ``_annual_series`` (which DROPS NaN cells and therefore loses the
    position <-> fiscal-year correspondence), this keeps every column so the
    F-Score can period-match values ACROSS statements. Alias-tolerant like
    ``_cashflow_series``: the first label whose row carries at least one real
    value wins. Missing frame/label, or an all-NaN row under every alias ->
    ``([], [])`` — the F-Score then falls back to the positional path.
    """
    for label in labels:
        try:
            if df is None or df.empty or label not in df.index:
                continue
            row = df.loc[label]
            try:
                row = row.sort_index(ascending=False)   # newest-first, like _annual_series
            except Exception:
                pass
            dates: list[str] = []
            values: list[float | None] = []
            for key, v in zip(list(row.index), row.tolist()):
                try:
                    d = key.date().isoformat()          # pandas Timestamp column
                except Exception:
                    d = str(key)[:10]                   # already a date/str column
                dates.append(d)
                values.append(_as_float(v))             # NaN -> None, kept IN PLACE
            if any(v is not None for v in values):
                return dates, values
        except Exception:
            continue
    return [], []


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
