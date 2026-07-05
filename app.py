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
    contested_banner,
    degraded_banner,
    matrix_comparison_line,
    matrix_verdict_text,
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
UNIVERSES_DIR = ROOT / "universes"
VERDICTS_DIR = ROOT / "verdicts"
REPORTS_DIR = ROOT / "reports"
SNAPSHOTS_CSV = ROOT / "snapshots" / "verdict_consensus.csv"
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

# The one-line banner on every PRE-V2 surface (the single-ticker council flow and its
# Report/History browsers). The council no longer issues the verdict — it narrates the
# deterministic ranker — so these surfaces are kept for comparison, clearly labeled.
_LEGACY_BANNER = (
    "Pre-v2 architecture: an LLM council issued the verdict. Demoted to narrator "
    "after a controlled experiment (README: 'Why this design'). Kept for comparison "
    "and demonstration."
)


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
    """Every USER-RUNNABLE SINGLE-TICKER (council) strategy as (label, path, strategy),
    id-sorted.

    Classification is by SHAPE (``aristos_council.strategy.discovery``): council
    strategies have ``criteria:`` and are NOT referenced as a rank strategy's
    council-lens screen. The rank strategies (Universe Run tab) and the internal lens
    screens are excluded here. Invalid YAMLs are skipped silently (the loader gates).
    """
    from aristos_council.strategy.discovery import council_strategies

    out: list[tuple[str, Path, Strategy]] = []
    for info in council_strategies(strategies_dir):
        try:
            s = load_strategy(info.path)
        except Exception:
            continue
        out.append((f"{s.name} · {s.id}", info.path, s))
    return out


def list_rank_strategy_options(strategies_dir: Path) -> list[tuple[str, Path, object]]:
    """Every RANK strategy (Universe Run tab) as (label, path, rank_strategy),
    id-sorted — the schema-split counterpart to ``list_strategy_options``."""
    from aristos_council.strategy.discovery import rank_strategies
    from aristos_council.strategy.rank_loader import load_rank_strategy

    out: list[tuple[str, Path, object]] = []
    for info in rank_strategies(strategies_dir):
        try:
            s = load_rank_strategy(info.path)
        except Exception:
            continue
        out.append((f"{s.name} · {s.id}", info.path, s))
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

    # Hybrid verdict: the deterministic matrix verdict next to the LLM one, with its
    # working in an expander (the matrix's edge — a fully auditable verdict).
    comparison = matrix_comparison_line(report)
    if comparison:
        st.info(f"🔢 {comparison}")
        m = report.matrix_decision
        if m is not None and m.contributions:
            with st.expander("Matrix working (deterministic score breakdown)"):
                for c in m.contributions:
                    st.markdown(f"- `{c.points:+.1f}` — {c.detail}")
                if not m.gated:
                    st.markdown(f"**Total score: {m.score:+.1f}** "
                                f"→ {matrix_verdict_text(m)}")

    # Contested-verdict line: a close call (panel split / dissent) routes the user
    # to the report and their own judgement. Clean verdicts get nothing.
    contested_line = contested_banner(report)
    if contested_line:
        st.warning(f"⚖ **{contested_line}**")

    # Decision-node micro-harness: if this run was measured and came back BORDERLINE,
    # show the vote distribution under the verdict.
    ds = report.decision_stability or {}
    if ds.get("stability") == "BORDERLINE":
        dist = ds.get("verdict_distribution", {})
        dist_txt = " / ".join(f"{v.upper()} {c}"
                              for v, c in sorted(dist.items(),
                                                 key=lambda kv: (-kv[1], kv[0])))
        st.warning(
            f"⚖ **BORDERLINE — the Decision node returned {dist_txt} over "
            f"{ds.get('n')} replays on identical evidence; treat as a lead and "
            f"read the report.**")

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
    # The GATING metric is the deterministic evidence coverage — NOT the narrator's
    # self-assigned confidence (which is now a non-gating prose note below).
    cov = report.evidence_coverage
    cov_txt = f"{cov:.2f}" if cov is not None else "—"
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
    col2.metric("Evidence coverage", cov_txt,
                help="Deterministic coverage of what the run actually saw — this "
                     "gates the low-confidence escalation, in place of the narrator's "
                     "self-assigned number.")
    col3.metric("Run", _fmt_local(report.run_at))
    if d:
        # The narrator's number, kept as an HONEST non-gating note (renamed from
        # "confidence" — it no longer moves any mechanical outcome).
        st.caption(f"Narrator's note on conviction: **{d.confidence:.2f}** — a prose "
                   "signal only; it does NOT gate escalation.")


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
    # Honest scope: this editor knows only COUNCIL-strategy YAMLs (legacy schemas). The
    # sidebar dropdown already lists council strategies only (the schema-split
    # classifier hides rank + lens screens), so a v2 rank strategy can never land here.
    st.info("**Edits council-strategy YAMLs (legacy schemas).** Rank strategies (v2) "
            "are versioned files under `strategies/` — edit via the repo, not here.")
    if selected_path is None:
        st.caption("Pick a strategy in the sidebar to view or version it.")
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
# Universe Run tab — the v2 rank pipeline (screen -> rank -> gates -> narrator)
# --------------------------------------------------------------------------- #
def _parse_universe(raw: str) -> list[str]:
    """Whitespace/comma/newline-separated tickers -> normalized, de-duped, ordered."""
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for tok in raw.replace(",", " ").split():
        nt = normalize_ticker(tok)
        if nt and nt not in seen:
            seen.add(nt)
            out.append(nt)
    return out


