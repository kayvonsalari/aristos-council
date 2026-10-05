"""Dump the report sweep's RESULTS (votes, ranks, verdicts, exclusions, factor values) as JSON.

    python -m scripts.sweep_results_json > results.json

Used to prove a wording-only change moved no result: run it before and after and diff the files.
Only structured results are written - never rendered text - and no timestamps, so two runs on the
same code are byte-identical.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _ranked(rows):
    return [{"ticker": r.ticker, "verdict": r.verdict, "combined_rank": r.combined_rank,
             "position": r.cohort_position, "factor_ranks": r.factor_ranks,
             "factor_values": r.factor_values, "excluded": r.excluded} for r in rows]


def _result(res):
    return {"ranked": _ranked(res.ranked),
            "excluded": [list(x) for x in res.excluded],
            "unrateable": [list(x) for x in res.unrateable]}


def main() -> int:
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "src"))
    from tests import test_report_sweep as t

    reports = t.build_sweep(Path(tempfile.mkdtemp(prefix="sweep")))
    out = {}
    for name, case in reports.items():
        entry = {}
        company = case["company"]
        if company is not None:
            entry["votes"] = [{"lens": v.strategy_id, "status": v.status, "verdict": v.verdict,
                               "position": v.position, "of": v.cohort_size,
                               "badge": v.badge.label if v.badge else None}
                              for v in company.votes]
            ag = company.agreement
            entry["agreement"] = None if ag is None else {
                "buy": list(ag.buy), "hold": list(ag.hold), "sell": list(ag.sell),
                "na": [x[0] for x in ag.not_applicable], "checks": dict(ag.checks)}
            entry["lens_ranks"] = company.lens_ranks
            entry["universe"] = list(company.universe)
        if case["multi"] is not None:
            m = case["multi"]
            entry["multi"] = {sid: _result(r) for sid, r in m.results.items()}
            entry["rows"] = [{"ticker": r.ticker, "rank_sum": r.rank_sum,
                              "cells": {sid: [c.status, c.position, c.cohort_size, c.verdict,
                                              c.score] for sid, c in r.cells.items()}}
                             for r in m.rows]
        if case["single"] is not None:
            entry["single"] = _result(case["single"])
        out[name] = entry
    json.dump(out, sys.stdout, indent=1, sort_keys=True, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
