"""Self-contained HTML export of the two shared reports (REPORT-HTML-1).

WHY: reports get sent to people outside the repo. Markdown renders poorly for them and
the PDF path breaks the wide rank tables. This module renders the SAME report data as a
single ``.html`` file — all CSS inline, no external requests, no JS, no build step — so it
can be mailed, opened anywhere, and printed to PDF by the browser.

HARD CONSTRAINT: this is a PRESENTATION layer, nothing else. The universe ``.md`` report
and the Fund Profile ``.txt`` report stay CANONICAL and byte-identical (frozen monthly
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

_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 26px 30px 44px; background: #ffffff; color: #16181d;
       font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
             "Helvetica Neue", Arial, sans-serif; }
.wrap { max-width: 1180px; margin: 0 auto; }
code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas,
              "Liberation Mono", monospace; font-size: 0.92em; }
a { color: inherit; }

header.doc { border-bottom: 3px solid #16181d; padding-bottom: 12px; margin-bottom: 18px; }
header.doc .kicker { text-transform: uppercase; letter-spacing: .09em; font-size: 11px;
                     font-weight: 700; color: #5b6472; margin: 0 0 4px; }
header.doc h1 { font-size: 26px; line-height: 1.2; margin: 0 0 8px; }
.house { margin: 10px 0 0; padding: 8px 12px; border: 1px solid #c7cdd8;
         border-left: 5px solid #16181d; background: #f6f8fb; font-weight: 600; }

.kv { display: table; width: 100%; margin: 10px 0 0; border-collapse: collapse; }
.kv .row { display: table-row; }
.kv .k, .kv .v { display: table-cell; padding: 3px 10px 3px 0; vertical-align: top;
                 font-size: 13px; }
.kv .k { color: #5b6472; white-space: nowrap; width: 1%; text-transform: uppercase;
         letter-spacing: .05em; font-size: 11px; font-weight: 700; padding-top: 5px; }

h2 { font-size: 17px; margin: 26px 0 8px; padding-bottom: 4px;
     border-bottom: 1px solid #c7cdd8; }
h3 { font-size: 14px; margin: 16px 0 6px; }
h4 { font-size: 13px; margin: 12px 0 4px; }
p { margin: 8px 0; }
ul { margin: 8px 0; padding-left: 22px; }
li { margin: 3px 0; }
.note { color: #5b6472; font-size: 12.5px; margin: 4px 0; }
.section { margin-bottom: 4px; }

.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 13px; }
th, td { border: 1px solid #c7cdd8; padding: 5px 8px; text-align: left;
         vertical-align: top; }
th { background: #eef1f6; font-size: 11.5px; text-transform: uppercase;
     letter-spacing: .04em; overflow-wrap: anywhere; }
tbody tr:nth-child(even) td { background: #fafbfd; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.pos { font-weight: 700; white-space: nowrap; }
.pos .detail { font-weight: 400; color: #5b6472; font-size: 12px; white-space: normal; }
.verdict { font-weight: 700; white-space: nowrap; }
.verdict-buy { color: #2E7D32; }
.verdict-hold { color: #B8860B; }
.verdict-sell { color: #B23B3B; }
.flag { display: block; margin-top: 2px; font-weight: 700; font-size: 11.5px;
        color: #16181d; border: 1px solid #16181d; border-radius: 3px;
        padding: 1px 5px; background: #f1f3f7; white-space: normal; }
.status { font-weight: 700; white-space: nowrap; }

.badge { display: inline-block; border: 1px solid #9aa3b2; border-radius: 999px;
         background: #f1f3f7; color: #333a45; padding: 0 7px;
         font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
         font-size: 11px; font-weight: 600; white-space: nowrap; }

.callout { margin: 10px 0; padding: 9px 12px; border: 2px solid #9a6700;
           border-left-width: 7px; border-radius: 4px; background: #fff8e5; }
.callout .body { font-weight: 600; }
.callout.stamp { border-color: #9a6700; }
.callout.alert { border-color: #B23B3B; background: #fdf2f2; }
.callout .label { display: block; text-transform: uppercase; letter-spacing: .08em;
                  font-size: 10.5px; font-weight: 700; color: #5b6472;
                  margin-bottom: 3px; }

.name-section { border: 1px solid #c7cdd8; border-radius: 5px; margin: 12px 0;
                padding: 2px 14px 10px; background: #ffffff; }
.name-section > summary { cursor: pointer; font-weight: 700; font-size: 15px;
                          margin: 10px -4px; padding-left: 4px; }
.name-section > summary::marker { color: #5b6472; }

footer.doc { margin-top: 34px; padding-top: 12px; border-top: 3px solid #16181d; }
footer.doc .doctrine { font-weight: 700; margin: 0 0 6px; }
footer.doc .disclaimer { color: #4b5462; font-size: 12px; margin: 0; }

@media print {
  @page { size: A4 portrait; margin: 12mm 10mm 14mm 10mm; }
  body { padding: 0; font-size: 10.5pt; }
  .wrap { max-width: none; }
  .scroll { overflow: visible; }
  h2 { break-after: avoid; page-break-after: avoid; }
  thead { display: table-header-group; }
  tr, .callout, .kv { break-inside: avoid; page-break-inside: avoid; }
  /* Wide rank tables must SHRINK, never clip: auto layout + smaller type + hard wrap. */
  table { font-size: 8pt; table-layout: auto; width: 100%; }
  th, td { padding: 3px 4px; overflow-wrap: anywhere; word-break: break-word; }
  .pos .detail { font-size: 7.5pt; }
  /* One per-name narration per page. */
  .name-section { break-before: page; page-break-before: always; border: none;
                  padding: 0; }
  .name-section:first-of-type { break-before: auto; page-break-before: auto; }
  details, details > summary ~ * { display: block; }
  /* Grayscale legibility: structure carries the meaning, not the hue. */
  .callout, .badge, .flag { background: #ffffff !important; border-color: #000000 !important;
                            color: #000000 !important; }
  .callout { border-left-width: 7px !important; }
  .verdict-buy, .verdict-hold, .verdict-sell, .status { color: #000000 !important; }
  .house { background: #ffffff !important; }
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


def _table(head: list[str], rows: list[list[str]], *, cls: str = "") -> str:
    """A bordered table from pre-rendered HTML cells (``head`` entries are escaped)."""
    ths = "".join(f"<th>{_esc(h)}</th>" for h in head)
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
                  for row in rows)
    return (f'<div class="scroll"><table class="{cls}"><thead><tr>{ths}</tr></thead>'
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
        factor_integrity,
        format_integrity_entry,
        format_screen_basis_entry,
        ranked_abstention_footnotes,
        screen_basis_integrity,
    )
    from ..data.adapter import display_name

    m = result.meta
    strategy_id = m.get("rank_strategy_id", "")
    title_name = (strategy_display_name or m.get("rank_strategy_name") or strategy_id)
    stamp = _local_stamp(run_start)
    parts: list[str] = []

    # ----- header: who ran what, on which cohort, when, in which mode.
    parts.append(
        '<header class="doc">'
        '<p class="kicker">Aristos Council · universe run</p>'
        f"<h1>{_esc(title_name)}</h1>"
        + _kv([
            ("strategy", f'<code>{_esc(strategy_id)}</code>'),
            ("universe", f'<code>{_esc(m.get("universe_id") or "adhoc")}</code>'),
            ("run", _esc(stamp)),
            ("mode", _esc(m.get("council_mode", ""))),
            ("screen", f'<code>{_esc(m.get("screen_strategy_id", ""))}</code>'),
            ("ranked", _esc(f'{m.get("ranked_count", "—")} of '
                            f'{m.get("universe_size", "—")}')),
            ("shortlist", "" if m.get("ranker_only") else
             _esc(f'{len(m.get("shortlist") or [])} · est '
                  f'${float(m.get("est_cost") or 0.0):.2f} · narrate '
                  f'{m.get("narrate_coverage", "buys_only")}')),
            ("run id", f'<code>{_esc(m["run_id"])}</code>' if m.get("run_id") else ""),
        ])
        + f'<p class="house">{_esc(result.header)}</p>'
        "</header>")

    # ----- 1: the ranked table — the verdict of record.
    rows, factor_names = ranked_table_rows(result.ranked, result.names)
    parts.append('<section class="section"><h2>Ranked — verdict of record</h2>')
    if rows:
        head = ["Position (score)", "Name", "Verdict", *factor_names]
        body = [[_position_cell(r["Position (score)"]),
                 _esc(r["Name"]),
                 _verdict_cell(r["Verdict"]),
                 *[f'<span class="mono">{_esc(r[f])}</span>' for f in factor_names]]
                for r in rows]
        parts.append(_table(head, body, cls="ranked"))
        parts.append('<p class="note">Rank 1 = best on a factor; a lower combined '
                     'rank-sum is better. <code>*</code> marks an IMPUTED factor rank '
                     '(the value was absent), <code>†</code> a name that passed the '
                     f'screen while a criterion abstained, <code>{_esc(BOUNDARY_FLAG)}</code> '
                     'a verdict that split from a tied name on the alphabetical '
                     'tie-break.</p>')
    else:
        parts.append('<p class="note">(no names survived the screen)</p>')
    footnotes = ranked_abstention_footnotes(result)
    if footnotes:
        parts.append(_bullets(_esc(f) for f in footnotes))
    parts.append("</section>")

    # ----- 2: factor integrity — what each factor was actually measured from.
    entries = factor_integrity(result)
    if entries:
        parts.append('<section class="section"><h2>Factor integrity</h2>'
                     '<p class="note">Per-factor source across the ranked names.</p>'
                     + _bullets(f'<strong>{_esc(e["factor"])}</strong> — '
                                + _inline(format_integrity_entry(e))
                                for e in entries)
                     + "</section>")

    basis_entries = screen_basis_integrity(result)
    if basis_entries:
        parts.append('<section class="section"><h2>Screen basis</h2>'
                     '<p class="note">Measurement basis across the screened names.</p>'
                     + _bullets(f'<strong>{_esc(e["criterion"])}</strong> — '
                                + _inline(format_screen_basis_entry(e))
                                for e in basis_entries)
                     + "</section>")

    # ----- 3/4/5: the three NON-verdict axes, each kept distinct.
    def _reason_list(pairs) -> str:
        return _bullets(
            f'<strong>{_esc(display_name(t, result.names.get(t)))}</strong> — '
            + _inline(why) for t, why in pairs)

    if result.excluded:
        parts.append('<section class="section">'
                     f"<h2>Excluded — screen / cap / sector · {len(result.excluded)}</h2>"
                     + _reason_list(result.excluded) + "</section>")
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
# Fund Profile
# --------------------------------------------------------------------------- #
def fund_profile_html(result, *, run_start: Optional[datetime] = None,
                      strategy_display_name: str = "") -> str:
    """The single-name profile as ONE self-contained HTML file (REPORT-HTML-1).

    Renders the same content as the canonical ``.txt`` report — the identity header, every
    screen criterion with its observed value and three-valued status, the gates, the FULL
    membership of the reference cohort, the fit warning when the cohort is not a confirmed
    sector match, each factor's value with its source badge, cohort context and the cohort
    median, the verdict OF RECORD (quoted, never recomputed), the divergence flag, and the
    data-integrity block with its ⚠ flags as callouts. NO verdict is ever issued here: a
    rank over a class of one is a fabricated verdict.
    """
    from ..fund_profile import (
        # The SAME gloss/builders the .txt renders, so the surfaces cannot drift.
        _expense_ratio_gloss,
        format_factor_value,
        format_median,
        identity_rows,
    )

    stamp = _local_stamp(run_start)
    title_name = strategy_display_name or result.rank_strategy_id
    parts: list[str] = []

    parts.append(
        '<header class="doc">'
        '<p class="kicker">Aristos Council · fund profile · single-name profile</p>'
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

    # ----- identity (FUND-PROFILE-1 rule 6): what this instrument IS, with provenance.
    rows = identity_rows(getattr(result, "identity", None))
    if rows:
        parts.append(
            '<section class="section"><h2>Identity</h2>'
            + _kv([(r.label,
                    _esc(r.value)
                    + (f' <span class="badge">[{_esc(r.source)}]</span>'
                       if r.source else ""))
                   for r in rows])
            + "</section>")

    if result.unrateable:
        parts.append(_callout(f"UNRATEABLE — {result.data_integrity.note}. No data, so no "
                              "profile and no verdict.", kind="alert"))
        parts.append(f'<p class="note">{_inline(result.pointer)}</p>')
        parts.append(_footer())
        return _document(title=f"Fund Profile — {result.display}", body="\n".join(parts))

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

    # ----- the reference cohort, in full (rule 3): a comparison group the reader can see.
    members = getattr(result, "cohort_members", None) or []
    if members:
        body = []
        for m in members:
            pos = f"#{m.position}" if m.position is not None else "—"
            if m.tied:
                pos += " (tied)"
            ticker = (f"<strong>{_esc(m.ticker)}</strong>" if m.is_profiled
                      else f'<span class="mono">{_esc(m.ticker)}</span>')
            name = _esc(m.display) + (" ← this name" if m.is_profiled else "")
            body.append([f'<span class="pos">{_esc(pos)}</span>', ticker, name,
                         _verdict_cell(m.verdict), f'<span class="mono">{_esc(m.score)}'
                                                   "</span>"])
        parts.append(
            '<section class="section"><h2>Reference cohort</h2>'
            f'<p class="note">{_esc(result.cohort_display_name)} '
            f"({result.reference_universe_id}) · "
            f"run {_esc(result.reference_run_date or '')}"
            f" · run id {_esc(result.reference_run_id or '')} · "
            f"{result.reference_cohort_n} ranked, {result.cohort_excluded_n} excluded · "
            f"declared sector: {_esc(result.cohort_sector or 'none declared')}</p>"
            + (f'<p class="note">{_esc(result.cohort_note)}.</p>'
               if result.cohort_note else "")
            + _table(["Rank", "Ticker", "Name", "Verdict", "Score"], body)
            + "</section>")

    # ----- the fit warning (rule 4): ONE plain sentence when the cohort is not a match.
    if getattr(result, "fit_warning", None):
        parts.append(_callout(result.fit_warning, kind="alert", label="fit"))

    # ----- factor values + cohort context (source as a badge — [static: …] included).
    ref = (f"reference: latest run of {result.reference_universe_id} "
           f"(run {result.reference_run_date}, {result.reference_cohort_n} ranked)"
           if result.reference_available
           else "reference: none available — run the universe once for context")
    items = []
    for fc in result.factors:
        gloss = _expense_ratio_gloss(fc.value) if fc.factor == "expense_ratio" else ""
        item = (f"<strong>{_esc(fc.label)}</strong> "
                f'<span class="mono">({_esc(fc.factor)})</span>: '
                f"{_esc(format_factor_value(fc.factor, fc.value))}{_esc(gloss)} "
                f'<span class="badge">[{_esc(fc.source)}]</span> — {_inline(fc.context)}')
        med = format_median(fc, result.reference_run_date)
        if med:
            item += f'<div class="note">↳ {_esc(med)}</div>'
        items.append(item)
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
    title = f"Fund Profile — {result.display}" + (f" — {stamp}" if stamp else "")
    return _document(title=title, body="\n".join(parts))


# DEPRECATED internal alias (FUND-PROFILE-1) — the export is user-visibly "Fund Profile".
company_check_html = fund_profile_html


def _num(value) -> str:
    """A screen cell's number, formatted exactly as the .txt report formats it (one source
    of truth — fund_profile._fmt_num)."""
    from ..fund_profile import _fmt_num
    return _fmt_num(value)
