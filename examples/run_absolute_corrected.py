"""ABSOLUTE-BACKTEST-CORRECTED: re-run every industry with the split fix, the two-sided cost and the re-centred luck test.

    python examples/run_absolute_corrected.py [--only slug ...] [--out backtests_absolute/corrected]

Day cache only: EODHD_API_KEY is blanked in this process BEFORE anything loads ``.env`` (``load_dotenv`` never overrides a variable
that is already set), so nothing can be fetched; a name that is not cached counts as "no accounts" or "unpriced" and is skipped, as in
the published run. One industry at a time; an industry with a DONE.json is skipped, so re-running resumes. The published results
under ``backtests_absolute/`` are not touched.
"""
import os

os.environ["EODHD_API_KEY"] = ""                       # first, before any import that might read .env

import sys
from datetime import date

from aristos_council.absolute_backtest import __main__ as cli

if __name__ == "__main__":
    argv = ["run", "--out", "backtests_absolute/corrected", "--end", "2026-10-31"] + sys.argv[1:]
    code = cli.main(argv)
    assert os.environ.get("EODHD_API_KEY", "") == "", "the key must stay blank"
    raise SystemExit(code)
