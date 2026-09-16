"""Replay the frozen 2026-09-16 oil run offline and dump its facts pack."""
import json, sys
from datetime import date
from pathlib import Path
from aristos_council.pipeline import run_rank_pipeline, shortlist, MultiStrategyResult
from aristos_council.pipeline import combine_rank_results
from aristos_council.reader_facts import build_facts_pack

RUNS = {
    "magic_formula_raw_v1": "2026-09-16T16-47-26Z_magic_formula_raw_v1",
    "forensic_v1":          "2026-09-16T16-47-16Z_forensic_v1",
    "cyclical_income_v1":   "2026-09-16T16-47-12Z_cyclical_income_v1",
}
results, names = {}, {}
for sid, run_id in RUNS.items():
    r = run_rank_pipeline(None, sid, replay_run_id=run_id, freeze_dir=Path("runs"),
                          strategies_dir=Path("strategies"),
                          universes_dir=Path("universes"),
                          universe_id="oil_dividend_v1",
                          ranker_only=True, today=date(2026, 9, 16),
                          with_valuation_band=True)
    results[sid], names[sid] = r, r.meta.get("rank_strategy_name", sid)
    print(f"  {sid:26s} ranked {len([x for x in r.ranked if not x.excluded]):4d}"
          f"  excluded {len(r.excluded):4d}", flush=True)

ids = list(RUNS)
built = MultiStrategyResult(strategy_ids=ids, strategy_names=names, results=results,
                            rows=combine_rank_results(results, ids),
                            meta={"universe_size": results[ids[0]].meta["universe_size"],
                                  "universe_name": "Oil — Dividend-type",
                                  "shortlist_primary_id": "magic_formula_raw_v1"})
import dataclasses
built = dataclasses.replace(built, shortlist=shortlist(built, primary_id="magic_formula_raw_v1"))
pack = build_facts_pack(built, cohort_name="Oil — Dividend-type", cohort_thesis="income")
Path(sys.argv[1]).write_text(json.dumps(pack, indent=1, sort_keys=True,
                                        ensure_ascii=False), encoding="utf-8")
print("\nfacts pack written:", sys.argv[1])
print(json.dumps(pack, indent=1, sort_keys=True, ensure_ascii=False)[:1400])
