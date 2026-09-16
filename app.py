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
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
from pydantic import ValidationError

from aristos_council.data.adapter import (
    DataUnavailable, display_name, normalize_ticker)
from aristos_council.demo_surface import (
    strategy_label, strategy_role, suggested_first,
    universe_label, universe_role, visible_universes)
from aristos_council.costs import actual_vs_estimate, cost_phrase
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
from aristos_council.strategy.applicability import (
    applicable_rank_strategies,
    cohort_asset_kind,
    cohort_scope_note,
    out_of_scope_note,
)
from aristos_council.strategy.loader import Strategy, load_strategy
from aristos_council.strategy.picker import (
    choice_labels,
    default_index,
    resolve,
    resolve_all,
    selected_labels,
    strategy_choices,
)
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
# Auto-persisted universe-run .md/.html (UI-FIX-1) — gitignored, a disposable copy of
# what the download buttons serve, kept apart from the committed reports/<TICKER>/ tree.
UNIVERSE_RUNS_DIR = REPORTS_DIR / "universe_runs"
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
    "Earlier architecture: an LLM council issued the verdict. Demoted to narrator "
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
    council-lens screen. The rank strategies (Run tab) and the internal lens
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
    """Every RANK strategy (Run tab) as (label, path, rank_strategy),
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


def render_strategy_tab(selected_path: Path | None = None) -> None:
    """Dynamic Strategy VIEWER (Sprint 4C): renders the selected strategy ENTIRELY from
    its YAML via ``strategy.detail`` — nothing strategy-specific is hardcoded, so a new
    strategy dropped into ``strategies/`` appears fully rendered with zero UI changes.
    Editing is done in the repo (configs are versioned, never mutated in place)."""
    from aristos_council.strategy.detail import (
        PROVENANCE_NOTE, strategy_detail)
    from aristos_council.strategy.discovery import discover_strategies

    st.subheader("Strategy — config viewer")
    st.caption("Rendered entirely from the selected strategy's YAML. Add a strategy to "
               "`strategies/` and it appears here with no UI changes; configs are "
               "versioned, never edited in place.")

    infos = discover_strategies(STRATEGIES_DIR)
    if not infos:
        st.error(f"No strategies found under {STRATEGIES_DIR}")
        return
    labels = [f"{(i.display_name or i.name)} · {i.id} ({i.kind})" for i in infos]
    choice = st.selectbox("Strategy config", labels, key="strat_view_select")
    info = infos[labels.index(choice)]
    d = strategy_detail(info.id, STRATEGIES_DIR)

    # 1 — header
    st.markdown(f"### {d.display_name}")
    created = f" · created {d.created}" if d.created else ""
    st.caption(f"`{d.id}` · version {d.version} · {d.kind}{created}")
    # UNI-1 ITEM 3: the strategy↔universe pairing, rendered from YAML (present only when
    # the strategy declares suggested_universes; absent -> the header is unchanged).
    if d.suggested_universes:
        st.caption(f"Suggested universes: {', '.join(d.suggested_universes)}")

    # 2 — description (verbatim)
    if d.description:
        st.markdown(d.description)

    # 3 — screen criteria (REPORT-1: the rule's human name first, its limit in plain
    # English, and its id kept as the record key).
    from aristos_council.company_check import _criterion_label, _criterion_threshold

    st.subheader(f"Screen criteria · {d.screen_source}")
    if d.criteria:
        st.dataframe(
            [{"Rule": _criterion_label(c.name),
              "Limit": _criterion_threshold(c.name, c.threshold),
              "Gating": "gating" if c.gating else "non-gating",
              "Criterion id": c.name} for c in d.criteria],
            hide_index=True, width="stretch")
    else:
        st.caption("No screen criteria.")

    # 4 — gates (sector + rationale, market cap, payout)
    if d.gates:
        st.subheader("Gates")
        for g in d.gates:
            st.markdown(f"- **{g.name}** — {g.value}")
            if g.rationale:
                st.caption(f"↳ {g.rationale}")

    # 5 — rank factors + verdict cut
    if d.factors:
        st.subheader("Rank factors + verdict cut")
        from aristos_council.factors import FACTOR_REGISTRY
        st.dataframe(
            [{"Factor": (getattr(FACTOR_REGISTRY.get(f.name), "label", "") or f.name),
              "Better when": ("higher" if f.direction == "high" else "lower"),
              "Factor id": f.name} for f in d.factors],
            hide_index=True, width="stretch")
        st.caption(f"Verdict cut: {d.cut_rule}")

    # 6 — policy flags (plain meanings from the shared glossary)
    if d.policy:
        st.subheader("Policy")
        for p in d.policy:
            st.markdown(f"- **{p.name}** = `{p.value}` — {p.meaning}")

    # 7 — provenance footer
    st.divider()
    st.caption(f"Source: `{d.path}` · {PROVENANCE_NOTE}")


# --------------------------------------------------------------------------- #
# Run tab — the ONE run flow: strategies + a ticker list + run (FUND-UI-2), over the v2
# rank pipeline (screen -> rank -> gates -> narrator)
# --------------------------------------------------------------------------- #
# The run cap — shared by the guard and its message. It used to be re-declared per section,
# which is how the run flow and the editor drifted apart.
#
# CAP-1: ONE number became TWO, because the two run modes are capped for different reasons.
# A NARRATED run bills one LLM call per shortlisted name, so its cap protects API spend and
# stays where it was. A DETERMINISTIC run (ranker-only, and a multi-lens re-grade that does
# not narrate) spends nothing — its only cost is wall-clock on the free ranking pass, so the
# cap there protects patience, nothing more. Holding both to 60 blocked every saved oil list
# above that size (top-100 = 100, dividend-type = 135, USD-listed = 121, non-USD = 72) from a
# run that costs nothing.
UNIVERSE_CAP_NARRATED = 60
UNIVERSE_CAP_DETERMINISTIC = 250
# Kept as the NARRATED value so existing imports and tests resolve to the spend-protecting
# number — the one a bare `UNIVERSE_CAP` has always meant.
UNIVERSE_CAP = UNIVERSE_CAP_NARRATED


def universe_cap(deterministic: bool) -> int:
    """The cap that applies to a run in this mode (CAP-1) — one place, so the guard, its
    message and the Run tab's caption can never quote three different numbers."""
    return UNIVERSE_CAP_DETERMINISTIC if deterministic else UNIVERSE_CAP_NARRATED


def saved_list_labels(saved) -> list[str]:
    """Selector labels for the saved ticker lists — friendly name + size, with the id
    appended ONLY where two lists would otherwise share a label. Same discipline as the
    strategy picker: a label must name exactly one thing, or picking one silently loads
    another."""
    base = [f"{universe_label(u)} · {len(u.tickers)} names" for u in saved]
    times = Counter(base)
    return [b if times[b] == 1 else f"{b} ({u.id})" for u, b in zip(saved, base)]


def lens_checkbox_key(strategy_id: str) -> str:
    """Session-state key for one extra-lens checkbox. Keyed by the strategy ID (the stable
    record key), never by the label — so a display-name change cannot silently re-point a
    ticked box at a different config."""
    return f"uni_lens_{strategy_id}"


# --------------------------------------------------------------------------- #
# RUN MODE (RUNMODE-1) — ONE control for what used to be two
# --------------------------------------------------------------------------- #
# "Ranker only" (a checkbox) and "Council mode" (a selectbox) expressed ONE decision, and
# on a multi-lens run they lied about it: the code forced ranker_only=True internally
# while the checkbox rendered DISABLED AND UNCHECKED and the mode selector still read
# "narrator". The screen said narration would run; the run was deterministic. The values
# were right and the display was not — and a disabled widget that shows a value other
# than the one in force is worse than no widget at all.
#
# One selector now carries the decision, and the UI never shows a value it is not using.
RUN_MODE_RANKER = "ranker_only"
RUN_MODE_NARRATOR = "narrator"
RUN_MODE_SECOND_OPINION = "second_opinion"
RUN_MODES: tuple[str, ...] = (RUN_MODE_RANKER, RUN_MODE_NARRATOR,
                              RUN_MODE_SECOND_OPINION)

RUN_MODE_LABELS = {
    RUN_MODE_RANKER: "Ranker only — deterministic, no LLM, no cost",
    RUN_MODE_NARRATOR: "Narrator — the LLM explains the ranker's verdict",
    RUN_MODE_SECOND_OPINION: "Second opinion — experimental; null result, see README",
}

MULTI_LENS_LOCK_REASON = ("Several lenses is a deterministic comparison — it cannot "
                          "narrate.")


def effective_run_mode(selected: str, *, n_strategies: int) -> str:
    """The run mode ACTUALLY in force.

    NARR-UNION-1 relaxed the earlier multi-lens LOCK to a DEFAULT: a multi-lens run can
    now narrate, because "what does a cross-lens narration even say?" has an answer — one
    section per NAME over the union of every lens's BUYs, attributing each lens and
    adjudicating none. Ranker-only remains the default there (it is free), but it is the
    user's to change, so nothing is forced and nothing is displayed that is not in force."""
    return selected if selected in RUN_MODES else RUN_MODE_NARRATOR


def run_mode_locked(n_strategies: int) -> bool:
    """Is the run mode forced (and therefore not the user's to choose)? Nothing forces it
    any more — kept as the ONE place that answers the question, so a future lock has a
    home and the invariant test has something to read."""
    return False


def default_run_mode(n_strategies: int) -> str:
    """The mode a run STARTS on: ranker-only for several lenses (a cross-lens comparison
    is usually wanted for free), narrator for one."""
    return RUN_MODE_RANKER if n_strategies > 1 else RUN_MODE_NARRATOR


def run_mode_arguments(run_mode: str) -> tuple[bool, str]:
    """``(ranker_only, council_mode)`` — the EXISTING pipeline arguments this mode maps
    onto. The pipeline's signature and behaviour are untouched; this is a UI-layer
    translation only.

    Ranker-only passes ``council_mode="narrator"`` because the pipeline ignores the mode
    when ranker_only is set (it stamps the executed mode "ranker-only" itself) — the same
    inert value today's disabled selectbox already handed it, so the call is unchanged."""
    if run_mode == RUN_MODE_RANKER:
        return True, RUN_MODE_NARRATOR
    if run_mode == RUN_MODE_SECOND_OPINION:
        return False, RUN_MODE_SECOND_OPINION
    return False, RUN_MODE_NARRATOR


def run_mode_narrates(run_mode: str) -> bool:
    """Does this mode spend on an LLM? Narration coverage is shown only when it does —
    HIDDEN rather than greyed, because a greyed control still invites a reading."""
    return not run_mode_arguments(run_mode)[0]


def run_button_label(run_mode: str, *, n_strategies: int,
                     est_cost: float | None = None,
                     narrated_count: int | None = None) -> str:
    """The button says what will happen and what it costs, on its own line:

        ``▶ Run 5 lenses — deterministic, free``
        ``▶ Run 5 lenses — up to 13 names narrated, est. ≤ $0.68``
        ``▶ Run — narrated, est. $0.42``

    ``narrated_count`` is the size of the UNION of every lens's BUYs (NARR-UNION-1) — the
    thing the bill is actually proportional to. Five lenses produced 18 BUY verdicts over
    only 13 distinct names on the 2026-08-24 run, and it is the 13 that gets charged, so
    it is the 13 the button states."""
    what = f"Run {n_strategies} lenses" if n_strategies > 1 else "Run"
    if not run_mode_narrates(run_mode):
        return f"▶ {what} — deterministic, free"
    # THIS BUTTON IS FREE. It runs the deterministic ranking and charges nothing —
    # narration is offered afterwards, from a second button carrying the exact figure
    # (CONFIRM-SPEND-1). The label used to read "Run 3 lenses — up to 12 names narrated,
    # est. ≤ $2.28", which reads as this button's price; a second button then appeared at
    # a DIFFERENT price and there was no way to tell which one spent. So "free" sits
    # against the action that is free, and the upper bound is marked as describing a step
    # that has not been offered yet.
    #
    # UP TO, still: the true union is only knowable after the ranking pass (lenses
    # OVERLAP — five lenses produced 18 BUY verdicts over 13 distinct names on
    # 2026-08-24), and predicting the overlap would mean inventing a coefficient.
    # COST-2: every figure states its SCOPE. A bare "est. $0.95" could be read as the
    # total, the per-name rate or the per-lens rate; it is the total, once, for all the
    # sections, so the label says total and gives the per-name figure rather than leaving
    # the reader to divide.
    if narrated_count is not None:
        tail = (f" · ≤ {cost_phrase(est_cost, narrated_count)}"
                if est_cost is not None else "")
        return (f"▶ {what} — free · then choose whether to narrate "
                f"up to {narrated_count} names{tail}")
    verb = "narrate" if run_mode == RUN_MODE_NARRATOR else "take a second opinion"
    tail = f" · ≤ ${est_cost:.2f} total" if est_cost is not None else ""
    return f"▶ {what} — free · then choose whether to {verb}{tail}"


# COST-2: a confirmation on EVERY run is friction, not a guard. The mode was already
# chosen; a second click on a routine sub-dollar spend adds nothing but a step. So the
# guard becomes PROPORTIONATE rather than absent — above the threshold nothing is ever
# spent without an explicit click carrying the exact figure, and 0 restores "always ask".
DEFAULT_CONFIRM_THRESHOLD = 5.00


