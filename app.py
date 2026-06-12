"""Council Station — a local Streamlit UI over the Aristos Council.

Launch:
    pip install -e ".[ui,yfinance,llm]"
    streamlit run app.py

Browsing past runs needs only ".[ui]". LAUNCHING a council additionally needs
the runtime deps (".[yfinance,llm]") and the API keys it reads from the
environment or a local .env (ANTHROPIC_API_KEY, optionally FINNHUB_API_KEY).

Billing note: a council run bills real API credits — this app is meant to run
on a machine that holds the runtime keys, NEVER inside the subscription-only
Claude Code dev environment. The sidebar gates every run behind an explicit
cost acknowledgement for exactly this reason.

The council itself is imported and invoked IN-PROCESS (not shelled out), and the
graph stays disk-free: this edge loads the prior verdict before the run and
writes the verdict log + full run report after it, mirroring run_council.py.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
from pydantic import ValidationError

from aristos_council.data.adapter import DataUnavailable
from aristos_council.persistence.reports import (
    RunReport,
    list_reports,
    load_report,
    report_from_state,
    save_report,
)
from aristos_council.persistence.verdicts import (
    append_record,
    load_latest,
    load_records,
    record_from_state,
)
from aristos_council.state import Stance
from aristos_council.strategy.loader import Strategy, load_strategy
from aristos_council.strategy.versioning import (
    bump_version,
    make_new_version,
    save_strategy,
)

ROOT = Path(__file__).resolve().parent
STRATEGIES_DIR = ROOT / "strategies"
VERDICTS_DIR = ROOT / "verdicts"
REPORTS_DIR = ROOT / "reports"

COMING_SOON = "Dividend + Growth — coming soon"

# Timestamps are STORED in UTC everywhere; the UI converts to this zone for
# DISPLAY only. Storage and persisted records never change.
DISPLAY_TZ = ZoneInfo("Europe/Berlin")


def _to_local(dt: datetime) -> datetime:
    """A UTC-stored timestamp in the display timezone. Naive == UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(DISPLAY_TZ)


