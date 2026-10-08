"""Self-contained HTML export of the two shared reports (REPORT-HTML-1).

WHY: reports get sent to people outside the repo. Markdown renders poorly for them and
the PDF path breaks the wide rank tables. This module renders the SAME report data as a
single ``.html`` file — all CSS inline, no external requests, no JS, no build step — so it
can be mailed, opened anywhere, and printed to PDF by the browser.

HARD CONSTRAINT: this is a PRESENTATION layer, nothing else. The universe ``.md`` report
and the Company Check ``.txt`` report stay CANONICAL and byte-identical (frozen monthly
records and the narration-check fixtures depend on them) — this module reads the same
structured result and writes a second export beside them. It never mutates the result and
never rewrites the model's prose.

Two content rules the export must honour, both inherited from the house doctrine:

- **Nothing is silently dropped.** Every narration sentence and every ``[⚠ narration
  check: …]`` stamp the markdown carries is present in the HTML, verbatim (markdown
  emphasis markers are the only characters consumed — ``**bold**`` becomes ``<strong>``,
  exactly the ``_demark`` discipline ``narration_check`` already applies for parsing).
- **Nothing is invented.** A field the result does not carry (no run timestamp, no
  strategy display name) is OMITTED, never guessed.

Presentation of the two annotation classes is deliberate:

- ``[⚠ narration check: …]`` stamps render as WARNING CALLOUTS attached to the paragraph
  holding the sentence they annotate (matched on the claim the stamp quotes), falling back
  to the end of that name's section when the claim cannot be located — a stamp is never
  dropped for want of a match.
- Provenance receipts (``[static: 2026-07-21, EODHD]``) render as small BADGES, brackets
  and all, so the text stays verbatim while the eye reads them as metadata.

Print (``@media print``) is a first-class target: A4 pages, the ranked table shrunk to fit
rather than clipped, a page break before each per-name section, and callouts/badges forced
to white-on-black borders so they stay legible in grayscale.
"""

from __future__ import annotations

from aristos_council.plurals import plural

from aristos_council.tools.price_context import format_money

import html
import re
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from ..rank_engine import BOUNDARY_FLAG, ranked_table_rows

# Timestamps are captured in UTC; every user-facing surface displays Europe/Berlin.
DISPLAY_TZ = ZoneInfo("Europe/Berlin")

# The house doctrine line — who judges and who writes. Verbatim in every export footer.
DOCTRINE = ("Verdicts are deterministic — math judges, the LLM only writes; "
            "a fact-checker annotates the prose.")

# These files get sent to third parties, so the disclaimer travels with them.
DISCLAIMER = ("Research tool, not financial advice. Nothing here is a recommendation to "
              "buy or sell any security. Figures come from third-party vendor data and "
              "may be incomplete, stale or wrong; abstentions and warnings in this "
              "document are part of the record, not noise.")

# Verdict palette — the SAME hexes Council Station uses on screen (app._VERDICT_HEX), so
# a shared report and the app read alike. Forced to black in print (grayscale legibility).
_VERDICT_HEX = {"BUY": "#2E7D32", "HOLD": "#B8860B", "SELL": "#B23B3B"}
_STATUS_HEX = {"PASS": "#2E7D32", "FAIL": "#B23B3B", "NOT-EVALUATED": "#B8860B"}

# A narration-check stamp is one appended line opening with "[⚠" (narration_check's
# _annotation / _tie_annotation). Lifted out of the prose flow so each renders as its own
# callout — the text itself is never altered.
_STAMP_OPEN = "[⚠"
# The claim a stamp quotes, so the callout can be attached to the paragraph stating it.
_STAMP_CLAIM = re.compile(r'(?:narration|AI text) check:\s*"(.*?)"\s+')

# Inline markdown the narrator writes. A BARE `_` is NEVER touched — it is load-bearing
# in the factor keys the prose quotes verbatim (fund_size, momentum_12m), the same reason
# narration_check._demark leaves it alone. HTML-NARR-MD-1 — a PAIR of underscores
# wrapping a whole multi-word span ("_Each lens ranks only..._") is unambiguously
# markdown italics, never a factor key (no factor key contains a space), so that one
# shape IS converted; requiring an internal space is what keeps `fund_size` untouched.
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_SPAN = re.compile(r"(?<!\w)_([^_\n]*\s[^_\n]*)_(?!\w)")
_CODE = re.compile(r"`([^`]+)`")
_MD_MARKS = re.compile(r"[*`]+")
# A provenance receipt in prose — "[static: 2026-07-21, EODHD]". Rendered as a badge with
# its brackets INTACT, so the document's text stays verbatim.
_PROVENANCE = re.compile(r"\[(static:\s?[^\]\n]{1,200})\]")
_BULLET_MARK = re.compile(r"^\s*[-•]\s*")

# Five LENS ACCENTS (REPORT-HTML-STYLE). A multi-lens report is read by following ONE
# lens down the page — its rules block, its column in the verdict grid, its detail
# section — so each lens gets a colour and keeps it in all three places. Chosen from the
# blue/orange/teal/purple/magenta family rather than any red-green pair, so they stay
# distinguishable under the common colour-vision deficiencies, and every one is a
# LABEL's companion, never a label's replacement.
LENS_ACCENTS = 5


def _compact_rules_html(multi_result) -> str:
    """RULES-TOP-1 — one short line per lens under the header, linked to the full
    tables. Derived from the strategies that ran (``pipeline.compact_rules``)."""
    from ..pipeline import compact_rules

    rows = compact_rules(multi_result)
    if not rows:
        return ""
    body = " · ".join(f"<strong>{_esc(r['lens'])}</strong> — {_esc(r['summary'])}"
                      for r in rows)
    return (f'<p class="rules-compact">{body} '
            f'<a href="#rules">[details]</a></p>')


def _glossary_html(multi_result, rendered: str) -> str:
    from ..factors import FACTOR_REGISTRY
    from ..glossary import glossary_entries, glossary_html
    from ..pipeline import rules_applied
    from ..tools.criteria.registry import REGISTRY

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
    entries = glossary_entries(factors=factors.values(), criteria=criteria.values(),
                               text=_visible(rendered))
    return glossary_html(entries, esc=_esc)


def _visible(html_text: str) -> str:
    """Tag-stripped text, so the glossary decides on what a READER sees rather than on
    class names and attributes."""
    return re.sub(r"<[^>]+>", " ", html_text)


def _contents(sections) -> str:
    """REPORT-4 part 3 — the contents list, built from ``pipeline.report_sections``.

    The document runs to ~70KB across seven sections and a narration block per name, and
    had no way to jump. Every entry is an in-page anchor to a section this render will
    actually emit (the section list is the same one the renderer walks, so a link cannot
    point at a section that was skipped). It stays useful on paper: the print stylesheet
    keeps it, because a printed contents list is still a map even without the links."""
    def _items(nodes) -> str:
        out = []
        for node in nodes:
            kids = _items(node["children"]) if node["children"] else ""
            out.append(f'<li><a href="#{html.escape(node["anchor"], quote=True)}">'
                       f'{html.escape(str(node["title"]))}</a>{kids}</li>')
        return f"<ul>{''.join(out)}</ul>" if out else ""

    body = _items(sections)
    if not body:
        return ""
    return f'<nav class="contents" aria-label="Contents"><h2>Contents</h2>{body}</nav>'


def lens_class(index: int) -> str:
    """The accent class for the nth lens in the run — wraps past five, so a six-lens run
    repeats a colour rather than silently rendering one lens unaccented."""
    return f"lens-{index % LENS_ACCENTS}"


# The valuation percentile's five buckets (REPORT-1's fixed gloss) mapped to a diverging
# BACKGROUND tint: cool at the cheap end, warm at the dear end. Deliberately a different
# channel and a different hue family from the verdict palette — the band ranks nothing
# and decides nothing, so it must not borrow buy/sell colours.
_PERCENTILE_BUCKETS = ("cheapest", "cheap", "mid", "dear", "dearest")


def percentile_class(cell: str) -> str:
    """The tint class for a valuation-percentile cell, taken from the ONE-WORD gloss
    REPORT-1 already renders inside it ("1st (cheapest)"). Read, never re-derived — the
    scale cannot disagree with the word beside it, and a cell that abstained
    ("not evaluated — …") gets no tint at all."""
    for bucket in _PERCENTILE_BUCKETS:
        if f"({bucket})" in cell:
            return f"pct-{bucket}"
    return ""

