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

from aristos_council.gap_ledger.config import DEFAULT_ROOT, now_ny
from aristos_council.gap_ledger.ledger import (GROUP_BASELINE, LedgerRow, ledger_days,
                                               read_all, read_day)
from aristos_council.gap_ledger.outcomes import is_complete
from aristos_council.gap_ledger.score import score
from aristos_council.gap_ledger.viewer import (candidates_of, company_of, day_summary,
                                               details_of, order_rows, row_flags,
                                               source_facts, table_markdown)

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


def render_day(day: date, rows: list[LedgerRow], *, key_ns: str) -> None:
    """One logged day: the banner, the table, the details, then the control group."""
    names = company_names()
    candidates = candidates_of(rows)
    control = order_rows(r for r in rows if r.group == GROUP_BASELINE)
    stamp = rows[0].run_at_et if rows else ""
    st.caption(f"Screened {day.isoformat()}"
               + (f", run at {stamp} (New York)" if stamp else ""))

    render_banner(rows)

    if not candidates:
        st.info(f"No candidates on {day.isoformat()} — nothing cleared the gap and volume "
                f"screen. An empty list is a result, not a failure.")
    else:
        st.subheader(f"{len(candidates)} candidate(s)")
        render_table(candidates, names)
        render_details(candidates, names, key_ns=f"{key_ns}_cand")

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
def render_today(days: list[date]) -> None:
    today = now_ny().date()
    if today in days:
        render_day(today, read_day(today, LEDGER_ROOT), key_ns="today")
        return
    st.info(f"No screen logged for {today.isoformat()} (New York) yet. "
            f"Run `{RUN_HINT}`.")
    if days:
        st.caption(f"The most recent logged day is {days[-1].isoformat()} — it is in "
                   f"**Past days**.")


def render_past(days: list[date]) -> None:
    if not days:
        st.info(f"No days logged yet under `{DEFAULT_ROOT}`. Run `{RUN_HINT}`.")
        return
    chosen = st.selectbox("Day", list(reversed(days)),
                          format_func=lambda d: d.isoformat(), key="past_day")
    render_day(chosen, read_day(chosen, LEDGER_ROOT), key_ns="past")


def render_day_panel(days: list[date]) -> None:
    """The left panel's Day section: pick a logged day, newest first, and see its counts."""
    if not days:
        st.sidebar.caption("No days logged yet.")
        return
    chosen = st.sidebar.selectbox("Day", list(reversed(days)),
                                 format_func=lambda d: d.isoformat(), key="panel_day")
    counts = day_summary(read_day(chosen, LEDGER_ROOT))
    st.sidebar.markdown(
        f"**{chosen.isoformat()}**\n\n"
        f"- {counts['logged']} logged\n"
        f"- {counts['candidates']} candidate(s)\n"
        f"- {counts['ibkr_confirmed']} IBKR-confirmed\n"
        f"- {counts['unverified']} unverified\n"
        f"- {counts['with_news']} with news\n"
        f"- {counts['outcomes_filled']} with outcomes filled")


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

    left, right = st.columns(2)
    left.metric("Days with filled outcomes", f"{card.days_scored} / {card.min_days}")
    right.metric("Names scored", f"{card.candidates} candidates · {card.baseline} baseline")

    if card.checkpoints:
        st.dataframe([{
            "Checkpoint": c.label,
            "Candidates": c.candidates.sentence(),
            "Control group": c.baseline.sentence(),
            "Edge": "—" if c.edge is None else f"{c.edge * 100:+.0f} points",
        } for c in card.checkpoints], width="stretch", hide_index=True)
    else:
        st.info(f"Days are logged but no outcome has been filled in yet. "
                f"Run `{OUTCOMES_HINT}`.")


# --------------------------------------------------------------------------- #
def main() -> None:
    st.set_page_config(page_title="Gap Ledger", page_icon="📈", layout="wide")
    st.title("Gap Ledger")
    st.caption("Pre-market movers on news, picked by maths and scored afterwards. "
               "No recommendations — this is a shortlist and a record, not advice.")

    days = ledger_days(LEDGER_ROOT)
    st.sidebar.header("Gap Ledger")
    st.sidebar.caption(f"Reading `{DEFAULT_ROOT}` — {len(days)} day(s) logged.")
    st.sidebar.info("This viewer is read-only. It never starts a screen: that costs news "
                    "calls and posts a Todoist task, so it stays a deliberate command.")
    st.sidebar.code(f"{RUN_HINT}\n{OUTCOMES_HINT}\n"
                    f"python -m aristos_council.gap_ledger score", language="bash")
    render_day_panel(days)

    today_tab, past_tab, score_tab = st.tabs(["Today", "Past days", "Scorecard"])
    with today_tab:
        render_today(days)
    with past_tab:
        render_past(days)
    with score_tab:
        render_scorecard()


if __name__ == "__main__":
    main()
