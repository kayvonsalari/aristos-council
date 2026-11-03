"""The price-and-return section of the company page, in one place (ABSOLUTE-VALUE-1).

The Streamlit page, the text download, the Markdown download and the HTML download all read the SAME
blocks from here, so the four cannot drift. Plain English: no strategy or column names, and never the
words "expected return", "target" or "proven". A reader who ignores the section loses nothing, and the
title says it becomes the verdict only in the next phase.
"""
from __future__ import annotations

from ..tools.price_context import format_money
from .engine import (BELOW_HURDLE, CLEARS_HURDLE, EXCLUDED, HIGH_GROWTH_NOTE, NOT_COVERED, NOT_ENOUGH_DATA,
                     AbsoluteValue)

SECTION_TITLE = "Price test and return (ABSOLUTE-1 phase 2; becomes the verdict in phase 3)"
CAPTION = ("What today's price asks of the business, and what a buyer would earn each year if the stated "
           "growth holds. This section changes no vote and no verdict yet.")
RETURN_LABEL = "Long-run owner's return if the growth holds"
NOT_A_FORECAST = ("These are what the price returns if the stated growth repeats. They are not forecasts. "
                  "Past growth may not repeat. Forecasts can be wrong. The model's narration cannot change them.")
EVIDENCE = ("This figure shows what today's price requires. Studies of company-level implied returns find "
            "they rank future returns only partly: where 1 would be perfect, the weakest model scores about "
            "0.2 and the better ones 0.5 to 0.65, and four of five measures do no better than a fixed "
            "constant in the cross section. So the figure is a question put to the reader and not an answer. "
            "The gate and the cash yield carry the evidence; this figure carries the question.")
OUTSIDE_RANGE_LINE = ("Outside the tested range: this company is smaller than the range the checks were "
                      "tested on. It is shown, never blocked.")
NOT_COVERED_LINE = ("Not covered: banks and insurers are outside this path. No return figure and no verdict "
                    "is shown here; the price-to-book and return-on-equity readings are the context for them.")
HEADERS_BASE = ("Year", "Free cash flow per share")
HEADERS_RECORDS = ("Record", "Growth a year", "Note")
HEADERS_RETURNS = ("Growth case", "Growth", RETURN_LABEL, "Against the hurdle")


def _pct(v, places: int = 1) -> str:
    return f"{100.0 * v:.{places}f}%"


def _date_label(iso: str) -> str:
    from datetime import datetime
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d %b %Y").lstrip("0")
    except ValueError:
        return iso


def _money(v, ccy, *, big: bool = False) -> str:
    return format_money(v, ccy or None, abbreviate=big)


def _stated(word: str, reason: str) -> str:
    """"Word: reason", without saying the word twice when the reason already opens with it."""
    if not reason:
        return f"{word}."
    text = reason if reason.lower().startswith(word.lower()) else f"{word}: {reason}"
    return text if text.endswith(".") else text + "."


def _verdict_line(av: AbsoluteValue) -> str:
    v = av.verdict
    if v.state in (EXCLUDED, NOT_ENOUGH_DATA, NOT_COVERED):
        return f"Verdict: {_stated(v.word, v.reason)}"
    return (f"Verdict: {v.word}, {v.count_text()}. The range is {_pct(v.low)} to {_pct(v.high)} a year, "
            f"against a hurdle of {_pct(av.hurdle.hurdle, 2)}.")


def _backtest_line(av: AbsoluteValue) -> str:
    v = av.backtest_verdict
    head = ("Backtest form (own 5-year, own 10-year and the downside case with own 5-year growth in its "
            "first years; no analyst figures)")
    if v is None:
        return f"{head}: not available."
    if v.state in (EXCLUDED, NOT_ENOUGH_DATA, NOT_COVERED):
        return f"{head}: {v.word + '.' if v.state == EXCLUDED else _stated(v.word, v.reason)}"
    return f"{head}: {v.word}, {v.count_text()}."


def _hurdle_line(av: AbsoluteValue) -> str:
    h = av.hurdle
    if not h.available:
        return f"Hurdle: not enough data: no hurdle ({h.note})."
    peg = f" ({h.note})" if h.note else ""
    return (f"Hurdle: {_pct(h.hurdle, 2)} = {h.bond_name} {_pct(h.bond_yield, 2)} "
            f"({_date_label(h.bond_date)}, series {h.bond_symbol}){peg} + {100.0 * h.margin:.1f} points.")


