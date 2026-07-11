"""Deterministic rank-semantics post-check on the narrator's output (ITEM 4).

The narrator (an LLM) occasionally INVERTS or misstates rank ordinals while every number
it cites is correct — e.g. calls rank 2 "second-worst" (it's second-BEST), or claims a
combined rank-sum is "the best in the cohort" when a lower one exists. This module parses
ordinal superlative claims out of the generated narrative and verifies them against the
authoritative rank table. On a contradiction it APPENDS a machine annotation to the
narrative — it never silently rewrites the model's prose. The rank table is authoritative.

Rank convention: **rank 1 = best** on every factor; **lower combined rank-sum = better**.

Two parser disciplines the check must honour (NARR-CHK-1), both learned from false
positives on real garp_v2 narration:
- **Decimals are atomic.** Sentence splitting must not break `digits.digits` — a claim
  like "CAGR of 31.4%" must never be truncated to "CAGR of 31" (which then reads as a bare
  ordinal/number and mis-parses).
- **Ordinals bind to the factor they NAME, not to a column position.** A sentence like
  "1st on revenue_growth, 2nd on roic, 4th on momentum_12m" states each ordinal against a
  named factor, in any order; each is validated against THAT factor's rank in the table.

A superlative is only a checkable claim when it is genuinely predicated of the narrated
name's rank. These are NOT claims and are left alone (NARR-CHK-1/2):
- **Hedged** — "near-best", "almost worst" (approximations).
- **Theoretical bounds / cohort arithmetic** — "worst possible = 48", "best case" (a
  hypothetical, not the name).
- **Generic/hypothetical subjects** — "the best-ranked NAME in the cohort is not insulated
  …" describes a role, not the narrated name (so the combined subject requires the explicit
  "combined"/"rank-sum" metric word, not a bare "in the cohort").
- **Spelled relative ordinals** — "third-best" is rank 3, never the bare "best" (rank 1).
"""

from __future__ import annotations

import re
from typing import Callable, Optional

# Word superlative tokens -> the rank position they imply, given cohort size N.
# Ordered SPECIFIC-FIRST so "second-worst" is matched before "worst", etc.
# NB: only UNAMBIGUOUS superlatives. "top"/"bottom" are deliberately omitted — phrases
# like "third from the bottom" are correct and must NOT be flagged; the observed error
# class (best/worst/second-*) is fully covered without them. Spelled-out ordinals
# ("third") are likewise NOT parsed here — only the digit ordinals below.
_ORDINALS: tuple[tuple[str, Callable[[int], int]], ...] = (
    (r"second[-\s]worst", lambda n: n - 1),
    (r"second[-\s]best", lambda n: 2),
    (r"best[-\s]in[-\s]cohort", lambda n: 1),
    (r"worst", lambda n: n),
    (r"best", lambda n: 1),
)

# A superlative is NOT a checkable ordinal claim when the text just BEFORE it is:
#   - a HEDGE ("near-best" ≈ rank 2, not rank 1), or
#   - a spelled RELATIVE-ORDINAL prefix ("third-best", "fourth-worst" — a specific rank,
#     NOT the bare superlative; NARR-CHK-2 class 3 flagged "third-best" as "best"=rank 1).
# Matched against the text ENDING just before the ordinal token. ("second-*" is handled by
# its own specific pattern above, so it is intentionally absent here.)
_SKIP_BEFORE = re.compile(
    r"\b(?:near|nearly|almost|approx\w*|roughly|about|~"
    r"|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)[-\s]?$", re.I)

# A superlative is a THEORETICAL BOUND, not a claim about the name, when it is immediately
# FOLLOWED by "possible"/"case"/… ("worst possible = 48" is cohort arithmetic, not a claim
# that the name is worst; NARR-CHK-2 class 1). Matched against the text AFTER the token.
_SKIP_AFTER = re.compile(r"^[-\s]?(?:possible|case|conceivable|imaginable)\b", re.I)

