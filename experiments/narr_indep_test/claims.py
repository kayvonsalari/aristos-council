"""Claim extraction — normalizes a narration into a comparable set of factual claims
(NARR-INDEP-TEST Experiment A: "claim sets are identical up to the noise floor" is the
mechanical PASS criterion).

``aristos_council.narration_check.check_narration`` is the production fact-checker, but it
returns only pass/fail annotation STRINGS — it parses claims internally and discards the
matched value once checked (confirmed by inspection: every ``_word_check``/``_numeric_check``
/``_score_check`` helper returns a bare ``bool``). There is no reusable claim-extraction API
to import, so this module reuses the CHECKER's own parsing primitives (sentence/clause
splitting, markdown + polarity-descriptor stripping, the factor-subject regex table, the
ordinal token tables, the citation regexes) — importing them directly from
``narration_check`` — but assembles them into claims that are KEPT, not just checked.

Two claim families:
  - RANK claims (ordinal / cited-rank / rank-sum) — reuses the checker's own vocabulary, so
    a claim this module extracts is, by construction, exactly the class of claim the
    production checker itself would validate. NOTE (a genuine, discovered gap, not fixed
    here — out of scope for this harness): ``_FACTOR_SUBJECTS`` has no entry for ETF
    ``distribution_yield`` at all, so neither the checker nor this extractor can bind an
    ordinal claim ("distribution yield ranks 1st") to that factor. Flagged in the
    NARR-INDEP-TEST write-up as a follow-up, not silently routed around.
  - ABSOLUTE claims (a concrete fee/size/yield NUMBER near its factor name) — the checker
    has no equivalent at all (confirmed: it never reads ``factor_values``), so this is a
    fresh, narrow regex extractor, deliberately scoped to the three NARR-LEDGER-1 ledger
    factors.

Claim VALUES are compared with a tolerance (``compare_claims``), not exact float equality —
an LLM restating "42.99bn" as "€43.0bn" is the same claim, not a new one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from aristos_council.narration_check import (
    _COMBINED_LOOSE,
    _COMBINED_METRIC,
    _FACTOR_SUBJECTS,
    _NUM_ORDINAL,
    _RANK_CITE,
    _SCORE_CITE,
    _SCORE_SKIP_BEFORE,
    _binds,
    _cites_a_peers_score,
    _demark,
    _multi_factor_subject,
    _sentences,
    _strip_polarity_descriptors,
    _subject,
    _word_ordinal,
)


@dataclass(frozen=True)
class Claim:
    """One factual assertion, normalized for cross-narration comparison.

    ``kind``: "ordinal" (word/digit superlative bound to a subject), "cited_rank"
    ("rank R out of M"), "rank_sum" (a cited combined-score value), or "absolute" (a
    concrete fee/size/yield number). ``subject`` is a factor key, "combined", or
    "unbound" (a rank citation with no resolvable subject in its clause). ``value`` is
    the asserted number — a rank position, a rank-sum, or an absolute value in the
    factor's OWN convention (percent-points for expense_ratio, a decimal for
    distribution_yield, a raw currency amount for fund_size).
    """

    kind: str
    subject: str
    value: float


# --------------------------------------------------------------------------- #
# RANK claims — reuses narration_check's own vocabulary
# --------------------------------------------------------------------------- #
def _clause_subject_name(clause: str) -> Optional[str]:
    """Which factor key (or "combined") ``clause``'s subject names — the KEY the checker's
    own ``_subject()`` resolves a rank against, but ``_subject`` returns only the rank/span,
    not the name; this re-derives it from the SAME tables."""
    for pat, keys in _FACTOR_SUBJECTS:
        if re.search(pat, clause, re.I):
            return keys[0]
    if _COMBINED_METRIC.search(clause) or _COMBINED_LOOSE.search(clause):
        return "combined"
    return None


def _rank_claims_in_clause(clause: str, n: int, factors: dict,
                          combined_position: Optional[int],
                          ticker: Optional[str]) -> set[Claim]:
    claims: set[Claim] = set()
    cites = _RANK_CITE.findall(clause)
    subj = _subject(clause, factors, combined_position, ticker)
    subj_name = _clause_subject_name(clause)

    for r, m in cites:
        claims.add(Claim("cited_rank", subj_name or "unbound", float(r)))

    wo = _word_ordinal(clause, include_cited_only=len(cites) == 1)
    if wo is not None and subj is not None and not _multi_factor_subject(clause):
        start, end, posfn = wo
        if _binds(clause, (start, end), (subj[1], subj[2])):
            claims.add(Claim("ordinal", subj_name or "unbound", float(posfn(n))))

    if subj is not None and not _multi_factor_subject(clause):
        for m in _NUM_ORDINAL.finditer(clause):
            if _binds(clause, m.span(), (subj[1], subj[2])):
                claims.add(Claim("ordinal", subj_name or "unbound", float(m.group(1))))

    return claims


def _rank_sum_claims(sentence: str, ticker: Optional[str]) -> set[Claim]:
    claims: set[Claim] = set()
    for m in _SCORE_CITE.finditer(sentence):
        before = sentence[:m.start()]
        if _SCORE_SKIP_BEFORE.search(before) or _cites_a_peers_score(before, ticker):
            continue
        claims.add(Claim("rank_sum", "combined", float(m.group(1))))
    return claims


# --------------------------------------------------------------------------- #
# ABSOLUTE claims — fresh extraction; the checker has nothing equivalent
# --------------------------------------------------------------------------- #
_ABS_SUBJECTS: tuple[tuple[str, str], ...] = (
    (r"expense[-\s]?ratio|\bfees?\b", "expense_ratio"),
    (r"fund[-\s]?size|\bAUM\b|net assets|total assets", "fund_size"),
    (r"distribution[-\s]?yield|dividend[-\s]?yield|\byields?\b", "distribution_yield"),
)
_PERCENT_NUM = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_CURRENCY_MULT = {"tn": 1e12, "trillion": 1e12, "bn": 1e9, "billion": 1e9,
                  "mn": 1e6, "million": 1e6, "k": 1e3, "thousand": 1e3}
_CURRENCY_NUM = re.compile(
    r"(?:USD|EUR|GBP|US\$|[€$£])\s*([\d,]+(?:\.\d+)?)\s*"
    r"(tn|trillion|bn|billion|mn|million|k|thousand)?", re.I)


def _multi_abs_subject(clause: str) -> bool:
    """Does ``clause`` name TWO OR MORE distinct absolute-value factors? Then a number in
    it cannot be bound to one of them without guessing WHICH — mirrors
    ``narration_check._multi_factor_subject``'s "ambiguous pairing is never a claim"
    discipline. Without this, a clause like "a 0.07% expense ratio and a USD 43.0bn fund
    size" cross-assigned the fee number to fund_size and vice versa."""
    return sum(1 for pat, _ in _ABS_SUBJECTS if re.search(pat, clause, re.I)) > 1


