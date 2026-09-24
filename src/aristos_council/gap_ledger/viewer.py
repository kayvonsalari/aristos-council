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

# The row separator, spelled once as a constant and shared by both table builders, so the two
# Markdown tables on the page can never disagree about how a row ends.
NEWLINE = chr(10)


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


def _signed_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


# --------------------------------------------------------------------------- #
# GAP-EARLY-CHECKPOINT-1 — when the move first showed, and the price path from 04:00
# --------------------------------------------------------------------------- #
NO_EARLY_DATA = ("not available — the first signal needs IBKR pre-market volume, and this "
                 "row was not IBKR-verified")


def first_signal_of(row: LedgerRow) -> str:
    """``"05:35 ET at $52.10"``, or blank when there is none. The reason it is blank is a
    separate detail (``early_signal_note``), so a verified row says why and an unverified one
    says what is missing."""
    if row.first_signal_price is None or not row.first_signal_time_et:
        return "" if row.source == SOURCE_IBKR else NO_EARLY_DATA
    stamp = row.first_signal_time_et
    clock = stamp[11:16] if len(stamp) >= 16 else stamp
    return f"{clock} ET at {_money(row.first_signal_price)}"


def price_path(row: LedgerRow) -> list[tuple[str, Optional[float]]]:
    """The name's price from 04:00 to the close, in order. A moment with no reading is None —
    a gap in the path, never a price borrowed from a neighbour."""
    return [("04:00", row.price_0400), ("06:00", row.price_0600), ("07:00", row.price_0700),
            ("08:00", row.price_0800), ("09:00", row.price_0900),
            ("09:30 open", row.open_price), ("10:00", row.price_1000),
            ("11:30", row.price_1130), ("Close", row.close_price)]


def path_markdown(row: LedgerRow) -> str:
    """The price path as a two-row table: the price, and where it stood against the previous
    close. Empty when the row has no point on the path at all, so an old CSV shows nothing
    rather than a row of dashes."""
    points = price_path(row)
    if all(price is None for _label, price in points):
        return ""
    against = []
    for _label, price in points:
        if price is None or not row.previous_close or row.previous_close <= 0:
            against.append("—")
        else:
            against.append(f"{(price - row.previous_close) / row.previous_close * 100:+.2f}%")
    lines = ["| " + " | ".join(["Price path"] + [label for label, _ in points]) + " |",
             "|" + "|".join(["---"] * (len(points) + 1)) + "|",
             "| Price | " + " | ".join(_money(price) for _label, price in points) + " |",
             "| vs previous close | " + " | ".join(against) + " |"]
    return NEWLINE.join(lines)


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

    add("First signal", first_signal_of(row))
    add("First-signal note", row.early_signal_note)
    add("Move from first signal to 09:00", row.signal_move_0900, _signed_pct)
    add("Move from first signal to the open", row.signal_move_open, _signed_pct)
    add("Move from first signal to the close", row.signal_move_close, _signed_pct)
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
# the filters (left panel, section b)
# --------------------------------------------------------------------------- #
# Deliberately three, each matching a COLUMN the reader can already see. A filter over
# something the table does not show would be a way to hide rows for reasons nobody can check.
VERIFIED_ANY, VERIFIED_ONLY, UNVERIFIED_ONLY = "Any", "IBKR-verified", "Unverified"
NEWS_ANY, NEWS_WITH, NEWS_WITHOUT = "Any", "With news", "Without news"
RESULT_ANY, RESULT_PENDING = "Any", "not filled in yet"

VERIFIED_CHOICES = (VERIFIED_ANY, VERIFIED_ONLY, UNVERIFIED_ONLY)
NEWS_CHOICES = (NEWS_ANY, NEWS_WITH, NEWS_WITHOUT)
RESULT_CHOICES = (RESULT_ANY, CARRIED_ON, REVERSED, FLAT, RESULT_PENDING)


def apply_filters(rows: Sequence[LedgerRow], *, verified: str = VERIFIED_ANY,
                  news: str = NEWS_ANY, result: str = RESULT_ANY) -> list[LedgerRow]:
    """The rows a reader asked to see. Pure, so the filtering is testable without a browser.

    An unrecognised choice filters NOTHING rather than everything: a typo in a widget must not
    silently empty the page.
    """
    kept = list(rows)
    if verified == VERIFIED_ONLY:
        kept = [r for r in kept if r.source == SOURCE_IBKR]
    elif verified == UNVERIFIED_ONLY:
        kept = [r for r in kept if r.source != SOURCE_IBKR]
    if news == NEWS_WITH:
        kept = [r for r in kept if r.news_found == "news found"]
    elif news == NEWS_WITHOUT:
        kept = [r for r in kept if r.news_found != "news found"]
    if result == RESULT_PENDING:
        kept = [r for r in kept if not result_of(r)]
    elif result in (CARRIED_ON, REVERSED, FLAT):
        kept = [r for r in kept if result_of(r) == result]
    return kept


