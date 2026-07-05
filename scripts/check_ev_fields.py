"""EV-components availability probe (hardening ITEM 6, Step 1 — DIAGNOSTIC ONLY).

Before switching ``earnings_yield`` from EBIT/market-cap to a truer EBIT/EV, we must
know whether EV's components are actually available at scale on free data. This prints,
for the ``growth_40_v1`` universe, how many names have ``totalDebt``, ``totalCash``, and
``marketCap`` in the provider's raw ``info``.

DECISION BAR (Step 2 proceeds ONLY IF): ``totalDebt`` AND ``totalCash`` populate for
>= 90% of names. Otherwise STOP and record "EV components unavailable at scale on free
data as of <date>" in CALCULATIONS.md — do NOT implement the EV upgrade blind.

Run this LIVE (it needs yfinance + network); it does not run in CI. The ``Fundamentals``
DTO does not (yet) expose ``totalCash``, so this probes the raw provider ``info``
directly — a raw-availability check, not a council data path.

    python scripts/check_ev_fields.py
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from aristos_council.universe import load_universe_by_id

ROOT = Path(__file__).resolve().parents[1]
UNIVERSES_DIR = ROOT / "universes"
BAR = 0.90


def main() -> None:
    universe = load_universe_by_id("growth_40_v1", UNIVERSES_DIR)
    tickers = universe.tickers

    import yfinance as yf                       # local: heavy + network-only

    have = {"totalDebt": 0, "totalCash": 0, "marketCap": 0}
    both = 0
    rows: list[tuple] = []
    for t in tickers:
        try:
            info = yf.Ticker(t).info or {}
        except Exception as exc:                # a flaky name must not abort the probe
            print(f"  {t}: info fetch failed ({exc})")
            info = {}
        td, tc, mc = info.get("totalDebt"), info.get("totalCash"), info.get("marketCap")
        have["totalDebt"] += td is not None
        have["totalCash"] += tc is not None
        have["marketCap"] += mc is not None
        both += (td is not None and tc is not None)
        rows.append((t, td, tc, mc))

    n = len(tickers)
    print(f"\ngrowth_40_v1 — {n} names — EV-component availability ({date.today()})")
    for k, c in have.items():
        print(f"  {k:<12} {c}/{n}  ({100 * c / n:.0f}%)")
    frac = both / n if n else 0.0
    print(f"  totalDebt AND totalCash: {both}/{n} ({100 * frac:.0f}%)")
    verdict = "PASS -> do the EBIT/EV upgrade (Step 2)" if frac >= BAR else \
        "FAIL -> STOP; record 'EV components unavailable at scale on free data'"
    print(f"  DECISION BAR {int(BAR * 100)}%: {verdict}")

    print("\n  ticker      totalDebt         totalCash         marketCap")
    for t, td, tc, mc in rows:
        print(f"  {t:<10}  {str(td):<16}  {str(tc):<16}  {mc}")


if __name__ == "__main__":
    main()
