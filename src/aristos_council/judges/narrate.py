"""JUDGE-PANEL-1 - the narrator's one short paragraph about where the panel differs.

Written only when the panel disagrees with (or is mixed against) the verdict of record and at
least one judge is stable. It runs on the narrator's own model tier (the "decision" tier the
council's narration uses) and its output goes through a check in the spirit of the narration
check: the paragraph is a READING, so it is withheld - with its reason, never silently rewritten -
if it is longer than 80 words, gives a recommendation, asserts a verdict word nobody gave, or
combines the judges into a new verdict. One corrective retry, as the plain-English summary has.
"""
from __future__ import annotations

import re
from pathlib import Path
from pydantic import BaseModel

from .panel import PanelReading, STABLE, UNSTABLE, needs_reading

PROMPT_VERSION = "judge_reading_prompt_v1"
PROMPT_PATH = Path(__file__).resolve().parent / f"{PROMPT_VERSION}.md"
MAX_WORDS = 80
NO_KEY_NOTE = "The reading is unavailable: no ANTHROPIC_API_KEY set"


class PanelReadingOutput(BaseModel):
    paragraph: str


_RECOMMENDS = re.compile(
    r"\b(?:you\s+should|investors?\s+should|one\s+should|should\s+(?:buy|sell|hold|be\s+bought|be\s+sold)|"
    r"we\s+recommend|i\s+recommend|recommend(?:s|ed|ation)?|advis(?:e|es|ed)|worth\s+(?:buying|selling)|"
    r"consider\s+(?:buying|selling)|is\s+(?:the\s+)?right|is\s+(?:the\s+)?wrong)\b", re.I)
_COMBINES = re.compile(r"\b(?:combined|net|consensus|overall|average|averaged|blended)\s+"
                       r"(?:verdict|view|call|rating)\b", re.I)
_MARKUP = re.compile(r"(?m)^\s*(?:[-*•#]|\d+[.)])\s")


def check_reading(paragraph: str, allowed_verdicts) -> list[str]:
    """Every reason the paragraph cannot be shown (empty = it can)."""
    from ..narration_schema import _asserted_verdict_words

    text = (paragraph or "").strip()
    if not text:
        return ["the paragraph is empty"]
    problems: list[str] = []
    words = len(text.split())
    if words > MAX_WORDS:
        problems.append(f"it is {words} words; the limit is {MAX_WORDS}")
    if "\n" in text or _MARKUP.search(text):
        problems.append("it is not a single plain paragraph")
    if _RECOMMENDS.search(text):
        problems.append("it reads as a recommendation or says which side is right")
    if _COMBINES.search(text):
        problems.append("it combines the judges into a new verdict")
    allowed = {str(v).lower() for v in allowed_verdicts}
    stray = sorted(_asserted_verdict_words(text) - allowed)
    if stray:
        problems.append("it asserts a verdict word nobody gave: " + ", ".join(w.upper() for w in stray))
    return problems


def _material(result, report) -> str:
    rec = result.record
    lines = [f"Company: {report.display} ({report.ticker})",
             f"Verdict of record (the lenses): {rec.verdict} - {rec.headline}; split {rec.split}",
             f"Panel against the record: {result.agreement}", "", "The judges:"]
    for j in result.judges:
        if j.status == STABLE:
            c = j.first_ok
            said = "ABSTAINED" if j.verdict == "ABSTAIN" else j.verdict
            lines.append(f"- {j.label} ({j.model}): {said}; "
                         f"confidence {c.confidence}; reason: {c.reason}; would change its mind if: "
                         f"{c.would_change_my_mind}")
        elif j.status == UNSTABLE:
            lines.append(f"- {j.label}: unstable (its two answers were " + " and ".join(j.verdicts) + ")")
        else:
            lines.append(f"- {j.label}: {j.verdict_text()}")
    return "\n".join(lines)


def allowed_verdicts(result) -> set[str]:
    out = {w for w in (result.record.verdict,) if w in ("BUY", "HOLD", "SELL")}
    out |= {j.verdict for j in result.judges if j.status == STABLE and j.verdict in ("BUY", "HOLD", "SELL")}
    out |= {v for j in result.judges if j.status == UNSTABLE for v in j.verdicts if v in ("BUY", "HOLD", "SELL")}
    return out


def _default_runner():
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    from ..agents.runners import LangChainRunner
    return LangChainRunner("decision", PanelReadingOutput)


def write_panel_reading(result, report, pack=None, *, runner=None) -> PanelReading:
    """ONE model call (plus at most one corrective retry). Skipped - returning a reading with a
    note and no call - when the panel agrees or no judge is stable. Never raises."""
    if not needs_reading(result.agreement) or not any(j.status == STABLE for j in result.judges):
        return PanelReading(note="not needed: the panel agrees, or no judge is stable")
    runner = runner or _default_runner()
    if runner is None:
        return PanelReading(note=NO_KEY_NOTE)
    system = PROMPT_PATH.read_text(encoding="utf-8")
    user = _material(result, report)
    allowed = allowed_verdicts(result)
    calls, problems = 0, []
    try:
        for attempt in (1, 2):
            prompt = user if attempt == 1 else (
                user + "\n\nYour previous paragraph was rejected: " + "; ".join(problems) +
                ". Write it again, fixing exactly that.")
            calls += 1
            out = runner.invoke(system, prompt)
            paragraph = str(getattr(out, "paragraph", "") or "").strip()
            problems = check_reading(paragraph, allowed)
            if not problems:
                return PanelReading(paragraph=" ".join(paragraph.split()), calls=calls)
    except Exception as exc:                                # noqa: BLE001 - reported, never raised
        return PanelReading(note=f"The reading is unavailable: {type(exc).__name__}: {str(exc)[:160]}",
                            calls=calls)
    return PanelReading(note="The reading was withheld: " + "; ".join(problems), calls=calls)
