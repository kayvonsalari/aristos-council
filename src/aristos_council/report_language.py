"""Plain-English report language (REPORT-1) — the ONE place a number becomes words.

Why this exists
---------------
The reports were written for the person who built the system. Column headers were raw
code identifiers (``low_volatility``), the header block showed only machine ids
(``conservative_plus_v1``), ratios printed as raw decimals (``observed 0.009547 vs
threshold 0.015``), section titles were internal jargon ("Factor integrity"), and there
was no summary anywhere — you had to count the table by hand to learn a run produced 2
BUY and excluded 6 of 16.

Two rules, applied everywhere:

1. **Label first, id second.** Every machine identifier gets its human label first and
   its id second, parenthetically. Ids are stable record keys — a run report, a verdict
   log and a frozen run all key on them — so an id is never removed. It is just never
   the only thing shown.

2. **Format from a declared unit, never from the render site.** Each registered
   criterion parameter and each factor declares a ``unit``; this module turns a value
   into words from that declaration. No per-string special cases, no guessing at the
   point of display: a criterion added tomorrow renders correctly because it declared
   what its number MEANS, not because someone remembered to add a case here.

Everything here is a PURE function of already-computed values. Nothing rounds a number
that a decision was made on, nothing re-derives a value, and nothing here can change a
verdict, a rank, a threshold or an abstention — this module only chooses words.
"""

from __future__ import annotations

import re

from dataclasses import dataclass
from typing import Optional

from .tools.price_context import format_money

# --------------------------------------------------------------------------- #
# Units — the declared vocabulary
# --------------------------------------------------------------------------- #
# A registered criterion parameter or factor declares exactly one of these, and this
# module formats from the declaration. Adding a seventh unit is a change HERE plus the
# registry entries that use it — never a special case at a render site.
UNIT_PERCENT = "percent"        # a DECIMAL fraction: 0.015 -> "1.5%"
# P5 / CRIT-NOCUT-2 — a decimal fraction rendered as a WHOLE percent: 0.523 -> "52%". For a
# figure that is a MAGNITUDE rather than a rate (how big a cut was), where a tenth of a
# percent is false precision. UNIT_PERCENT's magnitude-appropriate decimals are right for a
# yield and wrong here, so this is a separate unit rather than a change to that one.
UNIT_PERCENT0 = "percent0"
UNIT_RATIO = "ratio"            # a bare ratio with no natural unit: 2.0 -> "2.00"
UNIT_MULTIPLE = "multiple"      # a times-covered figure: 1.0 -> "1.0x"
UNIT_CURRENCY = "currency"      # a money amount, rendered compactly: 5e9 -> "$5.0bn"
UNIT_COUNT = "count"            # a whole number of things: 10 -> "10"
UNIT_SCORE = "score"            # a points/percentile scale: 5 -> "5", 80 -> "80"

UNITS: frozenset[str] = frozenset({UNIT_PERCENT, UNIT_RATIO, UNIT_MULTIPLE,
                                   UNIT_CURRENCY, UNIT_COUNT, UNIT_SCORE})

_MISSING = "not available"


def format_value(value, unit: str, *, currency: Optional[str] = None) -> str:
    """One number as words, from its DECLARED unit.

    ``percent`` takes a DECIMAL fraction and scales it, with magnitude-appropriate
    precision — a 0.95% yield and a 120% payout both need to read correctly, and fixed
    decimal places cannot do both::

        0.009547 -> "0.95%"     (below 1%: two decimals, or it reads as "1%")
        0.015    -> "1.5%"
        -0.1396  -> "-14.0%"
        1.198    -> "120%"      (above 100%: decimals are noise)

    ``currency`` renders compactly with its symbol (``$5.0bn``) and never converts —
    the currency comes from the declaration, not from an assumption. A None/non-numeric
    value renders as an honest "not available", never as 0.
    """
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return _MISSING
    v = float(value)
    if unit == UNIT_PERCENT:
        return _percent(v)
    if unit == UNIT_PERCENT0:
        return f"{v:.0%}"
    if unit == UNIT_MULTIPLE:
        return f"{v:,.1f}x"
    if unit == UNIT_CURRENCY:
        return _currency(v, currency)
    if unit == UNIT_COUNT:
        return f"{int(round(v)):,}"
    if unit == UNIT_SCORE:
        return f"{v:g}"
    return f"{v:,.2f}"                                   # UNIT_RATIO and anything else