def _estimate_shortlist_size(n: int, rank_strategy) -> int:
    """Rough shortlist size for a pre-run cost hint (exact size is known only after
    the free ranking pass, which exclusions shrink)."""
    if n == 0:
        return 0
    runs_on = rank_strategy.council_runs_on
    if runs_on == "all":
        return n
    if runs_on == "top_k" or rank_strategy.cut == "top_k":
        return min(rank_strategy.k, n)
    return max(1, round(n / 5))          # buy_quintile


def _ranked_rows(ranked) -> tuple[list[dict], list[str]]:
    """Rows + the ordered factor columns for the ranked table. A per-factor rank is
    marked with a trailing ``*`` when it was imputed (the value was absent)."""
    factor_names: list[str] = []
    for r in ranked:
        for f in r.factor_ranks:
            if f not in factor_names:
                factor_names.append(f)
    rows: list[dict] = []
    for i, r in enumerate(ranked, 1):
        row = {"#": i, "Ticker": r.ticker, "Verdict": r.verdict.upper(),
               "Combined": round(r.combined_rank, 1)}
        for f in factor_names:
            if f in r.factor_ranks:
                row[f] = f"{r.factor_ranks[f]:.0f}" + \
                    ("*" if f in r.imputed_factors else "")
            else:
                row[f] = "—"
        rows.append(row)
    return rows, factor_names


def _universe_markdown(result) -> str:
    """The run as a self-contained markdown doc (the download; NO new storage format
    this sprint — the pipeline does not persist reports)."""
    m = result.meta
    lines = [f"# Universe run — {m['rank_strategy_id']}", "",
             f"_{result.header}_", "",
             f"- screen: `{m['screen_strategy_id']}`",
             f"- mode: {m['council_mode']}",
             f"- ranked: {m['ranked_count']} / {m['universe_size']}"]
    if not m["ranker_only"]:
        lines.append(f"- shortlist: {len(m['shortlist'])} · est ${m['est_cost']:.2f}")
    lines += ["", "## Ranked (verdict of record)", ""]
    rows, factor_names = _ranked_rows(result.ranked)
    if rows:
        head = ["#", "Ticker", "Verdict", "Combined", *factor_names]
        lines.append("| " + " | ".join(head) + " |")
        lines.append("|" + "---|" * len(head))
        for row in rows:
            cells = [str(row["#"]), row["Ticker"], row["Verdict"],
                     str(row["Combined"]), *[str(row[f]) for f in factor_names]]
            lines.append("| " + " | ".join(cells) + " |")
    else:
        lines.append("_(no names survived the screen)_")
    from aristos_council.pipeline import factor_integrity, format_integrity_entry

    entries = factor_integrity(result)
    if entries:
        lines += ["", "## Factor integrity", ""]
        lines += [f"- **{e['factor']}** — {format_integrity_entry(e)}" for e in entries]
    if result.excluded:
        lines += ["", "## Excluded (screen / cap / sector)", ""]
        lines += [f"- **{t}** — {why}" for t, why in result.excluded]
    if result.unrateable:
        lines += ["", "## Unrateable (no data — no verdict)", ""]
        lines += [f"- **{t}** — {why}" for t, why in result.unrateable]
    if result.narratives:
        lines += ["", "## Narrative", ""]
        for t, text in result.narratives.items():
            lines += [f"### {t}", "", text, ""]
    return "\n".join(lines)


