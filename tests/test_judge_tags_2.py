"""JUDGE-TAGS-2 - three render bugs in the judge panel block, pinned on the live 2026-10-22 sweep lines.

(a) the source tag belongs on the labelled fields only (the fact that drove it, the reasoning); key
    risk, could-not-account-for and would-change-its-mind render plain. Live: OpenAI on KO rendered
    "- *Could not account for:* *nothing.* `own knowledge`", and on MSFT/JPM
    "- *Key risk:* *A sustained slowdown ...* `own knowledge`".
(b) a judge that answered on ONE call only ("incomplete") shows its answer plain - live JPM Gemini
    rendered "- *The fact that drove it:* HOLD: JPMorgan trades at ..." under a head line that
    already said "incomplete: HOLD on one call only".
(c) side by side (an unstable judge) each side is closed on its own before the " | " - live VKTX
    OpenAI rendered "*HOLD: ... * `own knowledge` *| BUY: ...* `own knowledge`".
"""
from __future__ import annotations

import json

import pytest

from aristos_council.judges import panel, render
from aristos_council.judges.answer import parse_answer
from tests.test_judge_panel import _config, _record

pytestmark = pytest.mark.min_group(3)


def _call(n, verdict, *, fact, reasoning, risk="A one-off could recur.", unexplained="nothing",
          change="A cut to the dividend.", confidence="medium"):
    """A JudgeCall built the way the panel builds one: from a reply that passes ``parse_answer``."""
    raw = json.dumps({"verdict": verdict, "confidence": confidence, "driving_fact": fact,
                      "reasoning": reasoning, "key_risk": risk, "unexplained": unexplained,
                      "would_change_my_mind": change})
    a, why = parse_answer(raw)
    assert a is not None, why
    return panel.JudgeCall(n, "ok", a.verdict, a.confidence, a.reasoning, a.driving_fact, a.key_risk,
                           a.unexplained, a.would_change_my_mind, a.own_knowledge_claims)


def _result(*judges):
    return panel.PanelResult(ticker="CO", available=True, record=_record("HOLD"), judges=list(judges),
                             agreement=panel.NO_READING)


def _judge_lines(result):
    """The Markdown lines of the judge cards (the block between the caption and the summary)."""
    lines = render.markdown_lines(result)
    return [ln for ln in lines if ln.startswith("- *") or ln.startswith("**")]


def _balanced(line):
    return line.count("`") % 2 == 0 and line.replace("**", "").count("*") % 2 == 0


OPENAI = _config().judges[1]
GEMINI = _config().judges[2]


# --------------------------------------------------------------------------- (a)
def test_ko_openai_nothing_and_its_own_knowledge_tag_render_plain():
    """KO.US.md line 60 (live): "- *Could not account for:* *nothing.* `own knowledge`"."""
    c = _call(1, "HOLD",
              fact="The company trades at a forward P/E of about 28x. [pack]",
              reasoning="Net debt is $27.9bn. [pack] High historical valuation limits upside at current price. "
                        "[own knowledge]",
              risk="The view could be wrong if Coca-Cola converts recent distribution wins and marketing "
                   "into sustained higher organic sales growth. [own knowledge]",
              unexplained="nothing. [own knowledge]",
              change="A sustained increase in free cash flow to at least $8bn annually would change my "
                     "verdict to BUY. [own knowledge]")
    j = panel.settle(OPENAI, [c, c])
    lines = _judge_lines(_result(j))
    assert "- *Could not account for:* nothing." in lines
    assert ("- *Key risk:* The view could be wrong if Coca-Cola converts recent distribution wins and "
            "marketing into sustained higher organic sales growth.") in lines
    assert ("- *Would change its mind if:* A sustained increase in free cash flow to at least $8bn "
            "annually would change my verdict to BUY.") in lines
    # the tag stays where provenance matters
    assert ("- *Why:* Net debt is $27.9bn. *High historical valuation limits upside at current price.* "
            "`own knowledge`") in lines
    assert all(_balanced(ln) for ln in lines)
    html = render.html_section(_result(j))
    assert "<li><em>Could not account for:</em> nothing.</li>" in html
    assert html.count("own-knowledge") == 1                      # the reasoning claim only


