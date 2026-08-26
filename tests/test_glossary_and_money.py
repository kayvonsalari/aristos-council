"""GLOSSARY-1 / RULES-TOP-1 / MONEY-ABBREV-2 / MOMENTUM-GLOSS-1.

Four report changes, one theme: a reader who is not a finance professional should be able
to read the document without leaving it.

  * every term it uses is defined at the bottom, from the REGISTRIES so the next factor
    or rule added arrives explained;
  * the lenses are named at the top, where their verdicts are, not only in the reference
    tables far below;
  * a thirteen-digit figure is a number you COUNT, not one you read;
  * two bare momentum percentages state facts and hide the relationship between them.
"""

from __future__ import annotations

import re

import pytest

from aristos_council.agents.schemas import Narration, SpecialistView
from aristos_council.factors import FACTOR_REGISTRY
from aristos_council.glossary import glossary_entries, glossary_markdown
from aristos_council.narration_render import (
    gloss_momentum_in_text,
    momentum_gloss,
    narration_markdown,
)
from aristos_council.tools.criteria.registry import REGISTRY
from aristos_council.tools.price_context import format_money


def _multi():
    from tests.test_merged_multi_report import MOMENTUM, _multi as build
    from tests.test_multi_strategy_run import RAW, SCREENED

    return build([SCREENED, RAW, MOMENTUM])


# --------------------------------------------------------------------------- #
# 1. THE REGISTRIES CARRY THE DEFINITIONS — so the next addition arrives explained
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", sorted(FACTOR_REGISTRY))
def test_every_registered_factor_declares_a_glossary(name):
    """A registered factor with an empty glossary FAILS. This is the guard that keeps a
    hardcoded definition list from being necessary — and that stops the next factor
    shipping as a bare column heading nobody can read."""
    gloss = FACTOR_REGISTRY[name].glossary
    assert gloss.strip(), f"factor {name} has no plain-English definition"
    assert len(gloss.split()) >= 5, f"factor {name}'s definition is too terse to help"


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_registered_criterion_declares_a_glossary(name):
    gloss = REGISTRY[name].glossary
    assert gloss.strip(), f"criterion {name} has no plain-English definition"
    assert len(gloss.split()) >= 5, f"criterion {name}'s definition is too terse"


# --------------------------------------------------------------------------- #
# 2. THE SECTION HOLDS ONLY WHAT THE REPORT USED
# --------------------------------------------------------------------------- #
def test_the_glossary_holds_only_terms_the_report_actually_uses():
    """A glossary of every registered term would be a wall to scroll past, and a reader
    could not tell which entries bear on what they just read."""
    used = FACTOR_REGISTRY["roic"]
    entries = glossary_entries(factors=[used], criteria=[], text="ROIC and rank-sum.")
    terms = {e.term for e in entries}

    assert used.label in terms                       # the factor that ran
    assert "Rank-sum" in terms                       # a report term the text mentions
    # ...and nothing the run never touched
    assert not any("volatility" in t.lower() for t in terms)
    assert not any("dividend" in t.lower() for t in terms)


def test_a_term_the_text_never_mentions_is_left_out():
    entries = glossary_entries(factors=[], criteria=[], text="Nothing of note here.")
    assert [e.term for e in entries] == []


def test_the_definitions_avoid_jargon_inside_a_definition():
    """A definition that needs its own glossary has not defined anything."""
    entries = glossary_entries(factors=FACTOR_REGISTRY.values(),
                               criteria=REGISTRY.values(), text="")
    banned = ("EBIT/EV", "rank-sum", "quintile", "CAGR")
    for entry in entries:
        body = entry.definition
        for token in banned:
            # allowed in the term itself, never unexplained inside another's definition
            assert token.lower() not in body.lower() or token.lower() in entry.term.lower(), \
                f"{entry.term} defines itself with jargon: {token}"


def test_the_glossary_renders_as_the_last_section_of_the_markdown_report():
    pytest.importorskip("streamlit")
    import app
    from datetime import datetime, timezone

    from aristos_council.glossary import SECTION_TITLE

    md = app._multi_strategy_markdown(
        _multi(), datetime(2026, 8, 24, 11, 49, tzinfo=timezone.utc))
    heads = [h for lvl, h in re.findall(r"^(#{1,3}) (.+)$", md, re.M) if lvl == "##"]
    assert heads[-1] == SECTION_TITLE, heads[-3:]
    assert SECTION_TITLE in md


def test_the_glossary_renders_in_the_html_and_is_linked_from_the_contents():
    from datetime import datetime, timezone

    from aristos_council.export.report_html import multi_strategy_report_html

    doc = multi_strategy_report_html(
        _multi(), run_start=datetime(2026, 8, 24, 11, 49, tzinfo=timezone.utc))
    assert 'id="glossary"' in doc
    nav = doc.split('<nav class="contents"', 1)[1].split("</nav>", 1)[0]
    assert 'href="#glossary"' in nav


