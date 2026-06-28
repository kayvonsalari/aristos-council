"""Shared presentation helpers — PURE (no Streamlit, no PDF deps).

Both Council Station (screen) and the PDF exporter must clean prose and shape the
screen-results table identically, so that logic lives here. Display-only: none
of this is ever applied to stored data (reports keep call_ids for auditability).
"""

from __future__ import annotations

import re

from .state import FailureKind, ResearchState, RunIssue

# Provenance plumbing in PROSE: agents inline citations like
# "(call_id: 8d39404e0e90, criteria[0].observed)" / "[call_id 1db8...]" and bare
# field-path refs like "`criteria[0].passed = false`". Display strips them by
# default; the provenance toggle (UI) / nothing (PDF is a clean record) keeps the
# raw text. Leading trims use [ \t] (NOT \s) so newlines — and therefore the
# markdown structure (headers, lists, tables) — always survive.
_CALLID_PAREN_RE = re.compile(
    r"[ \t]*\((?:[^()]|\([^()]*\))*call_id\b(?:[^()]|\([^()]*\))*\)"
)
_CALLID_BRACKET_RE = re.compile(r"[ \t]*\[[^\]]*call_id\b[^\]]*\]")
# BARE call_id citations with no surrounding parens/brackets — the "Key Figures
# (Provenance)" list format: "... observed: 0.1242 — call_id df243ae60ec6,
# criteria[0].observed". Strip the (optional) leading separator (— : ; , -),
# "call_id", the id token, and an optional trailing ", <field_path>".
_CALLID_BARE_RE = re.compile(
    r"[ \t]*(?:[—–:;,\-][ \t]*)?"
    r"call_id\b\s*:?\s*\w+(?:\s*,\s*[\w\[\].%+\-]+)?"
)
# A field path: name[idx](.field)* with an optional '= <single token>' value
# (token only, so prose after the value is never swallowed), optional backticks.
_FIELDPATH_RE = re.compile(
    r"`?\b[A-Za-z_]\w*\[-?\d+\](?:\.\w+)*(?:\s*=\s*[\w.%+-]+)?`?"
)


def strip_provenance(text: str) -> str:
    """Remove inline call_id / field-path citations from prose for clean display.

    Line structure (newlines) is always preserved so markdown survives."""
    if not text:
        return text
    out = _CALLID_PAREN_RE.sub("", text)
    out = _CALLID_BRACKET_RE.sub("", out)
    out = _CALLID_BARE_RE.sub("", out)
    out = _FIELDPATH_RE.sub("", out)
    # Clean up artifacts left by the removals, WITHOUT touching newlines.
    out = re.sub(r"`\s*`", "", out)                  # empty backticks
    out = re.sub(r"\([ \t]*\)", "", out)             # empty parens
    out = re.sub(r":[ \t]*,", ":", out)              # "X: ," -> "X:"
    out = re.sub(r"[ \t]+,", ",", out)               # " ," -> ","
    out = re.sub(r",[ \t]*,", ",", out)              # ", ," -> ","
    out = re.sub(r"[ \t]{2,}", " ", out)             # collapse runs of spaces
    out = re.sub(r"[ \t]+([.,;:)])", r"\1", out)     # no space before punctuation
    out = re.sub(r"[ \t]+\n", "\n", out)             # trailing spaces before EOL
    return out.strip()


# Screen-results status: shared labels + semantic colors (pass/fail/not-eval).
SCREEN_STATUS = {True: "PASS", False: "FAIL", None: "NOT-EVAL"}
SCREEN_STATUS_HEX = {"PASS": "#2E7D32", "FAIL": "#B23B3B", "NOT-EVAL": "#B8860B"}