_CSS = """
/* --------------------------------------------------------------------------
   Tokens. Every colour is defined here on :root for the light scheme and
   redefined once under prefers-color-scheme: dark, so a reader in either mode
   gets the same document with legible contrast — and no rule below hardcodes
   a colour that only works on white.
   -------------------------------------------------------------------------- */
:root {
  color-scheme: light dark;
  --bg: #ffffff;
  --fg: #16181d;
  --fg-quiet: #5b6472;
  --rule: #c7cdd8;
  --rule-strong: #16181d;
  --panel: #f6f8fb;
  --panel-alt: #fafbfd;
  --head: #eef1f6;

  --buy: #2E7D32;
  --hold: #96690a;
  --sell: #B23B3B;

  --lens-0: #1f6fb2;   /* blue    */
  --lens-1: #b25f00;   /* orange  */
  --lens-2: #0f7b6c;   /* teal    */
  --lens-3: #7a4bb5;   /* purple  */
  --lens-4: #a8306f;   /* magenta */

  /* Diverging tint for the valuation percentile: cool = its own cheap end,
     warm = its own dear end. Background only; the word is always present too. */
  --pct-cheapest: rgba(31, 111, 178, .20);
  --pct-cheap:    rgba(31, 111, 178, .10);
  --pct-mid:      transparent;
  --pct-dear:     rgba(178, 95, 0, .12);
  --pct-dearest:  rgba(178, 95, 0, .24);

  --callout-bg: #fff8e5;
  --callout-edge: #9a6700;
  --alert-bg: #fdf2f2;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14161a;
    --fg: #e6e9ee;
    --fg-quiet: #9aa3b2;
    --rule: #333a45;
    --rule-strong: #e6e9ee;
    --panel: #1c1f26;
    --panel-alt: #191c22;
    --head: #232833;

    --buy: #6cc46f;
    --hold: #e0b243;
    --sell: #ef8080;

    --lens-0: #6fb8ee;
    --lens-1: #f0a35e;
    --lens-2: #4fc3ae;
    --lens-3: #bfa0ee;
    --lens-4: #f08fc0;

    --pct-cheapest: rgba(111, 184, 238, .26);
    --pct-cheap:    rgba(111, 184, 238, .13);
    --pct-mid:      transparent;
    --pct-dear:     rgba(240, 163, 94, .15);
    --pct-dearest:  rgba(240, 163, 94, .30);

    --callout-bg: #2a2415;
    --callout-edge: #d0a02a;
    --alert-bg: #2b1c1c;
  }
}

* { box-sizing: border-box; }
body { margin: 0; padding: 26px 30px 44px; background: var(--bg); color: var(--fg);
       font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
             "Helvetica Neue", Arial, sans-serif; }
.wrap { max-width: 1180px; margin: 0 auto; }
code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas,
              "Liberation Mono", monospace; font-size: 0.92em; }
a { color: inherit; }

header.doc { border-bottom: 3px solid var(--rule-strong); padding-bottom: 12px;
             margin-bottom: 18px; }
header.doc .kicker { text-transform: uppercase; letter-spacing: .09em; font-size: 11px;
                     font-weight: 700; color: var(--fg-quiet); margin: 0 0 4px; }
header.doc h1 { font-size: 26px; line-height: 1.2; margin: 0 0 8px; }
.house { margin: 10px 0 0; padding: 8px 12px; border: 1px solid var(--rule);
         border-left: 5px solid var(--rule-strong); background: var(--panel);
         font-weight: 600; }
/* REPORT-1: the one-line verdict summary, directly under the header. There was no
   summary anywhere before — a reader had to count the ranked table by hand. */
.summary { margin: 8px 0 0; font-size: 16px; font-weight: 700; }
/* A machine id kept beside its human label: present for auditability, visually second. */
.muted { color: var(--fg-quiet); font-size: 0.86em; font-weight: 400; }

.kv { display: table; width: 100%; margin: 10px 0 0; border-collapse: collapse; }
.kv .row { display: table-row; }
.kv .k, .kv .v { display: table-cell; padding: 3px 10px 3px 0; vertical-align: top;
                 font-size: 13px; }
.kv .k { color: var(--fg-quiet); white-space: nowrap; width: 1%; text-transform: uppercase;
         letter-spacing: .05em; font-size: 11px; font-weight: 700; padding-top: 5px; }

h2 { font-size: 17px; margin: 0 0 8px; padding-bottom: 4px;
     border-bottom: 1px solid var(--rule); }
h3 { font-size: 14px; margin: 16px 0 6px; }
h4 { font-size: 13px; margin: 12px 0 4px; }
p { margin: 8px 0; }
ul { margin: 8px 0; padding-left: 22px; }
li { margin: 3px 0; }
.note { color: var(--fg-quiet); font-size: 12.5px; margin: 4px 0; }

/* --------------------------------------------------------------------------
   Section hierarchy. A major section is a CARD — its own panel, its own top
   rule — so the eye finds its edges instead of reading one long column of
   text. (The valuation band was being missed entirely for want of this.)
   -------------------------------------------------------------------------- */
/* REPORT-4 — the record id kept BESIDE the human title, muted, never removed. */
.record-id { font-size: 13px; font-weight: 400; color: var(--fg-quiet);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; white-space: nowrap; }

/* REPORT-4 — contents. Anchors survive print (the list is a map on paper too). */
nav.contents { margin: 14px 0 0; padding: 10px 14px; background: var(--panel-alt);
  border: 1px solid var(--rule); border-radius: 6px; }
nav.contents h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .08em;
  margin: 0 0 6px; color: var(--fg-quiet); }
nav.contents ul { margin: 0; padding-left: 18px; }
nav.contents ul ul { padding-left: 16px; }
nav.contents li { margin: 2px 0; }
nav.contents a { color: var(--fg); text-decoration: none; border-bottom: 1px dotted
  var(--rule); }
nav.contents a:hover { border-bottom-style: solid; }

/* NARR-SCHEMA-1 — the structural warning banner. Shares the callout's shape so it
   reads as the same CLASS of annotation as a fact-check stamp, with its own edge so
   the two are distinguishable at a glance. Print rules already force callouts to
   white-on-black, so this stays legible in grayscale. */
.callout.structural { border-left-color: var(--sell); background: var(--alert-bg); }
.callout.structural ul { margin: 6px 0 0; padding-left: 18px; }
.callout.structural p { margin: 4px 0; }

/* REPORT-4 — the narration's own sub-blocks, rendered from the narrator's FIELDS. */
.narr-block { margin: 0 0 14px; }
.narr-block h4 { font-size: 13px; text-transform: uppercase; letter-spacing: .07em;
  color: var(--fg-quiet); margin: 0 0 6px; border-bottom: 1px solid var(--rule);
  padding-bottom: 3px; }
.narr-block h5 { font-size: 13px; margin: 10px 0 4px; }
.narr-block p.echoed { font-weight: 600; }
.narr-block p.screens { color: var(--fg-quiet); font-size: 13px; margin: 4px 0; }
p.clean { color: var(--fg-quiet); font-style: italic; margin: 4px 0; }

.section { margin: 0 0 18px; padding: 14px 16px 10px; background: var(--panel);
           border: 1px solid var(--rule); border-radius: 6px;
           border-top: 3px solid var(--rule-strong); }
.section > h2 { margin-top: 0; }
.section table { background: var(--bg); }

.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 13px; }
th, td { border: 1px solid var(--rule); padding: 5px 8px; text-align: left;
         vertical-align: top; }
th { background: var(--head); font-size: 11.5px; text-transform: uppercase;
     letter-spacing: .04em; overflow-wrap: anywhere; }
/* Sticky headers, but only where they can DO anything: a table long enough to be
   given its own scroll box (see .scroll.tall). Short tables are left alone. */
.scroll.tall { max-height: 78vh; overflow: auto; }
.scroll.tall thead th { position: sticky; top: 0; z-index: 2;
                        box-shadow: inset 0 -1px 0 var(--rule); }
tbody tr:nth-child(even) td { background: var(--panel-alt); }
tbody tr:hover td { background: var(--head); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.pos { font-weight: 700; white-space: nowrap; }
.pos .detail { font-weight: 400; color: var(--fg-quiet); font-size: 12px;
               white-space: normal; }

/* Verdicts carry COLOUR AND TEXT — never colour alone, so the meaning survives
   printing, grayscale and colour-vision deficiency. */
.verdict { font-weight: 700; white-space: nowrap; }
.verdict-buy { color: var(--buy); }
.verdict-hold { color: var(--hold); }
.verdict-sell { color: var(--sell); }
td.cell-buy { box-shadow: inset 3px 0 0 var(--buy); }
td.cell-hold { box-shadow: inset 3px 0 0 var(--hold); }
td.cell-sell { box-shadow: inset 3px 0 0 var(--sell); }

/* TAB-MERGE-1 part 2 commit 2: the company's own row in a peers table — a VISIBLE
   highlight beside the "(this company)" text marker, not instead of it. */
td.this-company { background: #f6d86b !important; color: #1d2127 !important; font-weight: 700; }

/* Valuation percentile: a diverging tint behind a cell that ALREADY says the word. */
td.pct-cheapest { background: var(--pct-cheapest) !important; }
td.pct-cheap    { background: var(--pct-cheap) !important; }
td.pct-mid      { background: var(--pct-mid); }
td.pct-dear     { background: var(--pct-dear) !important; }
td.pct-dearest  { background: var(--pct-dearest) !important; }

/* --------------------------------------------------------------------------
   Lens accents. One colour per lens, used in all three places that lens
   appears, so a reader can follow it down the page.
   -------------------------------------------------------------------------- */
.lens-0 { --accent: var(--lens-0); }
.lens-1 { --accent: var(--lens-1); }
.lens-2 { --accent: var(--lens-2); }
.lens-3 { --accent: var(--lens-3); }
.lens-4 { --accent: var(--lens-4); }

h3.lens-head { color: var(--accent); border-left: 4px solid var(--accent);
               padding-left: 8px; margin-top: 20px; font-size: 15px; }
section.lens-card { border-top-color: var(--accent); }
section.lens-card > h2 { color: var(--accent); border-bottom-color: var(--accent); }
th.lens-col { color: var(--accent); border-bottom: 3px solid var(--accent); }
td.lens-col { box-shadow: inset 3px 0 0 var(--accent); }
/* A verdict cell inside a lens column keeps the lens stripe; the verdict itself is
   carried by the coloured WORD, which is always present. */
td.lens-col.cell-buy, td.lens-col.cell-hold, td.lens-col.cell-sell {
  box-shadow: inset 3px 0 0 var(--accent); }

.flag { display: block; margin-top: 2px; font-weight: 700; font-size: 11.5px;
        color: var(--fg); border: 1px solid var(--fg); border-radius: 3px;
        padding: 1px 5px; background: var(--head); white-space: normal; }
.status { font-weight: 700; white-space: nowrap; }

/* DETAIL-1 — a pre-screen gate's names fold away behind their own summary. The names
   are always THERE; they are one click from view rather than thirty lines in front of
   the first rule. Print forces every <details> open (see the print block below), so the
   paper copy loses nothing. */
details.gate > summary { cursor: pointer; margin: 14px 0 4px; }
details.gate > summary::marker { color: var(--fg-quiet); }

.badge { display: inline-block; border: 1px solid var(--fg-quiet); border-radius: 999px;
         background: var(--head); color: var(--fg); padding: 0 7px;
         font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
         font-size: 11px; font-weight: 600; white-space: nowrap; }

.callout { margin: 10px 0; padding: 9px 12px; border: 2px solid var(--callout-edge);
           border-left-width: 7px; border-radius: 4px; background: var(--callout-bg); }
.callout .body { font-weight: 600; }
.callout.stamp { border-color: var(--callout-edge); }
.callout.alert { border-color: var(--sell); background: var(--alert-bg); }
.callout .label { display: block; text-transform: uppercase; letter-spacing: .08em;
                  font-size: 10.5px; font-weight: 700; color: var(--fg-quiet);
                  margin-bottom: 3px; }

.name-section { border: 1px solid var(--rule); border-radius: 5px; margin: 12px 0;
                padding: 2px 14px 10px; background: var(--bg); }
.name-section > summary { cursor: pointer; font-weight: 700; font-size: 15px;
                          margin: 10px -4px; padding-left: 4px; }
.name-section > summary::marker { color: var(--fg-quiet); }

footer.doc { margin-top: 34px; padding-top: 12px; border-top: 3px solid var(--rule-strong); }
footer.doc .doctrine { font-weight: 700; margin: 0 0 6px; }
footer.doc .disclaimer { color: var(--fg-quiet); font-size: 12px; margin: 0; }

@media print {
  @page { size: A4 portrait; margin: 12mm 10mm 14mm 10mm; }
  body { padding: 0; font-size: 10.5pt; background: #ffffff !important;
         color: #000000 !important; }
  .wrap { max-width: none; }
  .scroll, .scroll.tall { overflow: visible; max-height: none; }
  .scroll.tall thead th { position: static; box-shadow: none; }
  h2 { break-after: avoid; page-break-after: avoid; }
  thead { display: table-header-group; }
  tr, .callout, .kv { break-inside: avoid; page-break-inside: avoid; }
  /* Cards flatten in print: the page break is the separator there. */
  .section { background: #ffffff !important; border: none; border-top: 2px solid #000000;
             border-radius: 0; padding: 8px 0 0; margin-bottom: 14px;
             break-inside: auto; }
  /* Wide rank tables must SHRINK, never clip: auto layout + smaller type + hard wrap. */
  table { font-size: 8pt; table-layout: auto; width: 100%; }
  th, td { padding: 3px 4px; overflow-wrap: anywhere; word-break: break-word; }
  .pos .detail { font-size: 7.5pt; }
  /* REPORT-4: the contents list SURVIVES printing. The links are inert on paper, but
     the list is still the document's map, and the section titles still name the order.
     Kept off its own page — it belongs with the header it explains. */
  nav.contents { background: #ffffff !important; border: 1px solid #000000;
                 break-inside: avoid; page-break-inside: avoid; }
  nav.contents a { color: #000000 !important; border-bottom: none; }
  .record-id { color: #000000 !important; }
  .narr-block h4 { color: #000000 !important; border-bottom-color: #000000; }
  /* One per-name narration per page. */
  .name-section { break-before: page; page-break-before: always; border: none;
                  padding: 0; }
  .name-section:first-of-type { break-before: auto; page-break-before: auto; }
  details, details > summary ~ * { display: block; }
  /* Grayscale legibility: structure carries the meaning, not the hue. Every coloured
     signal above also has a WORD, so nothing is lost when the colour goes. */
  .callout, .badge, .flag { background: #ffffff !important; border-color: #000000 !important;
                            color: #000000 !important; }
  .callout { border-left-width: 7px !important; }
  .verdict-buy, .verdict-hold, .verdict-sell, .status { color: #000000 !important; }
  .house { background: #ffffff !important; }
  h3.lens-head, section.lens-card > h2, th.lens-col { color: #000000 !important;
                                                      border-color: #000000 !important; }
  td.lens-col, td.cell-buy, td.cell-hold, td.cell-sell { box-shadow: none !important; }
  td[class*="pct-"] { background: #ffffff !important; }
  tbody tr:nth-child(even) td { background: #ffffff !important; }
}
"""


# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #
def _esc(value) -> str:
    """Text-node escaping. ``quote=False`` on purpose: no user content ever lands in an
    attribute here, and leaving quotes literal keeps the document's text VERBATIM against
    the canonical .md/.txt (the no-content-dropped guarantee)."""
    return html.escape("" if value is None else str(value), quote=False)


def _local_stamp(dt: Optional[datetime]) -> str:
    """``dt`` as ``dd.mm.YYYY HH:MM TZ`` in Europe/Berlin, or "" when absent (omit, never
    invent). A naive datetime is treated as UTC — that is how run-start is captured."""
    if dt is None:
        return ""
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(DISPLAY_TZ).strftime("%d.%m.%Y %H:%M %Z")


def _norm(text: str) -> str:
    """Prose normalized for MATCHING only: markdown emphasis dropped, a leading bullet
    marker dropped, whitespace collapsed. Never used to render."""
    return " ".join(_BULLET_MARK.sub("", _MD_MARKS.sub("", text)).split())


def _inline(text: str) -> str:
    """One line of prose -> inline HTML: escaped, then ``**bold**``, a whole-span
    ``_italic_`` and ``` `code` ``` markers consumed and provenance receipts badged
    (brackets kept). No other rewriting — the model's words are the model's words."""
    out = _esc(text)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _ITALIC_SPAN.sub(r"<em>\1</em>", out)
    out = _CODE.sub(r"<code>\1</code>", out)
    return _PROVENANCE.sub(r'<span class="badge">[\1]</span>', out)


def _callout(text: str, *, kind: str = "stamp", label: str = "") -> str:
    """A warning callout carrying ``text`` VERBATIM (escaped). ``label`` is an optional
    small caps header above it."""
    head = f'<span class="label">{_esc(label)}</span>' if label else ""
    return (f'<div class="callout {kind}">{head}'
            f'<span class="body">{_esc(text)}</span></div>')


def _kv(pairs) -> str:
    """A definition grid from ``(key, value_html)`` pairs; empty values are omitted."""
    rows = "".join(f'<div class="row"><div class="k">{_esc(k)}</div>'
                   f'<div class="v">{v}</div></div>'
                   for k, v in pairs if v)
    return f'<div class="kv">{rows}</div>' if rows else ""


def _document(*, title: str, body: str) -> str:
    """The finished self-contained document: one file, inline CSS, no external request of
    any kind (no script, no link, no image, no font fetch)."""
    return ("<!DOCTYPE html>\n"
            '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{_esc(title)}</title>\n"
            f"<style>{_CSS}</style>\n</head>\n<body>\n"
            f'<div class="wrap">\n{body}\n</div>\n</body>\n</html>\n')


def _footer() -> str:
    return ('<footer class="doc">'
            f'<p class="doctrine">{_esc(DOCTRINE)}</p>'
            f'<p class="disclaimer">{_esc(DISCLAIMER)}</p>'
            "</footer>")


def _verdict_cell(cell: str) -> str:
    """The verdict cell: the bare verdict coloured by the shared palette, with the
    boundary-tie mark (``⚑ boundary (tied … )``) on its own line as a flag — the mark
    qualifies the VERDICT, so it rides in the verdict cell exactly as every other surface
    renders it (VERDICT-TIE-1)."""
    verdict, _, note = cell.partition(BOUNDARY_FLAG)
    verdict = verdict.strip()
    cls = f"verdict-{verdict.lower()}" if verdict.upper() in _VERDICT_HEX else ""
    out = f'<span class="verdict {cls}">{_esc(verdict)}</span>'
    if note:
        out += f'<span class="flag">{_esc(BOUNDARY_FLAG + note)}</span>'
    return out


def _position_cell(cell: str) -> str:
    """``#1 of 9 · score 11 (best 3 · worst 27)`` -> the ordinal bold, the rank-sum detail
    quiet. Text unchanged; a cell without the separator renders whole."""
    head, sep, tail = cell.partition(" · ")
    if not sep:
        return f'<span class="pos">{_esc(cell)}</span>'
    return (f'<span class="pos">{_esc(head)}'
            f'<span class="detail"> · {_esc(tail)}</span></span>')


def _cell_cls(column_cls: str, cell_classes, row: int, col: int) -> str:
    """Merge a COLUMN's class with an optional PER-CELL one (the verdict tint, the
    valuation-percentile tint). Styling only — no cell's text is touched."""
    extra = ""
    if cell_classes:
        extra = (cell_classes.get((row, col), "") if isinstance(cell_classes, dict)
                 else "")
    if not column_cls and not extra:
        return ""
    inner = column_cls[8:-1] if column_cls else ""      # strip ' class="' ... '"'
    return f' class="{(inner + " " + extra).strip()}"'


def _table(head: list[str], rows: list[list[str]], *, cls: str = "",
           titles: list[str] | None = None,
           col_classes: list[str] | None = None,
           cell_classes: dict | None = None) -> str:
    """A bordered table from pre-rendered HTML cells (``head`` entries are escaped).

    ``titles`` supplies an optional per-column ``title`` attribute — REPORT-1 uses it to
    keep a factor's RAW ID available on hover while the header itself reads in plain
    English. An id is never dropped; it just stops being the only thing shown.

    ``col_classes`` styles a whole COLUMN (REPORT-HTML-STYLE uses it for a lens's accent,
    so a reader can follow one lens down the page) and ``cell_classes`` maps
    ``(row, col) -> class`` for a single cell (the verdict tint, the valuation-percentile
    scale). Both are presentation only: neither can alter a cell's text."""
    titles = titles or []
    col_classes = col_classes or []

    def _cls(i: int) -> str:
        c = col_classes[i] if i < len(col_classes) else ""
        return f' class="{c}"' if c else ""

    ths = "".join(
        f'<th{_cls(i)} title="{_esc(titles[i])}">{_esc(h)}</th>'
        if i < len(titles) and titles[i] else f"<th{_cls(i)}>{_esc(h)}</th>"
        for i, h in enumerate(head))
    trs = "".join(
        "<tr>" + "".join(f"<td{_cell_cls(_cls(i), cell_classes, r, i)}>{c}</td>"
                         for i, c in enumerate(row)) + "</tr>"
        for r, row in enumerate(rows))
    # A table long enough to run off the screen gets its own scroll box, which is what
    # makes a sticky header do anything at all; short tables are left alone.
    scroll = "scroll tall" if len(rows) > 12 else "scroll"
    return (f'<div class="{scroll}"><table class="{cls}"><thead><tr>{ths}</tr></thead>'
            f"<tbody>{trs}</tbody></table></div>")


def _bullets(items) -> str:
    lis = "".join(f"<li>{i}</li>" for i in items)
    return f"<ul>{lis}</ul>" if lis else ""


# --------------------------------------------------------------------------- #
# Narration prose (+ the ⚠ stamps the pipeline appended to it)
# --------------------------------------------------------------------------- #
def _split_stamps(narrative: str) -> tuple[str, list[str]]:
    """``(prose, stamps)``. The narration-check annotations the pipeline APPENDED to the
    narrative are lifted out of the prose flow, in order, so each can render as its own
    callout. Every annotation is exactly one line opening with ``[⚠``."""
    prose: list[str] = []
    stamps: list[str] = []
    for line in (narrative or "").splitlines():
        (stamps if line.strip().startswith(_STAMP_OPEN) else prose).append(line)
    return "\n".join(prose), stamps


_TABLE_SEP = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")


def _split_table_row(line: str) -> list[str]:
    """``"| a | b |"`` -> ``["a", "b"]`` — a leading/trailing pipe is optional, matching
    every real markdown-table renderer."""
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [cell.strip() for cell in inner.split("|")]


def _table_block_html(lines: list[str]) -> str:
    """A GFM-shaped table (header row, ``| --- | --- |`` separator, data rows) -> a real
    ``<table>``, cells run through ``_inline`` so a bold mark inside one still converts.
    HTML-NARR-MD-1 — before this, a markdown table reached the export as literal
    ``"| Lens | Verdict |"`` text lines: ``_blocks`` had no table recognition at all, so
    every row fell through to the default paragraph branch."""
    head = _split_table_row(lines[0])
    body = [[_inline(c) for c in _split_table_row(row)] for row in lines[2:]]
    return _table(head, body, cls="narr-table")


