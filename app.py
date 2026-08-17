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

    # 3 — screen criteria (name, threshold, gating/non-gating)
    st.subheader(f"Screen criteria · {d.screen_source}")
    if d.criteria:
        st.dataframe(
            [{"Criterion": c.name, "Threshold": _cc_num(c.threshold),
              "Gating": "gating" if c.gating else "non-gating"} for c in d.criteria],
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
        st.dataframe([{"Factor": f.name, "Direction": f.direction} for f in d.factors],
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
# The interactive run cap — ONE number, shared by the guard and its message. It used to be
# re-declared per section, which is how the run flow and the editor drifted apart.
UNIVERSE_CAP = 60


def saved_list_labels(saved) -> list[str]:
    """Selector labels for the saved ticker lists — friendly name + size, with the id
    appended ONLY where two lists would otherwise share a label. Same discipline as the
    strategy picker: a label must name exactly one thing, or picking one silently loads
    another."""
    base = [f"{universe_label(u)} · {len(u.tickers)} names" for u in saved]
    times = Counter(base)
    return [b if times[b] == 1 else f"{b} ({u.id})" for u, b in zip(saved, base)]


def run_problems(universe: list[str], *, n_strategies: int, deterministic: bool,
                 has_key: bool, cap: int = UNIVERSE_CAP) -> list[str]:
    """Why the Run button is disabled, in plain sentences (empty list = runnable).

    Pure, so the one run flow's guards are unit-tested rather than eyeballed in a browser —
    and there is now ONE guard set instead of the two that had already drifted. A
    ``deterministic`` run (ranker-only, or several strategies — which is ranker-only by
    construction) cannot spend, so it never asks for an API key.
    """
    problems: list[str] = []
    if n_strategies < 1:
        problems.append("Pick at least one strategy.")
    if not universe:
        problems.append("Add at least one ticker.")
    if len(universe) > cap:
        problems.append(f"List too large ({len(universe)} > {cap}) for an interactive "
                        "run — trim it.")
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


def _universe_markdown(result) -> str:
    """The run as a self-contained markdown doc (the download; NO new storage format
    this sprint — the pipeline does not persist reports)."""
    m = result.meta
    lines = [f"# Universe run — {m['rank_strategy_id']}", "",
             f"**{_confirmation_line(m)}**", "",
             f"_{result.header}_", "",
             f"- screen: `{m['screen_strategy_id']}`",
             f"- mode: {m['council_mode']}",
             f"- ranked: {m['ranked_count']} / {m['universe_size']}"]
    if not m["ranker_only"]:
        lines.append(f"- shortlist: {len(m['shortlist'])} · est ${m['est_cost']:.2f}")
        lines.append(f"- narration coverage: {m.get('narrate_coverage', 'buys_only')}")
    lines += ["", "## Ranked (verdict of record)", ""]
    rows, factor_names = _ranked_rows(result.ranked, result.names)
    if rows:
        head = ["Position (score)", "Name", "Verdict", *factor_names]
        lines.append("| " + " | ".join(head) + " |")
        lines.append("|" + "---|" * len(head))
        for row in rows:
            cells = [row["Position (score)"], row["Name"], row["Verdict"],
                     *[str(row[f]) for f in factor_names]]
            lines.append("| " + " | ".join(cells) + " |")
    else:
        lines.append("_(no names survived the screen)_")
    from aristos_council.pipeline import (
        factor_integrity, format_integrity_entry,
        format_screen_basis_entry, ranked_abstention_footnotes, screen_basis_integrity,
    )

    for foot in ranked_abstention_footnotes(result):
        lines.append(foot)

    entries = factor_integrity(result)
    if entries:
        lines += ["", "## Factor integrity", ""]
        lines += [f"- **{e['factor']}** — {format_integrity_entry(e)}" for e in entries]
    basis_entries = screen_basis_integrity(result)
    if basis_entries:
        lines += ["", "## Screen basis", ""]
        lines += [f"- **{e['criterion']}** — {format_screen_basis_entry(e)}"
                  for e in basis_entries]
    if result.excluded:
        lines += ["", "## Excluded (screen / cap / sector)", ""]
        lines += [f"- **{display_name(t, result.names.get(t))}** — {why}"
                  for t, why in result.excluded]
    if result.unrateable:
        lines += ["", "## Unrateable (no data — no verdict)", ""]
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


def _multi_columns(multi_result) -> dict[str, str]:
    """strategy_id -> its column header: the friendly display name, with the id appended
    ONLY when two selected strategies share a label (GARP v1/v2 do) — a column must never
    silently swallow another's cells."""
    ids = multi_result.strategy_ids
    labels = {sid: (multi_result.strategy_names.get(sid) or sid) for sid in ids}
    seen: dict[str, int] = {}
    for lbl in labels.values():
        seen[lbl] = seen.get(lbl, 0) + 1
    return {sid: (lbl if seen[lbl] == 1 else f"{lbl} ({sid})")
            for sid, lbl in labels.items()}


def _multi_grid_rows(multi_result) -> list[dict]:
    """The combined grid as table rows (FUND-RUN-1) — one row per name, one column per
    strategy, plus the rank-sum. Pure, so the table and the markdown download read the
    SAME cells. ``‡`` marks a rank-sum over FEWER lenses than the run used (nothing is
    imputed for a strategy that excluded the name), so a smaller sum is never misread."""
    columns = _multi_columns(multi_result)
    rows = []
    for row in multi_result.rows:
        rs = "—" if row.rank_sum is None else str(row.rank_sum)
        if row.rank_sum is not None and not row.comparable:
            rs = f"{rs}‡"
        cells = {"Name": row.display, "Rank-sum": rs,
                 "Graded by": f"{row.graded} of {len(multi_result.strategy_ids)}"}
        for sid, header in columns.items():
            cells[header] = row.cells[sid].render()
        rows.append(cells)
    return rows


def _multi_strategy_markdown(multi_result) -> str:
    """The multi-lens re-grade as a self-contained markdown doc (the download). The
    per-strategy runs are persisted individually by the existing single-run sink; this is
    the COMBINED view, which has no on-disk format of its own."""
    m = multi_result.meta
    ids = multi_result.strategy_ids
    lines = [f"# Multi-lens re-grade — {len(ids)} strategies", "",
             f"**Running {', '.join(ids)} on {m.get('universe_id') or 'adhoc'} in "
             f"{m['council_mode']}.**", "",
             "_Verdict: deterministic ranker. No LLM ran — narration is a per-strategy "
             "run._", "",
             f"- names in cohort: {m.get('universe_size', 0)}",
             f"- ranked by ALL {len(ids)} strategies: {m.get('graded_by_all', 0)}", "",
             "## Combined grid", ""]
    rows = _multi_grid_rows(multi_result)
    if rows:
        head = list(rows[0].keys())
        lines.append("| " + " | ".join(head) + " |")
        lines.append("|" + "---|" * len(head))
        for row in rows:
            lines.append("| " + " | ".join(str(row[h]) for h in head) + " |")
    else:
        lines.append("_(no names reported)_")
    lines += ["", "Rank-sum adds the per-strategy cohort POSITIONS and is comparable only "
                  "across names ranked by ALL strategies (‡ = ranked by fewer; nothing "
                  "imputed for an exclusion).", ""]
    for sid in ids:
        res = multi_result.results[sid]
        lines += [f"## {multi_result.strategy_names.get(sid) or sid} (`{sid}`)", ""]
        lines.append(f"- ranked: {res.meta['ranked_count']} / "
                     f"{res.meta['universe_size']}")
        if res.excluded:
            lines += ["", "Excluded (screen / cap / sector):", ""]
            lines += [f"- **{display_name(t, res.names.get(t))}** — {why}"
                      for t, why in res.excluded]
        if res.unrateable:
            lines += ["", "Unrateable (no data — no verdict):", ""]
            lines += [f"- **{display_name(t, res.names.get(t))}** — {why}"
                      for t, why in res.unrateable]
        lines.append("")
    # ONE cohort under N lenses — so ONE membership record covers the whole grid.
    lines += _cohort_membership_lines(m)
    return "\n".join(lines)


def _render_multi_strategy_result(multi_result) -> None:
    """The combined grid (FUND-RUN-1) — presentation only: every cell is the
    verdict-of-record a single run of that strategy produces."""
    m = multi_result.meta
    ids = multi_result.strategy_ids

    persisted = st.session_state.get("uni_multi_persisted")
    if persisted:
        paths = ", ".join(f"`{md.relative_to(ROOT)}`" for md, _html in persisted)
        st.success(f"💾 Saved the {len(persisted)} per-strategy run(s) to: {paths}")

    st.markdown(f"### Combined grid — {len(ids)} strategies × "
                f"{m.get('universe_size', 0)} names")
    st.caption(f"Running {', '.join(ids)} on {m.get('universe_id') or 'adhoc'} in "
               f"{m['council_mode']}.")
    st.caption("**Verdict: deterministic ranker.** No LLM ran — narration stays a "
               "per-strategy run.")
    rows = _multi_grid_rows(multi_result)
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.info("No names reported.")
    st.caption(f"Rank-sum adds the per-strategy cohort POSITIONS; comparable only across "
               f"the {m.get('graded_by_all', 0)} name(s) ranked by ALL {len(ids)} "
               f"strategies (‡ = ranked by fewer — nothing is imputed for an exclusion).")

    for sid in ids:
        res = multi_result.results[sid]
        label = multi_result.strategy_names.get(sid) or sid
        with st.expander(f"{label} — exclusions & unrateable "
                         f"({len(res.excluded)} excluded, "
                         f"{len(res.unrateable)} unrateable)"):
            if res.excluded:
                st.markdown("**Excluded (failed rule + observed value):**")
                for t, why in res.excluded:
                    st.markdown(f"- **{display_name(t, res.names.get(t))}** — {why}")
            if res.unrateable:
                st.markdown("**Unrateable (no data — no verdict):**")
                for t, why in res.unrateable:
                    st.markdown(f"- **{display_name(t, res.names.get(t))}** — {why}")
            if not res.excluded and not res.unrateable:
                st.caption("Every name was rateable and ranked.")

    run_start = st.session_state.get("uni_run_start") or datetime.now(timezone.utc)
    st.download_button(
        "⬇ Download combined grid (markdown)",
        data=_multi_strategy_markdown(multi_result).encode("utf-8"),
        file_name=f"multi_lens_regrade_{run_start.strftime('%Y%m%d_%H%M')}.md",
        mime="text/markdown", key="uni_multi_download")


def _render_universe_result(result) -> None:
    m = result.meta

    # UI-FIX-1: where this run landed on disk, prominent — the first thing a user sees
    # so a completed (possibly paid) run is never mistaken for session-only output.
    persisted = st.session_state.get("uni_persisted_paths")
    if persisted:
        md_path, html_path = persisted
        st.success(f"💾 Saved to: `{md_path.relative_to(ROOT)}` and "
                  f"`{html_path.relative_to(ROOT)}`")

    # ITEM 6: the confirmation line first — a wrong dropdown is visible immediately.
    st.caption(_confirmation_line(m))
    # 1 — the division-of-labor header line, prominent.
    st.markdown(f"#### {result.header}")
    meta_bits = (f"rank: `{m['rank_strategy_id']}` · screen: "
                 f"`{m['screen_strategy_id']}` · universe: "
                 f"`{m.get('universe_id', '—')}` · mode: {m['council_mode']} · "
                 f"ranked {m['ranked_count']}/{m['universe_size']}")
    if not m["ranker_only"]:
        meta_bits += (f" · shortlist {len(m['shortlist'])} · "
                      f"est ${m['est_cost']:.2f} · "
                      f"narrate {m.get('narrate_coverage', 'buys_only')}")
    st.caption(meta_bits)

    # 2 — RANKED table: sortable, verdict palette, per-factor ranks (imputed *).
    st.subheader("Ranked — verdict of record")
    rows, factor_names = _ranked_rows(result.ranked, result.names)
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
        from aristos_council.pipeline import ranked_abstention_footnotes

        for foot in ranked_abstention_footnotes(result):
            st.caption(foot)
    else:
        st.info("No names survived the screen to be ranked.")

    # 2b — FACTOR INTEGRITY: which computation path produced each factor per name
    # (ITEM 1) — EV vs EBIT/mcap proxy vs abstained, no longer silent.
    from aristos_council.pipeline import (
        factor_integrity, format_integrity_entry,
        format_screen_basis_entry, screen_basis_integrity,
    )

    entries = factor_integrity(result)
    if entries:
        st.subheader("Factor integrity")
        st.caption("Per factor, how each ranked name's value was produced — a silent "
                   "fallback (stale cache / missing fields) now shows in plain text.")
        for e in entries:
            st.markdown(f"- **{e['factor']}** — {format_integrity_entry(e)}")

    # 2c — SCREEN BASIS: the measurement basis each screen criterion used (payout FCF
    # vs EPS fallback) across the screened names.
    basis_entries = screen_basis_integrity(result)
    if basis_entries:
        st.subheader("Screen basis")
        st.caption("Which measurement basis each screen criterion used across the "
                   "screened names — a marked fallback (e.g. EPS when FCF is absent) "
                   "shows in plain text.")
        for e in basis_entries:
            st.markdown(f"- **{e['criterion']}** — {format_screen_basis_entry(e)}")

    # 3 — Excluded (screen / cap / sector / payout): a neutral table.
    if result.excluded:
        st.subheader(f"Excluded — screen / cap / sector · {len(result.excluded)}")
        st.dataframe([{"Name": display_name(t, result.names.get(t)), "Reason": why}
                      for t, why in result.excluded],
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
    picked_labels = st.multiselect(
        "Strategies", labels, default=[labels[default_index(choices)]],
        key="uni_strategies",
        help="One strategy runs the full flow (narration included). Several grade the SAME "
             "list under several lenses in one run and report ONE combined grid — "
             "deterministic, no LLM, no cost.")
    strategies = resolve_all(choices, picked_labels)
    multi = len(strategies) > 1
    for s in strategies:
        bits = f"`{s.id}`"                               # the stable record key
        if strategy_role(s):
            bits += f" · {strategy_role(s)}"
        st.caption(bits)
    if len(strategies) == 1 and getattr(strategies[0], "description", ""):
        st.caption(strategies[0].description.strip())
    # The cost estimate + the narration settings describe the FIRST selected strategy; a
    # multi-strategy run is deterministic, so neither is in play then.
    rank_strategy = strategies[0] if strategies else choices[0].strategy

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
    if picked_list is not None and not unchanged:
        st.caption("Edited — runs as an ad-hoc list (fingerprinted). Save it below to keep "
                   f"the changes in **{universe_label(picked_list)}**.")

    graded = graded_universe_ids(SNAPSHOTS_CSV)
    is_mine = (picked_list is not None and getattr(picked_list, "local", False)
               and picked_list.id not in graded)
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

    col_a, col_b = st.columns(2)
    with col_a:
        ranker_only = st.checkbox("Ranker only — no LLM, no cost", value=False,
                                  key="uni_ranker_only", disabled=multi)
    with col_b:
        # Value stays "second_opinion" (behavior unchanged); only its LABEL flags it as
        # the experimental null-result mode.
        _mode_label = {
            "narrator": "narrator",
            "second_opinion": "second_opinion (experimental — null result; see README)",
        }
        # Several lenses over one list is a DETERMINISTIC comparison — it cannot spend, so
        # the LLM settings are greyed out rather than quietly multiplied by N.
        mode = st.selectbox(
            "Council mode", ["narrator", "second_opinion"],
            key="uni_mode", disabled=ranker_only or multi,
            format_func=lambda m: _mode_label[m],
            help="narrator: the LLM explains the ranker verdict (default). "
                 "second_opinion: an independent comparison verdict — a pre-registered "
                 "experiment that returned a null result; kept behind this flag.")
        # NARR-2 ITEM 2: which ranked names get narrated. buys_only (default, cheapest)
        # for stock screens; all for core/ETF cohorts where the HOLDs are live
        # candidates being compared, not rejects.
        _cov_label = {"buys_only": "Narrate: BUYs only (cheapest)",
                      "all": "Narrate: all ranked names"}
        narrate_coverage = st.selectbox(
            "Narration coverage", ["buys_only", "all"],
            key="uni_coverage", disabled=ranker_only or multi,
            format_func=lambda c: _cov_label[c],
            help="BUYs only: narrate the shortlist (cheapest — good for stock screens). "
                 "all: narrate every ranked name — for core/ETF cohorts where the HOLDs "
                 "are live options you're comparing, not rejects.")

    st.caption(f"**{len(universe)}** ticker(s).")

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    # A multi-strategy run is deterministic by construction (FUND-RUN-1), so it needs no
    # key and shows no cost estimate — same footing as the Ranker-only checkbox.
    deterministic = ranker_only or multi
    # ONE guard set, pure and unit-tested (FUND-UI-2). The old flow re-declared CAP and the
    # key check per section, which is precisely how the two halves drifted apart.
    problems = run_problems(universe, n_strategies=len(strategies),
                            deterministic=deterministic, has_key=has_key)

    if not deterministic and universe and len(universe) <= UNIVERSE_CAP:
        est = estimate_cost(
            _estimate_shortlist_size(len(universe), rank_strategy,
                                     narrate_coverage=narrate_coverage))
        st.caption(f"Estimated council cost ≈ **${est:.2f}** — upper bound (pre-screen); "
                   "the exact shortlist (after the screen prefilter) is shown after ranking.")
    if multi:
        st.caption(f"Multi-lens re-grade: **{len(strategies)}** strategies × "
                   f"**{len(universe)}** name(s) — deterministic ranker only "
                   f"(no narration, no cost), reported as ONE combined grid.")

    for msg in problems:
        st.info(msg)

    if multi:
        label = f"▶ Run {len(strategies)} strategies (free)"
    else:
        label = "▶ Run ranker (free)" if ranker_only else "▶ Run"
    run = st.button(label, type="primary", disabled=bool(problems), key="uni_run")

    if run and multi:
        run_start = datetime.now(timezone.utc)
        status = st.status("Starting…", expanded=True)
        try:
            from aristos_council.pipeline import run_multi_strategy_pipeline

            multi_result = run_multi_strategy_pipeline(
                universe, strategy_ids, universe_id=universe_id,
                strategies_dir=STRATEGIES_DIR, universes_dir=UNIVERSES_DIR,
                freeze_dir=ROOT / "runs",
                progress=lambda msg: status.update(label=msg))
        except Exception as exc:
            status.update(label="Run failed", state="error")
            st.exception(exc)
            st.session_state.pop("uni_multi_result", None)
        else:
            status.update(label="Done.", state="complete")
            st.session_state["uni_multi_result"] = multi_result
            st.session_state["uni_run_start"] = run_start
            st.session_state["uni_universe_display_name"] = universe_display_name
            # Each column is a complete run of that strategy — persist them with the SAME
            # helper a single run uses, so no completed run lives only in the session.
            st.session_state["uni_persisted_paths"] = None
            st.session_state["uni_multi_persisted"] = [
                _persist_universe_run(res, run_start, universe_display_name)
                for res in multi_result.results.values()]
            st.session_state.pop("uni_result", None)
    elif run:
        run_start = datetime.now(timezone.utc)       # run-start for the download name (ITEM 6)
        status = st.status("Starting…", expanded=True)
        try:
            from aristos_council.pipeline import run_rank_pipeline

            result = run_rank_pipeline(
                universe, rank_strategy.id, universe_id=universe_id,
                council_mode=mode, ranker_only=ranker_only,
                narrate_coverage=narrate_coverage,
                strategies_dir=STRATEGIES_DIR, universes_dir=UNIVERSES_DIR,
                # Freeze this run's raw inputs so Company Check's reference-cohort reader
                # (_latest_reference_run) can replay it offline — without this the UI
                # never wrote runs/ and cohort context was dead code (ITEM 1).
                freeze_dir=ROOT / "runs",
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
            st.session_state["uni_run_start"] = run_start
            st.session_state["uni_universe_display_name"] = universe_display_name
            # UI-FIX-1: persist BEFORE rendering — a completed (possibly paid) run must
            # survive a restart even if nobody clicks a download button.
            st.session_state["uni_persisted_paths"] = _persist_universe_run(
                result, run_start, universe_display_name)
            st.session_state.pop("uni_multi_result", None)
            st.session_state.pop("uni_multi_persisted", None)

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
        st.caption("No reference run available — showing raw values. Run the universe "
                   "once (Universe Run tab) to get cohort context.")
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
