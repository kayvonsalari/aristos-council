"""NEWS-SUBJECT-1 (Batch 19A A11) — keep a headline only if the company is its SUBJECT.

JPM's "Recent news" block held five headlines, none about JPMorgan: a Synopsys buyback, a
Givaudan analyst note ("J.P. Morgan adds Givaudan to Positive Catalyst Watch"), an
arranger credit, a market column and an ETF piece. A news feed matches a company on any
mention, and a bank is named as analyst or arranger in a great many stories about other
firms.

The test is deterministic and string-only (no model): a headline is ABOUT the company when
it names the company (its name, a short form of it, or its ticker as an upper-case word) AND
that mention is not in an analyst/arranger role ("<name> adds/upgrades/raises ...",
"upgraded by <name>", "<name>-led", "bookrunner"). It errs toward dropping: a missing
headline is stated as missing, a stranger's headline presented as this company's is a wrong
statement.
"""
from __future__ import annotations

import re
from typing import Iterable, Sequence

NO_SUBJECT_NEWS_NOTE = "no headlines about this company in the window"
MIN_SUBJECT_HEADLINES = 2

_WORD = re.compile(r"[A-Za-z0-9]+")
_LEGAL_SUFFIXES = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc",
    "ag", "sa", "nv", "se", "spa", "llc", "lp", "holdings", "holding", "group", "the", "and",
    "ordinary", "shares", "common", "stock", "class", "adr", "ads"})
# A mention followed by one of these is the actor in someone else's story. The STRONG verbs
# are rating-desk words and decide on their own; the WEAK ones ("Tesla raises prices", "Apple
# adds a feature") are ordinary company actions and count only beside a rating-note word.
_STRONG_ACTOR_VERBS = frozenset({
    "upgrades", "downgrades", "initiates", "reiterates", "maintains", "reaffirms", "resumes",
    "assigns", "arranges", "underwrites", "upgraded", "downgraded", "initiated",
    "reiterated", "maintained", "reaffirmed", "arranged", "underwrote"})
_WEAK_ACTOR_VERBS = frozenset({
    "adds", "raises", "lowers", "cuts", "lifts", "sets", "keeps", "puts", "places", "boosts",
    "hikes", "trims", "names", "raised", "lowered", "cut", "lifted", "set", "kept", "put",
    "placed", "boosted", "hiked", "trimmed", "named", "added"})
_NOTE_WORDS = frozenset({"target", "targets", "rating", "ratings", "watch", "outperform",
                         "overweight", "underweight", "neutral", "buy", "sell", "hold",
                         "catalyst", "coverage", "estimates", "estimate", "pick", "picks"})
_ACTOR_VERBS = _STRONG_ACTOR_VERBS | _WEAK_ACTOR_VERBS
# "upgraded BY <name>": the mention is the source of someone else's rating or deal. Only
# with an actor verb in the headline - "Layoffs at <name>" style headlines are about the
# company and stay.
_SOURCE_PREPS = frozenset({"by", "per", "via"})
_ROLE_WORDS = frozenset({"bookrunner", "bookrunners", "underwriter", "underwriters",
                         "arranger", "arrangers", "lender", "lenders"})


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text or "")


def name_aliases(name: str) -> set[str]:
    """Collapsed lower-case forms a headline may use for ``name``: the whole cleaned name
    ("jpmorganchase"), its first word ("jpmorgan", when 4+ letters) and its first two words.
    "J.P. Morgan" collapses to "jpmorgan" the same way, so the two meet."""
    words = [w.lower() for w in _tokens(name) if w.lower() not in _LEGAL_SUFFIXES]
    if not words:                    # a name made only of suffix words ("Company Co")
        words = [w.lower() for w in _tokens(name)][:1]
    if not words:
        return set()
    out = {"".join(words), "".join(words[:2])}
    if len(words[0]) >= 4:
        out.add(words[0])
    return {a for a in out if len(a) >= 3}


def _mentions(headline: str, aliases: set[str], ticker: str) -> list[tuple[int, int]]:
    """``(first, last+1)`` token spans of every mention of the company in ``headline``."""
    toks = _tokens(headline)
    spans: list[tuple[int, int]] = []
    for i in range(len(toks)):
        joined, longest = "", 0
        for k in range(1, 6):
            if i + k > len(toks):
                break
            joined += toks[i + k - 1].lower()
            if joined in aliases:
                longest = k                  # the LONGEST alias wins: "Acme Capital", not "Acme"
        if longest:
            spans.append((i, i + longest))
    base = (ticker or "").split(".")[0].upper()
    if len(base) >= 2:
        spans += [(i, i + 1) for i, t in enumerate(toks) if t == base]
    return spans


def is_about_company(headline: str, *, name: str = "", ticker: str = "") -> bool:
    """True when ``headline`` names the company in a non-analyst, non-arranger role."""
    toks = [t.lower() for t in _tokens(headline)]
    # "... joint bookrunners J.P. Morgan and ...": a deal credit, not news about the bank
    mentions_role = any(t in _ROLE_WORDS for t in toks)
    has_actor_verb = any(t in _ACTOR_VERBS for t in toks)
    has_note_word = any(t in _NOTE_WORDS for t in toks)
    for start, end in _mentions(headline, name_aliases(name), ticker):
        after = toks[end] if end < len(toks) else ""
        before = toks[start - 1] if start > 0 else ""
        if (after in _STRONG_ACTOR_VERBS or mentions_role
                or (after in _WEAK_ACTOR_VERBS and has_note_word)
                or (before in _SOURCE_PREPS and has_actor_verb)):
            continue
        return True
    return False


def subject_news(items: Sequence, *, name: str = "", ticker: str = "",
                 minimum: int = MIN_SUBJECT_HEADLINES) -> tuple[list, str]:
    """``(kept items, note)``. Fewer than ``minimum`` surviving headlines is not a news
    block, so it returns ``([], NO_SUBJECT_NEWS_NOTE)`` - stated, never silently thin."""
    kept = [it for it in items
            if is_about_company(getattr(it, "headline", ""), name=name, ticker=ticker)]
    if len(kept) < minimum:
        return [], NO_SUBJECT_NEWS_NOTE
    return kept, ""


def headlines_kept(headlines: Iterable[str], *, name: str = "", ticker: str = "") -> list[str]:
    return [h for h in headlines if is_about_company(h, name=name, ticker=ticker)]
