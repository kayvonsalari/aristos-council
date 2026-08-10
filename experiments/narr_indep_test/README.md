# NARR-INDEP-TEST — harness

Does the narrator describe the evidence, or bend to fit the verdict / invent what it isn't
given? This is the empirical test of "math judges, LLM writes" — see the pre-registered
protocol and (once run) the results at
[`docs/experiments/NARR-INDEP-TEST.md`](../../docs/experiments/NARR-INDEP-TEST.md).

**This package makes no production code changes.** Every manipulation (forcing a verdict,
ablating the evidence pack, corrupting it) happens at the narrator-INPUT boundary — a
`RankedTicker` + `MarketDataAdapter` pair this package constructs — and is fed to the real,
unmodified `aristos_council` graph.

## Environment

Per this repo's `CLAUDE.md`: Claude Code (dev) never sets `ANTHROPIC_API_KEY` and never
launches a billed run. This harness was built and unit-tested entirely against fake/mocked
LLM runners (zero API calls) — see `tests/test_experiments_narr_indep_*.py`. The actual 27
narrations run in Colab (or any environment you control) with your own billed key.

## Modules

| File | Purpose |
|---|---|
| `fixtures.py` | The two funds — `FUND_GOOD` (VUSA.AS, real cheap/large/yielding) and `FUND_BAD` (IWMO.L, real pricier/smaller/zero-yield), hand-assigned to a clean-sweep rank 1/5 so the honest verdict is unambiguous. Adapters (normal + ablated), the screen-less `etf_dividend_v1` frame. |
| `conditions.py` | The 9 condition specs (Experiment A x4 + baseline, B1 x2, B2 x2) x 3 reps = 27. Each carries a `narrator_ticker` (what the graph sees) and a `true_ticker` (what the fact-checker validates against — deliberately different for B2). |
| `claims.py` | Extracts a normalized, comparable set of factual claims from narration text — reuses `narration_check`'s own parsing primitives for rank claims (so a claim here is exactly the class of claim the production checker would validate) plus fresh regexes for absolute fee/size/yield values (which the checker never parses at all). |
| `checker.py` | Wraps `narration_check.check_narration` against the fund's TRUE table — never the corrupted one the narrator was actually shown. |
| `manifest.py` | Append-only JSONL run log: condition, rep, model/temperature, timestamps, raw-output path. |
| `runner.py` | Invokes the graph directly (not `pipeline._council_stage` — see the module docstring for why) and saves each narration's raw output verbatim to its own file. |
| `analysis.py` | Builds the PASS/PARTIAL/FAIL comparison tables for all three experiments from saved raw outputs. Pure post-processing, no LLM calls — runs identically against mocked or real data. |
| `run_experiment.py` | The Colab entrypoint. |

## Colab quickstart

```python
!git clone <this repo's URL>
%cd aristos-council
!pip install -e ".[yfinance,llm]"

import os
os.environ["ANTHROPIC_API_KEY"] = "..."   # from Colab secrets, never hardcoded

# see the plan and cost estimate first, zero API calls:
!python experiments/narr_indep_test/run_experiment.py --dry-run

# the real run (prompts for confirmation; pass --yes to skip):
!python experiments/narr_indep_test/run_experiment.py
```

Raw output lands in `experiments/narr_indep_test/runs/<UTC timestamp>/` — one JSON file
per narration (`{condition_id}_rep{N}.json`) plus `manifest.jsonl`. Download that directory
and bring it back; `analysis.full_report(analysis.load_raw_outputs(out_dir))` reproduces
the tables offline, no re-running needed.

## Before you run for real

Run the mocked test suite first — it proves the whole pipeline (fixtures through the final
tables) with zero API calls:

```
python -m pytest tests/test_experiments_narr_indep_e2e_mocked.py -v
```

That test deliberately plants a real "bending" case and a real "invented claim" case in
its scripted narrations, and asserts the analysis correctly flags both — so a clean run
here means the detectors actually discriminate, not that they rubber-stamp everything PASS.
