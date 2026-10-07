"""UI-POLISH-1 item 4 - the Company Report's look: a header card, summary chips, a lens table of verdict
chips, and four small stat cards.

PRESENTATION ONLY. Every figure and word is read from what the run already holds (the votes, the
agreement, ``price_and_cash``, ``debt_and_cash``, the valuation band text); nothing is computed here that
a reader could not see on the page below, and no vote, rank or number can change. A figure that is
missing becomes a short muted reason on its card, never a blank or a zero. The HTML comes from
``ui_style``'s builders, which escape everything.
"""
from __future__ import annotations

import re
from typing import Optional

from . import ui_style as ui
from .tools.valuation_band import ordinal

CHEAP_AT = 30.0          # the same cut points the list report's "cheap / expensive" wording already uses
DEAR_AT = 70.0


# --------------------------------------------------------------------------- #
# small readers
# --------------------------------------------------------------------------- #
def _pac(report):
    return getattr(getattr(report, "check", None), "price_and_cash", None)


def _after(label: str, prefix: str) -> str:
    return label[len(prefix):] if label.startswith(prefix) else label


def _val(reading) -> Optional[float]:
    return getattr(reading, "value", None) if reading is not None else None


def _why(reading, fallback: str) -> str:
    """A short reason for a missing reading, from the reading's own words."""
    if reading is None:
        return fallback
    text = (getattr(reading, "not_meaningful", "") or getattr(reading, "failure", "")
            or getattr(reading, "note", "") or "").strip().rstrip(".")
    return (text[:1].lower() + text[1:]) if text else fallback


def band_word(percentile: Optional[float]) -> str:
    """"Cheap vs its own 5 years" / "Dear ..." / "Mid-range ..." / "Valuation band not read"."""
    if percentile is None:
        return "Valuation band not read"
    if percentile <= CHEAP_AT:
        return "Cheap vs its own 5 years"
    if percentile >= DEAR_AT:
        return "Dear vs its own 5 years"
    return "Mid-range vs its own 5 years"


_MULTIPLE = re.compile(r"^(P/E|EV/EBIT)\s+(-?[\d.]+)")


# --------------------------------------------------------------------------- #
# header card
# --------------------------------------------------------------------------- #
def header_card(report) -> str:
    check = report.check
    group = getattr(check, "peer_group", None)
    subject = getattr(group, "subject", None) if group is not None else None
    from .peer_table import clean_company_name
    from .exchange_names import exchange_name
    name = clean_company_name(getattr(check, "company_name", "") or "") or report.display
    bits = [report.ticker]
    if subject is not None and getattr(subject, "exchange", ""):
        bits.append(exchange_name(subject.exchange))
    if subject is not None and getattr(subject, "classification", ""):
        bits.append(subject.classification)
    n = len(getattr(group, "members", ()) or ()) if group is not None else 0
    if n:
        bits.append(f"compared with {n} similar {'company' if n == 1 else 'companies'}")
    sub = " · ".join(bits)
    pac = _pac(report)
    price = ""
    if pac is not None and getattr(pac.last_close, "available", False):
        last = _after(pac.last_close.label, "last close ")
        change = ""
        if getattr(pac.return_12m, "available", False):
            change = f'<div class="ar-sub ar-num">{ui.esc(_after(pac.return_12m.label, "12-month return "))} over 12 months</div>'
        price = (f'<div class="ar-price"><div class="ar-big ar-num">{ui.esc(last)}</div>{change}</div>')
    return ui._one_line(f'<div class="ar-card ar-header"><div><div class="ar-name">{ui.esc(name)}</div>'
                        f'<div class="ar-sub">{ui.esc(sub)}</div></div>{price}</div>')


# --------------------------------------------------------------------------- #
# summary chips
# --------------------------------------------------------------------------- #
def summary_chips(report) -> str:
    ag = getattr(report, "agreement", None)
    chips: list[str] = []
    if ag is not None:
        for key, word in (("buy", "BUY"), ("hold", "HOLD"), ("sell", "SELL")):
            n = len(getattr(ag, key, ()) or ())
            if n:
                chips.append(ui.chip(f"{n} {word}", word.lower()))
        if getattr(ag, "n_not_applying", 0):
            chips.append(ui.chip(f"{ag.n_not_applying} does not apply", "na"))
        for label, mark in (getattr(ag, "checks", None) or {}).items():
            if mark:
                chips.append(ui.chip(f"{label}: {mark}", "na"))
    chips.append(ui.chip(band_word(getattr(report.check, "band_percentile", None)), "na"))
    return ui.chip_row(chips)


