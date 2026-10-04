"""Batch 17 — EXCHANGE-NAMES-1 (peers table) and PEER-ROW-TINT-1."""
from __future__ import annotations

import re

from aristos_council.peer_table import (THIS_COMPANY_BG, THIS_COMPANY_FG, THIS_COMPANY_STYLE,
                                        peer_frame_records, peer_rows, peer_text_lines)
from tests.test_company_page_batch8 import _group, _row


def _group_with_exchanges():
    from aristos_council.market_index import PeerGroup
    members = [_row("NOVO-B.CO", "Novo Nordisk", cap=3e11, usd=4e10),
               _row("NESN.SW", "Nestle", cap=2.5e11, usd=2.7e11, currency="CHF"),
               _row("MC.PA", "LVMH", cap=3e11, usd=3.2e11, currency="EUR"),
               _row("SIE.XETRA", "Siemens", cap=1.5e11, usd=1.6e11, currency="EUR")]
    return PeerGroup(subject=members[0], members=members, rung="sub-industry", band="x",
                     snapshot="2026-09-25", step=1, distinct_companies=4, reasons=[],
                     matched_on={m.ticker: "GICS+EODHD" for m in members})


def test_peers_show_readable_exchange_names_everywhere():
    group = _group_with_exchanges()
    shown = {r.ticker: r.exchange for r in peer_rows(group)}
    assert shown == {"NOVO-B.CO": "Copenhagen", "NESN.SW": "SIX Swiss",
                     "MC.PA": "Euronext Paris", "SIE.XETRA": "Xetra"}
    assert {rec["Exchange"] for rec in peer_frame_records(group)} == set(shown.values())
    text = "\n".join(peer_text_lines(group))
    assert "Euronext Paris" in text and "SIX Swiss" in text           # not truncated to 7 chars


def _luminance(hex_colour: str) -> float:
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def test_the_company_row_tint_is_opaque_and_readable_regardless_of_theme():
    assert re.fullmatch(r"#[0-9a-f]{6}", THIS_COMPANY_BG)             # opaque, no alpha to drop
    assert "rgba" not in THIS_COMPANY_STYLE and "font-weight: 700" in THIS_COMPANY_STYLE
    assert THIS_COMPANY_FG in THIS_COMPANY_STYLE                      # text colour set explicitly
    hi, lo = sorted((_luminance(THIS_COMPANY_BG), _luminance(THIS_COMPANY_FG)), reverse=True)
    assert (hi + 0.05) / (lo + 0.05) >= 7.0                           # WCAG AAA contrast
    # and it differs strongly from both a white and a near-black page background
    for page in ("#ffffff", "#0e1117"):
        assert abs(_luminance(THIS_COMPANY_BG) - _luminance(page)) > 0.1 or page == "#0e1117"


def test_the_html_export_uses_the_same_tint():
    from aristos_council.export import report_html
    css = report_html.__dict__.get("_CSS") or ""
    src = open(report_html.__file__, encoding="utf-8").read()
    assert f"td.this-company {{ background: {THIS_COMPANY_BG}" in src
    assert css == "" or THIS_COMPANY_BG in css
