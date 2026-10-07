"""REPORT-SWEEP-1 - an automatic report checker.

Every defect the owner has found by reading a report was a *shape* that a program can look for:
an internal id where a plain word belonged, "$-147.8bn", "1 name ... are", an unmatched "**", the
same lens called "does not apply" in one section and "clean" in another, a list whose top got BUY
and whose bottom got no SELL, SELL votes missing from the agreement line, a size band printed
backwards. This module is those looks, as pure functions over text and over the structured result,
so a sweep over an awkward set of companies (``tests/test_report_sweep.py``) can run them all on
every change, offline.

Nothing here changes a report. Each rule returns ``Finding``s (empty means clean), and a rule is
named so a failing run says which owner promised to fix it.
"""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass
from typing import Iterable, Optional

# Rule names (stable: the sweep's xfail reasons and the batch notes quote them).
INTERNAL_IDS = "internal ids in reader text"
MONEY_MINUS = "'$-' or '€-' money formatting"
COUNT_GRAMMAR = "count grammar"
UNMATCHED_MARKUP = "unmatched ** or stray underscore"
LENS_TWO_WAYS = "the same lens stated two ways"
BUY_WITHOUT_SELL = "a lens list with BUY at the top and no SELL at the bottom"
SELL_MISSING = "SELL votes missing from the agreement"
BACKWARDS_BAND = "a backwards size band"
STRATEGY_WORD = "'strategy' in reader text (the reader's word is 'lens')"
BADGE_PARAGRAPH = "the price-badge paragraph pasted into a heading"
STORY_WORDS = "an id, a column name or the word 'cohort' in the answer, the story or the lens table"
STORY_FIRST_SCREEN = "the first screen runs past the fold"
FIRST_SCREEN_MAX_LINES = 25
WORDING = "a wording slip the owner already found (Batch 22 patterns)"


@dataclass(frozen=True)
class Finding:
    rule: str
    where: str          # which report / export it was found in
    detail: str         # the offending text or the lens and the two statements

    def __str__(self) -> str:
        return f"[{self.rule}] {self.where}: {self.detail}"


# --------------------------------------------------------------------------- #
# text helpers
# --------------------------------------------------------------------------- #
_TAG = re.compile(r"<[^>]+>")
_SCRIPTISH = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)


def visible_text(html_doc: str) -> str:
    """The text a reader sees in an HTML export: scripts and styles dropped, tags dropped,
    entities decoded, one line per block."""
    body = _SCRIPTISH.sub("", html_doc)
    body = re.sub(r"</(p|div|li|tr|h[1-6]|table|section|header|footer|caption)>", "\n", body,
                  flags=re.I)
    body = re.sub(r"</t[dh]>", " | ", body, flags=re.I)
    body = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
    return _html.unescape(_TAG.sub("", body))


def _lines(text: str) -> Iterable[str]:
    return (ln for ln in text.splitlines() if ln.strip())


# --------------------------------------------------------------------------- #
# 1. internal ids
# --------------------------------------------------------------------------- #
_STRATEGY_ID = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)*_v\d+\b")
_ADHOC_ID = re.compile(r"\badhoc:[0-9a-f]{4,}\b")


def _factor_ids() -> list[str]:
    """Every record key a reader could be shown by mistake: factor ids, criterion ids and cohort
    slugs (those with an underscore - the plain-word ones cannot be told from English)."""
    from pathlib import Path

    from .factors import FACTOR_REGISTRY
    from .tools.criteria.registry import REGISTRY
    names = set(FACTOR_REGISTRY) | set(REGISTRY)
    backtests = Path(__file__).resolve().parents[2] / "backtests"
    if backtests.is_dir():
        names |= {p.name for p in backtests.iterdir() if p.is_dir()}
    return sorted((n for n in names if "_" in n), key=len, reverse=True)


# Column / heading words that are themselves identifiers ("Criterion id").
_ID_HEADINGS = re.compile(r"\b(?:Criterion|Strategy|Lens|Factor|Run|Cohort) ids?\b")


# A factor key printed with an abstention tag ("roic - abstained", "revenue growth - abstained"):
# the would-rank line once listed the record key and the provider's tag instead of plain words.
_ABSTAINED_TAG = re.compile(r"\b[a-z][a-z ]{1,40} - (?:abstained|not computed)\b")
# The progress line the app shows while a multi-lens run grades ("Grading with <id> (1 of 9)").
_PROGRESS_ID = re.compile(r"Grading with [a-z][a-z0-9]*(?:_[a-z0-9]+)+")


