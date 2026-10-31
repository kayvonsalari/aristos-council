"""``python -m aristos_council.holdings_check [--names X,Y] [--dated] [--council]``."""

from __future__ import annotations

import argparse
import os

from ..cli_guards import force_utf8_stdout
from . import HOLDINGS_CSV, HOLDINGS_DIR, LOCAL_ROOT, parse_holdings, run_check, seed_or_template


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m aristos_council.holdings_check",
        description="Private holdings check: each stock in data/local/holdings/holdings.csv against "
                    "its own peer group, via the Analyse tab's company engine. Output stays under "
                    "data/local/holdings/. No model is called unless --council is passed.")
    p.add_argument("--names", default="", help="comma-separated names or tickers to check (default: all)")
    p.add_argument("--dated", action="store_true",
                   help="also keep a dated copy and list what changed since the last dated run")
    p.add_argument("--council", action="store_true",
                   help="also convene the council narrator per stock: about six model calls each, "
                        "billed to ANTHROPIC_API_KEY. It writes, it never votes.")
    args = p.parse_args(argv)
    force_utf8_stdout()
    try:
        from dotenv import load_dotenv
        from . import ROOT
        load_dotenv(ROOT / ".env")
    except Exception:                                        # noqa: BLE001
        pass

    if not HOLDINGS_CSV.is_file():
        print(seed_or_template(HOLDINGS_CSV, local_root=LOCAL_ROOT))
        return 0
    holdings, problems = parse_holdings(HOLDINGS_CSV.read_text(encoding="utf-8"))
    for line in problems:
        print(f"warning: {line}")
    if not holdings:
        print(f"No holdings found in {HOLDINGS_CSV}.")
        return 1
    if args.council:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("--council needs ANTHROPIC_API_KEY (environment or .env).")
            return 1
        print("--council: about six model calls per stock, billed to your API key.")
    names = [n for n in args.names.split(",") if n.strip()]
    result = run_check(holdings, HOLDINGS_DIR, names=names, with_council=args.council,
                       dated=args.dated, progress=lambda m: print(m, flush=True))
    print(f"\nWrote {result.out_dir / 'summary.md'} and summary.csv "
          f"({len(result.rows)} holdings).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