def _blockquote_block_html(lines: list[str]) -> str:
    """A markdown blockquote (every line starting with ``>``) -> the SAME callout shape
    NARR-SCHEMA-1's structural banner already has CSS for (``.callout.structural``).
    HTML-NARR-MD-1 — before this, ``_blocks`` had no ``>`` recognition, so each line fell
    through to the default paragraph branch, which ESCAPES a leading ">" to "&gt;"
    rather than consuming it as the blockquote marker it is."""
    inner = [ln[1:].lstrip() for ln in lines]           # drop "> " / ">" marker only
    body: list[str] = []
    bullets: list[str] = []
    for line in inner:
        line = line.strip()
        if line[:2] == "- ":
            bullets.append(line[2:].strip())
            continue
        if bullets:
            body.append(f"<ul>{''.join(f'<li>{_inline(b)}</li>' for b in bullets)}</ul>")
            bullets.clear()
        if line:
            body.append(f"<p>{_inline(line)}</p>")
    if bullets:
        body.append(f"<ul>{''.join(f'<li>{_inline(b)}</li>' for b in bullets)}</ul>")
    return f'<div class="callout structural">{"".join(body)}</div>'


def _blocks(prose: str) -> list[tuple[str, str]]:
    """Prose -> ``[(plain_text, html)]`` blocks: blank-line-separated paragraphs, ``-``/
    ``*`` bullet lists, ``#`` headings, GFM tables and ``>`` blockquotes. Numbered lists
    are deliberately left as paragraphs (dropping a "1." would drop content)."""
    blocks: list[tuple[str, str]] = []
    para: list[str] = []
    bullets: list[str] = []

    def flush_para() -> None:
        if para:
            body = "<br>".join(_inline(line) for line in para)
            blocks.append(("\n".join(para), f"<p>{body}</p>"))
            para.clear()

    def flush_bullets() -> None:
        if bullets:
            body = "".join(f"<li>{_inline(b)}</li>" for b in bullets)
            blocks.append(("\n".join(bullets), f"<ul>{body}</ul>"))
            bullets.clear()

    lines = [raw.strip() for raw in prose.splitlines()]
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line:
            flush_para()
            flush_bullets()
            i += 1
            continue
        # A table needs its separator row IMMEDIATELY after the header — a lone "|"
        # line with no separator following was never a real table, and stays a
        # paragraph line (the default branch below still escapes it correctly).
        if line.startswith("|") and i + 1 < n and _TABLE_SEP.match(lines[i + 1]):
            flush_para()
            flush_bullets()
            table = [line, lines[i + 1]]
            i += 2
            while i < n and lines[i].startswith("|"):
                table.append(lines[i])
                i += 1
            blocks.append(("\n".join(table), _table_block_html(table)))
            continue
        if line.startswith(">"):
            flush_para()
            flush_bullets()
            quote = [line]
            i += 1
            while i < n and lines[i].startswith(">"):
                quote.append(lines[i])
                i += 1
            blocks.append(("\n".join(quote), _blockquote_block_html(quote)))
            continue
        if line.startswith("#"):
            flush_para()
            flush_bullets()
            heading = line.lstrip("#").strip()
            blocks.append((heading, f"<h4>{_inline(heading)}</h4>"))
            i += 1
            continue
        if line[:2] in ("- ", "* ", "• "):
            flush_para()
            bullets.append(line[2:].strip())
            i += 1
            continue
        flush_bullets()
        para.append(line)
        i += 1
    flush_para()
    flush_bullets()
    return blocks


def narration_reader_html(narrative: str) -> str:
    """B24-E9 - the READER view of an AI section: the prose with a small amber marker at the end of each
    flagged sentence (hover or tap for the reason) and NO list of flags below it. The full list lives
    under "Show the workings" and in the downloaded reports (``_narration_html``)."""
    from .. import ai_text_check as atc

    prose, stamps = atc.split(narrative)
    marked, swaps, unplaced = atc.mark_sentences(prose, stamps)
    blocks = _blocks(marked)
    out = "".join(block_html for _plain, block_html in blocks)
    out = atc.apply_swaps(out, swaps)
    if unplaced:             # a flag whose sentence cannot be found is still marked, at the section's end
        out += "<p>" + "".join(atc.marker_html(atc.plain_reason(s)) for s in unplaced) + "</p>"
    return out or "<p>(no narrative produced)</p>"


def _narration_html(narrative: str) -> str:
    """One name's narration as HTML: the prose in blocks, each ⚠ stamp rendered as a
    warning callout DIRECTLY AFTER the block stating the sentence it quotes. A stamp whose
    claim cannot be located lands at the end of the section — never dropped."""
    prose, stamps = _split_stamps(narrative)
    blocks = _blocks(prose)
    pending = list(stamps)
    out: list[str] = []
    for plain, block_html in blocks:
        out.append(block_html)
        if not pending:
            continue
        haystack = _norm(plain)
        still: list[str] = []
        for stamp in pending:
            m = _STAMP_CLAIM.search(stamp)
            claim = _norm(m.group(1)) if m else ""
            if claim and claim in haystack:
                out.append(_callout(stamp, label="AI text check"))
            else:
                still.append(stamp)
        pending = still
    out.extend(_callout(s, label="AI text check") for s in pending)
    if not out:
        out.append("<p>(no narrative produced)</p>")
    return "".join(out)


# --------------------------------------------------------------------------- #
# Merged multi-lens report (REPORT-2)
# --------------------------------------------------------------------------- #
def multi_strategy_report_html(multi_result, *,
                               run_start: Optional[datetime] = None) -> str:
    """ONE self-contained HTML report for a whole multi-lens run (REPORT-2).

    Ticking three extra lenses used to write FOUR standalone documents, each repeating
    the same cohort, the same prices and the same valuation band. This is the single
    document: one header, the rules each lens applied, ONE verdict table with a column
    per lens, the per-NAME facts stated once, then only what genuinely differs per lens,
    and one footer.

    Reads the SAME builders every other surface does — ``multi_strategy_grid_rows`` for
    the table, ``rules_applied`` / ``exclusion_rows`` / ``provenance_sentences`` per
    lens, ``valuation_band_table`` for the shared price-and-valuation section — so this
    export can never carry a value the Run tab or the markdown does not.

    RECORD LAYER UNTOUCHED: only the human-facing report merges. Each strategy's run is
    still frozen individually under ``runs/`` and still grades individually."""
    from ..pipeline import (
        evidence_gaps_clean_note,
        EVIDENCE_GAPS_NOTE,
        EVIDENCE_GAPS_TITLE,
        PROVENANCE_SECTION_TITLE,
        RULES_SECTION_TITLE,
        VERDICT_TABLE_NOTE,
        verdict_table_note,
        VERDICT_TABLE_TITLE,
        evidence_gaps,
        exclusion_rows,
        multi_strategy_grid_rows,
        multi_header_line,
        multi_summary_line,
        narration_issues,
        narration_structs,
        provenance_sentences,
        report_sections,
        fetch_guard_line,
        floor_override_line,
        lens_asks,
        lens_detail,
        rules_applied,
        union_valuation_band_table,
    )
    from ..data.adapter import display_name
    from ..download_names import slugify as _slug
    from ..narration_render import narration_html, split_stamps
    from ..report_language import cohort_title, label_with_id

    m = multi_result.meta
    ids = multi_result.strategy_ids
    names = multi_result.strategy_names
    cohort = label_with_id(m.get("universe_name", ""), m.get("universe_id") or "adhoc")
    # REPORT-4 part 4: the HUMAN description leads; the record id stays beside it, muted.
    title = cohort_title(m, n_lenses=len(ids))
    stamp = _local_stamp(run_start)
    mode = m.get("council_mode", "")
    mode_phrase = ("ranker only, no AI commentary" if mode == "ranker-only"
                   else f"{mode} commentary" if mode else "")
    lens_labels = [label_with_id(names.get(sid) or sid, sid) for sid in ids]
    structs = narration_structs(multi_result)
    issues = narration_issues(multi_result)
    sections = report_sections(multi_result)
    parts: list[str] = []

    # ----- 1: ONE header, not one per lens.
    parts.append(
        '<header class="doc">'
        '<p class="kicker">Aristos · universe run · multi-lens</p>'
        f"<h1>{_esc(title.headline)}</h1>"
        + _kv([
            ("Cohort", _esc(f'{cohort} — {plural(m.get("universe_size", 0), "name")}')),
            ("Lenses", "<br>".join(_esc(lbl) for lbl in lens_labels)),
            # FLOOR-1: directly under the lenses, and only when the cohort was widened
            # for this run. _kv omits an empty value, so a no-override report is
            # byte-identical to before.
            ("Company size floor", _esc(floor_override_line(m))),
            ("Run", _esc(" — ".join(p for p in (stamp, mode_phrase) if p))),
        ])
        + f'<p class="house">{_esc(multi_header_line(multi_result))}</p>'
        f'<p class="summary">{_esc(multi_summary_line(multi_result))}</p>'
        + _compact_rules_html(multi_result)
        + _contents(sections)
        + "</header>")

    # ----- 1a (READER-1): the note that explains the rest, above everything it explains.
    parts.append(_reader_section(getattr(multi_result, "reader", None)))

    # ----- 1b (SHORTLIST-1): the answer, before the evidence for it. Derived from the
    # grid below; the grid itself is untouched.
    parts.append(_shortlist_section(getattr(multi_result, "lens_agreement", None)))

    # ----- 2 (REPORT-4): what the run could NOT see, BEFORE any prose resting on what it
    # could. Rendered even when empty — an absent section is indistinguishable from a
    # feature that was never switched on.
    gaps = evidence_gaps(multi_result)
    parts.append(f'<section class="section" id="gaps"><h2>{_esc(EVIDENCE_GAPS_TITLE)}</h2>'
                 f'<p class="note">{_esc(EVIDENCE_GAPS_NOTE)}</p>')
    if gaps:
        parts.append(_table(
            ["Channel", "Why it is dark", "Names affected"],
            [[_esc(g["channel"]), _esc(g["reason"]), _esc(", ".join(g["names"]))]
             for g in gaps], cls="ranked"))
    else:
        parts.append(f'<p class="clean">{_esc(evidence_gaps_clean_note(multi_result))}</p>')
    parts.append("</section>")

    # ----- 3: THE VERDICT TABLE — the answer, and the heart of the merged report.
    rows, head = multi_strategy_grid_rows(multi_result)
    parts.append(f'<section class="section" id="verdicts">'
                 f'<h2>{_esc(VERDICT_TABLE_TITLE)}</h2>'
                 f'<p class="note">{_esc(verdict_table_note(multi_result))}</p>')
    if rows:
        body = [[f'<strong>{_esc(row[head[0]])}</strong>']
                + [_verdict_grid_cell(row[c]) for c in head[1:]]
                for row in rows]
        # Column 0 is the Name; columns 1..len(ids) are the lenses, each carrying its own
        # accent; the trailing Rank-sum / Graded by columns are cohort-wide, not a lens's.
        col_classes = [""] + [f"lens-col {lens_class(i)}" for i in range(len(ids))]
        # ...and a RANKED cell also carries its verdict's tint. The verdict WORD is
        # rendered regardless, so nothing depends on the colour.
        cell_classes = {}
        for r, row in enumerate(rows):
            for c, column in enumerate(head):
                verdict = verdict_of_cell(row[column])
                if verdict:
                    cell_classes[(r, c)] = f"cell-{verdict.lower()}"
        parts.append(_table(head, body, cls="ranked", col_classes=col_classes,
                            cell_classes=cell_classes))
    else:
        parts.append('<p class="note">(no names reported)</p>')
    parts.append("</section>")

    # ----- 4 (NARR-UNION-1 + REPORT-4): ONE narration per NAME, over the union of every
    # lens's BUYs — the WHY, straight after the answer. Rendered from the narrator's
    # FIELDS into real headings, tables and lists; a pre-REPORT-4 record with only prose
    # still renders through the paragraph path.
    if multi_result.narratives:
        basis = m.get("narration_basis", "")
        count = m.get("narrated_count", len(multi_result.narratives))
        parts.append('<section class="section" id="narration"><h2>Narration</h2>'
                     f'<p class="note">{_esc(count)} '
                     f'name{"s" if count != 1 else ""} narrated — {_esc(basis)}. ONE '
                     "section per NAME: a name several lenses bought is narrated once, "
                     "with each lens's verdict attributed. The narrator explains the "
                     "ranker's verdicts; it never weighs the lenses against each "
                     "other.</p>")
        kids = {c["title"]: c["anchor"]
                for s in sections if s["anchor"] == "narration" for c in s["children"]}
        for ticker, text in multi_result.narratives.items():
            display = next((r.display for r in multi_result.rows if r.ticker == ticker),
                           ticker)
            anchor = kids.get(display, "")
            narration = structs.get(ticker)
            if narration is not None:
                _, stamps = split_stamps(text)
                # NARR-SCHEMA-1: the same banner the .md carries, from the same
                # validator — the two surfaces cannot disagree about whether a narration
                # met its contract.
                inner = narration_html(narration, anchor_prefix=anchor, stamps=stamps,
                                       issues=issues.get(ticker, []),
                                       callout=_callout)
            else:
                inner = _narration_html(text)
            parts.append(f'<details class="name-section" open id="{_esc(anchor)}">'
                         f"<summary>{_esc(display)}</summary>{inner}</details>")
        parts.append(_narration_line_html(multi_result))
        parts.append("</section>")

    # ----- 5: the per-NAME facts, ONCE — they do not vary by lens.
    # BAND-2: over the UNION of every lens's ranked names, not the first lens's. A lens
    # attaches a band only to what it ranked, so reading the first one made the section's
    # size an accident of lens order (2 rows vs 81 on the same 121-name cohort).
    band_table = union_valuation_band_table(multi_result) if ids else None
    if band_table is not None:
        parts.append(_band_section(band_table, anchor="band"))

    # ----- 6: rules applied, ONE sub-block per lens — each screens on its own rules.
    # REFERENCE material, so it sits after the answer rather than in front of it.
    parts.append('<section class="section" id="rules">'
                 f'<h2>{_esc(RULES_SECTION_TITLE)} — by lens</h2>'
                 '<p class="note">Each lens screens on its own rules; a name excluded by '
                 "one may be ranked by another. The rules below are read from the "
                 "lenses that actually ran.</p>")
    for i, (sid, label) in enumerate(zip(ids, lens_labels)):
        rules = rules_applied(multi_result.results[sid])
        # Each lens keeps ONE accent in all three places it appears — here, its column in
        # the verdict grid, and its detail section — so a reader can follow it down the
        # page by colour. The label is always present; the colour only helps find it.
        parts.append(f'<h3 class="lens-head {lens_class(i)}" '
                     f'id="rules-{_esc(_slug(sid))}">{_esc(label)}</h3>')
        # CAPTION-1: the question this lens asks, directly under its name — so the rules
        # below read as the answer to something rather than as a list of floors.
        _asks = lens_asks(multi_result.results[sid])
        if _asks:
            parts.append(f'<p class="note">{_esc(_asks)}</p>')
        if rules is None:
            parts.append('<p class="note">This lens declares no screen.</p>')
            continue
        show_basis = any(r.measured for r in rules.rules)
        head = ["Rule", "Limit", "What it did"] + (["Measured on"] if show_basis else [])
        body = [[f'<strong>{_esc(r.label)}</strong>',
                 _esc(r.threshold_phrase), _esc(r.tally)]
                + ([_esc(r.measured or "—")] if show_basis else [])
                for r in rules.rules]
        parts.append(f'<p class="note"><strong>{_esc(rules.screen_heading)}</strong><br>'
                     f'{_esc(rules.screen_note)}</p>'
                     + (_table(head, body, cls="ranked") if body else "")
                     + _bullets(_esc(line) for line in rules.ranker_lines))
    parts.append("</section>")

    # ----- 7: what DOES vary per lens, clearly headed by lens — and carrying that lens's
    # accent on its own card, so the section is findable by the same colour as its column.
    parts.append('<div id="detail"></div>')
    for i, (sid, label) in enumerate(zip(ids, lens_labels)):
        res = multi_result.results[sid]
        parts.append(f'<section class="section lens-card {lens_class(i)}" '
                     f'id="detail-{_esc(_slug(sid))}">'
                     f"<h2>{_esc(label)} — detail</h2>"
                     + _lens_detail_html(lens_detail(res, strategy_id=sid, label=label)))
        parts.append("</section>")

    # ----- 8 (GLOSSARY-1): the LAST section before the footer, built from the
    # registries and holding only the terms this report actually uses.
    parts.append(_glossary_html(multi_result, "\n".join(parts)))

    # ----- 9: ONE common footer.
    parts.append(_footer())
    return _document(title=title.headline, body="\n".join(parts))