def filter_caption(shown: int, total: int) -> str:
    """What the filters are hiding, said out loud.

    A table that quietly shows three of seventeen rows is a table that lies by omission, so the
    count is always on the page when a filter is doing anything.
    """
    if shown == total:
        return f"Showing all {total} candidate(s)."
    return f"Showing {shown} of {total} candidate(s) — filters are hiding {total - shown}."


# --------------------------------------------------------------------------- #
# how to read it (left panel, section c)
# --------------------------------------------------------------------------- #
# Short, and about the things that are easy to misread rather than the things that are obvious.
HOW_TO_READ = (
    ("Gap", "Last pre-market price against the previous DIVIDEND- AND SPLIT-ADJUSTED close. "
            "Green up, red down."),
    ("Verified", "✓ IBKR means Interactive Brokers confirmed the move against real "
                 "pre-market volume, and its price and volume replaced yfinance's. "
                 "*Unverified* means nothing measured the volume."),
    ("Rel. volume", "Today's pre-market volume against the median of the same clock window "
                    "over the prior 20 sessions. **n/a** means it was never measured — not "
                    "that it was low."),
    ("Result", "From the OPEN in the gap's direction, at the close: did the move carry on, "
               "reverse, or end flat. Blank until `outcomes` has been run for that day."),
    ("Control group", "An equal-size sample of names that passed the liquidity filter but "
                      "NOT the screen, drawn with a seed fixed by the date. It is what the "
                      "candidates are compared against."),
    ("No recommendation", "Maths picks the names. Nothing here is advice, and an empty list "
                          "is a result rather than a failure."),
)


# --------------------------------------------------------------------------- #
# the scorecard's progress (item 5)
# --------------------------------------------------------------------------- #
def scorecard_progress(card) -> str:
    """``"12 of 40 trading days"`` — how far the record is from being able to answer.

    The scorecard's own verdict already refuses to call anything a finding below the floor;
    this is the same fact as a number, so the reader can see the distance rather than infer it.
    """
    return f"{card.days_scored} of {card.min_days} trading days"


def points_cell(edge) -> str:
    """An edge in points, coloured the same way the gap is — so the two tables read alike."""
    if edge is None:
        return "—"
    text = f"{edge * 100:+.0f} points"
    return f":green[{text}]" if edge > 0 else (f":red[{text}]" if edge < 0 else text)


CHECKPOINT_COLUMNS_VIEW = ("Checkpoint", "Candidates", "Control group", "Edge")


def checkpoint_markdown(card) -> str:
    """The scorecard's per-checkpoint table, in the same Markdown shape as the day table."""
    lines = ["| " + " | ".join(CHECKPOINT_COLUMNS_VIEW) + " |",
             "|" + "|".join(["---"] * len(CHECKPOINT_COLUMNS_VIEW)) + "|"]
    for check in card.checkpoints:
        lines.append("| " + " | ".join([_escape(check.label),
                                        _escape(check.candidates.sentence()),
                                        _escape(check.baseline.sentence()),
                                        points_cell(check.edge)]) + " |")
    return NEWLINE.join(lines)


EARLY_COLUMNS = ("Entry", "Exit", "Names", "Mean", "Median", "Up")


def early_markdown(early) -> str:
    """The first-signal-versus-open comparison as a Markdown table; empty when absent."""
    if early is None:
        return ""
    lines = ["| " + " | ".join(EARLY_COLUMNS) + " |",
             "|" + "|".join(["---"] * len(EARLY_COLUMNS)) + "|"]
    for entry, rows in (("First signal", early.at_signal), ("The open", early.at_open)):
        for label, stats in rows:
            if stats.n == 0:
                cells = ["0", "—", "—", "—"]
            else:
                cells = [str(stats.n), _signed_pct(stats.mean), _signed_pct(stats.median),
                         f"{stats.positive} of {stats.n}"]
            lines.append("| " + " | ".join([entry, _escape(label)] + cells) + " |")
    return NEWLINE.join(lines)


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
