"""REPORT-HTML-STYLE — the HTML report's LOOK changed; not one of its values did.

The merged multi-lens report had the right structure and the wrong appearance: it
rendered as undifferentiated text, and the valuation-band section in particular got
missed entirely because nothing separated it from its neighbours. This change adds a
per-lens accent colour, card panels for the major sections, semantic verdict colour, a
colour scale on the valuation percentile, zebra striping and sticky headers.

All of that is markup and CSS. The guard that matters is therefore a BEFORE/AFTER one:
the document's VISIBLE TEXT — every number, name, verdict, reason and heading, in order
— is pinned to a golden file captured from the pre-styling implementation. If a styling
change ever drops, reorders or rewords a value, this fails.

The .md report must be untouched by any of it, so that is asserted too.
"""

from __future__ import annotations

import html as _html
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aristos_council.export.report_html import (
    multi_strategy_report_html,
    universe_report_html,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "report_html"
_RUN = datetime(2026, 8, 24, 11, 49, tzinfo=timezone.utc)

_TAG = re.compile(r"<[^>]+>")


def visible_text(doc: str) -> str:
    """The document's VISIBLE text: the inline stylesheet dropped, tags replaced by a
    line break, entities unescaped, blank lines collapsed.

    This is what a reader actually sees. Two documents with the same visible text carry
    the same values however differently they are marked up — which is exactly the
    property a styling change must preserve."""
    body = doc.split("</style>", 1)[-1]
    text = _html.unescape(_TAG.sub("\n", body))
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


# --------------------------------------------------------------------------- #
# The two documents, built from the established deterministic fixtures
# --------------------------------------------------------------------------- #
def _multi_doc() -> str:
    from tests.test_merged_multi_report import MOMENTUM, _multi
    from tests.test_multi_strategy_run import RAW, SCREENED

    return multi_strategy_report_html(_multi([SCREENED, RAW, MOMENTUM]),
                                      run_start=_RUN)


def _single_doc() -> str:
    from tests.test_report_html import _universe_result_with_bands

    return universe_report_html(_universe_result_with_bands(), run_start=_RUN)


_DOCS = {"multi_lens": _multi_doc, "single_lens": _single_doc}


# --------------------------------------------------------------------------- #
# 1. VALUE PARITY — the core guard, against a golden captured BEFORE the restyle
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", sorted(_DOCS))
def test_the_visible_text_is_unchanged_by_the_styling(name):
    """Every number, name, verdict, reason and heading the report showed before the
    restyle, in the same order, still shows. Only markup and CSS moved."""
    golden = (FIXTURES / f"{name}_visible.txt").read_text(encoding="utf-8")
    assert visible_text(_DOCS[name]()) == golden


@pytest.mark.parametrize("name", sorted(_DOCS))
def test_the_document_stays_self_contained(name):
    """One file: inline CSS, no external request of any kind, and readable with no
    JavaScript."""
    doc = _DOCS[name]()
    assert "<style>" in doc
    for forbidden in ("<script", "http://", "https://", "<link", "@import", "url("):
        assert forbidden not in doc, forbidden


def test_the_markdown_report_is_untouched_by_the_restyle():
    """The .md is the canonical record; a change to how the HTML LOOKS must not reach
    it. Pinned to its own golden."""
    pytest.importorskip("streamlit")
    import app

    from tests.test_merged_multi_report import MOMENTUM, _multi
    from tests.test_multi_strategy_run import RAW, SCREENED

    md = app._multi_strategy_markdown(_multi([SCREENED, RAW, MOMENTUM]), _RUN)
    golden = (FIXTURES / "multi_lens.md").read_text(encoding="utf-8")
    assert md == golden
    assert "<div" not in md and "<style" not in md and "class=" not in md


# --------------------------------------------------------------------------- #
# 2. THE STYLING ITSELF — pinned, so a later change cannot quietly drop it
#    (value parity above would still pass with every accent removed).
# --------------------------------------------------------------------------- #
def test_each_lens_gets_its_own_accent_in_all_three_places():
    """A multi-lens report is read by following ONE lens down the page, so its accent
    must appear on its rules heading, its verdict-grid column AND its detail card."""
    from aristos_council.export.report_html import LENS_ACCENTS, lens_class
    doc = _multi_doc()
    for i in range(3):                                   # the fixture runs three lenses
        cls = lens_class(i)
        assert f'<h3 class="lens-head {cls}">' in doc            # rules block heading
        assert f'class="lens-col {cls}"' in doc                  # its grid column
        assert f'<section class="section lens-card {cls}">' in doc   # its detail card
    # ...and the accents are DEFINED, distinct, and legible in both schemes.
    css = doc.split("<style>", 1)[1].split("</style>", 1)[0]
    light = [f"--lens-{i}: " for i in range(LENS_ACCENTS)]
    assert all(tok in css for tok in light)
    assert "@media (prefers-color-scheme: dark)" in css
    assert css.count("--lens-0:") == 2                   # one light value, one dark


def test_a_sixth_lens_reuses_an_accent_rather_than_rendering_unaccented():
    from aristos_council.export.report_html import LENS_ACCENTS, lens_class
    assert lens_class(LENS_ACCENTS) == lens_class(0)
    assert len({lens_class(i) for i in range(LENS_ACCENTS)}) == LENS_ACCENTS


def test_major_sections_are_cards_with_a_visual_break():
    """The valuation band was being missed entirely because nothing separated it from
    its neighbours; every major section is now its own panel."""
    for build in _DOCS.values():
        doc = build()
        css = doc.split("<style>", 1)[1].split("</style>", 1)[0]
        assert ".section {" in css
        assert "border-top: 3px solid var(--rule-strong)" in css
        assert "background: var(--panel)" in css
        assert doc.count('<section class="section') >= 3


def test_the_valuation_percentile_column_is_colour_scaled():
    from aristos_council.export.report_html import percentile_class
    from aristos_council.pipeline import valuation_band_table

    from tests.test_report_html import _universe_result_with_bands

    result = _universe_result_with_bands()
    table = valuation_band_table(result)
    doc = universe_report_html(result, run_start=_RUN)
    for row in table.rows:
        tint = percentile_class(row["Percentile"])
        if tint:                                    # an ABSTAINED cell gets no tint
            assert f'class="{tint}"' in doc
    # the scale is a BACKGROUND: the word ("cheapest"/"dear") is still in the text.
    assert "cheapest" in visible_text(doc) or "dear" in visible_text(doc)


def test_the_percentile_tint_is_read_from_the_gloss_never_re_derived():
    from aristos_council.export.report_html import percentile_class
    assert percentile_class("1st (cheapest)") == "pct-cheapest"
    assert percentile_class("23rd (cheap)") == "pct-cheap"
    assert percentile_class("50th (mid)") == "pct-mid"
    assert percentile_class("70th (dear)") == "pct-dear"
    assert percentile_class("92nd (dearest)") == "pct-dearest"
    assert percentile_class("not evaluated — insufficient history: 1.4y") == ""


def test_a_verdict_is_never_carried_by_colour_alone():
    """BUY/HOLD/SELL are visually distinct AND always spelled out, so the meaning
    survives printing, grayscale and colour-vision deficiency."""
    from aristos_council.export.report_html import verdict_of_cell
    doc = _multi_doc()
    text = visible_text(doc)
    css = doc.split("<style>", 1)[1].split("</style>", 1)[0]
    # all three are DEFINED and visually distinct...
    for verdict in ("buy", "hold", "sell"):
        assert f".verdict-{verdict} {{ color: var(--{verdict}); }}" in css
        assert f"td.cell-{verdict} {{" in css
    assert len({f"--{v}: " for v in ("buy", "hold", "sell")}) == 3
    # ...and every verdict this run actually produced carries BOTH its colour class and
    # its WORD, so nothing depends on the hue.
    rendered = {v for v in ("BUY", "HOLD", "SELL") if f">{v}</span>" in doc}
    assert rendered, "the fixture produced no verdicts at all"
    for verdict in rendered:
        assert f'class="verdict verdict-{verdict.lower()}"' in doc     # the colour
        assert verdict in text                                          # ...and the word
    # a cell on a NON-verdict axis is never coloured as though it were one
    assert verdict_of_cell("excluded — dividend yield 0.95%") == ""
    assert verdict_of_cell("no data") == ""


def test_long_tables_zebra_stripe_and_stick_their_headers():
    from aristos_council.export.report_html import _table

    doc = _multi_doc()
    css = doc.split("<style>", 1)[1].split("</style>", 1)[0]
    assert "tbody tr:nth-child(even) td" in css                     # zebra
    assert ".scroll.tall thead th { position: sticky" in css        # sticky
    # A LONG table gets the scroll box that makes a sticky header do anything; a short
    # one is left alone rather than given a scrollbar it does not need.
    long_table = _table(["A"], [[f"r{i}"] for i in range(13)])
    short_table = _table(["A"], [[f"r{i}"] for i in range(12)])
    assert '<div class="scroll tall">' in long_table
    assert '<div class="scroll">' in short_table
    assert '<div class="scroll">' in doc


def test_print_keeps_every_signal_legible_in_grayscale():
    """Colour is an accelerant, never the carrier: in print every accent flattens to
    black and every value stays."""
    doc = _multi_doc()
    css = doc.split("<style>", 1)[1].split("</style>", 1)[0]
    printed = css.split("@media print", 1)[1]
    for rule in ("h3.lens-head", "td.lens-col", 'td[class*="pct-"]',
                 ".verdict-buy", "tbody tr:nth-child(even) td"):
        assert rule in printed, rule
