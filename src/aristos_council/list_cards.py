"""UI-POLISH-1 item 7 - the list (Cohort / list) results in the same chips and table style as the
company page. Presentation only: every cell is the string the shared builders already produced
(``pipeline.lens_agreement_table``, ``multi_strategy_grid_rows``, the lens detail groups); this module
only decides how a cell LOOKS. A verdict word always shows beside its colour.
"""
from __future__ import annotations

import re

from . import ui_style as ui

_VOTES = re.compile(r"^\d+ of \d+")


def verdict_cell(text: str) -> str:
    """A grid cell such as ``#1 of 9 · BUY`` (or ``· doubted · ranked on 2 of 3 factors``,
    ``excluded — reason``, ``no data``) as HTML: the position muted, the verdict word as a chip, every
    other cell as muted text exactly as written."""
    parts = [p.strip() for p in str(text).split(" · ")]
    for i, part in enumerate(parts):
        if i and part:                                    # the verdict word is never the first part
            if ui.chip_kind(part) != "na" or part.lower() in _CHECK_WORDS:
                rest = [p for j, p in enumerate(parts) if j != i]
                lead = f'<span class="ar-num">{ui.esc(rest[0])}</span> ' if rest else ""
                tail = "".join(f'<span class="ar-detail">{ui.esc(p)}</span>' for p in rest[1:])
                return lead + ui.chip(part) + tail
    return f'<span class="ar-muted">{ui.esc(text)}</span>'


_CHECK_WORDS = {"clean", "no concern", "doubted"}


def grid_table(rows: list[dict], head: list[str]) -> str:
    cells = [[r.get(head[0], "")] + [verdict_cell(r.get(h, "")) for h in head[1:]] for r in rows]
    return ui.table(head, cells, raw_cols=range(1, len(head)))


def agreement_table(cols: list[str], rows: list[dict]) -> str:
    """The shortlist: BUY votes and SELL votes as a chip plus who voted; check columns as neutral chips;
    the valuation percentile in the mono face; marks as outlined labels."""
    out = []
    for r in rows:
        cells = []
        for c in cols:
            v = str(r.get(c, ""))
            if c == "BUY votes" and _VOTES.match(v):
                cells.append(ui.chip("BUY") + f'<span class="ar-detail">{ui.esc(v)}</span>')
            elif c == "SELL votes" and _VOTES.match(v):
                cells.append(ui.chip("SELL") + f'<span class="ar-detail">{ui.esc(v)}</span>')
            elif c == "Marks":
                cells.append("".join(ui.badge(m.strip()) + " " for m in v.split("·") if m.strip())
                             or '<span class="ar-muted">—</span>')
            elif c in ("Name", "BUY votes", "SELL votes", "Valuation percentile"):
                cells.append(ui.esc(v) if c != "Valuation percentile" else
                             f'<span class="ar-num">{ui.esc(v)}</span>')
            else:                                          # a check lens column: its own word, neutral
                cells.append(ui.chip(v, "na") if v else "")
        out.append(cells)
    return ui.table(cols, out, raw_cols=range(1, len(cols)))


def detail_table(rows: list[dict]) -> str:
    """A per-lens detail group (Name / Measured / Note), in the shared table style."""
    if not rows:
        return ""
    heads = list(rows[0])
    return ui.table(heads, [[str(r.get(h, "")) for h in heads] for r in rows],
                    num_cols=[i for i, h in enumerate(heads) if h == "Measured"])
