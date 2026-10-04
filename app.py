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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

from aristos_council.ui_text import escape_dollars, install as install_dollar_safety
from pydantic import ValidationError

from aristos_council.data.adapter import (
    DataUnavailable, display_name, normalize_ticker)
from aristos_council.pipeline import (
    DEFAULT_NARRATION_CAP, DEFAULT_NARRATION_LEVEL, NARRATION_LEVELS)
from aristos_council.demo_surface import (
    ASSET_MODES, DEFAULT_ASSET_MODE, ETFS, asset_mode_filter,
    strategy_label, strategy_role, suggested_first,
    universe_label, universe_role, visible_universes)
from aristos_council.costs import actual_vs_estimate, cost_phrase

# READER-1 — what one summary costs, for the checkbox label and the Run-button line. One
# call on the cheapest tier over a facts pack of a few thousand tokens; stated as "about"
# because it is a hint on a control, not a billed figure (the run records the real one).
# READER-3: the reader may be asked twice (one retry on a failed check), so the
# hint covers both calls. A hint that quotes only the best case is not a hint.
READER_COST_HINT = "1-2 cents"
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
    return escape_dollars(text) if text else text


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

          /* HTML-NARR-MD-1 — the council narrative is rendered as real HTML (the same
             builder the export uses), not raw markdown text, so give its table and
             blockquote-callout shapes a presentable style here instead of leaving them
             to the browser's bare defaults. */
          .council-narrative table { border-collapse: collapse; width: 100%; margin: 8px 0; }
          .council-narrative th, .council-narrative td {
            border: 1px solid rgba(128, 128, 128, .35); padding: 5px 8px; text-align: left;
          }
          .council-narrative .callout.structural {
            margin: 10px 0; padding: 9px 12px; border: 2px solid rgba(178, 59, 59, .6);
            border-left-width: 6px;
          }
          .council-narrative .callout.structural ul { margin: 6px 0 0; padding-left: 18px; }
          .council-narrative .callout.structural p { margin: 4px 0; }

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


def opt_lens_checkbox_key(input_kind: str):
    """TAB-MERGE-1 commit 1 — the ONE options block's key family (``opt_lens_*``),
    replacing the ``uni_lens_*``/``cc_lens_*`` duplicates. Qualified by ``input_kind``
    ("list" | "company") — commit 3's ``render_run_tab`` renders only ONE of the two
    branches per script run (the other is simply never called), so collision is no
    longer possible either way, but the qualifier is kept: it is what lets a COMPANY
    pick and a LIST pick remember their own separate lens ticks across a switch back and
    forth on the same page."""
    def _key(strategy_id: str) -> str:
        return f"opt_lens_{input_kind}_{strategy_id}"
    return _key