def internal_id_findings(text: str, where: str) -> list[Finding]:
    """Strategy ids (``magic_formula_raw_v1``), ad-hoc list ids (``adhoc:3f9a1c2b``) and factor ids
    (``distribution_yield``) in text a reader reads. They are record keys; the reader's word for
    each is its plain name."""
    out: list[Finding] = []
    seen: set = set()
    for pattern in (_STRATEGY_ID, _ADHOC_ID):
        for m in pattern.finditer(text):
            if m.group(0) not in seen:
                seen.add(m.group(0))
                out.append(Finding(INTERNAL_IDS, where, m.group(0)))
    for pattern in (_ABSTAINED_TAG, _PROGRESS_ID):
        for m in pattern.finditer(text):
            if m.group(0) not in seen:
                seen.add(m.group(0))
                out.append(Finding(INTERNAL_IDS, where, m.group(0)))
    for m in _ID_HEADINGS.finditer(text):
        if m.group(0) not in seen:
            seen.add(m.group(0))
            out.append(Finding(INTERNAL_IDS, where, m.group(0)))
    for fid in _factor_ids():
        if fid not in seen and re.search(rf"(?<![\w]){re.escape(fid)}(?![\w])", text):
            seen.add(fid)
            out.append(Finding(INTERNAL_IDS, where, fid))
    return out


# --------------------------------------------------------------------------- #
# 1b. the word "strategy" (19B B3) and the badge paragraph in a heading (19B B10)
# --------------------------------------------------------------------------- #
_STRATEGY = re.compile(r"\bstrateg(?:y|ies)\b", re.I)


def strategy_word_findings(text: str, where: str) -> list[Finding]:
    """The reader's word for what grades a company is "lens". "Strategy" is the file's word."""
    return [Finding(STRATEGY_WORD, where, ln.strip()[:140])
            for ln in _lines(text) if _STRATEGY.search(ln)]


# The opening words of ``glossary.DETAIL_BADGE_NOTE`` (the long form that lives in the glossary).
_BADGE_PARAGRAPH = re.compile(r"cyclical company means one of two things|asks for a human look|"
                              r"not part of the rule the company failed")


def badge_paragraph_findings(text: str, where: str) -> list[Finding]:
    """The long price-badge explanation belongs to the glossary only. Anywhere before the glossary
    heading it is that paragraph pasted into a lens section (it once rode inside a bold heading)."""
    from .glossary import SECTION_TITLE

    cut = text.lower().find(SECTION_TITLE.lower())
    body = text if cut < 0 else text[:cut]
    return [Finding(BADGE_PARAGRAPH, where, ln.strip()[:140])
            for ln in _lines(body) if _BADGE_PARAGRAPH.search(ln)]


# --------------------------------------------------------------------------- #
# 1c. COMPANY-STORY-1: sections 1-3 of a company page (the answer, the story, the lens table)
# --------------------------------------------------------------------------- #
_FOLD = re.compile(r"show the workings", re.I)
_COHORT = re.compile(r"\bcohorts?\b", re.I)
_COLUMN_NAMES = re.compile(r"\b(?:BUY votes|SELL votes|Voted BUY|Voted SELL)\b")


def story_sections(text: str) -> str:
    """Everything above the fold of a company page, in any of its renderings (the text's
    ``SHOW THE WORKINGS``, the HTML's ``<summary>`` once its tags are dropped, the Markdown's
    ``## Show the workings``): the answer, the story, the lens table and, when ticked, the council
    opinion."""
    m = _FOLD.search(text)
    return text if m is None else text[:m.start()]


def story_findings(text: str, where: str) -> list[Finding]:
    """No lens id, no column name and never the word "cohort" in sections 1-3 (and, because
    ``story_word_findings`` below shares the rule, never "strategy" either)."""
    body = story_sections(text)
    out = list(internal_id_findings(body, where)) + list(strategy_word_findings(body, where))
    out += [Finding(STORY_WORDS, where, ln.strip()[:140]) for ln in _lines(body)
            if _COHORT.search(ln) or _COLUMN_NAMES.search(ln)]
    return out


def first_screen_lines(text: str) -> int:
    """Lines of the plain-text report before the fold - the design's "at most 25"."""
    return len(story_sections(text).rstrip("\n").split("\n"))