# A DIGIT ordinal bound to a position: 1st, 2nd, 4th, 21st. Bound to whatever factor it
# names within the same clause (defect-b fix). Guards: not a bare "12" (needs the suffix),
# so "momentum_12m" never reads as an ordinal.
_NUM_ORDINAL = re.compile(r"\b(\d+)(?:st|nd|rd|th)\b", re.I)

# Factor subject phrases -> factor key(s) to look up in the rank table (first present
# wins). Underscore-tolerant, because the narrator quotes the factor KEYS
# (`revenue_growth`, `earnings_yield`, `momentum_12m`, …) verbatim.
_FACTOR_SUBJECTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"low[-\s_]?volatilit|volatilit|volatile", ("low_volatility",)),
    (r"revenue[-\s_]?(?:growth|cagr)|revenue", ("revenue_growth", "revenue_cagr")),
    (r"return on (?:invested )?capital|\broic\b", ("roic",)),
    (r"earnings[-\s_]?yield", ("earnings_yield",)),
    (r"net[-\s_]?payout|payout[-\s_]?yield", ("net_payout_yield",)),
    (r"price[-\s_]?to[-\s_]?book|p/b", ("price_to_book",)),
    (r"return on equity|\broe\b", ("return_on_equity",)),
    (r"momentum", ("momentum_12m", "momentum_6m")),
)

# Combined-position subjects (checked only when no factor subject is present). The EXPLICIT
# metric word binds on its own — the ASML class of catch, "best combined rank-sum". A LOOSE
# phrase ("in the cohort"/"overall") is too weak to bind a superlative by itself: it fired
# on generic/hypothetical subjects ("the best-ranked NAME in the cohort is not insulated…",
# NARR-CHK-2 class 2). It binds ONLY when the narrated name is NAMED in the text — a real
# claim about THIS name generally names it, a generic role reference does not.
_COMBINED_METRIC = re.compile(r"combined|rank[-\s]sum", re.I)
_COMBINED_LOOSE = re.compile(r"in the cohort|overall", re.I)

# "rank of 2 out of 10" / "rank 21 out of 23" / "rank 2/7" / "rank 5". NOT "rank-sum of 12"
# (a value, not a position) — the hyphen blocks the whitespace this requires after "rank".
_RANK_CITE = re.compile(
    r"\brank(?:\s+of)?\s+(\d+)(?:\s*(?:out\s+of|/)\s*(\d+))?", re.I)


def _word_ordinal(text: str) -> Optional[tuple[int, Callable[[int], int]]]:
    """The earliest UN-HEDGED word-superlative token in ``text`` (specific tokens win a
    tie) as ``(start, position_fn)``. A superlative that is hedged ("near-best"), a spelled
    relative ordinal ("third-best"), or a theoretical bound ("worst possible") is skipped."""
    best: Optional[tuple[int, Callable[[int], int]]] = None
    for pat, fn in _ORDINALS:
        m = re.search(pat, text, re.I)
        if (not m or _SKIP_BEFORE.search(text[:m.start()])
                or _SKIP_AFTER.match(text[m.end():])):
            continue
        if best is None or m.start() < best[0]:
            best = (m.start(), fn)
    return best


def _names_ticker(text: str, ticker: Optional[str]) -> bool:
    """Does ``text`` name the narrated ticker (word-boundary, case-sensitive)? A rank claim
    about THIS name generally names it; a generic role reference ("the best-ranked name")
    does not — the discriminator for NARR-CHK-2 class 2."""
    return bool(ticker) and re.search(rf"\b{re.escape(ticker)}\b", text) is not None


def _subject_rank(text: str, factors: dict, combined_position: Optional[int],
                  ticker: Optional[str] = None) -> Optional[int]:
    """The authoritative rank for the text's subject: a factor's rank if a factor phrase is
    present, else the name's combined position if a combined subject is present. The
    combined position binds on an EXPLICIT metric word ("combined"/"rank-sum"), or on a
    LOOSE phrase ("in the cohort") ONLY when the narrated name is named (else it is a
    generic reference, not a claim about this name). A named factor absent from the table ->
    ``None`` (undeterminable, never guessed)."""
    for pat, keys in _FACTOR_SUBJECTS:
        if re.search(pat, text, re.I):
            for k in keys:
                if k in factors and factors[k] is not None:
                    return int(round(factors[k]))
            return None
    if combined_position is not None:
        if _COMBINED_METRIC.search(text):
            return int(combined_position)
        if _COMBINED_LOOSE.search(text) and _names_ticker(text, ticker):
            return int(combined_position)
    return None


