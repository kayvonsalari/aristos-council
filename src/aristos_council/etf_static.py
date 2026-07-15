"""ETF-STATIC-1 — a dated, committed static layer for slow-moving ETF fields.

Why this exists
---------------
Some ETF fields the lenses rank on (expense ratio, fund size, distribution yield)
are served unevenly — or not at all — by the free vendor, yet they change rarely
and are trivially human-verifiable from a factsheet. This module lets the adapter
FILL those gaps for ETF-kind names from a committed CSV (``data/etf_static.csv``),
with three disciplines that mirror the rest of the codebase:

- **Vendor precedence** — a vendor value that is PRESENT and PLAUSIBLE always wins;
  static only fills what the vendor doesn't serve (or serves implausibly).
- **It shows its work** — every static-sourced number carries a provenance tag
  ``static: <as_of>, <source>`` that flows through the SAME factor-source path the
  FX receipt uses, so the report renders ``[static: <as_of>, <source>]``.
- **No silent stale data** — an entry older than 90 days (or with an unparseable
  ``as_of``) ABSTAINS with "static data stale — refresh required": the field is NOT
  filled, and the staleness note is surfaced. Never served silently.

Replay: the CSV is COMMITTED, so a frozen run replays it byte-identically — the
static data lives in the record's world, exactly like every other frozen input.

The math and decisions here are PURE and offline-testable — no network, no vendor
SDK. ``load_static`` reads the committed file; ``apply_static_fill`` is the pure
merge the adapter path (``factors.gather_factor_inputs``) calls per name.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Optional

# An entry older than this ABSTAINS rather than serving silently.
STALE_AFTER_DAYS = 90

# The staleness abstention note (surfaced on the factor source). Kept as one literal so
# the render sites and the tests agree on it exactly.
STALE_NOTE = "static data stale — refresh required"

# CSV column -> the Fundamentals attribute the static value fills. ``share_class`` and
# ``domicile`` are DESCRIPTIVE metadata carried on the row (no factor reads them), so they
# map to nothing here — only these three numeric fields fill the ETF factor inputs.
STATIC_TO_FUNDAMENTALS: dict[str, str] = {
    "expense_ratio": "net_expense_ratio",
    "fund_size": "total_assets",
    "distribution_yield": "dividend_yield",
}

# The committed static file, resolved relative to the repo root (…/src/aristos_council/
# -> parents[2] == repo root -> data/etf_static.csv). A missing file is tolerated (the
# layer simply does nothing), so a checkout without it never crashes a run.
DEFAULT_STATIC_PATH = Path(__file__).resolve().parents[2] / "data" / "etf_static.csv"


@dataclass(frozen=True)
class StaticRow:
    """One committed, human-verified static entry for an ETF. Numeric fields are None
    when the file leaves the cell blank (nothing to fill); ``share_class``/``domicile``
    are descriptive. ``as_of`` is 'YYYY-MM-DD' — the date the human verified the row,
    which the staleness guard reads."""

    ticker: str
    expense_ratio: Optional[float]
    fund_size: Optional[float]
    distribution_yield: Optional[float]
    share_class: Optional[str]
    domicile: Optional[str]
    source: str
    as_of: str

    @property
    def tag(self) -> str:
        """The provenance receipt, e.g. ``static: 2026-06-01, iShares factsheet`` — the
        report wraps it as ``[static: <as_of>, <source>]`` (the FX-receipt convention)."""
        return f"static: {self.as_of}, {self.source}"


@dataclass(frozen=True)
class StaticFill:
    """The outcome of applying the static layer to ONE name: which Fundamentals fields
    were filled from static (field -> provenance tag) and which were WITHHELD because the
    entry is stale (field -> the staleness note). Both empty for a name the layer didn't
    touch (a stock, or an ETF with no matching row)."""

    filled: dict[str, str]
    stale: dict[str, str]

    @property
    def touched(self) -> bool:
        return bool(self.filled or self.stale)


_EMPTY_FILL = StaticFill(filled={}, stale={})


def _parse_float(raw: Optional[str]) -> Optional[float]:
    raw = (raw or "").strip()
    if raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def load_static(path=DEFAULT_STATIC_PATH) -> dict[str, StaticRow]:
    """Parse the committed ETF static CSV into ``{TICKER: StaticRow}``.

    Comment lines (``# …``) and blank lines are skipped; the first remaining line is the
    header. A missing or unreadable file yields ``{}`` — the static layer then simply
    does nothing, so a run never crashes on its absence. Tickers are upper-cased to match
    the adapter's normalized form."""
    p = Path(path)
    if not p.exists():
        return {}
    with p.open(newline="", encoding="utf-8") as fh:
        data_lines = [ln for ln in fh
                      if ln.strip() and not ln.lstrip().startswith("#")]
    rows: dict[str, StaticRow] = {}
    for rec in csv.DictReader(data_lines):
        ticker = (rec.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        rows[ticker] = StaticRow(
            ticker=ticker,
            expense_ratio=_parse_float(rec.get("expense_ratio")),
            fund_size=_parse_float(rec.get("fund_size")),
            distribution_yield=_parse_float(rec.get("distribution_yield")),
            share_class=(rec.get("share_class") or "").strip() or None,
            domicile=(rec.get("domicile") or "").strip() or None,
            source=(rec.get("source") or "").strip(),
            as_of=(rec.get("as_of") or "").strip())
    return rows


_DEFAULT_ROWS: Optional[dict[str, StaticRow]] = None


def default_static_rows() -> dict[str, StaticRow]:
    """The committed static rows, loaded once and cached. Injected explicitly in tests;
    the adapter path uses this when no rows are passed."""
    global _DEFAULT_ROWS
    if _DEFAULT_ROWS is None:
        _DEFAULT_ROWS = load_static()
    return _DEFAULT_ROWS


def _as_of_date(as_of: str) -> Optional[date]:
    try:
        return date.fromisoformat((as_of or "").strip())
    except (ValueError, TypeError):
        return None


def is_stale(row: StaticRow, today: date, *, max_age_days: int = STALE_AFTER_DAYS) -> bool:
    """Is a static entry too old to serve? True when ``as_of`` is more than
    ``max_age_days`` before ``today``, OR when ``as_of`` can't be parsed — an
    unverifiable freshness can't be trusted fresh, so it abstains too."""
    d = _as_of_date(row.as_of)
    if d is None:
        return True
    return (today - d).days > max_age_days


def _vendor_plausible(fund_field: str, value) -> bool:
    """Does the VENDOR already serve a usable value for this ETF field? Vendor wins when
    present and plausible; a missing or implausible vendor value yields to static.

    Plausibility is deliberately loose (the lens ranks these RELATIVELY, so units don't
    matter) — it only rejects values that can't be real: a non-positive expense ratio or
    fund size, and a distribution yield outside ``[0, 1]`` (a decimal; the >100% unit
    slip is already caught upstream by ``sane_dividend_yield``)."""
    if value is None:
        return False
    if fund_field in ("net_expense_ratio", "total_assets"):
        return value > 0
    if fund_field == "dividend_yield":
        return 0 <= value <= 1.0
    return True


def apply_static_fill(fundamentals, *, kind: Optional[str], row: Optional[StaticRow],
                      today: date, max_age_days: int = STALE_AFTER_DAYS):
    """Fill ETF factor fields from the committed static layer — the pure merge.

    Returns ``(fundamentals, StaticFill)``. ONLY an ETF-kind name (``kind == "etf"``)
    with a matching static ``row`` is touched: a stock, or an ETF with no row, is
    returned UNCHANGED with an empty fill — so a stock-kind name never reads the static
    layer. For each mappable field: the vendor value wins where present and plausible; a
    missing/implausible field is filled from static and tagged; but if the entry is STALE
    the field is NOT filled and the staleness note is recorded instead (never served
    silently). ``fundamentals`` is a frozen dataclass, so filled values are applied via
    ``replace`` (a new instance) — the input is never mutated."""
    if fundamentals is None or kind != "etf" or row is None:
        return fundamentals, _EMPTY_FILL
    stale = is_stale(row, today, max_age_days=max_age_days)
    filled: dict[str, str] = {}
    stale_fields: dict[str, str] = {}
    updates: dict[str, float] = {}
    for csv_field, fund_field in STATIC_TO_FUNDAMENTALS.items():
        static_value = getattr(row, csv_field)
        if static_value is None:
            continue                                      # static has nothing here
        if _vendor_plausible(fund_field, getattr(fundamentals, fund_field, None)):
            continue                                      # vendor present & plausible wins
        if stale:
            stale_fields[fund_field] = STALE_NOTE         # never serve stale silently
            continue
        updates[fund_field] = static_value
        filled[fund_field] = row.tag
    if updates:
        fundamentals = replace(fundamentals, **updates)
    return fundamentals, StaticFill(filled=filled, stale=stale_fields)