# --------------------------------------------------------------------------- #
# 3. RULES-TOP-1 — the compact line
# --------------------------------------------------------------------------- #
def test_the_compact_rules_line_names_every_lens_that_ran():
    from aristos_council.pipeline import compact_rules

    result = _multi()
    rows = compact_rules(result)
    assert len(rows) == len(result.strategy_ids)
    for sid in result.strategy_ids:
        label = result.strategy_names.get(sid) or sid
        assert any(r["lens"] == label for r in rows), label
    # a screen-less lens SAYS so rather than being dropped
    assert any("no screen" in r["summary"] for r in rows)
    # ...and a screened one states its rule count
    assert any(re.search(r"\d+ rules?", r["summary"]) for r in rows)


def test_the_compact_line_links_to_the_full_section_that_exists():
    from datetime import datetime, timezone

    from aristos_council.export.report_html import multi_strategy_report_html

    doc = multi_strategy_report_html(
        _multi(), run_start=datetime(2026, 8, 24, 11, 49, tzinfo=timezone.utc))
    assert 'class="rules-compact"' in doc
    assert 'href="#rules"' in doc
    assert 'id="rules"' in doc                     # the link resolves


# --------------------------------------------------------------------------- #
# 4. MONEY-ABBREV-2 — the shared rule reaches the NARRATION
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value,currency,expected", [
    (69_659_000_000, "USD", "$69.7bn"),
    (28_989_000, "USD", "$29.0m"),
    (950_000, "USD", "$950,000"),
    (24_793_783_000_000, "KRW", "KRW 24.8tn"),
])
def test_the_shared_helper_abbreviates_by_magnitude(value, currency, expected):
    assert format_money(value, currency, abbreviate=True) == expected


def test_a_narration_field_carrying_a_billion_scale_value_renders_abbreviated():
    """THE bug: MONEY-ABBREV-1 reached the structured fields and not the narrator's
    prose, so the 2026-08-26 15:40 report still read "USD 28,989,000,000" in three
    separate places. One helper, all surfaces."""
    n = Narration(
        echoed_verdict="x",
        neutral_context=["Free cash flow was USD 28,989,000,000 last year."],
        open_questions=["Is USD 64,134,000,000 enough to cover it?"],
        specialist_views=[SpecialistView(specialist="risk", stance="neutral",
                                         confidence=0.6,
                                         reasoning="FCF of USD 70,012,000,000 fell.")])
    md = narration_markdown(n)
    assert "$29.0bn" in md and "$64.1bn" in md and "$70.0bn" in md
    assert "28,989,000,000" not in md


def test_no_unabbreviated_billion_scale_figure_survives_in_a_rendered_report():
    """Regex-scan the whole HTML: nothing at or above a million may render raw."""
    from datetime import datetime, timezone

    from aristos_council.export.report_html import multi_strategy_report_html

    doc = multi_strategy_report_html(
        _multi(), run_start=datetime(2026, 8, 24, 11, 49, tzinfo=timezone.utc))
    body = doc.split("</style>", 1)[-1]
    # a currency marker against seven-plus digits with thousands groups
    offenders = re.findall(
        r"(?:[$€£¥]|\b(?:USD|EUR|GBP|KRW|JPY|CHF)\s?)\d{1,3}(?:,\d{3}){2,}", body)
    assert not offenders, offenders


def test_the_fact_checker_still_sees_full_precision():
    """Abbreviation is DISPLAY. The checker resolves claims against the ledger, so the
    prose it reads must carry the exact figure — otherwise honest numbers start looking
    unverifiable."""
    from aristos_council.narration_render import narration_prose

    n = Narration(echoed_verdict="x",
                  neutral_context=["FCF was USD 28,989,000,000."])
    assert "28,989,000,000" in narration_prose(n)


def test_the_full_precision_figure_is_available_on_hover():
    from aristos_council.narration_render import narration_html

    n = Narration(echoed_verdict="x",
                  neutral_context=["FCF was USD 28,989,000,000."])
    doc = narration_html(n)
    assert "28,989,000,000" in doc            # in the title attribute
    assert "$29.0m" in doc or "$29.0bn" in doc
    assert "title=" in doc


# --------------------------------------------------------------------------- #
# 5. MOMENTUM-GLOSS-1 — four sign cases, fixed wording, computed not freestyled
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("r12,r6,expect", [
    (12.0, 8.0, "sustained advance"),
    (-5.0, -3.0, "sustained weakness"),
    (-10.3, 32.4, "fell early, recovering since"),
    (20.0, -4.0, "rose early, giving it back since"),
])
def test_every_sign_case_has_fixed_wording(r12, r6, expect):
    assert expect in momentum_gloss(r12, r6)


def test_the_gloss_is_attached_where_the_pair_appears():
    text = "The 12-month price return is -10.3% and the 6-month return is +32.4%."
    out = gloss_momentum_in_text(text)
    assert "fell early, recovering since" in out
    assert "-10.3%" in out and "+32.4%" in out       # the values are untouched


def test_a_single_return_gets_no_gloss():
    """One return has no relationship to describe; inventing one would be the
    freestyling this replaces."""
    text = "The 12-month return is -10.3%."
    assert gloss_momentum_in_text(text) == text


def test_the_gloss_reaches_the_rendered_narration():
    n = Narration(echoed_verdict="x", neutral_context=[
        "The 12-month price return is -10.3% and the 6-month return is +32.4%."])
    assert "fell early, recovering since" in narration_markdown(n)
