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
6. **Roles** (READER-4) — a test the text NAMES and describes as a check or a picker must
   be described as the pack says it is. The 09:10 summary called a second selector "a
   check"; a reader told that reads its BUYs as "nothing objectionable found" rather than
   "this test chose it", which inverts what the run said.
7. **Unanimous BUYs** (READER-4) — a name every test rated BUY is the strongest single
   fact a multi-test run produces, and it must be named. The 09:10 summary omitted the
   only one it had.

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

# READER-2 — terms the prompt already demands be glossed on first use. The demand was
# unchecked, and the first live summary duly used "percentile" bare. A gloss is a bracketed
# phrase within GLOSS_WINDOW characters of the term, which is where a reader looks for it.
# READER-3 — "balance sheet" is OFF this list. The others are terms of art a general
# reader may genuinely not hold ("accrual", "percentile", "momentum"); a balance sheet is a
# household phrase, and demanding a bracketed gloss for it bought nothing while costing
# real summaries — a summary WITHHELD over a word every reader already knows is a worse
# outcome than the word left unglossed.
# READER-4 — "valuation" is off this list too, for a different reason from "balance
# sheet". It is not that a reader knows the word: it is that the bracketed gloss the rule
# extracted for it was the WORST sentence in the 09:10 summary — "their own price history
# range [valuation (price against its own past)]", a bracket inside a bracket, which is
# less legible than the jargon it was meant to explain. v4 asks for the plain phrase in
# the sentence instead ("cost far more than usual for the profit they make, compared with
# their own last five years"), and a rule demanding a bracket would work against that.
GLOSS_TERMS = ("percentile", "free cash flow", "accrual", "momentum")
GLOSS_WINDOW = 60

# READER-2 — "2 to 3 names" when the pack holds the exact figure. A range is a way of not
# saying a number, and the writer is never short of the number.
_VAGUE_RANGE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s+to\s+(\d[\d,]*(?:\.\d+)?)")

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



# READER-4 — a nested gloss. "[valuation (price against its own past)]" is what the
# bracket rule produced when the writer tried to gloss a word inside an aside it was
# already making. A gloss exists to be read; one that needs its own gloss has failed.
_NESTED_GLOSS = re.compile(r"[\[(][^\[\]()]*[\[(][^\[\]()]*[\])]")

# READER-4 — the role words a summary may apply to a named test, and the pack role each
# one asserts. "Picker" and "primary" both claim the test SELECTS; "check" claims it does
# not. A summary may still use the words freely about tests it does not name — this fires
# only where a NAME and a role word sit in the same sentence.
# Singular AND plural: the 09:10 summary said "Forensic and Magic Formula RAW are
# checks", and a rule that only saw "check" would have let the sentence through.
_ROLE_WORDS = {
    "check": "check", "checks": "check",
    "the selector": "picker", "selector": "picker", "selectors": "picker",
    "primary picker": "picker", "second picker": "picker",
    "picker": "picker", "pickers": "picker", "primary": "picker",
}


def _role_claim(sentence: str, name: str):
    """The role a sentence claims for ``name``, as "check" or "picker", or None.

    Read only from the sentence the NAME appears in, because a summary that says "one test
    is a check" two sentences later is describing the run, not this test."""
    if name.lower() not in sentence.lower():
        return None
    lowered = sentence.lower()
    for phrase in sorted(_ROLE_WORDS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(phrase)}\b", lowered):
            return _ROLE_WORDS[phrase]
    return None


def _pack_roles(pack) -> dict:
    """``{test name: "check" | "picker"}`` from the pack's stated roles."""
    out = {}
    for lens in (pack or {}).get("lenses") or []:
        name = (lens.get("name") or "").strip()
        role = (lens.get("role") or "").strip()
        if name and role:
            out[name] = "check" if role == "check" else "picker"
    return out


def _sentences(text: str) -> list:
    return [s for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]


def _is_named(display: str, ticker: str, text: str) -> bool:
    """Whether ``text`` mentions this company, the way a plain-English summary would.

    Not by its full legal name: the prompt asks for plain words, so "Suncor Energy" is the
    right way to write "Suncor Energy Inc." and demanding the "Inc." would punish the
    writer for obeying. The distinctive FIRST word of the name is enough, and so is the
    ticker — as a whole TOKEN, never a substring, because "SU" sits inside "survives" and
    a rule that accepted that would call a name mentioned on the strength of a verb."""
    lowered = text.lower()
    if display.lower() in lowered:
        return True
    first = re.split(r"[\s,]+", display.strip())[0]
    if first and re.search(rf"\b{re.escape(first)}\b", text, re.I):
        return True
    return bool(ticker) and bool(re.search(rf"\b{re.escape(ticker)}\b", text))


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

    # READER-2: a term the prompt requires glossed, used bare on FIRST appearance.
    ungloss = []
    lowered = text.lower()
    for term in GLOSS_TERMS:
        at = lowered.find(term)
        if at < 0:
            continue
        window = text[at:at + len(term) + GLOSS_WINDOW]
        if "(" not in window:
            ungloss.append(term)
    if ungloss:
        problems.append("term without gloss: " + ", ".join(ungloss))

    # READER-2: a vague range where the pack has the figure.
    ranges = [f"{a} to {b}" for a, b in _VAGUE_RANGE.findall(text)]
    if ranges:
        problems.append("vague range: " + ", ".join(ranges))

    # READER-4: a named test described with the wrong role. The 09:10 summary called
    # Magic Formula RAW "a check"; it is a second picker. A reader who is told a picker is
    # a check will read its BUYs as "nothing objectionable found" rather than "this test
    # chose it", which inverts what the run said.
    roles = _pack_roles(pack)
    mismatched = []
    for sentence in _sentences(text):
        for name, role in roles.items():
            claimed = _role_claim(sentence, name)
            if claimed is not None and claimed != role:
                said = "a check" if claimed == "check" else "a picker"
                if (name, said) not in mismatched:
                    mismatched.append((name, said))
    if mismatched:
        problems.append("role mismatch: "
                        + "; ".join(f"{n} called {w}" for n, w in mismatched))

    # READER-4: a bracket inside a bracket. A gloss exists to be read.
    nested = _NESTED_GLOSS.findall(text)
    if nested:
        problems.append("garbled gloss: " + ", ".join(sorted(set(nested))))

    # READER-4: a name EVERY test rated BUY is the strongest single fact a multi-test run
    # produces, and the 09:10 summary omitted it entirely. Naming it is not optional.
    missing_unanimous = []
    for entry in (pack or {}).get("unanimous_buy") or []:
        label = (entry.get("name") or "").strip()
        if not label:
            continue
        plain = label.split(" (")[0].strip()
        ticker = label[label.find("(") + 1:label.rfind(")")] if "(" in label else ""
        if plain and not _is_named(plain, ticker, text):
            missing_unanimous.append(plain)
    if missing_unanimous:
        problems.append("unanimous BUY not mentioned: " + ", ".join(missing_unanimous))

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
