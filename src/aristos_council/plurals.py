"""ONE helper for "1 name" / "2 names" (Batch 18B).

Every count a reader sees goes through ``plural`` (or ``was_were`` for the verb), so "(s)" and
"(ies)" never reach a report, and "1 name were" cannot be written. No imports: any module may use it.
"""
from __future__ import annotations


def plural(n, singular: str, plural_form: str | None = None) -> str:
    """``plural(1, "name") -> "1 name"``, ``plural(2, "name") -> "2 names"``,
    ``plural(2, "company", "companies") -> "2 companies"``. ``n`` may be a float that is a whole
    number (``2.0`` reads "2")."""
    try:
        whole = float(n).is_integer()
    except (TypeError, ValueError):
        whole = False
    shown = int(n) if whole else n
    one = whole and int(n) == 1
    word = singular if one else (plural_form or _regular(singular))
    return f"{shown} {word}"


def noun(n, singular: str, plural_form: str | None = None) -> str:
    """Just the word, for a number that is already printed elsewhere: ``noun(1, "year") -> "year"``."""
    try:
        one = float(n) == 1
    except (TypeError, ValueError):
        one = False
    return singular if one else (plural_form or _regular(singular))


def was_were(n) -> str:
    """"was" for exactly one, else "were"."""
    try:
        return "was" if float(n) == 1 else "were"
    except (TypeError, ValueError):
        return "were"


def is_are(n) -> str:
    try:
        return "is" if float(n) == 1 else "are"
    except (TypeError, ValueError):
        return "are"


def has_have(n) -> str:
    try:
        return "has" if float(n) == 1 else "have"
    except (TypeError, ValueError):
        return "have"


def _regular(word: str) -> str:
    if word.endswith("y") and word[-2:-1] not in "aeiou":
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "ch", "sh")):
        return word + "es"
    return word + "s"


def verb(n, singular: str, plural_form: str) -> str:
    """The verb that agrees with ``n``: ``verb(1, "applies", "apply") -> "applies"``."""
    try:
        return singular if float(n) == 1 else plural_form
    except (TypeError, ValueError):
        return plural_form
