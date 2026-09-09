"""NARR-SCHEMA-1 — the narration's STRUCTURE is a contract, not a convention.

``narration_check`` already validated rank-claim CONTENT. Nothing validated SHAPE, so a
narration missing its lens verdicts, carrying a confidence of 4.2, quoting a figure that
will not parse, or echoing SELL over a ranker BUY would have reached a report unflagged.

The boundary between the two is deliberate and is asserted here, because the expensive
failure mode would be the two guards arguing about the same sentence:

    narration_check   ->  rank PROSE ("#2 of 12", "best in the cohort", tie claims)
    narration_schema  ->  the verdict WORD only, plus shape and parseability

Doctrine, inherited: ANNOTATE, NEVER REWRITE. A failing narration still ships, in full
and unchanged, with a visible banner in both the .md and the .html.
"""

from __future__ import annotations

import pytest

from aristos_council.agents.schemas import (
    FactorRank,
    LensAttributionItem,
    LensVerdictItem,
    Narration,
    SpecialistView,
)
from aristos_council.narration_render import narration_html, narration_markdown
from aristos_council.narration_schema import (
    REQUIRED_SECTIONS,
    issues_as_records,
    validate_narration,
)


def _well_formed() -> Narration:
    """What the narrator actually emits today — the required-section set is DERIVED from
    this shape, not invented alongside it."""
    return Narration(
        echoed_verdict="The ranker rated ASML BUY, first of twelve in the Growth cohort.",
        lens_verdicts=[
            LensVerdictItem(lens="Growth", verdict="buy", position=1, cohort_size=12),
            LensVerdictItem(lens="Classic Value", verdict="excluded",
                            excluded_reason="earnings yield below the floor"),
        ],
        lens_attribution=[LensAttributionItem(
            lens="Growth",
            factor_ranks=[FactorRank(factor="roic", rank=1, cohort_size=12)],
            screens_passed=["Company size"],
            reasoning="Led on ROIC at 21.5%, with $69.7bn of free cash flow.")],
        neutral_context=["The last close was EUR 612.30."],
        specialist_views=[
            SpecialistView(specialist="fundamental", stance="bullish", confidence=0.72,
                           reasoning="Capital efficiency leads the cohort."),
            SpecialistView(specialist="sentiment", assessed=False,
                           not_assessed_reason="Finnhub refused this listing (HTTP 403)"),
        ],
        open_questions=["Whether the capex cycle flatters ROIC."],
    )


def _codes(narration, **kw) -> list[str]:
    return [i.code for i in validate_narration(narration, **kw)]


# --------------------------------------------------------------------------- #
# 1. THE CLEAN CASE — a well-formed narration must pass silently
# --------------------------------------------------------------------------- #
def test_a_well_formed_narration_passes_clean():
    assert validate_narration(_well_formed(), ranker_verdict="buy") == []


def test_the_optional_sections_are_genuinely_optional():
    """disagreement_note, neutral_context, open_questions and money_series are legitimately
    empty for some names. Requiring them would push the narrator to invent filler, which
    is the opposite of what this guard is for."""
    n = _well_formed()
    n.disagreement_note = ""
    n.neutral_context = []
    n.open_questions = []
    n.money_series = []
    assert validate_narration(n, ranker_verdict="buy") == []


# --------------------------------------------------------------------------- #
# 2. (a) REQUIRED SECTIONS
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("section", REQUIRED_SECTIONS)
def test_a_missing_required_section_is_flagged(section):
    n = _well_formed()
    setattr(n, section, "" if isinstance(getattr(n, section), str) else [])
    codes = _codes(n, ranker_verdict="buy")
    assert f"missing_section:{section}" in codes, codes


def test_an_absent_narration_is_itself_a_structural_failure():
    issues = validate_narration(None, ranker_verdict="buy")
    assert [i.code for i in issues] == ["narration_absent"]


# --------------------------------------------------------------------------- #
# 3. (b) CONFIDENCE
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", [4.2, -0.1, 1.01])
def test_a_confidence_outside_0_1_is_flagged(value):
    n = _well_formed()
    n.specialist_views[0].confidence = value
    assert "confidence_out_of_range" in _codes(n, ranker_verdict="buy")


def test_a_NOT_ASSESSED_specialist_may_carry_no_confidence_at_all():
    """A dark channel has no measurement — "abstain, 0.00" reads as a measured neutral,
    which is precisely the misreading SENT-ISOLATE-1 exists to prevent."""
    n = _well_formed()
    assert n.specialist_views[1].assessed is False
    assert validate_narration(n, ranker_verdict="buy") == []      # None is correct

    n.specialist_views[1].confidence = 0.0
    assert "not_assessed_carries_confidence" in _codes(n, ranker_verdict="buy")


def test_a_missing_confidence_on_an_ASSESSED_specialist_is_not_a_range_error():
    n = _well_formed()
    n.specialist_views[0].confidence = None
    assert "confidence_out_of_range" not in _codes(n, ranker_verdict="buy")


