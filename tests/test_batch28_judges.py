"""Batch 28 A - JUDGE-PANEL-4: the claim counts, the footer, the story tag leak.

Every model call is a fake; nothing here reaches a provider."""
from __future__ import annotations

import pytest

from aristos_council.judges import answer, panel, render
from tests.test_judge_panel import _reply, _run_panel, _scripted, report  # noqa: F401  (report is a fixture)

pytestmark = pytest.mark.min_group(3)

MIXED = ("Free cash flow fell to $5.3bn. [pack] Interest cover is 9.0 times. [pack] "
         "Management has bought back stock for years. [own knowledge]")


def _three(reasons):
    caller = _scripted({f: [_reply("HOLD", reason=r, fact="Free cash flow was $5.3bn. [pack]")] * 2
                        for f, r in zip(("anthropic", "openai", "google"), reasons)})
    return _run_panel(report_obj, caller)


def test_count_claims_counts_each_tagged_sentence():
    assert answer.count_claims(MIXED) == (2, 1)
    assert answer.count_claims("A fact. [pack]", "Another. [own knowledge]") == (1, 1)
    assert answer.count_claims("") == (0, 0)


def test_each_judge_card_carries_its_claim_counts_and_the_line_reads_plainly(report):
    global report_obj
    report_obj = report
    result = _three([MIXED, "Cover is thin. [pack]", MIXED])
    cards = render.cards(result)
    assert [(c["claims_pack"], c["claims_own"]) for c in cards] == [(3, 1), (2, 0), (3, 1)]
    assert render.claims_line(cards[0]) == "3 claims from the pack, 1 from own knowledge"
    assert render.claims_line(cards[1]) == "2 claims from the pack, 0 from own knowledge"
    for lines in (render.text_lines(result), render.markdown_lines(result)):
        assert any("3 claims from the pack, 1 from own knowledge" in ln for ln in lines)


def test_the_footer_says_none_was_marked_only_when_no_judge_marked_any(report):
    global report_obj
    report_obj = report
    none = _three(["Cover is thin. [pack]"] * 3)
    assert render.own_tag_note(none) == "No passage was marked own knowledge in this run."
    assert render.text_lines(none)[-1] == "  (No passage was marked own knowledge in this run.)"
    some = _three([MIXED, "Cover is thin. [pack]", "Cover is thin. [pack]"])
    assert render.own_tag_note(some) == render.OWN_TAG_NOTE
    assert "No passage was marked" not in "\n".join(render.markdown_lines(some))


def test_a_panel_where_nobody_answered_keeps_the_standing_note(report):
    global report_obj
    report_obj = report
    caller = _scripted({f: [RuntimeError("x")] * 2 for f in ("anthropic", "openai", "google")})
    result = _run_panel(report, caller)
    assert render.own_tag_note(result) == render.OWN_TAG_NOTE


def test_the_story_sentence_carries_no_source_tags(report):
    global report_obj
    report_obj = report
    caller = _scripted({f: [_reply("HOLD", unexplained="The cause of the $21.3bn swing in other investing. [pack]")] * 2
                        for f in ("anthropic", "openai", "google")})
    line = render.unexplained_line(_run_panel(report, caller))
    assert line.startswith("The judge panel could not account for: ")
    assert "[pack]" not in line and "[own knowledge]" not in line
    assert line.endswith("$21.3bn swing in other investing.") or "swing in other investing." in line
    assert answer.strip_tags("It was $21.3bn. [own knowledge]") == "It was $21.3bn."