def _reader_section(reader) -> str:
    """READER-1 — five short paragraphs, or the one line saying why there are none."""
    from ..reader import (READER_SECTION_NOTE, READER_SECTION_TITLE,
                          reader_paragraphs)

    if reader is None:
        return ""
    body = [f'<section class="section" id="summary">'
            f"<h2>{_esc(READER_SECTION_TITLE)}</h2>"]
    if not reader.available:
        body.append(f'<p class="note">{_esc(reader.note)}</p></section>')
        return "".join(body)
    for lead, text in reader_paragraphs(reader.summary):
        body.append(f"<p><strong>{_esc(lead)}</strong> {_esc(text)}</p>")
    body.append(f'<p class="note">{_esc(READER_SECTION_NOTE)}</p></section>')
    return "".join(body)



def _narration_line_html(result) -> str:
    """NARR-2 — the one line under the narrations: which rule chose them, how many met it,
    and which qualified names are not here.

    Never silent. A run that narrated nothing says which rule produced nothing and how to
    get more, because an absent section is indistinguishable from a feature that was never
    switched on — the corollary this repo has paid for twice."""
    from ..pipeline import narration_line, narration_was_requested

    plan = (getattr(result, "meta", None) or {}).get("narration")
    if not plan or not narration_was_requested(plan):
        return ""
    out = [f'<p class="note">{_esc(narration_line(plan))}</p>']
    missing = plan.get("not_narrated") or []
    if missing:
        out.append('<p class="note">Met the rule and not narrated:</p>' + _bullets(
            f'<strong>{_esc(m["name"])}</strong> — {m["buy_votes"]} BUY vote'
            f'{"s" if m["buy_votes"] != 1 else ""}'
            + (f' · {_esc(" · ".join(m["marks"]))}' if m.get("marks") else "")
            for m in missing))
    return "".join(out)


def _shortlist_section(ag) -> str:
    """SHORTLIST-3 — the agreement table. Placed directly after the summary and BEFORE the
    verdict grid, because it is the answer the grid is evidence for.

    ONE builder (``pipeline.agreement_table``) feeds this, the markdown and the Run tab, so
    the three cannot show different names or a different order."""
    from ..pipeline import lens_agreement_table

    if ag is None:
        return ""
    body = [f'<section class="section" id="shortlist">'
            f"<h2>{_esc(ag.title)}</h2>"]
    body.append(f'<p class="note">{_esc(ag.rule_sentence)}</p>')
    if not ag.available:
        return "".join(body) + "</section>"
    # The overlap note goes ABOVE the table, because it changes how every row in it should
    # be read: two lenses ranking on the same factors make one view look like two votes.
    if ag.overlap_note:
        body.append(f'<p class="flag">{_esc(ag.overlap_note)}</p>')
    cols, rows = lens_agreement_table(ag)
    if rows:
        body.append(_table(
            cols,
            [[f'<span class="badge">{_esc(r[c])}</span>' if c == "Marks" and r[c]
              else _esc(r[c]) for c in cols] for r in rows],
            cls="ranked"))
    else:
        body.append('<p class="note">No name was rated BUY by any voting lens. That is a '
                    'result, not a gap.</p>')
    if ag.no_buy_count:
        one = ag.no_buy_count == 1
        body.append(f'<p class="note">{ag.no_buy_count} name{"" if one else "s"} '
                    f'had no BUY from any lens, and '
                    f'{"is" if one else "are"} not listed here.</p>')
    body.append("</section>")
    return "".join(body)


def _band_section(band_table, *, anchor: str = "") -> str:
    """The price-and-valuation section — the ONE builder both the single-lens and the
    merged report render, so they cannot drift apart visually any more than they can
    numerically.

    The percentile column carries a diverging BACKGROUND tint keyed off the one-word
    gloss already inside the cell, so the cheap and dear ends of a cohort are findable at
    a glance. The word is always there; the tint only speeds the eye. This section was
    the one being missed entirely for want of a visual edge, so it gets the card
    treatment like every other major section."""
    columns = band_table.columns
    body = [[_esc(row[c]) if i else f"<strong>{_esc(row[c])}</strong>"
             for i, c in enumerate(columns)]
            for row in band_table.rows]
    pct_col = columns.index("Percentile") if "Percentile" in columns else None
    cell_classes = {}
    if pct_col is not None:
        for r, row in enumerate(band_table.rows):
            tint = percentile_class(row[columns[pct_col]])
            if tint:
                cell_classes[(r, pct_col)] = tint
    # Hoisted out of the f-string on purpose: nesting an f-string that REUSES the outer
    # delimiter inside a replacement field is PEP 701, i.e. Python 3.12+. CI runs 3.11
    # too, where it is a SyntaxError at import — so the whole module failed to collect
    # while a 3.14 dev box saw nothing wrong.
    anchor_attr = f' id="{anchor}"' if anchor else ""
    return (f'<section class="section"{anchor_attr}>'
            f"<h2>{_esc(band_table.title)}</h2>"
            f'<p class="note">{_esc(band_table.intro)}</p>'
            + _table(columns, body, cls="ranked", cell_classes=cell_classes)
            + _bullets(_esc(n) for n in band_table.footnotes)
            + "</section>")