def _render_universe_result(result) -> None:
    m = result.meta

    # 1 — the division-of-labor header line, prominent.
    st.markdown(f"#### {result.header}")
    meta_bits = (f"rank: `{m['rank_strategy_id']}` · screen: "
                 f"`{m['screen_strategy_id']}` · universe: "
                 f"`{m.get('universe_id', '—')}` · mode: {m['council_mode']} · "
                 f"ranked {m['ranked_count']}/{m['universe_size']}")
    if not m["ranker_only"]:
        meta_bits += (f" · shortlist {len(m['shortlist'])} · "
                      f"est ${m['est_cost']:.2f}")
    st.caption(meta_bits)

    # 2 — RANKED table: sortable, verdict palette, per-factor ranks (imputed *).
    st.subheader("Ranked — verdict of record")
    rows, factor_names = _ranked_rows(result.ranked)
    if rows:
        import pandas as pd

        df = pd.DataFrame(rows)
        styler = df.style.map(
            lambda v: f"color: {_verdict_hex(v)}; font-weight: 700",
            subset=["Verdict"])
        st.dataframe(styler, hide_index=True, width="stretch")
        if any("*" in str(row[f]) for row in rows for f in factor_names):
            st.caption("\\* = factor value absent; rank imputed from the name's "
                       "other factors (judged on what it has, not punished).")
    else:
        st.info("No names survived the screen to be ranked.")

    # 2b — FACTOR INTEGRITY: which computation path produced each factor per name
    # (ITEM 1) — EV vs EBIT/mcap proxy vs abstained, no longer silent.
    from aristos_council.pipeline import factor_integrity, format_integrity_entry

    entries = factor_integrity(result)
    if entries:
        st.subheader("Factor integrity")
        st.caption("Per factor, how each ranked name's value was produced — a silent "
                   "fallback (stale cache / missing fields) now shows in plain text.")
        for e in entries:
            st.markdown(f"- **{e['factor']}** — {format_integrity_entry(e)}")

    # 3 — Excluded (screen / cap / sector / payout): a neutral table.
    if result.excluded:
        st.subheader(f"Excluded — screen / cap / sector · {len(result.excluded)}")
        st.dataframe([{"Ticker": t, "Reason": why} for t, why in result.excluded],
                     hide_index=True, width="stretch")

    # 4 — UNRATEABLE: its OWN axis (no data, no verdict) — deliberately distinct.
    if result.unrateable:
        st.subheader(f"⚪ Unrateable — no data, no verdict · {len(result.unrateable)}")
        with st.container(border=True):
            st.caption("A SELL implies an assessment was made; these names had no "
                       "usable data at all (likely delisted), so they receive NO "
                       "verdict and reached no model.")
            for t, why in result.unrateable:
                st.markdown(f"- **{t}** — {why}")

    # 4b — FETCH FAILED: a transient failure (429/timeout/5xx) — NOT a verdict, NOT
    # UNRATEABLE. The name aborted this run and should be RE-RUN, distinct from a
    # genuinely dataless name.
    if result.fetch_errors:
        st.subheader(f"🔁 Fetch failed — rerun · {len(result.fetch_errors)}")
        st.warning("These names hit a **transient** fetch failure (rate limit / "
                   "timeout / server error) that did not recover after retries — a "
                   "live ticker, NOT delisted. They were aborted (no verdict, not "
                   "worst-ranked); re-run to recover them.")
        for t, why in result.fetch_errors:
            st.markdown(f"- **{t}** — {why}")

    # 5 — NARRATIVE: one expander per shortlisted (BUY) name — the narrator's job.
    if not m["ranker_only"]:
        st.subheader("Narrative")
        if result.narratives:
            verdict_of = {r.ticker: r.verdict.upper() for r in result.ranked}
            for ticker, text in result.narratives.items():
                v = verdict_of.get(ticker, "")
                with st.expander(f"{ticker}{(' · ' + v) if v else ''} — narration"):
                    st.markdown(_md(text) or "_(no narrative produced)_")
        else:
            st.caption("No names reached the council.")

    # 6 — download the run as markdown (no new on-disk storage this sprint).
    st.download_button(
        "⬇ Download run as markdown",
        data=_universe_markdown(result),
        file_name=f"universe_{m['rank_strategy_id']}.md",
        mime="text/markdown", key="uni_download")


