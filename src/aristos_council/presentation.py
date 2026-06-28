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


# --------------------------------------------------------------------------- #
# Contested-verdict flag — a ONE-RUN signal that a verdict is a close call
# --------------------------------------------------------------------------- #
# The REPORT is more trustworthy than the VERDICT: the verdict is a lossy
# compression of a richer, stable report, and the run-to-run wobble lives ONLY in
# that compression. Its fingerprint — panel disagreement — is present in EVERY
# single report (specialist_conflict / dissent / majority_override), so we can
# PREDICT instability from one run without re-running. This does NOT compute new
# analysis; it COMBINES signals the graph already produced.
#
# The confidence band only ESCALATES wording when a panel/dissent signal already
# fired — it NEVER marks a verdict contested on its own (empirically confidence is
# a weak predictor: stable mid-confidence names like MSFT 0.59 would be mislabelled).
_CONTESTED_CONF_BAND = (0.50, 0.65)


def _is_gated(decision) -> bool:
    """A GATED verdict is settled by deterministic CODE (SELL cap / INSUFFICIENT_
    EVIDENCE), not an LLM near-tie — so it is never 'contested'."""
    if decision is None:
        return False
    rec = getattr(decision, "recommendation", None)
    if rec is not None and getattr(rec, "value", None) == "insufficient_evidence":
        return True
    return bool(getattr(decision, "gate_override_applied", False)
                or getattr(decision, "insufficient_evidence", False))


def contested(obj, *, conf_band: tuple[float, float] = _CONTESTED_CONF_BAND
              ) -> tuple[bool, list[str]]:
    """Is this verdict a contested (near-tie) call? Returns (flag, reasons).

    Duck-typed over a live ResearchState OR a stored RunReport. Fires when the ONE
    report shows the markers of a near-tie:
      - ``panel_split``       — specialist_conflict veto (>=1 bullish AND >=1 bearish)
      - ``decision_dissent``  — the Decision overrode >=1 specialist (dissent non-empty)
      - ``majority_override`` — verdict contradicts the stance-majority
    These three are the PRIMARY triggers (panel disagreement, stable across runs).
    A confidence inside the contested band adds the supplementary
    ``contested_confidence`` reason ONLY when a primary already fired — confidence
    alone never marks contested. GATED outcomes are settled, never contested.
    """
    d = getattr(obj, "decision", None)
    if d is None or _is_gated(d):
        return False, []
    vetoes = {f.trigger.value for f in getattr(obj, "veto_flags", [])}
    reasons: list[str] = []
    if "specialist_conflict" in vetoes:
        reasons.append("panel_split")
    if getattr(d, "dissent", None):
        reasons.append("decision_dissent")
    if "majority_override" in vetoes:
        reasons.append("majority_override")
    primary = bool(reasons)
    conf = getattr(d, "confidence", None)
    if primary and conf is not None and conf_band[0] <= conf <= conf_band[1]:
        reasons.append("contested_confidence")
    return primary, reasons


def contested_banner(obj) -> str | None:
    """The short 'read the report / your call' line shown under a contested verdict,
    or None when the verdict is a clean/clear call. Names the concrete split so the
    label is explainable."""
    is_c, reasons = contested(obj)
    if not is_c:
        return None
    ops = getattr(obj, "specialist_opinions", []) or []
    bull = sum(1 for o in ops if getattr(o.stance, "value", o.stance) == "bullish")
    bear = sum(1 for o in ops if getattr(o.stance, "value", o.stance) == "bearish")
    neutral = sum(1 for o in ops if getattr(o.stance, "value", o.stance) == "neutral")
    d = getattr(obj, "decision", None)
    dissent_n = len(getattr(d, "dissent", []) or []) if d else 0

    bits: list[str] = []
    if "panel_split" in reasons:
        split = f"{bull} bullish / {bear} bearish"
        if neutral:
            split += f" / {neutral} neutral"
        bits.append(f"the panel was split ({split})")
    if "decision_dissent" in reasons and dissent_n:
        bits.append(f"the Decision overrode {dissent_n} "
                    f"specialist{'s' if dissent_n != 1 else ''}")
    if "majority_override" in reasons:
        bits.append("the verdict contradicts the specialist majority")
    detail = "; ".join(bits) if bits else "the panel showed disagreement"

    line = (f"CONTESTED CALL — {detail}. Treat this as a LEAD, not a conclusion: "
            f"read the specialist and critic sections and apply your own judgement. "
            f"A re-run may land differently.")
    if "contested_confidence" in reasons:
        line += " Confidence sits in the contested band, reinforcing this."
    return line


def contested_label(obj) -> str:
    """Inline screener tag, e.g. ``[CONTESTED: panel_split, decision_dissent]`` —
    or ``""`` for a clean verdict. So a row reads ``META  BUY  0.62  [CONTESTED: …]``
    and the user can separate clean picks from ones needing their own analysis."""
    is_c, reasons = contested(obj)
    if not is_c:
        return ""
    return f"[CONTESTED: {', '.join(reasons)}]"
