"""NARR-PARSE-1 — a writer's malformed answer must not kill the run.

Live, 2026-09-18 (``universe_my-portfolio_4lenses_ranker_2026-09-18_1619``): the decision
tier returned the nested ``narration`` object as a JSON **string**. The content was well
formed; only the envelope was wrong. Structured output rejected it, the runner re-raised,
and ONE name took the whole narration stage down — the run produced no narration at all.

Two guards, and they are deliberately separate:

  * the runner repairs a stringified nested object ONCE and counts it, so a wrong envelope
    is not a lost answer;
  * the narration stage contains a failure to the NAME it happened to, so a genuinely bad
    answer costs one section rather than the run.

The schema is not touched, and neither are the prompts. A model that says the wrong thing
still fails — it just fails alone.
"""

from __future__ import annotations

import json

import pytest

from aristos_council.agents.runners import (LangChainRunner, repair_stringified,
                                            repaired_count, tool_call_args)
from aristos_council.agents.schemas import DecisionOutput
from aristos_council.pipeline import narration_failure_line, narration_failures
from aristos_council.state import Recommendation

from tests.test_multi_strategy_run import RAW, SCREENED
from tests.test_narration_union import _CountingRunners, _narrated


# =========================================================================== #
# 1. the repair itself
# =========================================================================== #
def test_a_nested_object_sent_as_a_json_string_is_parsed():
    args = {"recommendation": "buy", "narration": '{"echoed_verdict": "BUY"}'}
    fixed, n = repair_stringified(args)
    assert n == 1
    assert fixed["narration"] == {"echoed_verdict": "BUY"}
    assert fixed["recommendation"] == "buy"     # untouched


def test_prose_and_bare_numbers_in_strings_are_left_alone():
    """Only a string that BEGINS as JSON punctuation and parses to a dict or list is
    repaired. Anything looser would silently change a field's type."""
    args = {"rationale": "The company is cheap. {not json}", "confidence": "0.7",
            "note": "", "verdict": "buy"}
    fixed, n = repair_stringified(args)
    assert n == 0 and fixed == args


def test_a_stringified_object_nested_one_level_down_is_reached():
    args = {"narration": {"sections": '[{"heading": "Why"}]'}}
    fixed, n = repair_stringified(args)
    assert n == 1
    assert fixed["narration"]["sections"] == [{"heading": "Why"}]


def test_a_string_that_looks_like_json_but_is_not_is_left_alone():
    args = {"narration": '{"unclosed": '}
    fixed, n = repair_stringified(args)
    assert n == 0 and fixed == args


# =========================================================================== #
# 2. the runner: repair once, then raise
# =========================================================================== #
class _Message:
    """The raw AIMessage shape ``include_raw=True`` keeps beside a parse failure."""

    def __init__(self, args):
        self.tool_calls = [{"name": "DecisionOutput", "args": args}]
        self.usage_metadata = None


class _LLM:
    def __init__(self, out):
        self._out = out

    def invoke(self, messages):
        return self._out


def _runner(out) -> LangChainRunner:
    """A LangChainRunner without ``__init__`` — which would need langchain and a key.

    The seam under test is ``invoke``; constructing the real client would test
    init_chat_model, which is not ours.
    """
    runner = object.__new__(LangChainRunner)
    runner.tier = "decision"
    runner.schema = DecisionOutput
    runner.model_id = "test:model"
    runner.temperature = 0.0
    runner.meter = None
    runner.repaired = 0
    runner._llm = _LLM(out)
    return runner


def _good_args(**over):
    args = {"recommendation": "buy", "confidence": 0.7, "rationale": "Because."}
    args.update(over)
    return args


def test_the_runner_repairs_a_stringified_nested_object_and_counts_it():
    narration = {"echoed_verdict": "BUY", "sections": []}
    args = _good_args(narration=json.dumps(narration))
    runner = _runner({"parsed": None, "raw": _Message(args),
                      "parsing_error": ValueError("model_type")})

    out = runner.invoke("sys", "user")

    assert isinstance(out, DecisionOutput)
    assert out.recommendation == Recommendation.BUY
    assert runner.repaired == 1
    assert repaired_count({"decision": runner}) == 1


def test_a_genuinely_malformed_answer_still_raises_the_original_error():
    """The repair is not a way to keep bending an answer until it fits."""
    boom = ValueError("confidence must be a number")
    runner = _runner({"parsed": None, "raw": _Message({"recommendation": "buy"}),
                      "parsing_error": boom})
    with pytest.raises(ValueError, match="confidence must be a number"):
        runner.invoke("sys", "user")
    assert runner.repaired == 0


def test_an_answer_that_needs_no_repair_is_returned_untouched_and_counts_nothing():
    good = DecisionOutput(recommendation=Recommendation.BUY, confidence=0.7,
                          rationale="Because.")
    runner = _runner({"parsed": good, "raw": _Message(_good_args()),
                      "parsing_error": None})
    assert runner.invoke("sys", "user") is good
    assert runner.repaired == 0


def test_a_string_that_parses_but_still_fails_the_schema_raises_the_original():
    """Repaired shape, wrong content — the schema is still the judge."""
    original = ValueError("narration: Input should be a valid dictionary")
    args = _good_args(narration='["not", "an", "object"]')
    runner = _runner({"parsed": None, "raw": _Message(args), "parsing_error": original})
    with pytest.raises(ValueError, match="Input should be a valid dictionary"):
        runner.invoke("sys", "user")
    assert runner.repaired == 0


