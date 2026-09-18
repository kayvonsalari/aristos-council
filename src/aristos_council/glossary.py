"""GLOSSARY-1 — "What the terms mean", built from the registries, for the terms USED.

Two rules decide everything here.

**Built from the registries, never a hardcoded list.** ``FactorDef.glossary`` and
``Criterion.glossary`` carry the definitions, so adding a factor or a rule adds its entry
automatically — and a test fails the registry when either is left blank, which is what
stops the next addition arriving unexplained. A hardcoded list in a renderer would rot
the first time someone registered a factor and forgot this file existed.

**Only what the report actually contains.** A term the run never used does not appear: a
glossary of forty entries for a three-lens run is a wall to scroll past, and a reader
cannot tell which of them bear on what they just read. The report passes in the factors
and criteria that ran; the fixed terms below are included only when the report's own text
mentions them.

Everything here is display-only and deterministic — no IO, no LLM, no clock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SECTION_TITLE = "What the terms mean"
SECTION_NOTE = ("Every term this report uses, in plain English. Terms the run did not "
                "use are left out.")


@dataclass(frozen=True)
class GlossaryEntry:
    term: str
    definition: str


# DETAIL-1b — the ⚠ badge explained, in ONE place.
#
# The first attempt read "a price that ran up hard while a floor the lens screens on was
# failing (cyclical inflection or mania; human review)". Every clause of that is true, and
# none of it tells a reader who has not read the code what the symbol means, what number
# produced it, or what they are meant to do about it. It also tucked the ambiguity into a
# parenthetical, when the ambiguity IS the point: the report cannot tell a turning cycle
# from a mania, and the honest thing is to say so and ask for a human.
#
# It lives HERE rather than beside the detail section because this module is the lower
# layer (pipeline imports it, not the other way round), and because a mark's explanation
# belongs with the vocabulary. The detail section states it above its groups and the
# glossary entry below is the same string, so one mark can never acquire two
# explanations.
DETAIL_BADGE_NOTE = (
    "A ⚠ badge such as ⚠ +80% 12m means the share price has risen 80% over the last "
    "twelve months. The report attaches it whenever the rise is more than +30%, "
    "because a move that large in a cyclical company means one of two things: the "
    "cycle has turned and profits are about to catch up, or the price has run ahead of "
    "anything the business can earn. The numbers cannot tell which, so the badge asks "
    "for a human look. It is a warning about the price, not part of the rule the "
    "company failed.")


# Terms that belong to the REPORT rather than to a factor or a criterion — the vocabulary
# the document itself introduces. Each is included only when the rendered text mentions
# it, matched on ``probe``. Definitions condensed from CALCULATIONS.md; where that file
# defines a term, this shortens it and must never contradict it.
_REPORT_TERMS: tuple[tuple[str, str, str], ...] = (
    ("EPS (earnings per share)", "eps",
     "Annual profit divided by the number of shares."),
    ("P/E ratio", "p/e",
     "Share price divided by earnings per share: how many years of today's profit you "
     "pay for one share."),
    ("EV/EBIT", "ev/ebit",
     "What the whole company costs — shares plus debt, less cash — divided by operating "
     "profit. Like P/E but debt-aware, so companies that borrow differently still "
     "compare fairly."),
    ("FCF (free cash flow)", "free cash flow",
     "Cash the business generates after paying its bills and its investment; the cash "
     "actually available for dividends, buybacks or paying down debt."),
    ("Own 5-year median", "median",
     "The middle value of THIS company's own multiple over the last five years — its "
     "normal price level, judged by its own history rather than against other names."),
    ("Percentile (valuation band)", "percentile",
     "Where today's multiple sits in that five-year history. The 10th percentile is "
     "cheaper than 90% of the period; the 90th is dearer than 90% of it."),
    ("Reversion value", "reversion",
     "What the share price would be TODAY if the multiple returned to the company's own "
     "five-year median. Arithmetic on its own history — not a forecast and not a target."),
    ("Quintile cut", "quintile",
     "The top fifth of ranked names are rated BUY, the bottom fifth SELL, and everything "
     "between them HOLD."),
    ("PEG ratio", "peg",
     "The P/E divided by the growth rate: what you pay per unit of growth."),
    ("Revenue CAGR", "cagr",
     "Average yearly revenue growth over the period."),
    ("Annualized volatility", "volatilit",
     "How violently the share price swings in a typical year."),
    # CHECK-WORDS-1 — a check lens's three words. Defined together, because the point of
    # them is the contrast with BUY/HOLD/SELL: a check answers a different question, and
    # saying its answer in a verdict's words invites exactly the wrong reading.
    ("clean · no concern · doubted", "no concern",
     "A CHECK lens's three readings, in place of BUY / HOLD / SELL. A check does not pick "
     "companies — it looks for reasons to doubt the ones other tests picked. "
     "**clean** = it found nothing to doubt (its top fifth); **doubted** = it found "
     "something (its bottom fifth); **no concern** = everything in between. "
     "\"clean\" is not a recommendation and \"doubted\" is not a sell."),
    ("Not tested / not evaluated", "not evaluated",
     "The data this check needed was missing, so the check was skipped honestly rather "
     "than guessed at. It is not a pass and it is not a fail."),
    ("Not assessed", "not assessed",
     "The evidence channel this specialist reads returned nothing, so it offered no "
     "view. A missing channel, not a measured neutral."),
    ("⚠ (price badge)", "⚠", DETAIL_BADGE_NOTE),
    ("⚑", "⚑",
     "This verdict sits on a boundary: the name tied on score with another that got a "
     "DIFFERENT verdict, and the alphabetical tie-break decided which side each fell."),
)


def _mentions(text: str, probe: str) -> bool:
    if not probe:
        return False
    if not probe.isalnum() and len(probe) <= 2:      # a symbol like ‡ or ⚑
        return probe in text
    return re.search(re.escape(probe), text, re.I) is not None


def glossary_entries(*, factors=(), criteria=(), text: str = "") -> list[GlossaryEntry]:
    """The entries this report needs, alphabetical by term.

    ``factors`` / ``criteria`` are the registry objects that ACTUALLY ran (so their
    definitions are included whether or not the prose happens to name them); ``text`` is
    the rendered report, which decides the fixed report-vocabulary terms. A factor or
    criterion with no glossary is skipped rather than rendered blank — the registry test
    is what makes that case impossible in the first place."""
    entries: dict[str, str] = {}

    for factor in factors or ():
        gloss = (getattr(factor, "glossary", "") or "").strip()
        if gloss:
            entries[getattr(factor, "label", "") or factor.name] = gloss

    for criterion in criteria or ():
        gloss = (getattr(criterion, "glossary", "") or "").strip()
        if gloss:
            label = getattr(criterion, "label", "") or criterion.name
            entries[f"{label} ({criterion.name})"] = gloss

    for term, probe, definition in _REPORT_TERMS:
        if _mentions(text, probe):
            entries.setdefault(term, definition)

    return [GlossaryEntry(term=t, definition=d)
            for t, d in sorted(entries.items(), key=lambda kv: kv[0].lower())]


def glossary_markdown(entries) -> list[str]:
    """The section as markdown — a definition list the .md can carry unaided."""
    if not entries:
        return []
    out = ["", f"## {SECTION_TITLE}", "", f"_{SECTION_NOTE}_", ""]
    out += [f"- **{e.term}** — {e.definition}" for e in entries]
    return out


def glossary_html(entries, *, esc, anchor: str = "glossary") -> str:
    """The same section as HTML, from the same entries."""
    if not entries:
        return ""
    items = "".join(f"<dt>{esc(e.term)}</dt><dd>{esc(e.definition)}</dd>"
                    for e in entries)
    return (f'<section class="section" id="{anchor}">'
            f"<h2>{esc(SECTION_TITLE)}</h2>"
            f'<p class="note">{esc(SECTION_NOTE)}</p>'
            f'<dl class="glossary">{items}</dl></section>')