def render_lens_checkboxes(choices, key_for) -> list[tuple[str, bool]]:
    """THE lens tick boxes (SHORTLIST-3 / CAPTION-2): same list, same order, same VISIBLE caption
    under each lens, in up to three contiguous columns. The Run tab and Company Check both call
    this, so a change to how lenses are offered lands on both at once. ``key_for`` maps a strategy
    id to its session key. Returns ``[(label, ticked), ...]`` for ``selected_labels``."""
    extras: list[tuple[str, bool]] = []
    if not choices:
        return extras
    n_cols = min(3, len(choices))
    per_col = -(-len(choices) // n_cols)             # ceil: contiguous, offer-ordered
    for i, col in enumerate(st.columns(n_cols)):
        with col:
            for c in choices[i * per_col:(i + 1) * per_col]:
                extras.append((c.label, st.checkbox(c.label, key=key_for(c.id))))
                # CAPTION-2: a VISIBLE caption, not a hover tooltip — a reader comparing five
                # checkboxes cannot hover five things at once.
                if lens_caption(c.strategy):
                    st.caption(lens_caption(c.strategy))
    return extras


def lens_selection_captions(strategies) -> None:
    """TAB-MERGE-1 commit 1 — the id + role + "asks" caption loop under the TICKED
    lenses (CAPTION-1), unified from the two copies that existed today: the Run tab's own
    (id, role, asks-for-every-lens, single-lens description) and Company Check's own
    (id, role ONLY — no asks repeat, no single-lens description). Unified on the FULLER
    version: it is a strict ADDITION for Company Check (the "asks" text was already
    visible once per lens under its checkbox via CAPTION-2 inside
    ``render_lens_checkboxes`` — this is the SAME text, repeated under the ticked-lens
    caption exactly as the Run tab already did), never a removal for either caller."""
    for s in strategies:
        bits = f"`{s.id}`"                               # the stable record key
        if strategy_role(s):
            bits += f" · {strategy_role(s)}"
        st.caption(bits)
        _asks = (getattr(s, "asks", "") or "").strip()
        if _asks:
            st.caption(_asks)
    if len(strategies) == 1 and getattr(strategies[0], "description", ""):
        st.caption(strategies[0].description.strip())


# --------------------------------------------------------------------------- #
# TAB-MERGE-1 commit 1 — the ONE options block both input kinds call: lenses (+
# captions), the plain-English summary checkbox, and the council-opinion checkbox
# (company input always; list input too, per commit 3, unless the validation toggle
# restores the old 3-way run-mode radio instead). The list side's OWN narration-level /
# cap / skip-doubted / spend-threshold / size-floor controls are UNCHANGED and stay in
# render_run_tab's list branch — they are not part of this shared block.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RunOptions:
    strategies: list = field(default_factory=list)
    with_summary: bool = False
    with_council: bool = False          # always False when show_council=False


# The "Plain-English summary" checkbox means two different things on the two inputs (one
# writer narrates the RUN, the other narrates the COMPANY) — same label, same new key
# family, but the help text must keep saying which, so it stays input-kind-specific.
_SUMMARY_HELP = {
    "list": ("Adds a short note at the top of the report saying what the run asked, what "
            "happened, what survived the checks and what to doubt — in language a "
            "non-specialist reads in a minute. It is written from the tables, and every "
            "number in it is checked back against them; a summary that fails that check "
            "is withheld with its reason rather than published. It explains the results; "
            "it never recommends anything."),
    "company": ("One short note at the top, written from the tables below; every number "
               "in it is checked back against them, and a summary that fails is withheld "
               "with its reason. It explains; it never recommends. One model call, off "
               "unless you tick it."),
}
_COUNCIL_HELP = ("The four specialists, a critic and a narrator — the same council the "
                "rest of Aristos uses — read this company's votes, marks and readings "
                "and write about them. They never vote: the agreement above stays the "
                "verdict of record. About six model calls, mostly on the cheap tier; "
                "off unless you tick it.")


def render_run_options(choices, *, input_kind: str, show_council: bool) -> RunOptions:
    """ONE options block, called once per input kind. ``input_kind`` is "list" (the Run
    tab) or "company" (Company Check) — it only qualifies widget keys (see
    ``opt_lens_checkbox_key``) and picks the right summary help text; the WIDGETS
    themselves, their labels and their order are identical either way.

    ``show_council`` renders the council-opinion checkbox or not: True for Company
    Check (which has one today); False for the Run tab in commit 1, which keeps its own
    run-mode radio untouched and simply never reads ``.with_council`` (always False)."""
    key_for = opt_lens_checkbox_key(input_kind)
    st.markdown("**Lenses**")
    st.caption("Every ticked lens is an equal vote. Forensic marks; it does not vote.")
    _preselect_default_lens(choices, seeded_key=f"opt_lenses_seeded_{input_kind}",
                            key_for=key_for)
    extras = render_lens_checkboxes(choices, key_for)
    strategies = resolve_all(choices, selected_labels(extras=extras))
    lens_selection_captions(strategies)
    with_summary = st.checkbox(
        "Plain-English summary", value=False, key=f"opt_summary_{input_kind}",
        help=_SUMMARY_HELP[input_kind])
    with_council = False
    if show_council:
        with_council = st.checkbox(
            "Council opinion", value=False, key=f"opt_council_{input_kind}",
            help=_COUNCIL_HELP)
    return RunOptions(strategies=strategies, with_summary=with_summary,
                      with_council=with_council)


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


def run_button_label(run_mode: str = RUN_MODE_RANKER, *, n_strategies: int,
                     est_cost: float | None = None,
                     narrated_count: int | None = None,
                     with_reader: bool = False,
                     with_council: bool | None = None) -> str:
    """The button says what will happen and what it costs, on its own line:

        ``▶ Run 5 lenses — deterministic, free``
        ``▶ Run 5 lenses — up to 13 names narrated, est. ≤ $0.68``
        ``▶ Run — narrated, est. $0.42``

    ``with_council`` (TAB-MERGE-1 commit 1, not None): the COMPANY-input label instead
    of the list-input one — ``run_mode``/``est_cost``/``narrated_count`` are ignored,
    and the label states the (summary, council) pair exactly as Company Check's own
    inline ``_label`` logic did (call counts are hardcoded there too; the two label
    styles have no shared cost-estimate machinery to draw on, same as before the merge).

    ``narrated_count`` is the size of the UNION of every lens's BUYs (NARR-UNION-1) — the
    thing the bill is actually proportional to. Five lenses produced 18 BUY verdicts over
    only 13 distinct names on the 2026-08-24 run, and it is the 13 that gets charged, so
    it is the 13 the button states."""
    if with_council is not None:
        extras = [n for n, on in (("summary", with_reader), ("council opinion", with_council))
                 if on]
        if not extras:
            return "▶ Run company check (free — no LLM)"
        if extras == ["summary"]:
            return "▶ Run company check + summary (one model call)"
        if extras == ["council opinion"]:
            return "▶ Run company check + council opinion (~6 model calls)"
        return "▶ Run company check + summary + council opinion (~7 model calls)"
    what = f"Run {n_strategies} lenses" if n_strategies > 1 else "Run"
    # READER-1: the summary is ONE call and is independent of the run mode, so a
    # ranker-only run with it ticked is no longer free and the button must stop saying so.
    reader_tail = f" + summary ~{READER_COST_HINT}" if with_reader else ""
    if not run_mode_narrates(run_mode):
        return (f"▶ {what} — deterministic, free{reader_tail}" if with_reader
                else f"▶ {what} — deterministic, free")
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
        return (f"▶ {what} — free{reader_tail} · then choose whether to narrate "
                f"up to {narrated_count} names{tail}")
    verb = "narrate" if run_mode == RUN_MODE_NARRATOR else "take a second opinion"
    tail = f" · ≤ ${est_cost:.2f} total" if est_cost is not None else ""
    return f"▶ {what} — free{reader_tail} · then choose whether to {verb}{tail}"


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


def lens_caption(strategy) -> str:
    """The one-line definition to show under a lens's own control — its ``asks``, or "".

    CAPTION-2. CAPTION-1 put this sentence under the lenses a reader had ALREADY chosen,
    which is the wrong moment: the question a lens asks is what you need in order to choose
    it. Pure, so the text a checkbox carries is unit-tested rather than eyeballed."""
    return (getattr(strategy, "asks", "") or "").strip()


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

    Defaults to the FIRST ticked lens's own floor, so the control opens showing what the
    run would do untouched; clearing it removes the floor for this run entirely. The floor
    is a COHORT statement — it decides who is in the room — so one value applies to every
    lens in the run (SHORTLIST-3: no lens is privileged, so there is no "its" floor to
    prefer, and the first ticked one is simply the one already in hand)."""
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


# --------------------------------------------------------------------------- #
# BACKTEST-2 — Run-tab track-record badges
#
# The Run tab already knows its universe directly (unlike Company Check, which infers a cohort
# from one company's industry): if the universe's own name slugifies to one of the 13 backtested
# cohorts, its lenses' badges are shown; any other universe (the ordinary case — an ad-hoc list,
# a saved list of a different shape) shows none. Display only; nothing here touches a vote, a
# rank or the combined grid.
# --------------------------------------------------------------------------- #
def _run_track_record(m: dict, lens_ids) -> tuple:
    """``(cohort_slug or None, {lens_id: Badge})`` for a Run-tab universe. Empty/``None`` when the
    universe's name does not slugify to a cohort with any committed backtest result."""
    from aristos_council.backtest import cohort_slug, has_track_record, track_record

    name = (m.get("universe_name") or "").strip()
    slug = cohort_slug(name) if name else None
    if not slug or not has_track_record(slug):
        return None, {}
    return slug, {sid: track_record(slug, sid) for sid in lens_ids}


def _run_track_record_lines(m: dict, lens_labels: dict) -> list:
    """The caption lines for a Run-tab track-record section — the cohort/date caption, one badge
    per lens (by its display label), and the summary count — or ``[]`` when the universe matched
    no backtested cohort. Shared by the UI renderers and the markdown exports so the two can
    never drift."""
    from aristos_council.backtest import format_track_record_summary, track_record_caption

    slug, badges = _run_track_record(m, list(lens_labels))
    if not badges:
        return []
    lines = []
    caption = track_record_caption(slug)
    if caption:
        lines.append(caption)
    lines += [f"{lens_labels.get(sid, sid)}: {b.label}" for sid, b in badges.items()]
    summary = format_track_record_summary(badges.values())
    if summary:
        lines.append(summary)
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
    # BACKTEST-2 — the same track-record lines the UI shows, so the download can't drift from it.
    _lens_label = m.get("rank_strategy_name") or m["rank_strategy_id"]
    lines += [f"- {line}" for line in
             _run_track_record_lines(m, {m["rank_strategy_id"]: _lens_label})]
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

    # DETAIL-1: the same three-column table the merged report renders, from the same
    # builder — the counts a reader checks (did this factor abstain, and for whom) are
    # columns, not the tail of a sentence.
    from aristos_council.pipeline import lens_detail as _lens_detail
    _detail = _lens_detail(result)
    if _detail.sources:
        lines += ["", f"## {PROVENANCE_SECTION_TITLE}", "",
                  f"_{PROVENANCE_SECTION_NOTE}_", ""]
        lines += _md_table(["Factor", "Real data", "Abstained"],
                           [{"Factor": r["label"], "Real data": r["real"],
                             "Abstained": r["abstained"]} for r in _detail.sources])
    # VALBAND-1 / PRICE-2 / REPORT-1: the absolute counterpart to the ranked table, in
    # the RECORD too — a rank position ages into "it was cheapest of those"; the price
    # and the band age into "it cost $119.85 and sat at the 1st percentile of its own
    # five years", which is the sentence a reader of an old run actually needs.
    from aristos_council.pipeline import exclusion_rows, valuation_band_table

    # PRICE-1/PRICE-2/REPORT-1: price, 12-month range and valuation in ONE table — the
    # same names are no longer listed twice in two sections.
    lines += _valuation_band_markdown(valuation_band_table(result))
    if result.excluded:
        # DETAIL-1: grouped by the rule that fired, worst miss first — the same groups,
        # in the same order, as the merged report and the HTML.
        lines += ["", "## Excluded — did not pass a rule, so was never ranked", ""]
        for group in _detail.groups:
            count = f"**{group.count} {'name' if group.count == 1 else 'names'}**"
            rule = f" · rule: {group.rule}" if group.rule else ""
            note = f" ({group.note})" if group.note else ""
            ident = "" if group.is_gate else f" `{group.key}`"
            lines += ["", f"**{group.title}**{ident}{rule} · {count}{note}", ""]
            if group.why:
                lines += [f"_{group.why}_", ""]
            if group.keeps_sentences:
                lines += [f"- **{n.name}** — {n.sentence}" for n in group.names]
                continue
            if group.is_gate:
                lines += [", ".join(n.name for n in group.names)]
                continue
            lines += _md_table(["Name", "Measured", "Note"],
                               [{"Name": n.name, "Measured": n.measured or "—",
                                 "Note": ", ".join(n.badges)} for n in group.names])
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


def _thin_lens_hint(level: str) -> str:
    """The NARR-ZERO-1 pre-run hint, from the last ranked result for this cohort.

    Pure apart from the session lookup, so the judgement itself
    (``pipeline.thin_voting_lens_note``) is unit-tested without Streamlit.
    """
    from aristos_council.pipeline import thin_voting_lens_note

    pending = st.session_state.get("uni_pending_narration") or {}
    result = (pending.get("result")
              or st.session_state.get("uni_multi_result")
              or st.session_state.get("uni_result"))
    if result is None:
        return ""
    try:
        return thin_voting_lens_note(result, level)
    except Exception:                    # a hint must never be able to break the tab
        return ""


def _narration_levers(pending: dict) -> dict:
    """The NARR-2 levers off a pending narration, with the defaults a record written
    before they existed would need. One reader, so the confirmation and the run cannot
    disagree about what was asked for."""
    return {"level": pending.get("level", DEFAULT_NARRATION_LEVEL),
            "cap": int(pending.get("cap", DEFAULT_NARRATION_CAP)),
            "skip_marked": bool(pending.get("skip_marked", True))}


def _render_narration_line(result) -> None:
    """NARR-2's line on screen — the same builder the two reports render."""
    from aristos_council.pipeline import narration_line

    plan = (getattr(result, "meta", None) or {}).get("narration")
    if not plan:
        return
    st.caption(narration_line(plan))
    missing = plan.get("not_narrated") or []
    if missing:
        with st.expander(f"Met the rule and not narrated · {len(missing)}",
                         expanded=False):
            for m in missing:
                marks = f" · {' · '.join(m['marks'])}" if m.get("marks") else ""
                plural = "s" if m["buy_votes"] != 1 else ""
                st.markdown(f"- **{m['name']}** — {m['buy_votes']} BUY vote"
                            f"{plural}{marks}")


def _render_narration_confirmation() -> None:
    """CONFIRM-SPEND-1 — the confirmation step, between the free ranking and the spend.

    The ranking has ALREADY happened, so this states the EXACT count and the EXACT
    estimate — no upper bound, no coefficient, and the names themselves. Money is only
    ever spent from the button that carries that figure. "Keep the free ranking" reports
    the run exactly as a ranker-only run: the work is done and is never discarded."""
    from aristos_council.pipeline import (
        narrate_multi_strategy, narrate_rank_result, narration_plan, narration_record,
        narration_zero_line)

    pending = st.session_state.get("uni_pending_narration")
    if not pending:
        return
    result = pending["result"]
    plan = narration_plan(result, pending.get("coverage", "buys_only"),
                          **_narration_levers(pending))
    run_start = st.session_state.get("uni_run_start") or datetime.now(timezone.utc)
    display_name = st.session_state.get("uni_universe_display_name", "")

    if not plan["count"]:
        # Nothing to narrate — there is no spend to confirm, so do not ask.
        #
        # NARR-ZERO-1: but the RECORD must still say the narrator was asked for. This path
        # never calls narrate_multi_strategy, so before this fix the published report was
        # the rank stage's, headed "ranker only, no AI commentary" — contradicting the
        # mode the owner had chosen, with nothing anywhere saying the rule matched zero
        # names. A correct run was indistinguishable from a narrator that had crashed.
        if getattr(result, "meta", None) is None:
            result.meta = {}
        result.meta["narration"] = narration_record(plan, mode="narrator")
        result.meta["council_mode"] = "narrator"
        result.meta["ranker_only"] = False
        st.session_state.pop("uni_pending_narration", None)
        (_publish_multi if pending["kind"] == "multi" else _publish_single)(
            result, run_start, display_name)
        st.info(narration_zero_line(result) or
                "The ranking produced no name to narrate, so the run is complete and "
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

    plan = narration_plan(pending["result"], pending.get("coverage", "buys_only"),
                          **_narration_levers(pending))
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




def _reader_markdown(reader) -> list[str]:
    """READER-1 in the .md — the same five paragraphs the HTML renders."""
    from aristos_council.reader import (READER_SECTION_NOTE, READER_SECTION_TITLE,
                                        reader_paragraphs)

    if reader is None:
        return []
    lines = ["", f"## {READER_SECTION_TITLE}", ""]
    if not reader.available:
        return lines + [f"_{reader.note}_"]
    for lead, text in reader_paragraphs(reader.summary):
        lines += [f"**{lead}** {text}", ""]
    return lines + [f"_{READER_SECTION_NOTE}_"]



def _narration_line_markdown(result) -> list[str]:
    """NARR-2's line in the .md — the same builder the HTML renders."""
    from aristos_council.pipeline import narration_line

    plan = (getattr(result, "meta", None) or {}).get("narration")
    if not plan:
        return []
    lines = ["", f"_{narration_line(plan)}_"]
    missing = plan.get("not_narrated") or []
    if missing:
        lines += ["", "**Met the rule and not narrated**", ""]
        for m in missing:
            marks = f" · {' · '.join(m['marks'])}" if m.get("marks") else ""
            plural = "s" if m["buy_votes"] != 1 else ""
            lines.append(f"- **{m['name']}** — {m['buy_votes']} BUY vote{plural}{marks}")
    return lines


def _shortlist_markdown(ag, lens_agreement_table) -> list[str]:
    """SHORTLIST-3 in the .md — the same cells the HTML renders, from the same builder."""
    if ag is None:
        return []
    lines = ["", f"## {ag.title}", "", f"_{ag.rule_sentence}_"]
    if not ag.available:
        return lines
    if ag.overlap_note:
        lines += ["", f"**{ag.overlap_note}**"]
    lines.append("")
    cols, rows = lens_agreement_table(ag)
    if rows:
        lines += _md_table(cols, rows)
    else:
        lines.append("_No name was rated BUY by any voting lens. That is a result, not a "
                     "gap._")
    # COHORT-BAND-1 — directly under the table: the one thing a reader scanning the
    # shortlist could read every row and still miss.
    from aristos_council.pipeline import cohort_band_line
    band_line = cohort_band_line(ag)
    if band_line:
        lines += ["", f"**{band_line}**"]
    if ag.no_buy_count:
        plural = "s" if ag.no_buy_count != 1 else ""
        lines += ["", f"_{ag.no_buy_count} name{plural} had no BUY from any lens, and "
                      "are not listed here._"]
    return lines


# --------------------------------------------------------------------------- #
# DETAIL-1 — the per-lens detail section in markdown
# --------------------------------------------------------------------------- #
# The mirror of ``report_html._lens_detail_html``: same builder, same groups, same order,
# same names. Markdown has no <details>, so a collapsed group becomes a one-line summary
# with its names inline — the information is identical, only the folding is absent.
def _lens_detail_markdown(detail) -> list[str]:
    """One lens's grouped detail section as markdown lines."""
    from aristos_council.pipeline import DETAIL_SOURCES_TITLE

    lines: list[str] = []
    if detail.asks:                                          # CAPTION-1
        lines += [f"_{detail.asks}_", ""]
    headline = detail.headline
    if detail.badge_note:
        headline = f"{headline} {detail.badge_note}"
    lines += [f"**{headline}**"]

    for group in detail.groups:
        count = f"**{group.count} {'name' if group.count == 1 else 'names'}**"
        rule = f" · rule: {group.rule}" if group.rule else ""
        note = f" ({group.note})" if group.note else ""
        # The criterion id stays beside the label, as the old per-name bullets carried it.
        ident = "" if group.is_gate else f" `{group.key}`"
        lines += ["", f"**{group.title}**{ident}{rule} · {count}{note}", ""]
        if group.why:
            lines += [f"_{group.why}_", ""]
        if group.keeps_sentences:
            lines += [f"- **{n.name}** — {n.sentence}" for n in group.names]
            continue
        if group.is_gate:
            # No <details> in markdown: the names go inline on one line rather than
            # becoming thirty bullets that the HTML deliberately folds away.
            lines += [", ".join(n.name for n in group.names)]
            continue
        lines += _md_table(
            ["Name", "Measured", "Note"],
            [{"Name": n.name, "Measured": n.measured or "—",
              "Note": ", ".join(n.badges)} for n in group.names])

    if detail.unrateable:
        lines += ["", "**No usable data — no verdict was formed**", ""]
        lines += [f"- **{name}** — {why}" for name, why in detail.unrateable]
    if detail.fetch_errors:
        lines += ["", "**Data fetch failed — re-run to recover**", ""]
        lines += [f"- **{name}** — {why}" for name, why in detail.fetch_errors]
    if detail.sources:
        lines += ["", f"**{DETAIL_SOURCES_TITLE}**", ""]
        lines += _md_table(["Factor", "Real data", "Abstained"],
                           [{"Factor": r["label"], "Real data": r["real"],
                             "Abstained": r["abstained"]} for r in detail.sources])
    return lines


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
        fetch_guard_line,
        floor_override_line,
        lens_asks,
        lens_agreement_table, lens_detail, provenance_sentences, report_sections,
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
    # FETCH-GUARD-1 — above the lenses and the run line, because it governs how to read
    # everything below it.
    _guard = fetch_guard_line(m.get("fetch_guard") or {})
    if _guard:
        lines.append(f"**⚠ {_guard}**")
    if run_start is not None:
        lines.append(f"**Run: {_local_stamp(run_start)} — "
                     f"{_mode_phrase(m.get('council_mode', ''))}**")
    else:
        lines.append(f"**Run: {_mode_phrase(m.get('council_mode', ''))}**")
    lines += ["", f"_{multi_header_line(multi_result)}_", ""]
    lines += [f"### {multi_summary_line(multi_result)}", ""]

    # BACKTEST-2 — the same track-record lines the UI shows, so the download can't drift from it.
    _run_lens_labels = {sid: label_with_id(names.get(sid) or sid, sid) for sid in ids}
    _track_lines = _run_track_record_lines(m, _run_lens_labels)
    if _track_lines:
        lines += [f"_{line}_" for line in _track_lines] + [""]

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

    # 1a (READER-1) — the note that explains the rest, above everything it explains.
    lines += _reader_markdown(getattr(multi_result, "reader", None))

    # 1b (SHORTLIST-1) — the answer, before the evidence for it.
    lines += _shortlist_markdown(getattr(multi_result, "lens_agreement", None),
                                 lens_agreement_table)

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

    # 7 — what DOES vary per lens. DETAIL-1: the SAME builder the HTML renders, so the
    # downloaded markdown and the downloaded HTML cannot show different names or a
    # different order.
    for sid in ids:
        res = multi_result.results[sid]
        lines += ["", f"## {label_with_id(names.get(sid) or sid, sid)} — detail", ""]
        lines += _lens_detail_markdown(
            lens_detail(res, strategy_id=sid, label=names.get(sid) or sid))

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
    the name, in the verdict table's own order. ``[]`` on a ranker-only run.

    NARR-ZERO-1: a run that ASKED to narrate and matched nothing gets the section anyway,
    carrying the reason. An absent section is indistinguishable from a feature that was
    never switched on — which is exactly how this defect read."""
    if not multi_result.narratives:
        from aristos_council.pipeline import narration_zero_line
        zero = narration_zero_line(multi_result)
        return ["", "## Narration", "", f"_{zero}_"] if zero else []
    m = multi_result.meta
    basis = m.get("narration_basis", "")
    count = m.get("narrated_count", len(multi_result.narratives))
    lines = ["", "## Narration", "",
             f"_{count} name{'s' if count != 1 else ''} narrated — {basis}. ONE section "
             "per NAME: a name several lenses bought is narrated once, with each lens's "
             "verdict attributed. The narrator explains the ranker's verdicts; it never "
             "weighs the lenses against each other._", ""]
    # NARR-PARSE-1 — above the sections, because a reader scanning the shortlist would
    # otherwise only find out by opening the one section that says nothing.
    from aristos_council.pipeline import narration_failure_line
    failure_line = narration_failure_line(multi_result)
    if failure_line:
        lines += [f"**{failure_line}**", ""]
    for ticker, text in multi_result.narratives.items():
        display = next((r.display for r in multi_result.rows if r.ticker == ticker),
                       ticker)
        lines += [f"### {display}", "", text, ""]
    # NARR-2: which rule chose them, and what met it and is not here.
    lines += _narration_line_markdown(multi_result)
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



def _render_shortlist(ag) -> None:
    """The agreement table on screen — the same builder the two reports render, so the app
    and the downloaded files cannot show different names."""
    if ag is None:
        return
    from aristos_council.pipeline import cohort_band_line, lens_agreement_table

    st.subheader(ag.title)
    st.caption(ag.rule_sentence)
    if not ag.available:
        return
    if ag.overlap_note:
        st.warning(ag.overlap_note)
    cols, rows = lens_agreement_table(ag)
    if rows:
        st.dataframe(rows, column_order=cols, hide_index=True, width="stretch")
    else:
        st.info("No name was rated BUY by any voting lens. That is a result, not a gap.")
    band_line = cohort_band_line(ag)            # COHORT-BAND-1
    if band_line:
        st.markdown(f"**{band_line}**")
    if ag.no_buy_count:
        plural = "s" if ag.no_buy_count != 1 else ""
        st.caption(f"{ag.no_buy_count} name{plural} had no BUY from any lens, and are not "
                   "listed here.")



# --------------------------------------------------------------------------- #
# ASSET-MODE-1 — when a pasted list held names for the OTHER side of the switch
# --------------------------------------------------------------------------- #
# The asset-kind gate is untouched: it excluded those names before this change and it
# excludes them now, with the same reason on the same line of the report. What was missing
# was the ONE sentence that turns "asset kind 'ETF' outside this strategy's scope" from a
# dead end into a next step — because the reader now has a switch that would grade them.
#
# Above the results, never inside them. The report is a record and is unchanged; this is
# the app telling you what to do next.
_WRONG_KIND_REASON = "asset kind "


def wrong_kind_count(result, *, mode: str) -> int:
    """How many names this run's asset-kind gate excluded as the OTHER kind.

    Counted from the run's own exclusion reasons — the gate already wrote them — so this
    can never disagree with the report about which names were gated. Zero when the run
    gated none, which is the ordinary case and renders nothing.

    The KIND needs no inspecting: the gate fires only for a name the lens does not admit,
    so in Stocks mode every gated name is a fund and in ETFs mode every one is a company.
    ``mode`` therefore decides only the wording, not the count."""
    results = getattr(result, "results", None)
    runs = list(results.values()) if isinstance(results, dict) else [result]
    seen: set = set()
    for run in runs:
        for ticker, reason in (getattr(run, "excluded", None) or []):
            if str(reason).startswith(_WRONG_KIND_REASON):
                seen.add(ticker)
    return len(seen)


def wrong_kind_line(count: int, *, mode: str) -> str:
    """The sentence, or "". It names the count, what they are, and the way to grade
    them — a warning that does not say what to do instead is only half a warning."""
    if not count:
        return ""
    if mode == ETFS:
        return (f"{count} of these names are stocks and were not graded. "
                "Switch to Stocks to analyse them.")
    return (f"{count} of these names are ETFs and were not graded. "
            "Switch to ETFs to analyse them.")


def _render_wrong_kind_line(result) -> None:
    line = wrong_kind_line(wrong_kind_count(result, mode=asset_mode()),
                           mode=asset_mode())
    if line:
        st.info(line)


# --------------------------------------------------------------------------- #
# TAB-MERGE-1 part 2 commit 4 — click-through from a list result to a company page.
# "Open a company page" loads the Company input with the picked ticker; it NEVER
# starts a run and NEVER spends on its own (design doc E1/E3) — the run button, with
# its own cost label, stays the user's press. The free "In your list: ..." line(s),
# already known from THIS run, ride along in session state and are shown once, at the
# top of the company page, on click-through only (_render_company_report).
# --------------------------------------------------------------------------- #
def _multi_list_context_lines(multi_result, ticker: str) -> list[str]:
    """One "In your list: <lens>: <result>" line per lens, reusing votes_from_multi's
    OWN per-lens status→text mapping (ranked/excluded/too_few/...) — no new wording,
    no new arithmetic; it is the SAME function Company Check itself uses to read a
    multi-strategy result's outcome for one ticker."""
    from aristos_council.company_report import votes_from_multi
    votes = votes_from_multi(multi_result, ticker)
    return [f"In your list: {v.label}: {v.result()}" for v in votes]


def _single_list_context_lines(result, ticker: str, *, lens_label: str) -> list[str]:
    """The single-lens equivalent of ``_multi_list_context_lines`` — RankPipelineResult
    has no MultiStrategyResult shape to feed votes_from_multi, so this reads the SAME
    already-computed position (rank_engine.cohort_positions) and verdict directly,
    in the SAME wording LensVote.result() uses."""
    from aristos_council.rank_engine import MIN_RANKABLE_COHORT, cohort_positions, too_few_to_rank_text
    from aristos_council.report_language import verdict_word
    from aristos_council.tools.valuation_band import ordinal

    target = ticker.upper()
    rt = next((r for r in result.ranked if r.ticker.upper() == target and not r.excluded), None)
    if rt is not None:
        cohort_size = sum(1 for r in result.ranked if not r.excluded)
        if cohort_size < MIN_RANKABLE_COHORT:
            where = too_few_to_rank_text(cohort_size)
        else:
            pos, _tied = cohort_positions(result.ranked).get(target, (None, False))
            where = f"{ordinal(pos)} of {cohort_size}" if pos else f"ranked of {cohort_size}"
        return [f"In your list: {lens_label}: {verdict_word(rt.verdict)} - {where}"]
    excluded_reason = next((why for t, why in result.excluded if t.upper() == target), None)
    if excluded_reason is not None:
        return [f"In your list: {lens_label}: does not apply - {excluded_reason}"]
    unrateable_reason = next((why for t, why in result.unrateable if t.upper() == target), None)
    if unrateable_reason is not None:
        return [f"In your list: {lens_label}: {unrateable_reason}"]
    return []


def _render_open_as_company(tickers: list[str], *, make_lines, key_prefix: str) -> None:
    """The picker + button itself. ``make_lines(ticker)`` is called ONLY on click (not
    for every option up front) and returns the free context line(s) to carry over."""
    if not tickers:
        return
    st.markdown("**Open a company page**")
    col_pick, col_open = st.columns([4, 1])
    with col_pick:
        picked = st.selectbox("pick a name", tickers, label_visibility="collapsed",
                              key=f"{key_prefix}_open_company_pick")
    with col_open:
        go = st.button("Open", key=f"{key_prefix}_open_company_button")
    if go:
        # The "Input" radio (key run_input_kind) and the Ticker box (key cc_ticker)
        # have ALREADY been instantiated earlier in THIS run (render_input runs first)
        # — Streamlit forbids overwriting a widget's own key after that. Stash a
        # PENDING switch instead and rerun; render_input applies it on the NEXT run,
        # before either widget is drawn (the same pre-instantiation-write pattern the
        # find-box's "Use this company" button already uses for cc_ticker alone).
        st.session_state["_pending_open_as_company"] = (normalize_ticker(picked), make_lines(picked))
        st.rerun()


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

    # SHORTLIST-1/2 — the answer, on screen, ahead of the evidence for it. Both reports
    # have carried this section since SHORTLIST-1; the Run tab carried only the summary
    # line's count, so a reader working in the app could see THAT names survived without
    # seeing WHICH — and, after SHORTLIST-2, without seeing the price warning on one.
    _render_shortlist(getattr(multi_result, "lens_agreement", None))
    # BACKTEST-2 — this universe's own track record per lens, when its name matches one of the
    # 13 backtested cohorts; nothing shown otherwise. Display only — the shortlist above is
    # unaffected.
    for line in _run_track_record_lines(m, lens_labels):
        st.caption(line)
    # NARR-2: which names the run explained and which met the rule without being explained.
    # The Run tab does not render the narrations themselves (they travel in the downloaded
    # report), but the SELECTION is a decision the reader made and should see the result of.
    _render_narration_line(multi_result)

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
    from aristos_council.pipeline import lens_detail, provenance_sentences

    for sid in ids:
        res = multi_result.results[sid]
        with st.expander(f"{lens_labels[sid]} — detail "
                         f"({res.meta['ranked_count']} ranked, "
                         f"{len(res.excluded)} excluded, "
                         f"{len(res.unrateable)} with no data)"):
            # DETAIL-1: the same groups, in the same order, as the downloaded report.
            detail = lens_detail(res, strategy_id=sid, label=lens_labels[sid])
            if detail.badge_note:
                st.caption(detail.badge_note)
            for group in detail.groups:
                rule = f" · rule: {group.rule}" if group.rule else ""
                note = f" ({group.note})" if group.note else ""
                ident = "" if group.is_gate else f" `{group.key}`"
                st.markdown(f"**{group.title}**{ident}{rule} · **{group.count} "
                            f"{'name' if group.count == 1 else 'names'}**{note}")
                if group.why:
                    st.caption(group.why)
                if group.keeps_sentences:
                    for n in group.names:
                        st.markdown(f"- **{n.name}** — {n.sentence}")
                    continue
                if group.is_gate:
                    st.caption(", ".join(n.name for n in group.names))
                    continue
                st.dataframe([{"Name": n.name, "Measured": n.measured or "—",
                               "Note": ", ".join(n.badges)} for n in group.names],
                             hide_index=True, width="stretch")
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

    _render_open_as_company(
        sorted({r.ticker for r in multi_result.rows}),
        make_lines=lambda t: _multi_list_context_lines(multi_result, t),
        key_prefix="multi")


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
    # PRICE-STALE-1 — the same line the report carries, but LOUD here: a caption among
    # captions is exactly how a stale cache went unnoticed in the first place.
    from aristos_council.pipeline import price_stale_line
    _stale = price_stale_line(result)
    for line in head[1:]:
        (st.warning if line == _stale else st.caption)(line)
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
    # BACKTEST-2 — this lens's track record in this cohort, when the universe matches one of the
    # 13 backtested cohorts; nothing shown otherwise. Display only.
    _lens_label = m.get("rank_strategy_name") or m["rank_strategy_id"]
    for line in _run_track_record_lines(m, {m["rank_strategy_id"]: _lens_label}):
        st.caption(line)

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
        from aristos_council.pipeline import narration_failure_line
        _failed = narration_failure_line(result)
        if _failed:
            st.warning(_failed)                 # NARR-PARSE-1 — never a silent gap
        from aristos_council.pipeline import narration_zero_line
        _zero = narration_zero_line(result)     # NARR-ZERO-1 — say WHY there is nothing
        if _zero:
            st.info(_zero)
        if result.narratives:
            verdict_of = {r.ticker: r.verdict.upper() for r in result.ranked}
            for ticker, text in result.narratives.items():
                v = verdict_of.get(ticker, "")
                disp = display_name(ticker, result.names.get(ticker))
                with st.expander(f"{disp}{(' · ' + v) if v else ''} — narration"):
                    st.markdown(_md(text) or "_(no narrative produced)_")
        elif not _zero:
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

    _lens_label_for_click_through = m.get("rank_strategy_name") or m["rank_strategy_id"]
    _click_through_tickers = sorted(
        {r.ticker for r in result.ranked} | {t for t, _ in result.excluded}
        | {t for t, _ in result.unrateable})
    _render_open_as_company(
        _click_through_tickers,
        make_lines=lambda t: _single_list_context_lines(
            result, t, lens_label=_lens_label_for_click_through),
        key_prefix="single")



def _preselect_default_lens(choices, *, seeded_key: str = "uni_lenses_seeded", key_for) -> None:
    """Tick the suggested-first lens ONCE per session (SHORTLIST-3).

    With the primary dropdown gone, nothing would be selected on a fresh start and the Run
    button would open disabled — which reads as breakage rather than as a choice. So the
    lens ``default_index`` already nominated is pre-ticked, exactly once: the flag is what
    makes unticking it stick, instead of the box re-ticking itself on every rerun.

    ``key_for`` is REQUIRED (TAB-MERGE-1 part 2 commit 5) — render_run_options is the
    ONE caller since Part 1, and it always passes opt_lens_checkbox_key(input_kind);
    the old ``uni_lens_*`` fallback this used to default to (``lens_checkbox_key``) was
    dead code once that became true, and is deleted, not just defaulted away.
    """
    if st.session_state.get(seeded_key) or not choices:
        return
    st.session_state[seeded_key] = True
    chosen = choices[default_index(choices)]
    st.session_state.setdefault(key_for(chosen.id), True)


# --------------------------------------------------------------------------- #
# TAB-MERGE-1 commit 3 — ONE Analyse tab: Company or Cohort / list, decided by an
# EXPLICIT switch, never inferred from what was typed.
# --------------------------------------------------------------------------- #
INPUT_COMPANY = "company"
INPUT_LIST = "list"


@dataclass(frozen=True)
class InputChoice:
    """``render_input``'s result. Company-mode fields are set only for
    ``kind == INPUT_COMPANY``; list-mode fields only for ``kind == INPUT_LIST`` — the
    other side's fields sit at their defaults and the caller never reads them."""
    kind: str = INPUT_LIST
    # company mode
    ticker: str = ""
    include_small: bool = False
    small_company_line: str = ""        # "" unless shown
    # list mode
    universe: list = field(default_factory=list)
    picked_list: object = None
    universe_id: Optional[str] = None
    universe_display_name: str = ""
    derived_from: str = ""


def _market_cap_from_index(ticker: str, *, store=None) -> Optional[float]:
    """A FREE (offline, no network) market cap for a ticker already known to the local
    market index — the SAME field (``IndexRow.market_cap_usd``) ``run_company_report``'s
    own smallcap detection reads off ``peer_group.subject``. Used here only to decide the
    ADVANCE info line; ``run_company_report`` re-derives this itself when it actually
    runs and is the real authority — this is a best-effort heads-up, never a guarantee."""
    from aristos_council.market_index import peers as _peers
    try:
        group = _peers(ticker, store=store)
    except Exception:                                       # noqa: BLE001 — never block the page
        return None
    subject = getattr(group, "subject", None)
    return getattr(subject, "market_cap_usd", None) if subject is not None else None


def _resolve_company_cap(ticker: str, *, match_cap_usd: Optional[float], adapter_factory,
                         store=None) -> tuple[Optional[float], bool]:
    """``(market_cap_usd, was_live_fetched)``. Three sources, free ones first: (1) the
    find-box's own ``CompanyMatch.market_cap_usd`` (already fetched by the search);
    (2) the local index, for a hand-typed ticker that happens to be listed (free, no
    network); (3) a live, CACHED fundamentals fetch — the one genuinely unknown-at-
    pick-time case (a ticker the index has no row for at all). ``adapter_factory`` is
    called ONLY if tier (3) is actually reached — tiers (1)/(2) must never touch the
    real adapter factory at all (TEST-ISOLATION-1; also just wasted work when the free
    tiers already answered). Whatever it returns reuses the SAME cache_dir/day as the
    run that follows, so this costs no second network call once the run proceeds — the
    on-disk day-cache serves the identical request again, even from a fresh instance."""
    if match_cap_usd is not None:
        return match_cap_usd, False
    cap = _market_cap_from_index(ticker, store=store)
    if cap is not None:
        return cap, False
    try:
        cap = adapter_factory().get_fundamentals(ticker).market_cap
    except Exception:                                       # noqa: BLE001 — unknown, not a crash
        cap = None
    return cap, True


def small_company_notice(cap_usd: Optional[float], *, live_fetched: bool) -> str:
    """The exact info line (owner's ruling 2026-10-03) for a sub-$5bn company — "" when
    not small, or when the cap could not be determined at all (a missing cap is never
    guessed either way, SMALLCAP-VIEW-1's own rule)."""
    from aristos_council.smallcap_band import SMALLCAP_CEILING_USD
    from aristos_council.tools.price_context import format_money

    if cap_usd is None or cap_usd >= SMALLCAP_CEILING_USD:
        return ""
    cap_text = format_money(cap_usd, "USD", abbreviate=True)
    tail = (" Its size was not known in advance; this was decided once its market cap "
           "was read." if live_fetched else "")
    return (f"This company is worth {cap_text}, below the $5bn rule, so it will be "
           f"compared with other small companies. Results are outside the tested "
           f"range.{tail}")


def render_input(*, show_validation: bool) -> InputChoice:
    """ONE switch — "Company" or "Cohort / list" — never inferred from the ticker box's
    contents. Hidden (not greyed) in ETF mode: the market index covers listed common
    stocks only, so there is no company search and no company peer group for a fund."""
    # TAB-MERGE-1 part 2 commit 4 — a click-through "Open a company page" (under a
    # list result, rendered LATER in the same run) cannot set run_input_kind/cc_ticker
    # directly: Streamlit forbids writing a widget's own key after that widget has
    # already rendered this run, and the Input radio + Ticker box below both render
    # on EVERY call. So it stashes a pending switch and reruns; applied HERE, first,
    # before either widget is instantiated.
    pending = st.session_state.pop("_pending_open_as_company", None)
    if pending is not None:
        ticker, lines = pending
        st.session_state["run_input_kind"] = "Company"
        st.session_state["cc_ticker"] = ticker
        st.session_state["cc_from_list"] = (ticker, lines)

    etf_mode = asset_mode() == ETFS
    if etf_mode:
        kind = INPUT_LIST
        st.caption("ETF mode: a list of fund tickers only — there is no per-fund peer "
                  "group to check one against.")
    else:
        # TAB-MERGE-1 part 2 commit 1 — owner's ruling 2026-10-03: Company is the
        # DEFAULT (index=0). Part 1 defaulted to "Cohort / list" to minimise test
        # churn from the merge itself; this is a deliberate, separate UI decision.
        choice = st.radio("Input", ["Company", "Cohort / list"], index=0,
                          key="run_input_kind", horizontal=True,
                          help="Company: one name against its own peer group. "
                               "Cohort / list: several names, ranked and compared "
                               "against each other.")
        kind = INPUT_COMPANY if choice == "Company" else INPUT_LIST

    if kind == INPUT_COMPANY:
        # FIND-COMPANY-1 — fronts the bare ticker box: type part of a name or a ticker,
        # pick a match, it fills the box below. Offline (the local market index and the
        # built cohorts' own member lists only, each read once and cached) — no network
        # call, no holdings data.
        st.markdown("**Find a company**")
        find_query = st.text_input(
            "Find a company", value="", key="cc_find", label_visibility="collapsed",
            placeholder="Type a name or ticker — siemens, novo, rheinmetall, 2330…")
        if find_query.strip():
            from aristos_council.company_search import search_companies
            found = search_companies(find_query)
            if not found.matches:
                st.caption("No match in the local market index.")
            else:
                if not found.cohorts_known:
                    st.caption("No cohort has been built locally yet, so cohort "
                              "membership is not shown.")

                def _match_label(m) -> str:
                    where = f"{m.name} ({m.ticker}) — {m.where} — {m.market_cap_display}"
                    if m.cohorts:
                        return f"{where} — {', '.join(m.cohorts)}"
                    return where if not found.cohorts_known else f"{where} — no cohort"

                match_options = [_match_label(m) for m in found.matches]
                picked = st.selectbox("Matches", match_options, key="cc_find_pick",
                                      label_visibility="collapsed")
                if st.button("Use this company", key="cc_find_use"):
                    chosen = found.matches[match_options.index(picked)]
                    st.session_state["cc_ticker"] = chosen.ticker
                    # SMALLCAP-VIEW-1 — the find box already paid for this fetch; carry
                    # it to the cap-resolution check below so a hand-edit of the ticker
                    # box (NOT matching what was just picked) can tell "stale pick" from
                    # "still the one I picked" and never trust a cap for the wrong name.
                    st.session_state["cc_matched_cap"] = (chosen.ticker, chosen.market_cap_usd)
                    st.rerun()

        ticker = normalize_ticker(st.text_input("Ticker", value="", key="cc_ticker",
                                                placeholder="MU"))
        matched = st.session_state.get("cc_matched_cap")
        match_cap_usd = matched[1] if matched and matched[0] == ticker else None

        small_line = ""
        include_small = False
        # LAZY — the real adapter factory is touched only if the live-fetch tier is
        # actually reached (an unindexed ticker). Tiers (1)/(2) are free/offline and
        # must never build it at all (TEST-ISOLATION-1; also just wasted work when a
        # free tier already answered). The adapter the actual run uses is built fresh,
        # at run time, in ``_render_company_run`` — a fresh instance shares the SAME
        # on-disk day-cache, so nothing here costs that run a second network call.
        if ticker:
            cap_usd, live_fetched = _resolve_company_cap(
                ticker, match_cap_usd=match_cap_usd, adapter_factory=_company_check_adapter)
            small_line = small_company_notice(cap_usd, live_fetched=live_fetched)
            include_small = bool(small_line)
            if small_line:
                st.info(small_line)
        return InputChoice(kind=INPUT_COMPANY, ticker=ticker, include_small=include_small,
                           small_company_line=small_line)

    # --- list input: unchanged from the pre-merge Run tab (saved-list picker, ticker
    # box, save-list expander) --------------------------------------------------------- #
    from aristos_council.universe import list_universes
    from aristos_council.universe_editor import (
        existing_universe_ids, graded_universe_ids, list_id_from_name,
        parse_ticker_lines, save_local_universe)

    saved = visible_universes(list_universes(UNIVERSES_DIR), show_validation=show_validation)
    saved = [u for u in saved if _mode_filters()[1](u)]
    NEW_LIST = "New list"
    list_labels = [NEW_LIST] + saved_list_labels(saved)
    list_choice = st.selectbox("List", list_labels, key="uni_list",
                               help="Your saved ticker lists. Selecting one loads it "
                                    "below, where you can edit it before running.")
    picked_list = (saved[list_labels.index(list_choice) - 1]
                  if list_choice != NEW_LIST else None)
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
    if len(universe) == 1:
        st.caption(f"Just **{universe[0]}** — open this as a company page instead?")

    unchanged = picked_list is not None and universe == list(picked_list.tickers)
    universe_id = picked_list.id if unchanged else None
    universe_display_name = picked_list.display_name if unchanged else ""
    derived_from = ("" if unchanged or picked_list is None
                    else (picked_list.display_name or picked_list.id))

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
        _theses = ["", "value", "growth", "income", "quality", "funds"]
        _current = getattr(picked_list, "thesis", "") or ""
        list_thesis = st.selectbox(
            "Built for (optional)", _theses,
            index=_theses.index(_current) if _current in _theses else 0,
            format_func=lambda t: t or "— not stated —",
            key="uni_list_thesis",
            help="What this list was assembled to find. Recorded on the list and stated in "
               "the run's summary; it never filters a lens or blocks a run. Leave blank "
               "to make no claim.")
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
                        graded_ids=graded, overwrite=True, thesis=list_thesis,
                        asset_kind=_mode_asset_kind())
                else:
                    new_id = list_id_from_name(name, existing_universe_ids(UNIVERSES_DIR))
                    path = save_local_universe(
                        UNIVERSES_DIR, id=new_id, tickers=universe, created=created,
                        display_name=name.strip(), graded_ids=graded, thesis=list_thesis,
                        asset_kind=_mode_asset_kind())
            except (ValueError, ValidationError) as exc:
                st.error(str(exc))
            else:
                st.success(f"Saved **{name.strip() or path.stem}** → "
                          f"`{path.relative_to(ROOT)}` ({len(universe)} names).")

    return InputChoice(kind=INPUT_LIST, universe=universe, picked_list=picked_list,
                       universe_id=universe_id, universe_display_name=universe_display_name,
                       derived_from=derived_from)


def render_run_tab(show_validation: bool = False) -> None:
    """TAB-MERGE-1 commit 3 — ONE tab, "Analyse": Company or Cohort / list, picked by an
    explicit switch (``render_input``). Options (lenses, summary, council) are shown
    once (commit 1). The two run ENGINES (``run_company_report`` for a company,
    ``run_multi_strategy_pipeline``/``run_rank_pipeline`` for a list) and their own
    result renderers are UNCHANGED — this only decides which one runs and shows its
    EXISTING page, in its EXISTING order (Part 2 reorders the company page)."""
    st.subheader("Analyse — one company, or a cohort")
    st.caption("Screen → rank → gates issue the verdict of record; the LLM only "
               "narrates. Pick Company or Cohort / list, pick strategies, run.")

    choice = render_input(show_validation=show_validation)

    # STRATEGIES. ONE picker (FUND-UI-2, strategy/picker.py), shared by both input kinds
    # exactly as it was shared by the two separate tabs before this merge.
    choices = strategy_choices([o[2] for o in list_rank_strategy_options(STRATEGIES_DIR)],
                               show_validation=show_validation)
    choices = [c for c in choices if _mode_filters()[0](c.strategy)]
    if not choices:
        st.error(f"No {asset_mode()} strategies found under {STRATEGIES_DIR}")
        return
    labels = choice_labels(choices)          # kept, as before (unused; see the Part 2 cleanup)

    if choice.kind == INPUT_COMPANY:
        _render_company_run(choice, choices)
    else:
        _render_list_run(choice, choices, show_validation=show_validation)


def _render_company_run(choice: InputChoice, choices) -> None:
    """The company-input half of the Analyse tab — unchanged from the pre-merge Company
    Check tab, except: options come from the shared block (commit 1), and
    ``include_small``/the adapter come from ``render_input``'s own auto-detection
    (commit 3) instead of a tick box."""
    import os

    from aristos_council.company_report import run_company_report

    run_options = render_run_options(choices, input_kind="company", show_council=True)
    strategies = run_options.strategies
    with_summary = run_options.with_summary
    with_council = run_options.with_council

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if (with_summary or with_council) and not has_key:
        st.info("This needs ANTHROPIC_API_KEY in the environment or `.env`; without it the page "
                "runs without it and says so.")

    run = st.button(_md(run_button_label(n_strategies=len(strategies),
                                         with_reader=with_summary,
                                         with_council=with_council)),
                    type="primary", disabled=not choice.ticker, key="cc_run")
    if run:
        run_start = datetime.now(timezone.utc)       # run-start for the download name (ITEM 6)
        status = st.status("Starting…", expanded=True)
        try:
            report = run_company_report(
                choice.ticker, [s_.id for s_ in strategies], adapter=_company_check_adapter(),
                strategies_dir=STRATEGIES_DIR, universes_dir=UNIVERSES_DIR,
                runs_dir=ROOT / "runs", with_summary=with_summary, with_council=with_council,
                include_small=choice.include_small,
                progress=lambda msg: status.update(label=msg))
        except Exception as exc:
            status.update(label="Run failed", state="error")
            st.exception(exc)
            st.session_state.pop("cc_report", None)
        else:
            status.update(label="Done.", state="complete")
            st.session_state["cc_report"] = report
            st.session_state["cc_run_start"] = run_start

    report = st.session_state.get("cc_report")
    if report is not None:
        st.divider()
        _render_company_report(report)


def _render_list_run(choice: InputChoice, choices, *, show_validation: bool) -> None:
    """The list-input half of the Analyse tab — unchanged pipeline calls and result
    renderers; what changed: options come from the shared block (commit 1); "Council
    opinion" ticked/unticked now drives Narrator/Ranker-only by default, with the old
    3-way Run-mode radio (Second opinion) restored only behind the validation toggle —
    never both controls at once; the spend threshold and size-floor override moved into
    an "Advanced (list only)" expander."""
    import os

    from aristos_council.pipeline import NARRATION_BASIS
    from aristos_council.reproducibility import estimate_cost

    universe = choice.universe
    picked_list = choice.picked_list
    universe_id = choice.universe_id
    universe_display_name = choice.universe_display_name
    derived_from = choice.derived_from

    # TAB-MERGE-1 commit 3: "Council opinion" ticked = Narrator, unticked = Ranker only.
    # "Second opinion" is KEPT, visible only behind the validation toggle — as the OLD
    # 3-way radio, restored in place of the checkbox so the two controls never both show.
    show_run_mode_radio = show_validation
    options = render_run_options(choices, input_kind="list",
                                 show_council=not show_run_mode_radio)
    strategies = options.strategies
    with_reader = options.with_summary
    multi = len(strategies) > 1
    rank_strategy = strategies[0] if strategies else None

    # STRAT-PICKER-1: which lenses can HONESTLY grade this cohort (unchanged).
    all_rank_strategies = [c.strategy for c in choices]
    cohort_kind = cohort_asset_kind(universe_id, all_rank_strategies)
    applicable = applicable_rank_strategies(all_rank_strategies, cohort_kind)
    st.caption(cohort_scope_note(cohort_kind, len(applicable), adhoc=universe_id is None))
    for s in strategies:
        scope_warning = out_of_scope_note(s, cohort_kind)
        if scope_warning:
            st.warning(scope_warning)

    strategy_ids = [s.id for s in strategies]

    # RUNMODE-1 — the mode in force. Behind the validation toggle, the ORIGINAL 3-way
    # radio (unchanged mechanism: it follows the lens count until touched, then obeys the
    # user). Otherwise it is DERIVED from the one "Council opinion" checkbox above.
    n_strategies = len(strategies)
    if show_run_mode_radio:
        st.session_state.setdefault("uni_run_mode_choice", default_run_mode(n_strategies))
        st.session_state.setdefault(
            "uni_run_mode", effective_run_mode(st.session_state["uni_run_mode_choice"],
                                               n_strategies=n_strategies))
        run_mode = st.radio(
            "Run mode", RUN_MODES,
            format_func=lambda m: RUN_MODE_LABELS[m], key="uni_run_mode",
            help="Ranker only: the deterministic ranking, free. "
                 "Narrator: the LLM explains the ranker's verdict. "
                 "Second opinion: an independent comparison verdict — a "
                 "pre-registered experiment that returned a null result; kept "
                 "behind this option.")
        st.session_state["uni_run_mode_choice"] = run_mode
    else:
        run_mode = RUN_MODE_NARRATOR if options.with_council else RUN_MODE_RANKER
    if n_strategies > 1 and run_mode_narrates(run_mode):
        st.caption(MULTI_LENS_NARRATION_NOTE)

    # NARR-2: WHICH names get narrated — unchanged location/behaviour, list-only, HIDDEN
    # (not greyed) when nothing narrates.
    narrate_coverage = "buys_only"
    if run_mode_narrates(run_mode):
        narrate_level = st.selectbox(
            "Narrate", list(NARRATION_LEVELS),
            key="uni_narr_level",
            format_func=lambda k: NARRATION_LEVELS[k],
            help="Which names to explain. The lenses that VOTE are the ticked ones "
                 "that are not checks; a check marks rather than votes.")
        n_voting = sum(1 for st_ in strategies
                       if (getattr(st_, "kind", "selector") or "selector") != "check")
        if n_voting == 2:
            st.caption("2 voting lenses ticked: most = all.")
        _thin = _thin_lens_hint(narrate_level)
        if _thin:
            st.caption(_thin)
        narrate_cap = int(st.number_input(
            "Up to", min_value=1, max_value=60, value=DEFAULT_NARRATION_CAP, step=1,
            key="uni_narr_cap",
            help="How many names to explain, at most, in agreement-table order. Each "
                 "one is a model call."))
        narrate_skip = st.checkbox(
            "Skip names doubted by Forensic", value=True,
            key="uni_narr_skip",
            help="On: a name a check lens doubted is left out. Off: it is explained "
                 "too, with the narrator told the doubt. A PRICED-HIGH name is always "
                 "explained either way — that it is dear against its own history is "
                 "the thing most worth explaining, not a reason to skip it.")
    else:
        narrate_level = st.session_state.get("uni_narr_level", DEFAULT_NARRATION_LEVEL)
        narrate_cap = int(st.session_state.get("uni_narr_cap", DEFAULT_NARRATION_CAP))
        narrate_skip = bool(st.session_state.get("uni_narr_skip", True))

    # TAB-MERGE-1 commit 3 — list-only, tucked away: the spend-confirmation threshold and
    # the company-size floor override. Same widgets, same keys, same behaviour; only the
    # container is new.
    with st.expander("Advanced (list only)"):
        if run_mode_narrates(run_mode):
            confirm_threshold = read_threshold(st.number_input(
                "Ask before narrating when the estimate exceeds:",
                min_value=0.0, max_value=1000.0, step=1.0,
                value=read_threshold(
                    st.session_state.get("uni_confirm_threshold", DEFAULT_CONFIRM_THRESHOLD),
                    default=DEFAULT_CONFIRM_THRESHOLD),
                format="%.2f", key="uni_confirm_threshold",
                help="0 = always ask. At or below this figure a narrated run goes straight "
                     "through after the free ranking; above it the run stops and shows the "
                     "exact count and cost for you to confirm."), default=0.0)
        else:
            confirm_threshold = read_threshold(
                st.session_state.get("uni_confirm_threshold", DEFAULT_CONFIRM_THRESHOLD),
                default=0.0)
        min_market_cap_override = _company_size_floor_override(rank_strategy, len(strategies))

    # The two pipeline arguments, derived from the mode in force (UI layer only).
    ranker_only, mode = run_mode_arguments(run_mode)

    _cap_now = universe_cap(ranker_only)
    st.caption(f"**{len(universe)}** ticker(s) — up to **{_cap_now}** for "
               f"{'a ranker-only' if ranker_only else 'a narrated'} run.")

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    deterministic = ranker_only
    problems = run_problems(universe, n_strategies=len(strategies),
                            deterministic=deterministic, has_key=has_key)

    est = None
    narrated_count = None
    if (not deterministic and universe and rank_strategy is not None
            and len(universe) <= UNIVERSE_CAP):
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

    run = st.button(_md(run_button_label(
                        run_mode, n_strategies=n_strategies, est_cost=est,
                        narrated_count=narrated_count, with_reader=with_reader)),
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

            multi_result = run_multi_strategy_pipeline(
                universe, strategy_ids, universe_id=universe_id,
                strategies_dir=STRATEGIES_DIR, universes_dir=UNIVERSES_DIR,
                freeze_dir=ROOT / "runs", with_valuation_band=True,
                derived_from=derived_from,
                min_market_cap_override=min_market_cap_override,
                with_reader=with_reader,
                cohort_thesis=getattr(picked_list, "thesis", "") or "",
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
            _publish_multi(multi_result, run_start, universe_display_name)
            if run_mode_narrates(run_mode):
                _offer_or_narrate(
                    {"kind": "multi", "result": multi_result, "mode": mode,
                     "coverage": narrate_coverage,
                     "level": narrate_level, "cap": narrate_cap,
                     "skip_marked": narrate_skip},
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

            result = run_rank_pipeline(
                universe, rank_strategy.id, universe_id=universe_id,
                council_mode=mode, ranker_only=True,
                narrate_coverage=narrate_coverage, derived_from=derived_from,
                with_valuation_band=True,
                strategies_dir=STRATEGIES_DIR, universes_dir=UNIVERSES_DIR,
                freeze_dir=ROOT / "runs",
                min_market_cap_override=min_market_cap_override,
                progress=lambda msg: status.update(label=msg))
        except Exception as exc:
            status.update(label="Run failed", state="error")
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
                     "coverage": narrate_coverage,
                     "level": narrate_level, "cap": narrate_cap,
                     "skip_marked": narrate_skip},
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
        _render_wrong_kind_line(multi_result)
        _render_multi_strategy_result(multi_result)

    result = st.session_state.get("uni_result")
    if result is not None:
        st.divider()
        _render_wrong_kind_line(result)
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


def _company_check_adapter():
    """A cached yfinance adapter for the single-name fetch (free — no keys, no LLM)."""
    from datetime import date as _date

    from aristos_council.data.cache import DEFAULT_CACHE_DIR, CachingAdapter
    from aristos_council.data.provider import select_market_adapter
    return CachingAdapter(select_market_adapter(), cache_dir=DEFAULT_CACHE_DIR,
                          today=_date.today())



def _render_price_and_cash(result) -> None:
    """COMPANY-FACTS-TABLE-1 — price, cash and forward-valuation facts the council's own
    evidence pack already fetched that this page never showed. No comparison group, no
    vote — the same category as Absolute readings, rendered the same plain way."""
    pac = getattr(result, "price_and_cash", None)
    st.subheader("Price and cash")
    if pac is None:
        st.caption("not requested")
        return
    st.caption("No comparison group. These are facts about this company's own price and "
               "cash flow — they are not lenses, they do not vote, and nothing here is ranked.")
    lines = pac.lines()
    if not lines:
        st.write("none available")
        return
    for line in lines:
        st.markdown(f"- {line}")


def _render_absolute_readings(result, *, with_analyst: bool = True) -> None:
    """ABS-READINGS-1 — what the accounts say, with no comparison group involved.

    Beside the valuation band and the Forensic marks because they answer the same kind of
    question: not "how does this rank" but "what is this company like". They do not vote.
    """
    from aristos_council.company_check import mixed_source_marker

    debt, growth = result.debt_and_cash, result.growth_record
    trend = getattr(result, "analyst_trend", None) if with_analyst else None
    if debt is None and growth is None and trend is None:
        return
    st.subheader("Absolute readings")
    st.caption("No comparison group. These are facts about this company's own accounts — "
               "they are not lenses, they do not vote, and nothing here is ranked.")
    if debt is not None:
        st.markdown("**Debt and cash**")
        for line in debt.lines():
            st.markdown(f"- {line}")
    if growth is not None:
        st.markdown("**Growth record**" + mixed_source_marker(result, growth.source_tag))
        for line in growth.lines():
            st.markdown(f"- {line}")
        for note in growth.notes():                  # said once, under the section
            st.caption(note)
    if trend is not None:
        # ANALYST-TREND-1 - a mark, not a lens: it does not vote and changes no verdict. An
        # abstention is shown with its reason rather than left as an absent section.
        st.markdown("**What analysts say**" + mixed_source_marker(result, trend.source))
        _render_analyst_body(trend)


def _render_analyst_body(trend) -> None:
    """WHAT ANALYSTS SAY: the ratings (a line, a one-row table, the average target against today's
    price), then the forecasts as plain sentences - no table of forecasts, no 'EPS'."""
    import pandas as pd

    ratings = trend.ratings
    if ratings is not None and ratings.available:
        st.markdown(f"**{ratings.summary_line()}**")
        head, row = ratings.table()
        st.dataframe(pd.DataFrame([dict(zip(head, row))]), hide_index=True, width="stretch")
        for line in ratings.lines()[1:]:
            st.markdown(line)
    else:
        st.caption(ratings.lines()[0] if ratings is not None else
                   "Analyst ratings are not shown: no analyst data.")
    for sentence in trend.forecast_sentences():
        st.markdown(sentence)


def _render_analyst_forecasts(result) -> None:
    """The Company Report's own section, after the absolute readings."""
    from aristos_council.company_check import mixed_source_marker

    trend = getattr(result, "analyst_trend", None)
    st.subheader("What analysts say")
    if trend is None:
        st.caption("Not available.")
        return
    st.caption("A mark: it does not vote and changes no verdict."
               + mixed_source_marker(result, trend.source))
    _render_analyst_body(trend)


def _render_peers(result, columns=None, company_ticker: str = "") -> None:
    """MARKET-INDEX-1 — who this company would be measured against. With ``columns`` (the Company
    Report's per-lens ranks, read from the run's saved ranks) the company is the first row and there
    is one sortable rank column per lens."""
    from aristos_council.peer_table import (LOCAL_COLUMN, LOCAL_FORMAT, ONE_SYSTEM_NOTE,
                                            THIS_COMPANY_STYLE, USD_COLUMN, USD_FORMAT,
                                            has_one_system_peers,
                                            peer_frame_records, peer_rows)

    st.subheader("Peers")
    group = getattr(result, "peer_group", None)
    if group is None:
        st.info("The market index is not available — "
                "`python -m aristos_council.market_index build`")
        if getattr(result, "peer_error", ""):
            st.caption(f"({result.peer_error})")
        return

    if not group.available:
        st.info("No peer group for this name.")
        for reason in group.reasons:
            st.caption(f"· {reason}")
        return

    st.caption(group.sentence())
    import pandas as pd

    from aristos_council.peer_table import rank_display
    # Numbers stay numbers (sortable by size), largest USD cap first. The local column is in the
    # MAJOR unit: a London cap is pounds although its quote code says GBX (INDEX-GBX-SCALE-1).
    columns = list(columns or ())
    frame = pd.DataFrame(peer_frame_records(group, columns, company_ticker))
    data = frame
    rank_headers = [c.header for c in columns if c.kind == "rank"]
    # TAB-MERGE-1 part 2 commit 2: row 0 is reliably the company whenever columns+
    # company_ticker are both given (peer_table.peer_rows's own contract) — a VISIBLE
    # highlight on it, not just the "(this company)" text marker.
    highlight_company = bool(columns and company_ticker and not frame.empty)
    if rank_headers or highlight_company:
        data = frame.style
        if rank_headers:
            # A rank column stays NUMERIC (it sorts by rank); the words - "does not
            # apply", "no data" - are only how a cell that has no number reads.
            data = data.format({h: (lambda v: rank_display(v)) for h in rank_headers},
                               na_rep="does not apply")
        if highlight_company:
            data = data.apply(
                lambda row: ([THIS_COMPANY_STYLE] * len(row)) if row.name == 0
                else [""] * len(row), axis=1)
    st.dataframe(
        data, hide_index=True, width="stretch",
        column_config={
            USD_COLUMN: st.column_config.NumberColumn(USD_COLUMN, format=USD_FORMAT),
            LOCAL_COLUMN: st.column_config.NumberColumn(LOCAL_COLUMN, format=LOCAL_FORMAT),
        })
    if has_one_system_peers(peer_rows(group)):
        st.caption(ONE_SYSTEM_NOTE)
    for reason in group.reasons:
        st.caption(f"· {reason}")


def _render_company_report(report) -> None:
    """The Company Report page, in the ONE order every surface uses (TAB-MERGE-1 part 2
    commit 2): summary (if asked for) → agreement headline and table → each lens's vote
    → valuation band → price and cash → absolute readings → analyst forecasts → council
    opinion (if asked for) → the full peers table → Sources. The text and HTML exports
    follow the same order."""
    import pandas as pd

    from aristos_council.company_report import (HOUSE_LINE, NO_LENS_REASON,
                                                OUTSIDE_TESTED_RANGE_LINE,
                                                format_company_report)
    from aristos_council.export.report_html import company_report_html

    check = report.check
    st.markdown(f"### Company Report — {report.display}")
    st.caption(HOUSE_LINE)

    # TAB-MERGE-1 part 2 commit 4 — click-through from a list result. Shown ONLY when
    # THIS exact ticker was just opened that way (staleness-checked by ticker match,
    # same pattern as cc_matched_cap — a hand-typed ticker never sees a stale line from
    # an earlier click-through). The company page ranks against its OWN industry
    # peers, never against the list it was opened from; both facts are stated together.
    from_list = st.session_state.get("cc_from_list")
    if from_list is not None and from_list[0] == report.ticker:
        group = getattr(check, "peer_group", None)
        if group is not None and group.available:
            st.caption(f"Ranked against its {len(group.members)} industry peers, not "
                      "against your list.")
        for line in from_list[1]:
            st.caption(line)

    # SMALLCAP-VIEW-1 — the header caveat, exactly the line every lens's own vote also
    # carries below. Never shown for a company at or above the $5bn gate.
    if report.outside_tested_range:
        floor = (f"${report.smallcap_floor_usd / 1e9:g}bn" if report.smallcap_floor_usd
                else "its own floor")
        st.warning(f"**{OUTSIDE_TESTED_RANGE_LINE}**" + (
            f" Small-company peer band: the {report.smallcap_cohort} cohort, {floor}-$5bn."
            if report.smallcap_cohort else "")
            + (f" {report.smallcap_band_note.capitalize()}." if report.smallcap_band_note else ""))
    if report.unrateable:
        st.warning(f"⚪ **UNRATEABLE** — {check.data_integrity.note}. No data, so no votes and no "
                   "readings.")
        return

    if report.summary is not None:                       # only when it was ticked
        from aristos_council.reader import (READER_SECTION_NOTE, READER_SECTION_TITLE,
                                            reader_paragraphs)
        st.subheader(READER_SECTION_TITLE)
        if report.summary.available:
            for lead, text in reader_paragraphs(report.summary.summary):
                st.markdown(f"**{lead}** {text}")
            st.caption(READER_SECTION_NOTE)
        else:
            st.info(report.summary.note)

    st.subheader("Agreement")
    if report.agreement is not None:
        st.markdown(f"**{report.agreement.headline}**")
        st.dataframe(pd.DataFrame([report.agreement.table_row(report.display)]),
                     hide_index=True, width="stretch")
        # BACKTEST-2 — display only, right under the agreement count; no vote, rank or verdict
        # above is affected by anything here.
        if report.track_record_caption:
            st.caption(report.track_record_caption)
        if report.track_record_summary:
            st.caption(report.track_record_summary)
    else:
        st.info(f"No vote: {report.no_vote_reason}")

    st.subheader("Lens votes")
    if report.votes:
        # SMALLCAP-VIEW-1 — every lens's own verdict carries the caveat on a small-company-
        # band run; never shown otherwise.
        _suffix = f" — {OUTSIDE_TESTED_RANGE_LINE}" if report.outside_tested_range else ""
        st.dataframe(pd.DataFrame([{"Lens": v.label, "Role": v.role,
                                    "Result": v.result() + v.badge_suffix + _suffix,
                                    "What it asks": v.asks} for v in report.votes]),
                     hide_index=True, width="stretch")
        badged = [v for v in report.votes if v.badge is not None]
        if badged:
            from aristos_council.backtest import BADGE_MEANINGS
            with st.expander("Track record — what each badge means"):
                for v in badged:
                    st.markdown(f"**{v.label} ({v.badge.label})**")
                    st.caption(f"{v.badge.detail_line()} — {BADGE_MEANINGS[v.badge.label]}")
    else:
        st.info(report.no_vote_reason or NO_LENS_REASON)

    st.subheader("Valuation band")
    st.caption("This company against its own history; a mark, never a veto.")
    st.write(check.valuation_band)

    _render_price_and_cash(check)
    _render_absolute_readings(check, with_analyst=False)
    _render_analyst_forecasts(check)

    if report.council_opinion is not None:                # only when it was ticked
        st.subheader("Council opinion")
        st.caption("Narration only — never a vote; the agreement above is the verdict of "
                   "record.")
        op = report.council_opinion
        if op.available:
            # HTML-NARR-MD-1 — rendered through the SAME HTML builder the export uses
            # (tables as tables, bold/italic as bold/italic, a blockquote as a callout),
            # not st.markdown's own parser: a structural-warning banner's "> **…**"
            # lines and the narrator's own GFM table were reaching the screen as raw
            # markdown text (literal "&gt;", "**", "|") rather than rendering.
            from aristos_council.export.report_html import _narration_html

            html_block = _narration_html(op.narrative) if op.narrative \
                else "<p><em>(no narrative produced)</em></p>"
            st.markdown(f'<div class="council-narrative">{html_block}</div>',
                       unsafe_allow_html=True)
        else:
            st.info(op.note)

    from aristos_council.peer_table import rank_columns
    _render_peers(check, rank_columns(report), report.ticker)

    _render_sources(check)
    # BACKTEST-2 — the page footer, so a badge is never on screen without a way to read how it
    # was earned.
    st.caption("How lenses are graded: docs/BACKTEST.md")

    # Two exports side by side (REPORT-HTML-1): the text is canonical, the HTML the shareable copy.
    from aristos_council.download_names import (company_check_download_name,
                                                company_check_html_download_name)

    run_start = st.session_state.get("cc_run_start") or datetime.now(timezone.utc)
    txt_name = company_check_download_name(report.ticker, "company_report", run_start)
    html_name = company_check_html_download_name(report.ticker, "company_report", run_start)
    col_txt, col_html = st.columns(2)
    with col_txt:
        st.download_button(f"⬇ Download report as text — {txt_name}",
                           data=format_company_report(report), file_name=txt_name,
                           mime="text/plain", key="cc_report_download")
    with col_html:
        st.download_button(f"⬇ Download report (HTML) — {html_name}",
                           data=company_report_html(report, run_start=run_start),
                           file_name=html_name, mime="text/html", key="cc_report_download_html")
    tail = f"Ran in {report.seconds:.1f}s"
    if report.cache.get("hits") is not None:
        tail += f"; day-cache {report.cache['hits']} hits, {report.cache['misses']} fetched"
    if report.saved_to:
        tail += f"; saved under `{report.saved_to}`"
    st.caption(tail)


def _render_sources(result) -> None:
    """ONE Sources block at the bottom (batch 8): every provider the page drew on with its as-of
    date, and the correction files used. Nothing above it names a provider."""
    from aristos_council.company_check import company_sources

    sources = company_sources(result)
    if not sources:
        return
    st.subheader("Sources")
    for s in sources:
        st.markdown(f"- **{s.topic}:** {s.text}")


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# ASSET-MODE-1 — the switch, read wherever a picker is built
# --------------------------------------------------------------------------- #
def asset_mode() -> str:
    """The current Stocks / ETFs mode. Stocks whenever nothing has been chosen — on a
    fresh start, after a refresh, and in every test that never touches the switch."""
    return st.session_state.get("asset_mode") or DEFAULT_ASSET_MODE


@st.cache_data(show_spinner=False)
def _known_etf_tickers() -> frozenset:
    """Every ticker this repo already knows to be a fund, for classifying an unmarked
    list. Two sources, both of which exist for other reasons:

    * the ETF static layer (``data/etf_static.csv``) — human-verified fund rows;
    * the shipped ETF universes — a list whose thesis is ``funds`` is a list of funds.

    Not a lookup that can fail a run: a ticker missing from both is simply not known to
    be a fund, and an unmarked list containing one falls to the stocks default."""
    tickers: set = set()
    try:
        from aristos_council.etf_static import default_static_rows
        tickers.update(t.upper() for t in default_static_rows())
    except Exception:                                  # a missing/garbled CSV is not fatal
        pass
    try:
        from aristos_council.universe import list_universes

        for manifest in list_universes(UNIVERSES_DIR):
            if (getattr(manifest, "thesis", "") or "").strip().lower() == "funds":
                tickers.update(t.upper() for t in (manifest.tickers or []))
    except Exception:
        pass
    return frozenset(tickers)


def _mode_asset_kind() -> str:
    """The current mode as a Universe ``asset_kind`` value — "stocks" or "etfs"."""
    from aristos_council.demo_surface import ASSET_KINDS

    return "etfs" if asset_mode() == ETFS else ASSET_KINDS[0]


def _mode_filters():
    """``(lens_ok, list_ok)`` for the current mode — the ONE pair every picker applies."""
    return asset_mode_filter(asset_mode(), etf_tickers=_known_etf_tickers())


def visible_for_mode(strategies=None, universes=None):
    """Filter either collection by the current mode. Returns whichever was passed."""
    lens_ok, list_ok = _mode_filters()
    if strategies is not None:
        return [s for s in strategies if lens_ok(s)]
    return [u for u in (universes or []) if list_ok(u)]


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
    install_dollar_safety(st)       # DOLLAR-MATH-1: every markdown-rendering call is $-safe

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
        # ASSET-MODE-1 — the one control that decides what every picker offers. In the
        # SIDEBAR, not in a tab, because it governs the Run tab, Company Check and every
        # other surface that starts new analysis, and a switch that lived in one of them
        # would be invisible from the others.
        #
        # SESSION ONLY, and deliberately. Nothing is written to a settings file, a query
        # param or local storage, so the app opens on Stocks every time — which is what
        # "a stock-analysis tool by default" means. A persisted ETFs choice would make
        # the majority job the one you have to remember to switch back to.
        # TAB-MERGE-1 part 2 commit 1: renamed from "Analyse" — the merged tab (app.py's
        # main Analyse tab) now carries that name, and this switch must not share it.
        # Options and default (Stocks, index=0) unchanged.
        st.radio("Asset type", list(ASSET_MODES), horizontal=True, index=0,
                 key="asset_mode")
        st.caption("ETF lists and lenses are hidden while Stocks is selected.")
        st.divider()

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
        # v2-ONLY landing: Analyse + Scoreboard (first-class, not legacy). Validation
        # assets hidden (show_validation=False). TAB-MERGE-1 commit 3: Company Check and
        # the old Run tab are now ONE tab, "Analyse" — picking Company or Cohort / list is
        # an explicit switch inside it, not a choice of tab.
        tab_analyse, tab_scoreboard = st.tabs(["Analyse", "Scoreboard"])
        with tab_analyse:
            render_run_tab(show_validation=False)
        with tab_scoreboard:
            render_scoreboard_tab()
        return

    # Legacy ON: Analyse FIRST (Streamlit default-selects it), Scoreboard next
    # (first-class), then the pre-v2 council browsers (Legacy), the YAML editor last. The
    # toggle is ON here, so validation assets are revealed (incl. the "Second opinion"
    # run mode, TAB-MERGE-1 commit 3).
    tab_analyse, tab_scoreboard, tab_report, tab_history, tab_strategy = \
        st.tabs(["Analyse", "Scoreboard", "Report · Legacy",
                 "History · Legacy", "Strategy · Legacy"])

    with tab_analyse:
        render_run_tab(show_validation=True)

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
