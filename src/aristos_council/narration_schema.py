"""NARR-SCHEMA-1 — structural validation of a narration, before it enters a report.

WHY THIS IS SEPARATE FROM narration_check.py
``narration_check`` validates CONTENT: does a rank claim in the prose agree with the rank
table? This validates STRUCTURE: are the sections the report renders actually there, does
a confidence parse as a number in [0,1], is every numeric claim in a recognised format
readable, and does the verdict WORD the narrator quoted back match the ranker's
verdict-of-record.

The two must not duplicate-fight, so the boundary is drawn deliberately:

    narration_check   ->  rank PROSE. "#2 of 12", "best ROIC in the cohort", tie claims.
    narration_schema  ->  the verdict WORD only, plus shape and parseability.

This one never looks at ordinals and never re-checks a rank sentence; that stays the
fact-checker's job. It exists because the narration was structured BY CONVENTION — the
schema admits an empty ``lens_verdicts``, a ``confidence`` of 4.2 and a narration that
echoes SELL over a ranker BUY, and every one of those would have reached a report
unflagged.

DOCTRINE, inherited from the fact-checker: ANNOTATE, NEVER REWRITE. A narration that
fails validation still ships, carrying a visible banner in both the ``.md`` and the
``.html``, and the reasons are recorded machine-readably on the run. Nothing here fixes,
drops, or reorders a narration — a silent repair leaves no evidence the contract broke,
and the next run breaks it again.

Pure and deterministic: no LLM call, no IO, no clock. Same narration -> same issues.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# The sections a narration MUST carry, derived from what the narrator emits today and
# what the report renders — NOT a new format. Each is load-bearing:
#   echoed_verdict    the ranker's verdict, quoted back. Its absence is how a narration
#                     silently stops being ABOUT the verdict it is explaining.
#   lens_verdicts     EVERY lens's verdict, before any prose (NARR-UNION-1). Missing, the
#                     reader gets a one-sided case.
#   lens_attribution  why each lens placed it there — the narration's actual content.
#   specialist_views  the specialist alignment panel.
# Deliberately NOT required: disagreement_note (present only when lenses disagree),
# neutral_context, open_questions, money_series — all legitimately empty for some names,
# and requiring them would push the narrator to invent filler.
REQUIRED_SECTIONS: tuple[str, ...] = (
    "echoed_verdict", "lens_verdicts", "lens_attribution", "specialist_views",
)

# The verdict vocabulary the ranker issues. Matched as WHOLE WORDS, case-insensitively.
_VERDICTS = ("buy", "hold", "sell")

# Numeric shapes a narration is allowed to state. A token that LOOKS like one of these
# but will not parse is the failure this catches — "12.3.4%", "1/", "$1,2,3".
_PERCENT = re.compile(r"(?<![\w.])(-?\d[\d,]*(?:\.\d+)?)\s*%")
_RANK = re.compile(r"(?<![\w/])(\d+)\s*/\s*(\d+)(?![\w/])")
_MONEY = re.compile(r"(?:[$€£¥]|\b(?:USD|EUR|GBP|JPY|CHF|KRW|SEK|DKK|NOK|CAD|AUD|HKD|TWD)\s?)"
                    r"(-?[\d][\d,]*(?:\.\d+)?)\s*(tn|bn|m)?", re.I)
# A malformed numeric token: more than one decimal point, or a stray thousands group.
_BAD_NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*\.\d+\.\d+(?![\w])")
_BAD_RANK = re.compile(r"(?<![\w/])\d+\s*/\s*(?![\d])")


@dataclass(frozen=True)
class StructuralIssue:
    """One structural failure. ``code`` is the machine-readable key recorded on the run;
    ``detail`` is the sentence a reader sees in the banner."""

    code: str
    detail: str

    def as_dict(self) -> dict:
        return {"code": self.code, "detail": self.detail}


def _text_fields(narration) -> list[str]:
    """Every free-text field, for the parseability sweep."""
    out = [narration.echoed_verdict or "", narration.disagreement_note or ""]
    out += list(narration.neutral_context or [])
    out += list(narration.open_questions or [])
    out += [a.reasoning or "" for a in (narration.lens_attribution or [])]
    for s in narration.specialist_views or []:
        out += [s.reasoning or "", s.not_assessed_reason or ""]
    return [t for t in out if t and t.strip()]


def _verdict_words(text: str) -> set[str]:
    return {m.group(0).lower()
            for m in re.finditer(r"\b(buy|hold|sell)\b", text or "", re.I)}


def validate_narration(narration, *, ranker_verdict: Optional[str] = None,
                       ticker: str = "") -> list[StructuralIssue]:
    """Every structural failure in one narration, in a stable order.

    ``ranker_verdict`` enables check (d); omit it and the verdict-word check is SKIPPED
    rather than guessed — a run with no verdict-of-record to compare against is not a
    mismatch, and inventing one would be the opposite of the house rule.
    """
    issues: list[StructuralIssue] = []
    if narration is None:
        return [StructuralIssue("narration_absent",
                                "No structured narration was produced for this name.")]

    # (a) required sections
    for name in REQUIRED_SECTIONS:
        value = getattr(narration, name, None)
        empty = not value if not isinstance(value, str) else not value.strip()
        if empty:
            issues.append(StructuralIssue(
                f"missing_section:{name}",
                f"The narration carries no {name.replace('_', ' ')}."))

    # (b) confidence parses and sits in [0, 1]
    for view in narration.specialist_views or []:
        conf = view.confidence
        if conf is None:
            # None is legitimate — a NOT ASSESSED specialist carries no confidence at
            # all, which is the point of that state. Only a PRESENT one is range-checked.
            continue
        try:
            value = float(conf)
        except (TypeError, ValueError):
            issues.append(StructuralIssue(
                "confidence_unparseable",
                f"{view.specialist} reports a confidence that is not a number "
                f"({conf!r})."))
            continue
        if not (0.0 <= value <= 1.0):
            issues.append(StructuralIssue(
                "confidence_out_of_range",
                f"{view.specialist} reports a confidence of {value:g}, outside 0–1."))
        if not view.assessed and conf is not None:
            issues.append(StructuralIssue(
                "not_assessed_carries_confidence",
                f"{view.specialist} is marked not assessed but still carries a "
                f"confidence of {value:g} — a dark channel has no measurement."))

    # (c) every numeric claim in a recognised shape actually parses
    for text in _text_fields(narration):
        if _BAD_NUMBER.search(text):
            issues.append(StructuralIssue(
                "unparseable_figure",
                f"A figure cannot be read as a number: "
                f"\"{_BAD_NUMBER.search(text).group(0)}\"."))
        if _BAD_RANK.search(text):
            issues.append(StructuralIssue(
                "unparseable_rank",
                f"A rank is missing its cohort size: "
                f"\"{_BAD_RANK.search(text).group(0).strip()}\"."))
        for match in _PERCENT.finditer(text):
            if not _parses(match.group(1)):
                issues.append(StructuralIssue(
                    "unparseable_percentage",
                    f"A percentage cannot be read: \"{match.group(0)}\"."))
        for match in _MONEY.finditer(text):
            if not _parses(match.group(1)):
                issues.append(StructuralIssue(
                    "unparseable_amount",
                    f"A money amount cannot be read: \"{match.group(0).strip()}\"."))
        for match in _RANK.finditer(text):
            rank, cohort = int(match.group(1)), int(match.group(2))
            if cohort == 0 or rank > cohort:
                issues.append(StructuralIssue(
                    "impossible_rank",
                    f"A rank is outside its cohort: \"{match.group(0)}\"."))

    # (d) the verdict WORD matches the verdict-of-record. The WORD only — every ordinal
    # claim stays the fact-checker's, so the two never argue about the same sentence.
    if ranker_verdict:
        wanted = str(ranker_verdict).strip().lower()
        if wanted in _VERDICTS:
            said = _verdict_words(narration.echoed_verdict)
            if said and wanted not in said:
                issues.append(StructuralIssue(
                    "verdict_mismatch",
                    f"The narration echoes {'/'.join(sorted(said)).upper()} but the "
                    f"ranker's verdict of record{f' for {ticker}' if ticker else ''} "
                    f"is {wanted.upper()}."))
    return issues


def _parses(token: str) -> bool:
    try:
        float((token or "").replace(",", ""))
        return True
    except (TypeError, ValueError):
        return False


BANNER_TITLE = "Structural warning"
BANNER_NOTE = ("This narration did not satisfy the report's structural contract. It is "
               "shown UNCHANGED and in full — nothing has been fixed, dropped or "
               "reordered. Treat the points below as reasons to read it more carefully.")


def banner_lines(issues) -> list[str]:
    """The banner's human text — one source, so the ``.md`` and the ``.html`` say the
    same thing. Empty when the narration is well-formed."""
    if not issues:
        return []
    return [i.detail if isinstance(i, StructuralIssue) else str(i.get("detail", i))
            for i in issues]


def issues_as_records(issues) -> list[dict]:
    """The machine-readable form recorded on the run."""
    return [i.as_dict() if isinstance(i, StructuralIssue) else dict(i) for i in issues]
