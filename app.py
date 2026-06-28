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

import base64
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
from pydantic import ValidationError

from aristos_council.data.adapter import DataUnavailable, normalize_ticker
from aristos_council.tracing import trace_config
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
from aristos_council.presentation import (
    SCREEN_STATUS_HEX,
    degraded_banner,
    run_health_line,
    screen_table_rows,
    strip_provenance,
)
from aristos_council.state import Stance
from aristos_council.strategy.loader import Strategy, load_strategy
from aristos_council.strategy.overrides import applied_overrides, effective_strategy
from aristos_council.tools.criteria.registry import REGISTRY
from aristos_council.strategy.versioning import (
    bump_version,
    make_new_version,
    save_strategy,
)

# Anchor all data dirs to the APP FILE's location (resolved to absolute at import),
# never the launch cwd — so discovery works no matter where streamlit is started.
ROOT = Path(__file__).resolve().parent
STRATEGIES_DIR = ROOT / "strategies"
VERDICTS_DIR = ROOT / "verdicts"
REPORTS_DIR = ROOT / "reports"
ASSETS_DIR = ROOT / "assets"
LOGO_PATH = ASSETS_DIR / "aristos_council_logo.svg"

# Verdict semantic colors — the ONLY semantic colors in the app (everything else
# is the dark base + the single gold accent). Applied to the verdict banner, the
# history verdict markers, and the run-selector labels, consistently.
# INSUFFICIENT_EVIDENCE is OFF the directional ladder, so it gets a NON-directional
# slate grey — deliberately NOT green/amber/red (it is not a buy/hold/sell call).
_VERDICT_HEX = {"BUY": "#2E7D32", "HOLD": "#B8860B", "SELL": "#B23B3B",
                "INSUFFICIENT_EVIDENCE": "#5B6B7B"}
_VERDICT_DOT = {"BUY": "🟢", "HOLD": "🟡", "SELL": "🔴",
                "INSUFFICIENT_EVIDENCE": "⚪"}  # selectbox can't take hex
GOLD = "#52B6A4"  # the single accent


def _verdict_hex(verdict: str | None) -> str:
    return _VERDICT_HEX.get((verdict or "").upper(), "#8A8A8A")

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


def _prose(text: str, show_provenance: bool) -> str:
    """Prose for display: raw under the provenance toggle, stripped otherwise."""
    return text if show_provenance else strip_provenance(text)


def _md(text: str) -> str:
    """Escape '$' so st.markdown can't read currency as LaTeX math and eat it
    ("$1.048 trillion" -> "`1.048 trillion"). Financial text must keep its $."""
    return text.replace("$", "\\$") if text else text


def _render_prose(text: str, show_provenance: bool) -> str:
    """Display-ready prose: provenance-cleaned (unless toggled) and $-escaped."""
    return _md(_prose(text, show_provenance))


# Stance display helpers ------------------------------------------------------ #
_STANCE_BADGE = {
    Stance.BULLISH: "🟢 bullish",
    Stance.NEUTRAL: "🟡 neutral",
    Stance.BEARISH: "🔴 bearish",
    Stance.ABSTAIN: "⚪ abstain",
}


def _stance_badge(stance: Stance) -> str:
    return _STANCE_BADGE.get(stance, str(stance))


def _logo_markup(px: int) -> str:
    """Inline SVG logo sized to a px square, for the app header."""
    return f'<div style="width:{px}px;height:{px}px">' \
           f'{LOGO_PATH.read_text(encoding="utf-8")}</div>'


def _favicon() -> str:
    """SVG logo as a data URI for set_page_config (PIL can't open an SVG path,
    so a file path would raise; a data URI is handed straight to the browser)."""
    b64 = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


