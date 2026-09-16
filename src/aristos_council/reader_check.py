"""READER-1 — the deterministic check on a written summary.

The summary is the only part of a report a language model composes freely, so it is the only
part that can be wrong in a way no rule caught. This module is that rule. It runs on EVERY
summary, and a summary that fails is WITHHELD — not corrected, not trimmed, not published
with a caveat.

Withholding rather than fixing is the same doctrine the rest of the report follows: an
abstention states its reason, and the reason is visible. "Summary withheld: 342 words" tells
a reader the note was attempted and why it is not there, which an empty space does not.

Five checks, all mechanical, none of them a judgement about whether the prose is any good:

1. **Length** — 300 words or fewer.
2. **Forbidden words** — the advice vocabulary. A verdict label in CAPITALS is exempt,
   because quoting the run's own output is reporting, not advising.
3. **Numbers** — every number in the text must appear in the facts pack. This is the check
   that matters most: it is what makes the prose auditable rather than plausible.
4. **Names** — every company or ticker named must be one the run actually carries.
5. **Shape** — all five fields present and non-empty.

Nothing here reads a model, and nothing here blocks a run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

WORD_LIMIT = 300

# The advice vocabulary. "buy"/"sell" are the two the run's own verdict labels collide with,
# which is why the capitalised forms are exempted below rather than the words dropped.
FORBIDDEN = (
    "buy", "sell", "should", "recommend", "recommended", "recommendation",
    "attractive", "opportunity", "undervalued", "overvalued", "bargain",
)

# A verdict label quoted from the run: BUY, HOLD, SELL in capitals. Reporting, not advising.
_VERDICT_LABEL = re.compile(r"\b(?:BUY|HOLD|SELL)\b")

# Numbers as a reader writes them: 135, 3.5, 12%, 1,200, $5bn, 10th. Captured WITHOUT the
# decorations so "126" and "126 names" and "(126)" all reduce to the same token.
_NUMBER = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)")

# A ticker as the reports render one: 2-6 capitals, optionally dotted (BRK-B, 0857.HK).
_TICKER = re.compile(r"\b([A-Z]{1,6}(?:[.-][A-Z0-9]{1,4})?)\b")

_FIELDS = ("asked", "happened", "survived", "doubt", "cannot_say")


@dataclass(frozen=True)
class ReaderCheck:
    """The verdict on one summary. ``ok`` false -> the section is not rendered."""

    ok: bool
    reason: str = ""
    words: int = 0
    # Everything that tripped, not just the first — a writer fixing one problem should not
    # discover the next on the following run.
    problems: list[str] = field(default_factory=list)

    @property
    def withheld_line(self) -> str:
        return "" if self.ok else f"Summary withheld: {self.reason}"


def _numbers(text: str) -> list[str]:
    """Numeric tokens, normalised: thousands separators and trailing zeros removed, so
    "1,200" and "1200" are one token and "80" matches "80.0" in the pack."""
    out = []
    for raw in _NUMBER.findall(text or ""):
        token = raw.replace(",", "")
        if "." in token:
            token = token.rstrip("0").rstrip(".")
        out.append(token or "0")
    return out


def _pack_numbers(pack) -> set[str]:
    """Every number the pack contains, at any depth, as the same normalised tokens.

    Both the VALUES (counts, percentiles) and the numbers embedded in the pack's own
    strings (a rule's limit reads "at least 1.5%"; a drop reason names a percentile), so a
    figure the pack states in prose is quotable."""
    found: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v)
        elif isinstance(node, bool):
            return
        elif isinstance(node, (int, float)):
            found.update(_numbers(repr(node)))
        elif isinstance(node, str):
            found.update(_numbers(node))

    walk(pack)
    # A percentage the pack holds as a fraction is quotable as a percentage: the band's
    # 0.8 cutoff is "80%" to a reader, and the pack's own prose says "80th".
    for token in list(found):
        try:
            value = float(token)
        except ValueError:
            continue
        if 0 < value < 1:
            found.update(_numbers(repr(round(value * 100, 6))))
    return found


def _pack_names(pack) -> set[str]:
    """Company names and tickers the run carries, lowercased word-wise for matching."""
    names: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "name" and isinstance(value, str):
                    names.add(value)
                walk(value)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v)

    walk(pack)
    out: set[str] = set()
    for n in names:
        out.add(n.lower())
        # The PARENTHESISED ticker first, at any length: a display name reads "Suncor
        # Energy Inc. (SU)", and a two-letter ticker is still a ticker. The general word
        # split below keeps a >2 floor to avoid admitting "Inc" and "SA" as names, which
        # would blunt the check — so short tickers have to be taken from their own
        # position rather than from that split.
        for ticker in re.findall(r"\(([A-Za-z0-9.\-]{1,10})\)", n):
            out.add(ticker.lower())
        for tok in re.findall(r"[A-Za-z0-9.\-]+", n):
            if len(tok) > 2:
                out.add(tok.lower().strip("()"))
    return out


def check_summary(summary, pack: dict) -> ReaderCheck:
    """Run every check against ``summary`` (a ReaderSummary or a dict) and ``pack``."""
    fields = {f: (getattr(summary, f, None) if not isinstance(summary, dict)
                  else summary.get(f)) or "" for f in _FIELDS}

    missing = [f for f in _FIELDS if not fields[f].strip()]
    if missing:
        reason = f"missing or empty field(s): {', '.join(missing)}"
        return ReaderCheck(ok=False, reason=reason, problems=[reason])

    text = " ".join(fields[f] for f in _FIELDS)
    words = len(text.split())
    problems: list[str] = []

    if words > WORD_LIMIT:
        problems.append(f"{words} words")

    # Forbidden words, on a copy with quoted verdict labels removed.
    without_labels = _VERDICT_LABEL.sub(" ", text)
    hits = sorted({w for w in FORBIDDEN
                   if re.search(rf"\b{re.escape(w)}\b", without_labels, re.I)})
    if hits:
        problems.append("forbidden word: " + ", ".join(f'"{w}"' for w in hits))

    known = _pack_numbers(pack)
    strays = sorted({n for n in _numbers(text) if n not in known},
                    key=lambda t: (len(t), t))
    if strays:
        problems.append("number not in the facts: " + ", ".join(strays))

    known_names = _pack_names(pack)
    # Only check tokens that look like a ticker AND are not ordinary capitalised prose;
    # a name check that flagged "It" or "This" would be noise, not a guard.
    candidates = {t for t in _TICKER.findall(text)
                  if t.upper() == t and len(t) >= 2 and not _VERDICT_LABEL.fullmatch(t)}
    unknown = sorted({t for t in candidates if t.lower() not in known_names})
    if unknown:
        problems.append("name not in the run: " + ", ".join(unknown))

    return ReaderCheck(ok=not problems, reason="; ".join(problems), words=words,
                       problems=problems)