# FACTOR-MARK-2 — the coverage marker FACTOR-MARK-1 appends to a ranked cell
# (MultiStrategyCell.factor_note: " · ranked on 2 of 3 factors"). It lands AFTER the
# verdict word, so the "ends with · VERDICT" test below stopped matching and every marked
# cell silently lost BOTH its tint and its coloured verdict word — 37 cells in the
# Forensic column of the 2026-09-16 oil run (9 BUY, 17 HOLD, 11 SELL), which is exactly
# the population a reader most needs to see graded. The marker is stripped before the
# verdict is looked for, and re-rendered afterwards as a muted suffix, so the house rule
# (colour AND word, never one without the other) holds for a marked cell too.
_CELL_MARKER = re.compile(r" · ranked on \d+ of \d+ factors$")


# --------------------------------------------------------------------------- #
# DETAIL-1 — the per-lens detail section
# --------------------------------------------------------------------------- #
# The section used to be one bullet per excluded name, each repeating the rule sentence in
# full: 91 bullets on the oil run's income lens, the same free-cash-flow sentence 27 times
# running. Grouped by the rule that fired, the same data answers the question a reader
# actually has — which rule removed the most names, how far each missed, and which misses
# were close enough to argue about.
#
# Every number here is the run's own; ``lens_detail`` decides the arrangement and this
# decides the markup. Nothing is re-graded on either side of that line.


def _badges_html(badges) -> str:
    """The row's badges. Each is a WORD, never a bare colour or symbol on its own — the ⚠
    badge carries its figure, and the lens states what the symbol means once, above."""
    return " ".join(f'<span class="badge">{_esc(b)}</span>' for b in badges)


def _detail_heading_html(group) -> str:
    """The group's heading LINE (no tag): title, the criterion id, the rule stated once,
    the count. The id is muted and second, never dropped — the old bullet list carried it
    on every name for auditability, and grouping must not cost the document that."""
    return (f"<strong>{_esc(group.title)}</strong>"
            + (f' <span class="muted">· rule: {_esc(group.rule)}</span>'
               if group.rule else "")
            + f' · <strong>{group.count} '
              f'{"name" if group.count == 1 else "names"}</strong>'
            + (f' <span class="muted">({_esc(group.note)})</span>' if group.note else ""))


def _detail_group_html(group) -> str:
    """One rule's group: the rule stated ONCE, what it is for, then the names it removed
    with their measured values, worst miss first."""
    heading = _detail_heading_html(group)
    why = f'<p class="note">{_esc(group.why)}</p>' if group.why else ""

    # A GATE collapses. A name under the size floor or outside the lens's scope was never
    # tested on anything below, so its margin is not a reading, and a reader scanning the
    # rules should not have to pass thirty of them to reach the first one. Present, named
    # and one click away — never dropped, because "excluded and not shown" is the shape of
    # a report you cannot check.
    if group.keeps_sentences:
        return (f"<h3>{heading}</h3>"
                + _bullets(f"<strong>{_esc(n.name)}</strong> — {_esc(n.sentence)}"
                           for n in group.names))
    if group.is_gate:
        names = ", ".join(_esc(n.name) for n in group.names)
        return (f'<details class="gate"><summary>{heading}</summary>'
                f'{why}<p class="note">{names}</p></details>')

    body = [[_esc(n.name), _esc(n.measured) or "—", _badges_html(n.badges)]
            for n in group.names]
    return (f"<h3>{heading}</h3>" + why
            + _table(["Name", "Measured", "Note"], body, cls="ranked",
                     col_classes=["", "num", ""]))


def _lens_detail_html(detail) -> str:
    """One whole lens detail section (without its own <section> wrapper)."""
    from ..pipeline import DETAIL_SOURCES_TITLE

    out = []
    if detail.asks:
        out.append(f'<p class="note">{_esc(detail.asks)}</p>')          # CAPTION-1
    out.append(f'<p class="note"><strong>{_esc(detail.headline)}</strong></p>')
    if detail.badge_note:
        out.append(f'<p class="note">{_esc(detail.badge_note)}</p>')

    for group in detail.groups:
        out.append(_detail_group_html(group))

    if detail.unrateable:
        out.append(f"<h3>No usable data — no verdict · {len(detail.unrateable)}</h3>"
                   + _bullets(f"<strong>{_esc(name)}</strong> — {_inline(why)}"
                              for name, why in detail.unrateable))
    if detail.fetch_errors:
        out.append("<h3>Data fetch failed — re-run to recover · "
                   f"{len(detail.fetch_errors)}</h3>"
                   + _bullets(f"<strong>{_esc(name)}</strong> — {_inline(why)}"
                              for name, why in detail.fetch_errors))
    if detail.sources:
        # The provenance block as a TABLE. The same counts, and the same abstention
        # reasons, that it stated as one sentence per factor — which read well for three
        # factors and badly for ten, while the thing a reader checks (did this factor
        # abstain, and for whom) is a column.
        out.append(f"<h3>{_esc(DETAIL_SOURCES_TITLE)}</h3>"
                   + _table(["Factor", "Real data", "Abstained"],
                            [[_esc(r["label"]), _esc(r["real"]), _esc(r["abstained"])]
                             for r in detail.sources],
                            cls="ranked", col_classes=["", "num", ""]))
    return "".join(out)


# CHECK-WORDS-1 — a check lens's words, and the verdict each one is coloured as.
#
# `doubted` red, `clean` green, `no concern` neutral: the same three signals the palette
# already carries, because a reader scanning the grid for trouble should find it in the
# same colour whichever column it is in. The WORD is what says which question was asked,
# and the word is always rendered — colour is never the only carrier of meaning.
_CHECK_CELL_WORDS = {"clean": "BUY", "no concern": "HOLD", "doubted": "SELL"}


def verdict_of_cell(text: str) -> str:
    """The VERDICT a verdict-grid cell carries ("BUY"/"HOLD"/"SELL"), or "" for a cell on
    another axis (excluded / no data / fetch failed) — those are not verdicts and must
    never be coloured as though they were.

    A CHECK lens's cell reads "clean" / "no concern" / "doubted" (CHECK-WORDS-1); this
    returns the verdict each is coloured AS, while ``_verdict_grid_cell`` renders the word
    the cell actually carries."""
    body = _CELL_MARKER.sub("", text or "")
    for verdict in _VERDICT_HEX:
        if body.endswith(f"· {verdict}"):
            return verdict
    for word, verdict in _CHECK_CELL_WORDS.items():
        if body.endswith(f"· {word}"):
            return verdict
    return ""


def _cell_word(text: str) -> str:
    """The word the cell ends with — "BUY" or "doubted" — so the rendered span carries the
    lens's own vocabulary rather than the verdict it is coloured as."""
    body = _CELL_MARKER.sub("", text or "")
    for word in list(_VERDICT_HEX) + list(_CHECK_CELL_WORDS):
        if body.endswith(f"· {word}"):
            return word
    return ""


def _verdict_grid_cell(text: str) -> str:
    """One verdict-table cell. A ranked cell's VERDICT keeps the shared palette so the
    grid scans by colour exactly as the single-lens ranked table does; every other axis
    (excluded / no data / fetch failed) stays plain, because it is not a verdict. The
    verdict WORD is always rendered — colour is never the only carrier of meaning."""
    verdict = verdict_of_cell(text)
    if not verdict:
        return f'<span class="mono">{_esc(text)}</span>'
    marker = ""
    found = _CELL_MARKER.search(text)
    if found:
        marker, text = found.group(0), text[: found.start()]
    word = _cell_word(text) or verdict          # CHECK-WORDS-1: the lens's own word
    head = text[: -len(word)]
    return (f'<span class="mono">{_esc(head)}</span>'
            f'<span class="verdict verdict-{verdict.lower()}">{_esc(word)}</span>'
            + (f'<span class="mono muted">{_esc(marker)}</span>' if marker else ""))


