"""ETFCORE-2 ITEM 2 — generate `data/etf_static.csv` rows for a universe from EODHD.

Permanence for the static layer. The slow ETF fields (expense ratio, fund size,
distribution yield, share class, domicile) were first assembled in a Colab notebook against
the EODHD fundamentals API; that notebook was ephemeral. This script is the committed,
runnable successor — same shape, same accumulated sanity guards — so a NEW ETF universe can
be turned into paste-ready static rows without rediscovering the guards each time.

    EODHD_API_KEY=... python scripts/generate_etf_static_rows.py <universe_id>

It loads the named universe (``universes/<universe_id>.yaml``), fetches each ticker's
``/fundamentals`` payload from EODHD, and prints CSV rows to STDOUT in the
``data/etf_static.csv`` column order — ready to review and paste under that file's header via
an issue/PR (rows are NEVER written to disk here; a human verifies and commits them). Status
and per-ticker failures go to STDERR so a redirect of stdout captures only the rows.

Accumulated sanity guards (learned live; see CLAUDE.md rule 3 and the CNDX incident):

- **Fee fake-zero skip.** EODHD's ``NetExpenseRatio`` comes back ``0.0000`` for a whole
  class of UCITS funds — a fake zero, not a free fund. The fee is read from
  ``ETF_Data.Ongoing_Charge`` ONLY; ``NetExpenseRatio`` is never trusted. If Ongoing_Charge
  is itself missing/zero the fee cell is left BLANK with a note (never a phantom 0%).
- **Implausible fund size blanked.** A TotalAssets outside ``[1e7, 1.5e12]`` can't be real
  for one of these funds (the CNDX 270B lesson — a mis-scaled AUM). It is blanked with a
  note rather than served; the fund still ranks on its other fields.
- **Yield percent -> fraction.** ETF_Data.Yield is a PERCENT (``4.63`` == 4.63%); the CSV
  stores a DECIMAL (``0.0463``), so the value is divided by 100.
- **Dist/acc inferred from yield.** A positive distribution yield -> ``dist``; a true zero
  (accumulating funds reinvest) -> ``acc``. A missing yield leaves share_class blank
  (omit, never invent).
- **Domicile from ETF_Data.** ``ETF_Data.Domicile`` (a country name) is mapped to the
  two-letter code the CSV already uses (Ireland -> IE, ...); an unrecognized value passes
  through verbatim.

Pure by design: ``build_static_row`` / ``format_row`` take a parsed payload and an ``as_of``
date and do NO network, so they are unit-tested offline (tests/test_generate_etf_static_rows.py).
Only ``fetch_payload`` / ``main`` touch the network.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from aristos_council.data.adapter import normalize_ticker
from aristos_council.universe import load_universe_by_id

ROOT = Path(__file__).resolve().parents[1]
UNIVERSES_DIR = ROOT / "universes"
_BASE_URL = "https://eodhd.com/api"

# The CSV column order, kept in lock-step with data/etf_static.csv's header.
COLUMNS = ["ticker", "expense_ratio", "fund_size", "distribution_yield",
           "share_class", "domicile", "source", "as_of"]

SOURCE_BASE = "EODHD fundamentals API"

# Fund-size plausibility window (net assets, in reporting currency). Below 1e7 is too small
# to be one of these broad-market funds; above 1.5e12 exceeds the largest real ETF several
# times over and signals a mis-scaled value (the CNDX 270B incident).
SIZE_MIN = 1e7
SIZE_MAX = 1.5e12

# EODHD ETF_Data.Domicile is a country name; the CSV uses a short code. Unrecognized names
# pass through verbatim (omit-don't-invent — a new domicile is surfaced raw, not dropped).
_DOMICILE_CODES = {
    "ireland": "IE",
    "luxembourg": "LU",
    "germany": "DE",
    "netherlands": "NL",
    "france": "FR",
    "united kingdom": "GB",
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "switzerland": "CH",
}


@dataclass
class StaticRowDraft:
    """One generated (unverified) static row plus the notes explaining any blanked field.
    Numeric fields are None when a guard blanked them or the payload omitted them."""

    ticker: str
    expense_ratio: Optional[float]
    fund_size: Optional[float]
    distribution_yield: Optional[float]
    share_class: Optional[str]
    domicile: Optional[str]
    as_of: str
    notes: list[str] = field(default_factory=list)

    @property
    def source(self) -> str:
        """The provenance/source cell: the base source, with any guard notes appended so a
        blanked cell always carries the reason (mirrors the committed CNDX row)."""
        if not self.notes:
            return SOURCE_BASE
        return SOURCE_BASE + " — " + "; ".join(self.notes)


def _parse_num(raw: object) -> Optional[float]:
    """EODHD returns numbers as strings, numbers, or null/empty. Coerce to float; None on
    missing/unparseable/NaN so an absent field stays absent (never a phantom 0)."""
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip() == "":
        return None
    try:
        f = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if f != f else f   # NaN -> None


def _domicile_code(raw: object) -> Optional[str]:
    """Map an ETF_Data.Domicile country name to the CSV's short code; pass an unrecognized
    value through verbatim; None when absent."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    return _DOMICILE_CODES.get(text.lower(), text)