def first_screen_findings(text: str, where: str) -> list[Finding]:
    n = first_screen_lines(text)
    return ([Finding(STORY_FIRST_SCREEN, where, f"{n} lines before the fold (max "
                                                f"{FIRST_SCREEN_MAX_LINES})")]
            if n > FIRST_SCREEN_MAX_LINES else [])


# --------------------------------------------------------------------------- #
# Batch 22 - wording patterns found by hand (each one is a shape, so each one is a rule)
# --------------------------------------------------------------------------- #
# (pattern, what it was, story_only): a ``story_only`` pattern is looked for above the fold only,
# because the technical wording legitimately lives in the workings.
_WORDING: list[tuple[re.Pattern, str, bool]] = [
    (re.compile(r"\b(?:One|The one) lens did not apply, all for one reason"), "one lens 'all for one reason'", False),
    (re.compile(r"[(]step [0-9] of 4 of the peer search"), "peer-search jargon in the story", True),
    (re.compile(r"[0-9%x][.] [a-z]"), "a sentence starting in lowercase after a figure and a full stop", False),
    (re.compile(r"mean excess|luck [0-9]+%|rounds held"), "backtest jargon (say it in plain words)", False),
    (re.compile(r"tied with 1(?![0-9])"), "'tied with 1' (say 'tied with one other')", False),
    (re.compile(r"What survived[.]"), "'What survived.' heading on a company page", True),
]


def wording_findings(text: str, where: str) -> list[Finding]:
    out: list[Finding] = []
    company_page = "The answer" in text            # story-only patterns are about the company page
    for pat, what, story_only in _WORDING:
        if story_only and not company_page:
            continue
        body = story_sections(text) if story_only else text
        for ln in _lines(body):
            if pat.search(ln):
                out.append(Finding(WORDING, where, f"{what}: {ln.strip()[:120]}"))
                break
    return out


# --------------------------------------------------------------------------- #
# 2. money formatting
# --------------------------------------------------------------------------- #
_MONEY_MINUS = re.compile(r"[$€£¥]-\s?\d")


def money_minus_findings(text: str, where: str) -> list[Finding]:
    return [Finding(MONEY_MINUS, where, m.group(0) + "…")
            for m in _MONEY_MINUS.finditer(text)][:5]


# --------------------------------------------------------------------------- #
# 3. count grammar
# --------------------------------------------------------------------------- #
_GRAMMAR = [
    re.compile(r"\b1 names?\b[^.\n|;,]{0,80}?\b(?:are|were|have)\b"),
    re.compile(r"\b1 of these names are\b"),
    re.compile(r"\w\(s\)"),
    re.compile(r"\w\(ies\)"),
]


def count_grammar_findings(text: str, where: str) -> list[Finding]:
    out: list[Finding] = []
    for ln in _lines(text):
        for pat in _GRAMMAR:
            m = pat.search(ln)
            if m:
                out.append(Finding(COUNT_GRAMMAR, where, ln.strip()[:140]))
                break
    return out[:5]


# --------------------------------------------------------------------------- #
# 4. unmatched markup
# --------------------------------------------------------------------------- #
_OPEN_US = re.compile(r"(?<![\w])_(?=\S)")
_CLOSE_US = re.compile(r"(?<=\S)_(?![\w])")


def markup_findings(text: str, where: str, *, markdown: bool) -> list[Finding]:
    """An odd number of ``**`` on a line (an unmatched bold), and underscores used as emphasis
    that do not pair up (a stray leading or trailing ``_``). In a visible-text export (HTML or the
    plain text) ANY ``**`` is a leak of markdown into a place that does not render it."""
    out: list[Finding] = []
    for ln in _lines(text):
        stars = ln.count("**")
        if (markdown and stars % 2) or (not markdown and stars):
            out.append(Finding(UNMATCHED_MARKUP, where, ln.strip()[:140]))
            continue
        if len(_OPEN_US.findall(ln)) != len(_CLOSE_US.findall(ln)):
            out.append(Finding(UNMATCHED_MARKUP, where, ln.strip()[:140]))
    return out[:5]


# --------------------------------------------------------------------------- #
# 5. backwards band
# --------------------------------------------------------------------------- #
_BAND = re.compile(r"\$(\d+(?:\.\d+)?)(m|bn|tn)\s?[-–]\s?\$(\d+(?:\.\d+)?)(m|bn|tn)")
_SCALE = {"m": 1e6, "bn": 1e9, "tn": 1e12}