def _word_check(sentence: str, n: int, combined_position: Optional[int],
                factors: dict, ticker: Optional[str] = None) -> bool:
    """Word-superlative claim vs the table (the original discipline, hedge-aware):
    - **citation vs ordinal**: an explicit ``rank R out of M`` with a superlative must
      agree (rank 2 is not "second-worst").
    - **subject vs table**: a superlative about a named factor / the combined position must
      match that subject's actual rank.
    Ambiguous sentences (no ordinal, or >1 citation, or no resolvable subject) are left
    alone — the check never invents a contradiction."""
    wo = _word_ordinal(sentence)
    if wo is None:
        return False
    posfn = wo[1]
    cites = _RANK_CITE.findall(sentence)
    if len(cites) == 1:                          # citation vs ordinal
        r = int(cites[0][0])
        m = int(cites[0][1]) if cites[0][1] else n
        return r != posfn(m)
    if not cites:                                # subject vs table (no explicit rank)
        subj = _subject_rank(sentence, factors, combined_position, ticker)
        return subj is not None and subj != posfn(n)
    return False                                 # >1 citation -> pairing ambiguous


def _numeric_check(sentence: str, combined_position: Optional[int],
                   factors: dict, ticker: Optional[str] = None) -> bool:
    """Digit-ordinal claims bound to their NAMED factor, per clause (defect-b fix). Each
    comma/semicolon-separated clause states at most one ordinal about the factor it names;
    the ordinal is validated against THAT factor's rank — so factors named in any order
    each check against the right rank, never a positional column. A clause with no factor
    (or a factor absent from the table), no ordinal, or more than one ordinal is skipped."""
    for clause in re.split(r"[,;]", sentence):
        nums = [int(m.group(1)) for m in _NUM_ORDINAL.finditer(clause)
                if not _SKIP_BEFORE.search(clause[:m.start()])]
        if len(nums) != 1:
            continue
        subj = _subject_rank(clause, factors, combined_position, ticker)
        if subj is not None and subj != nums[0]:
            return True
    return False


def _sentences(text: str) -> list[str]:
    # Split on sentence punctuation, but NEVER on a period between two digits (a decimal):
    # "31.4" stays atomic, while "…high. Next…" still splits.
    parts = re.split(r"(?<!\d)\.|\.(?!\d)|[!?\n]+", text)
    return [s.strip() for s in parts if s and s.strip()]


def _claim(sentence: str) -> str:
    return re.sub(r"\s+", " ", sentence).strip()


def _annotation(claim: str) -> str:
    return (f'[⚠ narration check: "{claim}" contradicts rank table — '
            f'table is authoritative]')


def check_narration(narrative: str, table: dict) -> list[str]:
    """Return machine annotations for ordinal claims that contradict the rank ``table``.

    ``table``: ``{"N": cohort_size, "combined_position": int|None,
    "factors": {factor_key: rank}}``. Each sentence is checked two ways — a word-superlative
    claim (best/worst/second-*) against a citation or its subject, and any digit ordinals
    bound to the factor each names. Correct ordinal statements (in any factor order) pass
    untouched; ambiguous or hedged sentences are left alone — the check never invents a
    contradiction, and never rewrites the prose.
    """
    if not narrative:
        return []
    n = table.get("N")
    if not n or n < 1:
        return []
    combined_position = table.get("combined_position")
    factors = table.get("factors", {}) or {}
    ticker = table.get("ticker")

    flags: list[str] = []
    seen: set[str] = set()
    for sentence in _sentences(narrative):
        if (_word_check(sentence, n, combined_position, factors, ticker)
                or _numeric_check(sentence, combined_position, factors, ticker)):
            claim = _claim(sentence)
            if claim not in seen:
                seen.add(claim)
                flags.append(_annotation(claim))
    return flags
