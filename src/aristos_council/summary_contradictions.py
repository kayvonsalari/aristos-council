"""PILOT-STRIP-1 item 22 - a summary statement that CONTRADICTS the report.

The summary check already withholds a number the page does not hold, a name it does not carry, a rule given the
wrong role and a count the table contradicts. Two statements passed it on the Coca-Cola page of 2026-11-20, and
each was plainly false against the page it sat on:

* "no backtest of this method exists yet", on a page with a "Backtest of this method in this industry" section;
* "dividend cover is 1.3 times, which sits in the range sources call thin to comfortable", when that range is
  1.5 to 2.0 and 1.3 is below it.

Neither is a wrong NUMBER (1.3, 1.5 and 2.0 are all on the page), which is why the number check let them through.
Both are a wrong RELATION between the summary and the report, and this module checks relations. Each rule reads a
fact the facts pack states outright and looks for a sentence that says the opposite. Like the other four checks it
WITHHOLDS, because a summary that contradicts its own page is worse than no summary.

Deterministic; nothing here reads a model. A pack without the keys (the list report's pack) is checked by no rule,
so the run summary behaves exactly as before.

To add a rule: write ``fn(text, pack) -> list[str]`` returning one plain problem per contradiction, and add it to
``RULES``. Be narrow: a missed contradiction publishes one loose sentence, a false alarm destroys a true summary.
"""
from __future__ import annotations

import re
from typing import Optional

LEAD = "contradicts the report"


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]


# --------------------------------------------------------------------------- #
# Rule 1 - "no backtest exists" on a page that shows one
# --------------------------------------------------------------------------- #
_NO_BACKTEST = re.compile(
    r"\bno back-?test(?:ing)?\b[^.]{0,40}\b(?:exists?|has been (?:run|done)|is available|available)\b"
    r"|\bno back-?test (?:of|for) (?:this|the) (?:method|approach)\b"
    r"|\b(?:method|approach) (?:has|is) (?:not|never)(?: yet)? (?:been )?back-?tested\b"
    r"|\b(?:has|have) not(?: yet)? been back-?tested\b"
    r"|\bthere is no back-?test\b"
    r"|\bback-?test[^.]{0,30}\b(?:does not|doesn't) exist\b", re.I)


def backtest_problems(text: str, pack: dict) -> list[str]:
    """The summary says no backtest exists while the report carries one."""
    bt = (pack or {}).get("backtest") or {}
    if not bt.get("exists"):
        return []
    for sentence in _sentences(text):
        if _NO_BACKTEST.search(sentence):
            return [f"{LEAD}: it says no backtest exists, but the report has a backtest section for this industry"]
    return []


# --------------------------------------------------------------------------- #
# Rule 2 - a reading placed inside a range it is outside of (or outside one it is in)
# --------------------------------------------------------------------------- #
_RANGE_IN_LIMIT = re.compile(r"(\d+(?:\.\d+)?)\s+to\s+(\d+(?:\.\d+)?)\s+is the range")
_FIRST_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_SAYS_OUTSIDE = re.compile(r"\b(?:not|n't|never)\s+(?:\w+\s+){0,2}(?:in|within|inside)\s+(?:the|that|this|a)\s+range\b"
                           r"|\boutside\s+(?:of\s+)?(?:the|that|this|a)\s+range\b", re.I)
_SAYS_INSIDE = re.compile(r"\b(?:in|within|inside)\s+(?:the|that|this|a)\s+range\b", re.I)
_SAYS_BELOW = re.compile(r"\b(?:below|under|beneath|short of)\s+(?:the|that|this|a)\s+range\b", re.I)
_SAYS_ABOVE = re.compile(r"\b(?:above|over|beyond)\s+(?:the|that|this|a)\s+range\b", re.I)


def range_position(reading: str, limit: str) -> Optional[tuple[str, str, str, str]]:
    """``(position, value, low, high)`` with position "below" / "inside" / "above", when the rule's limit text
    names a range ("1.5 to 2.0 is the range sources call thin to comfortable") and its reading is a number.
    None otherwise. The strings are the page's own, so a sentence built from them adds no new number."""
    m = _RANGE_IN_LIMIT.search(limit or "")
    n = _FIRST_NUMBER.search(reading or "")
    if not m or not n:
        return None
    value, low, high = float(n.group(0)), float(m.group(1)), float(m.group(2))
    position = "below" if value < low else "above" if value > high else "inside"
    return position, n.group(0), m.group(1), m.group(2)


def position_sentence(name: str, reading: str, limit: str) -> str:
    """"Dividend cover of 1.3 is below the range 1.5 to 2.0." - stated in the facts pack so the writer never has
    to work the comparison out. "" when the rule has no range."""
    pos = range_position(reading, limit)
    if pos is None:
        return ""
    position, value, low, high = pos
    where = "inside" if position == "inside" else position
    return f"{name} of {value} is {where} the range {low} to {high}."


def _ranged_rules(pack: dict) -> list[dict]:
    gate = (pack or {}).get("gate") or {}
    return [r for r in list(gate.get("rules") or []) + list(gate.get("warnings") or [])
            if range_position(r.get("reading") or "", r.get("limit") or "") is not None]


def _claimed_position(sentence: str) -> Optional[str]:
    if _SAYS_OUTSIDE.search(sentence):
        return "outside"
    if _SAYS_BELOW.search(sentence):
        return "below"
    if _SAYS_ABOVE.search(sentence):
        return "above"
    if _SAYS_INSIDE.search(sentence):
        return "inside"
    return None


def range_problems(text: str, pack: dict) -> list[str]:
    """A sentence that names a ranged rule (or is the only one that could mean it) and puts its reading on the
    wrong side of the range."""
    rules = _ranged_rules(pack)
    out: list[str] = []
    for sentence in _sentences(text):
        claimed = _claimed_position(sentence)
        if claimed is None:
            continue
        low_sentence = sentence.lower()
        named = [r for r in rules if (r.get("name") or "").lower() in low_sentence]
        # a sentence about "the range" that names no rule can only mean the one ranged rule there is
        about = named or (rules if len(rules) == 1 else [])
        for r in about:
            actual, value, low, high = range_position(r.get("reading") or "", r.get("limit") or "")
            wrong = (claimed != actual) if claimed != "outside" else (actual == "inside")
            if not wrong:
                continue
            said = "outside" if claimed == "outside" else claimed
            problem = (f"{LEAD}: it says {(r.get('name') or '').lower()} of {value} is {said} the range {low} to "
                       f"{high}; it is {actual} that range")
            if problem not in out:
                out.append(problem)
    return out


RULES = (backtest_problems, range_problems)


def contradiction_problems(text: str, pack: dict) -> list[str]:
    """Every contradiction every rule finds, in rule order."""
    out: list[str] = []
    for rule in RULES:
        out += rule(text, pack)
    return out
