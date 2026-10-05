"""The peers table, as ONE structure every surface renders (batch 8).

The Company Check page, its text export, its HTML export and the cohort ``report.md`` used to format
a market cap four different ways ("76,547,235,840 GBX" here, "$102,486,591,485" there). This module
turns a ``market_index.PeerGroup`` into rows that carry BOTH the numbers (so the page can sort on
them) and the strings (so a static export prints exactly what the page shows), and every string goes
through the one money formatter, ``tools.price_context.format_money`` (``$466.4bn``, ``$1.03tn``,
``CHF 221.4bn``, ``£76.5bn``).

Two facts about the data are handled here so no surface has to know them:

* **A one-system peer is marked, not explained per row.** A peer admitted on ONE label system only
  (GICS or EODHD's industry, not both) carries a dagger and the table has one footnote line. Which
  system, per peer, stays in ``peer_snapshot`` and the run record - it is not a column. When the
  subject could only be matched in ONE system to begin with, every peer is one-system by
  construction and nothing is marked.

* **A London market cap is in POUNDS, not pence.** EODHD serves the cap in the major unit while the
  quote currency code says ``GBX`` (INDEX-GBX-SCALE-1). A row whose ``market_cap_usd_source`` is
  ``computed (major unit)`` is therefore labelled GBP, never "76,547,235,840 GBX".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .exchange_names import exchange_name
from .market_index import MINOR_UNIT_MARKET_CAP, USD_COMPUTED_MAJOR_UNIT
from .tools.price_context import format_money

PEER_METHOD_TITLE = "How this peer group was built"
THIS_COMPANY = "(this company)"
# PEER-ROW-TINT-1: the company's own row needs a tint a reader can SEE. The earlier translucent
# grey (rgba(127,127,127,.14)) was invisible on a light page and nearly so on a dark one — and a
# canvas data grid may drop the alpha anyway. An OPAQUE amber with an explicit dark text colour
# reads on either theme because neither the page's own background nor its own text colour takes
# part in the contrast. One definition, used by the page (``st.dataframe`` styler) and the HTML.
THIS_COMPANY_BG = "#f6d86b"
THIS_COMPANY_FG = "#1d2127"
THIS_COMPANY_STYLE = (f"background-color: {THIS_COMPANY_BG}; color: {THIS_COMPANY_FG}; "
                      "font-weight: 700")
DOES_NOT_APPLY = "does not apply"
# PEERS-RANK-RULE (19A): the under-3 rule the lens pages already follow. A rank among fewer than
# three names is not a ranking, so the column says so in every cell; a column nobody was ranked in
# (every name excluded or unrateable) is dropped rather than printed empty.
MIN_RANKED = 3
TOO_FEW_TO_RANK = "too few to rank"
ONE_SYSTEM_MARK = "†"
ONE_SYSTEM_NOTE = f"{ONE_SYSTEM_MARK} counted as a peer on one industry classification only"


# NAME-CLEAN-1 (19B B7): the index carries the exchange's security description, share class and all
# ("Indivior PLC Ordinary Shares", "MBX Biosciences, Inc. Common", "Alamar Biosciences, Inc. Com").
# A reader wants the company. Only DESCRIPTORS of the security are stripped, never part of the name
# ("Class A" stays: it tells two lines of one company apart).
_SHARE_CLASS_TAIL = re.compile(
    r"(?:[\s,]+(?:(?:class|cl\.?)\s+[a-z0-9]{1,2}\s+)?"
    r"(?:ordinary|common|capital)(?:\s+(?:shares?|stock))?"
    r"|[\s,]+ord\.?\s+shs?"
    r"|[\s,]+com"
    r"|[\s,]+(?:american\s+)?depositary\s+(?:shares|receipts?)"
    r"|[\s,]+ads)\.?$", re.I)
_KEEP_UPPER_NO = {"co", "ltd", "inc", "plc", "corp", "llc", "the", "and", "of", "company", "limited"}


def _title_case_caps(name: str) -> str:
    """"CALIWAY BIOPHARMACEUTICALS CO., LTD." -> "Caliway Biopharmaceuticals Co., Ltd." Only a name
    that is ENTIRELY capitals is touched; a mixed-case name ("NVIDIA Corporation", "BYD Company")
    keeps the capitals it was given."""
    letters = [c for c in name if c.isalpha()]
    if not letters or not all(c.isupper() for c in letters) or len(letters) < 5:
        return name
    out = []
    for word in name.split(" "):
        core = word.strip(",.&()")
        if core.lower() in _KEEP_UPPER_NO or len(core) > 3 or not core.isalpha():
            out.append(word[:1].upper() + word[1:].lower() if word else word)
        else:
            out.append(word)                      # a short run of capitals is an acronym: keep it
    return " ".join(out)


def clean_company_name(name: str) -> str:
    """The company's name without its share-class description, title-cased when it was all capitals.
    Empty in, empty out; never returns an empty string for a non-empty name."""
    text = (name or "").strip()
    previous = None
    while previous != text:
        previous = text
        stripped = _SHARE_CLASS_TAIL.sub("", text).strip(" ,")
        if stripped:
            text = stripped
    return _title_case_caps(text) or (name or "").strip()


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
    one_system: bool = False       # admitted on ONE of the subject's two label systems -> dagger
    # PEER-RANK-COLUMNS-1: this row is the company itself (first row, marked), and the value it holds
    # under each rank column, ``((header, cell), ...)`` - cells are ranks (int) or words.
    is_company: bool = False
    ranks: tuple = ()

    @property
    def marked_ticker(self) -> str:
        if self.is_company:
            return f"{self.ticker} {THIS_COMPANY}"
        return f"{self.ticker} {ONE_SYSTEM_MARK}" if self.one_system else self.ticker

    @property
    def usd_text(self) -> str:
        return format_money(self.cap_usd, "USD", abbreviate=True)

    @property
    def local_text(self) -> str:
        return format_money(self.cap_local, self.local_currency or None, abbreviate=True)


def has_one_system_peers(rows) -> bool:
    return any(r.one_system for r in rows)


@dataclass(frozen=True)
class RankColumn:
    """One lens's column of the peers table: each company's rank under that lens, or - for a check
    lens - its mark. ``values`` maps an upper-cased Yahoo ticker to a rank (int) or a word."""

    header: str
    kind: str                    # "rank" (a voting lens) | "mark" (a check lens: it does not vote)
    values: dict


def rank_columns(report) -> list[RankColumn]:
    """The columns for a Company Report, built ONLY from the ranks the run already computed and saved
    (``report.lens_ranks``): no new data, no request, no model call. One column per ticked lens that
    ran, in the order they were ticked; none when no lens ran (no lens ticked, or no peer group).

    A voting lens's column holds each company's cohort position and reads "Magic Formula RAW rank (of
    14)"; a check lens's holds its mark (clean / no concern / doubted) and reads "Forensic mark". A
    company the lens's rules exclude reads "does not apply" - not blank, not a number - and one it
    could not read reads "no data" or "fetch failed"."""
    from .report_language import verdict_word

    ranks = getattr(report, "lens_ranks", None) or {}
    columns: list[RankColumn] = []
    for vote in getattr(report, "votes", ()) or ():
        record = ranks.get(vote.strategy_id)
        if not record:
            continue
        voter = vote.votes
        ranked = record.get("ranked") or []
        values: dict = {}
        for entry in ranked:
            values[str(entry["ticker"]).upper()] = (
                entry.get("position") if voter else verdict_word(entry.get("verdict", ""), check=True))
        for key, word in (("excluded", DOES_NOT_APPLY), ("unrateable", "no data"),
                          ("fetch_errors", "fetch failed")):
            for entry in record.get(key) or []:
                values.setdefault(str(entry["ticker"]).upper(), word)
        if voter and not ranked:
            continue                                   # (of 0): nothing to show, so no column
        if voter and len(ranked) < MIN_RANKED:
            values = {key: TOO_FEW_TO_RANK for key in values}
            header = f"{vote.label} rank"
        else:
            header = (f"{vote.label} rank (of {len(ranked)})" if voter
                      else f"{vote.label} mark (check - does not vote)")
        columns.append(RankColumn(header=header, kind="rank" if voter else "mark", values=values))
    return columns


def _yahoo_key(member) -> str:
    from .cohorts.symbols import SymbolError, yahoo_symbol
    symbol = (getattr(member, "yahoo_ticker", "") or "").strip()
    if not symbol:
        try:
            symbol = yahoo_symbol(member.ticker)
        except SymbolError:
            symbol = ""
    return symbol.upper()


def peer_rows(group, columns=None, company_ticker: str = "") -> list[PeerRow]:
    """Members of ``group`` as rows, LARGEST USD cap first (a row with no USD cap last, then by
    ticker, so the order never depends on the order the index returned them).

    With ``columns`` and a ``company_ticker`` the COMPANY ITSELF is added as the FIRST row, marked
    "(this company)", so its rank reads against the others, and every row carries its value under each
    rank column (a peer the run did not rank reads "not in this run")."""
    columns = list(columns or ())

    def cells(key: str) -> tuple:
        return tuple((c.header, c.values.get(key, "not in this run")) for c in columns)

    rows = []
    both_systems = len(getattr(group, "systems", ()) or ()) > 1
    for m in group.members:
        how = (getattr(group, "matched_on", None) or {}).get(m.ticker, "")
        rows.append(PeerRow(
            ticker=m.ticker, name=clean_company_name(m.name or ""),
            exchange=exchange_name(m.exchange or ""),
            sub_industry=getattr(m, "classification", "") or "",
            cap_usd=m.market_cap_usd, cap_local=m.market_cap,
            local_currency=local_cap_currency(m),
            one_system=both_systems and bool(how) and "+" not in how,
            ranks=cells(_yahoo_key(m))))
    rows.sort(key=lambda r: (r.cap_usd is None, -(r.cap_usd or 0.0), r.ticker))
    subject = getattr(group, "subject", None)
    if columns and company_ticker and subject is not None:
        rows.insert(0, PeerRow(
            ticker=company_ticker, name=clean_company_name(subject.name or ""),
            exchange=exchange_name(subject.exchange or ""),
            sub_industry=getattr(subject, "classification", "") or "",
            cap_usd=subject.market_cap_usd, cap_local=subject.market_cap,
            local_currency=local_cap_currency(subject), is_company=True,
            ranks=cells(company_ticker.upper())))
    return rows


NAME_W = 36


def _fit_name(name: str, width: int = NAME_W) -> str:
    """The name for a fixed-width column: whole if it fits, else cut at a word boundary with an
    ellipsis - never mid-word ("InSilico Medicine Cayman Top")."""
    if len(name) <= width:
        return name
    cut = name[:width - 1].rsplit(" ", 1)[0].rstrip(" ,") or name[:width - 1]
    return cut + "…"


def _cell_text(value) -> str:
    return str(value)


def peer_text_lines(group, columns=None, company_ticker: str = "") -> list[str]:
    """The table as fixed-width text, for the ``.txt`` export and the CLI."""
    columns = list(columns or ())
    rows = peer_rows(group, columns, company_ticker)
    widths = [max(len(c.header), *(len(_cell_text(r.ranks[i][1])) for r in rows))
              for i, c in enumerate(columns)]
    # EXCHANGE-NAMES-1: a readable name ("Euronext Paris") is longer than the old code, so the column
    # widens to fit it (never below the old 7, which keeps a US-only table byte-identical).
    ex_w = max(7, *(len(r.exchange) for r in rows)) if rows else 7
    head = (f"{'Ticker':<26} {'Name':<{NAME_W}} {'Exch':<{ex_w}} {'Market cap (USD)':>17} {'Local':>15}  "
            + "".join(f"{c.header:>{w}}  " for c, w in zip(columns, widths)) + "Sub-industry")
    out = [head]
    for r in rows:
        out.append(f"{r.marked_ticker:<26} {_fit_name(r.name):<{NAME_W}} {r.exchange:<{ex_w}} "
                   f"{r.usd_text:>17} {r.local_text:>15}  "
                   + "".join(f"{_cell_text(cell):>{w}}  " for (_h, cell), w in zip(r.ranks, widths))
                   + r.sub_industry)
    if has_one_system_peers(rows):
        out.append(ONE_SYSTEM_NOTE)
    return out


# The page's table: numbers stay NUMBERS (so a column sorts by size, not alphabetically) and the
# display format is a Streamlit NumberColumn's. Values are in billions because a printf format
# cannot abbreviate; the static exports use ``PeerRow.usd_text`` / ``local_text`` instead.
USD_COLUMN = "Market cap (USD)"
LOCAL_COLUMN = "Market cap (local)"
USD_FORMAT = "$%.1fbn"
LOCAL_FORMAT = "%.1fbn"


# A rank column holds NUMBERS so it sorts by rank, not alphabetically; a company the lens did not rank
# is NaN (shown "does not apply", sorted last) or - for "no data" / "fetch failed" - a sentinel far above
# any rank, shown in words. ``rank_display`` turns either back into the words the exports print.
NO_DATA_SORT = 10 ** 6
_SENTINELS = {"no data": NO_DATA_SORT, "fetch failed": NO_DATA_SORT + 1,
              TOO_FEW_TO_RANK: NO_DATA_SORT + 2}


def _rank_number(value):
    """A rank column cell as a sortable number, or None ("does not apply" / not in this run)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return float(_SENTINELS[value]) if value in _SENTINELS else None