def render_universe_tab() -> None:
    import os

    from aristos_council.reproducibility import estimate_cost

    from aristos_council.universe import list_universes

    st.subheader("Universe Run — the v2 rank pipeline")
    st.caption("Screen → rank → gates issue the verdict of record; the LLM only "
               "narrates. Pick a rank strategy and a universe (a saved manifest or a "
               "custom list).")

    rank_options = list_rank_strategy_options(STRATEGIES_DIR)
    if not rank_options:
        st.error(f"No rank strategies found under {STRATEGIES_DIR}")
        return
    labels = [label for label, _, _ in rank_options]
    choice = st.selectbox("Rank strategy", labels, key="uni_strategy")
    rank_strategy = next(s for label, _, s in rank_options if label == choice)

    # Universe source: a declared manifest (recorded by id) or a custom paste
    # (recorded as adhoc:<hash>). The manifest is the reproducible, versioned input.
    manifests = list_universes(UNIVERSES_DIR)
    CUSTOM = "Custom (paste tickers)"
    source_labels = [f"{u.id} · {len(u.tickers)} names" for u in manifests] + [CUSTOM]
    source = st.selectbox("Universe", source_labels, key="uni_source")
    if source == CUSTOM:
        raw = st.text_area(
            "Universe — tickers separated by spaces, commas, or newlines",
            key="uni_universe", height=120, placeholder="AAPL MSFT GOOGL AMZN META …")
        universe = _parse_universe(raw)
        universe_id = None                          # -> adhoc:<hash> in the record
    else:
        picked = manifests[source_labels.index(source)]
        universe = list(picked.tickers)
        universe_id = picked.id
        st.caption(f"Manifest **{picked.id}** — {len(universe)} names. "
                   f"{picked.description}")
        with st.expander("Tickers in this manifest"):
            st.write(", ".join(universe))

    col_a, col_b = st.columns(2)
    with col_a:
        ranker_only = st.checkbox("Ranker only — no LLM, no cost", value=False,
                                  key="uni_ranker_only")
    with col_b:
        # Value stays "second_opinion" (behavior unchanged); only its LABEL flags it as
        # the experimental null-result mode.
        _mode_label = {
            "narrator": "narrator",
            "second_opinion": "second_opinion (experimental — null result; see README)",
        }
        mode = st.selectbox(
            "Council mode", ["narrator", "second_opinion"],
            key="uni_mode", disabled=ranker_only, format_func=lambda m: _mode_label[m],
            help="narrator: the LLM explains the ranker verdict (default). "
                 "second_opinion: an independent comparison verdict — a pre-registered "
                 "experiment that returned a null result; kept behind this flag.")

    st.caption(f"**{len(universe)}** ticker(s) parsed.")

    CAP = 60
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    problems: list[str] = []
    if not universe:
        problems.append("Enter at least one ticker.")
    if len(universe) > CAP:
        problems.append(f"Universe too large ({len(universe)} > {CAP}) for an "
                        f"interactive run — trim it.")
    if not ranker_only and not has_key:
        problems.append("Narrator / second-opinion needs ANTHROPIC_API_KEY (set it "
                        "in the environment or a local .env). Use **Ranker only** to "
                        "run with no LLM and no cost.")

    if not ranker_only and universe and len(universe) <= CAP:
        est = estimate_cost(_estimate_shortlist_size(len(universe), rank_strategy))
        st.caption(f"Estimated council cost ≈ **${est:.2f}** — upper bound (pre-screen); "
                   "the exact shortlist (after the screen prefilter) is shown after ranking.")

    for msg in problems:
        st.info(msg)

    label = "▶ Run ranker (free)" if ranker_only else "▶ Run universe"
    run = st.button(label, type="primary", disabled=bool(problems), key="uni_run")

    if run:
        status = st.status("Starting…", expanded=True)
        try:
            from aristos_council.pipeline import run_rank_pipeline

            result = run_rank_pipeline(
                universe, rank_strategy.id, universe_id=universe_id,
                council_mode=mode, ranker_only=ranker_only,
                strategies_dir=STRATEGIES_DIR, universes_dir=UNIVERSES_DIR,
                progress=lambda msg: status.update(label=msg))
        except Exception as exc:
            status.update(label="Run failed", state="error")
            # Finnhub scope-fence (sprint item 4): a live crash on Finnhub is a
            # SEPARATE bug with its own spec — capture the traceback and STOP,
            # do not paper over it. Sentiment should degrade to abstention upstream.
            st.exception(exc)
            st.session_state.pop("uni_result", None)
        else:
            status.update(label="Done.", state="complete")
            st.session_state["uni_result"] = result

    result = st.session_state.get("uni_result")
    if result is not None:
        st.divider()
        _render_universe_result(result)

    _render_snapshot_history()