def _inject_chrome() -> None:
    """Strip a little cosmetic Streamlit noise, and a print stylesheet so a
    report prints / exports to PDF legibly.

    SCREEN hides are deliberately surgical — ONLY the footer, which is not a
    control. We must NEVER hide the toolbar / hamburger menu (Settings + theme
    switch) or the sidebar collapse/expand toggle on screen: a past
    chrome-strip took those out. Aggressive chrome-hiding lives in @media print
    only, where there is no interaction to lose."""
    st.markdown(
        """
        <style>
          /* On screen we hide ONLY the footer (not a control). */
          footer {visibility: hidden;}
          /* Defensive: NEVER let theming / a stale stylesheet hide the user
             controls. Force the top-right menu and the sidebar collapse/expand
             toggle visible, whatever else is on the page. */
          [data-testid="stToolbar"], [data-testid="stMainMenu"], #MainMenu,
          [data-testid="stSidebarCollapseButton"],
          [data-testid="stSidebarCollapsedControl"],
          [data-testid="stExpandSidebarButton"] {
            visibility: visible !important;
          }

          @media print {
            @page { margin: 1.5cm; }
            /* Light scheme for paper: white bg, near-black text (theme text is
               off-white and would be invisible on white). */
            html, body, .stApp, [data-testid="stAppViewContainer"],
            [data-testid="stHeader"], [data-testid="stMain"] {
              background: #ffffff !important;
            }
            [data-testid="stMain"], [data-testid="stMain"] * {
              color: #1a1a1a !important;
            }
            /* Hide non-record chrome: sidebar, toolbar, toggles, menus. */
            [data-testid="stSidebar"], [data-testid="stToolbar"],
            [data-testid="stHeader"], [data-testid="stDecoration"],
            [data-testid="stToggle"], #MainMenu, footer, header {
              display: none !important;
            }
            /* Force expanders open so specialist content is never clipped
               (covers native <details> and the div-based container). */
            details:not([open]) > *:not(summary),
            [data-testid="stExpanderDetails"] {
              display: block !important; height: auto !important;
              max-height: none !important; overflow: visible !important;
              visibility: visible !important;
            }
            details > * { content-visibility: visible !important; }
            /* Verdict colors darkened for paper (override inline color: the
               !important + extra specificity beats the inline style). */
            [data-testid="stMain"] .verdict-buy  { color: #1B5E20 !important; }
            [data-testid="stMain"] .verdict-hold { color: #6B4F00 !important; }
            [data-testid="stMain"] .verdict-sell { color: #8B1A1A !important; }
            /* Don't clip content into a scroll region. */
            .stApp, [data-testid="stMain"], .block-container {
              overflow: visible !important; height: auto !important;
            }
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


def list_strategy_options(strategies_dir: Path) -> list[tuple[str, Path, Strategy]]:
    """Every loadable strategy YAML as (label, path, strategy), id-sorted.

    All live strategies are selectable (Sprint 4C lit up growth_v1). Invalid
    YAMLs are skipped silently — the loader is the gatekeeper.
    """
    out: list[tuple[str, Path, Strategy]] = []
    for p in sorted(strategies_dir.glob("*.yaml")):
        try:
            s = load_strategy(p)
        except Exception:
            continue
        out.append((f"{s.name} · {s.id}", p, s))
    return out


# --------------------------------------------------------------------------- #
# Running the council in-process
# --------------------------------------------------------------------------- #
def run_council(ticker: str, strategy_path: Path,
                overrides: dict | None = None) -> RunReport:
    """Invoke the council for one ticker and persist both sinks at the edge.

    ``overrides`` (optional) carries ephemeral per-run disposition settings —
    ``{"partial_pass_allows_hold": bool, "is_gating": {criterion_name: bool}}`` —
    applied IN MEMORY on top of the immutable YAML strategy for THIS run only. The
    file is never modified; the delta vs the file is recorded on the verdict and
    report. None/empty ⇒ a pure-defaults run (byte-identical to before).

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

    from aristos_council.agents.runners import production_runners, runner_metadata
    from aristos_council.data.provider import select_market_adapter
    from aristos_council.graph import build_council
    from aristos_council.state import ResearchState

    base = load_strategy(strategy_path)
    # Apply ephemeral per-run overrides in memory; the on-disk YAML is untouched.
    overrides = overrides or {}
    strategy = effective_strategy(
        base,
        partial_pass_allows_hold=overrides.get("partial_pass_allows_hold"),
        is_gating=overrides.get("is_gating"),
    )
    delta = applied_overrides(base, strategy)   # what actually differs vs the file

    sentiment = None
    sentiment_missing_key = False
    if os.environ.get("FINNHUB_API_KEY"):
        from aristos_council.data.finnhub_adapter import FinnhubAdapter
        sentiment = FinnhubAdapter()
    else:
        sentiment_missing_key = True   # -> MISSING_KEY run issue -> degraded banner

    # Provider chosen by $ARISTOS_MARKET_PROVIDER (default yfinance); adapter.name
    # rides into provenance so the run records which provider it used.
    adapter = select_market_adapter()
    runners = production_runners()
    app = build_council(adapter, strategy, runners,
                        sentiment_adapter=sentiment,
                        sentiment_missing_key=sentiment_missing_key)

    # Prior verdict for the SAME ticker AND strategy (recommendation_flip key).
    # load_latest skips prior OVERRIDE runs, so an experiment never becomes the
    # baseline; and a non-empty delta suppresses this run's own flip firing.
    prior = load_latest(ticker, VERDICTS_DIR, strategy_id=base.id)
    initial = ResearchState(
        ticker=ticker,
        strategy_id=base.id,                     # always the BASE id
        prior_recommendation=prior.verdict if prior else None,
        applied_overrides=delta,
    )

    # Stream the graph so the UI can show per-stage progress for free: each
    # "values" chunk is the full state after a node, which we label by what it
    # has populated so far.
    progress = st.progress(0.0, text="Gathering evidence…")
    final: dict | None = None
    STAGES = 7  # gather + 4 specialists + critic + decision (audit/veto are fast)
    # Trace metadata so a live (optional-LangSmith) run is filterable; harmless off.
    trace = trace_config(ticker, base.id, adapter.name, bool(delta))
    for i, chunk in enumerate(
            app.stream(initial, config=trace, stream_mode="values"), start=1):
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
    report.models = runner_metadata(runners)   # record model + temperature per tier
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
    """Dense one-line label for the run selector, with a verdict color dot:
    'MO · 12.06. 15:42 · 🟡 HOLD 0.55'."""
    d = report.decision
    if d:
        v = d.recommendation.value.upper()
        verdict = f"{_VERDICT_DOT.get(v, '')} {v} {d.confidence:.2f}".strip()
    else:
        verdict = "—"
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
    st.dataframe(rows, hide_index=True, width="stretch")


# Back-compat alias for the shared helper (kept for tests / call sites).
_screen_table_rows = screen_table_rows


def _render_screen_table(screen: dict | None) -> None:
    rows = screen_table_rows(screen)
    if not rows:
        return
    import pandas as pd

    df = pd.DataFrame(rows)
    styler = df.style.map(
        lambda v: f"color: {SCREEN_STATUS_HEX.get(v, '')}; font-weight: 600",
        subset=["Status"],
    )
    st.subheader("Screen results")
    st.dataframe(styler, hide_index=True, width="stretch")


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


@st.cache_data(show_spinner=False)
def _report_pdf_bytes(report_json: str) -> bytes:
    """Generate the export PDF, cached by report content so it's built once."""
    from aristos_council.export.report_pdf import render_report_pdf
    return render_report_pdf(RunReport.model_validate_json(report_json))


def _render_pdf_button(report: RunReport, run_uid: str, key_ns: str) -> None:
    """An 'Export PDF' download button — a purpose-built A4 council record."""
    try:
        pdf = _report_pdf_bytes(report.model_dump_json())
    except Exception as exc:  # missing ui extra, etc. — degrade, don't crash
        st.caption(f"PDF export unavailable: {exc}")
        return
    st.download_button(
        "⬇ Export PDF",
        data=pdf,
        file_name=f"{report.ticker}_{run_uid}.pdf",
        mime="application/pdf",
        key=f"pdf_{key_ns}_{report.ticker}_{run_uid}",
    )


def _plural(kind: str, n: int) -> str:
    if n == 1:
        return kind
    return kind + "es" if kind == "mismatch" else kind + "s"


def _dq_summary(audit: dict) -> str:
    """One-line data-quality summary from the provenance audit counts, e.g.
    '7 provenance issues: 5 mismatches, 2 unresolvable'."""
    n = len(audit.get("violations") or [])
    cats = []
    if audit.get("mismatch"):
        cats.append(f"{audit['mismatch']} {_plural('mismatch', audit['mismatch'])}")
    if audit.get("unresolvable"):
        cats.append(f"{audit['unresolvable']} unresolvable")
    base = f"{n} provenance issue{'' if n == 1 else 's'}"
    return base + (": " + ", ".join(cats) if cats else "")


def _violation_tool(v: str) -> str:
    """The tool a violation cites, parsed from '... at <tool> -> <field> ...'."""
    arrow = v.find(" → ")           # ' -> ' (unicode arrow used in the text)
    if arrow == -1:
        return "?"
    before = v[:arrow]
    at = before.rfind(" at ")
    return before[at + 4:].strip() if at != -1 else "?"


def _group_violations(violations: list[str]) -> list[tuple[str, list[str]]]:
    """Group violations by (kind, cited tool) so repeats collapse, e.g.
    '4 mismatches citing get_dividend_history'. Returns (header, items)."""
    groups: dict[tuple[str, str], list[str]] = {}
    order: list[tuple[str, str]] = []
    for v in violations:
        kind = "unresolvable path" if v.lower().startswith("unresolvable") \
            else "mismatch"
        key = (kind, _violation_tool(v))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(v)
    out = []
    for kind, tool in order:
        items = groups[(kind, tool)]
        out.append((f"{len(items)} {_plural(kind, len(items))} citing {tool}",
                    items))
    return out


def render_report(
    report: RunReport, sidebar_ticker: str | None = None, key_ns: str = "report"
) -> None:
    """Render a full run report. The deliberation is the product: everything
    examples/run_council.py prints to the console appears here too."""
    # Run health FIRST: a degraded run (a fixable tool failure) gets a LOUD banner
    # as the very first thing, above the verdict. A clean run renders no banner.
    banner = degraded_banner(report.run_issues)
    if banner:
        st.error(banner)
    st.caption(run_health_line(report))

    _render_report_header(report, sidebar_ticker)

    run_uid = _to_local(report.run_at).strftime("%Y%m%d%H%M%S")
    ctrl_left, ctrl_right = st.columns([3, 1], vertical_alignment="center")
    with ctrl_left:
        # Per-report provenance toggle (off by default): shows call_ids in the
        # figures tables and the RAW, unstripped prose (inline citations intact).
        show_prov = st.toggle(
            "Show provenance details",
            value=False,
            key=f"prov_{key_ns}_{report.ticker}_{run_uid}",
            help="Reveal call_ids and inline field references for auditing.",
        )
    with ctrl_right:
        _render_pdf_button(report, run_uid, key_ns)

    _render_verdict_banner(report)

    # Override stamp: a run that changed a setting must not read as a default run.
    if report.applied_overrides:
        ovr = "; ".join(f"`{k}` = {v}"
                        for k, v in report.applied_overrides.items())
        st.warning(f"⚠ **Overrides this run** (not strategy defaults): {ovr}")

    # Human-review flags — prominent, directly under the banner.
    if report.veto_flags:
        st.error(
            "**⚠ Human review required** — "
            f"{len(report.veto_flags)} veto trigger(s) fired."
        )
        audit = report.provenance_audit or {}
        for f in report.veto_flags:
            # data_quality: a one-line summary + grouped full list in an expander,
            # instead of dumping the raw violation text inline.
            if f.trigger.value == "data_quality" and audit.get("violations"):
                st.markdown(f"- **data_quality** — {_dq_summary(audit)}")
                with st.expander("Show provenance issues"):
                    for header, items in _group_violations(audit["violations"]):
                        st.markdown(f"**{header}**")
                        for it in items:
                            st.markdown(f"- {it}")
            else:
                st.markdown(f"- **{f.trigger.value}** — {f.detail}")
    else:
        st.success("No veto triggers — auto-proceed permitted.")

    # --- Screen results (deterministic table, above the rationale) ------- #
    st.divider()
    st.caption(f"{report.ticker} · {_ts_header(report.run_at)}")  # stay oriented
    _render_screen_table(report.screen)

    # --- Decision -------------------------------------------------------- #
    if report.decision:
        st.subheader("Decision rationale")
        st.markdown(
            _render_prose(report.decision.rationale, show_prov)
            or "_(no rationale)_"
        )
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
            st.markdown(_render_prose(op.thesis, show_prov) or "_(no thesis)_")
            _figures_table(op.figures, show_prov)
            if op.caveats:
                st.markdown("**Caveats**")
                for c in op.caveats:
                    st.markdown(f"- ⚠ {_render_prose(c, show_prov)}")

    # --- Critic ---------------------------------------------------------- #
    if report.critic_report:
        cr = report.critic_report
        st.divider()
        st.subheader(
            f"Critic — arguing against the {cr.targets_stance.value} consensus"
        )
        st.markdown(
            _render_prose(cr.counter_thesis, show_prov) or "_(no counter-thesis)_"
        )
        _figures_table(cr.figures, show_prov)
        if cr.challenged_figures:
            st.markdown("**Challenged figures** (cited by the council, contested)")
            for cf in cr.challenged_figures:
                st.markdown(f"- {_render_prose(cf, show_prov)}")
        if cr.weaknesses_found:
            st.markdown("**Weaknesses found**")
            for w in cr.weaknesses_found:
                st.markdown(f"- {_render_prose(w, show_prov)}")
        if cr.open_questions:
            st.markdown(
                "**Open questions** (for human resolution — not evidence)"
            )
            for q in cr.open_questions:
                st.markdown(f"- {_render_prose(q, show_prov)}")

    # --- Audit (call_ids always — that is its job) ----------------------- #
    _render_provenance_panel(report.provenance_audit)


def _render_verdict_banner(report: RunReport) -> None:
    d = report.decision
    verdict = d.recommendation.value.upper() if d else "—"
    conf = f"{d.confidence:.2f}" if d else "—"
    # Class lets the print stylesheet darken the verdict color for paper.
    vclass = f"verdict-{d.recommendation.value}" if d else "verdict-none"
    col1, col2, col3 = st.columns([2, 1, 2])
    # Verdict is the only colored value — its semantic color, nothing else.
    col1.markdown(
        "<div style='font-size:0.8rem;letter-spacing:0.08em;color:#9aa0aa'>"
        "VERDICT</div>"
        f"<div class='{vclass}' style='font-size:2.1rem;font-weight:700;"
        f"line-height:1.1;color:{_verdict_hex(verdict)}'>{verdict}</div>",
        unsafe_allow_html=True,
    )
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
    verdict_scale = alt.Scale(domain=["BUY", "HOLD", "SELL"],
                              range=["#2E7D32", "#B8860B", "#B23B3B"])

    # Verdict: a stepped categorical line with SELL/HOLD/BUY as labelled levels
    # (BUY on top). The line is the gold accent (continuous, not semantic); the
    # markers carry each run's semantic verdict color and carry the signal when
    # there are only a few runs.
    base = alt.Chart(chart_df).encode(x=x)
    y_verdict = alt.Y("verdict:N", sort=["BUY", "HOLD", "SELL"], title="Verdict",
                      scale=alt.Scale(domain=["BUY", "HOLD", "SELL"]))
    verdict_panel = alt.layer(
        base.mark_line(interpolate="step-after", color=GOLD).encode(y=y_verdict),
        base.mark_point(filled=True, size=120, opacity=1).encode(
            y=y_verdict,
            color=alt.Color("verdict:N", scale=verdict_scale, legend=None),
            tooltip=tooltip,
        ),
    ).properties(height=170, title="Verdict")

    # Confidence: its own 0–1 axis (so verdict levels never read as a flat line
    # pinned to the bottom of a shared scale), in the gold accent — not semantic.
    confidence_panel = (
        alt.Chart(chart_df)
        .mark_line(color=GOLD, point=alt.OverlayMarkDef(size=90, color=GOLD))
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
    st.altair_chart(chart, width="stretch")

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
    st.dataframe(runs_df, width="stretch")

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
        width="stretch",
    )


# --------------------------------------------------------------------------- #
# Strategy rendering (read-only form + edit-as-new-version)
# --------------------------------------------------------------------------- #
# CriterionSpec carries exactly these per-criterion params; the rest a criterion
# declares (e.g. min_revenue_cagr's `years`) are registry defaults the strategy
# can't yet override, so they render read-only.
_PERSISTABLE_PARAMS = {"threshold", "unverifiable_blocks"}


def _human_number(value) -> str | None:
    """Readable form for large thresholds (raw ints like 1e10 are unreadable).

    None for values that don't need it (small decimals/integers)."""
    n = float(value)
    if abs(n) < 1000:
        return None
    commas = f"{n:,.0f}"
    if abs(n) >= 1e9:
        return f"{commas} (${n / 1e9:.0f}B)"
    if abs(n) >= 1e6:
        return f"{commas} (${n / 1e6:.0f}M)"
    return commas


def _param_input_kwargs(param, value) -> dict:
    """st.number_input kwargs for a criterion ParamSpec (type/bounds/step)."""
    kw: dict = {}
    if param.type == "int":
        kw["value"] = int(value)
        if param.min is not None:
            kw["min_value"] = int(param.min)
        if param.max is not None:
            kw["max_value"] = int(param.max)
        kw["step"] = int(param.step or 1)
    else:
        kw["value"] = float(value)
        if param.min is not None:
            kw["min_value"] = float(param.min)
        if param.max is not None:
            kw["max_value"] = float(param.max)
        step = float(param.step) if param.step else 0.01
        kw["step"] = step
        kw["format"] = "%.4f" if step < 0.01 else ("%.2f" if step < 1 else "%.0f")
    return kw


# Friendlier labels + help for the generic criterion renderer (display-only).
_PARAM_LABELS = {
    "threshold": "Threshold",
    "years": "CAGR window (years)",
    "unverifiable_blocks": "Unverifiable result blocks",
}
_PARAM_HELP = {
    "unverifiable_blocks":
        "Marks whether a NOT-EVAL (couldn't-be-evaluated) result for this "
        "criterion should count as disqualifying for this strategy. Not yet "
        "active: today every NOT-EVAL result escalates to human review "
        "regardless of this setting — this per-criterion control is reserved "
        "for upcoming strategy-disposition logic.",
    "years":
        "Look-back window for the in-house revenue CAGR (shared by the revenue "
        "and PEG criteria). Fixed in code; not strategy-configurable yet.",
}


def _param_label(param) -> str:
    return _PARAM_LABELS.get(param.name, param.name.replace("_", " ").title())


def _render_criterion(spec, edit: bool, sid: str) -> dict:
    """Render one criterion's params from its registry metadata.

    Generic — no per-criterion branches. Numeric params share a row; the
    bool (unverifiable-blocks) gets its OWN line so it never reads as a
    threshold nor blurs into the strategy-level Policy checkbox (different
    section). Locked params (not strategy-configurable, e.g. the CAGR window)
    are still SHOWN, but disabled and tagged 🔒 so nothing verdict-affecting is
    invisible. Returns the persistable params to save.
    """
    crit = REGISTRY.get(spec.name)
    label = crit.label if crit else spec.name
    params = crit.params if crit else ()
    st.markdown(f"**{label}**  ·  `{spec.name}`")

    current = {"threshold": spec.threshold,
               "unverifiable_blocks": spec.unverifiable_blocks}
    saved = {"threshold": spec.threshold,
             "unverifiable_blocks": spec.unverifiable_blocks}

    numeric = [p for p in params if p.type in ("int", "float")]
    bools = [p for p in params if p.type == "bool"]

    # strategy-scoped widget keys: two strategies can share a criterion name
    # (both have min_market_cap), so switching must not reuse widgets.
    for col, param in zip(st.columns(len(numeric)), numeric):
        value = current.get(param.name, param.default)
        persistable = param.name in _PERSISTABLE_PARAMS
        disabled = not edit or not persistable
        key = f"c_{sid}_{spec.name}_{param.name}"
        lbl = _param_label(param) + ("  🔒" if not persistable else "")
        out = col.number_input(lbl, disabled=disabled, key=key,
                               help=_PARAM_HELP.get(param.name),
                               **_param_input_kwargs(param, value))
        human = _human_number(out)
        if human:
            col.caption(f"= {human}")
        if not persistable:
            col.caption("🔒 fixed — not configurable")
        if persistable:
            saved[param.name] = out

    # The per-criterion bool on its own full-width line, clearly labelled.
    for param in bools:
        value = current.get(param.name, param.default)
        persistable = param.name in _PERSISTABLE_PARAMS
        key = f"c_{sid}_{spec.name}_{param.name}"
        out = st.checkbox(_param_label(param), value=bool(value),
                          disabled=not edit or not persistable, key=key,
                          help=_PARAM_HELP.get(param.name))
        if persistable:
            saved[param.name] = out
    return {"name": spec.name, **saved}


def _run_overrides(strategy: Strategy) -> dict:
    """Sidebar controls for EPHEMERAL per-run disposition overrides — applied to
    THIS run only and recorded on the report, never written to the strategy file.

    Returns ``{"partial_pass_allows_hold": bool, "is_gating": {name: bool}}`` with
    the current control values; the run records only what actually differs from
    the file (so leaving everything at its default is a no-op). Deliberately NOT
    part of Save-new-version / _PERSISTABLE_PARAMS — this controls a run, not the
    file."""
    sid = strategy.id
    with st.expander("⚙️ Run overrides — this run only", expanded=False):
        st.caption("Applied to THIS run only and stamped on the report. The "
                   "strategy file is never modified.")
        if st.button("↺ Reset to strategy defaults", key=f"ovr_reset_{sid}"):
            for k in ([f"ovr_partial_{sid}"]
                      + [f"ovr_gate_{sid}_{c.name}" for c in strategy.criteria]):
                st.session_state.pop(k, None)
            st.rerun()
        partial = st.checkbox(
            "Partial pass allows HOLD",
            value=strategy.policy.partial_pass_allows_hold,
            key=f"ovr_partial_{sid}",
            help="Soft policy hint to the Decision agent (this run only).")
        st.caption("Gating — a confirmed fail caps the verdict at SELL:")
        is_gating: dict[str, bool] = {}
        for c in strategy.criteria:
            crit = REGISTRY.get(c.name)
            label = crit.label if crit else c.name
            is_gating[c.name] = st.checkbox(
                f"{label} · gating",
                value=c.is_gating,
                key=f"ovr_gate_{sid}_{c.name}",
                help="Deterministic SELL ceiling on a confirmed fail (this run "
                     "only).")
    return {"partial_pass_allows_hold": partial, "is_gating": is_gating}


def render_strategy_tab(selected_path: Path | None) -> None:
    if selected_path is None:
        st.info("Pick a strategy in the sidebar to view or version it.")
        return

    strategy = load_strategy(selected_path)
    new_id, _ = bump_version(strategy)
    # Strategy-scoped widget keys: switching the dropdown re-renders the whole
    # tab for the newly selected strategy, pre-filled from its YAML. One
    # strategy on screen at a time — never both.
    sid = strategy.id

    # 1 — prominent header: which ruleset is on screen (changes with dropdown).
    st.subheader(f"📋 Viewing: {strategy.name} ({strategy.id})")
    st.caption(f"Version {strategy.version} · one strategy at a time — switch "
               "in the sidebar to view another.")
    if strategy.description:
        st.caption(strategy.description.strip())

    edit = st.toggle("✏️ Edit as a new version", value=False,
                     key=f"strat_edit_{sid}")
    if edit:
        st.info(
            f"Saving creates a NEW file `strategies/{new_id}.yaml`. The current "
            "version is never modified — recorded verdicts reference their "
            "strategy_id and must stay reproducible."
        )

    # 2 — the screen's criteria, in their own card.
    # CRITERIA — editable thresholds, generic from registry metadata.
    with st.container(border=True):
        st.markdown("### 🎯 Criteria")
        st.caption("The screen's thresholds. 🔒 marks values fixed in code — "
                   "shown for transparency, not editable here.")
        edited = [_render_criterion(spec, edit, sid)
                  for spec in strategy.criteria]

    # 3 — strategy-level settings, set APART from the criteria stack: a hard
    # divider then a labelled two-card row, so Policy / Veto gate read as a
    # distinct settings zone and never as trailing criteria. The Policy checkbox
    # in particular sits in its own card, unmistakably separate from the
    # per-criterion "unverifiable blocks" boxes above.
    st.divider()
    st.markdown("#### Strategy settings")
    col_policy, col_veto = st.columns(2)

    with col_policy:
        with st.container(border=True):
            st.markdown("### ⚖️ Policy")
            st.caption("Strategy-level conflict handling — NOT a per-criterion "
                       "setting.")
            partial_hold = st.checkbox(
                "Partial pass allows HOLD",
                value=strategy.policy.partial_pass_allows_hold,
                disabled=not edit, key=f"p_partial_{sid}",
                help="When a stock passes some but not all criteria, allow the "
                     "council to weigh a HOLD rather than an outright SELL. "
                     "Advisory to the Decision agent.")

    with col_veto:
        with st.container(border=True):
            st.markdown("### 🚦 Veto gate")
            st.caption("Deterministic human-review trigger.")
            min_conf = st.number_input(
                "Min confidence (below this, the run pauses for human review)",
                value=float(strategy.veto.min_confidence), min_value=0.0,
                max_value=1.0, step=0.05, format="%.2f", disabled=not edit,
                key=f"v_conf_{sid}")

    if not edit:
        return

    if st.button("💾 Save new version", type="primary", key=f"strat_save_{sid}"):
        updates = {
            "criteria": edited,
            "policy": {"partial_pass_allows_hold": partial_hold},
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
    try:
        st.set_page_config(page_title="Council Station", page_icon=_favicon(),
                           layout="wide")
    except Exception:  # data-URI favicon rejected — fall back to an emoji
        st.set_page_config(page_title="Council Station", page_icon="🏛",
                           layout="wide")
    _inject_chrome()

    col_logo, col_title = st.columns([1, 11], vertical_alignment="center")
    with col_logo:
        st.markdown(_logo_markup(52), unsafe_allow_html=True)
    with col_title:
        st.title("Council Station")
    st.caption("Local control room for the Aristos Council.")

    # --- sidebar: pick a ticker + strategy, gate the run on a cost ack ---
    with st.sidebar:
        st.header("Run a council")
        # normalize_ticker also strips a stray trailing dot ("000660.KS." -> the
        # SK Hynix retrieval bug); upper-cases and trims like the old inline call.
        ticker = normalize_ticker(st.text_input("Ticker", value="JNJ"))

        options = list_strategy_options(STRATEGIES_DIR)
        if options:
            labels = [label for label, _, _ in options]
            choice = st.selectbox("Strategy", labels)
            by_label = {label: (p, s) for label, p, s in options}
            selected_path, selected_strategy = by_label[choice]
            run_overrides = _run_overrides(selected_strategy)
        else:  # no loadable strategy files — show the absolute path searched
            st.error(f"No strategies found under {STRATEGIES_DIR}")
            selected_path = None
            run_overrides = {}

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
                report = run_council(ticker, selected_path, run_overrides)
        except Exception as exc:  # surface, don't crash the page
            friendly = _friendly_error(exc, ticker)
            if friendly:
                st.error(friendly)
            else:
                st.exception(exc)  # unexpected — show the full traceback
        else:
            st.session_state["run_complete_msg"] = (
                f"Run complete — verdict and full report saved for {ticker}."
            )
            # Focus the browser on the just-completed run, re-arm the cost gate,
            # and re-render. The run becomes the selected report — not a second
            # copy pinned above the browser.
            st.session_state["_focus_ticker"] = ticker
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


def _available_tickers(reports_dir: Path) -> list[str]:
    """Tickers actually on record under reports/ (dirs holding ≥1 report), sorted.

    This is the browser's scope — independent of the sidebar text field, so every
    ticker with saved runs is reachable without editing the sidebar."""
    if not reports_dir.exists():
        return []
    return sorted(
        d.name for d in reports_dir.iterdir()
        if d.is_dir() and any(d.glob("*.json"))
    )


def _report_tab(ticker: str) -> None:
    """One report view. The past-run browser is scoped by its OWN ticker
    selector (built from reports/ on disk), defaulting to the sidebar ticker but
    navigable independently. A report is never rendered twice on the page."""
    tickers = _available_tickers(REPORTS_DIR)
    if not tickers:
        st.info("No saved reports yet. Run a council from the sidebar.")
        return

    # Empty scope: the sidebar ticker has nothing on record — say what does.
    if ticker not in tickers:
        st.caption(
            f"No reports for **{ticker}** yet. On record: {', '.join(tickers)}."
        )

    # Focus the just-completed run's ticker after a run; otherwise default to the
    # sidebar ticker. The choice then persists independently of the sidebar.
    focus = st.session_state.pop("_focus_ticker", None)
    if focus in tickers:
        st.session_state["browse_ticker"] = focus
    if st.session_state.get("browse_ticker") not in tickers:
        st.session_state["browse_ticker"] = (
            ticker if ticker in tickers else tickers[0]
        )
    sel = st.selectbox(
        f"Runs for · {len(tickers)} ticker(s) on record",
        tickers, key="browse_ticker",
    )

    reports = [load_report(p) for p in reversed(list_reports(sel, REPORTS_DIR))]
    if not reports:  # defensive — selector only lists tickers that have reports
        st.info(f"No reports for {sel}. On record: {', '.join(tickers)}.")
        return
    # Rich, verdict-bearing labels; select by index so shared labels can't collide.
    pick = st.selectbox(
        "Run", range(len(reports)),
        format_func=lambda i: _run_label(reports[i]),
        key=f"run_pick_{sel}",
    )
    chosen = reports[pick]
    st.caption(f"▶ Currently viewing: **{_run_label(chosen)}**")
    render_report(chosen, sidebar_ticker=ticker, key_ns="browse")


if __name__ == "__main__":
    main()
