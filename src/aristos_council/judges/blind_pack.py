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
               check_generic_names: bool = True) -> list[str]:
    """Every forbidden word or lens name still in ``text`` (empty = blind)."""
    leaks: list[str] = []
    for word in VERDICT_TOKENS + BADGE_WORDS:
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
    group = getattr(report.check, "peer_group", None)
    if group is None or not getattr(group, "available", False):
        return []
    names = []
    for m in list(group.members)[:limit]:
        name = _clean(getattr(m, "name", "") or getattr(m, "yahoo_ticker", "") or getattr(m, "ticker", ""))
        if name:
            names.append(name)
    return [f"Companies of similar kind and size (names only): {'; '.join(names)}"] if names else []


def _price(report) -> list[str]:
    pac = getattr(report.check, "price_and_cash", None)
    if pac is None:
        return []
    return _readings(pac.last_close, pac.sma_50, pac.sma_200, pac.pct_off_high, pac.return_6m,
                     pac.return_12m, pac.volatility, pac.fcf_series, pac.trailing_eps,
                     pac.trailing_pe, pac.forward_pe_this_year, pac.forward_pe_next_year)


def _series(fundamentals) -> list[str]:
    """The annual series the page's readings are built from, oldest first, each figure wearing its
    year and its currency. Order and years come from ``series_pack`` (FACTS-ORDER-1); the figures
    are formatted here because that module's own renderer rounds to whole units, which would turn
    a per-share figure of 2.19 into "2"."""
    if fundamentals is None:
        return []
    from ..series_pack import ORDER_OLDEST_FIRST, pack_series, packed_ok
    from ..tools.price_context import format_money

    ccy = getattr(fundamentals, "financial_currency", None) or getattr(fundamentals, "currency", None) or None
    out: list[str] = []
    for fld, label, per_share in (("total_revenue", "Revenue", False), ("net_income", "Net income", False),
                                  ("diluted_eps", "Diluted earnings per share", True),
                                  ("operating_cash_flow_annual", "Operating cash flow", False),
                                  ("free_cash_flow_annual", "Free cash flow", False)):
        packed = pack_series(fundamentals, fld, label=label, order=ORDER_OLDEST_FIRST)
        if not packed_ok(packed):
            continue
        cells = [f"{y} " + (format_money(v, ccy, decimals=2) if per_share else format_money(v, ccy, abbreviate=True))
                 for y, v in zip(packed["years"], packed["values"])]
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


def _debt_cash(report) -> list[str]:
    debt = getattr(report.check, "debt_and_cash", None)
    return [_clean(x) for x in (list(debt.lines()) + list(debt.notes()))] if debt is not None else []


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
        out.append(f"Dividend raised in each of the last {streak} years on file (a floor: the "
                   f"history on file is limited, so the true run may be longer)")
    cut = getattr(fundamentals, "last_dividend_reduction_year", None)
    if cut:
        out.append(f"Most recent year the dividend was cut: {cut}")
    return out


def build_blind_pack(report, fundamentals=None, *, strategies_dir: Optional[Path] = None) -> BlindPack:
    """The pack for ONE company report. ``fundamentals`` (optional) adds the annual series and
    the dividend record the report itself does not hold; without it those sections are absent
    and the pack says nothing about them."""
    raw = [
        ("Company", _identity(report)),
        ("Peers", _peers(report)),
        ("Price and trading", _price(report)),
        ("Financial series", _series(fundamentals)),
        ("Valuation against its own history", _valuation(report)),
        ("What analysts say", _analysts(report)),
        ("Debt and cash", _debt_cash(report)),
        ("Record of expansion", _growth(report)),
        ("Accounting-quality readings (raw values)", _forensic_raw(report)),
        ("Dividend record", _dividends(fundamentals)),
    ]
    pack = BlindPack()
    for title, lines in raw:
        kept: list[str] = []
        for line in lines:
            if not line:
                continue
            leaks = find_leaks(line, strategies_dir=strategies_dir,
                               check_generic_names=title not in _IDENTITY_SECTIONS)
            if leaks:
                pack.dropped.append((title, line, leaks))
                continue
            kept.append(line)
        pack.sections.append((title, kept))
    return pack
