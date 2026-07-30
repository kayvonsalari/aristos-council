"""DATA-HYGIENE-1 ITEM 4 — probe EODHD ETF records for base currency + null sentinels.

Answers, per ticker, the two questions the fix turns on:

1. **Which field (if any) states the FUND'S BASE currency?** It is not
   ``General::CurrencyCode`` — that is the LISTING currency, and the two differ exactly
   where it matters (IQQH.DE lists in EUR on XETRA and reports 4.17bn USD). The probe
   prints every candidate in ``eodhd_adapter.FUND_CURRENCY_CANDIDATES`` that is present,
   plus the listing currency for contrast, so the report says what EXISTS rather than
   what we hoped for.
2. **Which string cells carry EODHD's ``"NA"`` sentinel?** Reported as dotted field paths
   (``General.ISIN``, ``ETF_Data.Yield``, …) via ``eodhd_adapter.sentinel_fields``.

It also prints RAW vs NORMALISED fund size: raw ``ETF_Data::TotalAssets`` in the fund's
base currency, and the EUR value the ranker would use, with the FX receipt — or the
abstention note when the base currency is unknown. The FX rate comes from the SAME
adapter path production uses (``factors._fetch_fx_rate``), so the printed rate is the one
a run would apply.

    EODHD_API_KEY=... python scripts/probe_etf_fund_size_currency.py
    EODHD_API_KEY=... python scripts/probe_etf_fund_size_currency.py EUNL.DE IQQH.DE

Network edge, exactly like ``generate_etf_static_rows.py``: nothing here is unit-tested
(the pure helpers it composes — ``clean_sentinel`` / ``fund_base_currency`` /
``sentinel_fields`` / ``normalize_fund_size`` — are). Prints to STDOUT; per-ticker
failures to STDERR so a redirect captures only the report.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
from datetime import date

from aristos_council.data.eodhd_adapter import (
    FUND_CURRENCY_CANDIDATES,
    _coerce_float,
    clean_str,
    fund_base_currency,
    sentinel_fields,
)
from aristos_council.factors import (
    FUND_SIZE_CURRENCY,
    _fetch_fx_rate,
    normalize_fund_size,
)
from aristos_council.presentation import format_large_currency

# The tickers named in the issue: the EUNL factsheet gap, two small/mid UCITS names, and
# the three funds whose yield / expense / ISIN fields abstained live.
DEFAULT_TICKERS = ["EUNL.DE", "IUSN.DE", "SPYD.DE", "IQQH.DE", "DFEN.DE", "EUDF.DE"]


def _fx_lookup(today: date):
    """A rate lookup over the production FX path (yfinance ``<FROM><TO>=X``), memoised so
    a six-ticker probe fetches each pair once. Returns None on any failure -> abstain."""
    from aristos_council.data.provider import select_market_adapter

    try:
        adapter = select_market_adapter()
    except Exception as exc:                              # no provider installed
        print(f"# FX unavailable ({exc}) — fund sizes will report ABSTAINED",
              file=sys.stderr)
        return lambda from_ccy: None
    cache: dict[str, float | None] = {}

    def lookup(from_ccy: str):
        if from_ccy not in cache:
            try:
                cache[from_ccy] = _fetch_fx_rate(
                    adapter, from_ccy, FUND_SIZE_CURRENCY, today=today)
            except Exception as exc:                      # noqa: BLE001 — probe, not a run
                print(f"# FX {from_ccy}->{FUND_SIZE_CURRENCY} failed: {exc}",
                      file=sys.stderr)
                cache[from_ccy] = None
        return cache[from_ccy]

    return lookup


def probe_ticker(ticker: str, payload: dict, *, lookup, today: date) -> list[str]:
    """The report lines for ONE ticker — pure given a payload and a rate lookup."""
    etf = payload.get("ETF_Data") or {}
    general = payload.get("General") or {}
    raw = etf.get("TotalAssets")
    fund_ccy, ccy_field = fund_base_currency(payload)
    raw_value = _coerce_float(raw)
    value, receipt = normalize_fund_size(raw_value, fund_ccy,
                                         rate_lookup=lookup, as_of=today.isoformat())

    present = [f"{b}::{k}={clean_str(payload.get(b), k)}"
               for b, k in FUND_CURRENCY_CANDIDATES if clean_str(payload.get(b), k)]
    lines = [f"## {ticker} — {clean_str(general, 'Name') or '(no name)'}"]
    lines.append(f"- raw TotalAssets: {raw!r} -> {raw_value!r}")
    lines.append("- base-currency candidates present: "
                 + (", ".join(present) if present else "NONE"))
    lines.append(f"- listing currency (General::CurrencyCode, NOT usable as base): "
                 f"{clean_str(general, 'CurrencyCode')}")
    if receipt is None:
        lines.append("- normalised fund size: — (no TotalAssets to normalise)")
    elif receipt.ok:
        lines.append(f"- normalised fund size: "
                     f"{format_large_currency(value, FUND_SIZE_CURRENCY)} "
                     f"[{receipt.tag}] (base currency from {ccy_field})")
    else:
        lines.append(f"- normalised fund size: ABSTAINED — {receipt.note}")
    sentinels = sentinel_fields(payload)
    lines.append("- sentinel-filled string fields: "
                 + (", ".join(sentinels) if sentinels else "NONE"))
    for field in ("Yield", "Ongoing_Charge", "NetExpenseRatio", "ISIN"):
        lines.append(f"    raw ETF_Data::{field}: {etf.get(field)!r}")
    lines.append(f"    raw General::ISIN: {general.get('ISIN')!r}")
    return lines


def _generator_module():
    """The sibling generator script, loaded BY PATH (``scripts/`` is not on the import
    path) — it already owns the EODHD fetch + the .DE -> .XETRA suffix translation, so the
    probe reuses them instead of re-deriving either."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent / "generate_etf_static_rows.py"
    spec = importlib.util.spec_from_file_location("generate_etf_static_rows", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module      # dataclass creation resolves the module here
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] in (["-h"], ["--help"]):
        print(__doc__)
        return 0
    api_key = (os.environ.get("EODHD_API_KEY") or "").strip()
    if not api_key:
        print("error: EODHD_API_KEY is not set in the environment.", file=sys.stderr)
        return 2
    gen = _generator_module()
    tickers = argv or DEFAULT_TICKERS
    today = date.today()
    lookup = _fx_lookup(today)
    print(f"# DATA-HYGIENE-1 fund_size / sentinel probe — {today.isoformat()}")
    print(f"# target currency: {FUND_SIZE_CURRENCY}")
    print()
    for ticker in tickers:
        try:
            payload = gen.fetch_payload(gen.eodhd_query_symbol(ticker), api_key)
        except (urllib.error.URLError, TimeoutError, ValueError,
                json.JSONDecodeError) as exc:
            print(f"# SKIPPED {ticker}: {exc}", file=sys.stderr)
            continue
        print("\n".join(probe_ticker(ticker, payload, lookup=lookup, today=today)))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