def test_msft_key_risk_renders_plain():
    """Live MSFT/JPM: "- *Key risk:* *A sustained slowdown ...* `own knowledge`" (the rest of the
    sentence is filled in here)."""
    c = _call(1, "HOLD", fact="Forward P/E is 30x. [pack]", reasoning="Azure grew. [pack]",
              risk="A sustained slowdown in cloud spending would undercut the growth the price assumes. "
                   "[own knowledge]")
    lines = _judge_lines(_result(panel.settle(OPENAI, [c, c])))
    assert ("- *Key risk:* A sustained slowdown in cloud spending would undercut the growth the price "
            "assumes.") in lines
    assert not any("`own knowledge`" in ln for ln in lines if ln.startswith("- *Key risk:*"))
    text = "\n".join(render.text_lines(_result(panel.settle(OPENAI, [c, c]))))
    assert "key risk: A sustained slowdown in cloud spending would undercut the growth the price assumes." in text


# --------------------------------------------------------------------------- (b)
def test_jpm_gemini_incomplete_renders_its_one_answer_without_a_verdict_prefix():
    ok = _call(1, "HOLD", fact="JPMorgan trades at 2.4x book value. [pack]",
               reasoning="Total revenue rose to $182.4bn in FY2025. [pack]",
               risk="Credit losses could rise faster than reserves.", unexplained="nothing",
               change="A fall in return on equity below 15%.")
    err = panel.JudgeCall(2, "error", error="503", error_detail="HTTP 503: high demand")
    j = panel.settle(GEMINI, [ok, err])
    assert j.status == panel.INCOMPLETE
    assert _judge_lines(_result(j)) == [
        "**Gemini (gemini-test): incomplete: HOLD on one call only (confidence medium)**",
        "- *2 claims from the pack, 0 from own knowledge*",
        "- *The fact that drove it:* JPMorgan trades at 2.4x book value.",
        "- *Why:* Total revenue rose to $182.4bn in FY2025.",
        "- *Key risk:* Credit losses could rise faster than reserves.",
        "- *Could not account for:* nothing",
        "- *Would change its mind if:* A fall in return on equity below 15%.",
    ]


# --------------------------------------------------------------------------- (c)
def _vktx_unstable():
    hold = _call(1, "HOLD", fact="Viking closed offerings raising about $575m. [pack]",
                 reasoning="Cash was $705.7m at FY2025 year-end. [pack] Phase 3 outcomes are binary. "
                           "[own knowledge]",
                 risk="A failed or unsafe Phase 3 readout for VK2735 would likely destroy the valuation. "
                      "[own knowledge]",
                 unexplained="The pack does not explain the R&D jump to $345.0m.")
    buy = _call(2, "BUY", fact="Viking initiated the SUNRISE Phase 3 program. [own knowledge]",
                reasoning="Phase 3 has started. [own knowledge] Cash less debt was $497.6m. [pack]",
                risk="A negative Phase 3 VK2735 result would remove the rationale. [own knowledge]",
                unexplained="The planned use of the $575m is not detailed.")
    return panel.settle(OPENAI, [hold, buy])


def test_vktx_unstable_sides_are_each_closed_before_the_pipe():
    j = _vktx_unstable()
    assert j.status == panel.UNSTABLE
    assert _judge_lines(_result(j)) == [
        "**OpenAI (gpt-test): unstable: HOLD / BUY (confidence medium / medium)**",
        "- *2 claims from the pack, 1 from own knowledge*",
        "- *The fact that drove it:* HOLD: Viking closed offerings raising about $575m. | "
        "BUY: *Viking initiated the SUNRISE Phase 3 program.* `own knowledge`",
        "- *Why:* HOLD: Cash was $705.7m at FY2025 year-end. *Phase 3 outcomes are binary.* "
        "`own knowledge` | BUY: *Phase 3 has started.* `own knowledge` Cash less debt was $497.6m.",
        "- *Key risk:* HOLD: A failed or unsafe Phase 3 readout for VK2735 would likely destroy the "
        "valuation. | BUY: A negative Phase 3 VK2735 result would remove the rationale.",
        "- *Could not account for:* HOLD: The pack does not explain the R&D jump to $345.0m. | "
        "BUY: The planned use of the $575m is not detailed.",
        "- *Would change its mind if:* HOLD: A cut to the dividend. | BUY: A cut to the dividend.",
    ]


def test_every_judge_line_has_balanced_italics_and_backticks():
    c = _call(1, "SELL", fact="Debt rose. [own knowledge]", reasoning="Margins fell. [pack] Peers cut. "
              "[own knowledge]", risk="Rates fall. [own knowledge]", unexplained="nothing. [own knowledge]")
    res = _result(_vktx_unstable(), panel.settle(GEMINI, [c, c]))
    for ln in render.markdown_lines(res):
        assert _balanced(ln), ln
    html = render.html_section(res)
    assert html.count("<em>") == html.count("</em>")
    assert "own knowledge</span> | BUY:" in html                 # each side closed before the pipe
