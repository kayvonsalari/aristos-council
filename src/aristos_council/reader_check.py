"""READER-1..5 — the deterministic check on a written summary.

The summary is the only part of a report a language model composes freely, so it is the
only part that can be wrong in a way no rule caught. This module is that rule.

**READER-5 rewrote the contract, because the rule was eating the feature.** Of the first
five live summaries, ONE published. The four that were withheld were withheld for:
"balance sheet" used without a bracket, "momentum" used without a bracket (twice), and
"percentile" used without a bracket. Not one of them was withheld for saying something
untrue. A reader who ticked the box got "Summary withheld: term without gloss: momentum"
four times out of five, which is not honest abstention — it is a feature that does not
work, wearing abstention's clothes.

So the two kinds of problem are now separated, and only one of them can withhold.

**FOUR checks WITHHOLD.** Each one means the summary says something the run does not:

1. **Numbers** — every number in the text appears in the facts pack. This is the check
   that matters most: it is what makes the prose auditable rather than plausible.
2. **Names** — every company or ticker named is one the run actually carries.
3. **Roles** — a test the text NAMES and describes as a check or a picker is described as
   the pack says it is. Calling a picker a check inverts what the run said.
4. **Unanimous BUYs** — a name every test rated BUY is named. It is the strongest single
   fact a multi-test run produces.

Plus the shape check: all five fields present and non-empty. A summary with an empty field
is not a summary.

**EVERYTHING ELSE IS ADVISORY.** Length over the target, an advice word, a term used
without a gloss, a vague range, a bracket inside a bracket: all still detected, all
recorded on the run as ``notes``, none of them a reason to withhold. They are matters of
style, and a style rule that destroys a truthful summary costs the reader more than the
style fault ever did. The prompt still asks for all of them; ``notes`` is the evidence for
whether the asking works.

The advice-word note keeps its exemption for a verdict label in CAPITALS: quoting the
run's own output is reporting, not advising, and BUY/HOLD/SELL are the run's own words.

Nothing here reads a model, and nothing here blocks a run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# READER-5 — a TARGET, not a limit. Over it the summary is noted as long and published;
# a note that is 20 words too long is still the note, and throwing it away leaves the
# reader with nothing instead of with slightly too much.
WORD_TARGET = 300
WORD_LIMIT = WORD_TARGET          # retained: the prompt and the docs quote this name

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
    # READER-5 — style faults. Detected exactly as before and recorded on the run, but
    # never a reason to withhold. A summary can be published AND imperfect, and keeping
    # the two lists apart is what lets the prompt be improved from evidence instead of
    # from memory.
    notes: list[str] = field(default_factory=list)

    @property
    def withheld_line(self) -> str:
        return "" if self.ok else f"Summary withheld: {self.reason}"

    @property
    def notes_line(self) -> str:
        """The advisory faults as one line, or "". Never rendered in the report — this is
        for the run meta and for a person reading it."""
        return "; ".join(self.notes)


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


# The only shapes that COUNT as describing a test: "Forensic (a check)", "Forensic is a
# check", "Forensic, a check,", "…are checks". Everything else in the sentence is prose
# about the run.
_COPULA = r"^[\s,:;(\u2013\u2014-]*(?:is|are|was|were)?[\s]*(?:an?|the)?[\s]*"


def _role_claim(sentence: str, name: str, others=()):
    """The role a sentence claims for ``name``, as "check" or "picker", or None.

    Read only from the sentence the NAME appears in, because a summary that says "one test
    is a check" two sentences later is describing the run, not this test.

    READER-5 — attributed by PROXIMITY, and stopping at the next test named. The live 09:10
    summary put three tests and three role words in ONE sentence:

        "Three tests ran: Cyclical Income (the selector), Forensic (a check), and Magic
         Formula RAW (a check)."

    A rule that scanned the whole sentence for any role word gave every name the FIRST one
    it found — so Forensic was reported as called a picker (it is a check, correctly
    described), and Magic Formula RAW's genuinely wrong "(a check)" was never seen at all.
    The check accused the innocent name and missed the guilty one, on the exact sentence it
    was written for. The window for a name therefore runs FORWARD from that name to the
    next test named after it, which is where its own description lives.

    Forward only, deliberately. "The selector is Cyclical Income" is missed by this, and
    that is the right way to be wrong: a missed mismatch publishes a slightly misdescribed
    summary, while a misattributed one destroys a correct summary — which is the failure
    READER-5 exists to end."""
    lowered = sentence.lower()
    at = lowered.find(name.lower())
    if at < 0:
        return None
    start = at + len(name)
    end = len(sentence)
    for other in others:
        if other.lower() == name.lower():
            continue
        where = lowered.find(other.lower(), start)
        if where >= 0:
            end = min(end, where)
    window = lowered[start:end]

    # ...and the role word must be DESCRIBING the name, not merely sharing a clause with
    # it. The 20:47 summary contains "The 9 names rated BUY under Cyclical Income all fell
    # to checks", where "checks" is the object of a verb and says nothing about what
    # Cyclical Income is. A rule that read it as a claim would have withheld a true
    # summary — the exact failure READER-5 exists to end — so the role word has to sit in
    # the copular position: right after the name, optionally through "is/are/was/were" and
    # an article, and nothing else.
    for phrase in sorted(_ROLE_WORDS, key=len, reverse=True):
        if re.match(_COPULA + re.escape(phrase) + r"\b", window):
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
    """Check ``summary`` against ``pack``.

    Returns a ``ReaderCheck`` whose ``problems`` are the faults that WITHHOLD it and whose
    ``notes`` are the faults that do not. ``ok`` follows ``problems`` alone: the summary
    is published whenever nothing in it contradicts the run."""
    fields = {f: (getattr(summary, f, None) if not isinstance(summary, dict)
                  else summary.get(f)) or "" for f in _FIELDS}

    missing = [f for f in _FIELDS if not fields[f].strip()]
    if missing:
        reason = f"missing or empty field(s): {', '.join(missing)}"
        return ReaderCheck(ok=False, reason=reason, problems=[reason])

    text = " ".join(fields[f] for f in _FIELDS)
    words = len(text.split())
    problems: list[str] = []
    notes: list[str] = []

    # ----------------------------------------------------------------- WITHHOLD
    # 1. a number the run does not hold.
    known = _pack_numbers(pack)
    strays = sorted({n for n in _numbers(text) if n not in known},
                    key=lambda t: (len(t), t))
    if strays:
        problems.append("number not in the facts: " + ", ".join(strays))

    # 2. a company the run does not carry. Only tokens that LOOK like a ticker and are not
    # ordinary capitalised prose — a check that flagged "It" or "This" would be noise.
    known_names = _pack_names(pack)
    candidates = {t for t in _TICKER.findall(text)
                  if t.upper() == t and len(t) >= 2 and not _VERDICT_LABEL.fullmatch(t)}
    unknown = sorted({t for t in candidates if t.lower() not in known_names})
    if unknown:
        problems.append("name not in the run: " + ", ".join(unknown))

    # 3. a named test described with the wrong role (READER-4). The 09:10 summary called
    # Magic Formula RAW "a check"; it is a second picker, and a reader told otherwise
    # reads its BUYs as "nothing objectionable found" rather than "this test chose it".
    mismatched = []
    roles = _pack_roles(pack)
    for sentence in _sentences(text):
        for name, role in roles.items():
            claimed = _role_claim(sentence, name, others=roles)
            if claimed is not None and claimed != role:
                said = "a check" if claimed == "check" else "a picker"
                if (name, said) not in mismatched:
                    mismatched.append((name, said))
    if mismatched:
        problems.append("role mismatch: "
                        + "; ".join(f"{n} called {w}" for n, w in mismatched))

    # 4. a name EVERY test rated BUY, left out (READER-4).
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

    # ------------------------------------------------------------------ ADVISE
    # Detected exactly as before; recorded, never enforced. Each of these withheld a live
    # summary that was otherwise true, which is what READER-5 exists to stop.
    if words > WORD_TARGET:
        notes.append(f"{words} words (target {WORD_TARGET})")

    without_labels = _VERDICT_LABEL.sub(" ", text)
    hits = sorted({w for w in FORBIDDEN
                   if re.search(rf"\b{re.escape(w)}\b", without_labels, re.I)})
    if hits:
        notes.append("advice word: " + ", ".join(f'"{w}"' for w in hits))

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
        notes.append("term without gloss: " + ", ".join(ungloss))

    ranges = [f"{a} to {b}" for a, b in _VAGUE_RANGE.findall(text)]
    if ranges:
        notes.append("vague range: " + ", ".join(ranges))

    nested = _NESTED_GLOSS.findall(text)
    if nested:
        notes.append("garbled gloss: " + ", ".join(sorted(set(nested))))

    return ReaderCheck(ok=not problems, reason="; ".join(problems), words=words,
                       problems=problems, notes=notes)
