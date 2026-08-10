"""Deterministic rank-semantics post-check on the narrator's output (ITEM 4).

The narrator (an LLM) occasionally INVERTS or misstates rank ordinals while every number
it cites is correct — e.g. calls rank 2 "second-worst" (it's second-BEST), or claims a
combined rank-sum is "the best in the cohort" when a lower one exists. This module parses
ordinal superlative claims out of the generated narrative and verifies them against the
authoritative rank table. On a contradiction it APPENDS a machine annotation to the
narrative — it never silently rewrites the model's prose. The rank table is authoritative.

Rank convention: **rank 1 = best** on every factor; **lower combined rank-sum = better**.

A second class of contradiction is checked when the run hands over a **boundary tie**
(VERDICT-TIE-1): two ranked names shared a combined rank-sum with the verdict boundary
between them, so the deterministic alphabetical tie-break — not a score difference — decided
which side each fell on. The annotated table says TIED; prose that ORDERS the pair ("VWCE.DE
is decisively ranked below EUNL.DE") therefore contradicts it in either direction and is
annotated. Value comparisons between the same two names ("its fee is lower than EUNL.DE's")
are NOT rank claims and are left alone — see ``_tie_order_check``.

Two parser disciplines the check must honour (NARR-CHK-1), both learned from false
positives on real garp_v2 narration:
- **Decimals are atomic.** Sentence splitting must not break `digits.digits` — a claim
  like "CAGR of 31.4%" must never be truncated to "CAGR of 31" (which then reads as a bare
  ordinal/number and mis-parses).
- **Ordinals bind to the factor they NAME, not to a column position.** A sentence like
  "1st on revenue_growth, 2nd on roic, 4th on momentum_12m" states each ordinal against a
  named factor, in any order; each is validated against THAT factor's rank in the table.
  This holds for WORD superlatives too (NARR-CHK-4): "fund_size is the best, expense_ratio
  is second-best, momentum_12m is last" checks each superlative against the factor its OWN
  clause names — never pairing "best" with a factor two clauses away.

A superlative is only a checkable claim when it is genuinely predicated of the narrated
name's rank. These are NOT claims and are left alone (NARR-CHK-1/2/4):
- **Hedged** — "near-best", "almost worst" (approximations).
- **Negated** — "not best", "never worst", "not the best" (a rank-2 name is honestly
  "Competitive, Not Best"; the negation disclaims the rank rather than asserting it).
- **Theoretical bounds / cohort arithmetic** — "worst possible = 48", "best case" (a
  hypothetical, not the name).
- **Generic/hypothetical subjects** — "the best-ranked NAME in the cohort is not insulated
  …" describes a role, not the narrated name (so the combined subject requires the explicit
  "combined"/"rank-sum" metric word, not a bare "in the cohort").
- **Spelled relative ordinals** — "third-best" is rank 3, never the bare "best" (rank 1).
- **Value superlatives with no rank citation** — "exceptional momentum" praises the VALUE,
  not the rank; that word class only becomes a rank claim beside an explicit cohort
  citation (see ``_CITED_ONLY_ORDINALS``).
- **Factor-polarity descriptors** — "(low best)", "(high = best)" (NARR-CHK-FP-1) — the
  pipeline's own direction label, describing which way a factor is favourable, not a claim
  about this name's rank; stripped whole before parsing (see ``_POLARITY_DESCRIPTOR``).

A superlative also carries a POLARITY (NARR-CHK-5). "Strongest" asserts rank 1 only when it
modifies a POSITIVE thing; when it modifies a NEGATIVE one ("the strongest negative signal",
"the strongest drag") it asserts the WORST rank — the mirror position (``n + 1 - p``). The live
VUSA line "12-Month Momentum (rank 5/5): … the ranker's strongest negative signal" is TRUE and
must pass, while "rank 5/5 … the strongest in the cohort" stays a flagged inversion; the only
difference is the noun the superlative modifies. A BAD-direction superlative on a negative noun
("its worst headwind") is idiomatically the most severe one rather than the mildest, so it is
ambiguous and dropped instead of mirrored — the check never guesses a direction.

A superlative must also BIND to the subject it is checked against (NARR-CHK-5). The live EUNL
line "its worst-in-cohort cost rank and middling momentum rank produce a near-bottom combined
ranking" is true in every clause, yet was flagged: "worst" modifies the COST rank, but the only
factor phrase in the table vocabulary was "momentum" (rank 3), so the check paired words from
two different noun phrases. Two guards fix that on the subject-vs-table path, both instances of
"ambiguous pairing is never a contradiction" (the discipline already applied to >1 citation):
a COORDINATING CONJUNCTION between the superlative and the subject phrase breaks the binding
(`_binds`), and a clause naming TWO OR MORE factors is skipped outright.

Besides ordinal claims, the check verifies the cited combined rank-SUM VALUE against the
table's authoritative score when one is supplied (NARR-CHK-5): "combined rank-sum 6" when the
table says 6.5 is a numeric hallucination, and so is "rank-sum of 10" against 9.5 — the same
class, so both must flag (an integer prose value never "rounds to" the authoritative
half-point). Bounded so it cannot invent one: the number must sit inside the cohort's
best/worst rank-sum bounds, theoretical-bound arithmetic ("worst possible = 48") is skipped,
and a PEER's rank-sum ("NVO's rank-sum of 8") is skipped — only this name's score is checked.

Two claims in one sentence are TWO claims (NARR-CHK-FP-2). The narrator's closing synthesis
enumerates cautions — "(1) … (2) … (3) …" — and an enumeration marker is a claim boundary
exactly as a comma is: read as one blob, a superlative or ordinal from item (1) pairs with a
subject from item (2) and stamps prose in which every item is true (live on the 2026-08-10
GOOGL narration, whose "(1) … places it 4th of 21 (2) momentum_12m rank 2 out of 21 …" bound
"4th" — the COMBINED position — to the momentum rank). Markers are split BEFORE the
comma/semicolon split, so per-claim checking is the rule everywhere (see ``_clauses``).

A claim about ANOTHER name is checked against THAT name's row (NARR-CHK-FP-2). The same
GOOGL synthesis compared itself with "the identically scored MSFT" — true of MSFT's row,
false of GOOGL's, so it was stamped. When the caller supplies the cohort's other rows
(``table['peers']``), a clause that names exactly one PEER and not the narrated name is
resolved against that peer's factors/position/score, and a cross-name VERDICT claim ("MSFT
received HOLD") is checked against the peer's actual verdict. Ambiguous attribution (two
peers, or the narrated name AND a peer in one clause) is SKIPPED — never checked against the
wrong row. With no ``peers`` supplied the behavior is exactly as before: everything resolves
against the narrated name.

Markdown is stripped before parsing (NARR-CHK-4): the narrator emphasises heavily, and an
emphasis marker sitting between a hedge/negation and its superlative ("`**not** best`",
"`near-*best*`") otherwise defeats the adjacency the skip patterns rely on — a live latent
false-positive generator. Only `*` and backticks are stripped; `_` is load-bearing in the
factor keys the narrator quotes (`fund_size`, `momentum_12m`). Annotations always quote the
ORIGINAL prose, markdown included — the check never rewrites what the model wrote.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

# Word superlative tokens -> the rank position they imply, given cohort size N.
# Ordered SPECIFIC-FIRST so "second-worst" is matched before "worst", etc.
# NB: only UNAMBIGUOUS superlatives. "top"/"bottom" are deliberately omitted — phrases
# like "third from the bottom" are correct and must NOT be flagged; the observed error
# class (best/worst/second-*/strongest/weakest) is fully covered without them. "last"/
# "first" are ALSO omitted — they are temporally overloaded ("last year", "first quarter")
# and would mis-fire. Spelled-out ordinals ("third") are likewise NOT parsed here — only the
# digit ordinals below. `strongest`/`weakest` are true superlative synonyms of best/worst
# (NARR-CHK-4: a genuine "rank 5/5 … strongest" inversion must stay catchable).
_ORDINALS: tuple[tuple[str, Callable[[int], int]], ...] = (
    (r"second[-\s]worst", lambda n: n - 1),
    (r"second[-\s]best", lambda n: 2),
    (r"best[-\s]in[-\s]cohort", lambda n: 1),
    (r"strongest", lambda n: 1),
    (r"weakest", lambda n: n),
    (r"worst", lambda n: n),
    (r"best", lambda n: 1),
)

# Superlatives that are only a RANK claim when the clause also carries an explicit cohort
# citation (NARR-CHK-4, the SXR8 "momentum 5/5 is exceptional" inversion). Unlike
# best/worst/strongest, these words are routinely predicated of a VALUE rather than a rank
# ("exceptional revenue growth of 40%" says nothing about a rank and must never be flagged),
# so they are inert on the subject-vs-table path and live only on the citation-vs-ordinal
# path — where the prose is demonstrably talking about the cited rank.
_CITED_ONLY_ORDINALS: tuple[tuple[str, Callable[[int], int]], ...] = (
    (r"exceptional", lambda n: 1),
    (r"outstanding", lambda n: 1),
    (r"unmatched", lambda n: 1),
)

# Markdown emphasis the narrator wraps prose in. Stripped before parsing so a marker between
# a hedge/negation and its superlative cannot defeat the adjacency `_SKIP_BEFORE` requires
# ("`near-*best*`", "`**not** best`"). `_` is NOT stripped — it is load-bearing in the factor
# keys the narrator quotes verbatim (`fund_size`, `momentum_12m`).
_MD_EMPHASIS = re.compile(r"[*`]+")

# A factor-POLARITY descriptor — "(low best)", "(high = best)", "(lower is better)" — is the
# pipeline's OWN direction label (factors.py's "Expense ratio (low best)", "Price / book (low
# best)"), quoted verbatim by the narrator. It is a DESCRIPTION of which direction is
# favourable, never a claim about this name's rank (NARR-CHK-FP-1). Left in, the bare
# "best"/"better"/"worst" token inside it binds to the factor named just before the
# parenthetical and reads as a rank-1 claim — the live false positive on "fees (low = best)"
# (Gernot's EUDF run) and "expense ratio (low best)" (Karin's VUSA run). Stripped whole,
# before any ordinal search, so no token inside it can ever be matched.
_POLARITY_DESCRIPTOR = re.compile(
    r"\(\s*(?:low|high|lower|higher)\s*(?:=|is)?\s*(?:best|better|worst)\s*\)", re.I)

# A superlative is NOT a checkable ordinal claim when the text just BEFORE it is:
#   - a HEDGE ("near-best" ≈ rank 2, not rank 1),
#   - a NEGATION ("not best" / "never worst" DISCLAIMS the rank — "Competitive, Not Best" is
#     true of a rank-2 name; NARR-CHK-4 class), or
#   - a spelled RELATIVE-ORDINAL prefix ("third-best", "fourth-worst" — a specific rank,
#     NOT the bare superlative; NARR-CHK-2 class 3 flagged "third-best" as "best"=rank 1).
# Matched against the text ENDING just before the ordinal token. ("second-*" is handled by
# its own specific pattern above, so it is intentionally absent here.) An optional article is
# tolerated between the prefix and the token — "not THE best" disclaims rank 1 exactly as
# "not best" does (NARR-CHK-4); markdown emphasis is already stripped by then.
_SKIP_BEFORE = re.compile(
    r"\b(?:near|nearly|almost|approx\w*|roughly|about|~|not|never"
    r"|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)"
    r"(?:[-\s]+the)?[-\s]?$", re.I)

# A superlative is a THEORETICAL BOUND, not a claim about the name, when it is immediately
# FOLLOWED by "possible"/"case"/… ("worst possible = 48" is cohort arithmetic, not a claim
# that the name is worst; NARR-CHK-2 class 1). Matched against the text AFTER the token.
_SKIP_AFTER = re.compile(r"^[-\s]?(?:possible|case|conceivable|imaginable)\b", re.I)

# A superlative that modifies a NEGATIVE noun asserts the MIRROR position (NARR-CHK-5): the
# "strongest negative signal" / "biggest weakness" / "worst drag" of a name IS its worst rank,
# so rank 5/5 + "strongest negative signal" agrees with the table. Matched against the text
# AFTER the token (an article/intensifier may intervene: "the strongest single headwind" —
# markdown emphasis is already stripped). Deliberately a NOUN-polarity list, not a sentiment
# heuristic: only words that name a bad thing the rank can BE flip the direction.
_POLARITY_INVERTING = re.compile(
    r"^[-\s]?(?:(?:the|a|an|its|his|her|their|our|any|single|such)[-\s]+){0,3}"
    r"(?:negative|negatives|bearish|downside|weakness|weaknesses|drag|drags|headwind"
    r"|headwinds|detractor|detractors|drawback|drawbacks|liability|liabilities"
    r"|concern|concerns|deficit|penalty|blemish)\b", re.I)

# Only a GOOD-direction superlative mirrors cleanly on a negative noun: "the strongest
# negative signal" / "the best of a weak set" unambiguously means the WORST rank. The
# bad-direction tokens do NOT: "its worst headwind" idiomatically means the most severe one
# (already the worst rank), not the mildest — so a bad-direction token on a negative noun is
# AMBIGUOUS and the claim is dropped rather than guessed (never invent a contradiction).
# Keyed by the pattern string, so the token tables above keep their two-column shape.
_MIRRORABLE = frozenset({r"second[-\s]best", r"best[-\s]in[-\s]cohort", r"strongest",
                         r"best", r"exceptional", r"outstanding", r"unmatched"})

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
    (r"expense[-\s_]?ratio", ("expense_ratio",)),
    (r"fund[-\s_]?size", ("fund_size",)),
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

# A COORDINATING CONJUNCTION separates two noun phrases, so a superlative on one side of it is
# NOT predicated of a subject on the other (NARR-CHK-5, `_binds`).
_CONJUNCTION = re.compile(
    r"\b(?:and|or|but|while|whereas|versus|vs|though|although|yet|plus|alongside)\b", re.I)

# "rank of 2 out of 10" / "rank 21 out of 23" / "rank 2/7" / "rank 5". NOT "rank-sum of 12"
# (a value, not a position) — the hyphen blocks the whitespace this requires after "rank".
_RANK_CITE = re.compile(
    r"\brank(?:\s+of)?\s+(\d+)(?:\s*(?:out\s+of|/)\s*(\d+))?", re.I)

# A BARE cohort citation — "momentum 5/5", with the word "rank" elided (the SXR8 phrasing,
# NARR-CHK-4). Deliberately narrow, because `d/d` is also dates, fractions and star ratings:
# it counts only when the denominator EQUALS the cohort size N, the numerator is a valid
# position within it, and the clause resolves a rank subject (a named factor / the combined
# position). Used ONLY as a fallback when `_RANK_CITE` finds nothing in the clause. The
# lookarounds keep it off `2026/07/21` and `3/4/5`.
_BARE_CITE = re.compile(r"(?<![\d./])(\d+)\s*/\s*(\d+)(?![\d./])")

# The combined rank-SUM VALUE cited in prose — "combined rank-sum 6", "rank-sum of 10",
# "overall score 9.5" (NARR-CHK-5). Decimal-safe on BOTH sides: the prose number may carry a
# decimal, and an INTEGER prose value against an averaged-tie table value (6.5) is a mismatch,
# never a rounding — that asymmetry is why "10 vs 9.5" was caught and "6 vs 6.5" was not. Only
# the explicit combined-metric words bind: a bare "score" is used for too many other things
# ("sentiment score 0.4"), and "combined rank" would collide with the ordinal POSITION.
_SCORE_CITE = re.compile(
    r"\b(?:rank[-\s]sum|(?:combined|overall|total)\s+score)\b"
    r"[\s:=(]*(?:of|is|at|was|to)?[\s:=(]*(\d+(?:\.\d+)?)", re.I)

# A cited rank-sum is NOT a claim about this name's score when what precedes it makes it
# cohort arithmetic or a summary statistic — "worst possible rank-sum = 48" (the NARR-CHK-2
# class 1 aside), "the best combined rank-sum of 9" (a superlative claim the word path owns),
# "the average rank-sum is 9". Matched against the text ENDING just before the metric word.
_SCORE_SKIP_BEFORE = re.compile(
    r"\b(?:best|worst|lowest|highest|min|minimum|max|maximum|theoretical|possible"
    r"|average|mean|median|between|from|range|as\s+low\s+as|as\s+high\s+as)"
    r"(?:[-\s]+(?:possible|conceivable|case|combined|overall|total))*[-\s]*$", re.I)

# A PEER's rank-sum ("NVO's rank-sum of 8 beat ASML's") is not this name's score, so a
# possessive ticker other than the narrated one immediately before the metric word disarms the
# value check. Uppercase words that are never a ticker are exempt (the narrator writes "the
# ETF's combined rank-sum" about the name it IS narrating).
_PEER_POSSESSIVE = re.compile(r"\b([A-Z][A-Z0-9]{1,5}(?:\.[A-Z]{1,2})?)'s\b")
_NOT_A_TICKER = frozenset(
    ("ETF", "ETFS", "FUND", "NAV", "TER", "AUM", "USD", "EUR", "GBP", "CHF", "US", "UK", "EU",
     "ESG", "UCITS", "KIID", "AI", "IT", "ROIC", "PEG", "EPS", "CAGR", "BUY", "HOLD", "SELL"))


# VERDICT-TIE-1 — a BOUNDARY TIE cannot be ordered. When two ranked names share a combined
# rank-sum and the verdict boundary fell between them, the alphabetical tie-break (not a score
# difference) decided which side each got: the annotated table says TIED, so prose ordering the
# pair ("decisively ranked below VWCE.DE") contradicts it in EITHER direction. Deliberately
# narrow, three conditions in the SAME clause — a rank/score word, an un-hedged comparative,
# and the tie partner as that comparative's TARGET — so a legitimate VALUE comparison ("its
# fee is lower than VWCE.DE's") is never flagged, nor is a comparison aimed at a THIRD name.
_TIE_RANK_WORD = re.compile(r"rank\w*|scor\w*|position\w*|plac(?:e|ed|es|ing)\b|combined",
                            re.I)
_TIE_ORDER = re.compile(
    r"\b(?:below|beneath|behind|ahead\s+of|above|outrank\w*|outscor\w*|trail\w*|lag\w*"
    r"|(?:better|worse|weaker|stronger|higher|lower)\s+than)\b", re.I)


# NARR-CHK-FP-2 — an ENUMERATION MARKER is a claim boundary. "(1)", "(2)", "(iii)", "(a)"
# and bullet glyphs each open a NEW claim, so a superlative/ordinal in one item can never
# bind to a subject in another (the GOOGL synthesis false positive). Deliberately narrow:
# only a SINGLE digit/roman/letter inside parentheses, so the narrator's real parentheticals
# ("(lower is better)", "(rank 2/7)", "(low best)") are untouched, and NOT bare "1." — that
# would collide with decimals and sentence ends.
_ENUM_MARKER = re.compile(r"\(\s*(?:\d{1,2}|[ivx]{1,4}|[a-h])\s*\)|[•‣▪]", re.I)

# A cross-name VERDICT claim (NARR-CHK-FP-2): "MSFT received HOLD". Verdicts are checked
# UPPERCASE-only — the tables and prose render them upper (``format_verdict_cell``), and a
# lowercase "hold"/"buy" is an ordinary English word ("hold the position"), so requiring the
# upper form keeps the check off prose that isn't quoting a verdict.
_VERDICT_TOKEN = re.compile(r"\b(BUY|HOLD|SELL)\b")
# The claim must ASSERT the verdict (a verb attributing it), and must not be hypothetical or
# a reference to the tier/boundary itself ("would be a BUY", "the BUY tier", "the BUY cut").
_VERDICT_ASSERTED = re.compile(
    r"\b(?:received|receives|got|gets|was|is|are|rated|carries|carried|earned|earns"
    r"|issued|assigned|scored|lands|landed|sits|sat)\b", re.I)
_VERDICT_HYPOTHETICAL = re.compile(
    r"\b(?:would|could|should|might|may|if|were|unless|tier|tiers|threshold|boundary"
    r"|cut|band|bands|either|any|not|never)\b", re.I)


def _demark(text: str) -> str:
    """``text`` with markdown emphasis (`*`, backticks) removed — see the module docstring.
    Parsing only; annotations quote the original prose."""
    return _MD_EMPHASIS.sub("", text)


def _strip_polarity_descriptors(text: str) -> str:
    """``text`` with factor-polarity descriptors ("(low best)", "(high = best)") removed
    (NARR-CHK-FP-1) — parsing only, see `_POLARITY_DESCRIPTOR`. Annotations still quote the
    original prose."""
    return _POLARITY_DESCRIPTOR.sub("", text)


def _mirror(fn: Callable[[int], int]) -> Callable[[int], int]:
    """``fn``'s position reflected about the cohort — rank 1 <-> rank n (NARR-CHK-5). The
    position a GOOD-direction superlative asserts when it modifies a NEGATIVE noun: the
    "strongest negative signal" / "biggest drag" of a name is its WORST rank."""
    return lambda n: n + 1 - fn(n)


def _word_ordinal(text: str,
                  include_cited_only: bool = False
                  ) -> Optional[tuple[int, int, Callable[[int], int]]]:
    """The earliest UN-HEDGED word-superlative token in ``text`` (specific tokens win a
    tie) as ``(start, end, position_fn)``. A superlative that is hedged ("near-best"), a
    spelled relative ordinal ("third-best"), or a theoretical bound ("worst possible") is
    skipped; a GOOD-direction one that modifies a NEGATIVE noun ("strongest negative signal")
    keeps its claim but MIRRORS the position it asserts, and a BAD-direction one there ("worst
    headwind") is dropped as directionally ambiguous (NARR-CHK-5).
    ``include_cited_only`` admits the value-superlative class ("exceptional") — the caller
    sets it only when the clause carries exactly one rank citation to bind it to."""
    best: Optional[tuple[int, int, Callable[[int], int]]] = None
    tokens = _ORDINALS + (_CITED_ONLY_ORDINALS if include_cited_only else ())
    for pat, fn in tokens:
        m = re.search(pat, text, re.I)
        if (not m or _SKIP_BEFORE.search(text[:m.start()])
                or _SKIP_AFTER.match(text[m.end():])):
            continue
        if _POLARITY_INVERTING.match(text[m.end():]):
            if pat not in _MIRRORABLE:
                continue                     # "worst headwind" — direction ambiguous
            fn = _mirror(fn)
        if best is None or m.start() < best[0]:
            best = (m.start(), m.end(), fn)
    return best


def _names_ticker(text: str, ticker: Optional[str]) -> bool:
    """Does ``text`` name the narrated ticker (word-boundary, case-sensitive)? A rank claim
    about THIS name generally names it; a generic role reference ("the best-ranked name")
    does not — the discriminator for NARR-CHK-2 class 2."""
    return bool(ticker) and re.search(rf"\b{re.escape(ticker)}\b", text) is not None


def _subject(text: str, factors: dict, combined_position: Optional[int],
             ticker: Optional[str] = None) -> Optional[tuple[int, int, int]]:
    """The authoritative rank for the text's subject as ``(rank, start, end)`` — the span being
    the subject PHRASE, so a caller can test whether a superlative/ordinal actually binds to it
    (see `_binds`). A factor's rank if a factor phrase is present, else the name's combined
    position if a combined subject is present. The combined position binds on an EXPLICIT
    metric word ("combined"/"rank-sum"), or on a LOOSE phrase ("in the cohort") ONLY when the
    narrated name is named (else it is a generic reference, not a claim about this name). A
    named factor absent from the table -> ``None`` (undeterminable, never guessed)."""
    for pat, keys in _FACTOR_SUBJECTS:
        m = re.search(pat, text, re.I)
        if m:
            for k in keys:
                if k in factors and factors[k] is not None:
                    return int(round(factors[k])), m.start(), m.end()
            return None
    if combined_position is not None:
        m = _COMBINED_METRIC.search(text)
        if m:
            return int(combined_position), m.start(), m.end()
        m = _COMBINED_LOOSE.search(text)
        if m and _names_ticker(text, ticker):
            return int(combined_position), m.start(), m.end()
    return None


def _subject_rank(text: str, factors: dict, combined_position: Optional[int],
                  ticker: Optional[str] = None) -> Optional[int]:
    """`_subject`'s rank alone, for callers that only need to know a subject resolves."""
    s = _subject(text, factors, combined_position, ticker)
    return None if s is None else s[0]


def _multi_factor_subject(text: str) -> bool:
    """Does ``text`` name TWO OR MORE distinct factors? Then a single superlative/ordinal in it
    cannot be bound to one of them (NARR-CHK-5). Ambiguous pairing is never a contradiction, so
    the caller skips the clause — the same discipline as >1 rank citation."""
    return sum(1 for pat, _ in _FACTOR_SUBJECTS if re.search(pat, text, re.I)) > 1


def _binds(text: str, claim: tuple[int, int], subject_span: tuple[int, int]) -> bool:
    """Is the superlative/ordinal at ``claim`` predicated of the subject phrase at
    ``subject_span``, i.e. are they in the SAME noun phrase? A COORDINATING CONJUNCTION between
    them means they are not (NARR-CHK-5, the EUNL false positive: "its worst-in-cohort cost rank
    AND middling momentum rank …" — "worst" modifies the cost rank, and only the momentum phrase
    was in the table vocabulary, so the flag paired words from two different phrases). Adjacent
    in either order still binds ("momentum_12m is the best", "the best-in-cohort low
    volatility")."""
    lo, hi = sorted((claim, subject_span), key=lambda s: s[0])
    return not _CONJUNCTION.search(text[lo[1]:hi[0]])


def _clause_cites(clause: str, n: int, factors: dict, combined_position: Optional[int],
                  ticker: Optional[str]) -> list[tuple[int, int]]:
    """The rank citations in ``clause`` as ``(position, cohort_size)`` pairs. An explicit
    "rank R out of M" wins; failing that, a BARE cohort citation ("momentum 5/5") counts only
    under `_BARE_CITE`'s narrow gate — denominator == N, numerator a valid position, and a
    resolvable rank subject in the clause — so fractions and dates never read as ranks."""
    cites = [(int(r), int(m) if m else n) for r, m in _RANK_CITE.findall(clause)]
    if cites:
        return cites
    if _subject_rank(clause, factors, combined_position, ticker) is None:
        return []
    return [(int(b.group(1)), int(b.group(2))) for b in _BARE_CITE.finditer(clause)
            if int(b.group(2)) == n and 1 <= int(b.group(1)) <= n]


def _clauses(text: str) -> list[str]:
    """``text`` split into single CLAIMS: enumeration items first (NARR-CHK-FP-2), then the
    comma/semicolon clauses within each. One superlative/ordinal per claim, checked against
    the subject its OWN claim names — never a subject from a neighbouring enumeration item."""
    out: list[str] = []
    for item in _ENUM_MARKER.split(text):
        out.extend(re.split(r"[,;]", item))
    return [c for c in out if c and c.strip()]


def _items(text: str) -> list[str]:
    """``text`` split into enumeration ITEMS only (commas kept). A cross-name claim spans
    clauses inside one item — "the identically scored MSFT, which received HOLD" — so the
    verdict check reads items, not comma-clauses."""
    return [i for i in _ENUM_MARKER.split(text) if i and i.strip()]


def _own_row(ticker: Optional[str], factors: dict, combined_position: Optional[int],
             score: Optional[float] = None) -> dict:
    """The narrated name's authoritative row, in the shape the per-clause checks read."""
    return {"ticker": ticker, "factors": factors or {},
            "combined_position": combined_position, "score": score, "verdict": ""}


def _subject_row(clause: str, own: dict, peers: Optional[dict]) -> Optional[dict]:
    """Whose row this clause's claim must be checked against (NARR-CHK-FP-2).

    The narrated name's row by default. A clause that names exactly ONE peer from
    ``peers`` — and not the narrated name — is a CROSS-NAME claim and resolves to that
    peer's row (the live GOOGL synthesis compared itself with "the identically scored
    MSFT": true of MSFT's row, false of GOOGL's). ``None`` when attribution is ambiguous
    (two peers, or the narrated name AND a peer in one clause): the check never guesses
    whose claim it is, and a missed flag beats a wrong one. With no ``peers`` supplied the
    narrated row is always returned — behavior identical to before."""
    if not peers:
        return own
    ticker = own.get("ticker")
    named = [t for t in peers if t and t != ticker and _names_ticker(clause, t)]
    if not named:
        return own
    if len(named) > 1 or _names_ticker(clause, ticker):
        return None
    row = peers.get(named[0]) or {}
    return {"ticker": named[0], "factors": row.get("factors") or {},
            "combined_position": row.get("combined_position"),
            "score": row.get("score"), "verdict": row.get("verdict") or ""}


def _word_check(sentence: str, n: int, own: dict,
                peers: Optional[dict] = None) -> bool:
    """Word-superlative claim vs the table (hedge-, negation- and CLAUSE-aware). Each
    comma/semicolon-separated clause states at most one superlative about the subject it
    NAMES — so "fund_size is the best, expense_ratio is second-best, momentum_12m is last"
    validates each ordinal against its own factor, never pairing "best" (about fund_size)
    with a factor named two clauses away (the etf_core_v1 false positive, NARR-CHK-4). This
    mirrors the per-clause binding `_numeric_check` already enforces. Within each clause:
    - **citation vs ordinal**: an explicit ``rank R out of M`` with a superlative must
      agree (rank 2 is not "second-worst").
    - **subject vs table**: a superlative about a named factor / the combined position must
      match that subject's actual rank.
    A clause with no un-hedged/un-negated superlative, more than one citation, more than one
    named factor, or no resolvable subject is left alone — the check never invents a
    contradiction. On the subject path the superlative must also BIND to the subject phrase
    (no coordinating conjunction between them, `_binds`) — NARR-CHK-5. The value-superlative
    class ("exceptional") is admitted on the citation path only. Clauses are per-CLAIM
    (enumeration items split before commas) and resolve against the row of the name they
    name (NARR-CHK-FP-2)."""
    for clause in _clauses(sentence):
        row = _subject_row(clause, own, peers)
        if row is None:                          # ambiguous attribution -> not our claim
            continue
        factors, combined_position = row["factors"], row["combined_position"]
        ticker = row["ticker"]
        cites = _clause_cites(clause, n, factors, combined_position, ticker)
        wo = _word_ordinal(clause, include_cited_only=len(cites) == 1)
        if wo is None:
            continue
        start, end, posfn = wo
        if len(cites) == 1:                      # citation vs ordinal
            r, m = cites[0]
            if r != posfn(m):
                return True
        elif not cites:                          # subject vs table (no explicit rank)
            subj = _subject(clause, factors, combined_position, ticker)
            if (subj is not None and not _multi_factor_subject(clause)
                    and _binds(clause, (start, end), (subj[1], subj[2]))
                    and subj[0] != posfn(n)):
                return True
        # >1 citation in the clause -> pairing ambiguous, skip
    return False


def _numeric_check(sentence: str, own: dict, peers: Optional[dict] = None) -> bool:
    """Digit-ordinal claims bound to their NAMED factor, per clause (defect-b fix). Each
    comma/semicolon-separated clause states at most one ordinal about the factor it names;
    the ordinal is validated against THAT factor's rank — so factors named in any order
    each check against the right rank, never a positional column. A clause with no factor
    (or a factor absent from the table), no ordinal, more than one ordinal, more than one named
    factor, or an ordinal that does not BIND to the subject phrase (NARR-CHK-5) is skipped.
    Enumeration items are separate claims and a cross-name ordinal is checked against the
    NAMED name's row (NARR-CHK-FP-2: "the identically scored MSFT is 5th on roic")."""
    for clause in _clauses(sentence):
        row = _subject_row(clause, own, peers)
        if row is None:
            continue
        ords_ = [m for m in _NUM_ORDINAL.finditer(clause)
                 if not _SKIP_BEFORE.search(clause[:m.start()])]
        if len(ords_) != 1 or _multi_factor_subject(clause):
            continue
        subj = _subject(clause, row["factors"], row["combined_position"], row["ticker"])
        if subj is None or not _binds(clause, ords_[0].span(), (subj[1], subj[2])):
            continue
        if subj[0] != int(ords_[0].group(1)):
            return True
    return False


def _cites_a_peers_score(before: str, ticker: Optional[str]) -> bool:
    """Does the text just before a rank-sum citation attribute it to ANOTHER name? Only the
    last stretch is inspected — the possessive that governs the metric word ("NVO's rank-sum of
    8"), not any ticker mentioned earlier in the sentence."""
    m = _PEER_POSSESSIVE.search(before[-60:])
    return bool(m) and m.group(1) != ticker and m.group(1) not in _NOT_A_TICKER


def _score_check(sentence: str, n: int, own: dict,
                 peers: Optional[dict] = None) -> bool:
    """Does ``sentence`` cite a combined rank-sum VALUE that contradicts the table's
    authoritative score? (NARR-CHK-5 — the SXR8 "combined rank-sum 6" against 6.5 and the
    VUSA "rank-sum of 10" against 9.5 are the SAME class and both flag.) Runs only when the
    table supplies a score, and never invents a claim: cohort arithmetic and superlative
    phrasings are skipped (`_SCORE_SKIP_BEFORE`), a peer's score is skipped, and the cited
    number must lie inside this cohort's rank-sum bounds (best = number of factors, worst =
    factors × N) — outside them it is some other quantity, not this score. Per CLAIM, and a
    cross-name citation is compared with that name's own score (NARR-CHK-FP-2)."""
    if n < 1:
        return False
    for clause in _clauses(sentence):
        row = _subject_row(clause, own, peers)
        if row is None:
            continue
        score, factors = row["score"], row["factors"]
        if score is None or not factors:
            continue
        best, worst = len(factors), len(factors) * n
        for m in _SCORE_CITE.finditer(clause):
            before = clause[:m.start()]
            if (_SCORE_SKIP_BEFORE.search(before)
                    or _cites_a_peers_score(before, row["ticker"])):
                continue
            cited = float(m.group(1))
            if best <= cited <= worst and abs(cited - float(score)) > 1e-9:
                return True
    return False


def _verdict_check(sentence: str, own: dict,
                   peers: Optional[dict] = None) -> Optional[tuple[str, str, str]]:
    """A CROSS-NAME verdict claim that contradicts the table, as ``(peer, claimed,
    actual)`` — or ``None`` (NARR-CHK-FP-2).

    The live GOOGL synthesis leaned on "the identically scored MSFT that received HOLD".
    That is a checkable fact about MSFT, so it is checked against MSFT's row — and, before
    this, was not checkable at all. Read per enumeration ITEM (the peer and the verdict word
    sit in different comma-clauses of one item), and deliberately narrow: exactly one peer
    named, the narrated name NOT named, an UPPERCASE verdict token, an attributing verb, and
    no hypothetical/tier language ("would be a BUY", "the BUY tier") — otherwise skipped.
    The narrated name's OWN verdict is out of scope: it is the report's headline, stated by
    the ranker, not a comparison the writer could get wrong about someone else."""
    if not peers:
        return None
    for item in _items(sentence):
        row = _subject_row(item, own, peers)
        if row is None or row.get("ticker") == own.get("ticker"):
            continue                             # own-name claim -> not this check's job
        actual = (row.get("verdict") or "").strip()
        if not actual:
            continue                             # no authoritative verdict -> nothing to say
        if _VERDICT_HYPOTHETICAL.search(item) or not _VERDICT_ASSERTED.search(item):
            continue
        for m in _VERDICT_TOKEN.finditer(item):
            if m.group(1).lower() != actual.lower():
                return row["ticker"], m.group(1), actual.upper()
    return None


def _tie_order_check(sentence: str, boundary_tie: dict) -> Optional[str]:
    """The tie partner this sentence illegitimately ORDERS the narrated name against, or
    ``None`` (VERDICT-TIE-1). Three conditions must hold in the SAME clause: a rank/score
    word, an un-hedged comparative, and the tie partner as the comparative's TARGET (named
    AFTER it). So "decisively ranked below EUNL.DE" is flagged, while "its fee is lower than
    EUNL.DE's" (no rank word) and "VWCE.DE and EUNL.DE both score lower than SXR8.DE" (the
    target is a third name) are left alone — the check never invents a contradiction. The
    mirrored phrasing ("EUNL.DE ranks above it") is a deliberate false NEGATIVE: the doctrine
    prefers a missed flag to a wrong one."""
    partners = [str(p.get("ticker", "")) for p in (boundary_tie.get("partners") or [])]
    partners = [p for p in partners if p]
    if not partners:
        return None
    for clause in re.split(r"[,;]", sentence):
        if not _TIE_RANK_WORD.search(clause):
            continue
        for m in _TIE_ORDER.finditer(clause):
            if _SKIP_BEFORE.search(clause[:m.start()]):
                continue
            target = clause[m.end():]
            named = next((p for p in partners
                          if re.search(rf"(?<![\w.]){re.escape(p)}(?![\w])", target)), None)
            if named is not None:
                return named
    return None


def _sentences(text: str) -> list[str]:
    # Split on sentence punctuation, but NEVER on a period INSIDE a word: "31.4" stays atomic
    # (decimals-are-atomic) and so does a dotted ticker like "EUNL.DE" (VERDICT-TIE-1 — the
    # tie partner is a European listing; a split ticker can never be matched in the prose).
    # "…high. Next…" still splits: a real sentence end is followed by space/newline/end.
    parts = re.split(r"(?<!\w)\.|\.(?!\w)|[!?\n]+", text)
    return [s.strip() for s in parts if s and s.strip()]


def _claim(sentence: str) -> str:
    return re.sub(r"\s+", " ", sentence).strip()


def _annotation(claim: str) -> str:
    return (f'[⚠ narration check: "{claim}" contradicts rank table — '
            f'table is authoritative]')


def _tie_annotation(claim: str, partner: str, score: str) -> str:
    return (f'[⚠ narration check: "{claim}" orders a TIED pair — tied with {partner} at '
            f'combined rank-sum {score}; the verdict split on the alphabetical tie-break, '
            f'not a score difference — table is authoritative]')


def _verdict_annotation(claim: str, peer: str, claimed: str, actual: str) -> str:
    return (f'[⚠ narration check: "{claim}" misstates {peer}\'s verdict — it says '
            f'{claimed.upper()}, the table says {actual.upper()}; table is authoritative]')


def check_narration(narrative: str, table: dict) -> list[str]:
    """Return machine annotations for ordinal claims that contradict the rank ``table``.

    ``table``: ``{"N": cohort_size, "combined_position": int|None,
    "factors": {factor_key: rank}, "score": float|None, "boundary_tie": {…},
    "peers": {ticker: {"factors": …, "combined_position": …, "score": …, "verdict": …}}}``.
    Each sentence is checked five ways — a word-superlative claim (best/worst/second-*)
    against a citation or its subject, any digit ordinals bound to the factor each names,
    (when ``score`` is supplied) a cited combined rank-SUM value against that authoritative
    score, (when ``peers`` is supplied) a CROSS-NAME verdict claim against that name's actual
    verdict, and (VERDICT-TIE-1) any attempt to ORDER the name against a name it is TIED
    with, when ``boundary_tie`` says the verdict split on the alphabetical tie-break.

    Claims are read PER CLAIM: enumeration markers ("(1) … (2) …") split before commas, and a
    clause naming exactly one peer is resolved against THAT peer's row (NARR-CHK-FP-2 — the
    2026-08-10 GOOGL synthesis was stamped though every claim in it was true). Correct
    statements (in any factor order) pass untouched; ambiguous or hedged sentences are left
    alone — the check never invents a contradiction, and never rewrites the prose. ``peers``
    is optional: with none supplied every claim resolves against the narrated name, exactly
    as before.

    Markdown emphasis is stripped for PARSING only; a flagged claim is quoted verbatim from
    the narrative, emphasis markers included.
    """
    if not narrative:
        return []
    n = table.get("N")
    if not n or n < 1:
        return []
    own = _own_row(table.get("ticker"), table.get("factors", {}) or {},
                   table.get("combined_position"), table.get("score"))
    peers = table.get("peers") or {}
    boundary_tie = table.get("boundary_tie") or {}

    flags: list[str] = []
    seen: set[str] = set()
    for sentence in _sentences(narrative):
        parsed = _strip_polarity_descriptors(_demark(sentence))
        if (_word_check(parsed, n, own, peers)
                or _numeric_check(parsed, own, peers)
                or _score_check(parsed, n, own, peers)):
            claim = _claim(sentence)
            if claim not in seen:
                seen.add(claim)
                flags.append(_annotation(claim))
            continue                      # at most ONE annotation per sentence
        cross = _verdict_check(parsed, own, peers)
        if cross is not None:
            claim = _claim(sentence)
            if claim not in seen:
                seen.add(claim)
                flags.append(_verdict_annotation(claim, *cross))
            continue
        partner = _tie_order_check(parsed, boundary_tie)
        if partner is not None:
            claim = _claim(sentence)
            if claim not in seen:
                seen.add(claim)
                flags.append(_tie_annotation(
                    claim, partner, str(boundary_tie.get("score_display", ""))))
    return flags
