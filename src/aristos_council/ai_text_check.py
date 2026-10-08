"""B24-E9 - the AI text check as a reader sees it (it used to be called the "narration check").

The check itself lives in ``narration_check.py`` and never rewrites a sentence: it APPENDS one
``[⚠ AI text check: "<the sentence>" <what is wrong>]`` line per problem to the AI's text. This module is
only about showing those lines:

* ``top_line(n)`` - the one line at the top of each AI section;
* ``plain_reason(stamp)`` - the reason in plain English ("This rank belongs to a different lens");
* ``mark_sentences(prose, stamps)`` - puts a small amber marker at the END of each flagged sentence, with
  the reason a hover (desktop) or a tap (phone) reveals;
* ``flag_list(narrative)`` - the full list (each flag quotes its sentence) for "Show the workings" and
  the downloaded reports.

Stamps written before the rename read "narration check:"; every reader here accepts both labels, so an
older saved report still opens and counts correctly.
"""
from __future__ import annotations

import html
import re

LABEL = "AI text check"
_STAMP = re.compile(r'^\s*\[⚠ (?:narration|AI text) check:\s*"(?P<claim>.*?)"\s+(?P<why>.*?)\]\s*$', re.S)
_ANY_LABEL = re.compile(r"(?:narration|AI text) check:")

MARK = "⚠"
_TOKEN = "⟦FLAG{n}⟧"
_OPEN_TOKEN = "⟦FLAGSTART{n}⟧"


def is_stamp(line: str) -> bool:
    return bool(_STAMP.match(line or ""))


def split(narrative: str) -> tuple[str, list[str]]:
    """``(prose, stamps)``: the AI text check's appended lines lifted out, in order. Other ⚠ lines (a
    price-divergence note, say) stay where they are."""
    prose, stamps = [], []
    for line in (narrative or "").splitlines():
        (stamps if is_stamp(line) else prose).append(line)
    return "\n".join(prose), stamps


_OPEN = re.compile(r"\[⚠ (?:narration|AI text) check:")


def count(narrative: str) -> int:
    """How many flags the text carries (counted by the stamp's opening, so a stamp glued to the end of a
    line still counts)."""
    return len(_OPEN.findall(narrative or ""))


def top_line(n: int, *, noun: str = "sentence") -> str:
    """"AI text check: 3 sentences flagged, marked below" / "AI text check: no issues found"."""
    if not n:
        return f"{LABEL}: no issues found"
    return f"{LABEL}: {n} {noun}{'' if n == 1 else 's'} flagged, marked below"


def claim_of(stamp: str) -> str:
    m = _STAMP.match(stamp or "")
    return re.sub(r"\s+", " ", m.group("claim")).strip() if m else ""


def plain_reason(stamp: str) -> str:
    """The stamp's reason in plain English. Never rewrites the claim; only says what is wrong."""
    m = _STAMP.match(stamp or "")
    why = (m.group("why") if m else stamp or "").strip()
    low = why.lower()
    if "negative earnings-power reading" in low:
        return "A negative reading means the price is above its no-growth value, not a discount"
    if "attributes" in low and "rank" in low:
        return "This rank belongs to a different lens"
    if "without naming the lens it belongs to" in low:
        return "This rank does not say which lens it belongs to"
    if "would rank" in low and "not a vote" in low and "without saying" in low:
        return "Only where the company would rank; that lens did not vote"
    if "gives" in low and "a verdict or a vote" in low:
        return "This lens did not vote"
    if "weighs the lenses against each other" in low:
        return "This weighs the lenses against each other"
    if "orders a tied pair" in low:
        return "This puts one of two tied names ahead of the other"
    if "misstates" in low and "verdict" in low:
        return "This gives another company the wrong verdict"
    if "contradicts rank table" in low:
        return "This does not match the rank table"
    if "near-verbatim" in low:
        n = re.search(r"in (\d+) specialists", low)
        words = {2: "Two", 3: "Three", 4: "Four"}
        return f"{words.get(int(n.group(1)), 'Several') if n else 'Several'} specialists used the same words"
    return "This sentence may not match the figures"


def flag_list(narrative: str) -> list[tuple[str, str]]:
    """``[(quoted sentence, plain reason)]`` for the full list under "Show the workings"."""
    return [(claim_of(s), plain_reason(s)) for s in split(narrative)[1]]


def marker_html(reason: str) -> str:
    """The amber marker: hover shows the reason on a desktop, focus (a tap) on a phone. The reason is also
    the ``aria-label``/``title``, so a screen reader and the no-CSS case still get it."""
    r = html.escape(reason, quote=True).replace("$", "&#36;")
    return (f'<span class="ar-flag" tabindex="0" role="note" aria-label="{r}" title="{r}">'
            f'{MARK}<span class="ar-flag-tip">{r}</span></span>')


def mark_sentences(prose: str, stamps: list[str]) -> tuple[str, dict[str, str], list[str]]:
    """``(prose with placeholders, {placeholder: marker html}, stamps that could not be placed)``.

    Each flagged sentence gets a placeholder right after its end (and its full stop); the caller turns the
    prose into HTML and then swaps the placeholders for the markers, so markdown handling is untouched."""
    swaps: dict[str, str] = {}
    unplaced: list[str] = []
    text = prose
    for i, stamp in enumerate(stamps):
        claim = claim_of(stamp)
        if not claim:
            unplaced.append(stamp)
            continue
        pattern = r"\s+".join(re.escape(w) for w in claim.split())
        m = re.search(pattern + r"[.!?]?", text)
        if m is None:
            unplaced.append(stamp)
            continue
        token, start = _TOKEN.format(n=i), _OPEN_TOKEN.format(n=i)
        # the sentence is lightly underlined and the amber mark sits at its end (B25-5)
        text = text[:m.start()] + start + text[m.start():m.end()] + token + text[m.end():]
        swaps[start] = '<span class="ar-flagged">'
        swaps[token] = "</span>" + marker_html(plain_reason(stamp))
    return text, swaps, unplaced


def apply_swaps(rendered_html: str, swaps: dict[str, str]) -> str:
    for token, marker in swaps.items():
        rendered_html = rendered_html.replace(token, marker)
    return rendered_html
