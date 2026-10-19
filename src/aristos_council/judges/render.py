"""JUDGE-PANEL-1 - the panel as rows and lines, ONE source for the page, the text, the Markdown and
the HTML so the four cannot drift (the same rule the company page's story follows)."""
from __future__ import annotations

from .panel import (INCOMPLETE, NOT_RUN, STABLE, UNSTABLE, PanelResult, needs_reading)

SECTION_TITLE = "Judge panel"
CHECKBOX_LABEL = "Judge panel (blind, no vote)"
CAPTION = ("Independent models were each shown only this company's raw facts - no lens, rank, "
           "badge or verdict - and asked twice. They have no vote and change nothing above: the "
           "lenses stay the verdict of record. This is a record kept to see, in a year, whether "
           "model judgement adds anything.")
COLUMNS = ("Judge", "Verdict", "Confidence", "Reason", "Would change its mind if")
READING_CAPTION = "A reading of the difference, not a recommendation."

HELP = ("Three independent models (Claude, OpenAI, Gemini), each shown ONLY this company's raw facts "
        "- no lens, rank, badge or verdict - and asked the same question twice. They never vote and "
        "change nothing above; every answer is logged so that in a year we can see whether model "
        "judgement adds anything. A judge with no key is skipped. Off unless you tick it.")


def checkbox_caption(config, environ=None) -> str:
    """The expected call count, shown beside the box."""
    runnable = config.runnable(environ)
    calls = config.expected_calls(environ)
    if not runnable:
        return "No judge has a key set, so nothing would run (OPENAI_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY)."
    missing = [j.label for j in config.judges if j not in runnable]
    tail = f" · {', '.join(missing)}: no key, skipped" if missing else ""
    return (f"About {calls} model calls ({len(runnable)} judge{'s' if len(runnable) != 1 else ''} "
            f"x {config.calls_per_judge}, plus one short paragraph if the panel differs){tail}.")


def _per_call(j, attr: str) -> str:
    return " / ".join(getattr(c, attr) for c in j.calls if c.status == "ok" and getattr(c, attr))


def rows(result: PanelResult) -> list[dict]:
    out = []
    for j in result.judges:
        row = {"Judge": f"{j.label} ({j.model})", "Verdict": j.verdict_text(),
               "Confidence": "", "Reason": "", "Would change its mind if": ""}
        if j.status == STABLE:
            c = j.first_ok
            row.update({"Confidence": c.confidence, "Reason": c.reason,
                        "Would change its mind if": c.would_change_my_mind})
        elif j.status in (UNSTABLE, INCOMPLETE):
            ok = [c for c in j.calls if c.status == "ok"]
            row.update({"Confidence": " / ".join(c.confidence for c in ok),
                        "Reason": " | ".join(f"{c.verdict}: {c.reason}" for c in ok),
                        "Would change its mind if": " | ".join(f"{c.verdict}: {c.would_change_my_mind}" for c in ok)})
        out.append(row)
    return out


def summary_lines(result: PanelResult) -> list[str]:
    """The split line and the agreement line - two lines, no more."""
    return [f"Split: {result.split_line}.", result.agreement_line]


def reading_text(result: PanelResult) -> tuple[str, str]:
    """``(paragraph, note)``: the narrator's paragraph, or why there is none when one was due."""
    rd = result.reading
    if rd is None:
        return "", ""
    return rd.paragraph, ("" if rd.paragraph or not needs_reading(result.agreement) else rd.note)


def text_lines(result: PanelResult) -> list[str]:
    """Plain-text block for the .txt download (after the council opinion)."""
    if not result.available:
        return [f"  Not run: {result.note}"]
    out = []
    for r in rows(result):
        out.append(f"  {r['Judge']}: {r['Verdict']}"
                   + (f" (confidence {r['Confidence']})" if r["Confidence"] else ""))
        if r["Reason"]:
            out.append(f"      reason: {r['Reason']}")
        if r["Would change its mind if"]:
            out.append(f"      would change its mind if: {r['Would change its mind if']}")
    out += ["  " + ln for ln in summary_lines(result)]
    paragraph, note = reading_text(result)
    if paragraph:
        out += ["", "  " + paragraph, f"  ({READING_CAPTION})"]
    elif note:
        out += ["", "  " + note]
    return out


def markdown_lines(result: PanelResult) -> list[str]:
    out = [f"## {SECTION_TITLE}", "", f"_{CAPTION}_", ""]
    if not result.available:
        return out + [f"Not run: {result.note}", ""]
    out.append("| " + " | ".join(COLUMNS) + " |")
    out.append("|" + "|".join("---" for _ in COLUMNS) + "|")
    for r in rows(result):
        out.append("| " + " | ".join(str(r[c]).replace("|", "/").replace("\n", " ") for c in COLUMNS) + " |")
    out += ["", *summary_lines(result), ""]
    paragraph, note = reading_text(result)
    if paragraph:
        out += [paragraph, "", f"_{READING_CAPTION}_", ""]
    elif note:
        out += [f"_{note}_", ""]
    return out
