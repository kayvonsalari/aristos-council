"""Shared presentation helpers — PURE (no Streamlit, no PDF deps).

Both Council Station (screen) and the PDF exporter must clean prose and shape the
screen-results table identically, so that logic lives here. Display-only: none
of this is ever applied to stored data (reports keep call_ids for auditability).
"""

from __future__ import annotations

import re

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