def _percent(v: float) -> str:
    pct = v * 100.0
    a = abs(pct)
    if a >= 100 or pct == round(pct):
        # Above 100% decimals are noise; and a threshold that IS a round percentage —
        # 0.80, -0.10 — reads as "80%" and "-10%". A value that merely rounds to a whole
        # number (-13.96) keeps its decimal, because dropping it would overstate the
        # precision of the underlying number.
        return f"{pct:,.0f}%"
    if a >= 1:
        return f"{pct:,.1f}%"
    return f"{pct:,.2f}%"


_MAGNITUDES = ((1e12, "tn"), (1e9, "bn"), (1e6, "m"))


def _currency(v: float, currency: Optional[str]) -> str:
    for size, suffix in _MAGNITUDES:
        if abs(v) >= size:
            # ONE decimal at magnitude: "$5.0bn" is the number a reader wants; "$5.00bn"
            # implies a precision the compaction has already thrown away.
            return f"{format_money(v / size, currency, decimals=1)}{suffix}"
    return format_money(v, currency)


def format_signed_change(value, unit: str = UNIT_PERCENT) -> str:
    """A CHANGE as a direction plus a magnitude: ``"fell 14.0%"``, ``"rose 33%"``,
    ``"was unchanged"``.

    A signed decimal reads badly in a sentence — "share price change -0.1396 over 12
    months" makes the reader do the arithmetic and the sign. Derived from the sign
    alone, so it is not a per-criterion special case."""
    if value is None or not isinstance(value, (int, float)) or isinstance(value, bool):
        return _MISSING
    v = float(value)
    if v == 0:
        return "was unchanged"
    word = "rose" if v > 0 else "fell"
    return f"{word} {format_value(abs(v), unit)}"


# --------------------------------------------------------------------------- #
# Thresholds — the rule, in words
# --------------------------------------------------------------------------- #
COMPARISON_MIN = "min"          # the observed value must be >= the threshold
COMPARISON_MAX = "max"          # the observed value must be <= the threshold


def format_threshold(comparison: str, value, unit: str, *,
                     currency: Optional[str] = None) -> str:
    """A threshold as the rule it expresses: ``"at least 1.5%"``, ``"at most 80%"``,
    ``"no worse than -10%"``.

    A ``min`` floor on a NEGATIVE percent is a drawdown limit, not a target — "at least
    -10%" is technically true and reads as nonsense, so the sign (not the criterion's
    name) selects the phrasing."""
    shown = format_value(value, unit, currency=currency)
    if comparison == COMPARISON_MAX:
        return f"at most {shown}"
    if unit == UNIT_PERCENT and isinstance(value, (int, float)) and value < 0:
        return f"no worse than {shown}"
    return f"at least {shown}"


def format_limit_clause(comparison: str, value, unit: str, *,
                        currency: Optional[str] = None) -> str:
    """The second half of an exclusion sentence: ``"the rule allows at most 80%"``,
    ``"the rule requires at least 1.5%"``, ``"the rule allows at most a 10% fall"``."""
    if comparison == COMPARISON_MAX:
        return f"the rule allows {format_threshold(comparison, value, unit, currency=currency)}"
    if unit == UNIT_PERCENT and isinstance(value, (int, float)) and value < 0:
        # A negative floor is a maximum permitted FALL; say that, rather than making the
        # reader negate a negative.
        return f"the rule allows at most a {format_value(abs(value), unit)} fall"
    return (f"the rule requires "
            f"{format_threshold(comparison, value, unit, currency=currency)}")


# --------------------------------------------------------------------------- #
# Ids — never removed, never alone
# --------------------------------------------------------------------------- #
def label_with_id(label: str, identifier: str) -> str:
    """``"Defensive Income (conservative_plus_v1)"`` — the human name first, the stable
    record key second. Falls back to the bare id when no label exists (never invents
    one) and to the bare label when there is no id."""
    label = (label or "").strip()
    identifier = (identifier or "").strip()
    if not label:
        return identifier
    if not identifier or label == identifier:
        return label
    return f"{label} ({identifier})"


# --------------------------------------------------------------------------- #
# Verdict summary — the sentence the reader had to count the table to get
# --------------------------------------------------------------------------- #
_VERDICT_ORDER = ("buy", "hold", "sell")


def verdict_counts(ranked) -> dict[str, int]:
    """``{"buy": 2, "hold": 6, "sell": 2}`` over the LIVE ranked names, best-first
    order irrelevant. Excluded names carry no verdict and are never counted."""
    counts = {v: 0 for v in _VERDICT_ORDER}
    for r in ranked:
        if getattr(r, "excluded", False):
            continue
        v = (getattr(r, "verdict", "") or "").lower()
        if v in counts:
            counts[v] += 1
    return counts