def _price_line(av: AbsoluteValue) -> str:
    p = av.price
    if not p.available:
        return f"Price: not enough data: {p.note or 'no price'}."
    if p.rate:
        return (f"Price: {_money(p.quote_price, p.quote_currency)} quoted = "
                f"{_money(p.price, p.currency)} in the currency of the accounts, {p.rate_line}.")
    return f"Price: {_money(p.price, p.currency)} a share."


def _base_lines(av: AbsoluteValue) -> tuple[list, str]:
    b = av.base
    rows = [[y.label, (_money(y.fcf_per_share, b.currency) if y.fcf_per_share is not None
                       else f"not available ({y.note})")] for y in b.years]
    if b.not_valued:
        if b.latest is None:
            text = (f"Base: {NO_FCF_TEXT}. Operating cash flow was negative and capital spending is not on "
                    f"file, so free cash flow cannot be positive.")
        else:
            text = (f"Base: {NO_FCF_TEXT}. The latest year was {_money(b.latest, b.currency)} a share"
                    + (f" and the three-year average {_money(b.average, b.currency)}"
                       if b.average is not None else "") + ".")
    elif b.usable:
        if b.average is not None and b.latest is not None:
            text = (f"Base: {_money(b.base, b.currency)} a share, the lower of the latest year "
                    f"({_money(b.latest, b.currency)}) and the three-year average "
                    f"({_money(b.average, b.currency)}), so {b.chosen}. Share count: {b.share_basis}.")
        else:
            text = f"Base: {_money(b.base, b.currency)} a share, {b.chosen}. Share count: {b.share_basis}."
        if b.note:
            text += f" {b.note}."
    else:
        text = f"Base: not enough data: {b.note or 'no free cash flow per share'}."
    return rows, text


NO_FCF_TEXT = "not valued: no positive free cash flow"


def _record_rows(av: AbsoluteValue) -> list[list[str]]:
    rows = []
    for c in av.cases[:3]:
        if c.growth is None:
            rows.append([c.label, "not available", c.reason])
            continue
        note = c.note
        if c.high_growth:
            note = (note + "; " if note else "") + "above 18% a year"
        if c.not_testable:
            note = (note + "; " if note else "") + "not testable in the backtest"
        rows.append([c.label, _pct(c.growth), note])
    return rows


def _return_rows(av: AbsoluteValue) -> list[list[str]]:
    hurdle = av.hurdle.hurdle
    rows = []
    for c in av.cases:
        if not c.available:
            rows.append([c.label, (_pct(c.growth) + " a year") if c.growth is not None else "not available",
                         f"not available ({c.reason})" if c.reason else "not available", ""])
            continue
        growth = c.growth_text + (" (above 18%)" if c.high_growth else "")
        ret = c.ret_bound or _pct(c.ret)
        cmp = "" if hurdle is None else ("clears" if c.clears(hurdle) else "below")
        rows.append([c.label, growth, ret, cmp])
    return rows


def _context_lines(av: AbsoluteValue) -> list[str]:
    ctx, ccy = av.context, av.price.quote_currency
    out = []
    ey = ctx.get("ebit_over_ev")
    if ey is not None:
        out.append(f"{ctx.get('ebit_over_ev_label') or 'Operating profit over enterprise value'}: {_pct(ey)}.")
    epv = ctx.get("epv")
    if epv is not None:
        ev = ctx.get("epv_enterprise_value")
        tail = f", against an enterprise value of {_money(ev, ccy, big=True)}" if ev is not None else ""
        out.append(f"Earnings power value at 9%: {_money(epv, ccy, big=True)}, an enterprise value{tail}.")
    cd = ctx.get("capex_over_depreciation")
    if cd is not None:
        out.append(f"Capital spending is {cd:.1f} times depreciation and amortisation "
                   f"(the cash flow statement's line includes other items, so this is approximate).")
    ab = ctx.get("abnormal_investment")
    if ab is not None:
        out.append(f"Capital spending against sales is {_pct(ab)} {'above' if ab >= 0 else 'below'} its own "
                   f"average of the previous three years.")
    if ctx.get("investment_warning"):
        out.append(f"Warning: {ctx['investment_warning']}.")
    return out