# --------------------------------------------------------------------------- #
# Universe report
# --------------------------------------------------------------------------- #
def universe_report_html(result, *, run_start: Optional[datetime] = None,
                         strategy_display_name: str = "") -> str:
    """The universe run as ONE self-contained HTML file (REPORT-HTML-1).

    Renders the same content as the canonical markdown download — ranked table with the
    ``#N of M · score (best/worst)`` positions and boundary-tie flags, factor integrity,
    screen basis, exclusions, unrateables, fetch failures, and one collapsible section per
    narrated name with its ⚠ narration-check stamps — plus the header/footer a file sent to
    a third party needs. ``result`` is never mutated.

    ``strategy_display_name`` falls back to ``meta['rank_strategy_name']`` and then to the
    strategy id; ``run_start`` is omitted from the header when absent (never invented).
    """
    from ..pipeline import (
        PROVENANCE_SECTION_NOTE,
        PROVENANCE_SECTION_TITLE,
        RULES_SECTION_TITLE,
        exclusion_rows,
        header_lines,
        floor_override_line,
        lens_asks,
        lens_detail,
        provenance_sentences,
        rules_applied,
        summary_line,
        untested_rule_notes,
        used_symbol_notes,
        valuation_band_table,
    )
    from ..rank_engine import factor_column_label
    from ..report_language import label_with_id, score_gloss_with_note
    from ..data.adapter import display_name

    m = result.meta
    strategy_id = m.get("rank_strategy_id", "")
    title_name = (strategy_display_name or m.get("rank_strategy_name") or strategy_id)
    stamp = _local_stamp(run_start)
    parts: list[str] = []

    # ----- header (REPORT-1): the human names lead, the ids stay beside them as the
    # stable record keys, and the run id sits last. It used to be machine ids only —
    # "strategy conservative_plus_v1, universe defensive_income_16_v1" — which told a
    # reader nothing while hiding names the UI already knew.
    universe_label = label_with_id(m.get("universe_name", ""),
                                   m.get("universe_id") or "adhoc")
    mode = m.get("council_mode", "")
    mode_phrase = ("ranker only, no AI commentary" if mode == "ranker-only"
                   else f"{mode} commentary" if mode else "")
    parts.append(
        '<header class="doc">'
        '<p class="kicker">Aristos · universe run</p>'
        f"<h1>{_esc(title_name)} — {_esc(universe_label)}</h1>"
        + _kv([
            ("Universe", _esc(f'{universe_label} — '
                              f'{plural(m.get("universe_size", "—"), "name")}')),
            ("Lens", _esc(label_with_id(m.get("rank_strategy_name", ""),
                                            strategy_id))),
            # CAPTION-1 — the question this lens asks, beside the lens's name. _kv omits
            # an empty value, so a strategy with no `asks` renders no row at all.
            ("Asks", _esc(lens_asks(result))),
            ("Screen", _esc(label_with_id(m.get("screen_strategy_name", ""),
                                          m.get("screen_strategy_id", "")))),
            # FLOOR-1 — see the multi-lens header above; empty unless overridden.
            ("Company size floor", _esc(floor_override_line(m))),
            # A missing run timestamp is OMITTED, never guessed — so the mode must not
            # be left dangling behind an em-dash with nothing before it.
            ("Run", _esc(" — ".join(p for p in (stamp, mode_phrase) if p))),
            ("Ranked", _esc(f'{m.get("ranked_count", "—")} of '
                            f'{plural(m.get("universe_size", "—"), "name")}')),
            ("Shortlist", "" if m.get("ranker_only") else
             _esc(f'{plural(len(m.get("shortlist") or []), "name")} · estimated cost '
                  f'{format_money(float(m.get("est_cost") or 0.0), "USD")} · narrating '
                  f'{m.get("narrate_coverage", "buys_only")}')),

        ])
        + f'<p class="house">{_esc(result.header)}</p>'
        f'<p class="summary">{_esc(summary_line(result))}</p>'
        "</header>")

    # ----- 0 (REPORT-1): RULES APPLIED — every rule this run applied, with its limit in
    # plain English and what it actually did, BEFORE any result. The header used to name
    # only the screen's id; the screen holds six rules and the report named at most the
    # three something failed, so a reader could not tell what had been filtered or why.
    rules = rules_applied(result)
    if rules is not None:
        show_basis = any(r.measured for r in rules.rules)
        head = ["Rule", "Limit", "What it did"] + (["Measured on"] if show_basis else [])
        body = [[f'<strong>{_esc(r.label)}</strong>',
                 _esc(r.threshold_phrase), _esc(r.tally)]
                + ([_esc(r.measured or "—")] if show_basis else [])
                for r in rules.rules]
        parts.append(f'<section class="section"><h2>{_esc(RULES_SECTION_TITLE)}</h2>'
                     f'<p class="note"><strong>{_esc(rules.screen_heading)}</strong><br>'
                     f'{_esc(rules.screen_note)}</p>'
                     + (_table(head, body, cls="ranked") if body else "")
                     + _bullets(_esc(line) for line in rules.ranker_lines)
                     + "</section>")

    # ----- 1: the ranked table — the verdict of record. Factor columns are headed by
    # the factor's HUMAN label (the raw id rides in the header's title attribute, on
    # hover, and in a footnote), each cell carries the rank AND the value it was ranked
    # on, and the score gloss is stated ONCE above the table rather than in every row.
    rows, factor_names = ranked_table_rows(result.ranked, result.names)
    parts.append('<section class="section"><h2>Ranked — the verdict of record</h2>')
    if rows:
        labels = [factor_column_label(f) for f in factor_names]
        n_factors = next((len(r.factor_ranks) for r in result.ranked if r.factor_ranks),
                         0)
        parts.append('<p class="note">'
                     + _esc(score_gloss_with_note(n_factors, len(result.ranked)))
                     + "</p>")
        head = ["Position (score)", "Name", "Verdict", *labels]
        body = [[_position_cell(r["Position (score)"]),
                 _esc(r["Name"]),
                 _verdict_cell(r["Verdict"]),
                 *[f'<span class="mono">{_esc(r[lab])}</span>' for lab in labels]]
                for r in rows]
        parts.append(_table(head, body, cls="ranked", titles=[
            "", "", "", *factor_names]))
        symbols = used_symbol_notes(result)
        if symbols:
            parts.append(_bullets(f'<code>{_esc(sym)}</code> — {_esc(note)}'
                                  for sym, note in symbols))
    else:
        parts.append('<p class="note">(no names survived the screen)</p>')
    parts.append("</section>")

    # ----- 1b (REPORT-1): a rule that could NOT BE TESTED, in words, next to the
    # verdict it qualifies. Passing four of five rules with the fifth untestable is
    # materially different from passing all five, and it used to be a bare dagger.
    untested = untested_rule_notes(result)
    if untested:
        parts.append('<section class="section">'
                     "<h2>Rules that could not be tested</h2>"
                     '<p class="note">These names PASSED the screen — a rule that '
                     "cannot be evaluated never excludes anyone (an abstention is not a "
                     "failure) — but one of its rules returned no answer for them at "
                     "all, so their pass is thinner than it looks.</p>"
                     + _bullets(
                         f'<strong>{_esc(n["name"])}</strong> — {_esc(n["verdict"])} — '
                         + _esc(f'{len(n["rules"])} rule'
                                f'{"s" if len(n["rules"]) != 1 else ""} could not be '
                                "tested: ")
                         + _esc("; ".join(n["rules"]))
                         for n in untested)
                     + "</section>")

    # ----- 2 (REPORT-1): "Where the numbers came from" — was "Factor integrity", which
    # was internal jargon. Same counts, one sentence per factor.
    # DETAIL-1: the same three-column table the merged report renders, from the same
    # builder. The counts a reader is actually checking — did this factor abstain, and
    # for whom — are columns, not the tail of a sentence.
    detail = lens_detail(result)
    if detail.sources:
        parts.append(f'<section class="section"><h2>{_esc(PROVENANCE_SECTION_TITLE)}</h2>'
                     f'<p class="note">{_esc(PROVENANCE_SECTION_NOTE)}</p>'
                     + _table(["Factor", "Real data", "Abstained"],
                              [[_esc(r["label"]), _esc(r["real"]), _esc(r["abstained"])]
                               for r in detail.sources],
                              cls="ranked", col_classes=["", "num", ""])
                     + "</section>")

    # ----- 2b: price and valuation as ONE table (PRICE-2, extended by REPORT-1). The
    # separate "Share price & 52-week position" section is FOLDED IN as columns, so the
    # same names are not listed twice; the price columns are never gated by the band
    # toggle, so a band-off run still renders this table (without the band's columns).
    # Same cells as every other surface (pipeline.valuation_band_table).
    band_table = valuation_band_table(result)
    if band_table is not None:
        parts.append(_band_section(band_table))

    # ----- 3/4/5: the three NON-verdict axes, each kept distinct.
    def _reason_list(pairs) -> str:
        return _bullets(
            f'<strong>{_esc(display_name(t, result.names.get(t)))}</strong> — '
            + _inline(why) for t, why in pairs)

    if result.excluded:
        # REPORT-1: a sentence naming the rule, the observed value and the limit, both
        # in their proper units — the raw form was "screen: min_dividend_yield (observed
        # 0.009547 vs threshold 0.015)". The criterion id stays, muted, for auditability,
        # and a warning flag becomes its own marked line rather than brackets mid-sentence.
        parts.append('<section class="section">'
                     f"<h2>Excluded — did not pass a rule, so was never ranked · "
                     f"{len(result.excluded)}</h2>"
                     + (f'<p class="note">{_esc(detail.badge_note)}</p>'
                        if detail.badge_note else "")
                     + "".join(_detail_group_html(g) for g in detail.groups)
                     + "</section>")
    if result.unrateable:
        parts.append('<section class="section">'
                     f"<h2>Unrateable — no data, no verdict · {len(result.unrateable)}</h2>"
                     '<p class="note">A SELL implies an assessment was made; these names '
                     "had no usable data at all, so they receive NO verdict and reached no "
                     "model.</p>" + _reason_list(result.unrateable) + "</section>")
    if getattr(result, "fetch_errors", None):
        parts.append('<section class="section">'
                     f"<h2>Fetch failed — rerun · {len(result.fetch_errors)}</h2>"
                     '<p class="note">A TRANSIENT fetch failure (rate limit / timeout / '
                     "server error) that did not recover after retries — a live ticker, "
                     "NOT delisted and NOT unrateable. Re-run to recover these.</p>"
                     + _reason_list(result.fetch_errors) + "</section>")

    # ----- 6: narration — the LLM's entire job in narrator mode, stamps attached.
    if result.narratives:
        verdict_of = {r.ticker: r.verdict.upper() for r in result.ranked}
        parts.append('<section class="section"><h2>Narration — non-judging</h2>')
        for ticker, text in result.narratives.items():
            disp = display_name(ticker, result.names.get(ticker))
            verdict = verdict_of.get(ticker, "")
            head = f"{disp}{' · ' + verdict if verdict else ''}"
            parts.append(f'<details class="name-section" open>'
                         f"<summary>{_esc(head)}</summary>"
                         f"{_narration_html(text)}</details>")
        parts.append("</section>")

    parts.append(_footer())
    title = f"Universe run — {title_name}" + (f" — {stamp}" if stamp else "")
    return _document(title=title, body="\n".join(parts))


_JOIN = "\n"


def company_report_html(report, *, run_start: Optional[datetime] = None) -> str:
    """The Company Report as ONE self-contained HTML file, in the page order (TAB-MERGE-1
    part 2 commit 2): summary (if asked for), agreement headline and table, each lens's
    vote, valuation band, price and cash, absolute readings, analyst forecasts, council
    opinion (if asked for), the full peers table, Sources. The same objects the text
    export prints, so the two cannot drift."""
    from ..company_check import company_sources
    from ..company_report import HOUSE_LINE, NO_LENS_REASON, OUTSIDE_TESTED_RANGE_LINE
    from ..peer_table import rank_columns

    c = report.check
    stamp = _local_stamp(run_start)
    header_tail = f'<p class="house">{_esc(HOUSE_LINE)}</p>'
    parts = ['<header class="doc"><p class="kicker">Aristos · company report · one '
             "company against its peer group</p>"
             f"<h1>{_esc(report.display)}</h1>"
             + _kv([("lenses", ", ".join(_esc(v.label) for v in report.votes) or "none ticked"),
                    ("run", _esc(stamp))])
             + header_tail + "</header>"]
    if report.unrateable:
        parts.append(_callout(f"UNRATEABLE — {c.data_integrity.note}. No data, so no votes and no "
                              "readings.", kind="alert"))
        parts.append(_footer())
        return _document(title=f"Company Report — {report.display}", body=_JOIN.join(parts))

    parts.append(_story_html(report))

    if report.council_opinion is not None:
        parts.append('<section class="section"><h2>Council opinion</h2>'
                     '<p class="note">Narration only — never a vote; the agreement above is '
                     "the verdict of record.</p>")
        op = report.council_opinion
        if op.available:
            from .. import ai_text_check as _atc
            parts.append(f'<p class="note">{_esc(_atc.top_line(_atc.count(op.narrative or "")))}</p>')
        parts.append(_narration_html(op.narrative) if op.available
                     else f'<p class="note">{_esc(op.note)}</p>')
        parts.append("</section>")

    parts.append(_workings_html(report))
    tail = f"Ran in {report.seconds:.1f}s"
    if report.cache.get("hits") is not None:
        tail += f"; day-cache {report.cache['hits']} hits, {report.cache['misses']} fetched"
    parts.append(f'<p class="note">{_esc(tail)}</p>')
    parts.append(_footer())
    return _document(title=f"Company Report — {report.display}" + (f" — {stamp}" if stamp else ""),
                     body=_JOIN.join(parts))


