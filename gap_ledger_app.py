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
from aristos_council.gap_ledger.ledger import (GROUP_BASELINE, GROUP_CANDIDATE, LedgerRow,
                                               ledger_days, read_all, read_day)
from aristos_council.gap_ledger.outcomes import is_complete
from aristos_council.gap_ledger.score import CHECKPOINT_COLUMNS, continued, score

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


# --------------------------------------------------------------------------- #
# formatting — every value comes from the row; nothing is computed here
# --------------------------------------------------------------------------- #
def pct(value) -> str:
    return "—" if value is None else f"{value * 100:+.2f}%"


def ratio(value) -> str:
    return "—" if value is None else f"{value:.2f}x"


def money(value) -> str:
    return "—" if value is None else f"${value:,.2f}"


def spread(value) -> str:
    return "unknown" if value is None else f"{value * 100:.2f}%"


def relative_volume_cell(row: LedgerRow) -> str:
    """The ratio, or ``unavailable`` — never a dash that could read as "about zero".

    A candidate whose relative volume could not be read was selected on its GAP ALONE, and
    the table is where that has to be visible.
    """
    return "unavailable" if row.relative_volume is None else ratio(row.relative_volume)


def candidate_table(rows: list[LedgerRow]) -> list[dict]:
    """Today's list as a flat table. The link column is the headline's URL, so a claim is
    one click from the story it rests on."""
    return [{
        "Ticker": row.ticker,
        "Gap": pct(row.gap_pct),
        "Rel. pre-market volume": relative_volume_cell(row),
        "Prev. close": money(row.previous_close),
        "Pre-market": money(row.premarket_price),
        "Spread": spread(row.spread_pct),
        "Flags": " · ".join(f for f in (row.spread_note, row.relative_volume_note) if f),
        "News": row.news_found,
        "Headline": row.headline,
        "Link": row.news_link,
    } for row in rows]


def outcome_table(rows: list[LedgerRow]) -> list[dict]:
    """What the session did, per logged name, with the continuation answers spelled out.

    A blank continuation cell means NOT SCOREABLE — no direction or no price — which is
    deliberately distinct from "no". The scorecard counts it in neither column.
    """
    out = []
    for row in rows:
        record = {
            "Group": "candidate" if row.group == GROUP_CANDIDATE else "baseline",
            "Ticker": row.ticker,
            "Gap": pct(row.gap_pct),
            "Open": money(row.open_price),
            "10:00": money(row.price_1000),
            "11:30": money(row.price_1130),
            "Close": money(row.close_price),
        }
        for label, column in CHECKPOINT_COLUMNS:
            answer = continued(row, column)
            record[f"Carried on @ {label}"] = ("" if answer is None
                                               else ("yes" if answer else "no"))
        record["Note"] = row.outcome_note
        out.append(record)
    return out


def split(rows: list[LedgerRow]) -> tuple[list[LedgerRow], list[LedgerRow]]:
    return ([r for r in rows if r.group == GROUP_CANDIDATE],
            [r for r in rows if r.group == GROUP_BASELINE])


# --------------------------------------------------------------------------- #
# the day view, shared by "today" and "past days"
# --------------------------------------------------------------------------- #
def render_day(day: date, rows: list[LedgerRow], *, key_ns: str) -> None:
    candidates, baseline = split(rows)
    stamp = rows[0].run_at_et if rows else ""
    st.caption(f"Screened {day.isoformat()}"
               + (f", run at {stamp} (New York)" if stamp else ""))

    unavailable = [r for r in candidates if r.relative_volume is None]
    if unavailable:
        st.warning(f"Relative pre-market volume was unavailable for "
                   f"{len(unavailable)} of {len(candidates)} candidate(s) — "
                   f"{unavailable[0].relative_volume_note}. Those names were selected on "
                   f"their GAP ALONE.")

    if not candidates:
        st.info(f"No candidates on {day.isoformat()} — nothing gapped 3% on 3x pre-market "
                f"volume. An empty list is a result, not a failure.")
    else:
        st.subheader(f"{len(candidates)} candidate(s)")
        st.dataframe(candidate_table(candidates), width="stretch", hide_index=True,
                     column_config={"Link": st.column_config.LinkColumn("Link",
                                                                        display_text="story")})
        for row in candidates:
            if row.reason:
                st.markdown(f"**{row.ticker}** — {row.reason}")

    filled = [r for r in rows if is_complete(r)]
    with st.expander(f"Outcomes — {len(filled)} of {len(rows)} logged names filled in",
                     expanded=bool(filled)):
        if not rows:
            st.write("Nothing logged for this day.")
        elif not filled:
            st.info(f"Outcomes are filled after the close. Run `{OUTCOMES_HINT}`.")
        else:
            st.dataframe(outcome_table(rows), width="stretch", hide_index=True)

    with st.expander(f"Control group — {len(baseline)} name(s)"):
        st.caption("Drawn at random, with a seed fixed by the date, from the names that "
                   "passed the liquidity filter but NOT the gap-and-volume screen. It is "
                   "what the candidates are compared against; without it a carry-on rate "
                   "is a fact about the market, not about the screen.")
        if baseline:
            st.dataframe([{"Ticker": r.ticker, "Gap": pct(r.gap_pct),
                           "Rel. pre-market volume": relative_volume_cell(r),
                           "Why it did not qualify": r.screen_note} for r in baseline],
                         width="stretch", hide_index=True)
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

    today_tab, past_tab, score_tab = st.tabs(["Today", "Past days", "Scorecard"])
    with today_tab:
        render_today(days)
    with past_tab:
        render_past(days)
    with score_tab:
        render_scorecard()


if __name__ == "__main__":
    main()