def read_threshold(raw, default: float = 0.0) -> float:
    """The confirmation threshold as a NUMBER, whatever arrives.

    ``st.number_input`` returns a float, so the comparison is already numeric — but a
    threshold that fails to parse would silently skip BOTH branches (no auto-narrate, no
    confirm panel), and "silently" is the part that matters. A European locale renders
    the widget as "5,00"; if any future path ever hands this a display string,
    ``float("5,00")`` would raise. This makes that impossible rather than merely
    unlikely: a comma-decimal is understood, and anything unparseable falls back to the
    SAFE end of the range — 0 means always ask, so a broken threshold can never cause an
    unasked spend."""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    try:
        return float(str(raw).strip().replace(" ", "").replace(" ", "")
                     .replace(",", "."))
    except (TypeError, ValueError):
        return default


def needs_confirmation(est_cost: float, threshold: float) -> bool:
    """Does this spend need an explicit second click?

    ``threshold <= 0`` means ALWAYS ask. Otherwise a spend at or below the threshold runs
    straight through and anything above it stops for confirmation. A missing estimate is
    treated as needing confirmation: not knowing the cost is not the same as it being
    small, and the safe reading of "unknown" is the one that asks."""
    if est_cost is None:
        return True
    if threshold is None or threshold <= 0:
        return True
    return est_cost > threshold


MULTI_LENS_NARRATION_NOTE = (
    "Several lenses narrate ONE section per NAME, over the union of every lens's BUYs — "
    "a name three lenses bought is narrated once, not three times. Each lens's verdict is "
    "attributed; the narrator never reconciles them.")


def _estimate_union_size(n_names: int, strategies, *,
                         narrate_coverage: str = "buys_only") -> int:
    """An UPPER BOUND on the size of the narrated union, before the free ranking pass has
    run (NARR-UNION-1).

    The true union is only knowable after ranking, so this is deliberately an upper bound
    — the same discipline the single-lens estimate already uses. Coverage ``all`` bounds
    at the whole cohort; ``buys_only`` bounds at the union of each lens's BUY tier, capped
    at the cohort (lenses OVERLAP heavily, so the sum of the tiers overstates it — the cap
    is what stops the estimate growing without limit in the lens count)."""
    if narrate_coverage == "all":
        return n_names
    total = sum(_estimate_shortlist_size(n_names, s, narrate_coverage="buys_only")
                for s in strategies)
    return min(total, n_names)


def floor_override_from_input(raw, *, file_value: float | None) -> float | None:
    """The run's floor from the sidebar's number input, in DOLLARS — or None (FLOOR-1).

    Pure, so the control's semantics are unit-tested rather than eyeballed: BLANK means
    "no override, use the strategy's own floor" (None), and a value EQUAL to the file's
    is also no override, so nudging the control back to the default leaves the run
    byte-identical rather than recording a no-op. The input is in BILLIONS because that is
    how the floor is discussed; the pipeline takes dollars."""
    if raw is None:
        return None
    dollars = float(raw) * 1e9
    return None if file_value is not None and dollars == file_value else dollars


def _company_size_floor_override(rank_strategy, n_strategies: int) -> float | None:
    """The Run tab's ephemeral company-size floor control (FLOOR-1).

    Defaults to the PRIMARY strategy's own floor, so the control opens showing what the
    run would do untouched; clearing it removes the floor for this run entirely."""
    file_value = getattr(rank_strategy, "min_market_cap", None)
    with st.expander("⚙️ Run overrides — this run only", expanded=False):
        st.caption("Applied to THIS run only and stamped on the report. The strategy "
                   "file is never modified.")
        raw = st.number_input(
            "Company size floor (USD bn)",
            min_value=0.0, max_value=5_000.0, step=0.5, value=None,
            placeholder=(f"{file_value / 1e9:g} (strategy default)"
                         if file_value else "no floor (strategy default)"),
            key="uni_floor_override",
            help="Blank = use the strategy's own floor. The floor is applied BEFORE the "
                 "screen and before any factor, so a name below it is excluded before "
                 "anything is measured about it. 0 removes the floor for this run.")
        override = floor_override_from_input(raw, file_value=file_value)
        if override is not None:
            from aristos_council.pipeline import format_floor
            lenses = ("every lens in this run" if n_strategies > 1
                      else "this run")
            st.caption(f"Floor for {lenses}: **{format_floor(override)}** "
                       f"(strategy file: {format_floor(file_value)}).")
    return override


def run_problems(universe: list[str], *, n_strategies: int, deterministic: bool,
                 has_key: bool, cap: int | None = None) -> list[str]:
    """Why the Run button is disabled, in plain sentences (empty list = runnable).

    Pure, so the one run flow's guards are unit-tested rather than eyeballed in a browser —
    and there is now ONE guard set instead of the two that had already drifted. A
    ``deterministic`` run (ranker-only, or several strategies — which is ranker-only by
    construction) cannot spend, so it never asks for an API key.

    CAP-1: the cap follows the run MODE unless an explicit ``cap=`` overrides it — a
    narrated run is capped to protect API spend, a deterministic one only to protect
    patience, so a 135-name ranker-only run is no longer refused for a cost it cannot incur.
    """
    if cap is None:
        cap = universe_cap(deterministic)
    problems: list[str] = []
    if n_strategies < 1:
        problems.append("Pick at least one strategy.")
    if not universe:
        problems.append("Add at least one ticker.")
    if len(universe) > cap:
        # The message names the MODE, so the number is never mistaken for a single global
        # limit — and the narrated case points at the way out rather than only forbidding.
        if deterministic:
            problems.append(f"List too large ({len(universe)} > {cap}) for an "
                            "interactive run — trim it.")
        else:
            problems.append(f"List too large ({len(universe)} > {cap}) for a narrated "
                            "run — trim it, or switch to Ranker only.")
    if not deterministic and not has_key:
        problems.append("Narrator / second-opinion needs ANTHROPIC_API_KEY (set it "
                        "in the environment or a local .env). Use **Ranker only** to "
                        "run with no LLM and no cost.")
    return problems


def _estimate_shortlist_size(n: int, rank_strategy, *,
                             narrate_coverage: str = "buys_only") -> int:
    """Rough shortlist size for a pre-run cost hint (exact size is known only after
    the free ranking pass, which exclusions shrink). ``narrate_coverage='all'``
    narrates every ranked name, so the estimate is the whole (pre-screen) universe."""
    if n == 0:
        return 0
    if narrate_coverage == "all":
        return n
    runs_on = rank_strategy.council_runs_on
    if runs_on == "all":
        return n
    if runs_on == "top_k" or rank_strategy.cut == "top_k":
        return min(rank_strategy.k, n)
    return max(1, round(n / 5))          # buy_quintile


def _ranked_rows(ranked, names: dict | None = None) -> tuple[list[dict], list[str]]:
    """Rows + the ordered factor columns for the ranked table — a thin delegate to the
    ONE shared builder (`rank_engine.ranked_table_rows`), so the screen table, the
    markdown download and the HTML export render byte-identical cells (REPORT-HTML-1
    moved the body there; behavior unchanged)."""
    from aristos_council.rank_engine import ranked_table_rows

    return ranked_table_rows(ranked, names)


def _confirmation_line(m: dict) -> str:
    """The always-rendered pre-run confirmation (ITEM 6): a wrong dropdown is visible in
    the first second and in every exported report. Uses the truthful executed mode."""
    return (f"Running {m['rank_strategy_id']} on "
            f"{m.get('universe_id') or 'adhoc'} in {m['council_mode']}.")


def _render_valuation_band_table(table) -> None:
    """The valuation-band section in the Run tab (PRICE-2): the intro, the TABLE, then the
    footnotes underneath. Renders the cells ``pipeline.valuation_band_table`` produced —
    the same ones the markdown record, the HTML export and the CLI show — so nothing here
    formats a number of its own. Renders NOTHING when no name carried a band."""
    if table is None:
        return
    st.subheader(table.title)
    st.caption(table.intro)
    st.dataframe([{c: row[c] for c in table.columns} for row in table.rows],
                 hide_index=True, width="stretch")
    for note in table.footnotes:
        st.caption(note)


def _md_table(columns, rows, *, bold_first: bool = True) -> list[str]:
    """A markdown pipe table from already-rendered cells. One helper, so every markdown
    table in the report is built the same way and a cell containing a pipe cannot break
    the layout."""
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    for row in rows:
        cells = [str(row[c]).replace("|", "\\|") for c in columns]
        if bold_first and cells:
            cells[0] = f"**{cells[0]}**"
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _valuation_band_markdown(table) -> list[str]:
    """The price-and-valuation section as markdown (PRICE-2, extended by REPORT-1): at
    most two sentences, then the TABLE, then the footnotes. Reads
    ``pipeline.valuation_band_table`` — the ONE source the Run tab, the HTML export and
    the CLI also read — so the four cannot drift. ``[]`` when the run carries neither a
    price nor a band for any name."""
    if table is None:
        return []
    lines = ["", f"## {table.title}", "", table.intro, ""]
    lines += _md_table(table.columns, table.rows)
    lines.append("")
    lines += [f"- {note}" for note in table.footnotes]
    return lines


def _rules_applied_markdown(result, *, include_title: bool = True) -> list[str]:
    """The RULES APPLIED block as markdown (REPORT-1) — every rule the run applied, its
    limit in plain English and what it did, BEFORE any result. Read from the strategies
    that actually ran, so editing one changes this block with no code change.

    ``include_title=False`` for the MERGED report, where this block is already nested
    under "Rules applied — by lens" and a per-lens "### <lens>" heading. It used to emit
    its own "## Rules applied" there too, so a three-lens document carried four headings
    of that name at two different depths — noise in the heading tree, and now noise in
    the contents list REPORT-4 builds from it."""
    from aristos_council.pipeline import RULES_SECTION_TITLE, rules_applied

    block = rules_applied(result)
    if block is None:
        return []
    lines = (["", f"## {RULES_SECTION_TITLE}", ""] if include_title else [])
    lines += [f"**{block.screen_heading}**", "", block.screen_note, ""]
    if block.rules:
        cols = ["Rule", "Limit", "What it did"]
        if any(r.measured for r in block.rules):
            cols.append("Measured on")
        rows = [{"Rule": f"{r.label} `{r.criterion}`", "Limit": r.threshold_phrase,
                 "What it did": r.tally, "Measured on": r.measured or "—"}
                for r in block.rules]
        lines += _md_table(cols, rows, bold_first=False)
        lines.append("")
    lines += [f"- {line}" for line in block.ranker_lines]
    return lines


def _universe_markdown(result) -> str:
    """The run as a self-contained markdown doc (the download; NO new storage format
    this sprint — the pipeline does not persist reports).

    REPORT-1: the header leads with the HUMAN names and keeps every id beside them as
    the stable record key, a one-line verdict summary sits directly under it, and the
    rules that were applied are stated before any result."""
    from aristos_council.pipeline import (floor_override_line, header_lines, lens_asks,
                                          summary_line)
    from aristos_council.report_language import label_with_id

    m = result.meta
    head = header_lines(result)
    lines = [f"# Universe run — {head[0]}", ""]
    lines += [f"**{line}**" for line in head[1:]]
    # FLOOR-1: one line under the header when the cohort was widened for this run;
    # absent otherwise, so a no-override report is byte-identical to before.
    _floor = floor_override_line(m)
    if _floor:
        lines.append(f"**{_floor}**")
    # CAPTION-1: what this lens asks of a company, under the header. Absent -> no line.
    _asks = lens_asks(result)
    if _asks:
        lines += ["", f"_{_asks}_"]
    lines += ["", f"### {summary_line(result)}", "",
              f"_{_confirmation_line(m)}_", "",
              f"_{result.header}_", "",
              f"- Screen: {label_with_id(m.get('screen_strategy_name', ''), m['screen_strategy_id'])}",
              f"- Ranked: {m['ranked_count']} of {m['universe_size']} names"]
    if m.get("run_id"):
        lines.append(f"- Run id: `{m['run_id']}`")
    if not m["ranker_only"]:
        lines.append(f"- Shortlist: {len(m['shortlist'])} names · estimated cost "
                     f"${m['est_cost']:.2f}")
        lines.append(f"- narration coverage: {m.get('narrate_coverage', 'buys_only')}")
    # REPORT-1: every rule that was applied, with its limit and what it did, BEFORE any
    # result — the header used to name only the screen's id.
    lines += _rules_applied_markdown(result)

    from aristos_council.pipeline import (
        PROVENANCE_SECTION_NOTE, PROVENANCE_SECTION_TITLE, provenance_sentences,
        untested_rule_notes, used_symbol_notes,
    )
    from aristos_council.rank_engine import factor_column_label
    from aristos_council.report_language import format_score_gloss

    lines += ["", "## Ranked — the verdict of record", ""]
    rows, factor_names = _ranked_rows(result.ranked, result.names)
    if rows:
        n_factors = next((len(r.factor_ranks) for r in result.ranked if r.factor_ranks),
                         0)
        lines += [format_score_gloss(n_factors, len(result.ranked)), ""]
        labels = [factor_column_label(f) for f in factor_names]
        lines += _md_table(["Position (score)", "Name", "Verdict", *labels], rows,
                           bold_first=False)
        lines += ["", "Factor ids, in column order: "
                      + ", ".join(f"`{f}`" for f in factor_names) + "."]
    else:
        lines.append("_(no names survived the screen)_")

    symbols = used_symbol_notes(result)
    if symbols:
        lines.append("")
        lines += [f"- `{sym}` — {note}" for sym, note in symbols]

    # REPORT-1: a rule that could not be TESTED, in words, beside the verdict it
    # qualifies — it used to be a bare dagger in the name column.
    untested = untested_rule_notes(result)
    if untested:
        lines += ["", "## Rules that could not be tested", "",
                  "_These names PASSED the screen — a rule that cannot be evaluated "
                  "never excludes anyone — but one of its rules returned no answer for "
                  "them at all, so their pass is thinner than it looks._", ""]
        for n in untested:
            count = len(n["rules"])
            lines.append(f"- **{n['name']}** — {n['verdict']} — {count} rule"
                         f"{'s' if count != 1 else ''} could not be tested: "
                         + "; ".join(n["rules"]))

    entries = provenance_sentences(result)
    if entries:
        lines += ["", f"## {PROVENANCE_SECTION_TITLE}", "",
                  f"_{PROVENANCE_SECTION_NOTE}_", ""]
        lines += [f"- {e['sentence']}" for e in entries]
    # VALBAND-1 / PRICE-2 / REPORT-1: the absolute counterpart to the ranked table, in
    # the RECORD too — a rank position ages into "it was cheapest of those"; the price
    # and the band age into "it cost $119.85 and sat at the 1st percentile of its own
    # five years", which is the sentence a reader of an old run actually needs.
    from aristos_council.pipeline import exclusion_rows, valuation_band_table

    # PRICE-1/PRICE-2/REPORT-1: price, 12-month range and valuation in ONE table — the
    # same names are no longer listed twice in two sections.
    lines += _valuation_band_markdown(valuation_band_table(result))
    if result.excluded:
        lines += ["", "## Excluded — did not pass a rule, so was never ranked", ""]
        for row in exclusion_rows(result):
            muted = f" `{row['criterion']}`" if row["criterion"] else ""
            lines.append(f"- **{row['name']}** — {row['sentence']}{muted}")
            if row["flag"]:
                lines.append(f"  - {row['flag']}")
    if result.unrateable:
        lines += ["", "## No usable data — no verdict was formed", ""]
        lines += [f"- **{display_name(t, result.names.get(t))}** — {why}"
                  for t, why in result.unrateable]
    if result.narratives:
        lines += ["", "## Narrative", ""]
        for t, text in result.narratives.items():
            lines += [f"### {display_name(t, result.names.get(t))}", "", text, ""]
    lines += _cohort_membership_lines(result.meta)
    return "\n".join(lines)