# --------------------------------------------------------------------------- #
# CHECK-WORDS-1 — a check lens speaks its own words
# --------------------------------------------------------------------------- #
# A check lens does not pick, so its verdicts never meant what they said. Forensic's "BUY"
# means "I found nothing to doubt here" and its "SELL" means "I doubt this" — and a reader
# scanning a grid of BUY/HOLD/SELL cells has no way to know that one column is answering a
# different question in the same words. SHORTLIST-3 made the distinction structural (a
# check marks, it does not vote); this makes it VISIBLE.
#
# DISPLAY ONLY. The verdict field is still "buy" / "hold" / "sell" everywhere underneath —
# the ranker, the quintile cut, the frozen runs under runs/, the verdict log and every
# recorded report keep the values they have always had, so nothing replays differently and
# no scoreboard row moves. This is a mapping applied where a verdict is SHOWN, keyed on
# the strategy's own ``kind``.
CHECK_WORDS = {"buy": "clean", "hold": "no concern", "sell": "doubted"}

# The rules line a check lens gets in place of the quintile sentence, so the words are
# explained where they are first met rather than only in the glossary.
CHECK_QUINTILE_LINE = "top 20% clean, bottom 20% doubted, middle no concern"


# NARR-LEVER-1 - the three patterns the check-cell scrub uses, compiled once.
_VERDICT_TOKEN = re.compile(r"\b(buy|hold|sell)\b", re.IGNORECASE)
_CELL_EDGE = re.compile("^[\\s—–:,.\\-]+|[\\s—–:,.\\-]+$")
_MULTI_SPACE = re.compile(r"\s{2,}")


def scrub_check_cell(text: str) -> str:
    """A CHECK lens's cell never carries BUY, HOLD or SELL. NARR-LEVER-1.

    Live, 2026-09-18 18:34: the narrator's own per-name lens table rendered Forensic
    as "BUY — CLEAN". The verdict words are the RUN's, and a check does not
    issue them — it MARKS. "Forensic rated this BUY" reads as a call to buy
    and is not one, which is the whole reason CHECK-WORDS-1 exists.

    Detected by CONTENT, not by lens name: a cell carrying one of the check words is
    a check's cell, and no voting lens ever emits those words. That keeps this right
    for a check lens added later under a name nobody here has heard of.
    """
    raw = (text or "").strip()
    if not raw:
        return raw
    if not any(word in raw.lower() for word in CHECK_WORDS.values()):
        return raw
    cleaned = _VERDICT_TOKEN.sub("", raw)
    cleaned = _CELL_EDGE.sub("", cleaned).strip()
    return _MULTI_SPACE.sub(" ", cleaned) or raw


def is_check_lens(strategy) -> bool:
    """True for a lens whose verdicts are MARKS rather than picks."""
    return (getattr(strategy, "kind", "selector") or "selector") == "check"


def verdict_word(verdict: str, *, check: bool = False) -> str:
    """The verdict as a reader should see it: ``BUY`` for a voting lens, ``clean`` for a
    check.

    Lower case for a check's words on purpose. They are not verdicts and should not wear a
    verdict's shouting capitals — "doubted" is a reading, "SELL" is a call."""
    raw = (verdict or "").strip().lower()
    if not raw:
        return ""
    return CHECK_WORDS.get(raw, raw) if check else raw.upper()


def format_summary_line(ranked, *, universe_size: int, excluded: int,
                        unrateable: int = 0, fetch_errors: int = 0,
                        check: bool = False) -> str:
    """``"2 BUY · 6 HOLD · 2 SELL — 10 of 16 names ranked, 6 excluded by the screen"``.

    Derived from the result, never hardcoded. A category with ZERO names is omitted
    rather than printed as "0 SELL" — a zero is not information here, it is noise. The
    non-verdict axes (unrateable, fetch failures) are named only when non-empty, and
    each keeps its own distinct wording: they are not exclusions."""
    counts = verdict_counts(ranked)
    # CHECK-WORDS-1: "21 clean · 62 no concern · 21 doubted" for a check lens. The counts
    # are the same counts; only the words change.
    verdicts = " · ".join(f"{n} {verdict_word(v, check=check)}"
                          for v, n in counts.items() if n)
    ranked_n = sum(counts.values())
    tail = [f"{ranked_n} of {universe_size} names ranked"]
    if excluded:
        tail.append(f"{excluded} excluded by the screen")
    if unrateable:
        tail.append(f"{unrateable} with no usable data")
    if fetch_errors:
        tail.append(f"{fetch_errors} whose data fetch failed — re-run to recover")
    body = ", ".join(tail)
    return f"{verdicts} — {body}" if verdicts else body