def rank_display(value) -> str:
    """The words a rank column cell reads as: "3", "does not apply", "no data"."""
    number = _rank_number(value)
    if number is None:
        return DOES_NOT_APPLY if value in (DOES_NOT_APPLY, None) else str(value)
    if number >= NO_DATA_SORT:
        return {NO_DATA_SORT: "no data", NO_DATA_SORT + 1: "fetch failed"}.get(
            int(number), TOO_FEW_TO_RANK)
    return f"{number:.0f}"


def peer_frame_records(group, columns=None, company_ticker: str = "") -> list[dict]:
    """Rows for ``st.dataframe``: numeric caps in BILLIONS, sorted by USD cap descending, the company
    first when there are rank columns, and one numeric column per lens (a check lens's mark is text)."""
    columns = list(columns or ())
    out = []
    for r in peer_rows(group, columns, company_ticker):
        rec = {"Ticker": r.marked_ticker, "Name": r.name, "Exchange": r.exchange,
               USD_COLUMN: None if r.cap_usd is None else r.cap_usd / 1e9,
               LOCAL_COLUMN: None if r.cap_local is None else r.cap_local / 1e9,
               "Currency": r.local_currency or "not stated"}
        for column, (header, cell) in zip(columns, r.ranks):
            rec[header] = _rank_number(cell) if column.kind == "rank" else str(cell)
        rec["Sub-industry"] = r.sub_industry
        out.append(rec)
    return out