def _story_html(report) -> str:
    """COMPANY-STORY-1 sections 1-3 from the ONE ``StoryPage`` every renderer reads: the two-line
    answer, the story (or the model's summary in its place), one lens table whose "what it asks"
    captions are tooltips."""
    from ..company_story import SECTION_ANSWER, SECTION_STORY, SECTION_TABLE, story_page
    from ..reader import READER_SECTION_NOTE, READER_SECTION_TITLE

    page = story_page(report)
    out = [f'<section class="section" id="answer"><h2>{_esc(SECTION_ANSWER)}</h2>'
           f"<p><strong>{_esc(page.answer[0])}</strong></p><p>{_esc(page.answer[1])}</p></section>"]
    title = READER_SECTION_TITLE if page.model_summary else SECTION_STORY
    body = [f'<section class="section" id="story"><h2>{_esc(title)}</h2>']
    if page.summary_check:
        body.append(f'<p class="note">{_esc(page.summary_check)}</p>')
    body += [f"<p><strong>{_esc(lead)}</strong> {_esc(text)}</p>" for lead, text in page.paragraphs]
    if page.model_summary:
        body.append(f'<p class="note">{_esc(READER_SECTION_NOTE)}</p>')
    if page.note:
        body.append(f'<p class="note">{_esc(page.note)}</p>')
    out.append("".join(body) + "</section>")

    table = [f'<section class="section" id="lenses"><h2>{_esc(SECTION_TABLE)}</h2>']
    table += [f'<p class="note">{_esc(line)}</p>' for line in page.tag]
    if page.no_vote:
        table.append(f'<p class="note">{_esc(page.no_vote)}</p>')
    else:
        rows = []
        for r in page.rows:
            tip = html.escape(r.asks, quote=True)
            lens = f'<span title="{tip}">{_esc(r.lens)}</span>' if r.asks else _esc(r.lens)
            rows.append([lens, _esc(r.outcome), _esc(r.badge), _esc(r.reason)])
        table.append(_table(list(page.headers), rows))
    if page.caption:
        table.append(f'<p class="note">{_esc(page.caption)}</p>')
    if page.group_note:
        table.append(f'<p class="note">{_esc(page.group_note)}</p>')
    out.append("".join(table) + "</section>")
    return _JOIN.join(out)


def _workings_html(report) -> str:
    """COMPANY-STORY-1 section 4: everything that supports the answer, folded (open on click; print
    opens it), in the design's order."""
    from ..company_check import company_sources
    from ..company_story import SECTION_WORKINGS, narration_check_line, table_rows
    from ..peer_table import rank_columns

    c = report.check
    inner = ['<section class="section"><h2>Valuation band</h2>'
             '<p class="note">This company against its own history; a mark, never a veto.</p>'
             f"<p>{_esc(c.valuation_band)}</p></section>"]
    inner.append(_price_and_cash_html(c))
    inner.append(_absolute_readings_html(c, with_analyst=False)
                 or '<section class="section"><h2>Absolute readings</h2>'
                    '<p class="note">none available</p></section>')
    inner.append(_analyst_forecasts_html(c)
                 or '<section class="section"><h2>What analysts say</h2>'
                    '<p class="note">not available</p></section>')
    inner.append(_company_peers_html(c, rank_columns(report), report.ticker))
    line = narration_check_line(report)
    if line:
        inner.append(f'<details class="gate"><summary>AI text check</summary>'
                     f'<p class="note">{_esc(line)}</p></details>')
    notes = [r for r in table_rows(report) if r.asks or r.badge_detail or r.full_reason]
    if notes:
        items = []
        for r in notes:
            if r.asks:
                items.append(f"<strong>{_esc(r.lens)}</strong> asks: {_esc(r.asks)}")
            if r.full_reason:
                items.append(f"<strong>{_esc(r.lens)}</strong> did not apply: {_esc(r.full_reason)}")
            if r.badge_detail:
                items.append(f"<strong>{_esc(r.lens)}</strong> track record: {_esc(r.badge_detail)}")
        inner.append('<section class="section"><h2>Lens notes</h2>' + _bullets(items) + "</section>")
    sources = company_sources(c)
    if sources:
        inner.append('<section class="section"><h2>Sources</h2>'
                     + _bullets(f"<strong>{_esc(s.topic)}:</strong> {_esc(s.text)}"
                                for s in sources) + "</section>")
    return (f'<details class="gate workings" id="workings"><summary>{_esc(SECTION_WORKINGS)}</summary>'
            + _JOIN.join(inner) + "</details>")


def _price_and_cash_html(result) -> str:
    """COMPANY-FACTS-TABLE-1 — the same ``PriceAndCash`` object the text export reads, as
    one flat bullet list (the SAME lines, in the SAME order — this and the text export
    cannot drift), exactly like ``debt.lines()``/``growth.lines()`` above are rendered."""
    pac = getattr(result, "price_and_cash", None)
    if pac is None:
        return ('<section class="section"><h2>Price and cash</h2>'
               '<p class="note">not requested</p></section>')
    lines = pac.lines()
    out = ['<section class="section"><h2>Price and cash</h2>'
           '<p class="note">No comparison group. These are facts about this company\'s own '
           "price and cash flow - they are not lenses, they do not vote, and nothing here is "
           "ranked.</p>"]
    out.append(_bullets(_esc(ln) for ln in lines) if lines
              else '<p class="note">none available</p>')
    out.append("</section>")
    return "".join(out)


def _absolute_readings_html(result, *, with_analyst: bool = True) -> str:
    """Absolute readings (debt and cash, growth record, and - unless ``with_analyst`` is False -
    analyst forecasts) - the same sentences the page and the text export print, from the same
    objects."""
    from ..company_check import mixed_source_marker

    debt, growth = result.debt_and_cash, result.growth_record
    trend = result.analyst_trend if with_analyst else None
    if debt is None and growth is None and trend is None:
        return ""
    out = ['<section class="section"><h2>Absolute readings</h2>'
           '<p class="note">No comparison group. These are facts about this company\'s own '
           "accounts - they are not lenses, they do not vote, and nothing here is ranked.</p>"]
    if debt is not None:
        out.append("<h3>Debt and cash</h3>" + _bullets(_esc(ln) for ln in debt.lines())
                   + "".join(f'<p class="note">{_esc(ln)}</p>' for ln in debt.notes()))
    if growth is not None:
        out.append("<h3>Growth record" + _esc(mixed_source_marker(result, growth.source_tag))
                   + "</h3>" + _bullets(_esc(ln) for ln in growth.lines()))
        out.extend(f'<p class="note">{_esc(ln)}</p>' for ln in growth.notes())
    if trend is not None:
        out.append("<h3>What analysts say"
                   + _esc(mixed_source_marker(result, trend.source)) + "</h3>"
                   + _what_analysts_say_html(trend))
    out.append("</section>")
    return "".join(out)


def _analyst_forecasts_html(result) -> str:
    """The Company Report's own section, after the absolute readings."""
    from ..company_check import mixed_source_marker

    trend = result.analyst_trend
    if trend is None:
        return ""
    return ('<section class="section"><h2>What analysts say</h2>'
            f'<p class="note">A mark: it does not vote and changes no verdict'
            f'{_esc(mixed_source_marker(result, trend.source))}.</p>'
            + _what_analysts_say_html(trend) + "</section>")


def _what_analysts_say_html(trend) -> str:
    """The ratings (a line, a one-row table, the target sentence), then the forecast sentences."""
    ratings = trend.ratings
    out = []
    if ratings is not None and ratings.available:
        head, row = ratings.table()
        out.append(f"<p><strong>{_esc(ratings.summary_line())}</strong></p>"
                   + _table(head, [[f'<span class="mono">{_esc(c)}</span>' for c in row]]))
        out.extend(f"<p>{_esc(line)}</p>" for line in ratings.lines()[1:])
    else:
        out.append(f'<p class="note">{_esc(ratings.lines()[0] if ratings is not None else "Analyst ratings are not shown: no analyst data.")}</p>')
    out.extend(f"<p>{_esc(sentence)}</p>" for sentence in trend.forecast_sentences())
    return "".join(out)


def _company_peers_html(result, columns=None, company_ticker: str = "") -> str:
    """The peers table, market caps through the one money formatter; with ``columns`` the company is
    the first row and there is one rank column per lens."""
    from ..peer_table import ONE_SYSTEM_NOTE, has_one_system_peers, peer_rows, rank_display
    group = result.peer_group
    if group is None:
        return (f'<section class="section"><h2>Peers</h2><p class="note">The market index is '
                f"not available ({_esc(result.peer_error)}).</p></section>"
                if result.peer_error else "")
    out = ['<section class="section"><h2>Peers</h2>']
    if group.available:
        out.append(f'<p class="note">{_esc(group.reader_sentence())}</p>')
        columns = list(columns or ())
        rows = peer_rows(group, columns, company_ticker)
        body = [[f'<span class="mono">{_esc(r.marked_ticker)}</span>', _esc(r.name),
                 _esc(r.exchange),
                 f'<span class="mono">{_esc(r.usd_text)}</span>',
                 f'<span class="mono">{_esc(r.local_text)}</span>',
                 *(f'<span class="mono">{_esc(rank_display(cell) if c.kind == "rank" else cell)}'
                   f"</span>" for c, (_h, cell) in zip(columns, r.ranks)),
                 _esc(r.sub_industry)] for r in rows]
        # TAB-MERGE-1 part 2 commit 2: a VISIBLE highlight on the company's own row, not
        # just the "(this company)" text marker. Row 0 is reliably the company whenever
        # columns+company_ticker are both given (peer_table.peer_rows's own contract).
        n_cols = 6 + len(columns)
        cell_classes = ({(0, i): "this-company" for i in range(n_cols)}
                       if columns and company_ticker and rows else {})
        out.append(_table(["Ticker", "Name", "Exchange", "Market cap (USD)", "Market cap (local)",
                           *(c.header for c in columns), "Sub-industry"], body,
                          cell_classes=cell_classes))
        if has_one_system_peers(rows):
            out.append(f'<p class="note">{_esc(ONE_SYSTEM_NOTE)}</p>')
        from ..peer_table import PEER_METHOD_TITLE
        out.append(f'<details class="gate"><summary>{_esc(PEER_METHOD_TITLE)}</summary>'
                   + _bullets(_esc(line) for line in group.method_lines()) + "</details>")
    else:
        out.append('<p class="note">No peer group for this name.</p>')
        out.append(_bullets(_esc(reason) for reason in group.reasons))
    out.append("</section>")
    return "".join(out)


def _num(value) -> str:
    """A screen cell's number, formatted exactly as the .txt report formats it (one source
    of truth — company_check._fmt_num)."""
    from ..company_check import _fmt_num
    return _fmt_num(value)