def _cohort_membership_lines(meta: dict) -> list[str]:
    """The exact membership this run graded, recorded in the canonical markdown record
    (FUND-UI-2). A saved list is editable, so its id alone dates badly — and a rank
    position is a statement about the names it was ranked AGAINST. Kept at the foot of
    the report and out of the run flow entirely: the record needs it, the UI does not
    need a versioning ceremony to produce it. Older results carry no members, and then
    NO section is emitted — an empty block would imply nothing was graded."""
    members = meta.get("universe_members") or []
    if not members:
        return []
    return ["", "## Cohort graded (exact membership)", "",
            f"- list: `{meta.get('universe_id') or 'adhoc'}` · {len(members)} names · "
            f"members `{meta.get('universe_member_hash', '')}`",
            "", ", ".join(members), ""]


def _persist_universe_run(result, run_start: datetime,
                          universe_display_name: str) -> tuple[Path, Path]:
    """Auto-persist this run's markdown + HTML to reports/universe_runs/ (UI-FIX-1) the
    moment the run completes, before rendering — a Streamlit restart before download
    must never again destroy a completed (possibly paid) run. Byte-identical to what the
    download buttons serve: both read the SAME builder functions."""
    from aristos_council.download_names import (
        universe_download_name, universe_html_download_name)
    from aristos_council.export.report_html import universe_report_html
    from aristos_council.persistence.universe_runs import save_universe_run

    m = result.meta
    md_name = universe_download_name(m["rank_strategy_id"], m["council_mode"], run_start,
                                     universe_display_name=universe_display_name)
    html_name = universe_html_download_name(
        m["rank_strategy_id"], m["council_mode"], run_start,
        universe_display_name=universe_display_name)
    md_bytes = _universe_markdown(result).encode("utf-8")
    html_bytes = universe_report_html(result, run_start=run_start).encode("utf-8")
    return save_universe_run(md_bytes, html_bytes, md_name=md_name, html_name=html_name,
                             out_dir=UNIVERSE_RUNS_DIR)


def _shown_path(path) -> str:
    """A written file's path for display: relative to the repo when it lives there, the
    full path otherwise. ``Path.relative_to`` RAISES on a path outside the root, which
    turned a successful save into a crashed render the moment the sink was pointed
    anywhere else."""
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def _supersede(run_start, new_paths) -> None:
    """ONE RUN, ONE .md AND ONE .html (CONFIRM-SPEND-1).

    A run can publish TWICE — once for the free ranking, once after narration is
    confirmed — and the second pair is named for the narrated mode, so it does not
    overwrite the first. Left alone that strands a stale ranker-only pair beside the real
    report, and a reader sorting by name cannot tell which describes the run. The earlier
    pair for the SAME run-start is therefore deleted as the new one lands. Files from any
    OTHER run are never touched: the run-start must match."""
    previous = st.session_state.get("uni_published")
    st.session_state["uni_published"] = (run_start, [Path(p) for p in new_paths])
    if not previous:
        return
    when, paths = previous
    if when != run_start:
        return
    keep = {Path(p).name for p in new_paths}
    for p in paths:
        if Path(p).name not in keep:
            Path(p).unlink(missing_ok=True)


def _publish_multi(multi_result, run_start, universe_display_name) -> None:
    """Report a finished multi-lens run: persist it and put it on screen. Shared by the
    free path and the confirmed-narration path, so a kept ranking is reported exactly as
    a narrated one is — the ranking work is never thrown away (CONFIRM-SPEND-1).

    The files are re-rendered from the result HANDED IN, so a narrated result writes a
    narrated report under a narrated filename: nothing about the mode, the header or the
    name is carried over from an earlier publish of the same run."""
    st.session_state["uni_multi_result"] = multi_result
    st.session_state["uni_persisted_paths"] = None
    # REPORT-2: ONE merged report for the whole run, however many lenses ran. (The
    # per-strategy RECORDS are untouched — every column was frozen under runs/ by its own
    # run_rank_pipeline call, so each stays replayable.)
    paths = _persist_multi_strategy_run(multi_result, run_start, universe_display_name)
    st.session_state["uni_multi_persisted"] = paths
    _supersede(run_start, paths)


def _publish_single(result, run_start, universe_display_name) -> None:
    """Report a finished single-lens run — same contract as ``_publish_multi``."""
    st.session_state["uni_result"] = result
    # UI-FIX-1: persist BEFORE rendering — a completed (possibly paid) run must survive a
    # restart even if nobody clicks a download button.
    paths = _persist_universe_run(result, run_start, universe_display_name)
    st.session_state["uni_persisted_paths"] = paths
    _supersede(run_start, paths)


def _render_narration_confirmation() -> None:
    """CONFIRM-SPEND-1 — the confirmation step, between the free ranking and the spend.

    The ranking has ALREADY happened, so this states the EXACT count and the EXACT
    estimate — no upper bound, no coefficient, and the names themselves. Money is only
    ever spent from the button that carries that figure. "Keep the free ranking" reports
    the run exactly as a ranker-only run: the work is done and is never discarded."""
    from aristos_council.pipeline import (
        narrate_multi_strategy, narrate_rank_result, narration_plan)

    pending = st.session_state.get("uni_pending_narration")
    if not pending:
        return
    result = pending["result"]
    plan = narration_plan(result, pending.get("coverage", "buys_only"))
    run_start = st.session_state.get("uni_run_start") or datetime.now(timezone.utc)
    display_name = st.session_state.get("uni_universe_display_name", "")

    if not plan["count"]:
        # Nothing to narrate — there is no spend to confirm, so do not ask.
        st.session_state.pop("uni_pending_narration", None)
        (_publish_multi if pending["kind"] == "multi" else _publish_single)(
            result, run_start, display_name)
        st.info("The ranking produced no name to narrate, so the run is complete and "
                "nothing was charged.")
        return

    st.divider()
    st.subheader("Ranking complete — confirm the narration spend")
    # COST-2: the figure states its SCOPE. "est. $0.95" could be the total, the per-name
    # rate or the per-lens rate; it is the total, once, for all the sections.
    priced = cost_phrase(plan["est_cost"], plan["count"])
    st.markdown(_md(
        f"### {plan['count']} names rated BUY by at least one lens — "
        f"narrate all {plan['count']} for {priced}?"
        if pending["kind"] == "multi" else
        f"### {plan['count']} names to narrate — narrate all "
        f"{plan['count']} for {priced}?"))
    st.caption(f"Exact figures from the ranking that has just run — {plan['basis']}. "
               "Nothing has been charged yet.")
    names = getattr(result, "rows", None)
    if names is not None:
        labels = {row.ticker: row.display for row in result.rows}
    else:
        labels = {t: display_name_of(result, t) for t in plan["names"]}
    st.write(", ".join(labels.get(t, t) for t in plan["names"]))

    c1, c2 = st.columns(2)
    with c1:
        confirmed = st.button(
            _md(f"▶ Narrate — ${plan['est_cost']:.2f} total"),
            type="primary", key="uni_confirm_narrate")
    with c2:
        kept = st.button("Keep the free ranking", key="uni_keep_ranking")

    # DOWNLOADING AND NARRATING ARE NOT ALTERNATIVES. The ranker-only report is already
    # written by the time this panel renders, so its files are offered right here rather
    # than only after the narrate/keep choice is made. Narrating replaces this pair
    # (_supersede); keeping leaves it exactly as it is.
    _render_pending_downloads()

    if kept:
        # The ranking is DONE and already reported — this only dismisses the offer.
        # Nothing is re-run, nothing is re-persisted, nothing is charged.
        st.session_state.pop("uni_pending_narration", None)
        st.rerun()

    if confirmed:
        status = st.status("Narrating…", expanded=True)
        if _run_narration(pending, status=status, run_start=run_start,
                          display_name=display_name):
            st.rerun()


def _run_narration(pending, *, status, run_start, display_name) -> bool:
    """Narrate a completed ranking and republish. ``True`` when it succeeded.

    ONE implementation for both entry points — the confirm button and the under-threshold
    automatic path — so the two can never diverge on what narrating means, what it costs,
    or which files it leaves behind."""
    from aristos_council.pipeline import narrate_multi_strategy, narrate_rank_result

    result = pending["result"]
    try:
        if pending["kind"] == "multi":
            narrated = narrate_multi_strategy(
                result, coverage=pending.get("coverage", "buys_only"),
                progress=lambda msg: status.update(label=msg))
        else:
            narrated = narrate_rank_result(
                result, mode=pending.get("mode", "narrator"),
                progress=lambda msg: status.update(label=msg))
    except Exception as exc:
        status.update(label="Narration failed", state="error")
        st.exception(exc)
        return False
    status.update(label="Done.", state="complete")
    st.session_state.pop("uni_pending_narration", None)
    (_publish_multi if pending["kind"] == "multi" else _publish_single)(
        narrated, run_start, display_name)
    # COST-3: what it ACTUALLY cost, beside what was estimated — so the estimate can be
    # checked against reality instead of being quoted for ever on trust.
    meta = narrated.meta or {}
    st.session_state["uni_last_spend"] = {
        "names": meta.get("narrated_count") or len(narrated.narratives or {}),
        "actual": meta.get("actual_cost"), "estimated": meta.get("est_cost")}
    return True


def _offer_or_narrate(pending, *, threshold, status, run_start, display_name) -> None:
    """COST-2 — confirm only when it matters.

    The ranking is already done and already reported. Below the threshold the run simply
    continues into narration (the mode was chosen before the click; a second click on a
    routine sub-dollar spend adds a step and no information). Above it, the run STOPS and
    the confirm panel states the exact count and figure — nothing above the threshold is
    ever spent without an explicit click."""
    from aristos_council.pipeline import narration_plan

    plan = narration_plan(pending["result"], pending.get("coverage", "buys_only"))
    if not plan["count"]:
        status.update(label="Done.", state="complete")
        st.session_state.pop("uni_pending_narration", None)
        return
    if needs_confirmation(plan["est_cost"], threshold):
        status.update(label="Ranked — confirm the narration spend.", state="complete")
        st.session_state["uni_pending_narration"] = pending
        return
    st.session_state.pop("uni_pending_narration", None)
    status.update(label=_md(
        f"Ranked — narrating {plan['count']} name(s), "
        f"{cost_phrase(plan['est_cost'], plan['count'])}…"), state="running")
    _run_narration(pending, status=status, run_start=run_start,
                   display_name=display_name)


def _render_last_spend() -> None:
    """COST-3 — the run flow's report of what the last narration actually cost."""
    spend = st.session_state.get("uni_last_spend")
    if not spend:
        return
    line = actual_vs_estimate(spend.get("actual"), spend.get("estimated"),
                              spend.get("names") or 0)
    st.success(_md(f"Narrated {spend.get('names', 0)} names — {line}."))


def _render_pending_downloads() -> None:
    """The completed ranking's .md and .html, offered inside the confirm panel.

    Read from the files ALREADY ON DISK rather than re-rendered, so what downloads is
    byte-identical to what was persisted — and so this cannot become a second place that
    decides what a report says. Silent when a run somehow persisted nothing, rather than
    offering a button that would hand back an empty file."""
    paths = (st.session_state.get("uni_multi_persisted")
             or st.session_state.get("uni_persisted_paths"))
    if not paths:
        return
    st.caption("The deterministic ranking is already saved — download it now if you "
               "like. Narrating replaces these two files; keeping leaves them as they "
               "are.")
    cols = st.columns(len(paths))
    mimes = {".md": "text/markdown", ".html": "text/html"}
    for col, path in zip(cols, paths):
        path = Path(path)
        if not path.exists():
            continue
        with col:
            st.download_button(
                f"⬇ {path.suffix.lstrip('.') or 'file'} — the free ranking",
                data=path.read_bytes(), file_name=path.name,
                mime=mimes.get(path.suffix, "text/plain"),
                key=f"uni_pending_dl_{path.suffix.lstrip('.')}")


