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
_STAMP_CLAIM = re.compile(r'narration check:\s*"(.*)"\s+(?:contradicts|orders)')

# Inline markdown the narrator writes. `_` is NEVER touched — it is load-bearing in the
# factor keys the prose quotes verbatim (fund_size, momentum_12m), the same reason
# narration_check._demark leaves it alone.
_BOLD = re.compile(r"\*\*(.+?)\*\*")
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
    """One line of prose -> inline HTML: escaped, then ``**bold**`` and ``` `code` ```
    markers consumed and provenance receipts badged (brackets kept). No other rewriting —
    the model's words are the model's words."""
    out = _esc(text)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
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


def _blocks(prose: str) -> list[tuple[str, str]]:
    """Prose -> ``[(plain_text, html)]`` blocks: blank-line-separated paragraphs, ``-``/
    ``*`` bullet lists, and ``#`` headings. Numbered lists are deliberately left as
    paragraphs (dropping a "1." would drop content)."""
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

    for raw in prose.splitlines():
        line = raw.strip()
        if not line:
            flush_para()
            flush_bullets()
            continue
        if line.startswith("#"):
            flush_para()
            flush_bullets()
            heading = line.lstrip("#").strip()
            blocks.append((heading, f"<h4>{_inline(heading)}</h4>"))
            continue
        if line[:2] in ("- ", "* ", "• "):
            flush_para()
            bullets.append(line[2:].strip())
            continue
        flush_bullets()
        para.append(line)
    flush_para()
    flush_bullets()
    return blocks


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
                out.append(_callout(stamp, label="narration check"))
            else:
                still.append(stamp)
        pending = still
    out.extend(_callout(s, label="narration check") for s in pending)
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
        rules_applied,
        shortlist_table,
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
        '<p class="kicker">Aristos Council · universe run · multi-lens</p>'
        f"<h1>{_esc(title.headline)} "
        f'<span class="record-id">{_esc(title.record_id)}</span></h1>'
        + _kv([
            ("Cohort", _esc(f'{cohort} — {m.get("universe_size", 0)} names')),
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
    parts.append(_shortlist_section(getattr(multi_result, "shortlist", None)))

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
                 f'<p class="note">{_esc(VERDICT_TABLE_NOTE)}</p>')
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
                 "strategies that actually ran.</p>")
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
        body = [[f'<strong>{_esc(r.label)}</strong>'
                 f'<br><code class="muted">{_esc(r.criterion)}</code>',
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
                     + (f'<p class="note">{_esc(lens_asks(res))}</p>'
                        if lens_asks(res) else "")          # CAPTION-1
                     + f'<p class="note">Ranked {res.meta["ranked_count"]} of '
                       f'{res.meta["universe_size"]} names.</p>')
        if res.excluded:
            items = []
            for row in exclusion_rows(res):
                muted = (f' <code class="muted">{_esc(row["criterion"])}</code>'
                         if row["criterion"] else "")
                flag = (f'<br><span class="flag">{_esc(row["flag"])}</span>'
                        if row["flag"] else "")
                items.append(f'<strong>{_esc(row["name"])}</strong> — '
                             f'{_esc(row["sentence"])}{muted}{flag}')
            parts.append(f"<h3>Excluded — did not pass a rule · {len(res.excluded)}</h3>"
                         + _bullets(items))
        if res.unrateable:
            parts.append(f"<h3>No usable data — no verdict · {len(res.unrateable)}</h3>"
                         + _bullets(
                             f'<strong>{_esc(display_name(t, res.names.get(t)))}</strong>'
                             f" — {_inline(why)}" for t, why in res.unrateable))
        if getattr(res, "fetch_errors", None):
            parts.append(f"<h3>Data fetch failed — re-run to recover · "
                         f"{len(res.fetch_errors)}</h3>"
                         + _bullets(
                             f'<strong>{_esc(display_name(t, res.names.get(t)))}</strong>'
                             f" — {_inline(why)}" for t, why in res.fetch_errors))
        entries = provenance_sentences(res)
        if entries:
            parts.append(f"<h3>{_esc(PROVENANCE_SECTION_TITLE)}</h3>"
                         + _bullets(_esc(e["sentence"]) for e in entries))
        parts.append("</section>")

    # ----- 8 (GLOSSARY-1): the LAST section before the footer, built from the
    # registries and holding only the terms this report actually uses.
    parts.append(_glossary_html(multi_result, "\n".join(parts)))

    # ----- 9: ONE common footer.
    parts.append(_footer())
    return _document(title=title.full(), body="\n".join(parts))




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


def _shortlist_section(sl) -> str:
    """SHORTLIST-1 — the derived section. Placed directly after the summary and BEFORE the
    verdict grid, because it is the answer the grid is evidence for."""
    from ..pipeline import shortlist_table

    if sl is None:
        return ""
    body = [f'<section class="section" id="shortlist">'
            f"<h2>{_esc(sl.title)}</h2>"]
    if not sl.available:
        body.append(f'<p class="note">{_esc(sl.reason)}.</p></section>')
        return "".join(body)
    body.append(f'<p class="note">{_esc(sl.rule_sentence)}</p>')
    cols, rows = shortlist_table(sl)
    if rows:
        body.append(_table(cols, [[_esc(r[c]) for c in cols] for r in rows],
                           cls="ranked"))
    else:
        body.append('<p class="note">No candidate survived the checks. That is a '
                    'result, not a gap — every drop and its reason is below.</p>')
    if sl.dropped:
        body.append(f"<h3>Dropped · {len(sl.dropped)}</h3>" + _bullets(
            f'<strong>{_esc(r.display)}</strong> — {_esc(r.dropped_by)}'
            for r in sl.dropped))
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


def verdict_of_cell(text: str) -> str:
    """The VERDICT a verdict-grid cell carries ("BUY"/"HOLD"/"SELL"), or "" for a cell on
    another axis (excluded / no data / fetch failed) — those are not verdicts and must
    never be coloured as though they were."""
    for verdict in _VERDICT_HEX:
        if text.endswith(f"· {verdict}"):
            return verdict
    return ""


def _verdict_grid_cell(text: str) -> str:
    """One verdict-table cell. A ranked cell's VERDICT keeps the shared palette so the
    grid scans by colour exactly as the single-lens ranked table does; every other axis
    (excluded / no data / fetch failed) stays plain, because it is not a verdict. The
    verdict WORD is always rendered — colour is never the only carrier of meaning."""
    verdict = verdict_of_cell(text)
    if verdict:
        head = text[: -len(verdict)]
        return (f'<span class="mono">{_esc(head)}</span>'
                f'<span class="verdict verdict-{verdict.lower()}">{_esc(verdict)}'
                "</span>")
    return f'<span class="mono">{_esc(text)}</span>'


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
        provenance_sentences,
        rules_applied,
        summary_line,
        untested_rule_notes,
        used_symbol_notes,
        valuation_band_table,
    )
    from ..rank_engine import factor_column_label
    from ..report_language import format_score_gloss, label_with_id
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
        '<p class="kicker">Aristos Council · universe run</p>'
        f"<h1>{_esc(title_name)} — {_esc(universe_label)}</h1>"
        + _kv([
            ("Universe", _esc(f'{universe_label} — '
                              f'{m.get("universe_size", "—")} names')),
            ("Strategy", _esc(label_with_id(m.get("rank_strategy_name", ""),
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
                            f'{m.get("universe_size", "—")} names')),
            ("Shortlist", "" if m.get("ranker_only") else
             _esc(f'{len(m.get("shortlist") or [])} names · estimated cost '
                  f'${float(m.get("est_cost") or 0.0):.2f} · narrating '
                  f'{m.get("narrate_coverage", "buys_only")}')),
            ("Run id", f'<code class="muted">{_esc(m["run_id"])}</code>'
                       if m.get("run_id") else ""),
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
        body = [[f'<strong>{_esc(r.label)}</strong>'
                 f'<br><code class="muted">{_esc(r.criterion)}</code>',
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
                     + _esc(format_score_gloss(n_factors, len(result.ranked)))
                     + "</p>")
        head = ["Position (score)", "Name", "Verdict", *labels]
        body = [[_position_cell(r["Position (score)"]),
                 _esc(r["Name"]),
                 _verdict_cell(r["Verdict"]),
                 *[f'<span class="mono">{_esc(r[lab])}</span>' for lab in labels]]
                for r in rows]
        parts.append(_table(head, body, cls="ranked", titles=[
            "", "", "", *factor_names]))
        parts.append('<p class="note">Factor ids, in column order: '
                     + ", ".join(f"<code>{_esc(f)}</code>" for f in factor_names)
                     + ".</p>")
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
    entries = provenance_sentences(result)
    if entries:
        parts.append(f'<section class="section"><h2>{_esc(PROVENANCE_SECTION_TITLE)}</h2>'
                     f'<p class="note">{_esc(PROVENANCE_SECTION_NOTE)}</p>'
                     + _bullets(_esc(e["sentence"]) for e in entries)
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
        items = []
        for row in exclusion_rows(result):
            muted = (f' <code class="muted">{_esc(row["criterion"])}</code>'
                     if row["criterion"] else "")
            flag = (f'<br><span class="flag">{_esc(row["flag"])}</span>'
                    if row["flag"] else "")
            items.append(f'<strong>{_esc(row["name"])}</strong> — '
                         f'{_esc(row["sentence"])}{muted}{flag}')
        parts.append('<section class="section">'
                     f"<h2>Excluded — did not pass a rule, so was never ranked · "
                     f"{len(result.excluded)}</h2>"
                     + _bullets(items) + "</section>")
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


# --------------------------------------------------------------------------- #
# Company Check
# --------------------------------------------------------------------------- #
def company_check_html(result, *, run_start: Optional[datetime] = None,
                       strategy_display_name: str = "") -> str:
    """The single-name diagnostic as ONE self-contained HTML file (REPORT-HTML-1).

    Renders the same content as the canonical ``.txt`` report — every screen criterion with
    its observed value and three-valued status, the gates, each factor's value with its
    source badge and cohort context, the verdict OF RECORD (quoted, never recomputed), the
    divergence flag, and the data-integrity block with its ⚠ flags as callouts. NO verdict
    is ever issued here: a rank over a class of one is a fabricated verdict.
    """
    from ..company_check import (
        # The SAME gloss the .txt renders, so the two cannot drift.
        _expense_ratio_gloss,
        format_factor_value,
    )

    stamp = _local_stamp(run_start)
    title_name = strategy_display_name or result.rank_strategy_id
    parts: list[str] = []

    parts.append(
        '<header class="doc">'
        '<p class="kicker">Aristos Council · company check · single-name diagnostic</p>'
        f"<h1>{_esc(result.display)}</h1>"
        + _kv([
            ("strategy", f'<code>{_esc(result.rank_strategy_id)}</code>'
             + (f" · {_esc(title_name)}" if strategy_display_name else "")),
            ("lens screen",
             f'<code>{_esc(result.screen_strategy_id or "none")}</code>'),
            ("reference", f'<code>{_esc(result.reference_universe_id or "—")}</code>'),
            ("run", _esc(stamp)),
        ])
        + '<p class="house">NO VERDICT — a verdict is a cohort statement, so it comes '
          "from a universe run, never from a class of one.</p>"
        "</header>")

    if result.unrateable:
        parts.append(_callout(f"UNRATEABLE — {result.data_integrity.note}. No data, so no "
                              "diagnosis and no verdict.", kind="alert"))
        parts.append(f'<p class="note">{_inline(result.pointer)}</p>')
        parts.append(_footer())
        return _document(title=f"Company Check — {result.display}", body="\n".join(parts))

    # ----- screen: every criterion evaluated (a universe run stops at the first fail).
    parts.append('<section class="section"><h2>Screen</h2>')
    if result.screen_less:
        parts.append('<p class="note"><strong>No lens screen</strong> — this strategy '
                     "screens nothing; quality enters via ranking only. Gates below still "
                     "apply.</p>")
    else:
        parts.append('<p class="note">All criteria evaluated for diagnosis; a universe '
                     "run excludes on the first confirmed fail.</p>")
        body = []
        for c in result.screen:
            tags = ["gating" if c.gating else "non-gating"]
            if c.basis:
                tags.append(c.basis)
            if c.borderline:
                tags.append("borderline")
            observed = ("—" if c.status == "FAIL" and c.observed is None
                        else _num(c.observed))
            detail = (_esc(c.note or "fails closed by design")
                      if c.status == "FAIL" and c.observed is None else _esc(c.note))
            body.append([
                f'<span class="status" style="color:{_STATUS_HEX.get(c.status, "")}">'
                f"{_esc(c.status)}</span>",
                f'<span class="mono">{_esc(c.name)}</span>',
                f'<span class="mono">{_esc(observed)}</span>',
                f'<span class="mono">{_esc(_num(c.threshold))}</span>',
                " ".join(f'<span class="badge">{_esc(t)}</span>' for t in tags)
                + (f'<div class="note">{detail}</div>' if detail else ""),
            ])
        parts.append(_table(["Status", "Criterion", "Observed", "Threshold", "Notes"],
                            body))
        if result.market_cap_in_gates:
            parts.append('<p class="note">min_market_cap — same floor as the universe '
                         "gate; shown once, under Gates below.</p>")
    parts.append("</section>")

    # ----- gates.
    if result.gates:
        body = []
        for g in result.gates:
            detail = _inline(g.detail)
            if g.rationale:
                detail += f'<div class="note">↳ {_inline(g.rationale)}</div>'
            body.append([
                f'<span class="status" style="color:{_STATUS_HEX.get(g.status, "")}">'
                f"{_esc(g.status)}</span>",
                f'<span class="mono">{_esc(g.name)}</span>', detail])
        parts.append('<section class="section"><h2>Gates — sector / cap / payout</h2>'
                     + _table(["Status", "Gate", "Detail"], body) + "</section>")

    # ----- factor values + cohort context (source as a badge — [static: …] included).
    ref = (f"reference: latest run of {result.reference_universe_id} "
           f"(run {result.reference_run_date}, {result.reference_cohort_n} ranked)"
           if result.reference_available
           else "reference: none available — run the universe once for context")
    items = []
    for fc in result.factors:
        gloss = _expense_ratio_gloss(fc.value) if fc.factor == "expense_ratio" else ""
        items.append(
            f"<strong>{_esc(fc.label)}</strong> "
            f'<span class="mono">({_esc(fc.factor)})</span>: '
            f"{_esc(format_factor_value(fc.factor, fc.value))}{_esc(gloss)} "
            f'<span class="badge">[{_esc(fc.source)}]</span> — {_inline(fc.context)}')
    parts.append('<section class="section"><h2>Factor values + cohort context</h2>'
                 f'<p class="note">{_esc(ref)}</p>' + _bullets(items))
    if result.verdict_of_record:
        parts.append("<p><strong>VERDICT OF RECORD:</strong> "
                     f"{_inline(result.verdict_of_record)}</p>")
    parts.append("</section>")

    if result.divergence_flag:
        parts.append(_callout(f"Price/fundamentals divergence — {result.divergence_flag}",
                              kind="alert", label="divergence"))

    # ----- data integrity, incl. the ⚠ implausible-vendor-value flags as callouts.
    di = result.data_integrity
    parts.append('<section class="section"><h2>Data integrity</h2>')
    lines = [f"fundamentals: <strong>{'ok' if di.fundamentals_ok else 'MISSING'}</strong>"
             f" · price: <strong>{'ok' if di.price_ok else 'MISSING'}</strong>"]
    if di.abstained_criteria:
        lines.append("criteria not evaluated (abstained): "
                     + _esc(", ".join(di.abstained_criteria)))
    if di.not_evaluated_factors:
        lines.append("factors not evaluated: "
                     + _esc(", ".join(di.not_evaluated_factors)))
    parts.append(_bullets(lines))
    for flag in di.implausible:
        parts.append(_callout(f"⚠ {flag}", kind="alert", label="data flag"))
    parts.append("</section>")

    parts.append(f'<p class="note">{_inline(result.pointer)}</p>')
    parts.append(_footer())
    title = f"Company Check — {result.display}" + (f" — {stamp}" if stamp else "")
    return _document(title=title, body="\n".join(parts))


def _num(value) -> str:
    """A screen cell's number, formatted exactly as the .txt report formats it (one source
    of truth — company_check._fmt_num)."""
    from ..company_check import _fmt_num
    return _fmt_num(value)