# --------------------------------------------------------------------------- #
# The ranked table's own glosses
# --------------------------------------------------------------------------- #
RANK_COLUMN_NOTE = "rank, 1 = best"


def format_score_gloss(n_factors: int, cohort_size: int) -> str:
    """The one line that replaces ``score 13 (best 3 · worst 30)`` in every row.

    Stated ONCE above the table: what the score IS, which direction is good, and the
    two bounds it sits between."""
    return (f"Score is the sum of a name's factor ranks — lower is better. "
            f"With {n_factors} factors over {cohort_size} ranked names the best "
            f"possible score is {n_factors} and the worst is "
            f"{n_factors * cohort_size}.")


# The three table symbols, one full sentence each. They used to share a single dense
# run-on line, which is why none of them was read.
# FACTOR-MARK-3: the imputed note is keyed by a WORD now, not by "*". Named rather than
# spelled at both ends, so the legend selector and the legend itself cannot drift apart.
IMPUTED_NOTE_KEY = "imputed"

SYMBOL_NOTES: tuple[tuple[str, str], ...] = (
    (IMPUTED_NOTE_KEY, "A factor cell reading · imputed had NO value for this name, so the "
                "rank shown is the average of the name's other factor ranks rather than "
                "the worst rank — the name is judged on what it has, not punished for "
                "the gap. The count beside the verdict (“ranked on 2 of 3 "
                "factors”) counts these out, so the two always agree."),
    ("†", "A name marked † PASSED the screen while one of the screen's rules could not "
          "be tested at all for it; the rule and the reason are named under the verdict."),
    ("⚑", "A verdict marked ⚑ sits on a boundary: this name tied on score with another "
          "that received a DIFFERENT verdict, and the alphabetical tie-break, not a "
          "score difference, decided which side of the line each fell on."),
)


# --------------------------------------------------------------------------- #
# REPORT-4 — the cohort's HUMAN description, with the record id kept beside it
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CohortTitle:
    """What a run's cohort is CALLED, and the id it is filed under.

    The title and h1 read "adhoc:507e10cf — 3 lenses". That is the record key doing a job
    it cannot do: it names nothing a reader recognises, and two edits of the same list are
    two different opaque hex strings. An edit FORKS rather than mutating the manifest
    (FUND-UI-2), which is right — but the parent's name was dropped with it, so the report
    could not say what the cohort actually was.

    ``headline`` leads; ``record_id`` stays present and MUTED beside it, everywhere the id
    appears today. Nothing is renamed and nothing is hidden."""

    headline: str
    record_id: str

    def full(self) -> str:
        """Headline with the id appended — for a plain-text surface that cannot mute."""
        return f"{self.headline} ({self.record_id})" if self.record_id else self.headline


def cohort_title(meta: dict, *, n_lenses: int = 0) -> CohortTitle:
    """``"Edited from Growth 40 — 21 names · 3 lenses"`` + ``adhoc:507e10cf``.

    Three shapes, in order of how much the run knows about itself:
      * a SAVED list run unchanged   -> its own display name, its own id;
      * an EDITED saved list         -> "Edited from <list>", filed under the ad-hoc id;
      * a pasted list with no parent -> "Pasted list", filed under the ad-hoc id.
    """
    record_id = str(meta.get("universe_id") or "") or "adhoc"
    name = str(meta.get("universe_name") or "").strip()
    parent = str(meta.get("derived_from") or "").strip()
    size = meta.get("universe_size", 0)

    if name:
        lead = name
    elif parent:
        lead = f"Edited from {parent}"
    else:
        lead = "Pasted list"

    bits = [f"{size} name{'s' if size != 1 else ''}"] if size else []
    if n_lenses:
        bits.append(f"{n_lenses} lens{'es' if n_lenses != 1 else ''}")
    tail = " · ".join(bits)
    return CohortTitle(headline=f"{lead} — {tail}" if tail else lead,
                       record_id=record_id)


def cohort_filename_slug(meta: dict) -> str:
    """The cohort segment of a run's FILENAME, taken from the same description the header
    shows — so a folder of reports reads the way the documents do. Empty (the segment is
    then omitted) rather than inventing a name."""
    name = str(meta.get("universe_name") or "").strip()
    if name:
        return name
    parent = str(meta.get("derived_from") or "").strip()
    return f"Edited from {parent}" if parent else ""