def blocks(av: AbsoluteValue, outside_range: bool = False) -> list[tuple]:
    """``[("p"|"note"|"strong"|"head", text) | ("table", headers, rows)]`` in page order."""
    out: list[tuple] = []
    if outside_range:
        out.append(("note", OUTSIDE_RANGE_LINE))
    if not av.shown:
        out.append(("strong", NOT_COVERED_LINE if av.verdict.state == NOT_COVERED
                    else f"Verdict: {av.verdict.word}. {av.verdict.reason}"))
        return out
    rows, base_text = _base_lines(av)
    out += [("head", "The base"), ("table", list(HEADERS_BASE), rows), ("p", base_text),
            ("p", _price_line(av))]
    out.append(("head", "The price test"))
    if av.yield_on_base is not None:
        out.append(("p", f"Free cash flow yield on the base: {_pct(av.yield_on_base)}."))
    if av.implied_growth is not None:
        out.append(("p", f"Implied growth: at the hurdle, the price needs {_pct(av.implied_growth)} a year of "
                         f"growth in free cash flow per share for ten years, flat after that."))
    else:
        out.append(("p", f"Implied growth: {av.implied_note or 'not enough data'}."))
    out.append(("table", list(HEADERS_RECORDS), _record_rows(av)))
    if any(c.high_growth for c in av.cases):
        out.append(("note", f"Marked rates: {HIGH_GROWTH_NOTE}."))
    ctx_lines = _context_lines(av)
    out += [("p", ln) for ln in ctx_lines]
    out.append(("head", "The return"))
    out.append(("table", list(HEADERS_RETURNS), _return_rows(av)))
    out.append(("p", _hurdle_line(av)))
    out.append(("strong", _verdict_line(av)))
    out.append(("p", _backtest_line(av)))
    out.append(("note", NOT_A_FORECAST))
    out.append(("note", EVIDENCE))
    if av.debt_line and ctx_lines:
        out.append(("note", av.debt_line))
    return out


def has_content(av) -> bool:
    return av is not None


def text_lines(av: AbsoluteValue, outside_range: bool = False) -> list[str]:
    """The text download. No blank line inside the block (the first-screen counter drops it whole)."""
    out = [SECTION_TITLE.upper(), CAPTION]
    for b in blocks(av, outside_range):
        if b[0] == "table":
            table = [list(b[1])] + [list(r) for r in b[2]]
            widths = [max(len(str(r[i])) for r in table) for i in range(len(b[1]))]
            for n, r in enumerate(table):
                out.append("  " + "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)).rstrip())
                if n == 0:
                    out.append("  " + "  ".join("-" * w for w in widths))
        elif b[0] == "head":
            out.append(f"{b[1]}:")
        else:
            out.append(b[1])
    return out


def markdown_lines(av: AbsoluteValue, outside_range: bool = False) -> list[str]:
    out = [f"## {SECTION_TITLE}", "", f"*{CAPTION}*", ""]
    for b in blocks(av, outside_range):
        out += _md_block(b)
    return out


def _md_block(b) -> list[str]:
    if b[0] == "table":
        return (["| " + " | ".join(b[1]) + " |", "|" + "---|" * len(b[1])]
                + ["| " + " | ".join(str(c).replace("|", "/") for c in r) + " |" for r in b[2]] + [""])
    if b[0] == "head":
        return [f"**{b[1]}**", ""]
    if b[0] == "strong":
        return [f"**{b[1]}**", ""]
    if b[0] == "note":
        return [f"*{b[1]}*", ""]
    return [b[1], ""]


def markdown_block(b) -> str:
    """One block as Markdown, for the Streamlit page."""
    return chr(10).join(_md_block(b)).rstrip()


def html_section(av: AbsoluteValue, outside_range: bool, esc, table_html) -> str:
    """``esc`` is the exporter's escaper and ``table_html(headers, rows)`` its table builder, so this
    module imports nothing from the export package."""
    out = [f'<section class="section" id="absolute-value"><h2>{esc(SECTION_TITLE)}</h2>',
           f'<p class="note">{esc(CAPTION)}</p>']
    for b in blocks(av, outside_range):
        if b[0] == "table":
            out.append(table_html(list(b[1]), [[esc(str(c)) for c in r] for r in b[2]]))
        elif b[0] == "head":
            out.append(f"<h3>{esc(b[1])}</h3>")
        elif b[0] == "strong":
            out.append(f"<p><strong>{esc(b[1])}</strong></p>")
        elif b[0] == "note":
            out.append(f'<p class="note">{esc(b[1])}</p>')
        else:
            out.append(f"<p>{esc(b[1])}</p>")
    return "".join(out) + "</section>"
