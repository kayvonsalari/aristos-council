"""The peers table, as ONE structure every surface renders (batch 8).

The Company Check page, its text export, its HTML export and the cohort ``report.md`` used to format
a market cap four different ways ("76,547,235,840 GBX" here, "$102,486,591,485" there). This module
turns a ``market_index.PeerGroup`` into rows that carry BOTH the numbers (so the page can sort on
them) and the strings (so a static export prints exactly what the page shows), and every string goes
through the one money formatter, ``tools.price_context.format_money`` (``$466.4bn``, ``$1.03tn``,
``CHF 221.4bn``, ``£76.5bn``).

One fact about the data is handled here so no surface has to know it:

* **A London market cap is in POUNDS, not pence.** EODHD serves the cap in the major unit while the
  quote currency code says ``GBX`` (INDEX-GBX-SCALE-1). A row whose ``market_cap_usd_source`` is
  ``computed (major unit)`` is therefore labelled GBP, never "76,547,235,840 GBX".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .market_index import MINOR_UNIT_MARKET_CAP, USD_COMPUTED_MAJOR_UNIT
from .tools.price_context import format_money


def local_cap_currency(row) -> str:
    """The currency a row's LOCAL market cap is actually in.

    ``GBX`` with a ``computed (major unit)`` source means the number is pounds: GBP. Every other
    row is reported in its own code, unchanged (never converted here - the USD column does that)."""
    code = (getattr(row, "currency", "") or "").strip()
    source = getattr(row, "market_cap_usd_source", "") or ""
    if source == USD_COMPUTED_MAJOR_UNIT and code.upper() in MINOR_UNIT_MARKET_CAP:
        return MINOR_UNIT_MARKET_CAP[code.upper()]
    return code


@dataclass(frozen=True)
class PeerRow:
    ticker: str
    name: str
    exchange: str
    sub_industry: str
    cap_usd: Optional[float]
    cap_local: Optional[float]
    local_currency: str            # the major-unit currency, or "" when the row states none

    @property
    def usd_text(self) -> str:
        return format_money(self.cap_usd, "USD", abbreviate=True)

    @property
    def local_text(self) -> str:
        return format_money(self.cap_local, self.local_currency or None, abbreviate=True)


def peer_rows(group) -> list[PeerRow]:
    """Members of ``group`` as rows, LARGEST USD cap first (a row with no USD cap last, then by
    ticker, so the order never depends on the order the index returned them)."""
    rows = []
    for m in group.members:
        rows.append(PeerRow(
            ticker=m.ticker, name=m.name or "", exchange=m.exchange or "",
            sub_industry=getattr(m, "classification", "") or "",
            cap_usd=m.market_cap_usd, cap_local=m.market_cap,
            local_currency=local_cap_currency(m)))
    return sorted(rows, key=lambda r: (r.cap_usd is None, -(r.cap_usd or 0.0), r.ticker))


def peer_text_lines(group) -> list[str]:
    """The table as fixed-width text, for the ``.txt`` export and the CLI."""
    rows = peer_rows(group)
    out = [f"{'Ticker':<12} {'Name':<28} {'Exch':<7} {'Market cap (USD)':>17} {'Local':>15}  "
           f"Sub-industry"]
    for r in rows:
        out.append(f"{r.ticker:<12} {r.name[:28]:<28} {r.exchange[:7]:<7} "
                   f"{r.usd_text:>17} {r.local_text:>15}  {r.sub_industry}")
    return out


# The page's table: numbers stay NUMBERS (so a column sorts by size, not alphabetically) and the
# display format is a Streamlit NumberColumn's. Values are in billions because a printf format
# cannot abbreviate; the static exports use ``PeerRow.usd_text`` / ``local_text`` instead.
USD_COLUMN = "Market cap (USD)"
LOCAL_COLUMN = "Market cap (local)"
USD_FORMAT = "$%.1fbn"
LOCAL_FORMAT = "%.1fbn"


def peer_frame_records(group) -> list[dict]:
    """Rows for ``st.dataframe``: numeric caps in BILLIONS, sorted by USD cap descending."""
    return [{
        "Ticker": r.ticker, "Name": r.name, "Exchange": r.exchange,
        USD_COLUMN: None if r.cap_usd is None else r.cap_usd / 1e9,
        LOCAL_COLUMN: None if r.cap_local is None else r.cap_local / 1e9,
        "Currency": r.local_currency or "not stated",
        "Sub-industry": r.sub_industry,
    } for r in peer_rows(group)]
