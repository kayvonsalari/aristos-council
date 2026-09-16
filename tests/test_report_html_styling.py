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

GOLDEN UPDATED ONCE, DELIBERATELY (2026-08-25, NARR-UNION-1 follow-up). The goldens
pin VALUES against STYLING, not against every future content change, and one content
change was owed: the header's hardcoded "No LLM ran — narration stays a per-strategy
run" was true only while multi-lens runs were locked to ranker-only, and printed above
three narration sections once that lock was lifted. It is now DERIVED
(``pipeline.multi_header_line``). Exactly ONE of the 153 visible lines moved, and only
to the wording the single-lens header already used; the replacement is pinned by
``tests/test_narration_union.py::test_a_narrated_multi_lens_run_never_claims_no_LLM_ran``
so the guard did not simply get relaxed.

GOLDENS REGENERATED AGAIN, DELIBERATELY (2026-08-25, REPORT-4). That change REORDERS the
document, adds a contents list, adds a "what the run could not see" section and renders
the narration from the narrator's structured fields — so "the visible text is identical"
stops being the right question to ask of it. The goldens were rebuilt for the new
structure and the guarantee underneath was re-expressed ORDER-INDEPENDENTLY:
``test_no_value_changed_when_REPORT_4_reordered_the_document`` asserts the multiset of
numbers is unchanged against a snapshot of the pre-REPORT-4 render, in both the HTML and
the markdown. A presentation change may move a value; it may never alter one.

BASELINE MOVED ONCE MORE, DELIBERATELY (2026-08-26, GRID-COLS-1). The owner removed the
Rank-sum and Graded-by COLUMNS from the verdict table: on real portfolio runs almost every
row carried the "fewer lenses" marker, so the column was incomparable exactly where it was
most needed. That is a deliberate REMOVAL of rendered values, which the "nothing lost"
guard is built to catch — correctly. The snapshot is therefore re-taken at that point, so
the guard goes on protecting every change AFTER it. What is removed is recorded here
rather than exempted in the helper: the rank-sum figures and the "N of 3" graded counts,
and nothing else. The COMPUTATION behind them is untouched and still orders the rows,
which ``test_the_rank_sum_still_computed_even_though_it_is_no_longer_rendered`` pins.

GOLDENS MOVED AGAIN, DELIBERATELY (2026-09-16, BAND-2). The per-NAME section used to be
read off the FIRST lens, so a name that lens did not rank had no row — which made the
section's size an accident of lens ORDER (live, 2026-09-15: Defensive Income first gave 2
rows of a 121-name cohort, Magic Formula RAW first gave 81). It is now built over the
UNION of every lens's ranked names. In this fixture C is excluded by the screened lens and
ranked by the raw one, so C gains a row. The delta is exactly that, in both goldens: ONE
added row (``| **C** | 121.90 | not evaluated — only 0 weeks of closes | — |``) and its
four visible-text lines. Nothing was removed, reordered or reworded — `git show` on the
regeneration commit is +5/-0. The behaviour is pinned independently of these goldens by
``test_multi_strategy_run.py::test_band_is_computed_for_every_lens_not_just_the_first``
and the union-table unit tests beside it, so the guard was not simply relaxed.