def display_name_of(result, ticker: str) -> str:
    return display_name(ticker, (getattr(result, "names", None) or {}).get(ticker))


def _persist_multi_strategy_run(multi_result, run_start: datetime,
                                universe_display_name: str) -> tuple[Path, Path]:
    """Auto-persist a multi-lens run as ONE markdown + ONE HTML (REPORT-2).

    A run used to write one standalone pair PER LENS — four files for four lenses, each
    repeating the same cohort, prices and valuation band. It now writes exactly one pair
    covering every lens, named for the cohort and the LENS COUNT rather than for any one
    strategy (none of them owns the file).

    RECORD LAYER UNTOUCHED: this only changes the human-facing REPORT files. Each
    strategy's run is still frozen individually under ``runs/`` by
    ``run_rank_pipeline(freeze_dir=…)``, still carries its own membership manifest and
    member hash, and still grades individually on forward returns."""
    from aristos_council.download_names import multi_universe_download_name
    from aristos_council.export.report_html import multi_strategy_report_html
    from aristos_council.persistence.universe_runs import save_universe_run

    from aristos_council.report_language import cohort_filename_slug

    n = len(multi_result.strategy_ids)
    mode = multi_result.meta.get("council_mode", "ranker-only")
    # REPORT-4 part 4: an edited list's file was named `universe_3lenses_...` — the cohort
    # segment omitted, because the display name is blank on an ad-hoc run. It now falls
    # back to the same description the header leads with, so a folder of reports reads
    # the way the documents do.
    cohort_slug = universe_display_name or cohort_filename_slug(multi_result.meta)
    md_name = multi_universe_download_name(
        n, mode, run_start, universe_display_name=cohort_slug)
    html_name = multi_universe_download_name(
        n, mode, run_start, ext="html", universe_display_name=cohort_slug)
    md_bytes = _multi_strategy_markdown(multi_result, run_start).encode("utf-8")
    html_bytes = multi_strategy_report_html(
        multi_result, run_start=run_start).encode("utf-8")
    return save_universe_run(md_bytes, html_bytes, md_name=md_name,
                             html_name=html_name, out_dir=UNIVERSE_RUNS_DIR)


def _multi_columns(multi_result) -> dict[str, str]:
    """strategy_id -> its column header — a thin delegate to the shared builder."""
    from aristos_council.pipeline import multi_strategy_columns

    return multi_strategy_columns(multi_result)


def _multi_grid_rows(multi_result) -> list[dict]:
    """The verdict table's rows — a thin delegate to the ONE shared builder
    (``pipeline.multi_strategy_grid_rows``), so the Run tab, the merged markdown and the
    merged HTML export render byte-identical cells (REPORT-2 moved the body there;
    behaviour unchanged apart from the column order stated in the spec)."""
    from aristos_council.pipeline import multi_strategy_grid_rows

    return multi_strategy_grid_rows(multi_result)[0]


def _multi_strategy_markdown(multi_result, run_start=None) -> str:
    """ONE merged markdown report for the whole run, however many lenses ran (REPORT-2).

    Before this, ticking three extra lenses wrote FOUR standalone documents, each
    repeating the same cohort, the same prices and the same valuation band — so
    comparing lenses meant opening four files side by side. This is the single document
    the scout verdict reports are modelled on: one header, the rules each lens applied,
    one verdict table with a column per lens, the per-NAME facts stated ONCE, and then
    only the things that genuinely differ per lens.

    RECORD LAYER UNTOUCHED: only the human-facing report merges. Each strategy's run is
    still frozen individually under ``runs/`` (so it replays), still carries its own
    membership manifest and member hash, and still grades individually on forward
    returns. Merging the reports drops no per-strategy record."""
    from aristos_council.pipeline import (
        CONTENTS_TITLE, EVIDENCE_GAPS_NOTE, EVIDENCE_GAPS_TITLE, compact_rules,
        evidence_gaps_clean_note,
        VERDICT_TABLE_NOTE, VERDICT_TABLE_TITLE, evidence_gaps, exclusion_rows,
        multi_header_line, multi_strategy_grid_rows, multi_summary_line,
        floor_override_line,
        lens_asks,
        provenance_sentences, report_sections,
        union_valuation_band_table,
        valuation_band_table,
    )
    from aristos_council.export.report_html import DISCLAIMER, DOCTRINE
    from aristos_council.report_language import cohort_title, label_with_id

    m = multi_result.meta
    ids = multi_result.strategy_ids
    names = multi_result.strategy_names
    cohort = label_with_id(m.get("universe_name", ""), m.get("universe_id") or "adhoc")
    # REPORT-4 part 4: the HUMAN description leads; the record id follows it, never
    # replaces it. Markdown cannot mute, so the id is parenthesised — present, secondary.
    title = cohort_title(m, n_lenses=len(ids))

    # 1 — ONE header, not four.
    lines = [f"# Universe run — {title.headline}", "", f"`{title.record_id}`", ""]
    lines.append(f"**Cohort: {cohort} — {m.get('universe_size', 0)} names**")
    lines.append(f"**Lenses: " + "; ".join(
        label_with_id(names.get(sid) or sid, sid) for sid in ids) + "**")
    # FLOOR-1: one line under the lenses when the cohort was widened for this run. Empty
    # (and so absent) otherwise, which keeps a no-override report byte-identical.
    _floor = floor_override_line(m)
    if _floor:
        lines.append(f"**{_floor}**")
    if run_start is not None:
        lines.append(f"**Run: {_local_stamp(run_start)} — "
                     f"{_mode_phrase(m.get('council_mode', ''))}**")
    else:
        lines.append(f"**Run: {_mode_phrase(m.get('council_mode', ''))}**")
    lines += ["", f"_{multi_header_line(multi_result)}_", ""]
    lines += [f"### {multi_summary_line(multi_result)}", ""]

    # RULES-TOP-1 — what each lens IS, at the point the reader meets its verdicts. The
    # full tables stay below, as reference; this is one line each, linked to them.
    compact = compact_rules(multi_result)
    if compact:
        lines += [" · ".join(f"**{r['lens']}** — {r['summary']}" for r in compact)
                  + "  ([details](#rules-applied--by-lens))", ""]

    # REPORT-4 part 3 — the contents list. The .md carries the same structure without
    # relying on HTML anchors: markdown renderers derive their own heading ids, and a
    # plain-text reader still gets the document's map and its order.
    lines += _contents_markdown(report_sections(multi_result))

    # 2 (REPORT-4) — what the run could NOT see, BEFORE any prose that rests on what it
    # could. Rendered even when clean: an absent section is indistinguishable from a
    # feature that was never switched on.
    gaps = evidence_gaps(multi_result)
    lines += ["", f"## {EVIDENCE_GAPS_TITLE}", "", f"_{EVIDENCE_GAPS_NOTE}_", ""]
    if gaps:
        lines += _md_table(["Channel", "Why it is dark", "Names affected"],
                           [{"Channel": g["channel"], "Why it is dark": g["reason"],
                             "Names affected": ", ".join(g["names"])} for g in gaps])
    else:
        lines.append(f"_{evidence_gaps_clean_note(multi_result)}_")

    # 3 — THE VERDICT TABLE: the answer. One row per name, one column per lens.
    lines += ["", f"## {VERDICT_TABLE_TITLE}", "", VERDICT_TABLE_NOTE, ""]
    rows, head = multi_strategy_grid_rows(multi_result)
    if rows:
        lines += _md_table(head, rows)
    else:
        lines.append("_(no names reported)_")

    # 4 — NARR-UNION-1: ONE narration section per NAME, over the union of every lens's
    # BUYs — the WHY, straight after the answer.
    lines += _multi_narration_markdown(multi_result)

    # 5 — the per-NAME facts, ONCE: they do not vary by lens.
    # BAND-2: the UNION of every lens's ranked names — a lens bands only what it ranked,
    # so the first lens's ranked set must not decide who gets a row.
    if ids:
        lines += _valuation_band_markdown(union_valuation_band_table(multi_result))

    # 6 — rules applied, ONE sub-block per lens (each has its own screen and thresholds).
    # REFERENCE material: it sits after the answer, not in front of it. A reader used to
    # meet three screens' worth of thresholds before learning what the run decided.
    lines += ["", "## Rules applied — by lens", "",
              "_Each lens screens on its own rules; a name excluded by one may be ranked "
              "by another. The rules below are read from the strategies that actually "
              "ran._"]
    for sid in ids:
        lines += ["", f"### {label_with_id(names.get(sid) or sid, sid)}"]
        # CAPTION-1: the question, under the lens's name, so the rules read as the answer
        # to something. Absent `asks` adds no line.
        _asks = lens_asks(multi_result.results[sid])
        if _asks:
            lines += ["", f"_{_asks}_"]
        lines += _rules_applied_markdown(multi_result.results[sid],
                                         include_title=False)

    # 7 — what DOES vary per lens.
    for sid in ids:
        res = multi_result.results[sid]
        lines += ["", f"## {label_with_id(names.get(sid) or sid, sid)} — detail", ""]
        if lens_asks(res):                                   # CAPTION-1
            lines += [f"_{lens_asks(res)}_", ""]
        lines += [f"- Ranked: {res.meta['ranked_count']} of "
                  f"{res.meta['universe_size']} names"]
        if res.excluded:
            lines += ["", "**Excluded — did not pass a rule, so was never ranked**", ""]
            for row in exclusion_rows(res):
                muted = f" `{row['criterion']}`" if row["criterion"] else ""
                lines.append(f"- **{row['name']}** — {row['sentence']}{muted}")
                if row["flag"]:
                    lines.append(f"  - {row['flag']}")
        if res.unrateable:
            lines += ["", "**No usable data — no verdict was formed**", ""]
            lines += [f"- **{display_name(t, res.names.get(t))}** — {why}"
                      for t, why in res.unrateable]
        if res.fetch_errors:
            lines += ["", "**Data fetch failed — re-run to recover**", ""]
            lines += [f"- **{display_name(t, res.names.get(t))}** — {why}"
                      for t, why in res.fetch_errors]
        entries = provenance_sentences(res)
        if entries:
            lines += ["", "**Where the numbers came from**", ""]
            lines += [f"- {e['sentence']}" for e in entries]

    # ONE cohort under N lenses — so ONE membership record covers the whole grid.
    lines += _cohort_membership_lines(m)

    # GLOSSARY-1 — the LAST section before the footer, and only the terms this run used.
    # It reads the document rendered SO FAR, so a term is defined because the report
    # actually says it, not because the registry knows about it.
    lines += _glossary_markdown(multi_result, "\n".join(lines))
    # 7 — ONE common footer.
    lines += ["", "---", "", f"_{DOCTRINE}_", "", f"_{DISCLAIMER}_", ""]
    return "\n".join(lines)


def _glossary_markdown(multi_result, rendered: str) -> list[str]:
    """The report's own glossary, from the registries and from what this run used."""
    from aristos_council.factors import FACTOR_REGISTRY
    from aristos_council.glossary import glossary_entries, glossary_markdown
    from aristos_council.pipeline import rules_applied
    from aristos_council.tools.criteria.registry import REGISTRY

    factors, criteria = {}, {}
    for sid in multi_result.strategy_ids:
        res = multi_result.results[sid]
        for row in res.ranked:
            for fname in (row.factor_ranks or {}):
                if fname in FACTOR_REGISTRY:
                    factors[fname] = FACTOR_REGISTRY[fname]
        block = rules_applied(res)
        for rule in (block.rules if block else []):
            if rule.criterion in REGISTRY:
                criteria[rule.criterion] = REGISTRY[rule.criterion]
    return glossary_markdown(glossary_entries(
        factors=factors.values(), criteria=criteria.values(), text=rendered))


def _contents_markdown(sections) -> list[str]:
    """REPORT-4 part 3 — the contents list in markdown.

    The .md is the CANONICAL record and must stand alone, so this carries the document's
    order and its narrated names as a plain nested list. Markdown renderers derive their
    own heading ids from the heading text, so the links resolve where they are rendered
    and the list still reads as a map where they are not — no HTML anchors, no colour."""
    from aristos_council.pipeline import CONTENTS_TITLE

    def _items(nodes, depth=0) -> list[str]:
        out = []
        for node in nodes:
            out.append(f"{'  ' * depth}- {node['title']}")
            out += _items(node["children"], depth + 1)
        return out

    body = _items(sections)
    return ["", f"## {CONTENTS_TITLE}", ""] + body + [""] if body else []


def _multi_narration_markdown(multi_result) -> list[str]:
    """The narration sections of a multi-lens run (NARR-UNION-1) — ONE per NAME, headed by
    the name, in the verdict table's own order. ``[]`` on a ranker-only run."""
    if not multi_result.narratives:
        return []
    m = multi_result.meta
    basis = m.get("narration_basis", "")
    count = m.get("narrated_count", len(multi_result.narratives))
    lines = ["", "## Narration", "",
             f"_{count} name{'s' if count != 1 else ''} narrated — {basis}. ONE section "
             "per NAME: a name several lenses bought is narrated once, with each lens's "
             "verdict attributed. The narrator explains the ranker's verdicts; it never "
             "weighs the lenses against each other._", ""]
    for ticker, text in multi_result.narratives.items():
        display = next((r.display for r in multi_result.rows if r.ticker == ticker),
                       ticker)
        lines += [f"### {display}", "", text, ""]
    return lines