def _absolute_claims_in_clause(clause: str) -> set[Claim]:
    claims: set[Claim] = set()
    if _multi_abs_subject(clause):
        return claims
    for pat, key in _ABS_SUBJECTS:
        if not re.search(pat, clause, re.I):
            continue
        for m in _PERCENT_NUM.finditer(clause):
            claims.add(Claim("absolute", key, round(float(m.group(1)), 4)))
        for m in _CURRENCY_NUM.finditer(clause):
            mult = _CURRENCY_MULT.get((m.group(2) or "").lower(), 1.0)
            claims.add(Claim("absolute", key,
                             round(float(m.group(1).replace(",", "")) * mult, 2)))
    return claims


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #
def extract_claims_by_sentence(narrative: str, table: dict) -> list[tuple[str, list[Claim]]]:
    """``[(original_sentence, [claims found in it]), ...]``, in narrative order. The
    ORIGINAL sentence (not the stripped/demarked parse) is kept per entry so a caller can
    cross-reference against ``check_narration``'s annotations, which quote the sentence
    verbatim (``narration_check._claim``) — see B2's stamp-matching in analysis.py."""
    if not narrative:
        return []
    n = table.get("N")
    factors = table.get("factors", {}) or {}
    combined_position = table.get("combined_position")
    ticker = table.get("ticker")

    out: list[tuple[str, list[Claim]]] = []
    for sentence in _sentences(narrative):
        parsed = _strip_polarity_descriptors(_demark(sentence))
        found: list[Claim] = []
        if n and n >= 1:
            for clause in re.split(r"[,;]", parsed):
                found.extend(_rank_claims_in_clause(clause, n, factors, combined_position,
                                                    ticker))
            found.extend(_rank_sum_claims(parsed, ticker))
        for clause in re.split(r"[,;]", parsed):
            found.extend(_absolute_claims_in_clause(clause))
        out.append((sentence, found))
    return out


def extract_claims(narrative: str, table: dict) -> list[Claim]:
    """All claims in ``narrative``, given the SAME ``table`` shape
    ``check_narration``/``checker.true_table`` use: ``{"N", "combined_position", "factors",
    "ticker", "score"}``. Returns a flat list (not a set) — duplicate identical claims
    across sentences are preserved as repeats, which is itself informative (a claim
    repeated more insistently); callers that want a set can wrap the result."""
    out: list[Claim] = []
    for _sentence, claims in extract_claims_by_sentence(narrative, table):
        out.extend(claims)
    return out


