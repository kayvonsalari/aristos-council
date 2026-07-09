"""Deterministic rank-semantics post-check on the narrator's output (ITEM 4).

The narrator (an LLM) occasionally INVERTS or misstates rank ordinals while every number
it cites is correct — e.g. calls rank 2 "second-worst" (it's second-BEST), or claims a
combined rank-sum is "the best in the cohort" when a lower one exists. This module parses
ordinal superlative claims out of the generated narrative and verifies them against the
authoritative rank table. On a contradiction it APPENDS a machine annotation to the
narrative — it never silently rewrites the model's prose. The rank table is authoritative.

Rank convention: **rank 1 = best** on every factor; **lower combined rank-sum = better**.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

# Ordinal superlative tokens -> the rank position they imply, given cohort size N.
# Ordered SPECIFIC-FIRST so "second-worst" is matched before "worst", etc.
# NB: only UNAMBIGUOUS superlatives. "top"/"bottom" are deliberately omitted — phrases
# like "third from the bottom" are correct and must NOT be flagged; the observed error
# class (best/worst/second-*) is fully covered without them.
_ORDINALS: tuple[tuple[str, Callable[[int], int]], ...] = (
    (r"second[-\s]worst", lambda n: n - 1),
    (r"second[-\s]best", lambda n: 2),
    (r"best[-\s]in[-\s]cohort", lambda n: 1),
    (r"worst", lambda n: n),
    (r"best", lambda n: 1),
)

# Factor subject phrases -> factor key(s) to look up in the rank table (first present wins).
_FACTOR_SUBJECTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"low[-\s]volatilit|volatilit|volatile", ("low_volatility",)),
    (r"return on (?:invested )?capital|roic", ("roic",)),
    (r"earnings[-\s]yield", ("earnings_yield",)),
    (r"net[-\s]payout|payout[-\s]yield", ("net_payout_yield",)),
    (r"momentum", ("momentum_12m", "momentum_6m")),
)

# A COMBINED/overall subject (checked only when no factor subject is present).
_COMBINED_SUBJECT = re.compile(r"combined|rank[-\s]sum|in the cohort|overall", re.I)

# "rank of 2 out of 10" / "rank 21 out of 23" / "rank 5". NOT "rank-sum of 12" (a value,
# not a position) — the hyphen blocks the whitespace this requires after "rank".
_RANK_CITE = re.compile(r"\brank(?:\s+of)?\s+(\d+)(?:\s+out\s+of\s+(\d+))?", re.I)


def _first_ordinal(sentence: str) -> Optional[tuple[str, Callable[[int], int]]]:
    """The earliest ordinal token in the sentence (specific tokens win a tie)."""
    best: Optional[tuple[int, str, Callable[[int], int]]] = None
    for pat, fn in _ORDINALS:
        m = re.search(pat, sentence, re.I)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), m.group(0), fn)
    return None if best is None else (best[1], best[2])


def _subject_rank(sentence: str, factors: dict, combined_position: Optional[int]
                  ) -> Optional[int]:
    """The authoritative rank for the sentence's subject: a factor's rank if a factor
    phrase is present, else the name's combined position if a combined phrase is present."""
    for pat, keys in _FACTOR_SUBJECTS:
        if re.search(pat, sentence, re.I):
            for k in keys:
                if k in factors and factors[k] is not None:
                    return int(round(factors[k]))
    if combined_position is not None and _COMBINED_SUBJECT.search(sentence):
        return int(combined_position)
    return None


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[.!?\n]+", text) if s.strip()]


def _claim(sentence: str) -> str:
    return re.sub(r"\s+", " ", sentence).strip()


def _annotation(claim: str) -> str:
    return (f'[⚠ narration check: "{claim}" contradicts rank table — '
            f'table is authoritative]')


def check_narration(narrative: str, table: dict) -> list[str]:
    """Return machine annotations for ordinal claims that contradict the rank ``table``.

    ``table``: ``{"N": cohort_size, "combined_position": int|None,
    "factors": {factor_key: rank}}``. Two checks per sentence:
    - **citation vs ordinal**: an explicit ``rank R out of M`` with an ordinal must agree
      (rank 2 is not "second-worst").
    - **subject vs table**: an ordinal about a named factor / the combined position must
      match that subject's actual rank in the table.
    Correct ordinal statements pass untouched; ambiguous sentences (no ordinal, or no
    resolvable subject/citation) are left alone — the check never invents a contradiction.
    """
    if not narrative:
        return []
    n = table.get("N")
    if not n or n < 1:
        return []
    combined_position = table.get("combined_position")
    factors = table.get("factors", {}) or {}

    flags: list[str] = []
    seen: set[str] = set()
    for sentence in _sentences(narrative):
        ordinal = _first_ordinal(sentence)
        if ordinal is None:
            continue
        _, posfn = ordinal
        contradiction = False

        cites = _RANK_CITE.findall(sentence)
        if len(cites) == 1:                         # citation vs ordinal
            r = int(cites[0][0])
            m = int(cites[0][1]) if cites[0][1] else n
            if r != posfn(m):
                contradiction = True
        elif not cites:                             # subject vs table (no explicit rank)
            subj = _subject_rank(sentence, factors, combined_position)
            if subj is not None and subj != posfn(n):
                contradiction = True
        # >1 citation in one sentence: pairing is ambiguous -> leave it alone.

        if contradiction:
            claim = _claim(sentence)
            if claim not in seen:
                seen.add(claim)
                flags.append(_annotation(claim))
    return flags
