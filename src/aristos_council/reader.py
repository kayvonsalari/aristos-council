"""READER-1 — the stage that writes the run's plain-English summary.

Runs LAST, after the shortlist and the band exist, on the deterministic artefacts only.
One model call per RUN (not per name), on the cheapest configured tier, at temperature 0.

"Math judges, LLM writes" applies exactly: every number the writer may use is already
decided, the writer only arranges them into sentences, and ``reader_check`` then verifies
that every number it wrote came from the facts. A summary that fails is WITHHELD with its
reason — the same honest-abstention rule the rest of the report follows.

READER-5 narrowed what can stop it being published to four things, all of which mean the
prose contradicts the run: a number the facts do not hold, a company the run does not
carry, a test described with the wrong role, or a name every test rated BUY left unnamed.
Style faults are recorded and published anyway — see ``reader_check``.

No key, no runner, or a model that errors -> the section says so in one line. It never
raises, and it never stops a run: a free ranker-only run whose summary could not be written
is still a complete run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# READER-2/3b/4: every earlier version is kept on disk, so a run recorded under one stays
# reproducible. v3 differed from v2 by one substring — "balance sheet" left the must-gloss
# list, because READER-3 took it off the CHECK. v4 is the larger change: every test's ROLE
# is stated in the pack and the prompt may describe a test only by that role and its own
# `asks`; the price check is described in plain words rather than glossed in brackets; and
# a name every test rated BUY must be named. All three come from the same live summary
# (2026-09-17 09:10), which called a second picker "a check", said Forensic "looks for
# growth", and wrote a bracket inside a bracket.
#
# v5 separates what is CHECKED from what is ASKED. Four things are checked, all of them
# the summary contradicting the run; everything else — length, advice words, glosses,
# ranges — is craft the prompt asks for and the run records, and none of it withholds.
# The evidence: of the first five live summaries, four were destroyed by a missing
# bracket and the fifth, which published, was the only one that misdescribed the run.
#
# v6 follows SHORTLIST-3: there is no primary test, so a test is described by its own
# `asks` and by whether it VOTES, and the summary describes the AGREEMENT table rather
# than a shortlist a rule produced. The fourth withholding check moves with it — from
# "name every unanimous BUY" to "name every company at the top of the agreement", which is
# the same fact counted the way the table now counts, and which exists on a run where
# nothing was unanimous.
PROMPT_VERSION = "reader_v6"
_PROMPT_PATH = (Path(__file__).resolve().parent / "agents" / "prompts"
                / f"{PROMPT_VERSION}.md")

NO_KEY_NOTE = "Summary not written: no API key"
NO_RUNNER_NOTE = "Summary not written: no reader model configured"

# READER-3 — how many times the reader may be asked. ONE retry, never more.
#
# A failed check is usually one fixable slip: a number that is not in the pack, a stray
# ticker, a few words over the limit. Withholding the whole summary over that throws away
# a call already paid for and leaves a reader nothing but "Summary withheld: 312 words".
# Asking again, with the check's own complaint in front of the writer, recovers most of
# them.
#
# Exactly one retry, because the reason to stop is the same as the reason to try: a second
# attempt that fails the SAME deterministic check with the problems written out cannot be
# expected to find on a third pass what it could not find on the second, and every attempt
# costs money and delays a run that is otherwise finished. The summary is then withheld
# exactly as before — with BOTH checks recorded, so the pattern is visible in the meta
# rather than only the last one.
MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class ReaderResult:
    """What the stage produced, and enough about how to audit it later."""

    summary: Optional[object] = None      # a ReaderSummary when written and valid
    note: str = ""                        # the one line to render when there is no summary
    meta: dict = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.summary is not None


def prompt_text() -> str:
    """The versioned prompt, read from its file. A file rather than a string constant so a
    reword is a reviewable diff on its own, and so the version can be stamped on the run."""
    return _PROMPT_PATH.read_text(encoding="utf-8")


def write_summary(multi_result, *, runner=None, cohort_name: str = "",
                  cohort_thesis: str = "") -> ReaderResult:
    """Build the facts pack, ask the reader for five fields, check them, and return.

    ``runner`` is the seam: tests inject a fake and never touch a network. None means no
    reader is configured, which is a NOTE, not an error.

    READER-3: up to ``MAX_ATTEMPTS`` tries. A failed check is fed back to the writer as a
    correction and the summary is rewritten ONCE; a second failure withholds, as before.
    The facts pack is built once and never changes between attempts — the retry is allowed
    to reword, never to be given more to say.

    READER-5: only the FOUR withholding checks can fail, so only they can trigger that
    retry. A summary that is merely long, or that used a term without a gloss, publishes
    on the first call with the fault recorded in ``meta["notes"]``."""
    from .reader_check import check_summary
    from .reader_facts import build_facts_pack, facts_pack_json

    pack = build_facts_pack(multi_result, cohort_name=cohort_name,
                            cohort_thesis=cohort_thesis)
    base_meta = {"prompt_version": PROMPT_VERSION,
                 "model": getattr(runner, "model_id", None),
                 "temperature": getattr(runner, "temperature", None)}

    if runner is None:
        return ReaderResult(note=NO_RUNNER_NOTE, meta={**base_meta, "written": False})

    facts = facts_pack_json(pack)
    checks: list[str] = []
    summary = None
    check = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            summary = runner.invoke(prompt_text(), _user_message(facts, checks))
        except Exception as exc:                  # a model/transport failure is a NOTE
            return ReaderResult(
                note=f"Summary not written: {type(exc).__name__}",
                meta={**base_meta, "written": False, "attempts": attempt,
                      "checks": list(checks), "error": str(exc)[:200]})
        check = check_summary(summary, pack)
        checks.append("ok" if check.ok else check.reason)
        # READER-5: ``ok`` now follows the four WITHHOLDING checks alone, so the retry
        # fires only when the summary says something the run does not. A note about
        # length or a missing gloss no longer buys a second call — it never bought a
        # better summary either, and it billed for the attempt.
        if check.ok:
            break

    # READER-3: EVERY attempt's check is recorded, not only the last. A summary that
    # passed on the retry is not the same event as one that passed first time, and what
    # the first attempt keeps getting wrong is the evidence that would improve the prompt.
    # The COST of the attempts is already combined: the pipeline totals the reader's own
    # meter, and the retry goes through the same runner and therefore the same meter.
    meta = {**base_meta, "written": True, "words": check.words,
            "attempts": len(checks), "checks": list(checks),
            "check": "ok" if check.ok else check.reason,
            # READER-5 — the advisory faults, recorded on the run and shown nowhere in
            # the report. This is the evidence for whether the prompt's style rules are
            # working, which is the only thing they were ever able to tell us: before
            # this, a style fault either destroyed the summary or vanished.
            "notes": list(check.notes)}
    if not check.ok:
        # WITHHELD, not corrected: the reader is told it was tried and why it is absent.
        # READER-5: the rejected TEXT is recorded too. The evidence that justified this
        # whole change — what four withheld summaries actually said — was unrecoverable,
        # because withholding kept the reason and discarded the prose. Never again.
        return ReaderResult(note=check.withheld_line,
                            meta={**meta, "withheld_text": _as_fields(summary)})
    return ReaderResult(summary=summary, meta=meta)


def _as_fields(summary) -> dict:
    """The five fields as a plain dict, for the record. Empty when there is nothing."""
    from .reader_check import _FIELDS

    if summary is None:
        return {}
    return {f: (getattr(summary, f, None) if not isinstance(summary, dict)
                else summary.get(f)) or "" for f in _FIELDS}


def _user_message(facts: str, checks: list) -> str:
    """The facts pack, plus — on a retry — what the check said about the last attempt.

    A retry at temperature 0 against a byte-identical message would return a byte-identical
    summary and fail the same check, so the complaint has to travel. It is stated as a
    correction of the writer's own last try, never as new facts: the pack is unchanged, and
    the number check that rejected the attempt runs again against that same pack."""
    if not checks:
        return facts
    return "\n\n".join([
        facts,
        f"Your previous attempt was REJECTED by the automatic check:\n  {checks[-1]}",
        "Write the five fields again, fixing exactly that and changing nothing else. "
        "Every number you use must appear in the facts above — if a figure is not there, "
        "do not state it; say less instead. Never add a number to justify one that was "
        "rejected.",
    ])


def reader_paragraphs(summary) -> list[tuple[str, str]]:
    """``[(lead, text)]`` — the five fields with their bold leads, in reading order.

    ONE builder, so the HTML and the markdown render the same five paragraphs in the same
    order and cannot drift."""
    return [
        ("What this run asked.", summary.asked),
        ("What happened.", summary.happened),
        ("What survived.", summary.survived),
        ("What to doubt.", summary.doubt),
        ("What this cannot tell you.", summary.cannot_say),
    ]


READER_SECTION_TITLE = "Summary"
READER_SECTION_NOTE = (
    "Written by a language model from the tables below; every number checked against them. "
    "It explains the results; it does not recommend.")