def screen_table_rows(screen: dict | None) -> list[dict]:
    """Map a structured screen result to display rows. Deterministic — the
    criteria are always a clean table regardless of LLM prose formatting."""
    rows = []
    for c in ((screen or {}).get("criteria") or []):
        rows.append({
            "Criterion": c.get("name"),
            "Observed": c.get("observed"),
            "Threshold": c.get("threshold"),
            "Status": SCREEN_STATUS.get(c.get("passed"), "NOT-EVAL"),
        })
    return rows


# --------------------------------------------------------------------------- #
# Prompt-view summaries (shared by the evidence block AND the provenance audit)
# --------------------------------------------------------------------------- #
# Some list-shaped tool outputs (dividend history, recommendation trends) used to
# reach agents as a bare list they had to INDEX — the source of the index/semantic
# and summed provenance violations (agents guessed output[164], summed by hand,
# wrote [last] / "by date …"). These builders turn the list into NAMED, citable
# handles. They are used in TWO places that must never diverge: the evidence block
# (what the agent sees) and the audit's prompt-view aliases (what a citation
# resolves against). One definition guarantees they agree.
def _event_view(ev) -> dict:
    """One dividend event as named fields (amount is the citable number)."""
    return {"ex_date": str(ev.ex_date), "amount": ev.amount}


def dividend_view(events) -> dict:
    """Named handles over a CHRONOLOGICAL-ASCENDING dividend-event list.

    yfinance dividends ascend, so the latest event is the last element.
    ``by_year`` maps each calendar year to that year's LATEST per-payment amount
    (the prevailing rate — ascending order makes the last write per year win),
    which is the figure agents reach for when they cite "the <year> level".
    """
    events = list(events or [])
    return {
        "n_events": len(events),
        "latest": _event_view(events[-1]) if events else None,
        "earliest": _event_view(events[0]) if events else None,
        "by_year": {str(ev.ex_date.year): ev.amount for ev in events},
    }


def recommendation_view(trends) -> dict:
    """Named handles over a RecommendationTrend list.

    ``latest_period`` is the entry with the max ISO period (order-independent) and
    exposes the per-category counts AND ``total`` — the aggregate agents otherwise
    summed by hand — as a real citable field.
    """
    trends = list(trends or [])
    latest = max(trends, key=lambda t: t.period) if trends else None
    return {
        "n_periods": len(trends),
        "latest_period": ({
            "period": latest.period,
            "strong_buy": latest.strong_buy,
            "buy": latest.buy,
            "hold": latest.hold,
            "sell": latest.sell,
            "strong_sell": latest.strong_sell,
            "total": latest.total,
        } if latest is not None else None),
    }


# --------------------------------------------------------------------------- #
# Run health / observability — make SILENT degradation LOUD
# --------------------------------------------------------------------------- #
# Screen ledger tool names (current + legacy). Duplicated, NOT imported from the
# agent layer, so this presentation module stays dependency-free of `nodes`.
_SCREEN_TOOLS = {"run_strategy_screen", "run_dividend_aristocrat_screen"}
# The sources we report a one-word status for, in display order.
_SOURCE_ORDER = ("prices", "fundamentals", "dividends", "sentiment")


def degraded_banner(run_issues: list[RunIssue]) -> str | None:
    """The LOUD top-of-report banner for a degraded run, or None when clean.

    Lists ONLY the FIXABLE tool failures (FETCH_ERROR / EMPTY_RESPONSE /
    MISSING_KEY). Honest DATA_ABSENT / CURRENCY_MISMATCH abstentions never appear
    here, so a clean run renders nothing and good runs stay uncluttered — the
    no-crying-wolf rule. Render this as the VERY FIRST thing in a report.
    """
    degrading = [i for i in (run_issues or []) if i.is_degrading]
    if not degrading:
        return None
    lines = ["⚠️ DEGRADED RUN — this verdict was produced with incomplete data:"]
    for i in degrading:
        detail = f" — {i.detail}" if i.detail else ""
        lines.append(f"   • {i.source}: {i.reason.value.upper()}{detail}")
    lines.append("Treat the recommendation with extra caution; the gaps above were "
                 "not available to the council.")
    return "\n".join(lines)


