"""FACTS-ORDER-1 — a series carries its order from ONE place, and nobody re-orders it.

Live, oil services narrator run 2026-09-18 18:34, TechnipFMC. The pack rendered

    Free Cash Flow Annual (oldest first) (4 periods, oldest first):
        $1.4bn · $679.4m · $467.8m · $194.2m

and the narrator wrote "the series runs from most recent to oldest in the raw evidence",
re-ordered it, and concluded "a sustained decline across all four visible periods". The
risk specialist made that decline its main risk and four of the five open questions rested
on it.

The series RISES. ``Fundamentals.free_cash_flow_annual`` is NEWEST-FIRST — the adapter
says so in a comment at the point it is built — so $194.2m is the oldest figure and
$1,447.4m the latest. **The array was right and the LABEL was wrong.** The narrator was
handed a bare list with a label contradicting it and guessed, which is the only thing it
could do.

So a series is never packed as a bare list again. It is packed with its YEARS attached to
its values, and a series whose periods cannot be labelled is not packed at all — it
abstains, exactly like every other figure this repo cannot stand behind. There is then
nothing left to infer: the order is stated, the years are on the values, and any claim
about direction can be recomputed rather than believed.
"""
from __future__ import annotations

import re
from typing import Optional, Sequence

ORDER_OLDEST_FIRST = "oldest_first"
ORDER_NEWEST_FIRST = "newest_first"

NO_PERIODS = "period labels unavailable"

# What the provider actually hands us, recorded ONCE here rather than assumed at each
# call site. Every one of these is newest-first; ``aligned_period_ends`` carries the
# fiscal year-ends that prove it.
PROVIDER_ORDER = ORDER_NEWEST_FIRST

# The aligned (dated) series name behind each positional field, so a packer can find the
# years for a list that does not carry them itself.
_ALIGNED_FOR = {
    "free_cash_flow_annual": "free_cash_flow",
    "operating_cash_flow_annual": "operating_cash_flow",
    "total_revenue": "total_revenue",
    "net_income": "net_income",
    "diluted_eps": "diluted_eps",
}

_DIRECTION_WORDS = {
    "rose": "up", "rising": "up", "rise": "up", "grew": "up", "growing": "up",
    "growth": "up", "increased": "up", "increasing": "up", "improved": "up",
    "climbed": "up", "higher": "up",
    "fell": "down", "falling": "down", "fall": "down", "declined": "down",
    "declining": "down", "decline": "down", "decreased": "down", "decreasing": "down",
    "dropped": "down", "deteriorated": "down", "shrank": "down", "lower": "down",
    "eroded": "down",
}
# How near a direction word has to sit to the series name before it is read as a claim
# ABOUT that series. Same shape as reader_check's GLOSS_WINDOW, for the same reason: a
# window is what stops an unrelated sentence being scored against the wrong subject.
DIRECTION_WINDOW = 140


# --------------------------------------------------------------------------- #
# packing
# --------------------------------------------------------------------------- #
def _year_of(period: object) -> str:
    """``"2024-12-31"`` -> ``"FY2024"``. Anything unparseable is not a year."""
    text = str(period or "").strip()
    match = re.match(r"(\d{4})", text)
    return f"FY{match.group(1)}" if match else ""


