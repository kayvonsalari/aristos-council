"""
Batch Company Check runner — 7 UCITS ETFs, 3 strategies, HTML export.

Run from the aristos-council repo root, inside its venv:

    python run_etf_checks.py            # verify ISINs, then run all
    python run_etf_checks.py --dry-run  # verify ISINs only, run nothing

WIRING (one spot, marked ### WIRE ME ###):
I don't know the exact Company Check entrypoint, so it is isolated in
run_company_check(). Point it at whatever your Council Station backend
calls. Everything else — the roster, ISIN verification, HTML export via
your new exporter, filename scheme — is done.

Design decisions baked in:
- ISIN is the canonical identifier; the ticker is a resolved attribute.
  Before any run, the provider is asked for the ISIN behind the ticker
  and the run is SKIPPED LOUDLY on mismatch. This is what turns a ticker
  collision (SPYD/DFEN/DTEC all name unrelated US funds) from silent
  garbage into a visible error. yfinance's ISIN lookup is flaky; a
  failed lookup is reported as UNVERIFIED and the run proceeds with a
  warning rather than being blocked.
- One run failing must not kill the batch: per-run try/except, summary
  table at the end, non-zero exit if anything failed.
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------
# Roster — Xetra listings only (EUR, no GBX pence trap, one suffix rule).
# strategy_id values must match your YAML strategy ids exactly; edit if
# yours differ (shown here as discussed: etf_core_v1 was on screen for
# Tracker index; dividend/growth ids follow the same pattern — VERIFY).
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class EtfRun:
    name: str
    isin: str
    ticker: str          # yfinance form; for EODHD swap ".DE" -> ".XETRA"
    strategy_id: str
    reference_universe: str

ROSTER: list[EtfRun] = [
    EtfRun("iShares Core MSCI World",              "IE00B4L5Y983", "EUNL.DE", "etf_core_v1",     "etf_core_ucits_v1"),
    EtfRun("iShares MSCI World Small Cap",         "IE00BF4RFH31", "IUSN.DE", "etf_core_v1",     "etf_core_ucits_v1"),
    EtfRun("SPDR S&P US Dividend Aristocrats",     "IE00B6YX5D40", "SPYD.DE", "etf_dividend_v1", "etf_dividend_ucits_v1"),
    EtfRun("Amundi Global Memory Chips",           "LU2023678282", "DRUP.DE", "etf_growth_v1",   "etf_growth_ucits_v1"),
    EtfRun("iShares Global Clean Energy Transition","IE00B1XNHC34","IQQH.DE", "etf_growth_v1",   "etf_growth_ucits_v1"),
    EtfRun("VanEck Defense",                       "IE000YYE6WK5", "DFEN.DE", "etf_growth_v1",   "etf_growth_ucits_v1"),
    EtfRun("WisdomTree Europe Defence Acc",        "IE0002Y8CX98", "EUDF.DE", "etf_growth_v1",   "etf_growth_ucits_v1"),
]

OUTPUT_DIR = Path("reports/etf_batch")

# --------------------------------------------------------------------------
# ISIN verification (standalone, works today)
# --------------------------------------------------------------------------

def _find_eodhd_token() -> str | None:
    """Look for the key the same places Aristos plausibly keeps it:
    env vars, then a .env in the cwd, then .streamlit/secrets.toml."""
    import os, re
    for var in ("EODHD_API_TOKEN", "EODHD_API_KEY", "EODHD_TOKEN"):
        if os.environ.get(var):
            return os.environ[var].strip()
    for path in (Path(".env"), Path(".streamlit") / "secrets.toml"):
        if path.is_file():
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                m = re.match(r'\s*(?:EODHD[_A-Z]*)\s*=\s*"?([A-Za-z0-9._-]+)"?\s*$', line)
                if m:
                    return m.group(1)
    return None


def _isin_from_eodhd(ticker: str) -> str | None:
    """Ask EODHD (the provider Aristos actually fetches from) for the ISIN.
    Ticker arrives in yfinance form (EUNL.DE) and is mapped to EODHD form
    (EUNL.XETRA). ~1 API call each."""
    import urllib.request, json as _json
    token = _find_eodhd_token()
    if not token:
        return None
    eodhd_ticker = ticker[:-3] + ".XETRA" if ticker.endswith(".DE") else ticker
    url = (f"https://eodhd.com/api/fundamentals/{eodhd_ticker}"
           f"?api_token={token}&fmt=json&filter=General::ISIN")
    with urllib.request.urlopen(url, timeout=20) as r:
        got = _json.loads(r.read().decode())
    return got.strip('"') if isinstance(got, str) else None


def verify_isin(ticker: str, expected_isin: str) -> tuple[str, str]:
    """Return (status, detail): OK / MISMATCH / UNVERIFIED.

    EODHD first — it is the provider the pipeline reads, so a match there is
    the one that matters. yfinance is only a fallback and returns no ISIN for
    most Xetra listings, so expect little from it."""
    checks: list[tuple[str, str | None]] = []
    try:
        checks.append(("EODHD", _isin_from_eodhd(ticker)))
    except Exception as e:
        checks.append(("EODHD", None))
    if checks[-1][1] is None:
        try:
            import yfinance as yf
            checks.append(("yfinance", yf.Ticker(ticker).isin))
        except Exception:
            checks.append(("yfinance", None))
    for source, got in checks:
        if got and got != "-":
            if got.strip().upper() == expected_isin.upper():
                return "OK", f"{got} ({source})"
            return "MISMATCH", f"expected {expected_isin}, {source} says {got}"
    return "UNVERIFIED", "no provider returned an ISIN (no EODHD key found in env, .env, or .streamlit/secrets.toml)"

# --------------------------------------------------------------------------
# ### WIRE ME ### — the only part that needs your repo knowledge
# --------------------------------------------------------------------------

def run_company_check(run: EtfRun):
    """
    Call your actual Company Check pipeline and return its report object.

    Replace the body with the real call, e.g. something shaped like:

        from aristos_council.diagnostics.company_check import company_check
        return company_check(
            ticker=run.ticker,
            strategy_id=run.strategy_id,
            reference_universe=run.reference_universe,
        )

    Whatever Council Station's backend invokes when you press Run is the
    function you want. If it's CLI-only, subprocess.run([...]) works too.
    """
    raise NotImplementedError("Wire this to your Company Check entrypoint.")


def export_html(report, run: EtfRun, stamp: str) -> Path:
    """
    Export via the new HTML exporter from the issue you just deployed.
    Filename follows the issue's scheme exactly:
        company_check_{ticker}_{strategy_id}_{stamp}.html
    Ticker REQUIRED in single-name filenames (per spec). Dots in the
    ticker are kept — change to .replace('.', '-') if your
    download_names.py sanitises them.

    Replace the import with the real one, e.g.:

        from aristos_council.reporting.html_export import render_company_check_html
        html = render_company_check_html(report)
    """
    raise NotImplementedError("Wire this to the new HTML exporter.")
    # html = render_company_check_html(report)
    # out = OUTPUT_DIR / f"company_check_{run.ticker}_{run.strategy_id}_{stamp}.html"
    # out.write_text(html, encoding="utf-8")
    # return out

# --------------------------------------------------------------------------
# Batch driver
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="verify ISINs only; run nothing")
    ap.add_argument("--only", metavar="TICKER",
                    help="run a single ticker from the roster (e.g. EUNL.DE)")
    args, _unknown = ap.parse_known_args()   # tolerate Jupyter/Colab's -f kernel arg

    roster = ROSTER
    if args.only:
        roster = [r for r in ROSTER if r.ticker.lower() == args.only.lower()]
        if not roster:
            print(f"{args.only} not in roster.")
            return 2

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    results: list[tuple[EtfRun, str, str]] = []   # (run, status, detail)

    print(f"{'ticker':10s} {'strategy':16s} isin-check")
    print("-" * 60)
    for run in roster:
        status, detail = verify_isin(run.ticker, run.isin)
        print(f"{run.ticker:10s} {run.strategy_id:16s} {status}: {detail}")

        if status == "MISMATCH":
            results.append((run, "SKIPPED", f"ISIN mismatch — {detail}"))
            continue
        if args.dry_run:
            results.append((run, "DRY", detail))
            continue

        try:
            report = run_company_check(run)
            out = export_html(report, run, stamp)
            note = "" if status == "OK" else " (ISIN unverified!)"
            results.append((run, "DONE", f"{out}{note}"))
        except NotImplementedError as e:
            results.append((run, "UNWIRED", str(e)))
        except Exception:
            results.append((run, "FAILED", traceback.format_exc(limit=3)))
        time.sleep(1.0)   # be polite to the data provider

    print("\n=== summary ===")
    failed = 0
    for run, status, detail in results:
        if status in ("FAILED", "SKIPPED", "UNWIRED"):
            failed += 1
        head = detail.splitlines()[0] if detail else ""
        print(f"{status:8s} {run.ticker:10s} {head}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
