"""JUDGE-FIGURE-CHECK-1 - every number a judge quotes must appear in the pack it was given.

2026-10-31 18:20, MSFT: the OpenAI judge wrote "cash and short-term investments were $76.7bn". That is the
lease figure; cash was $64.5bn. The company summary has always had this check (``reader_check``: a number the
pack does not hold withholds it); the judge panel did not. A judge's answer is not withheld - it has no vote
and changes nothing - so the check MARKS instead: the sentence that quotes a figure the pack does not hold gets
``[figure not in the pack]`` inline, the count is kept on the call, and the panel header states the total.

What it checks, and what it cannot: a number is "in the pack" when the same figure appears anywhere in the
pack text (the same normalised token the summary check uses). It does NOT check that the figure is attached to
the right label - $76.7bn IS in the MSFT pack, as the lease obligation, so presence alone would not have caught
the very sentence that motivated this check. Catching a right number on the wrong line needs a label-aware
check; that is a separate piece of work and is not claimed here.

A sentence the judge itself tagged ``[own knowledge]`` is not marked: its figures come from outside the pack by
the judge's own declaration, and the page already shows them as own knowledge.
"""
from __future__ import annotations

import re

from ..reader_check import _numbers, _pack_numbers

FIGURE_MARK = "[figure not in the pack]"

# the labelled/free text fields of an answer that can carry a figure
FIELDS = ("driving_fact", "reason", "key_risk", "unexplained", "would_change_my_mind")

_TAG = re.compile(r"\[(?:pack|own knowledge)\]", re.I)
_OWN = re.compile(r"\[own knowledge\]", re.I)
# A sentence ends at . ! ? or at a closing source tag, and the next one starts with a capital, a quote, a
# bracket, a currency sign or a digit. A "[" never starts one, so a tag - and the figure mark before it - stays on
# the sentence it belongs to ("... 21% upside. [figure not in the pack] [pack]" is ONE sentence).
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"($0-9])|(?<=\])\s+(?=[A-Z\"($0-9])")


def _split_sentences(text: str) -> list[str]:
    return [p for p in _SENTENCE_BREAK.split(text) if p and p.strip()]


def _with_mark(sentence: str) -> str:
    """The mark placed before the sentence's closing tag, so the tag still ends the claim."""
    m = re.search(r"\s*(\[(?:pack|own knowledge)\])\s*([.!?]*)\s*$", sentence, re.I)
    if m:
        return f"{sentence[:m.start()].rstrip()} {FIGURE_MARK} {m.group(1)}{m.group(2)}"
    return f"{sentence.rstrip()} {FIGURE_MARK}"


def mark_text(text: str, known: set) -> tuple[str, int]:
    """``(text with unsupported sentences marked, how many were)``. Idempotent: a sentence already marked is
    left alone."""
    if not text or not text.strip():
        return text, 0
    out: list[str] = []
    count = 0
    for sentence in _split_sentences(text):
        if FIGURE_MARK in sentence or _OWN.search(sentence):
            out.append(sentence)
            continue
        bare = _TAG.sub(" ", sentence)
        stray = [n for n in _numbers(bare) if n not in known]
        if stray:
            out.append(_with_mark(sentence))
            count += 1
        else:
            out.append(sentence)
    return " ".join(out), count


def count_marks(call) -> int:
    """How many sentences of ``call`` carry the mark - counted from the text, so it survives a save and a reload."""
    return sum((getattr(call, field, "") or "").count(FIGURE_MARK) for field in FIELDS)


def check_call(call, pack_text: str, known: set | None = None) -> int:
    """Mark every unsupported sentence of ``call`` (an ok ``JudgeCall``) in place and return how many. A call
    that did not answer is left alone."""
    if getattr(call, "status", "") != "ok":
        return 0
    known = known if known is not None else _pack_numbers(pack_text)
    total = 0
    for field in FIELDS:
        text, n = mark_text(getattr(call, field, "") or "", known)
        if n:
            setattr(call, field, text)
            total += n
    return total


def check_judges(judges, pack_text: str) -> int:
    """Run the check on every ok call of every judge; the total number of marked sentences."""
    known = _pack_numbers(pack_text)
    return sum(check_call(c, pack_text, known) for j in judges for c in j.calls)
