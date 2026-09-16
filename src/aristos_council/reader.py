"""READER-1 — the stage that writes the run's plain-English summary.

Runs LAST, after the shortlist and the band exist, on the deterministic artefacts only.
One model call per RUN (not per name), on the cheapest configured tier, at temperature 0.

"Math judges, LLM writes" applies exactly: every number the writer may use is already
decided, the writer only arranges them into sentences, and ``reader_check`` then verifies
that every number it wrote came from the facts. A summary that fails is WITHHELD with its
reason — the same honest-abstention rule the rest of the report follows.

No key, no runner, or a model that errors -> the section says so in one line. It never
raises, and it never stops a run: a free ranker-only run whose summary could not be written
is still a complete run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PROMPT_VERSION = "reader_v1"
_PROMPT_PATH = Path(__file__).resolve().parent / "agents" / "prompts" / "reader_v1.md"

NO_KEY_NOTE = "Summary not written: no API key"
NO_RUNNER_NOTE = "Summary not written: no reader model configured"


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
    reader is configured, which is a NOTE, not an error."""
    from .reader_check import check_summary
    from .reader_facts import build_facts_pack, facts_pack_json

    pack = build_facts_pack(multi_result, cohort_name=cohort_name,
                            cohort_thesis=cohort_thesis)
    base_meta = {"prompt_version": PROMPT_VERSION,
                 "model": getattr(runner, "model_id", None),
                 "temperature": getattr(runner, "temperature", None)}

    if runner is None:
        return ReaderResult(note=NO_RUNNER_NOTE, meta={**base_meta, "written": False})

    try:
        summary = runner.invoke(prompt_text(), facts_pack_json(pack))
    except Exception as exc:                      # a model/transport failure is a NOTE
        return ReaderResult(note=f"Summary not written: {type(exc).__name__}",
                            meta={**base_meta, "written": False, "error": str(exc)[:200]})

    check = check_summary(summary, pack)
    meta = {**base_meta, "written": True, "words": check.words,
            "check": "ok" if check.ok else check.reason}
    if not check.ok:
        # WITHHELD, not corrected: the reader is told it was tried and why it is absent.
        return ReaderResult(note=check.withheld_line, meta=meta)
    return ReaderResult(summary=summary, meta=meta)


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
