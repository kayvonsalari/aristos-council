"""Share price + 52-week position (PRICE-1) — what does this cost today, and is that
high or low lately?

Why this exists
---------------
Every report line in this system was a RANK or a PERCENTILE. A reader could learn that
PFE sits 70th in its own five-year valuation band and still not know what one share
costs. This module supplies the two plainest facts there are, from data the run has
ALREADY fetched:

1. the last close, WITH its currency and its as-of date;
2. where that close sits between the trailing 52-week low and high.

Both come from the SAME 400-day close series ``gather_factor_inputs`` already fetches
for the momentum/volatility legs — no new fetch, no new provider call, and (critically)
NO re-windowing: ``low_volatility`` consumes the whole close list, so widening that
window would silently change every existing strategy's ranking. This module only reads.

Three deliberate decisions
--------------------------
1. **``PriceBar.close``, never ``adj_close``.** The same choice
   ``tools/valuation_band.py`` makes and for the same reason: ``adj_close`` is a
   TOTAL-RETURN series, and a total-return level is not a share price. For a dividend
   payer the adjusted series sits BELOW the traded price in the past, which would drag
   the 52-week low down and flatter the position. The traded close is what a reader
   means by "the share price".

2. **Currency is stated, never converted and never assumed.** Several ETF cohorts are
   EUR/GBP denominated. An unknown currency renders as unknown — house rule 8's honest
   abstention applied to display: no FX, ever.

3. **The 52-week window is the trailing 52 weeks of the series, not the whole 400
   days.** The 400-day fetch is ~13 months; presenting all of it as "the 52-week range"
   would quietly widen the range. Below 40 weeks of span the range ABSTAINS with the
   REAL span ("only 31 weeks of closes") — a partial range is never dressed as a full
   one.

Pure and deterministic: same bars -> same output, in any input order. No IO, no LLM,
no forecasting. Display only — nothing here feeds a factor, screen, gate, rank or
verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Optional, Sequence

# --- Construction constants (documented above) ------------------------------------- #
WEEKS_52 = 52
MIN_52W_WEEKS = 40          # below this span the 52-week range abstains, with the span

# Symbols for the currencies these cohorts actually quote in. Anything else renders with
# its ISO code ("CHF 84.20") and an ABSENT currency renders as explicitly unknown — a
# number without a currency is never silently dressed as dollars.
_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "C$", "AUD": "A$",
            "CHF": "CHF ", "JPY": "¥", "SEK": "SEK ", "DKK": "DKK ", "NOK": "NOK ",
            "GBp": "GBp "}


CURRENCY_UNKNOWN_NOTE = " — currency not reported, so the amounts above carry no " \
                        "currency (never assumed to be USD)"


def currency_note(currency: Optional[str]) -> str:
    """The one-per-line disclosure that a rendered amount has no known currency."""
    return "" if currency else CURRENCY_UNKNOWN_NOTE


# MONEY-ABBREV-1 — magnitude abbreviation, in the ONE helper every surface reads.
# "USD 69,659,000,000" is thirteen digits a reader has to COUNT to understand, and the
# 2026-08-26 12:33 run put four of them in a single sentence next to a
# "KRW 24,793,783,000,000". Abbreviating is display-only: the ledger, the records and the
# provenance audit keep full precision, and ``format_money_full`` is what a hover title
# shows so the exact figure is always one gesture away.
_MAGNITUDES = ((1e12, "tn"), (1e9, "bn"), (1e6, "m"))


def format_money(value: Optional[float], currency: Optional[str], *,
                 decimals: int = 2, abbreviate: bool = False) -> str:
    """One money amount as every PRICE-1 surface renders it: ``$27.14``, ``€61.30``,
    ``CHF 84.20`` — or a BARE ``27.14`` when the provider reported no currency.

    NEVER converts. The currency is part of the number, so a EUR-denominated ETF and a
    USD stock in the same table can't be read as comparable amounts. An unknown currency
    is stated ONCE per rendered line via ``currency_note`` rather than repeated after
    every amount — never silently defaulted to dollars.

    ``abbreviate=True`` collapses large magnitudes (``$69.7bn``, ``KRW 24.8tn``). The
    thresholds are ABSOLUTE, so a negative free-cash-flow year abbreviates the same way a
    positive one does — ``-$4.5bn``, never ``-4,501,657,000``. Default off, so every
    existing caller is byte-unchanged.
    """
    if value is None:
        return "—"
    if abbreviate:
        size = abs(value)
        for cut, suffix in _MAGNITUDES:
            if size >= cut:
                amount = f"{value / cut:,.1f}{suffix}"
                if not currency:
                    return amount
                sym = _SYMBOLS.get(currency)
                return f"{sym}{amount}" if sym else f"{currency} {amount}"
        # under a million: thousands separators, unabbreviated, and NO forced decimals —
        # "950,000" reads as money; "950,000.00" reads as a spreadsheet.
        decimals = 0 if float(value).is_integer() else decimals
    amount = f"{value:,.{decimals}f}"
    if not currency:
        return amount
    sym = _SYMBOLS.get(currency)
    return f"{sym}{amount}" if sym else f"{currency} {amount}"


def format_money_full(value: Optional[float], currency: Optional[str]) -> str:
    """The UNABBREVIATED amount — what a hover title carries beside an abbreviated one,
    and what the fact-checker resolves an abbreviated figure back to."""
    if value is None:
        return "—"
    decimals = 0 if float(value).is_integer() else 2
    amount = f"{value:,.{decimals}f}"
    if not currency:
        return amount
    sym = _SYMBOLS.get(currency)
    return f"{sym}{amount}" if sym else f"{currency} {amount}"


@dataclass(frozen=True)
class PriceContext:
    """The share price and its 52-week position for one name.

    ``last_close`` is None when the run has no price bars at all — ``note`` then carries
    the reason. ``position_pct`` is None when the 52-week range ABSTAINED (too little
    history); the price still renders, because the two facts fail independently and a
    missing range is no reason to hide the price.
    """

    last_close: Optional[float] = None
    as_of: Optional[date] = None              # the DAY of that close — a stale cache is
                                              # then visible rather than silent
    currency: Optional[str] = None            # None -> rendered "(currency unknown)"
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    position_pct: Optional[float] = None      # 0 = at the 52-week low, 100 = at the high
    weeks_covered: float = 0.0                # the REAL span of closes inside the window
    closes_in_window: int = 0
    note: str = ""                            # why the price is absent
    range_note: str = ""                      # why the 52-week range abstained

    @property
    def available(self) -> bool:
        return self.last_close is not None

    @property
    def range_available(self) -> bool:
        return self.position_pct is not None

    @property
    def price_display(self) -> str:
        """``$27.14 (as of 2026-08-22)`` — or the reason there is no price."""
        if not self.available:
            return f"price not available — {self.note}" if self.note \
                else "price not available"
        stamp = f" (as of {self.as_of.isoformat()})" if self.as_of else ""
        return f"{format_money(self.last_close, self.currency)}{stamp}"

    @property
    def range_display(self) -> str:
        """``34% of its 52-week range (low $22.80 · high $31.20)`` — or the abstention
        with the REAL span, e.g. ``52-week range not evaluated — only 31 weeks of
        closes``."""
        if not self.range_available:
            return f"52-week range not evaluated — {self.range_note}" if self.range_note \
                else "52-week range not evaluated"
        return (f"{self.position_pct:.0f}% of its 52-week range "
                f"(low {format_money(self.low_52w, self.currency)} · "
                f"high {format_money(self.high_52w, self.currency)})")

    @property
    def display(self) -> str:
        """The ONE line every surface renders (CLI, Run tab, markdown, HTML):

        ``$27.14 (as of 2026-08-22) — 34% of its 52-week range (low $22.80 · high
        $31.20)``. When the price itself is absent the range clause is dropped — there
        is nothing to place in a range."""
        if not self.available:
            return self.price_display
        return (f"{self.price_display} — {self.range_display}"
                f"{currency_note(self.currency)}")


def price_context(bars: Sequence, *, currency: Optional[str] = None,
                  weeks: int = WEEKS_52,
                  min_weeks: int = MIN_52W_WEEKS) -> PriceContext:
    """The share price + 52-week position from an EXISTING close series.

    ``bars`` is any sequence of ``PriceBar``-shaped objects (``.day``, ``.close``) — the
    400-day series the ranking legs already read. It is only READ: no window is widened,
    no fetch is made, and the sequence's order does not matter.

    The 52-week window is anchored on the LAST CLOSE's own day (``last_day - 52
    weeks``, inclusive), not on the calendar today: the range and the price are then two
    statements about the same series, and cache staleness shows up honestly in the
    as-of date rather than by silently truncating the window.

    Never raises on thin/absent data — it abstains with a reason.
    """
    usable: list[tuple[date, float]] = []
    for b in bars or []:
        d = getattr(b, "day", None)
        c = getattr(b, "close", None)
        if d is None or c is None or c <= 0:
            continue
        usable.append((d, float(c)))
    if not usable:
        return PriceContext(currency=currency,
                            note="no price history returned for this name")
    # Sorted by (day, close), NOT day alone: a real series carries one bar per day, but a
    # fixture or a provider hiccup can repeat one — and sorting on the day alone would
    # then leave the "last close" dependent on the INPUT ORDER, which this function
    # promises it is not.
    usable.sort(key=lambda dc: (dc[0], dc[1]))
    last_day, last_close = usable[-1]

    start = last_day - timedelta(weeks=weeks)
    window = [(d, c) for d, c in usable if d >= start]
    span_weeks = (window[-1][0] - window[0][0]).days / 7.0 if len(window) > 1 else 0.0
    base = PriceContext(last_close=last_close, as_of=last_day, currency=currency,
                        weeks_covered=round(span_weeks, 1),
                        closes_in_window=len(window))
    if span_weeks < min_weeks:
        return replace(base,
                       range_note=f"only {round(span_weeks)} weeks of closes")

    high = max(c for _, c in window)
    low = min(c for _, c in window)
    # At the high reads 100, at the low reads 0. A ZERO-WIDTH range (a price that never
    # moved all year) is degenerate: today's close IS the high, so the stated rule
    # "at the high reads 100%" governs — no interpolation over a zero denominator.
    pos = 100.0 if high <= low else 100.0 * (last_close - low) / (high - low)
    return replace(base, high_52w=high, low_52w=low, position_pct=round(pos, 1))


# --------------------------------------------------------------------------- #
# MONEY-ABBREV-2 — the shared rule, applied to text the MODEL wrote
# --------------------------------------------------------------------------- #
# MONEY-ABBREV-1 gave every STRUCTURED money field the abbreviation rule. It did not
# reach the narrator's FREE-TEXT fields, and that is where the offenders live: the
# 2026-08-26 15:40 report still read "USD 28,989,000,000; USD 69,659,000,000; ..." inside
# neutral_context, a specialist's reasoning and an open question. Instructing the model
# not to do it is not a guarantee — this is, and it routes through the SAME
# ``format_money``, so there is still exactly one rule.
#
# Deliberately NARROW. A token is money only when a currency marker sits against it, or
# when it carries thousands-groups reaching a million. Percentages, ranks ("22 of 37"),
# years and plain counts are left alone.
_CCY = r"(?:[$€£¥]|(?:USD|EUR|GBP|JPY|CHF|KRW|SEK|DKK|NOK|CAD|AUD|HKD|TWD|GBp))"
_AMOUNT = r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?"
_MONEY_IN_TEXT = re.compile(
    rf"(?P<pre>{_CCY})\s?(?P<amt_a>{_AMOUNT})"          # USD 28,989,000,000 / $29,000,000
    rf"|(?P<amt_b>{_AMOUNT})\s?\((?P<post_p>{_CCY})\)"  # 1,671,000.00 (KRW)
    rf"|(?P<amt_c>{_AMOUNT})\s(?P<post>{_CCY})\b"       # 1,671,000.00 KRW
)

# Below this the raw figure is already readable and abbreviating only loses precision.
ABBREVIATE_FROM = 1_000_000


def abbreviate_money_in_text(text: str) -> tuple[str, list[tuple[str, str]]]:
    """``(rendered, [(abbreviated, full), …])`` — every money amount in ``text`` at or
    above a million, rewritten through ``format_money``.

    The second element is what a hover title is built from, so the exact figure the
    ledger holds stays one gesture away. Text with no qualifying amount is returned
    unchanged and with an empty list, so this is safe to run over everything."""
    if not text:
        return text, []
    swaps: list[tuple[str, str]] = []

    def _sub(m: "re.Match") -> str:
        raw = m.group("amt_a") or m.group("amt_b") or m.group("amt_c")
        ccy = m.group("pre") or m.group("post_p") or m.group("post")
        try:
            value = float(raw.replace(",", ""))
        except ValueError:                                   # pragma: no cover
            return m.group(0)
        if abs(value) < ABBREVIATE_FROM:
            return m.group(0)
        code = ccy if ccy and ccy.isalpha() else _CODE_FOR_SYMBOL.get(ccy, "")
        short = format_money(value, code or None, abbreviate=True)
        swaps.append((short, format_money_full(value, code or None)))
        return short

    return _MONEY_IN_TEXT.sub(_sub, text), swaps


_CODE_FOR_SYMBOL = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