def build_static_row(ticker: str, payload: dict, *, as_of: str) -> StaticRowDraft:
    """Turn a parsed EODHD ``/fundamentals`` payload into one static-CSV row draft.

    Pure — no network. Applies every accumulated sanity guard (see the module docstring):
    the fee comes from ``Ongoing_Charge`` only (NetExpenseRatio's fake zero is ignored), an
    implausible fund size is blanked with a note, the yield is converted percent->fraction,
    share class is inferred from the yield, and domicile is mapped from ETF_Data.
    """
    etf = (payload or {}).get("ETF_Data") or {}
    notes: list[str] = []

    # --- fee: Ongoing_Charge ONLY; NetExpenseRatio's 0.0000 class is a fake zero -------- #
    expense_ratio = _parse_num(etf.get("Ongoing_Charge"))
    if expense_ratio is None or expense_ratio == 0.0:
        expense_ratio = None
        notes.append("fee fake-zero (NetExpenseRatio 0.0000 class) — "
                     "Ongoing_Charge unavailable / verify from factsheet")

    # --- fund size: blank an implausible value (the CNDX 270B lesson) ------------------- #
    fund_size = _parse_num(etf.get("TotalAssets"))
    if fund_size is not None and not (SIZE_MIN <= fund_size <= SIZE_MAX):
        notes.append(f"fund size implausible ({fund_size:g}) blanked — "
                     "outside 1e7..1.5e12 / verify from factsheet")
        fund_size = None

    # --- yield: percent -> fraction ----------------------------------------------------- #
    yield_pct = _parse_num(etf.get("Yield"))
    distribution_yield = None if yield_pct is None else round(yield_pct / 100.0, 4)

    # --- share class inferred from the (fraction) yield --------------------------------- #
    share_class: Optional[str]
    if distribution_yield is None:
        share_class = None
    elif distribution_yield > 0:
        share_class = "dist"
    else:
        share_class = "acc"

    domicile = _domicile_code(etf.get("Domicile"))

    return StaticRowDraft(
        ticker=normalize_ticker(ticker),
        expense_ratio=expense_ratio,
        fund_size=fund_size,
        distribution_yield=distribution_yield,
        share_class=share_class,
        domicile=domicile,
        as_of=as_of,
        notes=notes,
    )


def _fmt_num(value: Optional[float]) -> str:
    """Format a numeric CSV cell: blank when None, an integer when the value is whole
    (fund sizes), and a trimmed decimal otherwise — matching the committed rows' look."""
    if value is None:
        return ""
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


def format_row(draft: StaticRowDraft) -> str:
    """Render a draft as one CSV line in the ``data/etf_static.csv`` column order."""
    cells = [
        draft.ticker,
        _fmt_num(draft.expense_ratio),
        _fmt_num(draft.fund_size),
        _fmt_num(draft.distribution_yield),
        draft.share_class or "",
        draft.domicile or "",
        draft.source,
        draft.as_of,
    ]
    return ",".join(cells)


# --------------------------------------------------------------------------- #
# network edge (not unit-tested — no live network in CI)
# --------------------------------------------------------------------------- #
def fetch_payload(symbol: str, api_key: str, *, timeout: float = 15.0) -> dict:
    """GET the EODHD ``/fundamentals/{symbol}`` payload. Raises on any HTTP/parse error so
    the caller can skip the one ticker and keep going."""
    params = urllib.parse.urlencode({"api_token": api_key, "fmt": "json"})
    url = f"{_BASE_URL}/fundamentals/{urllib.parse.quote(symbol)}?{params}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict) or not data:
        raise ValueError(f"EODHD returned no fundamentals for {symbol}")
    return data


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1 or argv[0] in ("-h", "--help"):
        print(__doc__)
        print("usage: EODHD_API_KEY=... python scripts/generate_etf_static_rows.py "
              "<universe_id>", file=sys.stderr)
        return 0 if argv[:1] in (["-h"], ["--help"]) else 2

    universe_id = argv[0]
    api_key = (os.environ.get("EODHD_API_KEY") or "").strip()
    if not api_key:
        print("error: EODHD_API_KEY is not set in the environment.", file=sys.stderr)
        return 2

    try:
        universe = load_universe_by_id(universe_id, UNIVERSES_DIR)
    except Exception as exc:                                   # unknown/broken manifest
        print(f"error: could not load universe '{universe_id}': {exc}", file=sys.stderr)
        return 2

    as_of = date.today().isoformat()
    print(f"# {universe_id}: {len(universe.tickers)} tickers — EODHD fundamentals "
          f"({as_of})", file=sys.stderr)
    print("# review these rows, then paste under the header in data/etf_static.csv",
          file=sys.stderr)

    for ticker in universe.tickers:
        symbol = normalize_ticker(ticker)
        try:
            payload = fetch_payload(symbol, api_key)
        except (urllib.error.URLError, TimeoutError, ValueError,
                json.JSONDecodeError) as exc:
            print(f"# SKIPPED {ticker}: {exc}", file=sys.stderr)
            continue
        draft = build_static_row(ticker, payload, as_of=as_of)
        print(format_row(draft))
        print(f"#   emitted {draft.ticker}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