# --------------------------------------------------------------------------- #
# the lens table
# --------------------------------------------------------------------------- #
def lens_rows(report) -> list[dict]:
    """One dict per lens: ``lens``, ``word`` (the chip's words), ``kind``, ``detail`` (the position, muted),
    ``badge`` and ``reason`` - read from the votes and the story page's own table rows."""
    from .company_story import table_rows
    rows = []
    for v, row in zip(report.votes, table_rows(report)):
        if v.status == "ranked":
            where = (f"{ordinal(v.position)} of {v.cohort_size}" if v.position else f"of {v.cohort_size}")
            detail = where + (f", tied with {v.tied_with}" if v.tied_with else "")
            word, kind = v.word, ui.chip_kind(v.word) if v.votes else "na"
        else:
            word, detail, kind = row.outcome, "", "na"
        rows.append({"lens": row.lens, "word": word, "kind": kind, "detail": detail,
                     "badge": "" if row.badge == "none" else row.badge,
                     "reason": row.reason})
    return rows


def lens_table(report) -> str:
    rows = lens_rows(report)
    cells = []
    for r in rows:
        vote = ui.chip(r["word"], r["kind"]) + (f'<span class="ar-detail ar-num">{ui.esc(r["detail"])}</span>'
                                                   if r["detail"] else "")
        badge = ui.badge(r["badge"]) or '<span class="ar-muted">—</span>'
        cells.append([r["lens"], vote, badge, r["reason"]])
    return ui.table(["Lens", "Vote or mark", "Badge", "Reason"], cells, raw_cols=(1, 2), reason_col=3)


# --------------------------------------------------------------------------- #
# the four stat cards
# --------------------------------------------------------------------------- #
def valuation_card(report) -> str:
    check = report.check
    p = getattr(check, "band_percentile", None)
    text = str(getattr(check, "valuation_band", "") or "")
    m = _MULTIPLE.match(text)
    if p is None or m is None:
        reason = re.sub(r"^not evaluated\s*[—-]\s*", "", text).strip() or "not evaluated"
        return ui.stat_card("Valuation vs own history", abstain=reason.split(";")[0][:90])
    return ui.stat_card("Valuation vs own history", f"{m.group(1)} {m.group(2)}",
                        [f"{ordinal(round(p))} percentile of its own 5-year range"],
                        extra_html=ui.percentile_bar(p))


def price_card(report) -> str:
    pac = _pac(report)
    title = "Price"
    if pac is None or not getattr(pac.pct_off_high, "available", False):
        return ui.stat_card(title, abstain=_why(getattr(pac, "pct_off_high", None), "no price history"))
    v = pac.pct_off_high.value
    big = f"{abs(v) * 100:.1f}% {'below' if v < 0 else 'above'} 52-week high"
    sub = []
    if pac.return_6m.available:
        sub.append(f"6-month return {_after(pac.return_6m.label, '6-month return ')}")
    if pac.volatility.available:
        sub.append(f"volatility {_after(pac.volatility.label, 'annualised volatility ')}")
    return ui.stat_card(title, big, sub)


def earnings_price_card(report) -> str:
    pac = _pac(report)
    title = "Earnings price"
    fwd = getattr(pac, "forward_pe_this_year", None)
    if pac is None or _val(fwd) is None:
        return ui.stat_card(title, abstain=_why(fwd, "no analyst estimate"))
    sub = []
    nxt, trailing = _val(pac.forward_pe_next_year), _val(pac.trailing_pe)
    if nxt is not None:
        sub.append(f"next year {nxt:.1f}x")
    if trailing is not None:
        sub.append(f"trailing P/E {trailing:.1f}")
    return ui.stat_card(title, f"forward P/E {fwd.value:.1f}x", [" · ".join(sub)] if sub else [])


def balance_sheet_card(report) -> str:
    dc = getattr(report.check, "debt_and_cash", None)
    title = "Balance sheet"
    if dc is None:
        return ui.stat_card(title, abstain="not available")
    if getattr(dc.net_debt, "not_meaningful", ""):
        return ui.stat_card(title, abstain="not meaningful for banks")
    if not dc.net_debt.available:
        return ui.stat_card(title, abstain=_why(dc.net_debt, "not reported"))
    from .abs_readings import _money
    net = dc.net_debt.value
    money = _money(abs(net), dc.currency)
    big = f"net cash {money}" if net <= 0 else f"net debt {money}"
    cover = dc.interest_cover
    if cover.available:
        sub = ["interest immaterial" if "immaterial" in cover.label else f"interest cover {cover.value:.1f}x"]
    else:
        sub = [f"interest cover: {_why(cover, 'not stated')}"]
    return ui.stat_card(title, big, sub)


def stat_cards(report) -> str:
    return ui.cards_grid([valuation_card(report), price_card(report), earnings_price_card(report),
                          balance_sheet_card(report)])