GOLDENS MOVED AGAIN, DELIBERATELY (2026-09-16, CAPTION-1). Every lens now carries one
plain-English line saying what it ASKS OF A COMPANY, rendered under its name in the rules
section and under its detail heading — because two runs that day showed a BUY under a
value lens sitting beside a SELL under an income one with nothing on the page to say they
were answering different questions. The delta is +12/-0 across the two fixtures: four
added lines in the visible text, eight in the markdown (two lenses x two places, plus the
markdown's blank lines). Nothing was removed, reordered or reworded.

``magic_formula_v1`` is in this fixture and declares NO ``asks``, so it gains no line —
the absent-renders-nothing property demonstrated inside the golden itself rather than only
asserted in a unit test. The texts are pinned verbatim by ``tests/test_lens_asks.py``.

BASELINE MOVED AGAIN, DELIBERATELY (2026-09-16, SHORTLIST-1). A new DERIVED section --
the names the primary selector rated BUY that no check doubted -- is added directly after
the summary and before the verdict grid, and the summary line gains a "shortlist: N of M
BUYs" clause. Four fixtures move, and the "nothing lost" snapshot is re-taken here, which
is the GRID-COLS-1 treatment applied to an ADDITION rather than a removal.

What is added, isolated by rendering the same document with the shortlist suppressed
(which produced invented=[] lost=[], i.e. the rest of the document is untouched):
  - the summary line's new clause (the old line is REPLACED, not lost);
  - the contents entry and the section itself (title, rule sentence, one-row table);
  - the GLOSSARY entry for "Percentile (valuation band)". That one is not the section's
    own text: the glossary tracks terms the document actually USES, and this fixture's
    bands all abstain, so no percentile was mentioned anywhere until the rule sentence
    mentioned one. A new section bringing its vocabulary's definition with it is the
    glossary working, and it is why the guard reported 10/90/90% alongside the cutoff 80.
Nothing was removed and no value changed. The shortlist RULE is pinned independently of
every fixture here by ``tests/test_shortlist.py``, so the guard was not simply relaxed.
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


# REPORT-4 REORDERED the document and ADDED sections, so "the visible text is identical"
# is no longer the right shape of guard for it — the goldens above were regenerated for
# the new structure. The guarantee that must NOT weaken is the one underneath: a
# presentation change may move a value, never alter one. So parity is re-expressed
# order-INDEPENDENTLY, against a snapshot of the pre-REPORT-4 render, and it is strictly
# harder to satisfy by accident than a diff of two files nobody reads.
_PRE_REPORT4 = Path(__file__).resolve().parent / "fixtures" / "report_structure"
_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*%?")


def _numbers(text: str) -> list[str]:
    return sorted(_NUMBER.findall(text))


def _assert_values_survived(before: str, after: str) -> None:
    """Two things, and the pair is what makes this a real guard rather than a diff.

    NOTHING LOST — every number the document carried, it still carries, as many times.
    NOTHING INVENTED — a number that appears MORE often than before must be a value the
    document already held (REPORT-4 states the cohort size in the new title, so "21"
    legitimately appears once more). A value that is not in `before` at all cannot be a
    reordering; it is a new claim, and the assertion fails on it."""
    import collections

    cb = collections.Counter(_numbers(before))
    ca = collections.Counter(_numbers(after))
    lost = sorted((cb - ca).elements())
    assert not lost, f"values LOST in the restructure: {lost}"
    invented = sorted(set((ca - cb).elements()) - set(cb))
    assert not invented, f"values INVENTED by the restructure: {invented}"


def test_no_value_changed_when_REPORT_4_reordered_the_document():
    """Same multiset of numbers before and after the restructure — every verdict, rank,
    percentage and price survives, wherever the section it lives in has moved to."""
    before = (_PRE_REPORT4 / "pre_report4_visible.txt").read_text(encoding="utf-8")
    after = visible_text(_multi_doc())
    _assert_values_survived(before, after)


def test_no_value_changed_in_the_markdown_when_REPORT_4_reordered_it():
    pytest.importorskip("streamlit")
    import app

    from tests.test_merged_multi_report import MOMENTUM, _multi
    from tests.test_multi_strategy_run import RAW, SCREENED

    before = (_PRE_REPORT4 / "pre_report4.md").read_text(encoding="utf-8")
    after = app._multi_strategy_markdown(_multi([SCREENED, RAW, MOMENTUM]), _RUN)
    _assert_values_survived(before, after)


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
        # Matched on the CLASS, not on the whole tag: REPORT-4 added anchor ids to these
        # elements, and the property under test is the accent, not the attribute order.
        assert f'class="lens-head {cls}"' in doc                 # rules block heading
        assert f'class="lens-col {cls}"' in doc                  # its grid column
        assert f'class="section lens-card {cls}"' in doc         # its detail card
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