def _fmt_local(dt: datetime, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format a timestamp in the display timezone, tagged with its tz abbrev."""
    return _to_local(dt).strftime(f"{fmt} %Z")


def _local_label_from_slug(stem: str) -> str:
    """Render a report filename slug (UTC, '%Y-%m-%dT%H-%M-%SZ') in local time."""
    try:
        dt = datetime.strptime(stem, "%Y-%m-%dT%H-%M-%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return stem  # unrecognised slug — show it raw rather than hide the run
    return _fmt_local(dt, "%Y-%m-%d %H:%M:%S")


def _ts_header(dt: datetime) -> str:
    """Identity-header timestamp: European dotted, local — '12.06.2026 15:42'."""
    return _to_local(dt).strftime("%d.%m.%Y %H:%M")


def _ts_compact(dt: datetime) -> str:
    """Compact local timestamp for dense labels — '12.06. 15:42'."""
    return _to_local(dt).strftime("%d.%m. %H:%M")


# Provenance plumbing in PROSE: agents inline citations like
# "(call_id: 8d39404e0e90, criteria[0].observed)" or "[call_id 1db8...]". The
# STORED text keeps them (auditability); display strips them by default and the
# provenance toggle shows the raw, unstripped text. Handles one level of nested
# parens (a quoted headline that itself contains '(...)').
_CALLID_PAREN_RE = re.compile(r"\s*\(\s*call_id\b(?:[^()]|\([^()]*\))*\)")
_CALLID_BRACKET_RE = re.compile(r"\s*\[\s*call_id\b[^\]]*\]")


def strip_provenance(text: str) -> str:
    """Remove inline call_id parentheticals from prose for clean display.

    Display-only: never applied to stored data. The provenance toggle bypasses
    this and shows the raw text (call_ids and field references intact)."""
    if not text:
        return text
    out = _CALLID_PAREN_RE.sub("", text)
    out = _CALLID_BRACKET_RE.sub("", out)
    out = re.sub(r"\s{2,}", " ", out)            # collapse doubled spaces
    out = re.sub(r"\s+([.,;:])", r"\1", out)     # no space before punctuation
    return out.strip()


def _prose(text: str, show_provenance: bool) -> str:
    """Prose for display: raw under the provenance toggle, stripped otherwise."""
    return text if show_provenance else strip_provenance(text)


# Stance display helpers ------------------------------------------------------ #
_STANCE_BADGE = {
    Stance.BULLISH: "🟢 bullish",
    Stance.NEUTRAL: "🟡 neutral",
    Stance.BEARISH: "🔴 bearish",
    Stance.ABSTAIN: "⚪ abstain",
}


def _stance_badge(stance: Stance) -> str:
    return _STANCE_BADGE.get(stance, str(stance))


def list_strategy_options(strategies_dir: Path) -> list[tuple[str, Path, Strategy]]:
    """Every loadable strategy YAML as (label, path, strategy), id-sorted.

    Invalid YAMLs are skipped silently here — the loader is the gatekeeper, and a
    half-written file shouldn't break the picker.
    """
    out: list[tuple[str, Path, Strategy]] = []
    for p in sorted(strategies_dir.glob("*.yaml")):
        try:
            s = load_strategy(p)
        except Exception:
            continue
        out.append((f"{s.name} (live) · {s.id}", p, s))
    return out


# --------------------------------------------------------------------------- #
# Running the council in-process
# --------------------------------------------------------------------------- #
def run_council(ticker: str, strategy_path: Path) -> RunReport:
    """Invoke the council for one ticker and persist both sinks at the edge.

    Runtime imports (yfinance/langchain) are lazy so merely browsing past runs
    never requires the runtime extras to be installed.
    """
    import os

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")  # no-op if absent; never overrides real env vars

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Put it in the environment or a "
            "local .env before running the council."
        )

    from aristos_council.agents.runners import production_runners
    from aristos_council.data.yfinance_adapter import YFinanceAdapter
    from aristos_council.graph import build_council
    from aristos_council.state import ResearchState

    strategy = load_strategy(strategy_path)

    sentiment = None
    if os.environ.get("FINNHUB_API_KEY"):
        from aristos_council.data.finnhub_adapter import FinnhubAdapter
        sentiment = FinnhubAdapter()

    app = build_council(YFinanceAdapter(), strategy, production_runners(),
                        sentiment_adapter=sentiment)

    prior = load_latest(ticker, VERDICTS_DIR)
    initial = ResearchState(
        ticker=ticker,
        strategy_id=strategy.id,
        prior_recommendation=prior.verdict if prior else None,
    )

    # Stream the graph so the UI can show per-stage progress for free: each
    # "values" chunk is the full state after a node, which we label by what it
    # has populated so far.
    progress = st.progress(0.0, text="Gathering evidence…")
    final: dict | None = None
    STAGES = 7  # gather + 4 specialists + critic + decision (audit/veto are fast)
    for i, chunk in enumerate(app.stream(initial, stream_mode="values"), start=1):
        final = chunk
        progress.progress(min(i / STAGES, 1.0), text=_stage_label(chunk))
    progress.progress(1.0, text="Done.")

    result = ResearchState.model_validate(final)

    # Friendly fail on a ticker the adapter couldn't supply (bad symbol,
    # delisted). gather records adapter failures as failed tool calls rather
    # than raising, so detect a failed CORE fundamentals fetch here and raise
    # DataUnavailable — nothing meaningful was deliberated, so do NOT persist a
    # degenerate verdict/report. The UI maps this to a friendly message.
    fundamentals_tc = next(
        (tc for tc in result.tool_calls if tc.tool_name == "get_fundamentals"),
        None,
    )
    if fundamentals_tc is None or not fundamentals_tc.ok:
        raise DataUnavailable(
            fundamentals_tc.error if fundamentals_tc
            else f"no fundamentals fetched for {ticker}"
        )

    append_record(record_from_state(result), VERDICTS_DIR)
    report = report_from_state(result)
    save_report(report, REPORTS_DIR)
    return report


def _friendly_error(exc: Exception, ticker: str) -> str | None:
    """Map a run exception to a friendly UI message, or None to fall back to a
    full traceback. DataUnavailable (bad/delisted ticker) is the expected,
    user-actionable case; anything else is unexpected and shown in full."""
    if isinstance(exc, DataUnavailable):
        return f"No data found for {ticker} — check the symbol."
    return None


def _stage_label(chunk: dict) -> str:
    """A human progress label derived from how far the state has filled in."""
    if chunk.get("decision"):
        return "Decision issued — auditing…"
    if chunk.get("critic_report"):
        return "Critic deliberating…"
    ops = chunk.get("specialist_opinions") or []
    if ops:
        return f"{len(ops)} of 4 specialists reported…"
    if chunk.get("tool_calls"):
        return "Evidence gathered — specialists deliberating…"
    return "Gathering evidence…"


# --------------------------------------------------------------------------- #
# Report rendering (shared by fresh runs and browsing past runs)
# --------------------------------------------------------------------------- #
def _run_label(report: RunReport) -> str:
    """Dense one-line label for the run selector: 'MO · 12.06. 15:42 · HOLD 0.55'."""
    d = report.decision
    verdict = f"{d.recommendation.value.upper()} {d.confidence:.2f}" if d else "—"
    return f"{report.ticker} · {_ts_compact(report.run_at)} · {verdict}"


def _figures_table(figures, show_provenance: bool = False) -> None:
    """Render provenance-bound figures as a table.

    Default columns are label / value / unit / source field / tool — the
    auditable provenance a reader needs. The call_id (pure plumbing) is shown
    only when the per-report provenance toggle is on. Mirrors run_council.py.
    """
    if not figures:
        return
    rows = []
    for fig in figures:
        row = {
            "label": fig.label,
            "value": fig.value,
            "unit": fig.unit,
            "field": fig.provenance.field_path,
            "tool": fig.provenance.tool_name,
        }
        if show_provenance:
            row["call_id"] = fig.provenance.call_id
        rows.append(row)
    st.dataframe(rows, hide_index=True, use_container_width=True)


def _render_report_header(report: RunReport, sidebar_ticker: str | None) -> None:
    """Persistent identity header: ticker (large), company, strategy, timestamp.

    Cannot scroll out of ambiguity — it sits at the top of every rendered
    report, and ticker+timestamp are repeated as a caption above the decision.
    """
    name = f" — {report.company_name}" if report.company_name else ""
    with st.container(border=True):
        st.markdown(f"## {report.ticker}{name}")
        st.caption(
            f"{report.strategy_id} · {_ts_header(report.run_at)} · Europe/Berlin"
        )
        if sidebar_ticker and sidebar_ticker != report.ticker:
            # Prevent wrong-company misreads when the sidebar has moved on.
            st.caption(
                f"⚠ Viewing **{report.ticker}** — sidebar is set to "
                f"**{sidebar_ticker}**"
            )


def render_report(
    report: RunReport, sidebar_ticker: str | None = None, key_ns: str = "report"
) -> None:
    """Render a full run report. The deliberation is the product: everything
    examples/run_council.py prints to the console appears here too."""
    _render_report_header(report, sidebar_ticker)

    # Per-report provenance toggle (off by default): shows call_ids in the
    # figures tables and the RAW, unstripped prose (inline citations intact).
    run_uid = _to_local(report.run_at).strftime("%Y%m%d%H%M%S")
    show_prov = st.toggle(
        "Show provenance details",
        value=False,
        key=f"prov_{key_ns}_{report.ticker}_{run_uid}",
        help="Reveal call_ids and inline field references for auditing.",
    )

    _render_verdict_banner(report)

    # Human-review flags — prominent, directly under the banner.
    if report.veto_flags:
        st.error(
            "**⚠ Human review required** — "
            f"{len(report.veto_flags)} veto trigger(s) fired."
        )
        for f in report.veto_flags:
            st.markdown(f"- **{f.trigger.value}** — {f.detail}")
    else:
        st.success("No veto triggers — auto-proceed permitted.")

    # --- Decision -------------------------------------------------------- #
    st.divider()
    st.caption(f"{report.ticker} · {_ts_header(report.run_at)}")  # stay oriented
    if report.decision:
        st.subheader("Decision rationale")
        st.markdown(_prose(report.decision.rationale, show_prov) or "_(no rationale)_")
        if report.decision.dissent:
            st.markdown(
                "**Dissent recorded:** "
                + ", ".join(s.value for s in report.decision.dissent)
            )
        else:
            st.caption("No dissent recorded.")

    # --- Specialists ----------------------------------------------------- #
    st.divider()
    st.subheader("Specialists")
    for op in report.specialist_opinions:
        with st.expander(
            f"{_stance_badge(op.stance)}  {op.specialist.value.title()} "
            f"· confidence {op.confidence:.2f}"
        ):
            st.markdown(_prose(op.thesis, show_prov) or "_(no thesis)_")
            _figures_table(op.figures, show_prov)
            if op.caveats:
                st.markdown("**Caveats**")
                for c in op.caveats:
                    st.markdown(f"- ⚠ {_prose(c, show_prov)}")

    # --- Critic ---------------------------------------------------------- #
    if report.critic_report:
        cr = report.critic_report
        st.divider()
        st.subheader(
            f"Critic — arguing against the {cr.targets_stance.value} consensus"
        )
        st.markdown(_prose(cr.counter_thesis, show_prov) or "_(no counter-thesis)_")
        _figures_table(cr.figures, show_prov)
        if cr.challenged_figures:
            st.markdown("**Challenged figures** (cited by the council, contested)")
            for cf in cr.challenged_figures:
                st.markdown(f"- {_prose(cf, show_prov)}")
        if cr.weaknesses_found:
            st.markdown("**Weaknesses found**")
            for w in cr.weaknesses_found:
                st.markdown(f"- {_prose(w, show_prov)}")
        if cr.open_questions:
            st.markdown(
                "**Open questions** (for human resolution — not evidence)"
            )
            for q in cr.open_questions:
                st.markdown(f"- {_prose(q, show_prov)}")

    # --- Audit (call_ids always — that is its job) ----------------------- #
    _render_provenance_panel(report.provenance_audit)


def _render_verdict_banner(report: RunReport) -> None:
    d = report.decision
    verdict = d.recommendation.value.upper() if d else "—"
    conf = f"{d.confidence:.2f}" if d else "—"
    col1, col2, col3 = st.columns([2, 1, 2])
    col1.metric("Verdict", verdict)
    col2.metric("Confidence", conf)
    col3.metric("Run", _fmt_local(report.run_at))


def _render_provenance_panel(audit: dict | None) -> None:
    if not audit:
        return
    st.divider()
    st.subheader("Provenance audit")
    cols = st.columns(6)
    for col, key in zip(
        cols,
        ("figures_audited", "verified", "mismatch",
         "unresolvable", "unverifiable", "unit_scaled"),
    ):
        col.metric(key.replace("_", " "), audit.get(key, 0))
    violations = audit.get("violations") or []
    if violations:
        st.markdown("**Violations** (feed the DATA_QUALITY veto)")
        for v in violations:
            st.markdown(f"- {v}")
    notes = audit.get("unit_scaled_notes") or []
    if notes:
        st.markdown("**Unit-scaled notes** (reported, not veto-firing)")
        for n in notes:
            st.markdown(f"- {n}")


# --------------------------------------------------------------------------- #
# History rendering
# --------------------------------------------------------------------------- #
def render_history(ticker: str) -> None:
    records = load_records(ticker, VERDICTS_DIR)
    if not records:
        st.info(f"No verdict history for {ticker} yet.")
        return

    import altair as alt
    import pandas as pd

    st.subheader(f"{ticker} — verdict & confidence across runs")
    st.caption("Timestamps shown in Europe/Berlin (stored in UTC).")
    # Runs are sparse and irregular, so treat them as ordered discrete EVENTS
    # (#1, #2, … with date labels), not a continuous time axis — a real time
    # axis would render mostly empty space between clustered runs.
    chart_df = pd.DataFrame(
        [
            {
                "run_idx": i,
                "run_label": f"#{i} · {_to_local(r.run_at).strftime('%Y-%m-%d')}",
                "verdict": r.verdict.value.upper() if r.verdict else None,
                "confidence": r.confidence,
            }
            for i, r in enumerate(records, start=1)
        ]
    )
    run_order = chart_df["run_label"].tolist()  # already in chronological order
    x = alt.X("run_label:N", sort=run_order, title="Run",
              axis=alt.Axis(labelAngle=0))
    tooltip = ["run_label", "verdict", "confidence"]

    # Verdict: a stepped categorical line with SELL/HOLD/BUY as labelled levels
    # (BUY on top). Points carry the signal when there are only a few runs.
    verdict_panel = (
        alt.Chart(chart_df)
        .mark_line(interpolate="step-after", point=alt.OverlayMarkDef(size=90))
        .encode(
            x=x,
            y=alt.Y("verdict:N", sort=["BUY", "HOLD", "SELL"], title="Verdict",
                    scale=alt.Scale(domain=["BUY", "HOLD", "SELL"])),
            tooltip=tooltip,
        )
        .properties(height=170, title="Verdict")
    )
    # Confidence: its own 0–1 axis, so verdict levels never read as a flat line
    # pinned to the bottom of a shared scale.
    confidence_panel = (
        alt.Chart(chart_df)
        .mark_line(point=alt.OverlayMarkDef(size=90))
        .encode(
            x=x,
            y=alt.Y("confidence:Q", title="Confidence",
                    scale=alt.Scale(domain=[0, 1])),
            tooltip=tooltip,
        )
        .properties(height=170, title="Confidence")
    )
    chart = alt.vconcat(verdict_panel, confidence_panel).resolve_scale(
        x="shared")
    st.altair_chart(chart, use_container_width=True)

    st.subheader("Runs")
    runs_df = pd.DataFrame(
        [
            {
                "run (Europe/Berlin)": _fmt_local(r.run_at, "%Y-%m-%d %H:%M:%S"),
                "verdict": r.verdict.value if r.verdict else "—",
                "confidence": r.confidence,
                "strategy": r.strategy_id,
                "vetoes": ", ".join(t.value for t in r.veto_triggers) or "—",
            }
            for r in records
        ]
    ).set_index("run (Europe/Berlin)")
    st.dataframe(runs_df, use_container_width=True)

    st.subheader("Specialist stance across runs")
    st.caption(
        "Reads down a column to spot drift — e.g. whether Technical has been "
        "sliding toward neutral run over run."
    )
    stance_rows = []
    for r in records:
        row = {"run (Europe/Berlin)": _fmt_local(r.run_at, "%Y-%m-%d %H:%M:%S")}
        for name, stance in r.stances.items():
            row[name] = stance.value if hasattr(stance, "value") else stance
        stance_rows.append(row)
    st.dataframe(
        pd.DataFrame(stance_rows).set_index("run (Europe/Berlin)"),
        use_container_width=True,
    )


# --------------------------------------------------------------------------- #
# Strategy rendering (read-only form + edit-as-new-version)
# --------------------------------------------------------------------------- #
def render_strategy_tab(selected_path: Path | None) -> None:
    if selected_path is None:
        st.info("Pick a live strategy in the sidebar to view or version it.")
        return

    strategy = load_strategy(selected_path)
    new_id, _ = bump_version(strategy)

    st.subheader(f"{strategy.name} · {strategy.id}")
    if strategy.description:
        st.caption(strategy.description.strip())

    edit = st.toggle("✏️ Edit as a new version", value=False, key="strat_edit")
    if edit:
        st.info(
            f"Saving creates a NEW file `strategies/{new_id}.yaml`. The current "
            "version is never modified — recorded verdicts reference their "
            "strategy_id and must stay reproducible."
        )
    disabled = not edit

    c = strategy.criteria
    st.markdown("##### Screening thresholds")
    col1, col2 = st.columns(2)
    min_yield = col1.number_input(
        "Min dividend yield (decimal, e.g. 0.025 = 2.5%)",
        value=float(c.min_dividend_yield), min_value=0.0, max_value=1.0,
        step=0.005, format="%.4f", disabled=disabled, key="c_yield")
    max_payout = col2.number_input(
        "Max payout ratio", value=float(c.max_payout_ratio), min_value=0.0,
        step=0.05, format="%.2f", disabled=disabled, key="c_payout")
    min_mcap = col1.number_input(
        "Min market cap (USD)", value=float(c.min_market_cap), min_value=0.0,
        step=1e9, format="%.0f", disabled=disabled, key="c_mcap")
    min_years = col2.number_input(
        "Min dividend growth years", value=int(c.min_dividend_growth_years),
        min_value=0, step=1, disabled=disabled, key="c_years")

    p = strategy.policy
    st.markdown("##### Policy flags")
    streak_block = st.checkbox(
        "Unverifiable streak is blocking", value=p.unverifiable_streak_is_blocking,
        disabled=disabled, key="p_streak")
    partial_hold = st.checkbox(
        "Partial pass allows HOLD", value=p.partial_pass_allows_hold,
        disabled=disabled, key="p_partial")

    st.markdown("##### Veto gate")
    min_conf = st.number_input(
        "Min confidence (below this, the run pauses for human review)",
        value=float(strategy.veto.min_confidence), min_value=0.0, max_value=1.0,
        step=0.05, format="%.2f", disabled=disabled, key="v_conf")

    if not edit:
        return

    if st.button("💾 Save new version", type="primary", key="strat_save"):
        updates = {
            "criteria": {
                "min_dividend_yield": min_yield,
                "max_payout_ratio": max_payout,
                "min_market_cap": min_mcap,
                "min_dividend_growth_years": int(min_years),
            },
            "policy": {
                "unverifiable_streak_is_blocking": streak_block,
                "partial_pass_allows_hold": partial_hold,
            },
            "veto": {"min_confidence": min_conf},
        }
        try:
            new = make_new_version(strategy, updates)
            path = save_strategy(new, STRATEGIES_DIR)
        except FileExistsError as exc:
            st.error(str(exc))
        except ValidationError as exc:
            st.error(f"Invalid values — nothing was saved.\n\n{exc}")
        else:
            st.success(
                f"Saved `{path.name}`. Pick it from the sidebar Strategy "
                "dropdown to run a council under it."
            )


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
def main() -> None:
    st.set_page_config(page_title="Council Station", page_icon="🏛", layout="wide")
    st.title("🏛 Council Station")
    st.caption("Local control room for the Aristos Council.")

    # --- sidebar: pick a ticker + strategy, gate the run on a cost ack ---
    with st.sidebar:
        st.header("Run a council")
        ticker = st.text_input("Ticker", value="JNJ").strip().upper()

        options = list_strategy_options(STRATEGIES_DIR)
        labels = [label for label, _, _ in options]
        choice = st.selectbox("Strategy", labels + [COMING_SOON])
        if choice == COMING_SOON:
            st.info("This strategy isn't available yet.")
            selected_path = None
        else:
            selected_path = dict((l, p) for l, p, _ in options)[choice]

        st.divider()
        # Cost gate. Cleared BEFORE the widget renders, so it starts unchecked
        # each session AND re-arms after every run — each API run requires a
        # fresh acknowledgement, never a leftover tick.
        if st.session_state.pop("_clear_cost_ack", False):
            st.session_state["cost_ack"] = False
        ack = st.checkbox(
            "I understand an API run costs real credits.", key="cost_ack")
        run_clicked = st.button(
            "▶ Run council",
            type="primary",
            disabled=not (ack and ticker and selected_path is not None),
        )
        if not ack:
            st.caption("Acknowledge the cost to enable the Run button.")

    if run_clicked and selected_path is not None:
        try:
            with st.spinner(f"Running the council on {ticker}…"):
                report = run_council(ticker, selected_path)
        except Exception as exc:  # surface, don't crash the page
            friendly = _friendly_error(exc, ticker)
            if friendly:
                st.error(friendly)
            else:
                st.exception(exc)  # unexpected — show the full traceback
        else:
            st.session_state["last_report"] = report.model_dump(mode="json")
            st.session_state["run_complete_msg"] = (
                f"Run complete — verdict and full report saved for {ticker}."
            )
            # re-arm the cost gate, then re-render with the fresh report on top
            st.session_state["_clear_cost_ack"] = True
            st.rerun()

    pending = st.session_state.pop("run_complete_msg", None)
    if pending:
        st.success(pending)

    tab_report, tab_history, tab_strategy = st.tabs(
        ["Report", "History", "Strategy"])

    with tab_report:
        _report_tab(ticker)

    with tab_history:
        render_history(ticker)

    with tab_strategy:
        render_strategy_tab(selected_path)


def _report_tab(ticker: str) -> None:
    """Show the latest run plus a browser for any past run of this ticker."""
    past = list_reports(ticker, REPORTS_DIR)

    last = st.session_state.get("last_report")
    if last is not None:
        st.caption("Most recent run from this session:")
        render_report(RunReport.model_validate(last),
                      sidebar_ticker=ticker, key_ns="latest")
        st.divider()

    if not past:
        if last is None:
            st.info(
                f"No saved reports for {ticker} yet. Run a council from the "
                "sidebar, or pick a ticker that has history."
            )
        return

    st.markdown("#### Browse past runs")
    # Load each past report once (newest first) for rich, verdict-bearing
    # labels: 'MO · 12.06. 15:42 · HOLD 0.55'. Select by index so two runs that
    # share a label can't collide.
    reports = [load_report(p) for p in reversed(past)]
    pick = st.selectbox(
        "Run", range(len(reports)),
        format_func=lambda i: _run_label(reports[i]),
    )
    chosen = reports[pick]
    st.caption(f"▶ Currently viewing: **{_run_label(chosen)}**")
    render_report(chosen, sidebar_ticker=ticker, key_ns="browse")


if __name__ == "__main__":
    main()