def _find_screen(state: ResearchState) -> dict | None:
    for tc in reversed(state.tool_calls):
        if tc.tool_name in _SCREEN_TOOLS and isinstance(tc.output, dict):
            return tc.output
    return None


def _source_status(run_issues: list[RunIssue]) -> dict[str, str]:
    """One status token per source: 'OK' unless an issue degraded it, else the
    issue's reason (e.g. 'MISSING_KEY'). Honest abstentions are not tool failures,
    so they show as the reason too but never set the run degraded (see `degraded`)."""
    status: dict[str, str] = {}
    for i in run_issues or []:
        # Last write wins; a degrading reason takes precedence over an honest one.
        if i.source not in status or i.is_degrading:
            status[i.source] = i.reason.value.upper()
    return status


def run_health_line(obj) -> str:
    """A one-glance trustworthiness line: criteria evaluated/abstained and per-source
    status. Rendered under the banner (or in place of it on a clean run).

    Duck-typed so it serves BOTH a live ``ResearchState`` (screen pulled from the
    tool-call ledger) and a stored ``RunReport`` (which carries ``screen`` directly)
    — one renderer for the CLI and Council Station alike.
    """
    screen = getattr(obj, "screen", None)
    if screen is None and hasattr(obj, "tool_calls"):
        screen = _find_screen(obj)
    criteria = (screen or {}).get("criteria") or []
    evaluated = sum(1 for c in criteria if c.get("passed") is not None)
    abstained = [c for c in criteria if c.get("passed") is None]
    run_issues = getattr(obj, "run_issues", []) or []
    degraded = bool(getattr(obj, "degraded", False))
    status = _source_status(run_issues)
    sources = " / ".join(
        f"{src} {status.get(src, 'OK')}" for src in _SOURCE_ORDER
        if src in status or src in ("prices", "fundamentals"))
    health = "DEGRADED" if degraded else "OK"
    line = (f"Run health: {health} — criteria evaluated {evaluated}, "
            f"abstained {len(abstained)}")
    if abstained:
        names = ", ".join(c.get("name", "?") for c in abstained)
        line += f" ({names})"
    if sources:
        line += f"; sources: {sources}"
    return line


def batch_health_summary(rows: list[dict]) -> str:
    """BATCH HEALTH summary for the screener: one line so a systematically broken
    batch (e.g. the no-FINNHUB-key run) SCREAMS at the end instead of hiding across
    rows. Each row is a dict with at least ``degraded`` (bool); optional ``verdict``
    (str) and ``reasons`` (list of FailureKind/str). Pure — no IO."""
    n = len(rows)
    degraded_rows = [r for r in rows if r.get("degraded")]
    clean = n - len(degraded_rows)

    def _norm(x) -> str:
        # A reason may be a FailureKind or a bare string ('fetch_error'); compare on
        # the enum VALUE either way (str(FailureKind.X) is the member name, not value).
        return x.value if isinstance(x, FailureKind) else str(x)

    def _has(reason: FailureKind) -> int:
        c = 0
        for r in degraded_rows:
            vals = [_norm(x) for x in (r.get("reasons") or [])]
            if any(reason.value in v for v in vals):
                c += 1
        return c

    insufficient = sum(
        1 for r in rows
        if str(r.get("verdict", "")).lower() == "insufficient_evidence")
    parts = [f"{n} names: {clean} clean, {len(degraded_rows)} degraded"]
    sentiment_missing = _has(FailureKind.MISSING_KEY)
    fetch_errors = _has(FailureKind.FETCH_ERROR) + _has(FailureKind.EMPTY_RESPONSE)
    if sentiment_missing:
        parts.append(f"{sentiment_missing} sentiment missing")
    parts.append(f"{fetch_errors} fetch errors")
    parts.append(f"{insufficient} INSUFFICIENT_EVIDENCE")
    return "BATCH HEALTH — " + ", ".join(parts)