def _render_snapshot_history() -> None:
    """Minimal, read-only listing of the persisted rank-run records — the append-only
    snapshot store (date · strategy · universe · rows), labeled with universe_id, plus
    a raw-CSV download. Rank runs aren't saved as single-ticker reports, so this is
    where they're retrievable; it's a listing, NOT a new report renderer."""
    from aristos_council.scoreboard import read_rows

    if not SNAPSHOTS_CSV.exists():
        return
    rows = read_rows(SNAPSHOTS_CSV)
    if not rows:
        return
    with st.expander(f"📸 Persisted snapshots (rank-run records) · {len(rows)} rows"):
        agg: dict[tuple, int] = {}
        for r in rows:
            key = (r.snapshot_date, r.strategy, r.universe_id or "—")
            agg[key] = agg.get(key, 0) + 1
        table = [{"snapshot_date": d, "strategy": s, "universe_id": u, "rows": n}
                 for (d, s, u), n in sorted(agg.items(), reverse=True)]
        st.dataframe(table, hide_index=True, width="stretch")
        st.download_button(
            "⬇ Download snapshot CSV", data=SNAPSHOTS_CSV.read_bytes(),
            file_name="verdict_consensus.csv", mime="text/csv", key="snap_csv_dl")
        st.caption("Scored later on forward returns via "
                   "`examples/score_snapshot.py` (the prospective scoreboard).")


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
def main() -> None:
    # Load a local .env at APP START (item 4) so ANTHROPIC/FINNHUB keys reach the
    # Streamlit process regardless of the launch shell — the key guards below and
    # every run path then see them. No-op if absent; never overrides real env vars.
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except Exception:
        pass  # python-dotenv is a runtime extra; browsing past runs doesn't need it

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
    # v2 subtitle: the division of labor is the product's headline (the math judges,
    # the LLM narrates) — not "control room for the council" (the demoted pre-v2 frame).
    st.caption("**Verdict: deterministic ranker. Narrative: LLM (non-judging).**")

    # Legacy surfaces are HIDDEN BY DEFAULT (product decision): the app opens as
    # v2-only. Read the toggle's persisted value FIRST so the pre-v2 flow renders only
    # when enabled; the toggle itself sits small at the BOTTOM of the sidebar.
    show_legacy = st.session_state.get("show_legacy", False)

    ticker = "JNJ"
    selected_path: Path | None = None
    run_overrides: dict = {}
    run_clicked = False

    with st.sidebar:
        if show_legacy:
            # --- LEGACY single-ticker council flow (pre-v2) ---
            st.header("Run a council · Legacy (pre-v2)")
            st.caption(_LEGACY_BANNER)
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
            st.divider()

        # The toggle — small, at the very bottom of the sidebar, in BOTH states so it
        # is always the way back. No `value=` so its default is off and tests/session
        # can set it without a default-conflict warning.
        st.toggle(
            "Show legacy tools", key="show_legacy",
            help="Reveal the pre-v2 single-ticker council, its Report/History, and the "
                 "council-strategy editor. Off by default — the app opens as the v2 "
                 "Universe Run.")

    if show_legacy and run_clicked and selected_path is not None:
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

    if not show_legacy:
        # v2-ONLY landing: the Universe Run tab IS the app (its snapshot-history view
        # included). No legacy render function is called.
        render_universe_tab()
        return

    # Legacy ON: Universe Run FIRST (Streamlit default-selects it), then the pre-v2
    # council browsers (each labeled Legacy), the council-YAML editor last.
    tab_universe, tab_report, tab_history, tab_strategy = st.tabs(
        ["Universe Run", "Report · Legacy", "History · Legacy", "Strategy · Legacy"])

    with tab_universe:
        render_universe_tab()

    with tab_report:
        st.info(f"**Legacy (pre-v2).** {_LEGACY_BANNER}")
        _report_tab(ticker)

    with tab_history:
        st.info(f"**Legacy (pre-v2).** {_LEGACY_BANNER}")
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
