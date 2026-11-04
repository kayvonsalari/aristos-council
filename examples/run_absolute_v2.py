"""ABSOLUTE-BACKTEST-2: extract the name tables, then write every analysis.

    python examples/run_absolute_v2.py extract [--only slug ...] [--workers 6]
    python examples/run_absolute_v2.py analyse

Day cache dated 2026-11-04 only (the date is fixed in code). EODHD_API_KEY is blanked in this process BEFORE anything loads
``.env`` (``load_dotenv`` never overrides a variable that is already set), and the offline feed raises on any fetch, so nothing
can be bought; a name that is not cached is skipped and counted. Output: ``backtests_absolute/v2/``. The corrected run
(``backtests_absolute/corrected/``) is read, never written.
"""
import os

os.environ["EODHD_API_KEY"] = ""                       # first, before any import that might read .env

import sys
from multiprocessing import Pool
from pathlib import Path

OUT = Path("backtests_absolute/v2")


def _one(slug_name):
    os.environ["EODHD_API_KEY"] = ""
    from aristos_council.absolute_backtest import v2_extract as X
    slug, name = slug_name
    if (OUT / slug / X.META_FILE).exists():
        return slug, "skipped (already extracted)"
    try:
        m = X.extract_industry(slug, OUT, name=name)
        return slug, f"{m['members']} members, {m['rows']} rows, {m['names_without_accounts']} without accounts, {m['names_unpriced']} unpriced"
    except Exception as exc:                           # noqa: BLE001 - one industry never stops the run
        return slug, f"FAILED {type(exc).__name__}: {exc}"


def main(argv) -> int:
    cmd = argv[0] if argv else "extract"
    if cmd == "extract":
        from aristos_council.absolute_backtest.runner import list_industries
        only = argv[argv.index("--only") + 1:] if "--only" in argv else None
        workers = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else 6
        if only:
            only = [a for a in only if not a.startswith("--") and not a.isdigit()]
        todo = [(s, n) for s, n in list_industries() if not only or s in only]
        with Pool(workers) as pool:
            for slug, msg in pool.imap_unordered(_one, todo):
                print(f"{slug}: {msg}", flush=True)
    elif cmd == "analyse":
        from aristos_council.absolute_backtest import v2_report
        v2_report.main(OUT)
    else:
        print(__doc__)
        return 2
    assert os.environ.get("EODHD_API_KEY", "") == "", "the key must stay blank"
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