# --------------------------------------------------------------------------- #
# B1-specific: ANY rank-shaped or absolute-value-shaped mention, WITHOUT requiring
# subject resolution. In the ablation experiment the pack is EMPTY, so `_subject()` can
# never resolve (no factors, no combined_position — see fixtures.ablated_ticker), which
# would make `extract_claims` silently MISS exactly the invented claims B1 needs to catch.
# Deliberately over-inclusive (a human reads the flagged list — see the write-up's
# limitations section).
# --------------------------------------------------------------------------- #
_WORD_ORDINAL_ANY = re.compile(
    r"\b(?:best[-\s]in[-\s]cohort|second[-\s](?:best|worst)|strongest|weakest|best|worst)\b",
    re.I)


def find_quantitative_mentions(narrative: str) -> list[dict]:
    """Every rank-shaped or absolute-value-shaped token in ``narrative`` — digit ordinals,
    explicit rank/rank-sum citations, unbound word-superlatives, percent numbers, currency
    numbers — with NO subject-binding requirement. Used ONLY for Experiment B1."""
    if not narrative:
        return []
    out: list[dict] = []
    for sentence in _sentences(narrative):
        parsed = _strip_polarity_descriptors(_demark(sentence))
        for m in _NUM_ORDINAL.finditer(parsed):
            out.append({"kind": "digit_ordinal", "text": m.group(0), "sentence": sentence})
        for m in _RANK_CITE.finditer(parsed):
            out.append({"kind": "cited_rank", "text": m.group(0), "sentence": sentence})
        for m in _SCORE_CITE.finditer(parsed):
            out.append({"kind": "rank_sum", "text": m.group(0), "sentence": sentence})
        for m in _WORD_ORDINAL_ANY.finditer(parsed):
            out.append({"kind": "word_ordinal", "text": m.group(0), "sentence": sentence})
        for m in _PERCENT_NUM.finditer(parsed):
            out.append({"kind": "percent", "text": m.group(0), "sentence": sentence})
        for m in _CURRENCY_NUM.finditer(parsed):
            out.append({"kind": "currency", "text": m.group(0), "sentence": sentence})
    return out


# --------------------------------------------------------------------------- #
# Temporal/trend language — a lightweight, HUMAN-reviewed secondary signal (not a
# mechanical PASS/FAIL determinant on its own; see the write-up's limitations section)
# --------------------------------------------------------------------------- #
_TREND_LANGUAGE = re.compile(
    r"\b(?:has been (?:rising|falling|increasing|decreasing|climbing|declining)"
    r"|have been (?:rising|falling|increasing|decreasing)"
    r"|trending (?:up|down|higher|lower)"
    r"|historically|over the (?:past|last) \w+ (?:years?|months?|quarters?)"
    r"|used to be|has grown|has shrunk|has climbed|has dropped)\b", re.I)


def flag_temporal_claims(narrative: str) -> list[str]:
    """Sentences asserting a TREND ("fees have been rising", "historically low") — the
    evidence pack carries only POINT-IN-TIME values, no time series, so any such assertion
    is ungrounded by construction (no field it could be citing). Regex, not NLP — not
    exhaustive; a human reads the flagged sentences rather than trusting a bare count."""
    if not narrative:
        return []
    return [s for s in _sentences(narrative) if _TREND_LANGUAGE.search(s)]


# --------------------------------------------------------------------------- #
# Comparison — tolerant, not exact-float, matching
# --------------------------------------------------------------------------- #
def compare_claims(claims_a: list[Claim], claims_b: list[Claim], *,
                   rel_tol: float = 0.02, abs_tol: float = 1e-6) -> dict:
    """Groups claims by (kind, subject) and matches VALUES within tolerance — an LLM
    restating "42.99bn" as "€43.0bn" is the SAME claim, not a new one. Returns
    ``{"matched": [(a, b), ...], "value_changed": [(a, b), ...], "only_in_a": [...],
    "only_in_b": [...]}`` — every claim in both inputs accounted for exactly once."""
    remaining_b = list(claims_b)
    matched: list[tuple[Claim, Claim]] = []
    value_changed: list[tuple[Claim, Claim]] = []
    only_in_a: list[Claim] = []

    def _close(x: float, y: float) -> bool:
        return abs(x - y) <= max(abs_tol, rel_tol * max(abs(x), abs(y), 1e-12))

    for ca in claims_a:
        same_key = [cb for cb in remaining_b if cb.kind == ca.kind and cb.subject == ca.subject]
        exact = next((cb for cb in same_key if _close(ca.value, cb.value)), None)
        if exact is not None:
            matched.append((ca, exact))
            remaining_b.remove(exact)
        elif same_key:
            best = same_key[0]
            value_changed.append((ca, best))
            remaining_b.remove(best)
        else:
            only_in_a.append(ca)

    return {"matched": matched, "value_changed": value_changed,
            "only_in_a": only_in_a, "only_in_b": remaining_b}
