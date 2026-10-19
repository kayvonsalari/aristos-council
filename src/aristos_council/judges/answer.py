"""JUDGE-PANEL-1 - the one answer shape a judge may give, and its strict validation.

A judge answers with ONE JSON object: ``verdict`` (BUY|HOLD|SELL|ABSTAIN), ``confidence``
(low|medium|high), ``reason`` and ``would_change_my_mind`` (one sentence each). Anything else -
a missing key, an extra key, a verdict outside the four words, an empty sentence, prose around the
object that is not a code fence - is INVALID, and the caller retries once and then records
"invalid output". A model is never "helped" by guessing what it meant.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

VERDICTS = ("BUY", "HOLD", "SELL", "ABSTAIN")
CONFIDENCES = ("low", "medium", "high")
KEYS = ("verdict", "confidence", "reason", "would_change_my_mind")

MAX_SENTENCE_CHARS = 700

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S | re.I)


@dataclass(frozen=True)
class Answer:
    verdict: str
    confidence: str
    reason: str
    would_change_my_mind: str


def _object_text(raw: str) -> str:
    """The JSON object text: the reply itself, or the reply inside ONE code fence. Prose around it
    is not tolerated."""
    text = (raw or "").strip()
    fenced = _FENCE.match(text)
    return fenced.group(1).strip() if fenced else text


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
    sentences = []
    for key in ("reason", "would_change_my_mind"):
        value = doc[key]
        if not isinstance(value, str) or not value.strip():
            return None, f"{key} is empty"
        if len(value) > MAX_SENTENCE_CHARS:
            return None, f"{key} is longer than {MAX_SENTENCE_CHARS} characters"
        sentences.append(re.sub(r"\s+", " ", value).strip())
    return Answer(verdict.strip().upper(), confidence.strip().lower(), *sentences), ""