def backwards_band_findings(text: str, where: str) -> list[Finding]:
    out = []
    for m in _BAND.finditer(text):
        lo = float(m.group(1)) * _SCALE[m.group(2)]
        hi = float(m.group(3)) * _SCALE[m.group(4)]
        if lo >= hi:
            out.append(Finding(BACKWARDS_BAND, where, m.group(0)))
    return out


# --------------------------------------------------------------------------- #
# structured rules
# --------------------------------------------------------------------------- #
def _live(ranked) -> list:
    return [r for r in ranked if not getattr(r, "excluded", False)]


def buy_without_sell_findings(label: str, ranked, *, min_names: int = 3,
                              where: str = "") -> list[Finding]:
    """A lens that ranked ``min_names`` or more names, gave its top a BUY, and gave its bottom no
    SELL. The quintile cut is symmetric, so this is a contradiction in the cut itself."""
    live = _live(ranked)
    if len(live) < min_names:
        return []
    verdicts = [(getattr(r, "verdict", "") or "").lower() for r in live]
    if "buy" in verdicts and "sell" not in verdicts:
        return [Finding(BUY_WITHOUT_SELL, where or label,
                        f"{label}: {len(live)} names ranked, {verdicts.count('buy')} BUY, 0 SELL")]
    return []


def company_agreement_findings(report, where: str) -> list[Finding]:
    """Company page: SELL votes must be in the agreement line, and a lens must read the same in
    the vote table, the agreement and the peers table."""
    out: list[Finding] = []
    agreement = getattr(report, "agreement", None)
    if agreement is None:
        return out
    if getattr(agreement, "sell", ()) and "SELL" not in agreement.headline:
        out.append(Finding(SELL_MISSING, where,
                           f"{len(agreement.sell)} SELL vote(s) but the line reads: "
                           f"{agreement.headline!r}"))
    # The same lens stated two ways: the vote table says one thing, the agreement's checks or the
    # peers table's cell for the company says another.
    for vote in getattr(report, "votes", ()):
        applies = bool(vote.ranked)
        if not vote.votes:
            word = (getattr(agreement, "checks", {}) or {}).get(vote.label)
            if word is not None and ((word == "does not apply") == applies):
                out.append(Finding(LENS_TWO_WAYS, where,
                                   f"{vote.label}: the vote table says {vote.result()!r}, the "
                                   f"agreement says {word!r}"))
    return out


def peers_table_findings(report, where: str) -> list[Finding]:
    """The company's own row in the peers table must agree with its vote for every lens."""
    from .peer_table import DOES_NOT_APPLY, TOO_FEW_TO_RANK, peer_rows, rank_columns

    out: list[Finding] = []
    group = getattr(report, "peer_group", None)
    if group is None or not getattr(group, "available", False):
        return out
    columns = rank_columns(report)
    rows = peer_rows(group, columns, report.ticker)
    mine = next((r for r in rows if r.is_company), None)
    if mine is None:
        return out
    for vote in getattr(report, "votes", ()):
        cell = next((v for h, v in mine.ranks if h.startswith(vote.label)), None)
        if cell is None or cell == TOO_FEW_TO_RANK:
            continue            # no column (nobody ranked), or a column that says "too few to rank"
        says_applies = not (cell == DOES_NOT_APPLY or cell in ("no data", "fetch failed")
                            or cell == "not in this run")
        if vote.ranked != says_applies:
            out.append(Finding(LENS_TWO_WAYS, where,
                               f"{vote.label}: the vote table says {vote.result()!r}, the peers "
                               f"table's row for the company says {cell!r}"))
    return out


def list_agreement_findings(multi, where: str) -> list[Finding]:
    """List runs: every name a voting lens rated SELL must show a SELL vote in the agreement
    table, and a lens that the grid says did not rank a name must not be counted as a voter."""
    from .pipeline import lens_agreement_table

    out: list[Finding] = []
    ag = getattr(multi, "lens_agreement", None)
    if ag is None or not getattr(ag, "available", False):
        return out
    _cols, rows = lens_agreement_table(ag)
    for row, cells in zip(ag.rows, rows):
        if row.sell_votes and cells.get("SELL votes", "—") == "—":
            out.append(Finding(SELL_MISSING, where, f"{row.display}: SELL vote not shown"))
        for label, _why in row.not_ranked:
            if label in row.buy_lenses or label in row.sell_lenses or label in row.hold_lenses:
                out.append(Finding(LENS_TWO_WAYS, where,
                                   f"{row.display}: {label} is both a vote and 'did not apply'"))
    return out