# --------------------------------------------------------------------------- #
# 4. (c) NUMERIC CLAIMS PARSE
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,code", [
    ("The margin reached 12.3.4% last year.", "unparseable_figure"),
    ("It placed 3/ in the cohort.", "unparseable_rank"),
    ("It ranked 14/12 on ROIC.", "impossible_rank"),
])
def test_an_unparseable_figure_is_flagged(text, code):
    n = _well_formed()
    n.neutral_context = [text]
    assert code in _codes(n, ranker_verdict="buy")


def test_the_figures_a_narration_legitimately_states_all_parse():
    """Percentages, currency amounts (abbreviated and not), ranks and percentiles — the
    recognised shapes must NOT trip the guard, or it becomes noise and gets ignored."""
    n = _well_formed()
    n.neutral_context = [
        "Momentum returned -21.8% over 12 months and 12.0% over 6.",
        "Free cash flow was $69.7bn, against KRW 24.8tn of market value.",
        "It ranked 2/18 on ROIC and 9/18 on earnings yield.",
        "The band placed it at the 73rd percentile of its own history.",
        "The last close was 1,671,000.00 KRW.",
    ]
    assert validate_narration(n, ranker_verdict="buy") == []


# --------------------------------------------------------------------------- #
# 5. (d) THE VERDICT WORD — and ONLY the word
# --------------------------------------------------------------------------- #
def test_a_verdict_word_contradicting_the_ranker_is_flagged():
    n = _well_formed()
    assert "verdict_mismatch" in _codes(n, ranker_verdict="sell", ticker="ASML")


def test_the_verdict_check_is_SKIPPED_when_there_is_no_verdict_of_record():
    """Omit, never invent: a run with nothing to compare against is not a mismatch."""
    assert validate_narration(_well_formed()) == []
    assert validate_narration(_well_formed(), ranker_verdict="") == []


def test_this_validator_never_re_checks_a_RANK_claim():
    """The boundary with narration_check. A narration whose ORDINALS are wrong is the
    fact-checker's business; this guard must stay silent on it, or the two annotate the
    same sentence twice and each undermines the other's signal."""
    n = _well_formed()
    # a rank claim that is FALSE but structurally well-formed
    n.lens_attribution[0].reasoning = "It ranked 5 of 12 on ROIC, the worst in cohort."
    assert validate_narration(n, ranker_verdict="buy") == []


# --------------------------------------------------------------------------- #
# 6. THE BANNER — visible in BOTH surfaces, and the narration still ships
# --------------------------------------------------------------------------- #
def _broken() -> Narration:
    n = _well_formed()
    n.lens_verdicts = []
    n.specialist_views[0].confidence = 4.2
    return n


def test_the_banner_renders_in_the_markdown_report():
    n = _broken()
    issues = validate_narration(n, ranker_verdict="buy")
    md = narration_markdown(n, issues=issues)
    assert "Structural warning" in md
    assert "carries no lens verdicts" in md
    assert "outside 0–1" in md
    # ...and the narration SHIPS, unchanged and in full
    assert "Why each lens ranked it there" in md
    assert n.echoed_verdict in md


def test_the_banner_renders_in_the_html_report():
    n = _broken()
    issues = validate_narration(n, ranker_verdict="buy")
    doc = narration_html(n, issues=issues)
    assert 'class="callout structural"' in doc
    assert "Structural warning" in doc
    assert "carries no lens verdicts" in doc
    assert "Why each lens ranked it there" in doc          # the narration still ships


def test_a_clean_narration_renders_no_banner_in_either_surface():
    n = _well_formed()
    issues = validate_narration(n, ranker_verdict="buy")
    assert "Structural warning" not in narration_markdown(n, issues=issues)
    assert "structural" not in narration_html(n, issues=issues)


def test_the_banner_comes_BEFORE_the_narration_it_describes():
    n = _broken()
    md = narration_markdown(n, issues=validate_narration(n, ranker_verdict="buy"))
    assert md.index("Structural warning") < md.index("Ranker verdict")


# --------------------------------------------------------------------------- #
# 7. THE MACHINE-READABLE RECORD
# --------------------------------------------------------------------------- #
def test_the_failure_reasons_are_recorded_machine_readably():
    issues = validate_narration(_broken(), ranker_verdict="buy")
    records = issues_as_records(issues)
    assert records and all(set(r) == {"code", "detail"} for r in records)
    assert any(r["code"].startswith("missing_section:") for r in records)
    assert any(r["code"] == "confidence_out_of_range" for r in records)
    # codes are stable keys, not prose — a later pass greps these, not the banner text
    assert all(" " not in r["code"] for r in records)


def test_the_validator_is_pure_and_makes_no_llm_call(monkeypatch):
    """Zero LLM calls, by construction — it is parsing, and nothing else."""
    import aristos_council.agents.runners as runners_mod

    def _boom(*a, **kw):                                   # pragma: no cover
        raise AssertionError("the structural validator invoked a model")

    monkeypatch.setattr(runners_mod, "production_runners", _boom)
    assert validate_narration(_well_formed(), ranker_verdict="buy") == []
    # ...and deterministic: same input, same issues, every time
    n = _broken()
    assert _codes(n, ranker_verdict="buy") == _codes(n, ranker_verdict="buy")
