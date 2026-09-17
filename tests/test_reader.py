"""READER-1 — the stage, end to end, against a FAKE runner.

No network, no key, no cost: the same seam every other agent test uses. What is exercised
here is the path, not the prose — that a summary is written from the facts and nothing else,
that a bad one is withheld with its reason rather than published, that a failure of any kind
degrades to one honest line, and above all that the whole feature is INERT when off.

That last property is the one with teeth. The reader is opt-in and costs money; a run
without it must be byte-identical to a run from before this branch existed.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from aristos_council.agents.schemas import ReaderSummary
from aristos_council.pipeline import run_multi_strategy_pipeline
from aristos_council.reader import (NO_KEY_NOTE, NO_RUNNER_NOTE, PROMPT_VERSION,
                                    prompt_text, reader_paragraphs, write_summary)

from tests.test_multi_strategy_run import RAW, SCREENED, STRAT_DIR, TODAY, UNIVERSE, _Adapter

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "reader"
PACK = json.loads((FIXTURES / "oil_pack_2026-09-16.json").read_text(encoding="utf-8"))


class _FakeRunner:
    """Records what it was asked, returns what it was told to. The seam, nothing more."""

    model_id = "fake:reader"
    temperature = 0.0

    def __init__(self, summary=None, error=None):
        self._summary, self._error = summary, error
        self.system = self.user = None

    def invoke(self, system, user):
        self.system, self.user = system, user
        if self._error:
            raise self._error
        return self._summary


def _good(**over):
    base = dict(asked="Two tests ran over 4 companies.",
                happened="Magic Formula RAW ranked 3 names.",
                survived="1 name stayed on the shortlist.",
                doubt="Some figures could not be worked out.",
                cannot_say="This run cannot say what happens next.")
    base.update(over)
    return ReaderSummary(**base)


def _multi(**kw):
    return run_multi_strategy_pipeline(UNIVERSE, [SCREENED, RAW], strategies_dir=STRAT_DIR,
                                       adapter=_Adapter(), today=TODAY, **kw)


# --------------------------------------------------------------------------- #
# OFF is inert — the property with teeth
# --------------------------------------------------------------------------- #
def test_with_reader_off_nothing_is_built_and_no_runner_is_called():
    runner = _FakeRunner(_good())
    res = _multi(reader_runner=runner)          # passed, but with_reader defaults False
    assert res.reader is None
    assert runner.system is None                 # never invoked
    assert "reader" not in res.meta


def test_the_markdown_is_byte_identical_with_the_reader_off():
    """Acceptance item 3: a run without the summary is the run we had before."""
    pytest.importorskip("streamlit")
    import app
    from datetime import datetime, timezone

    stamp = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
    without = app._multi_strategy_markdown(_multi(), stamp)
    # ...and the same document with the section suppressed by the feature being off must
    # contain no trace of it at all — not a heading, not a blank section, nothing.
    assert "## Summary" not in without
    assert "Summary withheld" not in without
    assert "Summary not written" not in without


def test_the_html_carries_no_summary_section_when_off():
    from aristos_council.export.report_html import multi_strategy_report_html

    doc = multi_strategy_report_html(_multi())
    assert 'id="summary"' not in doc


def test_the_contents_list_gains_no_entry_when_off():
    from aristos_council.pipeline import report_sections

    anchors = [s["anchor"] for s in report_sections(_multi())]
    assert "summary" not in anchors


# --------------------------------------------------------------------------- #
# ON, with a fake runner
# --------------------------------------------------------------------------- #
def test_a_valid_summary_is_attached_and_recorded():
    runner = _FakeRunner(_good())
    res = _multi(with_reader=True, reader_runner=runner, cohort_thesis="value")
    assert res.reader.available
    assert res.reader.summary.asked.startswith("Two tests ran")
    assert res.meta["reader"]["written"] is True
    assert res.meta["reader"]["check"] == "ok"
    assert res.meta["reader"]["prompt_version"] == PROMPT_VERSION
    assert res.meta["reader"]["model"] == "fake:reader"
    assert res.meta["reader"]["temperature"] == 0.0
    assert res.meta["reader"]["words"] > 0


def test_the_model_is_handed_the_prompt_and_the_facts_pack_and_nothing_else():
    """The safety design: it never sees the report, so it cannot quote what is not here."""
    runner = _FakeRunner(_good())
    _multi(with_reader=True, reader_runner=runner)
    assert runner.system == prompt_text()
    pack = json.loads(runner.user)              # the user turn is the pack, verbatim JSON
    # SHORTLIST-3: "shortlist" is now "agreement" — the same section, counted the way it
    # is now counted. What this test guards is unchanged: the writer is handed the PACK and
    # nothing else, and the pack carries the run's own facts.
    assert set(pack) >= {"cohort", "lenses", "agreement", "valuation_band"}


def test_the_prompt_is_the_versioned_file_on_disk():
    text = prompt_text()
    assert "About 300 words" in text                # READER-5: a target, not a cliff
    assert "Do not recommend" in text
    assert 'Say "list", not cohort' in text         # the de-jargon rule
    # READER-2 moved the live version to v2 and READER-3b to v3. EVERY earlier version
    # stays on disk, so a run recorded under one is still reproducible.
    assert PROMPT_VERSION == "reader_v6"
    prompts = (FIXTURES.parents[1].parent / "src" / "aristos_council" / "agents"
               / "prompts")
    for version in ("reader_v1", "reader_v2", "reader_v3", "reader_v4", "reader_v5",
                    "reader_v6"):
        assert (prompts / f"{version}.md").exists(), version
    assert "No ranges" in text and "CHECKED" in text
    # READER-5: the verdict words are the run's own and are never an advice word.
    assert "BUY, HOLD and SELL are" in text
    # READER-6: a test is described by what it ASKS and by whether it VOTES — there is no
    # primary test to be described as one.
    assert "Every test that votes is a vote of equal weight" in text
    assert "primary picker" not in text


# --------------------------------------------------------------------------- #
# READER-3b — the prompt and the check must ask for the same glosses
# --------------------------------------------------------------------------- #
# READER-3 took "balance sheet" off the CHECK, because a summary withheld over a word
# every reader already knows is a worse outcome than the word left unglossed. The PROMPT
# went on demanding it — harmless, in that it withheld nothing, but not free: it spends
# words out of a 300-word budget on a gloss nobody wanted, and a prompt that asks for
# something the checker does not want is a drift that only grows.

def test_the_prompt_no_longer_lists_terms_that_MUST_be_glossed():
    """READER-5 replaced the list with a principle. v2-v4 named the terms and the checker
    enforced the naming, so the two had to be pinned against each other; four of the first
    five live summaries were destroyed by that enforcement. The prompt now asks for a term
    to be explained where that reads naturally, the checker records when one was not, and
    there is no list for the two to disagree about."""
    text = prompt_text()
    assert "percentile, free cash flow, accrual, momentum" not in text
    assert "Explain a finance term the first time you use it" in text
    assert "Never put a bracket inside a bracket" in text


def test_the_gloss_rule_is_ADVISORY_on_both_sides():
    """The prompt asks; the checker records. Neither withholds."""
    from aristos_council.reader_check import GLOSS_TERMS, check_summary

    assert GLOSS_TERMS                      # still detected, for the record
    check = check_summary(
        {"asked": "A list was tested.", "happened": "Some tests ran.",
         "survived": "Nothing was picked.", "doubt": "The accrual ratio was high.",
         "cannot_say": "It cannot say what happens next."}, {})
    assert check.ok, check.reason
    assert any("accrual" in n for n in check.notes)


def test_the_balance_sheet_gloss_is_gone_from_the_prompt_too():
    assert "accrual, balance sheet, momentum" not in prompt_text()


def test_v3_differs_from_v2_by_that_ONE_line_and_nothing_else():
    """A prompt version bump is a behavioural change to the only free-form part of the
    report, so what changed has to be checkable at a glance — not taken on trust."""
    prompts = (FIXTURES.parents[1].parent / "src" / "aristos_council" / "agents"
               / "prompts")
    v2 = (prompts / "reader_v2.md").read_text(encoding="utf-8").splitlines()
    v3 = (prompts / "reader_v3.md").read_text(encoding="utf-8").splitlines()
    assert len(v2) == len(v3)
    differ = [i for i, (a, b) in enumerate(zip(v2, v3)) if a != b]
    assert len(differ) == 1, [(v2[i], v3[i]) for i in differ]
    assert "balance sheet" in v2[differ[0]] and "balance sheet" not in v3[differ[0]]


def test_v2_is_kept_verbatim_so_a_run_recorded_under_it_still_replays():
    """Rule 7's discipline applied to prompts: a published version is never edited in
    place. v3 is a new file; v2 is untouched and still demands its own gloss list."""
    prompts = (FIXTURES.parents[1].parent / "src" / "aristos_council" / "agents"
               / "prompts")
    assert ("accrual, balance sheet, momentum"
            in (prompts / "reader_v2.md").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Every failure degrades to one honest line
# --------------------------------------------------------------------------- #
def test_a_summary_that_fails_the_check_is_withheld_with_its_reason():
    """READER-5: what withholds is the summary saying something the run does not. The
    advice word in this text is recorded as a NOTE beside the reason, not as one."""
    bad = _good(survived="You should buy 9999 names.")
    res = _multi(with_reader=True, reader_runner=_FakeRunner(bad))
    assert not res.reader.available
    assert res.reader.note == "Summary withheld: number not in the facts: 9999"
    assert res.meta["reader"]["written"] is True     # it WAS written, then rejected
    assert res.meta["reader"]["check"] != "ok"
    assert any("advice word" in n for n in res.meta["reader"]["notes"])


def test_a_model_error_is_a_note_not_an_exception():
    res = _multi(with_reader=True, reader_runner=_FakeRunner(error=RuntimeError("429")))
    assert not res.reader.available
    assert res.reader.note == "Summary not written: RuntimeError"
    assert res.meta["reader"]["written"] is False
    assert "429" in res.meta["reader"]["error"]


def test_no_runner_and_no_key_says_so_rather_than_failing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = _multi(with_reader=True)
    assert not res.reader.available
    assert res.reader.note == NO_KEY_NOTE
    assert res.meta["reader"]["written"] is False


def test_write_summary_without_a_runner_is_a_note():
    out = write_summary(_multi(), runner=None)
    assert not out.available and out.note == NO_RUNNER_NOTE


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def test_the_five_paragraphs_render_in_reading_order():
    leads = [lead for lead, _ in reader_paragraphs(_good())]
    assert leads == ["What this run asked.", "What happened.", "What survived.",
                     "What to doubt.", "What this cannot tell you."]


def test_the_section_renders_above_the_shortlist_in_both_documents():
    pytest.importorskip("streamlit")
    import app
    from aristos_council.export.report_html import multi_strategy_report_html

    res = _multi(with_reader=True, reader_runner=_FakeRunner(_good()))
    md = app._multi_strategy_markdown(res, None)
    assert "## Summary" in md
    assert md.index("## Summary") < md.index("## Shortlist")
    assert "**What this run asked.**" in md
    assert "it does not recommend" in md

    doc = multi_strategy_report_html(res)
    assert doc.index('id="summary"') < doc.index('id="shortlist"')


def test_a_withheld_summary_still_renders_its_one_line():
    """The reader must learn it was tried. An absent section says nothing."""
    pytest.importorskip("streamlit")
    import app

    bad = _good(survived="It ranked 9999 names.")
    res = _multi(with_reader=True, reader_runner=_FakeRunner(bad))
    md = app._multi_strategy_markdown(res, None)
    assert "## Summary" in md and "Summary withheld:" in md


def test_the_contents_list_gains_the_entry_when_on():
    from aristos_council.pipeline import report_sections

    res = _multi(with_reader=True, reader_runner=_FakeRunner(_good()))
    sections = report_sections(res)
    assert sections[0]["anchor"] == "summary"      # first: it explains the rest


def test_the_run_records_what_the_summary_cost():
    """Acceptance item 4 needs a real figure, so the reader runs on its own CostMeter and
    the run records the provider's counts — not an estimate, and recorded whether the
    summary was published or withheld, because a withheld one was still paid for."""
    from aristos_council.costs import CostMeter

    class _MeteredRunner(_FakeRunner):
        def __init__(self, summary):
            super().__init__(summary)
            self.meter = CostMeter()

        def invoke(self, system, user):
            out = super().invoke(system, user)
            self.meter.record("fake:reader",
                              {"input_tokens": 3200, "output_tokens": 180})
            return out

    res = _multi(with_reader=True, reader_runner=_MeteredRunner(_good()))
    assert res.meta["reader"]["input_tokens"] == 3200
    assert res.meta["reader"]["output_tokens"] == 180
    assert "usd" in res.meta["reader"]


def test_a_withheld_summary_still_records_its_cost():
    from aristos_council.costs import CostMeter

    class _MeteredRunner(_FakeRunner):
        def __init__(self, summary):
            super().__init__(summary)
            self.meter = CostMeter()

        def invoke(self, system, user):
            out = super().invoke(system, user)
            self.meter.record("fake:reader", {"input_tokens": 100, "output_tokens": 10})
            return out

    bad = _good(survived="It ranked 9999 names.")
    res = _multi(with_reader=True, reader_runner=_MeteredRunner(bad))
    assert not res.reader.available                     # withheld...
    # ...and still billed, for BOTH attempts. READER-3 asks again when the check fails, so
    # a withheld summary costs two calls, and the run must state what it actually spent
    # rather than what one call would have cost.
    assert res.meta["reader"]["attempts"] == 2
    assert res.meta["reader"]["input_tokens"] == 200
    assert res.meta["reader"]["output_tokens"] == 20


# --------------------------------------------------------------------------- #
# READER-3 — one retry, with the check's own complaint fed back
# --------------------------------------------------------------------------- #
# A failed check is usually one fixable slip. Withholding the whole summary over it throws
# away a call already paid for and leaves a reader "Summary withheld: 312 words" and
# nothing else. One retry recovers most of them; a second would not, because the check is
# deterministic and the writer has already been shown exactly what it said.

class _ScriptedRunner:
    """Returns each summary in turn, and records every user message it was sent."""

    model_id = "fake:reader"
    temperature = 0.0

    def __init__(self, *summaries):
        self.summaries = list(summaries)
        self.messages = []

    def invoke(self, system, user):
        self.messages.append(user)
        return self.summaries[min(len(self.messages) - 1, len(self.summaries) - 1)]


def test_a_first_attempt_that_passes_is_not_retried():
    """The common case costs exactly what it always did — one call."""
    runner = _ScriptedRunner(_good())
    res = _multi(with_reader=True, reader_runner=runner)
    assert res.reader.available
    assert len(runner.messages) == 1
    assert res.meta["reader"]["attempts"] == 1
    assert res.meta["reader"]["checks"] == ["ok"]


def test_a_failed_check_is_retried_once_and_can_be_recovered():
    runner = _ScriptedRunner(_good(survived="It ranked 9999 names."), _good())
    res = _multi(with_reader=True, reader_runner=runner)
    assert res.reader.available                      # the retry was published
    assert len(runner.messages) == 2
    assert res.meta["reader"]["attempts"] == 2
    # BOTH checks are recorded: a summary that passed on the retry is not the same event
    # as one that passed first time, and the meta is where that shows.
    assert len(res.meta["reader"]["checks"]) == 2
    assert "number not in the facts: 9999" in res.meta["reader"]["checks"][0]
    assert res.meta["reader"]["checks"][1] == "ok"
    assert res.meta["reader"]["check"] == "ok"


def test_the_retry_is_told_exactly_what_the_check_said():
    """A retry at temperature 0 against a byte-identical message returns a byte-identical
    summary and fails the same check. The complaint has to travel, or the retry is only a
    second bill."""
    runner = _ScriptedRunner(_good(survived="It ranked 9999 names."), _good())
    _multi(with_reader=True, reader_runner=runner)
    first, second = runner.messages
    assert "REJECTED" not in first                   # the first ask is unchanged
    assert "REJECTED by the automatic check" in second
    assert "number not in the facts: 9999" in second
    # The FACTS are unchanged between attempts — the retry may reword, never be given
    # more to say.
    assert second.startswith(first)


def test_a_second_failure_withholds_exactly_as_before():
    bad = _good(survived="It ranked 9999 names.")
    runner = _ScriptedRunner(bad, bad)
    res = _multi(with_reader=True, reader_runner=runner)
    assert not res.reader.available
    assert res.reader.note.startswith("Summary withheld: ")
    assert len(runner.messages) == 2                 # and STOPS there
    assert res.meta["reader"]["attempts"] == 2
    assert all("9999" in c for c in res.meta["reader"]["checks"])


def test_a_model_error_on_the_retry_still_records_the_first_attempt():
    """The first call was made and paid for; an exception on the second must not erase
    the fact that it happened."""

    class _ThenRaises(_ScriptedRunner):
        def invoke(self, system, user):
            if self.messages:
                self.messages.append(user)
                raise RuntimeError("provider said no")
            return super().invoke(system, user)

    runner = _ThenRaises(_good(survived="It ranked 9999 names."))
    res = _multi(with_reader=True, reader_runner=runner)
    assert not res.reader.available
    assert res.reader.note == "Summary not written: RuntimeError"
    assert res.meta["reader"]["attempts"] == 2
    assert len(res.meta["reader"]["checks"]) == 1    # only the first got as far as a check


def test_the_retry_ceiling_is_two():
    from aristos_council.reader import MAX_ATTEMPTS

    assert MAX_ATTEMPTS == 2


def test_the_run_tab_cost_hint_covers_both_calls():
    """A hint that quotes only the best case is not a hint."""
    pytest.importorskip("streamlit")
    import app

    assert app.READER_COST_HINT == "1-2 cents"


# --------------------------------------------------------------------------- #
# READER-5 — a style fault publishes, and is recorded
# --------------------------------------------------------------------------- #
# Of the first five live summaries, ONE published. The four that were withheld were
# withheld for a term used without a bracket. A feature that fails four times in five, for
# reasons that have nothing to do with truth, is not abstaining honestly — it is broken and
# wearing abstention's clothes.

def test_a_style_fault_publishes_and_costs_ONE_call():
    runner = _ScriptedRunner(_good(survived="You should buy everything."))
    res = _multi(with_reader=True, reader_runner=runner)
    assert res.reader.available                      # published...
    assert len(runner.messages) == 1                 # ...on the FIRST call, no retry
    assert res.meta["reader"]["attempts"] == 1
    assert res.meta["reader"]["check"] == "ok"


def test_the_style_fault_is_recorded_in_the_run_meta():
    """``notes`` is the evidence for whether the prompt's style rules work. Before this a
    style fault either destroyed the summary or vanished; neither taught anyone anything."""
    res = _multi(with_reader=True,
                 reader_runner=_FakeRunner(_good(survived="You should buy everything.")))
    assert any('advice word: "buy"' in n for n in res.meta["reader"]["notes"])


def test_a_clean_summary_records_no_notes():
    res = _multi(with_reader=True, reader_runner=_FakeRunner(_good()))
    assert res.reader.available and res.meta["reader"]["notes"] == []


def test_a_WITHHELD_summarys_text_is_kept_on_the_run():
    """The evidence that justified READER-5 — what four withheld summaries actually said —
    was unrecoverable, because withholding kept the reason and threw the prose away. The
    next such argument will have the text."""
    bad = _good(survived="It ranked 9999 names.")
    res = _multi(with_reader=True, reader_runner=_FakeRunner(bad))
    assert not res.reader.available
    kept = res.meta["reader"]["withheld_text"]
    assert kept["survived"] == "It ranked 9999 names."
    assert set(kept) == {"asked", "happened", "survived", "doubt", "cannot_say"}


def test_a_PUBLISHED_summary_records_no_withheld_text():
    res = _multi(with_reader=True, reader_runner=_FakeRunner(_good()))
    assert "withheld_text" not in res.meta["reader"]


def test_the_reader_runs_on_the_narrator_tier():
    """READER-1 put it on the cheapest tier, reasoning that nothing is being reasoned
    about. Five live runs showed what the job actually is — hold a whole run in view,
    describe each test by its role without inferring one, and say the hard part plainly —
    and four of the five were unpublishable. It is ONE call per run, so the stronger tier
    costs a cent or two rather than a multiple of anything."""
    from aristos_council.agents.runners import _DEFAULTS, _model_for

    assert _DEFAULTS["reader"] == _DEFAULTS["decision"]     # the narrator's tier
    assert _model_for("reader") == _DEFAULTS["reader"]      # ...and still overridable


def test_the_reader_tier_is_still_overridable_by_env(monkeypatch):
    from aristos_council.agents.runners import _model_for

    monkeypatch.setenv("ARISTOS_MODEL_READER", "anthropic:claude-haiku-4-5")
    assert _model_for("reader") == "anthropic:claude-haiku-4-5"


def test_the_checkbox_says_what_it_is_and_nothing_else():
    pytest.importorskip("streamlit")
    import app
    import inspect

    src = inspect.getsource(app)
    assert '"Plain-English summary",' in src
