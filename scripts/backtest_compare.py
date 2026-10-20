"""BASELINE-1 - compare two sets of committed backtest results, pair by pair.

    python -m scripts.backtest_compare <before> <after>

``<before>`` / ``<after>`` is either a DIRECTORY laid out like ``backtests/``
(``<cohort_slug>/<lens_id>.csv``) or a GIT REF (a tag, branch or commit) whose ``backtests/`` tree is
extracted to a temp dir. Prints markdown: one row per cohort x lens with the verdict, the badge label,
the mean annual excess and the luck figure before and after, then the counts of changes. Labels are
computed by the CURRENT code on both sides, so a pure wording change shows as zero label changes -
what the table shows is movement in the DATA (the CSVs), which is what a refresh can change.

Every refresh of ``backtests/`` commits its compare output beside the CSVs; an experiment run goes in
``backtests_experiments/<name>/`` and is compared the same way (docs/BACKTEST.md, "Refreshing the
results"). Read-only: no network, no backtest is run.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def resolve(source: str, workdir: Path) -> Path:
    """A directory is used as given; anything else is a git ref whose ``backtests/`` is extracted."""
    path = Path(source)
    if path.is_dir():
        return path
    target = workdir / "".join(c if c.isalnum() else "_" for c in source)
    target.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(["git", "archive", source, "backtests"], cwd=ROOT, capture_output=True)
    if archive.returncode != 0:
        raise SystemExit(f"not a directory or a git ref with a backtests/ tree: {source}\n"
                         f"{archive.stderr.decode(errors='replace').strip()}")
    subprocess.run(["tar", "-x", "-C", str(target)], input=archive.stdout, check=True)
    return target / "backtests"


def read_side(root: Path) -> dict:
    """{(cohort_slug, lens_id): (verdict, label, mean excess, luck)} for every readable CSV."""
    from aristos_council.backtest import _load_backtests, track_record, verdict
    out = {}
    for (slug, lens), r in _load_backtests(root).items():
        s = r.summary
        out[(slug, lens)] = (verdict(r, max_luck=r.max_luck), track_record(slug, lens, root=root).label,
                             s.mean_annual_excess, s.luck_pct_mean)
    return out


def _pct(x, signed=True) -> str:
    return "n/a" if x is None else f"{x * 100:{'+' if signed else ''}.1f}"


def compare(before: dict, after: dict) -> str:
    keys = sorted(set(before) | set(after))
    lines = ["| Cohort | Lens | Verdict before | Verdict after | Label before | Label after | "
             "Excess %/yr before | after | Luck % before | after |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    n_verdict = n_label = n_excess = n_luck = n_new = n_gone = 0
    for k in keys:
        b, a = before.get(k), after.get(k)
        if b is None:
            n_new += 1
        elif a is None:
            n_gone += 1
        else:
            n_verdict += b[0] != a[0]
            n_label += b[1] != a[1]
            n_excess += b[2] != a[2]
            n_luck += b[3] != a[3]
        dash = ("-", "-", None, None)
        b, a = b or dash, a or dash
        lines.append(f"| {k[0]} | {k[1]} | {b[0]} | {a[0]} | {b[1]} | {a[1]} | {_pct(b[2])} | {_pct(a[2])} "
                     f"| {_pct(b[3], False)} | {_pct(a[3], False)} |")
    lines += ["", f"**{len(keys)} pairs.** Verdict changed: {n_verdict}. Label changed: {n_label}. "
                  f"Mean excess changed: {n_excess}. Luck changed: {n_luck}. "
                  f"Only in after: {n_new}. Only in before: {n_gone}."]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("before", help="backtests directory or git ref")
    ap.add_argument("after", help="backtests directory or git ref")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT / "src"))
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    with tempfile.TemporaryDirectory() as tmp:
        b = read_side(resolve(args.before, Path(tmp)))
        a = read_side(resolve(args.after, Path(tmp)))
        print(compare(b, a))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
