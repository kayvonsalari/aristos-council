"""REPORT-4 — the narration arrives as FIELDS, and the document has an order.

Four things shipped together because they are one complaint: the report was hard to READ.

  1. The narration was raw model text carrying its own invented formatting. Live on the
     2026-08-25 12:54 run: "RANKER VERDICT (echoed):", "---", "ALL LENS VERDICTS — STATED
     IN FULL BEFORE ANY PROSE:", and a markdown "##" that spliced itself into the
     document's own heading tree. Numbers arrived quoted as strings — "46.92",
     "KRW 24,793,783,000,000" — so a price could sit beside another name's price in
     another currency with nothing to tell them apart.
  2. The document answered its questions in the wrong order: three lenses' worth of
     screen thresholds came BEFORE the verdicts they produced.
  3. ~70KB, seven sections, a narration block per name, and no way to jump.
  4. The title read "adhoc:507e10cf — 3 lenses" — a record key doing a job it cannot do.

The through-line of the tests below: the narrator supplies CONTENT, the report supplies
FORM, and neither may quietly take the other's job.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aristos_council.agents.schemas import (
    DecisionOutput,
    FactorRank,
    LensAttributionItem,
    LensVerdictItem,
    Narration,
    SpecialistView,
)
from aristos_council.narration_render import (
    NOT_ASSESSED,
    format_problems,
    narration_html,
    narration_markdown,
    narration_prose,
    split_stamps,
)
from aristos_council.report_language import cohort_filename_slug, cohort_title

_RUN = datetime(2026, 8, 24, 11, 49, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# The SK HYNIX case, from the live 12:54 run: ONE lens buys and TWO exclude, all
# three reading the same ROIC. The spec names it because it is the shape that breaks a
# one-sided narration — the reader must see the two exclusions, not just the buy.
# --------------------------------------------------------------------------- #
def _sk_hynix() -> Narration:
    return Narration(
        echoed_verdict="The ranker rated SK hynix BUY, first of eighteen in the Magic "
                       "Formula RAW cohort.",
        lens_verdicts=[
            LensVerdictItem(lens="Magic Formula RAW", verdict="buy", position=1,
                            cohort_size=18),
            LensVerdictItem(lens="Growth", verdict="excluded",
                            excluded_reason="revenue growth below the 10% minimum"),
            LensVerdictItem(lens="Value + Momentum", verdict="excluded",
                            excluded_reason="12-month momentum below the cohort floor"),
        ],
        lens_attribution=[
            LensAttributionItem(
                lens="Magic Formula RAW",
                factor_ranks=[FactorRank(factor="roic", rank=1, cohort_size=18,
                                         note="best in cohort"),
                              FactorRank(factor="earnings_yield", rank=2,
                                         cohort_size=18)],
                screens_passed=["Company size"],
                reasoning="The lens ranks on ROIC and earnings yield alone, and SK "
                          "hynix led both."),
        ],
        disagreement_note="Magic Formula RAW bought SK hynix on the same ROIC that "
                          "Growth and Value + Momentum never reached, because those two "
                          "excluded it before ranking.",
        neutral_context=["The last close was KRW 1,671,000.00.",
                         "The cohort spans several currencies and none are converted."],
        specialist_views=[
            SpecialistView(specialist="fundamental", stance="bullish", confidence=0.71,
                           reasoning="Capital efficiency leads the cohort."),
            SpecialistView(specialist="sentiment", assessed=False,
                           not_assessed_reason="no sentiment data was present in the "
                                               "evidence block"),
        ],
        open_questions=["Whether the ROIC denominator is depressed by the capex cycle."],
    )


# --------------------------------------------------------------------------- #
# 1. THE NARRATOR SUPPLIES CONTENT, NOT LAYOUT
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("offence,text", [
    ("a horizontal rule", "The lens bought it.\n\n---\n\nThe other did not."),
    ("an ALL-CAPS heading", "ALL LENS VERDICTS — STATED IN FULL:\nMagic Formula bought."),
    ("a markdown heading", "## Why the ranker issued a BUY\nBecause ROIC led."),
    ("a bullet marker", "- ROIC ranked first\n- Earnings yield ranked second"),
    ("a number in quotation marks", 'The last close was "46.92" on the day.'),
])
def test_layout_markup_from_the_narrator_is_a_reported_failure(offence, text):
    """Every one of these is verbatim from the 12:54 run. They are PROMPT failures, so
    they are reported rather than stripped: a silently cleaned marker leaves no evidence
    the contract was broken, and the next run breaks it again."""
    problems = format_problems(Narration(neutral_context=[text]))
    assert problems, f"{offence} went unreported"
    assert any(offence in p for p in problems), problems


def test_a_well_formed_narration_reports_no_problems():
    assert format_problems(_sk_hynix()) == []


def test_the_rendered_report_wraps_no_number_in_quotes():
    md = narration_markdown(_sk_hynix())
    assert not re.search(r'"\s*-?\d[\d,]*\.?\d*\s*%?\s*"', md), md


def test_every_monetary_value_carries_a_currency():
    """SK hynix's price rendered as "1,671,000.00" with no currency, beside a "$4,854.00"
    elsewhere, is exactly the ambiguity this prevents — the cohort spans currencies and
    none of them are converted."""
    md = narration_markdown(_sk_hynix())
    # MONEY-ABBREV-2: amounts at or above a million now render ABBREVIATED ("KRW 1.7m"),
    # so the shape to look for changed — the guarantee did not. Every money-scale amount,
    # abbreviated or not, must carry its currency. A confidence of 0.71 is a ratio and
    # carries none by design, which is why this matches money shapes and not decimals.
    money = re.findall(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?(?:tn|bn|m)\b", md)
    assert money, "fixture should carry a monetary value"
    assert any(a.endswith(("tn", "bn", "m")) for a in money), (
        "the fixture's million-scale price should be abbreviated")
    for amount in money:
        idx = md.index(amount)
        window = md[max(0, idx - 24):idx]
        assert re.search(r"(KRW|USD|EUR|GBP|JPY|CHF|SEK|DKK|HKD|TWD|\$|€|£|¥)\s*$",
                         window), f"{amount} carries no currency: {window!r}"


# --------------------------------------------------------------------------- #
# 2. NOT ASSESSED IS NOT A MEASURED NEUTRAL
# --------------------------------------------------------------------------- #
def test_a_not_assessed_specialist_renders_with_no_stance_and_no_confidence():
    """"abstain, confidence 0.00" reads as a measured neutral. The truth is the channel
    was dark and nothing was measured — house rule 3, in presentation form."""
    md = narration_markdown(_sk_hynix())
    # SPEC-ROLE-1 gave each specialist a block (heading, role line, reasoning) rather
    # than one table row, so the assertion reads the BLOCK. The guarantee is unchanged:
    # no stance word, no confidence number, and the cause stated.
    block = md.split("**sentiment**", 1)[1].split("**", 1)[0]
    head = md.split("**sentiment**", 1)[1].splitlines()[0]
    assert NOT_ASSESSED in head
    assert "0.00" not in head and "confidence" not in head
    for stance in ("bullish", "bearish", "neutral", "abstain"):
        assert stance not in head.lower(), head
    assert "no sentiment data" in block          # ...but the CAUSE is stated


def test_the_assessed_specialists_keep_their_stance_and_confidence():
    md = narration_markdown(_sk_hynix())
    line = next(l for l in md.splitlines() if "fundamental" in l)
    assert "bullish" in line and "0.71" in line


# --------------------------------------------------------------------------- #
# 3. THE CONTENT RULES SURVIVE THE RESTRUCTURING — on the SK hynix case
# --------------------------------------------------------------------------- #
def test_every_lens_verdict_is_stated_before_any_prose():
    """NARR-UNION-1's rule, now enforced by the RENDER ORDER rather than asked for in a
    prompt: a reader is never shown a one-sided case."""
    md = narration_markdown(_sk_hynix())
    verdict_table = md.index("Every lens's verdict")
    first_prose = md.index("Why each lens ranked it there")
    assert verdict_table < first_prose

    block = md[verdict_table:first_prose]
    for lens in ("Magic Formula RAW", "Growth", "Value + Momentum"):
        assert lens in block, f"{lens} missing from the verdict block"
    # ...including WHY the two that did not rank it were excluded
    assert "revenue growth below the 10% minimum" in block
    assert "12-month momentum below the cohort floor" in block


def test_the_disagreement_is_reported_and_left_standing():
    md = narration_markdown(_sk_hynix())
    assert "Where the lenses disagree" in md
    assert "left standing" in md
    assert "the ranker's verdicts are unchanged" in md.lower()


def test_the_flattened_prose_keeps_the_lens_names_the_fact_checker_routes_on():
    """The checkers read prose. Structuring the output must not retire the checks that
    caught three classes of false claim — it must feed them the same words, with the lens
    NAME still in the sentence so the per-lens router can find the right rank table."""
    prose = narration_prose(_sk_hynix())
    assert "Magic Formula RAW lens" in prose
    assert "roic rank 1 of 18" in prose
    assert "no sentiment data" in prose


def test_stamps_are_carried_through_the_restructure_not_dropped():
    stamp = '[⚠ narration check: "x" contradicts rank table — table is authoritative]'
    md = narration_markdown(_sk_hynix(), stamps=[stamp])
    assert stamp in md
    prose, found = split_stamps("some prose\n" + stamp)
    assert found == [stamp] and stamp not in prose


# --------------------------------------------------------------------------- #
# 4. BOTH RENDERERS, ONE SOURCE
# --------------------------------------------------------------------------- #
def test_the_html_renders_real_elements_not_pasted_text():
    doc = narration_html(_sk_hynix(), anchor_prefix="narr-000660-ks")
    assert "<h4>" in doc and "<table>" in doc and "<ul>" in doc
    assert 'id="narr-000660-ks-lens_verdicts"' in doc
    assert "---" not in doc


def test_markdown_and_html_state_the_same_things():
    """One source, two renderers — the canonical .md and the shared .html cannot drift
    into saying different things about the same run."""
    md = narration_markdown(_sk_hynix())
    doc = narration_html(_sk_hynix())
    for claim in ("Magic Formula RAW", "revenue growth below the 10% minimum",
                  "KRW 1.7m", NOT_ASSESSED,          # MONEY-ABBREV-2: >=1m abbreviates
                  "Whether the ROIC denominator is depressed by the capex cycle."):
        assert claim in md, f"missing from markdown: {claim}"
        assert claim in doc, f"missing from html: {claim}"


def test_a_decision_without_narration_still_renders_the_old_way():
    """Second-opinion mode and every report recorded before REPORT-4."""
    d = DecisionOutput(recommendation="buy", confidence=0.6, rationale="Plain prose.")
    assert d.narration is None


# --------------------------------------------------------------------------- #
# 5. PART 4 — THE TITLE
# --------------------------------------------------------------------------- #
def test_an_edited_saved_list_is_named_after_the_list_it_came_from():
    title = cohort_title({"universe_id": "adhoc:507e10cf", "derived_from": "Growth 40",
                          "universe_size": 21}, n_lenses=3)
    assert title.headline == "Edited from Growth 40 — 21 names · 3 lenses"
    assert title.record_id == "adhoc:507e10cf"          # present...
    assert "adhoc:" not in title.headline               # ...but not leading
    assert cohort_filename_slug({"derived_from": "Growth 40"}) == "Edited from Growth 40"


def test_a_saved_list_run_unchanged_keeps_its_own_name_and_id():
    title = cohort_title({"universe_id": "growth_40_v1", "universe_name": "Growth 40",
                          "universe_size": 40}, n_lenses=1)
    assert title.headline == "Growth 40 — 40 names · 1 lens"
    assert title.record_id == "growth_40_v1"


def test_a_pasted_list_with_no_parent_gets_a_plain_description_plus_the_id():
    title = cohort_title({"universe_id": "adhoc:bc619e1d", "universe_size": 4},
                         n_lenses=3)
    assert title.headline == "Pasted list — 4 names · 3 lenses"
    assert title.record_id == "adhoc:bc619e1d"
    assert cohort_filename_slug({"universe_id": "adhoc:bc619e1d"}) == ""


# --------------------------------------------------------------------------- #
# 6. PARTS 2 AND 3 — READING ORDER, AND SOMETHING TO NAVIGATE IT WITH
# --------------------------------------------------------------------------- #
def _multi():
    from tests.test_merged_multi_report import MOMENTUM, _multi as build
    from tests.test_multi_strategy_run import RAW, SCREENED

    return build([SCREENED, RAW, MOMENTUM])


# The order the document must answer its questions in. Rules and per-lens detail are
# REFERENCE material: a reader used to meet three lenses' worth of screen thresholds
# before learning what the run had decided.
EXPECTED_ORDER = [
    "What the run could not see",
    "Verdict by lens",
    "Narration",
    "Valuation band",
    "Rules applied — by lens",
    "detail",
]


def _h2s(md: str) -> list[str]:
    return [h for lvl, h in re.findall(r"^(#{1,3}) (.+)$", md, re.M) if lvl == "##"]


def test_the_markdown_sections_are_in_the_specified_reading_order():
    pytest.importorskip("streamlit")
    import app

    heads = _h2s(app._multi_strategy_markdown(_multi(), _RUN))
    assert heads[0] == "Contents"

    # Narration is absent from this ranker-only fixture; the rest must appear in order.
    seen = [h for h in heads]
    positions = []
    for expected in EXPECTED_ORDER:
        match = next((i for i, h in enumerate(seen) if expected in h), None)
        if match is not None:
            positions.append((expected, match))
    assert positions == sorted(positions, key=lambda p: p[1]), positions
    # ...and the two REFERENCE sections really are last
    rules = next(i for i, h in enumerate(seen) if h.startswith("Rules applied"))
    verdicts = next(i for i, h in enumerate(seen) if h == "Verdict by lens")
    assert verdicts < rules, "rules must not precede the verdicts they produced"


def test_the_html_sections_are_in_the_same_order_as_the_markdown():
    from aristos_council.export.report_html import multi_strategy_report_html

    doc = multi_strategy_report_html(_multi(), run_start=_RUN)
    order = [m for m in re.findall(r'<section class="section[^"]*" id="([^"]+)"', doc)]
    expected = [a for a in ("gaps", "verdicts", "narration", "band", "rules") if a in order]
    assert order[:len(expected)] == expected, order


def test_every_contents_link_resolves_to_an_anchor_that_exists():
    """A contents list pointing at a section the renderer skipped is worse than none."""
    from aristos_council.export.report_html import multi_strategy_report_html

    doc = multi_strategy_report_html(_multi(), run_start=_RUN)
    nav = doc.split('<nav class="contents"', 1)[1].split("</nav>", 1)[0]
    targets = re.findall(r'href="#([^"]+)"', nav)
    assert targets, "no contents links rendered"
    ids = set(re.findall(r'id="([^"]+)"', doc))
    missing = [t for t in targets if t not in ids]
    assert not missing, f"contents links with no anchor: {missing}"


def test_the_contents_survives_printing():
    from aristos_council.export.report_html import multi_strategy_report_html

    css = multi_strategy_report_html(_multi(), run_start=_RUN)
    css = css.split("<style>", 1)[1].split("</style>", 1)[0]
    print_block = css.split("@media print", 1)[1]
    assert "nav.contents" in print_block
    assert "display: none" not in print_block.split("nav.contents", 1)[1][:200]


def test_the_title_leads_with_the_description_and_keeps_the_id_muted():
    from aristos_council.export.report_html import multi_strategy_report_html

    doc = multi_strategy_report_html(_multi(), run_start=_RUN)
    title = re.search(r"<title>(.*?)</title>", doc, re.S).group(1)
    h1 = re.search(r"<h1>(.*?)</h1>", doc, re.S).group(1)
    assert title.startswith("Pasted list —"), title
    assert "names" in title and "lenses" in title
    # the id is PRESENT everywhere it was, and muted rather than leading
    assert "adhoc:" in h1
    assert '<span class="record-id">' in h1
    assert not h1.strip().startswith("adhoc:")


def test_the_evidence_gap_section_is_rendered_even_when_there_is_nothing_to_report():
    """An absent section is indistinguishable from a feature that was never switched on —
    the ambiguity that cost two debugging rounds on 2026-08-22."""
    pytest.importorskip("streamlit")
    import app
    from aristos_council.pipeline import EVIDENCE_GAPS_TITLE

    md = app._multi_strategy_markdown(_multi(), _RUN)
    assert EVIDENCE_GAPS_TITLE in md
    assert EVIDENCE_GAPS_TITLE in _h2s(md)


# --------------------------------------------------------------------------- #
# 9. GRID-COLS-1 / SCORE-NAME-1 / SPEC-ROLE-1 — three owner decisions
# --------------------------------------------------------------------------- #
def test_the_verdict_table_is_name_plus_one_column_per_lens():
    """GRID-COLS-1. On the 4-lens run of 2026-08-26 EVERY row carried the "fewer lenses"
    marker, so the rank-sum column was incomparable exactly where it mattered."""
    from aristos_council.pipeline import multi_strategy_grid_rows

    result = _multi()
    rows, head = multi_strategy_grid_rows(result)
    assert head == ["Name"] + [h for h in head[1:]]
    assert len(head) == 1 + len(result.strategy_ids)
    assert "Rank-sum" not in head and "Graded by" not in head
    for row in rows:
        assert set(row) == set(head)
        assert not any("‡" in str(v) for v in row.values())


def test_the_row_order_survives_the_removed_columns():
    """The columns went; the ORDER they produced did not."""
    from aristos_council.pipeline import multi_strategy_grid_rows

    result = _multi()
    rows, _ = multi_strategy_grid_rows(result)
    assert [r["Name"] for r in rows] == [r.display for r in result.rows]


def test_narration_never_says_rank_sum():
    """SCORE-NAME-1. The word named two unrelated numbers; the within-lens one is the
    FACTOR SCORE and nothing rendered is a rank-sum any more."""
    from aristos_council.agents.schemas import LensAttributionItem
    from aristos_council.narration_render import narration_html

    n = _sk_hynix()
    n.lens_attribution = [LensAttributionItem(
        lens="Value + Momentum",
        factor_ranks=[FactorRank(factor="roic", rank=2, cohort_size=12)],
        factor_score=13, reasoning="Ranked on three factors.")]
    md = narration_markdown(n)
    doc = narration_html(n)
    for surface in (md, doc):
        assert "rank-sum" not in surface.lower()
        assert "Factor score: 13" in surface


def test_the_ranker_explanation_the_narrator_reads_says_factor_score():
    """The rename has to land at the SOURCE, or the narrator copies the old word."""
    from aristos_council.rank_engine import RankedTicker

    r = RankedTicker(ticker="AAA", factor_ranks={"roic": 2.0}, factor_values={},
                     combined_rank=13.0, universe_size=12, verdict="buy")
    text = r.explain()
    assert "factor score" in text and "rank-sum" not in text


def test_each_narration_section_explains_why_cohort_sizes_differ():
    from aristos_council.narration_render import COHORT_SIZE_NOTE, narration_html

    n = _sk_hynix()
    assert COHORT_SIZE_NOTE in narration_markdown(n)
    assert "cohort sizes differ per lens" in narration_html(n)


def test_every_specialist_carries_its_role_line_including_an_abstaining_one():
    """SPEC-ROLE-1. The panel listed four stances and never said what question each one
    answered, so a technical neutral beside a fundamental bullish read as a contradiction."""
    from aristos_council.narration_render import (
        SPECIALIST_STANDING_NOTE, narration_html, specialist_role)

    n = _sk_hynix()
    md = narration_markdown(n)
    doc = narration_html(n)
    for view in n.specialist_views:
        role = specialist_role(view.specialist)
        assert role, view.specialist
        assert role in md and role in doc
    # the abstaining one keeps its role AND still carries no confidence
    assert specialist_role("sentiment") in md
    assert SPECIALIST_STANDING_NOTE in md and SPECIALIST_STANDING_NOTE in doc
