"""GAP-VIEWER-1 — what the Gap Ledger viewer shows, as pure functions.

The viewer is read-only and never starts a run; this module is the part of it that has no
Streamlit in it at all, so every decision about what appears on the page is testable without
a browser. ``gap_ledger_app.py`` is left as the thin layer that calls these and draws.

Three rules shape it:

**One row per stock, and the row is short.** Ticker, company, gap, whether IB verified it,
relative volume, the headline, open, close, result. Everything else — spread, prints,
confirmation average, IB's bid/ask, the 10:00 and 11:30 prices, pre-market versus open, the
notes — belongs in a per-row details expander. A table with thirty columns is a table nobody
reads.

**A data-source fact is said ONCE.** "IBKR unavailable", "no pre-market volume from yfinance",
"spread not available on this subscription" are facts about the RUN, not about any name, and
repeating them down a column teaches the reader to ignore that column. They go in a banner
above the table; per-row flags keep only what is specific to that row.

**Old CSVs must render.** A file written before Batch 3 has no prints, one written before
GAP-IBKR-1 has no source and no company. Every reader here treats an absent column as absent
— blank, or looked up — and never as a zero or a failure.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional, Sequence

from .ledger import GROUP_CANDIDATE, LedgerRow
from .verify import SOURCE_IBKR

# What the Result column says. Measured FROM THE OPEN in the gap's direction — the same
# definition the scorecard uses (``score.continued``), with one display-only addition: the
# scorecard counts a flat close as "did not carry on" (the conservative reading), and here it
# is named "flat" rather than lumped in with a reversal, because the two mean different things
# to someone reading a day.
CARRIED_ON = "carried on"
REVERSED = "reversed"
FLAT = "flat"

VERIFIED_IBKR = "✓ IBKR"
UNVERIFIED = "unverified"

NO_NEWS = "no news"
NOT_MEASURED = "n/a"

# Data-source facts: true of the run, not of a name. Recognised by what the row carries rather
# than by re-deriving them, so the banner and the record can never disagree.
FACT_NO_YF_VOLUME = ("no pre-market volume from yfinance — relative volume was not measured, "
                     "so these names were selected on the gap alone")
FACT_NO_SPREAD = "spread not available on this subscription"

# Only http(s) links are rendered as links. A viewer that will follow whatever scheme a news
# feed hands it is a viewer with a hole in it.
_SAFE_SCHEME = re.compile(r"^https?://", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# per-row readings
# --------------------------------------------------------------------------- #
def company_of(row: LedgerRow, names: Optional[dict] = None) -> str:
    """The company name: the row's own if it has one, else looked up, else blank.

    A CSV written before the column existed carries nothing, so the viewer looks the name up
    in the market index — the same source the run would have filled it from. Blank is a
    perfectly good answer; it is never an error.
    """
    if row.company:
        return row.company
    return ((names or {}).get((row.ticker or "").strip().upper()) or "")


def verified_of(row: LedgerRow) -> str:
    """Did IB verify this name? Old CSVs have no ``source`` and read as unverified, which is
    true of them: nothing checked the volume."""
    return VERIFIED_IBKR if row.source == SOURCE_IBKR else UNVERIFIED


def relative_volume_of(row: LedgerRow) -> str:
    """``"39x"``, or ``"n/a"`` when the ratio was never measured."""
    if row.relative_volume is None:
        return NOT_MEASURED
    return f"{row.relative_volume:.0f}x"


def result_of(row: LedgerRow) -> str:
    """``carried on`` / ``reversed`` / ``flat``, or ``""`` before outcomes are filled.

    From the OPEN in the gap's direction, the scorecard's definition. Blank when there is no
    direction or no price — never a guess, because this column is the one a reader will
    remember.
    """
    direction = row.direction
    if direction == 0 or row.open_price is None or row.close_price is None:
        return ""
    move = (row.close_price - row.open_price) * direction
    if move > 0:
        return CARRIED_ON
    if move < 0:
        return REVERSED
    return FLAT


def headline_of(row: LedgerRow) -> tuple[str, str]:
    """``(text, url)`` for the headline cell. ``url`` is empty unless it is safe to link."""
    text = (row.headline or "").strip()
    url = (row.news_link or "").strip()
    if not text:
        return (NO_NEWS, "")
    return (text, url if _SAFE_SCHEME.match(url) else "")


# --------------------------------------------------------------------------- #
# the order
# --------------------------------------------------------------------------- #
def sort_key(row: LedgerRow):
    """IBKR-verified first, then the largest absolute gap.

    A verified name is the one whose number somebody checked, so it earns the top of the page;
    within each group the biggest move leads.
    """
    return (0 if row.source == SOURCE_IBKR else 1, -abs(row.gap_pct or 0.0), row.ticker)


def order_rows(rows: Iterable[LedgerRow]) -> list[LedgerRow]:
    return sorted(rows, key=sort_key)


def candidates_of(rows: Iterable[LedgerRow]) -> list[LedgerRow]:
    return order_rows(r for r in rows if r.group == GROUP_CANDIDATE)


# --------------------------------------------------------------------------- #
# said once
# --------------------------------------------------------------------------- #
def source_facts(rows: Sequence[LedgerRow]) -> list[str]:
    """The data-source facts for this day, each once, in the order a reader needs them.

    Read off what the rows CARRY rather than re-derived, so the banner and the record cannot
    disagree. Order is deliberate: the biggest caveat first.
    """
    facts: list[str] = []
    note = next((r.ibkr_note for r in rows if r.ibkr_note), "")
    if note:
        facts.append(note)

    screened = [r for r in rows if r.group == GROUP_CANDIDATE] or list(rows)
    unmeasured = [r for r in screened if r.relative_volume is None]
    if screened and len(unmeasured) == len(screened):
        facts.append(FACT_NO_YF_VOLUME)

    if any("subscription" in (r.spread_note or "") for r in rows):
        facts.append(FACT_NO_SPREAD)
    return facts


def row_flags(row: LedgerRow) -> list[str]:
    """Only what is specific to THIS row. The run-level facts live in the banner.

    A wide spread is about this name. "Spread unknown because of the subscription" is not, and
    repeating it down a column is how a reader learns to stop looking at that column.
    """
    flags: list[str] = []
    note = row.spread_note or ""
    if note.startswith("wide spread"):
        flags.append(note)
    if row.screen_note and row.source != SOURCE_IBKR:
        flags.append(row.screen_note)
    if row.news_found == "related, not matched":
        flags.append("news related but not matched")
    return flags


# --------------------------------------------------------------------------- #
# the table
# --------------------------------------------------------------------------- #
COLUMNS = ("Ticker", "Company", "Gap", "Verified", "Rel. volume", "Headline", "Open",
           "Close", "Result")


def _escape(text: str) -> str:
    """Text that is safe inside a Markdown table cell.

    ``|`` would end the cell and ``[``/``]`` would be read as link syntax — a headline is
    somebody else's prose and must not be able to restructure the page.
    """
    return (text or "").replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")


def _money(value: Optional[float]) -> str:
    return "—" if value is None else f"${value:,.2f}"


def gap_cell(value: Optional[float]) -> str:
    """The gap, coloured green up and red down with Streamlit's own colour syntax.

    ``:green[...]`` rather than HTML: it needs no ``unsafe_allow_html``, which is not a knob
    to turn on in a page that renders text fetched from a news feed.
    """
    if value is None:
        return "—"
    text = f"{value * 100:+.2f}%"
    return f":green[{text}]" if value > 0 else (f":red[{text}]" if value < 0 else text)


def headline_cell(row: LedgerRow) -> str:
    text, url = headline_of(row)
    safe = _escape(text)
    return f"[{safe}]({url})" if url else safe


def table_rows(rows: Sequence[LedgerRow], names: Optional[dict] = None) -> list[list[str]]:
    """One list of cells per stock, already in display order."""
    out = []
    for row in rows:
        out.append([
            row.ticker,
            _escape(company_of(row, names)),
            gap_cell(row.gap_pct),
            verified_of(row),
            relative_volume_of(row),
            headline_cell(row),
            _money(row.open_price),
            _money(row.close_price),
            result_of(row),
        ])
    return out


def table_markdown(rows: Sequence[LedgerRow], names: Optional[dict] = None) -> str:
    """The whole table as Markdown.

    Markdown rather than ``st.dataframe`` for one reason: a dataframe's ``LinkColumn`` takes a
    single ``display_text`` for every row, so the headline could not be both the visible text
    and the link. The cost is click-to-sort, which the fixed order above replaces.
    """
    lines = ["| " + " | ".join(COLUMNS) + " |",
             "|" + "|".join(["---"] * len(COLUMNS)) + "|"]
    for cells in table_rows(rows, names):
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# the details expander
# --------------------------------------------------------------------------- #
def details_of(row: LedgerRow) -> list[tuple[str, str]]:
    """Everything the table deliberately left out, as label/value pairs.

    Only what this row actually has: a pair whose value is missing is omitted rather than
    printed as a dash, so an old CSV's expander is short instead of mostly empty.
    """
    pairs: list[tuple[str, str]] = []

    def add(label: str, value, fmt=str) -> None:
        if value is None or value == "":
            return
        pairs.append((label, fmt(value)))

    add("Previous close", row.previous_close, _money)
    add("Pre-market price", row.premarket_price, _money)
    add("Pre-market volume", row.premarket_volume, lambda v: f"{int(v):,}")
    add("Baseline median volume", row.baseline_median_volume, lambda v: f"{v:,.0f}")
    add("Baseline sessions", row.baseline_sessions)
    add("Spread", row.spread_pct, lambda v: f"{v * 100:.2f}%")
    add("Spread note", row.spread_note)
    add("Pre-market prints", row.premarket_prints)
    add("Prints in the final 30 min", row.confirm_prints)
    add("Confirmation average", row.confirm_average, _money)
    add("Screen note", row.screen_note)
    add("Relative volume note", row.relative_volume_note)

    add("IB last price", row.ib_last_price, _money)
    add("IB gap", row.ib_gap_pct, lambda v: f"{v * 100:+.2f}%")
    add("IB pre-market volume", row.ib_premarket_volume, lambda v: f"{int(v):,}")
    add("IB baseline median", row.ib_baseline_median, lambda v: f"{v:,.0f}")
    add("IB relative volume", row.ib_relative_volume, lambda v: f"{v:.2f}x")
    add("IB bid", row.ib_bid, _money)
    add("IB ask", row.ib_ask, _money)

    add("10:00 ET", row.price_1000, _money)
    add("11:30 ET", row.price_1130, _money)
    add("Pre-market vs open", row.premarket_vs_open, lambda v: f"{v * 100:+.2f}%")
    add("Outcome note", row.outcome_note)
    add("News", row.news_found)
    add("News matched by", row.news_match)
    add("Related stories", row.related_count)
    add("Window", f"{row.window_start_et} → {row.window_end_et}"
        if row.window_start_et else "")
    return pairs


# --------------------------------------------------------------------------- #
# the day's summary
# --------------------------------------------------------------------------- #
def day_summary(rows: Sequence[LedgerRow]) -> dict:
    """The counts the left panel shows for one day. Derived from the rows alone."""
    candidates = [r for r in rows if r.group == GROUP_CANDIDATE]
    return {
        "logged": len(rows),
        "candidates": len(candidates),
        "ibkr_confirmed": sum(1 for r in candidates if r.source == SOURCE_IBKR),
        "unverified": sum(1 for r in candidates if r.source != SOURCE_IBKR),
        "with_news": sum(1 for r in candidates if r.news_found == "news found"),
        "outcomes_filled": sum(1 for r in rows if r.close_price is not None),
    }
