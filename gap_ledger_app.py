"""Gap Ledger — the read-only local viewer. ``streamlit run gap_ledger_app.py``.

A SEPARATE Streamlit entry point from Council Station (``app.py``), launched separately.
Nothing in the Aristos UI imports or links to this, and this imports nothing from
``app.py``: they share a repo, adapters and a ``.env``, and that is all. A pre-market gap
list is not a council verdict and putting them behind one set of tabs would suggest it was.

**This app never runs a screen.** It reads ``data/local/gap_ledger/*.csv`` and renders it.
The screen is a scheduled morning job with a real bill (news calls, optionally one LLM
call) and an outward-facing side effect (a Todoist task), and a browser button that fires
all three on a stray click is not a thing this version offers. The CLI is the trigger:

    python -m aristos_council.gap_ledger run
    python -m aristos_council.gap_ledger outcomes
    python -m aristos_council.gap_ledger score

Three views, matching the three questions the record can answer: what is on today's list,
what was on a past day's list and what happened to it, and — across every logged day —
whether the screen's names carried on more often than the control group.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import streamlit as st

from typing import Optional

from aristos_council.gap_ledger.config import DEFAULT_ROOT, now_ny
from aristos_council.gap_ledger.ledger import (GROUP_BASELINE, LedgerRow, ledger_days,
                                               read_all, read_day)
from aristos_council.gap_ledger.outcomes import is_complete
from aristos_council.gap_ledger.score import score
from aristos_council.gap_ledger.viewer import (HOW_TO_READ, NEWS_ANY, NEWS_CHOICES,
                                               RESULT_ANY, RESULT_CHOICES, VERIFIED_ANY,
                                               VERIFIED_CHOICES, apply_filters,
                                               candidates_of, checkpoint_markdown,
                                               company_of, day_summary, details_of,
                                               early_markdown, filter_caption, order_rows,
                                               path_markdown, row_flags,
                                               scorecard_progress, source_facts,
                                               table_markdown)
from aristos_council.gap_ledger.viewer import NEWLINE

ROOT = Path(__file__).resolve().parent


def ledger_root() -> Path:
    """Where to read the day files from: ``GAP_LEDGER_ROOT`` if it is set, else the default
    beside the repo.

    The override exists because the CLI already takes ``--root`` and a viewer that could
    only ever read one directory would not be able to look at a ledger kept elsewhere — and
    because it is what lets the tests drive this app over a fixture directory rather than
    over whatever happens to be on the developer's disk.
    """
    override = (os.environ.get("GAP_LEDGER_ROOT") or "").strip()
    return Path(override) if override else ROOT / DEFAULT_ROOT


LEDGER_ROOT = ledger_root()

RUN_HINT = "python -m aristos_council.gap_ledger run"
OUTCOMES_HINT = "python -m aristos_council.gap_ledger outcomes"


def hide_deploy_button() -> None:
    """Hide the Deploy button, and NOTHING else.

    Surgical on purpose. ``app.py`` carries the scar in writing: a past chrome-strip took out
    the toolbar menu (settings, theme) and the sidebar collapse toggle along with the cosmetic
    bits. Deploy is the only control here that does nothing for a local read-only viewer, so it
    is the only one hidden — and the others are forced visible in the same breath, so a stale
    stylesheet or a theme cannot take them away either.
    """
    st.markdown(
        """
        <style>
          [data-testid="stAppDeployButton"] {display: none !important;}
          /* Never lose the real controls. */
          [data-testid="stToolbar"], [data-testid="stMainMenu"], #MainMenu,
          [data-testid="stSidebarCollapseButton"],
          [data-testid="stSidebarCollapsedControl"],
          [data-testid="stExpandSidebarButton"] {visibility: visible !important;}
        </style>
        """,
        unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def company_names() -> dict:
    """``{ticker: company}`` from the market index, for CSVs written before the column.

    Cached because it reads a parquet table of several thousand rows and the answer does not
    change while the page is open. A missing index is an empty dict, not an error — the name
    is a label, and a viewer that refused to render without one would be worse than a blank
    cell.
    """
    try:
        from aristos_council.gap_ledger.universe import names_from_index

        return names_from_index()
    except Exception:                                    # no index, no pandas, no matter
        return {}


def render_banner(rows: list[LedgerRow]) -> None:
    """The data-source facts, ONCE, above the table (GAP-VIEWER-1 item 3).

    These are facts about the RUN — the gateway was down, the provider served no volume, the
    subscription does not cover quotes — and repeating them down a column is how a reader
    learns to stop looking at that column.
    """
    for fact in source_facts(rows):
        st.warning(fact)


def render_table(rows: list[LedgerRow], names: dict) -> None:
    """One row per stock, nine columns, in the specified order.

    Markdown rather than ``st.dataframe``: a dataframe's ``LinkColumn`` takes one
    ``display_text`` for every row, so the headline could not be both the visible text and the
    link. The trade is click-to-sort, which the fixed order replaces — IBKR-verified first,
    then the largest absolute gap.
    """
    st.markdown(table_markdown(rows, names))


def render_details(rows: list[LedgerRow], names: dict, *, key_ns: str) -> None:
    """Everything the table left out, one expander per stock."""
    if not rows:
        return
    st.caption("Details — spread, prints, IB's own readings, the checkpoints, the notes.")
    for row in rows:
        label = f"{row.ticker}"
        # The same looked-up name the table uses, so the expander and the row agree.
        company = company_of(row, names)
        if company:
            label += f" — {company}"
        flags = row_flags(row)
        if flags:
            label += f"   ({'; '.join(flags)})"
        with st.expander(label):
            pairs = details_of(row)
            if not pairs:
                st.write("Nothing beyond the table for this row.")
                continue
            left, right = st.columns(2)
            half = (len(pairs) + 1) // 2
            for column, chunk in ((left, pairs[:half]), (right, pairs[half:])):
                with column:
                    for name, value in chunk:
                        st.markdown(f"**{name}** · {value}")
            path = path_markdown(row)
            if path:
                st.markdown(path)


def render_day(day: date, rows: list[LedgerRow], *, key_ns: str,
               filters: Optional[dict] = None) -> None:
    """One logged day: the banner, the table, the details, then the control group."""
    names = company_names()
    every_candidate = candidates_of(rows)
    candidates = apply_filters(every_candidate, **(filters or {}))
    control = order_rows(r for r in rows if r.group == GROUP_BASELINE)
    stamp = rows[0].run_at_et if rows else ""
    st.caption(f"Screened {day.isoformat()}"
               + (f", run at {stamp} (New York)" if stamp else ""))

    render_banner(rows)

    if not every_candidate:
        st.info(f"No candidates on {day.isoformat()} — nothing cleared the gap and volume "
                f"screen. An empty list is a result, not a failure.")
    else:
        st.subheader(f"{len(every_candidate)} candidate(s)")
        # What the filters are hiding, said out loud: a table quietly showing three of
        # seventeen rows is a table that lies by omission.
        st.caption(filter_caption(len(candidates), len(every_candidate)))
        if candidates:
            render_table(candidates, names)
            render_details(candidates, names, key_ns=f"{key_ns}_cand")
        else:
            st.info("No candidate matches these filters. Widen them in the left panel.")

    filled = [r for r in rows if is_complete(r)]
    if rows and not filled:
        st.caption(f"Outcomes are not filled in for this day yet — run `{OUTCOMES_HINT}`.")

    with st.expander(f"Control group — {len(control)} name(s)"):
        st.caption("Drawn at random, with a seed fixed by the date, from the names that "
                   "passed the liquidity filter but NOT the gap-and-volume screen. It is "
                   "what the candidates are compared against; without it a carry-on rate "
                   "is a fact about the market, not about the screen.")
        if control:
            render_table(control, names)
        else:
            st.write("No control group — there were no candidates to match in size.")

    path = LEDGER_ROOT / f"{day.isoformat()}.csv"
    if path.exists():
        st.download_button("Download this day's CSV", path.read_bytes(),
                           file_name=path.name, mime="text/csv",
                           key=f"{key_ns}_download")


# --------------------------------------------------------------------------- #
# the three views
# --------------------------------------------------------------------------- #
def render_today(days: list[date], filters: Optional[dict] = None) -> None:
    today = now_ny().date()
    if today in days:
        render_day(today, read_day(today, LEDGER_ROOT), key_ns="today", filters=filters)
        return
    st.info(f"No screen logged for {today.isoformat()} (New York) yet. "
            f"Run `{RUN_HINT}`.")
    if days:
        st.caption(f"The most recent logged day is {days[-1].isoformat()} — it is in "
                   f"**Past days**.")


def render_past(days: list[date], filters: Optional[dict] = None, *,
                chosen: Optional[date] = None) -> None:
    """The day the left panel is pointing at. ONE day selector, in the panel — two of them
    would be two answers to the same question."""
    if not days:
        st.info(f"No days logged yet under `{DEFAULT_ROOT}`. Run `{RUN_HINT}`.")
        return
    day = chosen if chosen in days else days[-1]
    render_day(day, read_day(day, LEDGER_ROOT), key_ns="past", filters=filters)


def render_day_panel(days: list[date]) -> Optional[date]:
    """Section (a) — pick a logged day, newest first, and see that day's counts.

    Returns the chosen day so the tabs can follow the panel instead of keeping a second,
    separately-remembered idea of which day is on screen.
    """
    st.sidebar.subheader("Day")
    if not days:
        st.sidebar.caption("No days logged yet.")
        return None
    chosen = st.sidebar.selectbox("Logged days, newest first", list(reversed(days)),
                                 format_func=lambda d: d.isoformat(), key="panel_day",
                                 label_visibility="collapsed")
    counts = day_summary(read_day(chosen, LEDGER_ROOT))
    st.sidebar.markdown(
        f"**{chosen.isoformat()}**" + NEWLINE + NEWLINE
        + NEWLINE.join([
            f"- {counts['logged']} logged",
            f"- {counts['candidates']} candidate(s)",
            f"- {counts['ibkr_confirmed']} IBKR-confirmed",
            f"- {counts['unverified']} unverified",
            f"- {counts['with_news']} with news",
            f"- {counts['outcomes_filled']} with outcomes filled",
        ]))
    return chosen


def render_filter_panel() -> dict:
    """Section (b) — three filters, each over a column the reader can already see.

    A filter over something the table does not show would be a way to hide rows for reasons
    nobody can check, so there are exactly three and they match the Verified, News and Result
    columns.
    """
    st.sidebar.subheader("Filters")
    return {
        "verified": st.sidebar.radio("Verified", VERIFIED_CHOICES, key="filter_verified",
                                     horizontal=False),
        "news": st.sidebar.radio("News", NEWS_CHOICES, key="filter_news"),
        "result": st.sidebar.selectbox("Result", RESULT_CHOICES, key="filter_result"),
    }


def render_help_panel() -> None:
    """Section (c) — how to read the table, and the commands, both collapsed."""
    st.sidebar.subheader("How to read it")
    with st.sidebar.expander("What the columns mean"):
        for term, meaning in HOW_TO_READ:
            st.markdown(f"**{term}** — {meaning}")
    with st.sidebar.expander("Commands"):
        st.caption("This viewer is read-only: it never starts a screen, because that costs "
                   "news calls and posts a Todoist task. Copy one of these instead.")
        st.code(NEWLINE.join([RUN_HINT, OUTCOMES_HINT,
                              "python -m aristos_council.gap_ledger score"]),
                language="bash")


def render_scorecard() -> None:
    days = read_all(LEDGER_ROOT)
    if not days:
        st.info(f"No days logged yet under `{DEFAULT_ROOT}`. Run `{RUN_HINT}`.")
        return
    card = score(days)
    st.subheader("Did the move carry on?")
    st.caption("Measured from the OPEN, not from the previous close: the gap is already "
               "realized by the bell, so measuring from the previous close would count it "
               "twice. A flat reading is not a continuation.")

    if not card.enough_days:
        st.warning(card.verdict)
    else:
        st.success(card.verdict)

    # Item 5 — the distance to an answer as a NUMBER, not only as a refusal.
    st.markdown(f"**{scorecard_progress(card)}** scored so far.")
    st.progress(min(1.0, card.days_scored / max(1, card.min_days)))

    left, right = st.columns(2)
    left.metric("Days with filled outcomes", f"{card.days_scored} / {card.min_days}")
    right.metric("Names scored", f"{card.candidates} candidates · {card.baseline} baseline")

    if card.early is not None:
        st.subheader("Acting at the first signal vs acting at the open")
        st.caption("IBKR-verified candidates only, in the gap's direction. The first signal is "
                   "the first 5-minute bar already at the gap with volume well above the usual "
                   "for its time of day; its time is when that bar closed.")
        st.markdown(f"**{card.early.days} of {card.early.min_days} trading days** with a "
                    f"first signal · {card.early.paired} names")
        st.markdown(early_markdown(card.early))
        if card.early.enough_days:
            st.success(card.early.verdict)
        else:
            st.warning(card.early.verdict)

    if card.checkpoints:
        # The same Markdown shape and the same green/red as the day table, so the two tables
        # on this page read alike instead of looking like two different products.
        st.markdown(checkpoint_markdown(card))
    else:
        st.info(f"Days are logged but no outcome has been filled in yet. "
                f"Run `{OUTCOMES_HINT}`.")


# --------------------------------------------------------------------------- #
def main() -> None:
    st.set_page_config(page_title="Gap Ledger", page_icon="📈", layout="wide")
    st.title("Gap Ledger")
    st.caption("Pre-market movers on news, picked by maths and scored afterwards. "
               "No recommendations — this is a shortlist and a record, not advice.")

    hide_deploy_button()

    days = ledger_days(LEDGER_ROOT)
    st.sidebar.header("Gap Ledger")
    st.sidebar.caption(f"Reading `{DEFAULT_ROOT}` — {len(days)} day(s) logged.")
    chosen = render_day_panel(days)
    filters = render_filter_panel()
    render_help_panel()

    today_tab, past_tab, score_tab = st.tabs(["Today", "Past days", "Scorecard"])
    with today_tab:
        render_today(days, filters)
    with past_tab:
        render_past(days, filters, chosen=chosen)
    with score_tab:
        render_scorecard()


if __name__ == "__main__":
    main()
