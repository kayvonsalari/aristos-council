"""The gate section of the company page, in one place (ABSOLUTE-GATE-1).

The text download, the Markdown download, the HTML download and the Streamlit page all read the SAME
rows from here, so the four cannot drift. Plain English: no lens ids, no column names. A reader who
ignores the section loses nothing, and the title says so.
"""
from __future__ import annotations

from .engine import (EXCLUDED, FAIL, NOT_APPLICABLE, NOT_COVERED, NOT_ENOUGH_DATA, NOT_EVALUATED,
                     PASS, QUALIFIES, WARN)

SECTION_TITLE = "Gate (ABSOLUTE-1 phase 1, does not change the verdict yet)"
CAPTION = ("Four checks that can exclude a company, and four warnings that never do. Only accounting "
           "and solvency failures exclude. This section changes no vote and no verdict.")
HEADERS = ("Check", "Kind", "Reading", "Limit", "Result")

_STATE_WORDS = {EXCLUDED: "Excluded", QUALIFIES: "Qualifies", NOT_ENOUGH_DATA: "Not enough data",
                NOT_COVERED: "Not covered"}


def _result_word(result: str, *, warning: bool) -> str:
    if result == PASS:
        return "passes"
    if result == WARN:
        return "warning"
    if result == FAIL:
        return "excluded"
    if result == NOT_EVALUATED:
        return "not evaluated"
    if result == NOT_APPLICABLE:
        return "does not apply"
    return result


def rows(gate) -> list[list[str]]:
    """One row per check: the four exclusions, then the two warnings."""
    out = []
    for kind, group in (("exclusion", gate.rules), ("warning", gate.warnings)):
        for r in group:
            result = _result_word(r.result, warning=(kind == "warning"))
            # a note explains a warning, an abstention or a non-applicable rule, and the distress grey
            # zone; a plain pass carries no method note
            if r.note and (r.result != PASS or r.key == "distress"):
                result = f"{result} ({r.note})"
            out.append([r.label, kind, r.reading, r.limit, result])
    return out


def state_line(gate) -> str:
    """"Gate result: Qualifies: no exclusion rule failed. No warnings." - the reason already opens with
    the state word, so it is not said twice."""
    return f"Gate result: {gate.reason}"


def has_rows(gate) -> bool:
    return bool(gate is not None and (gate.rules or gate.warnings))


def table_markdown(gate) -> list[str]:
    """Just the markdown table (the Streamlit page prints the title and notes itself)."""
    if not has_rows(gate):
        return []
    return ((["| " + " | ".join(HEADERS) + " |", "|" + "---|" * len(HEADERS)])
            + ["| " + " | ".join(c.replace("|", "/") for c in r) + " |" for r in rows(gate)])


def markdown_text(gate) -> str:
    """The markdown table as one string, for ``st.markdown``."""
    return chr(10).join(table_markdown(gate))


def text_lines(gate) -> list[str]:
    """The text download: title, caption, a plain table, the state line, the debt definition."""
    out = [SECTION_TITLE.upper(), CAPTION]
    if has_rows(gate):
        table = [list(HEADERS)] + rows(gate)
        widths = [max(len(str(r[i])) for r in table) for i in range(len(HEADERS))]
        for n, r in enumerate(table):
            out.append("  " + "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)).rstrip())
            if n == 0:
                out.append("  " + "  ".join("-" * w for w in widths))
    out.append(state_line(gate))
    if gate.debt_line and has_rows(gate):
        out.append(gate.debt_line)
    return out


def markdown_lines(gate) -> list[str]:
    out = [f"## {SECTION_TITLE}", "", f"*{CAPTION}*", ""]
    if has_rows(gate):
        out += ["| " + " | ".join(HEADERS) + " |", "|" + "---|" * len(HEADERS)]
        out += ["| " + " | ".join(c.replace("|", "/") for c in r) + " |" for r in rows(gate)]
        out.append("")
    out += [f"**{state_line(gate)}**", ""]
    if gate.debt_line and has_rows(gate):
        out += [f"*{gate.debt_line}*", ""]
    return out


def html_section(gate, esc, table_html) -> str:
    """``esc`` is the exporter's escaper and ``table_html(headers, rows)`` its table builder, so this
    module imports nothing from the export package."""
    out = [f'<section class="section" id="gate"><h2>{esc(SECTION_TITLE)}</h2>',
           f'<p class="note">{esc(CAPTION)}</p>']
    if has_rows(gate):
        out.append(table_html(list(HEADERS), [[esc(c) for c in r] for r in rows(gate)]))
    out.append(f"<p><strong>{esc(state_line(gate))}</strong></p>")
    if gate.debt_line and has_rows(gate):
        out.append(f'<p class="note">{esc(gate.debt_line)}</p>')
    return "".join(out) + "</section>"
