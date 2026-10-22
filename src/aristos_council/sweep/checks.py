"""SWEEP-1 - the REPORT-SWEEP-1 checks (approved 2026-10-04) as a sweep runs them.

``report_sweep`` holds the pure rules that tests/test_report_sweep.py replays offline. This module is
the same idea at sweep scale: every rule takes the text of one Markdown report (or the structured
result behind it) and returns ``SweepFinding``s carrying what the owner asked for - entry, section,
line, rule. It changes no report. Rules that already exist in ``report_sweep`` are called, not copied.

Rules over Markdown text (``check_markdown``):
  internal ids or strategy slugs          money "$-" / inconsistent unit scale in one series
  singular/plural slips                    unmatched ** or _
  diagnostic column names in reader text   truncated company names
  a repeated paragraph / sentence          a ticked lens absent from the lens table
Rules over the structured results (``check_structured``):
  the same lens or check stated two ways   SELL votes missing from the agreement
  BUY without a matching SELL at small list sizes
Rules over the judge panel (``check_judges``):
  a judge field starting with a verdict token       an error / incomplete / invalid judge row
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Iterable, Optional

from .. import report_sweep as rs

# Rule names: stable, they head the summary's finding list.
R_INTERNAL_IDS = "internal ids or strategy slugs in reader text"
R_MONEY = "'$-' money formatting"
R_UNIT_SCALE = "inconsistent unit scale within one series"
R_PLURAL = "singular/plural slip"
R_MARKUP = "unmatched ** or _"
R_LENS_TWO_WAYS = "the same lens or check stated two ways"
R_BUY_NO_SELL = "BUY without a matching SELL rule at small list sizes"
R_SELL_MISSING = "SELL votes missing from the agreement line"
R_COLUMN_NAMES = "diagnostic column names in reader text"
R_TRUNCATED = "truncated company name"
R_REPEATED = "a repeated paragraph"
R_LENS_ABSENT = "a ticked lens absent from the lens table"
R_JUDGE_VERDICT = "a judge field starting with a verdict token"
R_JUDGE_ROW = "a judge row that is an error or incomplete"

ALL_RULES = (R_INTERNAL_IDS, R_MONEY, R_UNIT_SCALE, R_PLURAL, R_MARKUP, R_LENS_TWO_WAYS,
             R_BUY_NO_SELL, R_SELL_MISSING, R_COLUMN_NAMES, R_TRUNCATED, R_REPEATED,
             R_LENS_ABSENT, R_JUDGE_VERDICT, R_JUDGE_ROW)

_RS_RULE = {rs.INTERNAL_IDS: R_INTERNAL_IDS, rs.MONEY_MINUS: R_MONEY, rs.COUNT_GRAMMAR: R_PLURAL,
            rs.UNMATCHED_MARKUP: R_MARKUP, rs.LENS_TWO_WAYS: R_LENS_TWO_WAYS,
            rs.BUY_WITHOUT_SELL: R_BUY_NO_SELL, rs.SELL_MISSING: R_SELL_MISSING}


@dataclass(frozen=True)
class SweepFinding:
    entry: str
    file: str            # which report of the entry ("report.md", "judge.json", ...)
    section: str         # the nearest heading above the line ("" when there is none)
    line: int            # 1-based; 0 when the finding is about the whole file
    rule: str
    detail: str

    def row(self) -> str:
        where = f"{self.file}:{self.line}" if self.line else self.file
        return f"{self.entry} | {self.section or '(top)'} | {where} | {self.rule} | {self.detail}"


# --------------------------------------------------------------------------- #
# locating a finding in the text
# --------------------------------------------------------------------------- #
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")


def section_of(lines: list[str], line_no: int) -> str:
    """The text of the nearest Markdown heading at or above 1-based ``line_no``."""
    for i in range(min(line_no, len(lines)) - 1, -1, -1):
        m = _HEADING.match(lines[i])
        if m:
            return m.group(1).strip("* ")
    return ""


def _locate(lines: list[str], snippet: str) -> int:
    probe = re.sub(r"[….]+$", "", (snippet or "").strip())[:60]
    if not probe:
        return 0
    for i, ln in enumerate(lines, 1):
        if probe in ln:
            return i
    return 0


def _finding(entry, file, lines, line_no, rule, detail) -> SweepFinding:
    return SweepFinding(entry, file, section_of(lines, line_no) if line_no else "", line_no, rule,
                        " ".join(str(detail).split())[:200])


# --------------------------------------------------------------------------- #
# tables
# --------------------------------------------------------------------------- #
def _cells(line: str) -> list[str]:
    s = line.strip()
    if not s.startswith("|"):
        return []
    return [c.strip() for c in s.strip("|").split("|")]


def _is_rule_row(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c)


def _table_lines(lines: list[str]) -> set[int]:
    return {i for i, ln in enumerate(lines, 1) if ln.lstrip().startswith("|")}


# --------------------------------------------------------------------------- #
# 1. the existing text rules, located
# --------------------------------------------------------------------------- #
def _existing_text_rules(text: str, entry: str, file: str, lines: list[str]) -> list[SweepFinding]:
    where = f"{entry}/{file}"
    found = (list(rs.internal_id_findings(text, where)) + list(rs.money_minus_findings(text, where))
             + list(rs.count_grammar_findings(text, where))
             + list(rs.markup_findings(text, where, markdown=True)))
    out = []
    for f in found:
        rule = _RS_RULE.get(f.rule, f.rule)
        out.append(_finding(entry, file, lines, _locate(lines, f.detail), rule, f.detail))
    return out


# --------------------------------------------------------------------------- #
# 2. singular / plural beyond what report_sweep looks for
# --------------------------------------------------------------------------- #
_NOUNS = r"(?:name|company|lens|ticker|stock|fund|vote|factor|criterion)"
_PLURALS = r"(?:names|companies|lenses|tickers|stocks|funds|votes|factors|criteria)"
_ONE_PLURAL = re.compile(rf"(?<![\w.,/-])1 {_PLURALS}\b")
_MANY_SINGULAR = re.compile(rf"(?<![\w.,/-])(?:0|[2-9]|[1-9]\d+) {_NOUNS}\b(?!s|-|\w)")


def plural_findings(lines: list[str]) -> list[tuple[int, str]]:
    out = []
    for i, ln in enumerate(lines, 1):
        for pat in (_ONE_PLURAL, _MANY_SINGULAR):
            m = pat.search(ln)
            if m:
                out.append((i, ln.strip()[max(0, m.start() - 20):m.end() + 40]))
                break
    return out[:8]


# --------------------------------------------------------------------------- #
# 3. money: unit scale within one series
# --------------------------------------------------------------------------- #
_MONEY_TOKEN = re.compile(r"[$€£¥]-?\d[\d,]*(?:\.\d+)?\s?(k|m|bn|tn)\b")
_PERIOD = re.compile(r"\b(?:19|20)\d\d\b|\bFY\s?\d|\bQ[1-4]\b|\bTTM\b|\bLTM\b", re.I)


def unit_scale_findings(lines: list[str]) -> list[tuple[int, str]]:
    """A SERIES is a table row whose columns are periods (years, quarters): every money value in
    it must be in one unit. (A column of different companies legitimately mixes $800m and $12bn.)"""
    out: list[tuple[int, str]] = []
    header: list[str] = []
    for i, ln in enumerate(lines, 1):
        cells = _cells(ln)
        if not cells:
            header = []
            continue
        if _is_rule_row(cells):
            continue
        if not header:
            header = cells
            continue
        if sum(1 for c in header[1:] if _PERIOD.search(c)) < 2:
            continue
        units = {m.group(1) for c in cells[1:] for m in [_MONEY_TOKEN.search(c)] if m}
        if len(units) > 1:
            out.append((i, f"{cells[0]}: " + ", ".join(sorted(units)) + " in one row"))
    return out


# --------------------------------------------------------------------------- #
# 4. diagnostic column names in reader text
# --------------------------------------------------------------------------- #
# Names of table columns and record fields. They belong in a table header or the workings, never in
# a sentence a reader reads. (A header row itself is skipped: it is where the names live.)
_COLUMN_WORDS = ("BUY votes", "SELL votes", "HOLD votes", "Voted BUY", "Voted SELL", "Voted HOLD",
                 "Combined rank", "combined_rank", "Rank-sum", "rank_sum", "Factor ranks",
                 "factor_rank", "Imputed", "is_gating", "NOT-EVAL", "Criterion id", "Strategy id")
_COLUMN_RE = re.compile("|".join(re.escape(w) for w in _COLUMN_WORDS))


def column_name_findings(lines: list[str]) -> list[tuple[int, str]]:
    out = []
    for i, ln in enumerate(lines, 1):
        if ln.lstrip().startswith(("|", "#")):
            continue
        m = _COLUMN_RE.search(ln)
        if m:
            out.append((i, f"'{m.group(0)}' in: {ln.strip()[:100]}"))
    return out[:8]


# --------------------------------------------------------------------------- #
# 5. truncated company names
# --------------------------------------------------------------------------- #
_DANGLING = re.compile(r"(?:\s&|\s(?:and|of|the|The)|[A-Za-z]-|…|\.\.\.)$")


def truncated_name_findings(lines: list[str], names: Iterable[str] = ()) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for i, ln in enumerate(lines, 1):
        for c in _cells(ln):
            if 2 <= len(c) <= 45 and _DANGLING.search(c.strip("* ")):
                out.append((i, f"cell ends mid-name: {c!r}"))
    for full in {n.strip() for n in names if n and len(n.split()) >= 3}:
        words = full.split()
        prefix = " ".join(words[:-1])
        if len(prefix.split()) < 2:
            continue
        pat = re.compile(re.escape(prefix) + r"(?!\s+" + re.escape(words[-1]) + r")(?![\w-])")
        for i, ln in enumerate(lines, 1):
            m = pat.search(ln)
            if m:
                out.append((i, f"{full!r} appears as {prefix!r}"))
                break
    return out[:8]


# --------------------------------------------------------------------------- #
# 6. a repeated paragraph / sentence
# --------------------------------------------------------------------------- #
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(])")
_LENS_NOTE = re.compile(r"(did not apply|track record|would have ranked|was doubted)\b", re.I)


def _norm(s: str) -> str:
    return re.sub(r"[\s*_`>-]+", " ", s).strip().lower()


def repeated_paragraph_findings(lines: list[str]) -> list[tuple[int, str]]:
    """The same paragraph under two different headings, or the same sentence in two different lens
    notes ("Magic Formula RAW track record: ...", "Quality track record: ...")."""
    out: list[tuple[int, str]] = []
    # paragraphs under headings
    seen: dict[str, tuple[int, str]] = {}
    heading, para, start = "", [], 0

    def flush():
        nonlocal para
        text = _norm(" ".join(para))
        if len(text) >= 60:
            if text in seen and seen[text][1] != heading:
                out.append((start, f"the same paragraph under '{seen[text][1]}' and '{heading}': "
                                   f"{text[:80]}"))
            else:
                seen.setdefault(text, (start, heading))
        para = []

    for i, ln in enumerate(lines, 1):
        m = _HEADING.match(ln)
        if m:
            flush()
            heading = m.group(1).strip("* ")
        elif not ln.strip() or ln.lstrip().startswith("|"):
            flush()
        else:
            if not para:
                start = i
            para.append(ln.strip())
    flush()
    # sentences inside lens notes
    first: dict[str, int] = {}
    for i, ln in enumerate(lines, 1):
        if ln.lstrip().startswith(("|", "#")) or not _LENS_NOTE.search(ln):
            continue
        for sent in _SENTENCE.split(ln.strip().lstrip("-*> ")):
            n = _norm(sent)
            if len(n) < 40:
                continue
            if n in first and first[n] != i:
                out.append((i, f"the same sentence in two lens notes (lines {first[n]} and {i}): "
                               f"{n[:80]}"))
            else:
                first.setdefault(n, i)
    return out[:8]


# --------------------------------------------------------------------------- #
# 7. a ticked lens absent from the lens table
# --------------------------------------------------------------------------- #
def lens_absent_findings(lines: list[str], ticked: Iterable[str]) -> list[tuple[int, str]]:
    tab = [ln.lower() for ln in lines if ln.lstrip().startswith("|")]
    out = []
    for label in ticked:
        if label and not any(label.lower() in ln for ln in tab):
            out.append((0, f"'{label}' was ticked but is in no table row or column"))
    return out


# --------------------------------------------------------------------------- #
# the Markdown entry point
# --------------------------------------------------------------------------- #
def check_markdown(text: str, *, entry: str, file: str = "report.md", ticked: Iterable[str] = (),
                   names: Iterable[str] = ()) -> list[SweepFinding]:
    lines = text.splitlines()
    out = _existing_text_rules(text, entry, file, lines)
    for rule, hits in ((R_PLURAL, plural_findings(lines)),
                       (R_UNIT_SCALE, unit_scale_findings(lines)),
                       (R_COLUMN_NAMES, column_name_findings(lines)),
                       (R_TRUNCATED, truncated_name_findings(lines, names)),
                       (R_REPEATED, repeated_paragraph_findings(lines)),
                       (R_LENS_ABSENT, lens_absent_findings(lines, ticked))):
        out += [_finding(entry, file, lines, n, rule, d) for n, d in hits]
    seen, unique = set(), []
    for f in out:                      # one finding per (rule, line, detail)
        k = (f.rule, f.line, f.detail)
        if k not in seen:
            seen.add(k)
            unique.append(f)
    return unique


# --------------------------------------------------------------------------- #
# structured rules
# --------------------------------------------------------------------------- #
SMALL_LIST_MAX = 4          # a quintile cut needs 5 names (B25-1); below that there is no SELL rule


def buy_without_sell_small(label: str, ranked, *, where: str = "") -> list[rs.Finding]:
    """2-4 live names, a BUY among them, no SELL: the lens had no rule that could ever rate the last
    name SELL, yet it printed a BUY. (``report_sweep.buy_without_sell_findings`` covers 5 and up.)"""
    live = [r for r in ranked if not getattr(r, "excluded", False)]
    if not 2 <= len(live) <= SMALL_LIST_MAX:
        return []
    verdicts = [(getattr(r, "verdict", "") or "").lower() for r in live]
    if "buy" in verdicts and "sell" not in verdicts:
        return [rs.Finding(rs.BUY_WITHOUT_SELL, where or label,
                           f"{label}: only {len(live)} names ranked, {verdicts.count('buy')} BUY, "
                           "0 SELL (no SELL rule at this size)")]
    return []


def _lens_results(objs: dict):
    out = []
    company = objs.get("company")
    if company is not None:
        for sid, record in (getattr(company, "lens_ranks", None) or {}).items():
            rows = [SimpleNamespace(verdict=r.get("verdict", ""), excluded=False)
                    for r in record.get("ranked", [])]
            out.append((sid, rows))
    multi = objs.get("multi")
    if multi is not None:
        for sid, res in multi.results.items():
            out.append((multi.strategy_names.get(sid) or sid, res.ranked))
    single = objs.get("single")
    if single is not None:
        out.append((single.meta.get("rank_strategy_id", "single"), single.ranked))
    return out


def check_structured(objs: dict, *, entry: str) -> list[SweepFinding]:
    """``objs``: {"company": CompanyReport | None, "multi": MultiStrategyResult | None,
    "single": RankPipelineResult | None}."""
    found: list[rs.Finding] = []
    where = entry
    company, multi = objs.get("company"), objs.get("multi")
    if company is not None:
        found += rs.company_agreement_findings(company, where)
        found += rs.peers_table_findings(company, where)
    if multi is not None:
        found += rs.list_agreement_findings(multi, where)
    for label, ranked in _lens_results(objs):
        found += rs.buy_without_sell_findings(label, ranked, where=where)
        found += buy_without_sell_small(label, ranked, where=where)
    return [SweepFinding(entry, "(structured result)", "", 0, _RS_RULE.get(f.rule, f.rule), f.detail)
            for f in found]


# --------------------------------------------------------------------------- #
# the judge panel
# --------------------------------------------------------------------------- #
_VERDICT_START = re.compile(r"^\W*(BUY|HOLD|SELL|ABSTAIN)\b[*_\s]*(?:[:\-–—,.;]|$)")
_JUDGE_FIELDS = ("reason", "driving_fact", "key_risk", "unexplained", "would_change_my_mind")
_BAD_STATUS = ("error", "invalid", "incomplete")


def _get(obj, key, default=""):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def check_judges(panel, *, entry: str) -> list[SweepFinding]:
    """``panel``: the judge panel as a dict (``dataclasses.asdict(PanelResult)``) or the object."""
    if panel is None:
        return []
    f = "judge.json"
    out: list[SweepFinding] = []
    if not _get(panel, "available", True):
        out.append(SweepFinding(entry, f, "panel", 0, R_JUDGE_ROW,
                                f"the panel did not run: {_get(panel, 'note') or 'no reason given'}"))
    for j in _get(panel, "judges", []) or []:
        label, status = _get(j, "label") or _get(j, "family"), _get(j, "status")
        if status in _BAD_STATUS:
            why = _get(j, "note") or "; ".join(
                {str(_get(c, "error") or _get(c, "status")) for c in _get(j, "calls", []) or []
                 if _get(c, "status") != "ok"}) or "no reason recorded"
            out.append(SweepFinding(entry, f, label, 0, R_JUDGE_ROW, f"{label}: {status} - {why}"))
        for n, call in enumerate(_get(j, "calls", []) or [], 1):
            for field_name in _JUDGE_FIELDS:
                val = str(_get(call, field_name) or "")
                m = _VERDICT_START.match(val)
                if m:
                    out.append(SweepFinding(entry, f, label, 0, R_JUDGE_VERDICT,
                                            f"{label} call {n} {field_name} starts with "
                                            f"'{m.group(1)}': {val[:80]}"))
    reading = _get(panel, "reading", None)
    para = str(_get(reading, "paragraph") or "") if reading else ""
    if _VERDICT_START.match(para):
        out.append(SweepFinding(entry, f, "panel reading", 0, R_JUDGE_VERDICT,
                                f"the panel reading starts with a verdict token: {para[:80]}"))
    return out
