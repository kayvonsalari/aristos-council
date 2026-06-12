"""Purpose-built PDF export of a run report — a deliverable council record.

NOT a screen dump: a clean A4 document generated from the report JSON via
HTML-to-PDF (xhtml2pdf), with a designed light-theme layout (identity header,
screen-results table, specialists, critic, decision, audit summary), page
numbers, and a footer carrying the strategy id + run timestamp. Layout is
designed for A4 from the start.

Prose is provenance-cleaned (call_id plumbing stripped) via the same shared
helper the UI uses, so the document reads as a record, not a debug log. The
markdown the agents wrote (headers, lists, tables) is converted to HTML.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..persistence.reports import RunReport
from ..presentation import SCREEN_STATUS_HEX, screen_table_rows, strip_provenance

DISPLAY_TZ = ZoneInfo("Europe/Berlin")
# Verdict colors darkened for paper (vs the screen palette).
_PDF_VERDICT_HEX = {"BUY": "#1B5E20", "HOLD": "#6B4F00", "SELL": "#8B1A1A"}
_AUDIT_KEYS = ("figures_audited", "verified", "mismatch", "unresolvable",
               "unverifiable", "unit_scaled")


def _local(dt: datetime, fmt: str = "%d.%m.%Y %H:%M") -> str:
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(DISPLAY_TZ).strftime(f"{fmt} %Z")


def _esc(v) -> str:
    return html.escape("" if v is None else str(v))


def _md_html(text: str) -> str:
    """Cleaned prose (markdown) -> HTML. Lazy import: markdown is a ui extra."""
    import markdown
    return markdown.markdown(strip_provenance(text or ""), extensions=["tables"])


def _plain(text: str) -> str:
    """Cleaned prose -> escaped one-liner (for caveats/weaknesses/questions)."""
    return _esc(strip_provenance(text or ""))


def _figures_html(figures) -> str:
    if not figures:
        return ""
    body = "".join(
        f"<tr><td>{_esc(f.label)}</td><td>{_esc(f.value)}</td>"
        f"<td>{_esc(f.unit)}</td><td>{_esc(f.provenance.field_path)}</td>"
        f"<td>{_esc(f.provenance.tool_name)}</td></tr>"
        for f in figures
    )
    return ("<table><thead><tr><th>label</th><th>value</th><th>unit</th>"
            "<th>field</th><th>tool</th></tr></thead>"
            f"<tbody>{body}</tbody></table>")


def _screen_html(screen) -> str:
    rows = screen_table_rows(screen)
    if not rows:
        return ""
    body = ""
    for r in rows:
        color = SCREEN_STATUS_HEX.get(r["Status"], "#1a1a1a")
        body += (
            f"<tr><td>{_esc(r['Criterion'])}</td><td>{_esc(r['Observed'])}</td>"
            f"<td>{_esc(r['Threshold'])}</td>"
            f"<td style='color:{color};font-weight:bold'>{_esc(r['Status'])}</td>"
            "</tr>"
        )
    return ("<h2>Screen results</h2><table><thead><tr><th>Criterion</th>"
            "<th>Observed</th><th>Threshold</th><th>Status</th></tr></thead>"
            f"<tbody>{body}</tbody></table>")


def _bullets(items) -> str:
    return "<ul>" + "".join(f"<li>{_plain(i)}</li>" for i in items) + "</ul>"


def report_to_html(report: RunReport) -> str:
    """Render the full council record as a self-contained A4 HTML document."""
    d = report.decision
    verdict = d.recommendation.value.upper() if d else "—"
    vcolor = _PDF_VERDICT_HEX.get(verdict, "#1a1a1a")
    conf = f"{d.confidence:.2f}" if d else "—"
    name = f" — {_esc(report.company_name)}" if report.company_name else ""
    ts = _local(report.run_at)

    parts: list[str] = []

    # Identity header (bordered-free table: ticker left, verdict right)
    parts.append(
        "<table class='plain'><tr>"
        f"<td class='plain'><h1>{_esc(report.ticker)}{name}</h1>"
        f"<p class='sub'>{_esc(report.strategy_id)} &middot; {_esc(ts)}</p></td>"
        f"<td class='plain' style='text-align:right; width:32%'>"
        f"<div class='verdict' style='color:{vcolor}'>{_esc(verdict)}</div>"
        f"<div class='sub'>confidence {_esc(conf)}</div></td>"
        "</tr></table>"
    )

    # Human-review flags
    if report.veto_flags:
        parts.append(
            f"<p class='flags'>&#9888; Human review required — "
            f"{len(report.veto_flags)} veto trigger(s):</p>"
        )
        parts.append(_bullets(
            f"{f.trigger.value}: {f.detail}" for f in report.veto_flags))
    else:
        parts.append("<p class='ok'>No veto triggers — auto-proceed permitted.</p>")

    # Screen results (deterministic table)
    parts.append(_screen_html(report.screen))

    # Decision
    if d:
        parts.append("<h2>Decision rationale</h2>")
        parts.append(_md_html(d.rationale))
        if d.dissent:
            parts.append("<p class='label'>Dissent recorded: "
                         + _esc(", ".join(s.value for s in d.dissent)) + "</p>")

    # Specialists
    parts.append("<h2>Specialists</h2>")
    for op in report.specialist_opinions:
        parts.append(
            f"<h3>{_esc(op.specialist.value.title())} — {_esc(op.stance.value)} "
            f"(confidence {op.confidence:.2f})</h3>"
        )
        parts.append(_md_html(op.thesis))
        parts.append(_figures_html(op.figures))
        if op.caveats:
            parts.append("<p class='label'>Caveats</p>" + _bullets(op.caveats))

    # Critic
    cr = report.critic_report
    if cr:
        parts.append(
            f"<h2>Critic — against the {_esc(cr.targets_stance.value)} "
            "consensus</h2>"
        )
        parts.append(_md_html(cr.counter_thesis))
        parts.append(_figures_html(cr.figures))
        if cr.weaknesses_found:
            parts.append("<p class='label'>Weaknesses found</p>"
                         + _bullets(cr.weaknesses_found))
        if cr.open_questions:
            parts.append("<p class='label'>Open questions (for human "
                         "resolution — not evidence)</p>"
                         + _bullets(cr.open_questions))

    # Audit summary
    audit = report.provenance_audit
    if audit:
        counts = " &middot; ".join(
            f"{k.replace('_', ' ')}: {_esc(audit.get(k, 0))}" for k in _AUDIT_KEYS)
        parts.append("<h2>Provenance audit</h2><p>" + counts + "</p>")
        if audit.get("violations"):
            parts.append("<p class='label'>Violations</p>"
                         + _bullets(audit["violations"]))

    footer = (
        f"<div id='footer_content' class='footer'>{_esc(report.strategy_id)} "
        f"&middot; {_esc(ts)} &middot; Page <pdf:pagenumber/> of "
        "<pdf:pagecount/></div>"
    )

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
      @page {{
        size: a4;
        margin: 1.8cm 1.5cm 2.2cm 1.5cm;
        @frame footer {{
          -pdf-frame-content: footer_content;
          bottom: 1.0cm; left: 1.5cm; right: 1.5cm; height: 1.2cm;
        }}
      }}
      body {{ font-family: "Times New Roman", serif; font-size: 10pt;
             color: #1a1a1a; }}
      h1 {{ font-size: 22pt; margin: 0; }}
      h2 {{ font-size: 13pt; border-bottom: 1px solid #bbbbbb;
            padding-bottom: 2pt; margin-top: 16pt; }}
      h3 {{ font-size: 11pt; margin: 12pt 0 2pt 0; }}
      p {{ margin: 4pt 0; }}
      .sub {{ color: #555555; font-size: 9pt; margin: 2pt 0 0 0; }}
      .verdict {{ font-size: 18pt; font-weight: bold; }}
      .label {{ font-weight: bold; margin: 6pt 0 0 0; }}
      .flags {{ color: #8B1A1A; font-weight: bold; }}
      .ok {{ color: #1B5E20; }}
      table {{ border-collapse: collapse; width: 100%; margin: 6pt 0; }}
      th, td {{ border: 1px solid #cccccc; padding: 3pt 5pt; text-align: left;
               font-size: 9pt; vertical-align: top; }}
      th {{ background-color: #f0f0f0; }}
      table.plain, table.plain td {{ border: none; padding: 0; }}
      .footer {{ color: #777777; font-size: 8pt; text-align: center; }}
    </style></head><body>
      {''.join(p for p in parts if p)}
      {footer}
    </body></html>"""


def render_report_pdf(report: RunReport) -> bytes:
    """Render the report to PDF bytes via xhtml2pdf (lazy import: ui extra)."""
    from io import BytesIO

    from xhtml2pdf import pisa

    buf = BytesIO()
    result = pisa.CreatePDF(src=report_to_html(report), dest=buf,
                            encoding="utf-8")
    if result.err:
        raise RuntimeError(f"PDF generation failed with {result.err} error(s)")
    return buf.getvalue()