def _mode_phrase(council_mode: str) -> str:
    """The executed mode in words — the same phrasing every REPORT-1 surface uses."""
    if council_mode == "ranker-only":
        return "ranker only, no AI commentary"
    return f"{council_mode} commentary" if council_mode else ""


def _local_stamp(run_start) -> str:
    """Run start in Europe/Berlin, the display zone every user-facing surface uses."""
    from aristos_council.export.report_html import _local_stamp as stamp

    return stamp(run_start)


def _render_multi_strategy_result(multi_result) -> None:
    """The combined grid (FUND-RUN-1) — presentation only: every cell is the
    verdict-of-record a single run of that strategy produces."""
    m = multi_result.meta
    ids = multi_result.strategy_ids

    persisted = st.session_state.get("uni_multi_persisted")
    if persisted:
        md_path, html_path = persisted
        st.success(f"💾 Saved this run to: `{_shown_path(md_path)}` and "
                   f"`{_shown_path(html_path)}` — ONE merged report covering all "
                   f"{len(ids)} lenses.")

    from aristos_council.pipeline import (
        RULES_SECTION_TITLE, VERDICT_TABLE_NOTE, VERDICT_TABLE_TITLE,
        multi_header_line, multi_strategy_grid_rows, multi_summary_line, rules_applied,
    )
    from aristos_council.report_language import label_with_id

    cohort = label_with_id(m.get("universe_name", ""), m.get("universe_id") or "adhoc")
    lens_labels = {sid: label_with_id(multi_result.strategy_names.get(sid) or sid, sid)
                   for sid in ids}
    st.markdown(f"#### {cohort} — {len(ids)} lenses × {m.get('universe_size', 0)} names")
    st.caption("Lenses: " + "; ".join(lens_labels.values()))
    st.markdown(f"### {multi_summary_line(multi_result)}")
    st.caption(multi_header_line(multi_result))

    # REPORT-2: the rules EACH lens applied, before the verdicts — a name excluded by one
    # lens and ranked by another is only legible once both rule sets are stated.
    with st.expander(f"{RULES_SECTION_TITLE} — by lens", expanded=False):
        for sid in ids:
            rules = rules_applied(multi_result.results[sid])
            st.markdown(f"**{lens_labels[sid]}**")
            if rules is None:
                st.caption("This lens declares no screen.")
                continue
            st.caption(f"{rules.screen_heading} — {rules.screen_note}")
            if rules.rules:
                st.dataframe([{"Rule": r.label, "Limit": r.threshold_phrase,
                               "What it did": r.tally,
                               "Measured on": r.measured or "—",
                               "Criterion id": r.criterion} for r in rules.rules],
                             hide_index=True, width="stretch")
            for line in rules.ranker_lines:
                st.caption(line)

    st.subheader(VERDICT_TABLE_TITLE)
    rows, _head = multi_strategy_grid_rows(multi_result)
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.info("No names reported.")
    st.caption(VERDICT_TABLE_NOTE)
    st.caption(f"{m.get('graded_by_all', 0)} name(s) were ranked by ALL {len(ids)} "
               "lenses — only those rank-sums are comparable.")

    # PRICE-1 / VALBAND-1: per-NAME context beside the combined grid, never a verdict.
    # It is ALWAYS shown; the band (and the reversion value riding with it) only when the
    # checkbox was on.
    # BAND-2: over the UNION of every lens's ranked names. Reading it off the first lens
    # made the section's size an accident of lens order — Defensive Income first showed
    # 2 rows of a 121-name cohort, Magic Formula RAW first showed 81.
    from aristos_council.pipeline import union_valuation_band_table
    _render_valuation_band_table(
        union_valuation_band_table(multi_result) if ids else None)

    # What DOES vary per lens — exclusion reasons, no-data names, factor sourcing.
    from aristos_council.pipeline import exclusion_rows, provenance_sentences

    for sid in ids:
        res = multi_result.results[sid]
        with st.expander(f"{lens_labels[sid]} — detail "
                         f"({res.meta['ranked_count']} ranked, "
                         f"{len(res.excluded)} excluded, "
                         f"{len(res.unrateable)} with no data)"):
            if res.excluded:
                st.markdown("**Excluded — did not pass a rule, so was never ranked**")
                for row in exclusion_rows(res):
                    muted = f" `{row['criterion']}`" if row["criterion"] else ""
                    st.markdown(f"- **{row['name']}** — {row['sentence']}{muted}")
                    if row["flag"]:
                        st.caption(row["flag"])
            if res.unrateable:
                st.markdown("**No usable data — no verdict was formed**")
                for t, why in res.unrateable:
                    st.markdown(f"- **{display_name(t, res.names.get(t))}** — {why}")
            if not res.excluded and not res.unrateable:
                st.caption("Every name was rateable and ranked.")
            entries = provenance_sentences(res)
            if entries:
                st.markdown("**Where the numbers came from**")
                for e in entries:
                    st.markdown(f"- {e['sentence']}")

    # REPORT-2: ONE merged pair, whatever the lens count. The old "download combined grid
    # (markdown)" button is GONE — it served a grid-only subset of this same document.
    run_start = st.session_state.get("uni_run_start") or datetime.now(timezone.utc)
    display_name_for_file = st.session_state.get("uni_universe_display_name", "")
    from aristos_council.download_names import multi_universe_download_name
    from aristos_council.export.report_html import multi_strategy_report_html

    n = len(ids)
    mode = m.get("council_mode", "ranker-only")
    c1, c2 = st.columns(2)
    with c1:
        st.download_button(
            "⬇ Download this run (markdown)",
            data=_multi_strategy_markdown(multi_result, run_start).encode("utf-8"),
            file_name=multi_universe_download_name(
                n, mode, run_start, universe_display_name=display_name_for_file),
            mime="text/markdown", key="uni_multi_download")
    with c2:
        st.download_button(
            "⬇ Download this run (HTML)",
            data=multi_strategy_report_html(
                multi_result, run_start=run_start).encode("utf-8"),
            file_name=multi_universe_download_name(
                n, mode, run_start, ext="html",
                universe_display_name=display_name_for_file),
            mime="text/html", key="uni_multi_download_html")


def _render_universe_result(result) -> None:
    m = result.meta

    # UI-FIX-1: where this run landed on disk, prominent — the first thing a user sees
    # so a completed (possibly paid) run is never mistaken for session-only output.
    persisted = st.session_state.get("uni_persisted_paths")
    if persisted:
        md_path, html_path = persisted
        st.success(f"💾 Saved to: `{_shown_path(md_path)}` and "
                  f"`{_shown_path(html_path)}`")

    from aristos_council.pipeline import (
        PROVENANCE_SECTION_NOTE, PROVENANCE_SECTION_TITLE, RULES_SECTION_TITLE,
        header_lines, provenance_sentences, rules_applied, summary_line,
        untested_rule_notes, used_symbol_notes,
    )
    from aristos_council.rank_engine import factor_column_label
    from aristos_council.report_language import format_score_gloss, label_with_id

    # ITEM 6: the confirmation line first — a wrong dropdown is visible immediately.
    st.caption(_confirmation_line(m))
    # 1 — REPORT-1: the human names lead; every id stays beside them as the record key.
    head = header_lines(result)
    st.markdown(f"#### {head[0]}")
    for line in head[1:]:
        st.caption(line)
    st.markdown(f"### {summary_line(result)}")
    st.caption(result.header)
    meta_bits = (f"Screen: {label_with_id(m.get('screen_strategy_name', ''), m['screen_strategy_id'])} · "
                 f"ranked {m['ranked_count']} of {m['universe_size']} names")
    if not m["ranker_only"]:
        meta_bits += (f" · shortlist {len(m['shortlist'])} · "
                      f"estimated cost ${m['est_cost']:.2f} · "
                      f"narrating {m.get('narrate_coverage', 'buys_only')}")
    if m.get("run_id"):
        meta_bits += f" · run id `{m['run_id']}`"
    st.caption(meta_bits)

    # 1b — REPORT-1: RULES APPLIED, before any result. Every rule the run applied, its
    # limit in plain English, and what it actually did — including rules nothing failed.
    rules = rules_applied(result)
    if rules is not None:
        st.subheader(RULES_SECTION_TITLE)
        st.caption(f"**{rules.screen_heading}** — {rules.screen_note}")
        if rules.rules:
            st.dataframe([{"Rule": r.label, "Limit": r.threshold_phrase,
                           "What it did": r.tally,
                           "Measured on": r.measured or "—",
                           "Criterion id": r.criterion}
                          for r in rules.rules], hide_index=True, width="stretch")
        for line in rules.ranker_lines:
            st.caption(line)

    # 2 — RANKED table: sortable, verdict palette, per-factor rank AND value.
    st.subheader("Ranked — the verdict of record")
    rows, factor_names = _ranked_rows(result.ranked, result.names)
    if rows:
        import pandas as pd

        n_factors = next((len(r.factor_ranks) for r in result.ranked if r.factor_ranks),
                         0)
        st.caption(format_score_gloss(n_factors, len(result.ranked)))
        df = pd.DataFrame(rows)
        styler = df.style.map(
            lambda v: f"color: {_verdict_hex(v)}; font-weight: 700",
            subset=["Verdict"])
        st.dataframe(styler, hide_index=True, width="stretch")
        st.caption("Factor ids, in column order: "
                   + ", ".join(f"`{f}`" for f in factor_names) + ".")
        for sym, note in used_symbol_notes(result):
            st.caption(f"{sym} — {note}")
    else:
        st.info("No names survived the screen to be ranked.")

    # 2a — REPORT-1: a rule that could not be TESTED, in words, next to the verdict it
    # qualifies. It used to be a bare dagger in the name column.
    untested = untested_rule_notes(result)
    if untested:
        st.subheader("Rules that could not be tested")
        st.caption("These names PASSED the screen — a rule that cannot be evaluated "
                   "never excludes anyone — but one of its rules returned no answer "
                   "for them at all, so their pass is thinner than it looks.")
        for n in untested:
            count = len(n["rules"])
            st.markdown(f"- **{n['name']}** — {n['verdict']} — {count} rule"
                        f"{'s' if count != 1 else ''} could not be tested: "
                        + "; ".join(n["rules"]))

    # 2b — REPORT-1: "Where the numbers came from" (was "Factor integrity"): which
    # computation path produced each factor per name, as a sentence. Same counts.
    entries = provenance_sentences(result)
    if entries:
        st.subheader(PROVENANCE_SECTION_TITLE)
        st.caption(PROVENANCE_SECTION_NOTE)
        for e in entries:
            st.markdown(f"- {e['sentence']}")

    # 2b1 — SHARE PRICE & 52-WEEK POSITION (PRICE-1): the plainest two facts in the
    # report — what one share costs today (in its OWN currency, with the close's date, so
    # a stale cache is visible) and where that sits in the trailing year. ALWAYS shown:
    # it reads the 400-day bars the ranking legs already fetched, so it is free and is not
    # gated by the valuation-band toggle. Display only.
    from aristos_council.pipeline import valuation_band_table

    # 2b2 — PRICE + VALUATION as ONE table (PRICE-2, extended by REPORT-1): what each
    # name costs, where that sits in its 12-month range, and — when the band is on —
    # where its multiple sits in its OWN multi-year range and what it would cost at that
    # name's median. Display only: it ranks nothing, screens nothing, decides nothing.
    _render_valuation_band_table(valuation_band_table(result))

    # 2c — the old "Screen basis" section is GONE (REPORT-1): the measurement basis each
    # rule used is now a "measured on …" line inside the RULES APPLIED block above, beside
    # the rule it qualifies, so the same information is not printed in two places.

    # 3 — REPORT-1: excluded names as SENTENCES naming the rule, the observed value and
    # the limit in their proper units — the raw form was "screen: min_dividend_yield
    # (observed 0.009547 vs threshold 0.015)". The criterion id stays for auditability.
    if result.excluded:
        from aristos_council.pipeline import exclusion_rows
        st.subheader(f"Excluded — did not pass a rule, so was never ranked · "
                     f"{len(result.excluded)}")
        st.dataframe([{"Name": r["name"], "Why": r["sentence"],
                       "Warning": r["flag"], "Criterion id": r["criterion"]}
                      for r in exclusion_rows(result)],
                     hide_index=True, width="stretch")

    # 4 — UNRATEABLE: its OWN axis (no data, no verdict) — deliberately distinct.
    if result.unrateable:
        st.subheader(f"⚪ Unrateable — no data, no verdict · {len(result.unrateable)}")
        with st.container(border=True):
            st.caption("A SELL implies an assessment was made; these names had no "
                       "usable data at all (likely delisted), so they receive NO "
                       "verdict and reached no model.")
            for t, why in result.unrateable:
                st.markdown(f"- **{display_name(t, result.names.get(t))}** — {why}")

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
            st.markdown(f"- **{display_name(t, result.names.get(t))}** — {why}")

    # 5 — NARRATIVE: one expander per shortlisted (BUY) name — the narrator's job.
    if not m["ranker_only"]:
        st.subheader("Narrative")
        if result.narratives:
            verdict_of = {r.ticker: r.verdict.upper() for r in result.ranked}
            for ticker, text in result.narratives.items():
                v = verdict_of.get(ticker, "")
                disp = display_name(ticker, result.names.get(ticker))
                with st.expander(f"{disp}{(' · ' + v) if v else ''} — narration"):
                    st.markdown(_md(text) or "_(no narrative produced)_")
        else:
            st.caption("No names reached the council.")

    # 6 — download the run (a convenience copy; UI-FIX-1 already auto-persisted the same
    # bytes above). Unique, self-describing filenames: universe display-name slug +
    # strategy + mode + run-start (Europe/Berlin) — ITEM 6 / UI-FIX-1. TWO exports side
    # by side (REPORT-HTML-1): the markdown stays the CANONICAL machine-readable record,
    # the HTML is the self-contained presentation copy for people outside the repo.
    from aristos_council.download_names import (
        universe_download_name, universe_html_download_name)
    from aristos_council.export.report_html import universe_report_html

    run_start = st.session_state.get("uni_run_start") or datetime.now(timezone.utc)
    uni_display_name = st.session_state.get("uni_universe_display_name", "")
    md_name = universe_download_name(m["rank_strategy_id"], m["council_mode"], run_start,
                                     universe_display_name=uni_display_name)
    html_name = universe_html_download_name(
        m["rank_strategy_id"], m["council_mode"], run_start,
        universe_display_name=uni_display_name)
    dl_md, dl_html = st.columns(2)
    with dl_md:
        st.download_button(
            f"⬇ Download run as markdown — {md_name}",
            data=_universe_markdown(result), file_name=md_name,
            mime="text/markdown", key="uni_download")
    with dl_html:
        st.download_button(
            f"⬇ Download report (HTML) — {html_name}",
            data=universe_report_html(result, run_start=run_start), file_name=html_name,
            mime="text/html", key="uni_download_html")
    st.caption("Markdown is the canonical machine-readable record. The HTML is one "
               "self-contained file (no external requests) for sharing outside the "
               "repo — open it in a browser and Print → PDF for paper.")