def test_tool_call_args_returns_none_when_there_is_nothing_to_look_at():
    class _Bare:
        tool_calls = []
    assert tool_call_args(_Bare()) is None
    assert tool_call_args(None) is None


# =========================================================================== #
# 3. the stage: one name's failure is one name's failure
# =========================================================================== #
class _FailingRunners(_CountingRunners):
    """The counting narrator, except that ONE narration blows up.

    Keyed on the CALL INDEX, not on the ticker appearing in the prompt. This fixture's
    tickers are single letters, so a substring test for "A" matches every prompt and the
    test would quietly be asserting that ALL names failed — which it did, before this
    note existed. The stage narrates the union in order, one decision call per name, so
    index 0 is union[0].
    """

    def __init__(self, fail_on: int = 0, exc: Exception | None = None):
        super().__init__()
        self.fail_on = fail_on
        self.exc = exc or ValueError("1 validation error for DecisionOutput\nnarration\n"
                                     "  Input should be a valid dictionary")
        outer = self
        inner = self["decision"]

        class _Decision:
            def invoke(self, system, user):
                if len(outer.decisions) == outer.fail_on:
                    outer.decisions.append(user)       # it WAS attempted; count it
                    raise outer.exc
                return inner.invoke(system, user)

        self["decision"] = _Decision()


# ``buys_only`` over this fixture narrates a single name, and "one name's failure leaves
# the others alone" needs others. ``all`` narrates every ranked name, which is the same
# stage over a wider union — the guard under test does not know the difference.
COVERAGE = "all"


def _run(runners=None):
    return _narrated([SCREENED, RAW], coverage=COVERAGE, runners=runners)


def _doomed_name(result) -> str:
    from aristos_council.pipeline import narrated_union
    union = narrated_union(result, COVERAGE)
    assert len(union) >= 2, "need at least two narrated names for this test to mean anything"
    return union[0]


def test_one_names_failure_leaves_every_other_name_narrated():
    baseline, _ = _run()
    doomed = _doomed_name(baseline)

    result, _runners = _run(runners=_FailingRunners())

    # the run COMPLETED and produced a report
    assert result.narratives, "the stage died instead of containing the failure"
    # ...every other name still narrated
    others = [t for t in baseline.narratives if t != doomed]
    assert others, "fixture gives us nothing to compare"
    for ticker in others:
        assert result.narratives[ticker] == baseline.narratives[ticker]


def test_the_failed_name_says_so_in_its_own_slot_and_is_never_silently_dropped():
    baseline, _ = _run()
    doomed = _doomed_name(baseline)

    result, _runners = _run(runners=_FailingRunners())

    assert doomed in result.narratives                      # the slot exists
    text = result.narratives[doomed]
    assert text.startswith("narration failed for this name: ValueError")
    assert "1 validation error for DecisionOutput" in text  # the FIRST line, and only it
    assert "Input should be a valid dictionary" not in text


def test_the_failure_is_recorded_machine_readably_on_the_run():
    baseline, _ = _run()
    doomed = _doomed_name(baseline)

    result, _runners = _run(runners=_FailingRunners())

    record = result.meta["narration"]
    assert record["failed_count"] == 1
    assert [f["ticker"] for f in record["failed"]] == [doomed]
    assert record["attempted"] == len(baseline.narratives)
    assert narration_failures(result) == record["failed"]


def test_the_report_carries_one_line_above_the_narrated_sections():
    baseline, _ = _run()
    doomed = _doomed_name(baseline)
    result, _runners = _run(runners=_FailingRunners())

    line = narration_failure_line(result)
    assert line == (f"Narration failed for 1 of {len(baseline.narratives)} names; "
                    f"their sections say so.")

    pytest.importorskip("streamlit")
    import app
    markdown = "\n".join(app._multi_narration_markdown(result))
    assert line in markdown
    # ...above the sections, not buried under them
    assert markdown.index(line) < markdown.index("### ")


def test_a_clean_run_carries_no_failure_line_and_repairs_nothing():
    """The good path is unchanged — the whole point of a guard nobody should notice."""
    result, runners = _run()

    record = result.meta["narration"]
    assert record["failed"] == [] and record["failed_count"] == 0
    assert record["repaired"] == 0
    assert narration_failure_line(result) == ""

    pytest.importorskip("streamlit")
    import app
    markdown = "\n".join(app._multi_narration_markdown(result))
    assert "Narration failed" not in markdown


def test_a_clean_run_narrates_byte_identically_to_one_with_the_guard_out_of_the_way():
    """Two identical runs, same bytes: the guard adds nothing to a good answer."""
    first, _ = _run()
    second, _ = _run()
    assert first.narratives == second.narratives
    assert list(first.narratives) == list(second.narratives)     # and the same ORDER


def test_a_failure_of_any_class_is_contained_not_just_a_validation_error():
    """Broad on purpose: a rate limit and a bad parse have the same blast radius."""
    baseline, _ = _run()
    doomed = _doomed_name(baseline)
    runners = _FailingRunners(exc=RuntimeError("429 rate limited"))

    result, _r = _run(runners=runners)

    assert result.narratives[doomed].startswith(
        "narration failed for this name: RuntimeError: 429 rate limited")
    assert result.meta["narration"]["failed_count"] == 1
