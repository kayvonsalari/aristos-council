"""SUMMARY-COUNT-1 - the summary's COUNTS and its lens-with-a-reason claims, checked against the table.

The READER-5 checks verify figures, names, roles and the BUY votes, but not how many lenses did
something. Live (Ford, 2026-10-06): the model wrote "six because Ford reported no operating profit,
one because Ford is not a bank or insurer, and one because revenue grew 5.7%" when the table shows
FIVE lenses out on operating profit, and every figure in the sentence was in the pack.

Two claims are checked, both only when the pack carries the lens table (a company pack does; a run
summary pack does not, so it is checked exactly as before):

1. **A count of lenses.** "N lenses/tests ..." must be a count the run holds (the totals, the voted /
   not-applying / check counts, each BUY/HOLD/SELL bucket, each reason group), and "N because <reason>"
   / "N lenses ... because <reason>" must equal the number of lenses whose own reason is that one.
2. **A lens named with a reason.** "<Lens> did not apply because <reason>" must name a reason the
   table gives for THAT lens.

Reasons are compared by topic (operating profit, bank/insurer, dividend, revenue growth ...), not by
wording, so a paraphrase passes and a wrong cause does not. A claim whose reason names no known topic
is left alone: the check withholds only what it can show to be wrong.
"""
from __future__ import annotations

import re

_WORDS = {"no": 0, "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_NUM = r"(?:\d{1,2}|" + "|".join(sorted((w for w in _WORDS if w != "no"), key=len, reverse=True)) + r")"

_TOPICS = {
    "operating profit": r"operating (?:profit|income|loss)",
    "bank or insurer": r"\bbank|\binsurer|not for this sector|\bsector\b|financial compan",
    "dividend": r"dividend|\byield\b|payout",
    "revenue growth": r"revenue (?:grew|growth|growing|compound)|sales (?:grew|growth)|\bcagr\b",
    "market cap": r"market cap|market value of|worth at least|too small",
    "debt": r"\bdebt\b|leverage",
    "return on capital": r"return on (?:invested )?capital|\broic\b",
    "peg": r"\bpeg\b",
    "history": r"history|years of data|insufficient",
    "price": r"\bp/e\b|earnings yield|price-to-book|valuation",
}
_TOPIC_RES = {k: re.compile(v, re.I) for k, v in _TOPICS.items()}

_COUNT_LENS = re.compile(rf"\b({_NUM})\s+(?:(?:voting|non-voting|ticked|check|checking)\s+)?"
                         r"(?:lenses|lens|tests|test)\b", re.I)
_COUNT_BECAUSE = re.compile(rf"\b({_NUM})\s+(?:of them\s+|of these\s+)?because\b", re.I)
_SPLIT_CLAUSE = re.compile(r",?\s+(?:and\s+)?(?=" + _NUM + r"\s+(?:of them\s+|of these\s+)?because\b)", re.I)


def topics(text: str) -> set:
    return {k for k, rx in _TOPIC_RES.items() if rx.search(text or "")}


def _n(token: str) -> int:
    t = token.lower()
    return int(t) if t.isdigit() else _WORDS[t]


def _sentences(text: str) -> list:
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text or "") if s.strip()]


def _excluded(pack: dict) -> list:
    """[(lens label, topics of its whole reason)] for the voting lenses that did not apply."""
    out = []
    for item in ((pack or {}).get("agreement") or {}).get("does_not_apply") or []:
        label, _sep, why = str(item).partition(" (")
        out.append((label.strip(), topics(why.rstrip(")"))))
    return out


def _legit_counts(pack: dict) -> set:
    ag = (pack or {}).get("agreement") or {}
    lenses = (pack or {}).get("lenses") or []
    legit = {1, len(lenses), len([x for x in lenses if x.get("votes")]),
             len([x for x in lenses if not x.get("votes")])}
    for key in ("lenses_that_voted", "lenses_that_did_not_apply", "check_lenses_that_do_not_vote",
                "buy_votes"):
        if isinstance(ag.get(key), int):
            legit.add(ag[key])
    for key in ("buy_lenses", "hold_lenses", "sell_lenses", "does_not_apply"):
        legit.add(len(ag.get(key) or []))
    excluded = _excluded(pack)
    for t in _TOPICS:
        legit.add(sum(1 for _l, ts in excluded if t in ts))
    legit.add(len(excluded))
    legit.discard(None)
    return legit


def count_problems(text: str, pack: dict) -> list[str]:
    """The faults in the summary's counts and lens-with-a-reason claims ([] when none)."""
    ag = (pack or {}).get("agreement") or {}
    if not ag.get("available") or not (pack or {}).get("lenses"):
        return []
    excluded = _excluded(pack)
    legit = _legit_counts(pack)
    problems: list[str] = []

    for sentence in _sentences(text):
        # "N because <reason>": the count of lenses whose reason is that one
        parts = _SPLIT_CLAUSE.split(sentence)
        for part in parts:
            m = _COUNT_BECAUSE.search(part)
            if not m:
                continue
            claimed = topics(part[m.end():])
            if not claimed:
                continue
            n = _n(m.group(1))
            actual = sum(1 for _l, ts in excluded if claimed & ts)
            if n != actual:
                problems.append(f'count mismatch: "{m.group(0)} {part[m.end():].strip()[:60]}" '
                                f"but the table shows {actual}")
        # "N lenses/tests": a count the run holds
        for m in _COUNT_LENS.finditer(sentence):
            n = _n(m.group(1))
            if n not in legit:
                problems.append(f'count mismatch: "{m.group(0)}" is not a count the table holds')
            tail = sentence[m.end():]
            bm = re.search(r"\b(?:because|failed on|failing on)\b(.*)$", tail, re.I)
            if bm and not _COUNT_BECAUSE.search(sentence):
                claimed = topics(bm.group(1))
                actual = sum(1 for _l, ts in excluded if claimed & ts)
                if claimed and n != actual:
                    problems.append(f'count mismatch: "{m.group(0)} ... {bm.group(0)[:40]}" '
                                    f"but the table shows {actual}")

    problems += _named_reason_problems(text, excluded)
    seen, out = set(), []
    for p in problems:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _named_reason_problems(text: str, excluded: list) -> list[str]:
    """A lens that did not apply, named with a reason that is not one of ITS reasons."""
    if not excluded:
        return []
    own = dict(excluded)
    problems = []
    for sentence in _sentences(text):
        low = sentence.lower()
        spots = sorted((low.find(label.lower()), label) for label in own if label and
                       low.find(label.lower()) >= 0)
        if not spots:
            continue
        clusters, cur = [], [spots[0]]
        for prev, nxt in zip(spots, spots[1:]):
            gap = low[prev[0] + len(prev[1]):nxt[0]]
            if re.fullmatch(r"[\s,;]*(?:and|&)?[\s,]*", gap):
                cur.append(nxt)
            else:
                clusters.append(cur)
                cur = [nxt]
        clusters.append(cur)
        for i, cluster in enumerate(clusters):
            start = cluster[-1][0] + len(cluster[-1][1])
            end = clusters[i + 1][0][0] if i + 1 < len(clusters) else len(sentence)
            claimed = topics(sentence[start:end])
            if not claimed:
                continue
            for _pos, label in cluster:
                if own[label] and not (claimed & own[label]):
                    problems.append(f"reason mismatch: {label} given as {'/'.join(sorted(claimed))}"
                                    f", the table says otherwise")
    return problems
