"""COMPANY-STORY-1 - the Company Report as Markdown, in the same order as the text, the HTML and the
page: the answer, the story (or the model's summary), one lens table, the council opinion (only when
ticked), then "Show the workings" as sub-headings at the end. The "what it asks" captions become
footnotes. One ``StoryPage`` feeds every renderer, so this cannot drift from the others.
"""
from __future__ import annotations

from .company_story import (SECTION_ANSWER, SECTION_STORY, SECTION_TABLE, SECTION_WORKINGS,
                            narration_check_line, story_page, table_rows)


def _cell(text: str) -> str:
    return str(text).replace("|", "/").replace("\n", " ")


def _bullets(lines) -> list[str]:
    out = []
    for raw in lines:
        text = str(raw).rstrip()
        if not text.strip():
            continue
        indent = len(text) - len(text.lstrip(" "))
        body = text.strip()
        if body.startswith("- "):
            body = body[2:]
            out.append(f"{'  ' * (1 if indent > 2 else 0)}- {body}")
        else:
            out.append(f"- {body}")
    return out


def company_report_markdown(report) -> str:
    """The Markdown export of ONE company report."""
    from .company_check import analyst_forecast_lines, absolute_reading_lines, company_sources
    from .company_report import HOUSE_LINE, price_and_cash_lines
    from .peer_table import rank_columns

    c = report.check
    out = [f"# Company Report — {report.display}", "", f"*{HOUSE_LINE}*", ""]
    if report.unrateable:
        out += [f"**UNRATEABLE** — {c.data_integrity.note}. No data, so no votes and no readings.",
                "", c.pointer]
        return "\n".join(out) + "\n"

    page = story_page(report)
    out += [f"## {SECTION_ANSWER}", "", f"**{page.answer[0]}**", "", page.answer[1], ""]

    from .reader import READER_SECTION_NOTE, READER_SECTION_TITLE
    out += [f"## {READER_SECTION_TITLE if page.model_summary else SECTION_STORY}", ""]
    for lead, text in page.paragraphs:
        out += [f"**{lead}** {text}", ""]
    if page.model_summary:
        out += [f"*{READER_SECTION_NOTE}*", ""]
    if page.note:
        out += [f"*{page.note}*", ""]

    out += [f"## {SECTION_TABLE}", ""]
    for line in page.tag:
        out += [f"*{line}*", ""]
    notes = []
    if page.no_vote:
        out += [page.no_vote, ""]
    else:
        out += ["| " + " | ".join(page.headers) + " |", "|" + "---|" * len(page.headers)]
        for r in page.rows:
            lens = _cell(r.lens)
            if r.asks:
                notes.append((len(notes) + 1, r))
                lens += f"[^{len(notes)}]"
            out.append("| " + " | ".join([lens, _cell(r.outcome), _cell(r.badge),
                                          _cell(r.reason)]) + " |")
        out.append("")
    if page.caption:
        out += [f"*{page.caption}*", ""]
    if page.group_note:
        out += [f"*{page.group_note}*", ""]

    if report.council_opinion is not None:
        op = report.council_opinion
        out += [f"## {'Council opinion'}", "",
                "*Narration only — never a vote; the agreement above is the verdict of record.*", ""]
        out += [(op.narrative or "(no narrative produced)") if op.available else op.note, ""]

    out += [f"## {SECTION_WORKINGS}", "", "### Valuation band", "",
            "*This company against its own history; a mark, never a veto.*", "", c.valuation_band, ""]
    out += ["### Price and cash", "", *_bullets(price_and_cash_lines(c)), ""]
    body = absolute_reading_lines(c)
    out += ["### Absolute readings", "", *(_bullets(body) if body else ["none available"]), ""]
    forecasts = analyst_forecast_lines(c)
    out += ["### What analysts say", "", *(_bullets(forecasts[1:]) if forecasts else ["not available"]), ""]
    from .company_check import peers_lines
    peer_block = peers_lines(c, columns=rank_columns(report), company_ticker=report.ticker)
    out += ["### Peers", "", "```", *(peer_block or ["none computed"]), "```", ""]
    line = narration_check_line(report)
    if line:
        out += ["### Narration check", "", line, ""]
    extra = [r for r in table_rows(report) if r.full_reason or r.badge_detail]
    if extra:
        out += ["### Lens notes", ""]
        for r in extra:
            if r.full_reason:
                out.append(f"- **{r.lens}** did not apply: {r.full_reason}")
            if r.badge_detail:
                out.append(f"- **{r.lens}** track record: {r.badge_detail}")
        out.append("")
    out += ["### Sources", "", *[f"- **{s.topic}:** {s.text}" for s in company_sources(c)], ""]
    for n, r in notes:
        out.append(f"[^{n}]: {r.lens} asks: {r.asks}")
    return "\n".join(out).rstrip() + "\n"
