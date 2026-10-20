"""JUDGE-PANEL-3 - the one answer shape a judge may give, and its strict validation.

A judge answers with ONE JSON object: ``verdict`` (BUY|HOLD|SELL|ABSTAIN), ``confidence``
(low|medium|high), ``driving_fact`` (the ONE fact that drove the verdict most), ``reasoning`` (up
to 150 words), ``key_risk``, ``unexplained`` (the most important thing in the pack it could not
account for, or "nothing") and ``would_change_my_mind``.

The judge may use what it knows beyond the pack, but every claim in ``driving_fact`` and
``reasoning`` must end with its source, ``[pack]`` or ``[own knowledge]``. An unlabelled claim is a
FORMAT error - the caller retries once and then records "invalid output" - because a reader has to
be able to see which claims came from outside the evidence. A model is never "helped" by guessing
what it meant.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

VERDICTS = ("BUY", "HOLD", "SELL", "ABSTAIN")
CONFIDENCES = ("low", "medium", "high")
KEYS = ("verdict", "confidence", "driving_fact", "reasoning", "key_risk", "unexplained",
        "would_change_my_mind")
LABELLED = ("driving_fact", "reasoning")             # every claim here must carry a source tag
FREE = ("key_risk", "unexplained", "would_change_my_mind")

PACK_TAG = "[pack]"
OWN_TAG = "[own knowledge]"
MAX_REASONING_WORDS = 150
MAX_FACT_WORDS = 60
MAX_SENTENCE_CHARS = 700
MAX_REASONING_CHARS = 1600

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S | re.I)
_TAG = re.compile(r"\[(pack|own knowledge)\]", re.I)
_ANY_BRACKET = re.compile(r"\[[^\]]*\]")
# a sentence boundary inside ONE labelled claim: lower-case / digit / % / ) then ". " then a capital.
# ("U.S. sales", "28.2x" do not match.)
_SENTENCE_BREAK = re.compile(r"(?<=[a-z0-9%\)])[.!?]\s+(?=[A-Z\"(])")


@dataclass(frozen=True)
class Answer:
    verdict: str
    confidence: str
    driving_fact: str
    reasoning: str
    key_risk: str
    unexplained: str
    would_change_my_mind: str

    @property
    def own_knowledge_claims(self) -> int:
        """How many claims the judge marked as coming from outside the pack."""
        return sum(len(_TAG.findall(t)) - len(re.findall(r"\[pack\]", t, re.I))
                   for t in (self.driving_fact, self.reasoning))


def count_claims(*texts: str) -> tuple:
    """``(pack, own knowledge)`` claim counts over the labelled texts (OWN-KNOWLEDGE-COUNT-1)."""
    pack = own = 0
    for t in texts:
        for _claim_text, source in segments(t):
            if source == "pack":
                pack += 1
            elif source == "own knowledge":
                own += 1
    return pack, own


def strip_tags(text: str) -> str:
    """The text without its [pack] / [own knowledge] tags, spacing and the closing full stop tidied
    (STORY-TAG-LEAK-1: a tag is bookkeeping for the judge card, never prose for the story)."""
    out = _TAG.sub("", text or "")
    return re.sub(r"\s+([.,;])", r"\1", re.sub(r"\s{2,}", " ", out)).strip()


def _object_text(raw: str) -> str:
    """The JSON object text: the reply itself, or the reply inside ONE code fence. Prose around it
    is not tolerated."""
    text = (raw or "").strip()
    fenced = _FENCE.match(text)
    return fenced.group(1).strip() if fenced else text


def _normalise_tags(text: str) -> str:
    return _TAG.sub(lambda m: PACK_TAG if m.group(1).lower() == "pack" else OWN_TAG, text)


def labelling_problem(text: str) -> str:
    """"" when every claim in ``text`` ends with a valid source tag, else why not."""
    unknown = [b for b in _ANY_BRACKET.findall(text) if not _TAG.fullmatch(b)]
    if unknown:
        return f"unknown source label {unknown[0]}"
    parts = _TAG.split(text)                       # text, tag-name, text, tag-name, ..., trailing text
    claims, trailing = parts[0::2][:-1], parts[-1]
    if len(parts) == 1:
        return "no claim is labelled [pack] or [own knowledge]"
    if re.search(r"\w", trailing):
        return "text after the last label is unlabelled"
    for claim in claims:
        if not re.search(r"\w", claim):
            return "a label has no claim before it"
        if _SENTENCE_BREAK.search(claim.strip()):
            return "a label covers more than one sentence; label every claim"
    return ""


def _words(text: str) -> int:
    return len(_TAG.sub(" ", text).split())


def parse_answer(raw: str) -> tuple[Optional[Answer], str]:
    """``(answer, "")`` when ``raw`` is a valid judge answer, else ``(None, why)``."""
    text = _object_text(raw)
    if not text:
        return None, "empty reply"
    try:
        doc = json.loads(text)
    except (ValueError, TypeError) as exc:
        return None, f"not JSON ({type(exc).__name__})"
    if not isinstance(doc, dict):
        return None, "not a JSON object"
    missing = [k for k in KEYS if k not in doc]
    extra = [k for k in doc if k not in KEYS]
    if missing:
        return None, "missing key(s): " + ", ".join(missing)
    if extra:
        return None, "unexpected key(s): " + ", ".join(sorted(map(str, extra)))
    verdict = doc["verdict"]
    if not isinstance(verdict, str) or verdict.strip().upper() not in VERDICTS:
        return None, f"verdict is not one of {', '.join(VERDICTS)}"
    confidence = doc["confidence"]
    if not isinstance(confidence, str) or confidence.strip().lower() not in CONFIDENCES:
        return None, f"confidence is not one of {', '.join(CONFIDENCES)}"
    values = {}
    for key in LABELLED + FREE:
        value = doc[key]
        if not isinstance(value, str) or not value.strip():
            return None, f"{key} is empty"
        if len(value) > (MAX_REASONING_CHARS if key == "reasoning" else MAX_SENTENCE_CHARS):
            return None, f"{key} is too long"
        values[key] = _normalise_tags(re.sub(r"\s+", " ", value).strip())
    for key, cap in (("reasoning", MAX_REASONING_WORDS), ("driving_fact", MAX_FACT_WORDS)):
        if _words(values[key]) > cap:
            return None, f"{key} is longer than {cap} words"
        problem = labelling_problem(values[key])
        if problem:
            return None, f"{key}: {problem}"
    return Answer(verdict.strip().upper(), confidence.strip().lower(), **values), ""


def _claim(text: str) -> str:
    """A claim as displayed: leading punctuation (the full stop that followed the previous tag)
    removed, and a full stop added when it has none."""
    text = text.strip().lstrip(".,;: ").strip()
    return text if not text or text[-1] in ".!?" else text + "."


def segments(text: str) -> list[tuple[str, str]]:
    """``[(claim, "pack" | "own knowledge"), ...]`` for a labelled text; an unlabelled remainder is
    ``(text, "")``. The page marks the own-knowledge ones."""
    parts = _TAG.split(text or "")
    out = [(_claim(parts[i]), parts[i + 1].lower()) for i in range(0, len(parts) - 1, 2)
           if _claim(parts[i]).strip(". ")]
    if len(parts) % 2 == 1 and parts[-1].strip(" ."):
        out.append((parts[-1].strip(), ""))
    return out