def render_universe_tab(show_validation: bool = False) -> None:
    import os

    from aristos_council.pipeline import NARRATION_BASIS
    from aristos_council.reproducibility import estimate_cost

    from aristos_council.universe import list_universes

    from aristos_council.universe_editor import (
        existing_universe_ids, graded_universe_ids, list_id_from_name,
        parse_ticker_lines, save_local_universe)

    st.subheader("Run — pick strategies, pick tickers, run")
    st.caption("Screen → rank → gates issue the verdict of record; the LLM only "
               "narrates. Pick one or more strategies, edit the ticker list, run. That "
               "is the whole flow (FUND-UI-2).")

    # 1 — STRATEGIES. ONE picker (FUND-UI-2, strategy/picker.py): visibility filtering (the
    # validation toggle reveals the ``ui: hidden`` baseline/superseded configs), the
    # flagship-first ordering, and the label->strategy resolution all live in that module
    # now, shared with Company Check — so a fix lands on both surfaces at once. EVERY
    # visible strategy is offered for ANY ticker list: no per-section "relevant strategies"
    # filtering, because a list does not make a strategy unofferable. Asset-class scope
    # stays an honest caption + a confirmed-mismatch warning below, never a hidden option.
    choices = strategy_choices([o[2] for o in list_rank_strategy_options(STRATEGIES_DIR)],
                               show_validation=show_validation)
    if not choices:
        st.error(f"No rank strategies found under {STRATEGIES_DIR}")
        return
    # The picker renders FRIENDLY display names; the technical id lives only in a small
    # caption (ids are the stable record keys — never renamed, never in the label). A label
    # two configs would SHARE carries its id, so a pick can't resolve to the wrong one.
    labels = choice_labels(choices)
    # The PRIMARY strategy stays a dropdown, and exactly one is always selected: narration is
    # single-strategy, and the primary is the verdict the narrator explains. Folding it into
    # the extra-lens control left the narrated strategy implicit (offer order decided it,
    # which the user can neither see nor choose) — FUND-UI-2 item 5.
    primary_label = st.selectbox(
        "Primary strategy — the verdict the narrator explains", labels,
        index=default_index(choices), key="uni_strategy",
        help="Runs the full flow: screen → rank → gates issue the verdict, and the LLM "
             "narrates it. Exactly one, because narration is single-strategy.")
    primary = resolve(choices, primary_label) or choices[0].strategy

    # Extra lenses are CHECKBOXES, one per lens, so every lens you could add is visible at
    # once instead of hidden behind a dropdown (FUND-UI-2 item 5). Presentation only: the
    # offered set is the same ONE picker's, and ticking any box runs FUND-RUN-1's combined
    # grid exactly as the old "Also grade with" multiselect did — deterministic by
    # construction (no LLM, no cost), so narration settings grey out below.
    st.caption("**Also grade with** — optional extra lenses. These re-grade the SAME "
               "ticker list deterministically and report a single combined grid; they are "
               "never narrated. Leave them all unticked for a normal narrated run of the "
               "primary strategy.")
    extras: list[tuple[str, bool]] = []
    extra_choices = [c for c in choices if c.label != primary_label]
    if extra_choices:
        n_cols = min(3, len(extra_choices))
        per_col = -(-len(extra_choices) // n_cols)       # ceil: contiguous, offer-ordered
        for i, col in enumerate(st.columns(n_cols)):
            with col:
                for c in extra_choices[i * per_col:(i + 1) * per_col]:
                    extras.append((c.label,
                                   st.checkbox(c.label, key=lens_checkbox_key(c.id))))
    # VALBAND-1: the valuation-band toggle rides WITH the extra-lens group but is NOT a
    # lens — extra lenses GRADE (add a verdict column), the band CONTEXTUALIZES (adds an
    # absolute percentile column, re-grades nothing). Default OFF: unticked -> no band
    # computation, no extra fetch, output byte-identical to a pre-VALBAND run.
    with_valuation_band = st.checkbox(
        "Valuation band (context column — no verdict)", value=False,
        key="uni_valuation_band",
        help="Adds an absolute column: where today's valuation sits in each name's OWN "
             "5-year EV/EBIT (or P/E) distribution — 15th percentile = historically "
             "cheap, 92nd = near its own peak. Unlike the extra lenses above it never "
             "grades, reorders, or narrates; it only contextualizes. Off by default — "
             "ticking it fetches each name's 5-year price history.")
    picked_labels = selected_labels(primary_label, extras)
    # OFFER order, not click order (picker.resolve_all), so the combined grid's columns are
    # reproducible. ``or [primary]`` only covers a stale widget value: with a required
    # dropdown a zero-strategy run is structurally unreachable here, though run_problems
    # still refuses one (that guard is the contract, not the only line of defence).
    strategies = resolve_all(choices, picked_labels) or [primary]
    multi = len(strategies) > 1
    for s in strategies:
        bits = f"`{s.id}`"                               # the stable record key
        if strategy_role(s):
            bits += f" · {strategy_role(s)}"
        st.caption(bits)
        # CAPTION-1: what this lens asks of a company, for EVERY selected lens — a
        # multi-lens run used to caption none of them, so a reader comparing a BUY under
        # one lens with a SELL under another had nothing saying they ask different
        # questions. Absent `asks` renders nothing.
        _asks = (getattr(s, "asks", "") or "").strip()
        if _asks:
            st.caption(_asks)
    if len(strategies) == 1 and getattr(strategies[0], "description", ""):
        st.caption(strategies[0].description.strip())
    # The cost estimate + the narration settings describe the PRIMARY strategy (the only one
    # that can narrate); a multi-lens run is deterministic, so neither is in play then.
    rank_strategy = primary

    # 2 — TICKERS. A list is a plain, editable ticker list: pick one of yours (or start a
    # new one), edit it right here, run it. Selecting a list LOADS it into this box — there
    # is no separate "universe edit runs" section any more, and no manifest ceremony.
    saved = visible_universes(list_universes(UNIVERSES_DIR),
                              show_validation=show_validation)
    NEW_LIST = "New list"
    list_labels = [NEW_LIST] + saved_list_labels(saved)
    list_choice = st.selectbox("List", list_labels, key="uni_list",
                               help="Your saved ticker lists. Selecting one loads it "
                                    "below, where you can edit it before running.")
    picked_list = (saved[list_labels.index(list_choice) - 1]
                   if list_choice != NEW_LIST else None)
    # Load-on-select: seed the ticker box from the chosen list. Written BEFORE the widget
    # below is instantiated (Streamlit's supported pre-instantiation write) and only when
    # the SELECTION changed, so an edit in progress is never overwritten on a rerun.
    if st.session_state.get("uni_loaded_list") != list_choice:
        st.session_state["uni_loaded_list"] = list_choice
        if picked_list is not None:
            st.session_state["uni_tickers"] = "\n".join(picked_list.tickers)
            st.session_state["uni_list_name"] = (picked_list.display_name
                                                 or picked_list.id)
    raw = st.text_area(
        "Tickers — one per line; spaces/commas fine, `# comments` allowed",
        key="uni_tickers", height=180,
        placeholder="AAPL\nMSFT  # anchor\n# --- energy ---\nXOM")
    universe = parse_ticker_lines(raw)

    # A list runs under its own id only while it still IS that list; an edited (or new) list
    # runs ad-hoc — ``adhoc:<hex8>`` — rather than filing a changed cohort under a name
    # whose past verdicts were graded on different members. Either way the run record
    # carries the exact membership (FUND-UI-2), so nothing is lost by not saving.
    unchanged = picked_list is not None and universe == list(picked_list.tickers)
    universe_id = picked_list.id if unchanged else None
    universe_display_name = picked_list.display_name if unchanged else ""
    # REPORT-4 part 4: an edit FORKS to `adhoc:<hex8>` (FUND-UI-2), which is right for the
    # record and useless as a title — "adhoc:507e10cf" names nothing a reader recognises.
    # The parent's name rides along, display-only; the id stays the record key.
    derived_from = ("" if unchanged or picked_list is None
                    else (picked_list.display_name or picked_list.id))

    # An edit is ALWAYS a fork, never an in-place mutation of the manifest on disk: the run
    # grades an ad-hoc copy and the source file is untouched until you explicitly save. Say
    # which list the edit came from and that the original is intact — and, when that list
    # cannot be rewritten at all (a shipped or scoreboard-graded one), say that too rather
    # than pointing at a Save button that is disabled.
    graded = graded_universe_ids(SNAPSHOTS_CSV)
    is_mine = (picked_list is not None and getattr(picked_list, "local", False)
               and picked_list.id not in graded)
    if picked_list is not None and not unchanged:
        keep = ("Use **Save changes** to write the edit back into it, or **Save as new "
                "list** to fork it." if is_mine else
                "It cannot be edited in place — use **Save as new list** to keep the edit.")
        st.caption(f"Edited — this run grades an ad-hoc copy (fingerprinted); "
                   f"**{universe_label(picked_list)}** on disk is untouched. {keep}")
    with st.expander("💾 Save this list"):
        st.caption("Lists live in `universes/local/` and are gitignored by default — "
                   "portfolio-class data never rides a commit.")
        name = st.text_input("List name", key="uni_list_name",
                             placeholder="My Portfolio")
        col_save, col_saveas = st.columns(2)
        with col_save:
            save_over = st.button("Save changes", key="uni_save_over",
                                  disabled=not (is_mine and universe),
                                  help=None if is_mine else
                                  "Only your own saved lists can be updated in place.")
        with col_saveas:
            save_new = st.button("Save as new list", key="uni_save_new",
                                 disabled=not (universe and name.strip()))
        if save_over or save_new:
            try:
                created = datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat()
                if save_over:
                    path = save_local_universe(
                        UNIVERSES_DIR, id=picked_list.id, tickers=universe,
                        created=created, display_name=name.strip() or picked_list.id,
                        graded_ids=graded, overwrite=True)
                else:
                    new_id = list_id_from_name(name,
                                               existing_universe_ids(UNIVERSES_DIR))
                    path = save_local_universe(
                        UNIVERSES_DIR, id=new_id, tickers=universe, created=created,
                        display_name=name.strip(), graded_ids=graded)
            except (ValueError, ValidationError) as exc:
                st.error(str(exc))
            else:
                st.success(f"Saved **{name.strip() or path.stem}** → "
                           f"`{path.relative_to(ROOT)}` ({len(universe)} names).")

    # STRAT-PICKER-1: which lenses can HONESTLY grade this cohort. The cohort's asset class
    # is DERIVED from the lenses that declare it (applicability.py); an AD-HOC cohort
    # declares nothing, so it is UNKNOWN and NOTHING is filtered out — the live 2026-08-10
    # bug was an ad-hoc stock cohort offered a single lens while five stock lenses sat
    # unreachable in strategies/. The picker above stays complete either way (never hide a
    # runnable strategy); this only NAMES what applies and warns on a CONFIRMED mismatch,
    # which the run-time asset-kind gate would exclude anyway.
    all_rank_strategies = [c.strategy for c in choices]
    cohort_kind = cohort_asset_kind(universe_id, all_rank_strategies)
    applicable = applicable_rank_strategies(all_rank_strategies, cohort_kind)
    st.caption(cohort_scope_note(cohort_kind, len(applicable),
                                 adhoc=universe_id is None))
    for s in strategies:
        scope_warning = out_of_scope_note(s, cohort_kind)
        if scope_warning:
            st.warning(scope_warning)

    strategy_ids = [s.id for s in strategies]

    # RUNMODE-1 — ONE control, and it always shows the value in force.
    # NARR-UNION-1 relaxed the multi-lens LOCK to a DEFAULT: several lenses now CAN
    # narrate (one section per NAME over the union of their BUYs), so the control is no
    # longer disabled. Ranker-only stays the default there because it is free.
    n_strategies = len(strategies)
    # The mode FOLLOWS the lens count until the user states a preference, and obeys the
    # user for ever after. Two session keys, because they answer different questions:
    #   uni_run_mode_choice  — what the user last picked (a plain key, so it outlives the
    #                          widget: Streamlit drops widget state when the widget is not
    #                          rendered, which is how the earlier lock erased the choice).
    #   uni_run_mode_touched — whether the user has picked AT ALL. Untouched, ticking a
    #                          second lens moves the default to ranker-only (free, and
    #                          what a cross-lens comparison usually wants); touched, the
    #                          choice stands through ticking and unticking alike.
    # SEEDED ONCE, then it is the user's. The lens count picks the STARTING mode and
    # never touches it again.
    #
    # It used to re-default on every lens-count change, gated on an `uni_run_mode_touched`
    # flag set from the radio's on_change. That flag could not do the job it was given:
    # Streamlit fires on_change only when the value CHANGES, so a user who selects the
    # option already showing (Narrator, on a one-lens run) registers as never having
    # expressed a preference — and ticking a second lens then silently moved them to
    # ranker-only. That is the CONFIRM-SPEND-1 live failure of 2026-08-25 11:21: narrator
    # chosen, three lenses, and a ranker-only report with no narration and no confirmation
    # step, because the mode in force was never the mode the user had picked.
    #
    # The re-default existed to stop a multi-lens run spending by accident. It is now
    # redundant: CONFIRM-SPEND-1 ranks for free and spends only from a button carrying the
    # exact figure, so nothing can be billed unasked whatever this control says. Between a
    # guard that cannot misfire and one that silently overrides intent, the guard stays and
    # the override goes.
    st.session_state.setdefault("uni_run_mode_choice", default_run_mode(n_strategies))
    st.session_state.setdefault(
        "uni_run_mode", effective_run_mode(st.session_state["uni_run_mode_choice"],
                                           n_strategies=n_strategies))

    col_a, col_b = st.columns(2)
    with col_a:
        run_mode = st.radio(
            "Run mode", RUN_MODES,
            format_func=lambda m: RUN_MODE_LABELS[m], key="uni_run_mode",
            help="Ranker only: the deterministic ranking, free. "
                 "Narrator: the LLM explains the ranker's verdict. "
                 "Second opinion: an independent comparison verdict — a "
                 "pre-registered experiment that returned a null result; kept "
                 "behind this option.")
        st.session_state["uni_run_mode_choice"] = run_mode
        if n_strategies > 1 and run_mode_narrates(run_mode):
            st.caption(MULTI_LENS_NARRATION_NOTE)
    with col_b:
        # NARR-2 ITEM 2: which ranked names get narrated. buys_only (default, cheapest)
        # for stock screens; all for core/ETF cohorts where the HOLDs are live
        # candidates being compared, not rejects. HIDDEN — not greyed — when nothing
        # narrates: a greyed control still invites a reading it cannot support.
        if run_mode_narrates(run_mode):
            _cov_label = {"buys_only": "Narrate: BUYs only (cheapest)",
                          "all": "Narrate: all ranked names"}
            narrate_coverage = st.selectbox(
                "Narration coverage", ["buys_only", "all"],
                key="uni_coverage",
                format_func=lambda c: _cov_label[c],
                help="BUYs only: narrate the shortlist (cheapest — good for stock "
                     "screens). all: narrate every ranked name — for core/ETF cohorts "
                     "where the HOLDs are live options you're comparing, not rejects.")
        else:
            # The inert default the pipeline already receives on a ranker-only run.
            narrate_coverage = st.session_state.get("uni_coverage", "buys_only")

    # COST-2 — the confirmation becomes PROPORTIONATE. Shown only where it applies:
    # ranker-only spends nothing, so a threshold there would be a control with no effect.
    if run_mode_narrates(run_mode):
        confirm_threshold = read_threshold(st.number_input(
            "Ask before narrating when the estimate exceeds:",
            min_value=0.0, max_value=1000.0, step=1.0,
            value=read_threshold(
                st.session_state.get("uni_confirm_threshold",
                                     DEFAULT_CONFIRM_THRESHOLD),
                default=DEFAULT_CONFIRM_THRESHOLD),
            format="%.2f", key="uni_confirm_threshold",
            help="0 = always ask. At or below this figure a narrated run goes straight "
                 "through after the free ranking; above it the run stops and shows the "
                 "exact count and cost for you to confirm."), default=0.0)
    else:
        confirm_threshold = read_threshold(
            st.session_state.get("uni_confirm_threshold", DEFAULT_CONFIRM_THRESHOLD),
            default=0.0)

    # The two pipeline arguments, derived from the ONE control (UI layer only).
    ranker_only, mode = run_mode_arguments(run_mode)

    # FLOOR-1 — the EPHEMERAL company-size floor. Same doctrine as the council strategy's
    # "Run overrides — this run only" block: applied to THIS run, stamped on the report,
    # the strategy file never touched. It sits here because the floor is a COHORT
    # statement — it decides who is in the room — so it applies to every lens in a
    # multi-lens run rather than being set per lens.
    min_market_cap_override = _company_size_floor_override(rank_strategy, len(strategies))

    # CAP-1: the caption quotes the cap that actually applies to the SELECTED mode, read
    # from the same helper the guard uses, so the two can never disagree.
    _cap_now = universe_cap(ranker_only)
    st.caption(f"**{len(universe)}** ticker(s) — up to **{_cap_now}** for "
               f"{'a ranker-only' if ranker_only else 'a narrated'} run.")

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    # NARR-UNION-1: a multi-lens run is no longer deterministic BY CONSTRUCTION — it can
    # narrate now — so the one thing that decides whether a key is needed and a cost is
    # shown is the run MODE, for one lens and for five alike.
    deterministic = ranker_only
    # ONE guard set, pure and unit-tested (FUND-UI-2). The old flow re-declared CAP and the
    # key check per section, which is precisely how the two halves drifted apart.
    problems = run_problems(universe, n_strategies=len(strategies),
                            deterministic=deterministic, has_key=has_key)

    # NARR-UNION-1: on a MULTI-LENS narrated run the bill is proportional to the size of
    # the UNION of every lens's BUYs — NOT to the lens count, and not to the number of BUY
    # verdicts (five lenses produced 18 verdicts over 13 distinct names on 2026-08-24).
    # The union cannot be known before the free ranking pass, so the pre-run number is the
    # same upper bound the single-lens flow already shows, stated per NAME.
    est = None
    narrated_count = None
    if not deterministic and universe and len(universe) <= UNIVERSE_CAP:
        per_lens = _estimate_shortlist_size(len(universe), rank_strategy,
                                            narrate_coverage=narrate_coverage)
        if multi:
            narrated_count = _estimate_union_size(
                len(universe), strategies, narrate_coverage=narrate_coverage)
            est = estimate_cost(narrated_count)
        else:
            est = estimate_cost(per_lens)
    if multi and not run_mode_narrates(run_mode):
        st.caption(f"Multi-lens re-grade: **{len(strategies)}** strategies × "
                   f"**{len(universe)}** name(s) — deterministic ranker only "
                   f"(no narration, no cost), reported as ONE combined grid.")

    for msg in problems:
        st.info(msg)

    # RUNMODE-1 + NARR-UNION-1: the button says WHAT will happen and WHAT IT COSTS, on its
    # own line — and on a multi-lens narrated run it says how many NAMES that is.
    run = st.button(_md(run_button_label(
                        run_mode, n_strategies=n_strategies, est_cost=est,
                        narrated_count=narrated_count)),
                    type="primary", disabled=bool(problems), key="uni_run")
    if est is not None:
        basis = (f" ONE section per NAME over "
                 f"{NARRATION_BASIS.get(narrate_coverage, narrate_coverage)} — a name "
                 f"several lenses bought is narrated once." if multi else "")
        st.caption("The estimate is an upper bound (pre-screen); the exact shortlist "
                   "(after the screen prefilter) is shown after ranking." + basis)

    if run and multi:
        run_start = datetime.now(timezone.utc)
        status = st.status("Starting…", expanded=True)
        try:
            from aristos_council.pipeline import run_multi_strategy_pipeline

            # CONFIRM-SPEND-1 — PHASE ONE, always: the FREE ranking pass, no
            # confirmation. This is work the pipeline does before any narration anyway;
            # running it now is what turns an upper bound into the exact figure. In a
            # narrating mode the result is HELD for confirmation rather than reported,
            # and phase two consumes it — the ranking is never thrown away or re-run.
            multi_result = run_multi_strategy_pipeline(
                universe, strategy_ids, universe_id=universe_id,
                strategies_dir=STRATEGIES_DIR, universes_dir=UNIVERSES_DIR,
                freeze_dir=ROOT / "runs", with_valuation_band=with_valuation_band,
                derived_from=derived_from,
                min_market_cap_override=min_market_cap_override,
                progress=lambda msg: status.update(label=msg))
        except Exception as exc:
            status.update(label="Run failed", state="error")
            st.exception(exc)
            st.session_state.pop("uni_multi_result", None)
            st.session_state.pop("uni_pending_narration", None)
        else:
            st.session_state["uni_run_start"] = run_start
            st.session_state["uni_universe_display_name"] = universe_display_name
            st.session_state.pop("uni_result", None)
            # The free ranking is REPORTED either way — downloading it and narrating it
            # are not alternatives (the confirm panel used to offer only "narrate" or
            # "keep", with the finished ranking's downloads nowhere on screen). Narrating
            # later REPLACES this pair rather than adding a second one (_supersede).
            _publish_multi(multi_result, run_start, universe_display_name)
            if run_mode_narrates(run_mode):
                _offer_or_narrate(
                    {"kind": "multi", "result": multi_result, "mode": mode,
                     "coverage": narrate_coverage},
                    threshold=confirm_threshold, status=status,
                    run_start=run_start, display_name=universe_display_name)
            else:
                status.update(label="Done.", state="complete")
                st.session_state.pop("uni_pending_narration", None)
    elif run:
        run_start = datetime.now(timezone.utc)       # run-start for the download name (ITEM 6)
        status = st.status("Starting…", expanded=True)
        try:
            from aristos_council.pipeline import run_rank_pipeline

            # CONFIRM-SPEND-1 — PHASE ONE: rank for free, always. A narrating mode
            # HOLDS the result for confirmation; ranker-only reports it straight away.
            result = run_rank_pipeline(
                universe, rank_strategy.id, universe_id=universe_id,
                council_mode=mode, ranker_only=True,
                narrate_coverage=narrate_coverage, derived_from=derived_from,
                with_valuation_band=with_valuation_band,
                strategies_dir=STRATEGIES_DIR, universes_dir=UNIVERSES_DIR,
                # Freeze this run's raw inputs so Company Check's reference-cohort reader
                # (_latest_reference_run) can replay it offline — without this the UI
                # never wrote runs/ and cohort context was dead code (ITEM 1).
                freeze_dir=ROOT / "runs",
                min_market_cap_override=min_market_cap_override,
                progress=lambda msg: status.update(label=msg))
        except Exception as exc:
            status.update(label="Run failed", state="error")
            # Finnhub scope-fence (sprint item 4): a live crash on Finnhub is a
            # SEPARATE bug with its own spec — capture the traceback and STOP,
            # do not paper over it. Sentiment should degrade to abstention upstream.
            st.exception(exc)
            st.session_state.pop("uni_result", None)
        else:
            st.session_state["uni_run_start"] = run_start
            st.session_state["uni_universe_display_name"] = universe_display_name
            st.session_state.pop("uni_multi_result", None)
            st.session_state.pop("uni_multi_persisted", None)
            _publish_single(result, run_start, universe_display_name)
            if run_mode_narrates(run_mode):
                _offer_or_narrate(
                    {"kind": "single", "result": result, "mode": mode,
                     "coverage": narrate_coverage},
                    threshold=confirm_threshold, status=status,
                    run_start=run_start, display_name=universe_display_name)
            else:
                status.update(label="Done.", state="complete")
                st.session_state.pop("uni_pending_narration", None)

    _render_last_spend()
    _render_narration_confirmation()

    multi_result = st.session_state.get("uni_multi_result")
    if multi_result is not None:
        st.divider()
        _render_multi_strategy_result(multi_result)

    result = st.session_state.get("uni_result")
    if result is not None:
        st.divider()
        _render_universe_result(result)


def render_scoreboard_tab() -> None:
    """Minimal, read-only listing of the persisted rank-run records — the append-only
    snapshot store (date · strategy · universe · rows), labeled with universe_id, plus
    a raw-CSV download. Rank runs aren't saved as single-ticker reports, so this is
    where they're retrievable; it's a listing, NOT a new report renderer.

    UI-FIX-1: its own top-level tab (moved off the Run flow, where it was
    easy to miss and easy to confuse with a just-completed run's own downloads).
    Content unchanged — only the placement moved."""
    from aristos_council.scoreboard import read_rows

    st.subheader("Scoreboard — persisted rank-run snapshots")
    st.caption("The prospective scoreboard's raw material: one row per ranked name "
               "per graded run, scored later on forward returns.")
    if not SNAPSHOTS_CSV.exists():
        st.info("No snapshots persisted yet.")
        return
    rows = read_rows(SNAPSHOTS_CSV)
    if not rows:
        st.info("No snapshots persisted yet.")
        return
    with st.expander(f"📸 Persisted snapshots (rank-run records) · {len(rows)} rows",
                     expanded=True):
        agg: dict[tuple, int] = {}
        for r in rows:
            key = (r.snapshot_date, r.strategy, r.universe_id or "—")
            agg[key] = agg.get(key, 0) + 1
        table = [{"snapshot_date": d, "strategy": s, "universe_id": u, "rows": n}
                 for (d, s, u), n in sorted(agg.items(), reverse=True)]
        st.dataframe(table, hide_index=True, width="stretch")
        from aristos_council.download_names import scoreboard_snapshots_download_name

        csv_name = scoreboard_snapshots_download_name(datetime.now(timezone.utc))
        st.download_button(
            f"⬇ Download snapshot CSV — {csv_name}", data=SNAPSHOTS_CSV.read_bytes(),
            file_name=csv_name, mime="text/csv", key="snap_csv_dl")
        st.caption("Scored later on forward returns via "
                   "`examples/score_snapshot.py` (the prospective scoreboard).")


_CC_STATUS_HEX = {"PASS": "#2E7D32", "FAIL": "#B23B3B", "NOT-EVALUATED": "#B8860B"}


def _company_check_adapter():
    """A cached yfinance adapter for the single-name fetch (free — no keys, no LLM)."""
    from datetime import date as _date

    from aristos_council.data.cache import DEFAULT_CACHE_DIR, CachingAdapter
    from aristos_council.data.provider import select_market_adapter
    return CachingAdapter(select_market_adapter(), cache_dir=DEFAULT_CACHE_DIR,
                          today=_date.today())


def render_company_check_tab(show_validation: bool = False) -> None:
    """Single-name diagnostic — 'why isn't X on the list?'. NO verdict is ever shown
    (a rank over one name is fabricated); this reports the screen, the gates, factor
    values with NAMED-cohort context, and the price-divergence flag."""
    from aristos_council.company_check import run_company_check
    from aristos_council.universe import list_universes

    st.subheader("Company Check — single-name diagnostic")
    st.caption("Why isn't a name on the list? Every screen criterion with values, each "
               "factor vs a named reference cohort, and the price-divergence flag. "
               "**No verdict** — a verdict is a cohort statement (a universe run).")

    # The SAME picker the Run tab uses (FUND-UI-2, strategy/picker.py). Before this,
    # Company Check re-implemented the filter, the ordering and the default inline — which
    # is why STRAT-PICKER-1's fix landed on one surface only.
    choices = strategy_choices([o[2] for o in list_rank_strategy_options(STRATEGIES_DIR)],
                               show_validation=show_validation)
    if not choices:
        st.error(f"No rank strategies found under {STRATEGIES_DIR}")
        return

    ticker = normalize_ticker(st.text_input("Ticker", value="", key="cc_ticker",
                                            placeholder="MU"))

    # Strategy — defaults to the flagship when it is offered. The dropdown shows the
    # friendly display_name; the id is a small caption.
    labels = choice_labels(choices)
    choice = st.selectbox("Strategy (lens screen + factors)", labels,
                          index=default_index(choices), key="cc_strategy")
    rank_strategy = resolve(choices, choice) or choices[0].strategy
    st.caption(f"`{rank_strategy.id}`")                  # the stable record key
    if strategy_role(rank_strategy):
        st.caption(f"↳ {strategy_role(rank_strategy)}")

    # Reference universe — manifests only (context comes from a persisted run; never a
    # fresh universe fetch). A 'None' option runs raw values with no cohort position.
    manifests = visible_universes(list_universes(UNIVERSES_DIR),
                                  show_validation=show_validation)
    NONE = "(none — raw values, no cohort context)"
    # UNI-1 ITEM 2: the selected strategy's SUGGESTED universes render first here too
    # (same helper the Run tab used for its manifest dropdown — no drift). This is a
    # reference cohort for factor CONTEXT, not a run input, so it stays a manifest picker.
    # Absent field -> unchanged.
    suggested, others = suggested_first(
        manifests, getattr(rank_strategy, "suggested_universes", []))
    ref_ordered = suggested + others
    ref_labels = ([f"⭐ {universe_label(u)} · {len(u.tickers)} names" for u in suggested]
                  + [f"{universe_label(u)} · {len(u.tickers)} names" for u in others]
                  + [NONE])
    ref_choice = st.selectbox("Reference universe (for factor context)", ref_labels,
                              key="cc_reference")
    if suggested:
        st.caption("⭐ = suggested for this strategy · every universe stays selectable")
    reference = None if ref_choice == NONE else ref_ordered[ref_labels.index(ref_choice)]
    reference_id = "" if reference is None else reference.id
    if reference is not None:
        st.caption(f"`{reference.id}`")                  # the stable record key
        if universe_role(reference):
            st.caption(f"↳ {universe_role(reference)}")

    run = st.button("▶ Run company check (free — no LLM)", type="primary",
                    disabled=not ticker, key="cc_run")
    if run:
        run_start = datetime.now(timezone.utc)       # run-start for the download name (ITEM 6)
        try:
            with st.spinner(f"Diagnosing {ticker}…"):
                adapter = _company_check_adapter()
                result = run_company_check(
                    ticker, rank_strategy.id, reference_id, adapter=adapter,
                    strategies_dir=STRATEGIES_DIR, universes_dir=UNIVERSES_DIR,
                    runs_dir=ROOT / "runs")
        except Exception as exc:
            st.exception(exc)
            st.session_state.pop("cc_result", None)
        else:
            st.session_state["cc_result"] = result
            st.session_state["cc_run_start"] = run_start
            # The friendly name for the HTML export's header (the result carries only the
            # id). Display-only; absent -> the export falls back to the id (never invents).
            st.session_state["cc_strategy_name"] = strategy_label(rank_strategy)

    result = st.session_state.get("cc_result")
    if result is not None:
        st.divider()
        _render_company_check(result)


def _render_company_check(result) -> None:
    from aristos_council.company_check import format_company_check

    st.markdown(f"### Company Check — {result.display}")
    st.caption("Single-name diagnostic · **NO VERDICT** — verdicts are cohort "
               "statements (see `docs/SCOREBOARD.md`).")
    st.caption(f"strategy: `{result.rank_strategy_id}` · lens screen: "
               f"`{result.screen_strategy_id or 'none'}` · reference: "
               f"`{result.reference_universe_id or '—'}`")

    if result.unrateable:
        st.warning(f"⚪ **UNRATEABLE** — {result.data_integrity.note}. No data, so no "
                   "diagnosis and no verdict.")
        st.info(result.pointer)
        return

    import pandas as pd

    # SCREEN — a screen-less strategy (CCFIX-2) screens nothing; say so rather than
    # diagnosing against a default lens.
    if result.screen_less:
        st.subheader("Screen — none")
        st.info("**No lens screen** — this strategy screens nothing; quality enters via "
                "ranking only. Gates below still apply.")
    else:
        st.subheader("Screen — all criteria evaluated for diagnosis")
        st.caption("A universe run excludes on the FIRST confirmed fail; here every "
                   "criterion is evaluated so the whole picture is visible.")
        srows = [{"Criterion": c.name, "Observed": _cc_num(c.observed),
                  "Threshold": _cc_num(c.threshold), "Status": c.status,
                  "Gating": "gating" if c.gating else "non-gating",
                  "Basis": c.basis or "", "Borderline": "●" if c.borderline else ""}
                 for c in result.screen]
        if srows:
            sdf = pd.DataFrame(srows)
            styler = sdf.style.map(
                lambda v: f"color: {_CC_STATUS_HEX.get(v, '')}; font-weight: 700",
                subset=["Status"])
            st.dataframe(styler, hide_index=True, width="stretch")
        # A must-fail with no observed value (e.g. PEG growth <= 0) shows its REASON,
        # not a bare "—" (CCFIX-3).
        for c in result.screen:
            if c.status == "FAIL" and c.observed is None:
                st.caption(f"↳ **{c.name}**: {c.note or 'fails closed by design'}")
        if result.market_cap_in_gates:
            st.caption("`min_market_cap` — same floor as the universe gate; shown once, "
                       "under **Gates** below.")

    if result.gates:
        st.subheader("Gates — sector / cap / payout")
        gdf = pd.DataFrame([{"Gate": g.name, "Status": g.status, "Detail": g.detail}
                            for g in result.gates])
        styler = gdf.style.map(
            lambda v: f"color: {_CC_STATUS_HEX.get(v, '')}; font-weight: 700",
            subset=["Status"])
        st.dataframe(styler, hide_index=True, width="stretch")
        for g in result.gates:                          # strategy-configured rationale (ITEM 2)
            if g.rationale:
                st.caption(f"↳ **{g.name}**: {g.rationale}")

    # FACTORS + cohort context.
    st.subheader("Factor values + cohort context")
    if result.reference_available:
        st.caption(f"Position vs the latest persisted run of "
                   f"`{result.reference_universe_id}` (run {result.reference_run_date}, "
                   f"{result.reference_cohort_n} ranked) — replayed offline, no fresh "
                   "fetch.")
    else:
        st.caption("No reference run available — showing raw values. Run that list "
                   "once (the Run tab) to get cohort context.")
    from aristos_council.company_check import format_factor_value

    for fc in result.factors:
        st.markdown(f"- **{fc.label}** (`{fc.factor}`): "
                    f"{format_factor_value(fc.factor, fc.value)} "
                    f"_[{fc.source}]_ — {fc.context}")

    # VERDICT OF RECORD (Spec 4D) — quoted verbatim from the frozen reference run when the
    # checked name had a recorded outcome; Company Check never issues one itself.
    if result.verdict_of_record:
        st.markdown(f"**VERDICT OF RECORD:** {result.verdict_of_record}")

    # Divergence flag — prominent.
    if result.divergence_flag:
        st.warning(f"**Price/fundamentals divergence** — {result.divergence_flag}")

    # Data integrity footer.
    di = result.data_integrity
    with st.expander("Data integrity"):
        st.markdown(f"- fundamentals: **{'ok' if di.fundamentals_ok else 'MISSING'}** · "
                    f"price: **{'ok' if di.price_ok else 'MISSING'}**")
        if di.abstained_criteria:
            st.markdown("- criteria not evaluated (abstained): "
                        + ", ".join(di.abstained_criteria))
        if di.not_evaluated_factors:
            st.markdown("- factors not evaluated: "
                        + ", ".join(di.not_evaluated_factors))
        for flag in di.implausible:                          # VERIFY-2 ITEM 4
            st.markdown(f"- ⚠ {flag}")

    st.info(result.pointer)
    # Unique, self-describing filenames: ticker + strategy + run-start (ITEM 6). A
    # single-name file ALWAYS carries the ticker. Two exports side by side
    # (REPORT-HTML-1): the text report stays canonical, the HTML is the shareable copy.
    from aristos_council.download_names import (
        company_check_download_name, company_check_html_download_name)
    from aristos_council.export.report_html import company_check_html

    cc_run_start = st.session_state.get("cc_run_start") or datetime.now(timezone.utc)
    cc_txt_name = company_check_download_name(result.ticker, result.rank_strategy_id,
                                               cc_run_start)
    cc_html_name = company_check_html_download_name(
        result.ticker, result.rank_strategy_id, cc_run_start)
    dl_txt, dl_html = st.columns(2)
    with dl_txt:
        st.download_button(
            f"⬇ Download check as text — {cc_txt_name}",
            data=format_company_check(result), file_name=cc_txt_name,
            mime="text/plain", key="cc_download")
    with dl_html:
        st.download_button(
            f"⬇ Download report (HTML) — {cc_html_name}",
            data=company_check_html(
                result, run_start=cc_run_start,
                strategy_display_name=st.session_state.get("cc_strategy_name", "")),
            file_name=cc_html_name,
            mime="text/html", key="cc_download_html")
    st.caption("Text is the canonical record. The HTML is one self-contained file (no "
               "external requests) for sharing outside the repo — Print → PDF for paper.")


def _cc_num(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and (abs(v) >= 1e6 or (v != 0 and abs(v) < 1e-3)):
        return f"{v:,.0f}"
    return f"{v:.4g}" if isinstance(v, float) else str(v)


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
            st.header("Run a council · Legacy")
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
            "Show validation & legacy tools", key="show_legacy",
            help="Reveal the validation assets — the known-trap bench universe and the "
                 "Classic Value baseline strategy (for side-by-side comparison) — plus "
                 "the legacy single-ticker council, its Report/History, and the "
                 "council-strategy editor. Off by default — the app opens on the live "
                 "scoreboard strategies and universes only.")

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
        # v2-ONLY landing: Run + Company Check + Scoreboard (all first-class, not legacy).
        # Validation assets hidden (show_validation=False). The tab is "Run" (FUND-UI-2):
        # there is ONE run flow to name, so naming it after the universe half is misleading.
        tab_universe, tab_company, tab_scoreboard = st.tabs(
            ["Run", "Company Check", "Scoreboard"])
        with tab_universe:
            render_universe_tab(show_validation=False)
        with tab_company:
            render_company_check_tab(show_validation=False)
        with tab_scoreboard:
            render_scoreboard_tab()
        return

    # Legacy ON: Run FIRST (Streamlit default-selects it), Company Check + Scoreboard next
    # (first-class), then the pre-v2 council browsers (Legacy), the YAML editor last. The
    # toggle is ON here, so validation assets are revealed.
    tab_universe, tab_company, tab_scoreboard, tab_report, tab_history, tab_strategy = \
        st.tabs(["Run", "Company Check", "Scoreboard", "Report · Legacy",
                 "History · Legacy", "Strategy · Legacy"])

    with tab_universe:
        render_universe_tab(show_validation=True)

    with tab_company:
        render_company_check_tab(show_validation=True)

    with tab_scoreboard:
        render_scoreboard_tab()

    with tab_report:
        st.info(f"**Legacy.** {_LEGACY_BANNER}")
        _report_tab(ticker)

    with tab_history:
        st.info(f"**Legacy.** {_LEGACY_BANNER}")
        render_history(ticker)

    with tab_strategy:
        render_strategy_tab()


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
