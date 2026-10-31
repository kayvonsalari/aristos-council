"""JUDGE-PANEL-1 - the BLIND facts pack a judge is given.

The judges must answer from the company's own numbers alone. Anything that carries a lens's
opinion - a verdict, a rank, a badge, an agreement count, a shortlist status, the council's or the
narrator's prose, a lens name - would let a judge copy the answer instead of forming one, and the
whole point of the panel is to find out whether independent judgement adds anything. So the pack
is built from the READINGS (the same ones the company page prints) and nothing else, and then
scanned: a line that still carries a forbidden word is dropped and counted, never sent.

Kept: identity, peer NAMES (no ranks), the financial series, the valuation band as a plain number
against the company's own history, analyst counts and forecasts, debt and cash, the growth record,
the cash runway, the Forensic RAW readings (accruals, Altman Z, F-Score as numbers - not the
clean/doubted label) and the dividend record.

Stripped: every lens verdict, rank and badge, the agreement and shortlist, the council opinion,
the narrator text, recent news headlines (they carry broker language), and any lens name.

Nothing here computes anything; each line is a reading the page already shows, re-worded where the
page's wording is itself a verdict ("strong buy" -> "strongest positive rating").
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[3]

PACK_VERSION = "blind_pack_v1"

# Words that are the lenses' or the council's own vocabulary. Matched as whole words,
# case-insensitively, anywhere in the pack.
VERDICT_TOKENS = ("buy", "hold", "sell")
BADGE_WORDS = ("proven", "promising", "no edge", "untested", "worked against you", "track record",
               "badge", "doubted", "no concern", "shortlist", "shortlisted", "verdict", "lens",
               "lenses", "council", "narrator", "agreement", "vote", "votes", "voted", "priced high",
               "ranked", "rank")
# Lens display names that are ordinary English words. They are checked by the unit test and by
# the runtime scan everywhere EXCEPT the identity and peer-name sections, where a company can
# legitimately be called "... Financials Inc".
GENERIC_LENS_NAMES = ("Growth", "Financials", "Forensic")

_IDENTITY_SECTIONS = ("Company", "Peers")
NEWS_SECTION = "Recent news (third-party headlines and openings)"
STATEMENT_TITLES = {"income": "Income statement", "balance": "Balance sheet",
                    "cashflow": "Cash flow statement"}


class BlindPackError(ValueError):
    """The pack could not be made blind."""


def _lens_names(strategies_dir: Optional[Path] = None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(distinctive, generic)`` lens names read off the strategy files: ids and display names.

    Derived from the YAML, never hardcoded, so a new lens is covered the day it is added."""
    import yaml

    folder = Path(strategies_dir) if strategies_dir else _ROOT / "strategies"
    distinctive: set[str] = set()
    generic: set[str] = set(GENERIC_LENS_NAMES)
    for path in sorted(folder.glob("*.yaml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:                                   # noqa: BLE001 - a bad file hides no lens
            continue
        if not isinstance(doc, dict):
            continue
        for key in ("id", "strategy_id"):
            if doc.get(key):
                distinctive.add(str(doc[key]))
        for key in ("name", "display_name"):
            value = str(doc.get(key) or "").strip()
            if not value:
                continue
            (distinctive if (" " in value or "+" in value) else generic).add(value)
    distinctive.discard("")
    return tuple(sorted(distinctive)), tuple(sorted(generic))


def find_leaks(text: str, *, strategies_dir: Optional[Path] = None,
               check_generic_names: bool = True, check_words: bool = True) -> list[str]:
    """Every forbidden word or lens name still in ``text`` (empty = blind). ``check_words=False``
    is for third-party text (news): a headline may say "buy zone", and that is the world talking,
    not Aristos - but it may still never carry one of OUR lens names."""
    leaks: list[str] = []
    for word in (VERDICT_TOKENS + BADGE_WORDS) if check_words else ():
        if re.search(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", text, re.I):
            leaks.append(word)
    distinctive, generic = _lens_names(strategies_dir)
    for name in distinctive:
        if name in text:
            leaks.append(name)
    if check_generic_names:
        for name in generic:
            if re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", text):
                leaks.append(name)
    return leaks


# --------------------------------------------------------------------------- #
# the pack
# --------------------------------------------------------------------------- #
@dataclass
class BlindPack:
    sections: list = field(default_factory=list)        # [(title, [line, ...]), ...]
    dropped: list = field(default_factory=list)         # lines withheld because they leaked
    version: str = PACK_VERSION

    def text(self) -> str:
        out: list[str] = []
        for title, lines in self.sections:
            if not lines:
                continue
            out.append(title.upper())
            out.extend(f"- {ln}" for ln in lines)
            out.append("")
        return "\n".join(out).strip()

    @property
    def pack_hash(self) -> str:
        """12 hex characters of the text, so a log row says exactly which facts were judged."""
        return hashlib.sha256(self.text().encode("utf-8")).hexdigest()[:12]

    @property
    def thin(self) -> bool:
        return sum(len(lines) for _, lines in self.sections) < 6


_BAND_DESCRIPTOR = re.compile(r"\s*\((?:dearest|cheapest)\)", re.I)
_RANK_TAIL = re.compile(r",?\s*rank\s+\d+\s+of\s+\d+", re.I)
_IMPUTED_NOTE = re.compile(r"\s*\(the factor is missing[^)]*\)", re.I)
_DIRECTION_NOTE = re.compile(r"\s*\((?:low|high)[^)]*(?:best|better|good)[^)]*\)", re.I)


def _clean(line: str) -> str:
    return re.sub(r"\s+", " ", str(line or "")).strip()


def _readings(*readings) -> list[str]:
    out: list[str] = []
    for r in readings:
        try:
            if getattr(r, "available", False):
                out.append(_clean(r.text()))
        except Exception:                                   # noqa: BLE001 - one bad reading hides no other
            continue
    return out


def _identity(report) -> list[str]:
    check = report.check
    group = getattr(check, "peer_group", None)
    subject = getattr(group, "subject", None) if group is not None else None
    lines = [f"Name: {report.display}", f"Ticker: {report.ticker}"]
    if subject is not None:
        from ..tools.price_context import format_money
        for label, value in (("Exchange", getattr(subject, "exchange", "")),
                             ("Country", getattr(subject, "country", "")),
                             ("Sector", getattr(subject, "sector", "") or getattr(subject, "gics_sector", "")),
                             ("Industry", getattr(subject, "industry", "") or getattr(subject, "gics_industry", ""))):
            if value:
                lines.append(f"{label}: {value}")
        cap = getattr(subject, "market_cap", None)
        ccy = getattr(subject, "currency", "") or ""
        if cap:
            lines.append(f"Market capitalisation: {format_money(cap, ccy, abbreviate=True)}")
        cap_usd = getattr(subject, "market_cap_usd", None)
        if cap_usd and ccy != "USD":
            lines.append(f"Market capitalisation in US dollars: {format_money(cap_usd, 'USD', abbreviate=True)}")
    return [_clean(ln) for ln in lines]


def _peers(report, limit: int = 40) -> list[str]:
    """Peer NAMES with market cap and sub-industry - never a rank."""
    from ..tools.price_context import format_money

    group = getattr(report.check, "peer_group", None)
    if group is None or not getattr(group, "available", False):
        return []
    out = []
    for m in list(group.members)[:limit]:
        name = _clean(getattr(m, "name", "") or getattr(m, "yahoo_ticker", "") or getattr(m, "ticker", ""))
        if not name:
            continue
        cap = getattr(m, "market_cap_usd", None)
        sub = _clean(getattr(m, "gics_subindustry", "") or getattr(m, "industry", ""))
        bits = [f"market cap {format_money(cap, 'USD', abbreviate=True)}" if cap else "", sub]
        out.append(name + (" - " + "; ".join(b for b in bits if b) if any(bits) else ""))
    return out


def _price(report) -> list[str]:
    pac = getattr(report.check, "price_and_cash", None)
    if pac is None:
        return []
    return _readings(pac.last_close, pac.sma_50, pac.sma_200, pac.pct_off_high, pac.return_6m,
                     pac.return_12m, pac.volatility, pac.fcf_series, pac.trailing_eps,
                     pac.trailing_pe, pac.forward_pe_this_year, pac.forward_pe_next_year)


# BANK-PACK-1: for a bank or insurer the cash-flow measures describe deposits and loans moving, not
# the business earning cash (live: OpenAI made "Operating cash flow was -$147.8bn in FY2025" its
# driving fact on JPM). The page replaces free cash flow with a sentence; the pack does the same for
# every such line - the line is KEPT, its figures replaced by the page's own words, so a judge reads
# why there is no number rather than guessing at a gap. The test is the page's own
# (``abs_readings.is_financial_sector``), so the page and the pack cannot disagree.
_BANK_CASH_FLOW_LINES = ("operating cash flow", "operating activities", "free cash flow",
                         "capital expenditure", "working capital")


def _is_financial(fundamentals) -> bool:
    from ..abs_readings import is_financial_sector
    return is_financial_sector(fundamentals)


def _bank_cash_flow_line(label: str) -> bool:
    low = label.lower()
    return any(w in low for w in _BANK_CASH_FLOW_LINES)


def _not_meaningful_line(label: str) -> str:
    from ..abs_readings import NOT_MEANINGFUL_FOR_FINANCIALS
    return f"{label}: {NOT_MEANINGFUL_FOR_FINANCIALS}"


def _series(fundamentals) -> list[str]:
    """The annual series the page's readings are built from, oldest first, each figure wearing its
    year and its currency. Order and years come from ``series_pack`` (FACTS-ORDER-1); the figures
    are formatted here because that module's own renderer rounds to whole units, which would turn
    a per-share figure of 2.19 into "2"."""
    if fundamentals is None:
        return []
    from ..series_pack import ORDER_OLDEST_FIRST, pack_series, packed_ok
    from ..tools.price_context import format_money, format_money_series

    ccy = getattr(fundamentals, "financial_currency", None) or getattr(fundamentals, "currency", None) or None
    out: list[str] = []
    for fld, label, per_share in (("total_revenue", "Revenue", False), ("net_income", "Net income", False),
                                  ("diluted_eps", "Diluted earnings per share", True),
                                  ("operating_cash_flow_annual", "Operating cash flow", False),
                                  ("free_cash_flow_annual", "Free cash flow", False)):
        if _is_financial(fundamentals) and _bank_cash_flow_line(label):
            out.append(_not_meaningful_line(label))
            continue
        packed = pack_series(fundamentals, fld, label=label, order=ORDER_OLDEST_FIRST)
        if not packed_ok(packed):
            continue
        if per_share:
            shown = [format_money(v, ccy, decimals=2) for v in packed["values"]]
        else:
            shown = format_money_series(packed["values"], ccy)             # FCF-UNITS-1: one unit scale
        cells = [f"{y} {m}" for y, m in zip(packed["years"], shown)]
        out.append(f"{label} (oldest first, each figure labelled with its year): " + " · ".join(cells))
    return out


def _valuation(report) -> list[str]:
    band = getattr(report.check, "valuation_band", "") or ""
    if not band or band.strip() == "—":
        return []
    out = [_BAND_DESCRIPTOR.sub("", _clean(band))]
    pct = getattr(report.check, "band_percentile", None)
    if pct is not None:
        out.append(f"Percentile of its own five-year range: {pct:.0f} (0 = cheapest it has been, 100 = dearest)")
        if "latest annual net income" in band:
            # SMALL-WORDING-1(d): JPM's judges set this band's P/E (15.7) against the trailing P/E (14.1) as if
            # they were one measure. The pack says, beside the percentile, which earnings the band divides by.
            out.append("That percentile is of a P/E on the latest ANNUAL net income, not the trailing twelve "
                       "months, so it is a different figure from the trailing P/E stated elsewhere in this pack.")
    return out


_RATING_WORDS = ("strongest positive", "positive", "neutral", "negative", "strongest negative")


def _analysts(report) -> list[str]:
    trend = getattr(report.check, "analyst_trend", None)
    if trend is None:
        return []
    out: list[str] = []
    ratings = getattr(trend, "ratings", None)
    if ratings is not None and getattr(ratings, "available", False):
        counts = ", ".join(f"{n} {w}" for n, w in zip(ratings.counts, _RATING_WORDS))
        out.append(f"{ratings.total} analysts rate it: {counts}")
        out.append(_clean(ratings.target_sentence))
    out += [_clean(s) for s in trend.forecast_sentences()]
    return out


def _debt_cash(report, fundamentals=None) -> list[str]:
    """The page's debt-and-cash readings. PACK-NETDEBT-1 dated the net-debt figure; NET-DEBT-1 states
    total debt, cash and short-term investments, and net debt as separate lines, EACH with the balance
    sheet's date, under the one definition the page uses (``abs_readings.NET_DEBT_DEFINITION``). Live:
    NVCR's judges saw "cash and investments $447.7m" and "net cash $207.1m" with no debt figure and
    could not reconcile them; MSFT's saw the provider's own "Net Debt" line ($19.4bn) beside the page's
    $52.0bn. That provider line is dropped from the pack's balance sheet (``_statement``)."""
    debt = getattr(report.check, "debt_and_cash", None)
    if debt is None:
        return []
    from ..abs_readings import NET_DEBT_DEFINITION, _balance_sheet_date
    when = _balance_sheet_date(fundamentals)
    dated = {r.text() for r in (debt.net_debt, debt.total_debt, debt.cash, debt.leases)
             if getattr(r, "available", False)}
    lines = [(f"{_clean(x)} ({when})" if x in dated and "balance sheet" not in x else _clean(x))
             for x in list(debt.lines())]
    notes = [_clean(x) for x in list(debt.notes())]
    if dated:
        notes.append(f"Net debt here is {NET_DEBT_DEFINITION}. The balance sheet above is at each "
                     f"fiscal year-end; the provider's own 'Net Debt' line, which leaves out short-term "
                     f"investments, is not shown.")
    return lines + notes


# NET-DEBT-1 - balance-sheet lines left out of the pack because they define net debt differently from
# the page (see ``_debt_cash``).
_DROPPED_STATEMENT_LINES = {"balance": {"net debt"}}


def _growth(report) -> list[str]:
    rec = getattr(report.check, "growth_record", None)
    return [_clean(x) for x in (list(rec.lines()) + list(rec.notes()))] if rec is not None else []


def _forensic_raw(report) -> list[str]:
    """The check lens's own factors as VALUES: no rank, no 'of N', no clean/doubted word."""
    out: list[str] = []
    for v in getattr(report, "votes", []) or []:
        if getattr(v, "kind", "") != "check":
            continue
        for line in getattr(v, "components", ()) or ():
            line = _IMPUTED_NOTE.sub("", _RANK_TAIL.sub("", str(line)))
            line = _DIRECTION_NOTE.sub("", line)
            out.append(_clean(line))
    return out


def _dividends(fundamentals) -> list[str]:
    if fundamentals is None:
        return []
    out: list[str] = []
    dy = getattr(fundamentals, "dividend_yield", None)
    dps = getattr(fundamentals, "dividend_per_share", None)
    po = getattr(fundamentals, "payout_ratio", None)
    if dy is not None:
        out.append(f"Dividend yield: {dy * 100:.2f}%")
    if dps is not None:
        out.append(f"Dividend per share (last twelve months): {dps:g}")
    if po is not None:
        out.append(f"Payout ratio: {po * 100:.0f}% of earnings")
    streak = getattr(fundamentals, "dividend_streak_years", None)
    if streak:
        is_floor = getattr(fundamentals, "dividend_streak_is_floor", None)
        start = getattr(fundamentals, "dividend_history_start_year", None)
        if is_floor is False:                    # DIV-STREAK-1: the run ended at a flat or cut year
            out.append(f"Dividend raised in each of the last {streak} years (regular dividend, "
                       f"specials left out)")
        elif is_floor and start:
            out.append(f"Dividend raised in each of at least the last {streak} years (history on "
                       f"file starts {start}); the true run may be longer")
        else:
            out.append(f"Dividend raised in each of the last {streak} years on file (a floor: the "
                       f"history on file is limited, so the true run may be longer)")
    cut = getattr(fundamentals, "last_dividend_reduction_year", None)
    if cut:
        out.append(f"Most recent year the regular dividend was cut: {cut}")
    # DIV-CUT-SPECIAL-1: supplemental / special payments, labelled apart from the regular dividend.
    from ..tools.screening import inferred_special_dividends
    stats = {int(y): (t, m, int(n)) for y, t, m, n in
             (getattr(fundamentals, "dividend_year_stats", None) or [])}
    specials = inferred_special_dividends(stats)
    if specials:
        shown = ", ".join(f"{y} +{amt:.2f}" for y, amt in specials.items())
        out.append(f"Supplemental or special payments above the regular dividend (inferred from the "
                   f"payment record, which does not label them; per share): {shown}. They are left out of "
                   f"the cut test for the regular dividend.")
    return out


_COUNT_WORDS = ("shares", "share issued", "number")
_SMALL_WORDS = ("eps", "rate", "per share")


def _fmt_statement_value(line: str, v, ccy) -> str:
    """A statement cell: a share COUNT is abbreviated with no currency, a per-share figure or a rate
    keeps its decimals, and any other line is money in the statements' currency."""
    from ..tools.price_context import format_money

    low = line.lower()
    if any(w in low for w in _COUNT_WORDS):
        for cut, suffix in ((1e12, "tn"), (1e9, "bn"), (1e6, "m")):
            if abs(v) >= cut:
                return f"{v / cut:,.2f}{suffix}"
        return f"{v:,.0f}"
    if any(w in low for w in _SMALL_WORDS) and abs(v) < 1000:
        return f"{v:,.2f}"
    return format_money(v, ccy, abbreviate=True)


def _statement(statements, key: str, ccy, *, financial: bool = False) -> list[str]:
    """One statement, every line item the provider gives, oldest period first. For a bank or
    insurer (``financial``) the cash-flow statement's operating-cash-flow, free-cash-flow, capex and
    working-capital lines carry the page's not-meaningful sentence in place of their figures."""
    block = (statements or {}).get(key) or {}
    periods, rows = block.get("periods") or [], block.get("rows") or {}
    if not rows:
        return []
    head = "fiscal years ending " + ", ".join(periods) + " (oldest first" + (f"; {ccy}" if ccy else "") + ")"
    out = [head]
    for line, vals in rows.items():
        if line.strip().lower() in _DROPPED_STATEMENT_LINES.get(key, ()):
            continue
        if financial and key == "cashflow" and _bank_cash_flow_line(str(line)):
            out.append(_not_meaningful_line(str(line)))
            continue
        cells = [(_fmt_statement_value(line, v, ccy) if v is not None else "n/a") for v in vals]
        out.append(f"{line}: " + " | ".join(cells))
    return out


def _news(news, report) -> list[str]:
    """Every recent headline: date, source, title and - when the provider gave one - the opening of
    the article. Falls back to the page's own (fewer, snippet-less) headlines."""
    items = list(news or [])
    if not items:
        pac = getattr(report.check, "price_and_cash", None)
        for it in getattr(pac, "news", ()) or ():
            items.append({"date": it.published.isoformat(), "title": it.headline,
                          "source": getattr(it, "source", ""), "snippet": ""})
    out = []
    for it in items:
        line = f"{it['date']} [{it.get('source') or 'news'}] {_clean(it['title'])}"
        if it.get("snippet"):
            line += f" - {_clean(it['snippet'])}"
        out.append(line)
    return out


def build_blind_pack(report, fundamentals=None, statements=None, news=None, *,
                     strategies_dir: Optional[Path] = None) -> BlindPack:
    """The pack for ONE company report. ``fundamentals`` (optional) adds the annual series and
    the dividend record the report itself does not hold; without it those sections are absent
    and the pack says nothing about them. ``statements`` (``evidence.fetch_statements``) adds the full
    annual statements and ``news`` (``evidence.fetch_news``) every recent headline with its opening."""
    ccy = (getattr(fundamentals, "financial_currency", None) or getattr(fundamentals, "currency", None)
           or None) if fundamentals is not None else None
    raw = [
        ("Company", _identity(report)),
        ("Peers", _peers(report)),
        ("Price and trading", _price(report)),
        ("Financial series", _series(fundamentals)),
        ("Income statement", _statement(statements, "income", ccy)),
        ("Balance sheet", _statement(statements, "balance", ccy)),
        ("Cash flow statement", _statement(statements, "cashflow", ccy,
                                           financial=_is_financial(fundamentals))),
        ("Valuation against its own history", _valuation(report)),
        ("What analysts say", _analysts(report)),
        ("Debt and cash", _debt_cash(report, fundamentals)),
        ("Record of expansion", _growth(report)),
        ("Accounting-quality readings (raw values)", _forensic_raw(report)),
        ("Dividend record", _dividends(fundamentals)),
        (NEWS_SECTION, _news(news, report)),
    ]
    pack = BlindPack()
    for title, lines in raw:
        kept: list[str] = []
        for line in lines:
            if not line:
                continue
            third_party = title == NEWS_SECTION
            leaks = find_leaks(line, strategies_dir=strategies_dir,
                               check_generic_names=title not in _IDENTITY_SECTIONS and not third_party,
                               check_words=not third_party)
            if leaks:
                pack.dropped.append((title, line, leaks))
                continue
            kept.append(line)
        pack.sections.append((title, kept))
    return pack