def pack_series(fundamentals, field: str, *, label: str = "",
                order: str = ORDER_OLDEST_FIRST) -> dict:
    """``{"label", "order", "values", "years"}`` — or an abstention.

    The values come back in ``order`` with ``years[i]`` belonging to ``values[i]``, so the
    order is stated AND redundant: a reader (or a model) that ignores the stated order can
    still recover it from the years. That redundancy is the point — the 18:34 failure was
    a stated order contradicting an unstated one, with nothing to break the tie.

    A series whose periods carry no fiscal labels ABSTAINS rather than being packed with a
    guessed order. An unlabelled series is exactly what caused this.
    """
    name = label or field.replace("_annual", "").replace("_", " ")
    aligned = getattr(fundamentals, "aligned_annual", None) or {}
    ends = getattr(fundamentals, "aligned_period_ends", None) or {}
    key = _ALIGNED_FOR.get(field, field)

    values = [v for v in (aligned.get(key) or [])]
    periods = [_year_of(p) for p in (ends.get(key) or [])]
    if not values or len(periods) != len(values) or not all(periods):
        return {"label": name, "order": order, "values": [], "years": [],
                "note": NO_PERIODS}

    pairs = [(y, v) for y, v in zip(periods, values) if v is not None]
    if not pairs:
        return {"label": name, "order": order, "values": [], "years": [],
                "note": NO_PERIODS}

    # The provider hands them newest-first; sorting by the YEAR rather than reversing the
    # list means the result is right even if a provider ever changes its mind.
    pairs.sort(key=lambda p: p[0], reverse=(order == ORDER_NEWEST_FIRST))
    return {"label": name, "order": order,
            "years": [y for y, _ in pairs], "values": [v for _, v in pairs], "note": ""}


def packed_ok(packed: dict) -> bool:
    return bool(packed) and bool(packed.get("values")) and not packed.get("note")


def render_packed(packed: dict) -> str:
    """One line for a prompt: every value wearing its year, and the order said out loud."""
    if not packed_ok(packed):
        return f"{packed.get('label', 'series')}: not stated — {packed.get('note') or NO_PERIODS}"
    pairs = " · ".join(f"{y} {v:,.0f}" for y, v in zip(packed["years"], packed["values"]))
    order = "oldest first" if packed["order"] == ORDER_OLDEST_FIRST else "newest first"
    return f"{packed['label']} ({order}, each figure labelled with its year): {pairs}"


# --------------------------------------------------------------------------- #
# direction — recomputed, never believed
# --------------------------------------------------------------------------- #
def series_direction(packed: dict) -> str:
    """``"up"``, ``"down"``, ``"flat"`` or ``""`` — from the YEARS, not the list order.

    Oldest value against newest value. Not a per-step test: a series that dips in the
    middle and ends higher has risen, and calling that a decline is the error under test.
    """
    if not packed_ok(packed) or len(packed["values"]) < 2:
        return ""
    pairs = sorted(zip(packed["years"], packed["values"]))
    oldest, newest = pairs[0][1], pairs[-1][1]
    if oldest == newest:
        return "flat"
    return "up" if newest > oldest else "down"


def _claims_near(text: str, label: str) -> set[str]:
    """The direction words asserted within the window around a mention of ``label``."""
    lowered = text.lower()
    needle = label.lower()
    found: set[str] = set()
    at = lowered.find(needle)
    while at != -1:
        window = lowered[max(0, at - DIRECTION_WINDOW): at + len(needle) + DIRECTION_WINDOW]
        for word, direction in _DIRECTION_WORDS.items():
            if re.search(rf"\b{re.escape(word)}\b", window):
                found.add(direction)
        at = lowered.find(needle, at + 1)
    return found


def direction_contradictions(text: str, packed_series: Sequence[dict]) -> list[str]:
    """Every series whose stated direction contradicts its own numbers.

    A FACTUAL check, not a style rule: it does not care how the prose is written, only
    that "decline" is not said about a series that rose. Silence is not a contradiction
    and neither is a claim the numbers agree with.
    """
    out: list[str] = []
    for packed in packed_series or ():
        actual = series_direction(packed)
        if actual not in ("up", "down"):
            continue
        label = str(packed.get("label") or "").strip()
        if not label:
            continue
        claimed = _claims_near(text, label)
        # Only a claim in the OPPOSITE direction contradicts. Prose carrying both words
        # ("fell in 2022 before rising") is describing the path, not asserting a trend.
        if len(claimed) == 1 and actual not in claimed:
            out.append(label)
    return out


def direction_problem(labels: Sequence[str]) -> str:
    """The withholding reason, in the repo's own phrasing."""
    return ("direction contradicts the series: "
            + ", ".join(sorted({str(x) for x in labels}))) if labels else ""
