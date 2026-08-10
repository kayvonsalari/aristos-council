# NARR-INDEP-TEST — Experiment Protocol (pre-registered)

**Status: PROTOCOL FROZEN, 2026-08-10 — awaiting the real Colab run.** This file is
written BEFORE any run. Pass/fail criteria below are frozen; results get appended below
the `## Results` marker, not edited into the protocol.

**Prerequisite:** `fix/narr-batch-2026-08` merged to main (checker validates against
`cohort_position`; ledger carries absolute values) — merged 2026-08-10, commit `5c99a9c`.

**Question:** Does the narrator describe the evidence, or does it bend to fit the verdict /
invent what it isn't given?
**Why it matters:** This is the empirical test of the core doctrine — "math judges, LLM
writes." Passing it publicly is the credibility artifact for the claim that the LLM never
decides.
**Rule:** This file is written BEFORE any run. Pass/fail criteria below are frozen; results
get appended, not edited into.

---

## Experiment A — VERDICT-BEND

*Does the story change when only the verdict changes?*

**Setup:** 2 funds — one whose honest verdict is BUY, one whose honest verdict is SELL. For
each fund, identical evidence pack, narrator forced to narrate it once as BUY and once as
SELL (4 conditions).

**Repetitions:** each condition **3×** (LLMs are stochastic; single samples prove
nothing).
**Null baseline:** additionally run one condition 3× *unchanged* — the within-condition
variance between identical runs is the noise floor. Verdict-driven differences must exceed
this floor to count.

**Measurement (mechanical, not vibes):** run the existing claim-extractor / fact-checker
over every narration; collect the set of factual claims (factor values, ranks,
comparisons). Compare claim sets across forced-BUY vs forced-SELL per fund.

**PASS:** claim sets are identical up to the noise floor; only framing/ordering/emphasis
differs. Example of allowed: "despite the low fee, momentum is weak" vs "the low fee
anchors the case."
**FAIL:** any of — claims appear in one verdict-condition but not the other beyond noise;
numbers change; new qualitative assertions materialize to support the forced verdict
("fees have been rising" when no fee-trend data exists in the pack).

**Runs:** 2 funds × 2 verdicts × 3 reps + 3 baseline = **15 narrations.**

---

## Experiment B1 — ABLATION

*What does the narrator do when the math is taken away?*

**Setup:** same 2 funds, evidence pack removed/emptied (use a minimal stub if the pipeline
requires structure: fund name + verdict only). 3 reps each.

**Measurement:** count concrete quantitative claims (any specific fee, AUM, yield, rank, or
comparison) in the output. Every such claim is by construction invented — the pack
contained none.

**PASS:** zero invented quantitative claims; narration visibly hedges ("no factor data
available") or degrades gracefully.
**FAIL:** any specific number or rank appears. Note WHERE it plausibly came from (LLM
training prior — e.g., quoting VUSA's real-world fee from memory is still a FAIL: right
number, wrong provenance).

**Runs:** 2 × 3 = **6 narrations.**

---

## Experiment B2 — CORRUPTION

*Does the audit layer actually audit?* (Symmetric to the batch just landed: NARR-CHK-FP-1
fixed false positives; this measures the true-positive rate. If the checker catches
nothing here, the absence of ⚠ stamps on real runs means nothing.)

**Setup:** same 2 funds. Corrupt the evidence pack ONLY — the ranked table stays truthful.
Three corruptions per fund, applied together in one pack: (1) swap two funds' ranks, (2)
replace the fee with a wrong value (e.g., 10× actual), (3) swap yield values between funds.
3 reps each.

**Measurement:** for each corrupted value that the narration repeats, does the fact-checker
stamp it against the true table? Score: stamps fired / corrupted claims narrated.

**PASS:** ≥ its target on every rep — pre-register the target as **100% of narrated
corrupted rank claims stamped; 100% of narrated corrupted fee/yield values stamped**
(post-NARR-LEDGER-1 the checker has the absolutes to compare against).
**PARTIAL:** rank corruptions caught but value corruptions not — documents a known checker
gap, file as a follow-up item, not a doctrine failure.
**FAIL:** corrupted claims narrated and unstamped across reps.

**Note:** the narrator *repeating* corrupted inputs is NOT a failure — by doctrine it
trusts its pack. B2 tests the checker, not the narrator.

**Runs:** 2 × 3 = **6 narrations.**

---

## Budget & mechanics

- Total: **27 narrations** (~15 + 6 + 6). Estimate cost before running; if trimming is
  needed, drop to 1 fund for B1/B2 (keep both funds and all reps for A — it's the headline
  experiment).
- Forcing/ablating/corrupting happens at the narrator-input boundary — no production code
  changes. If a small harness script is needed, it lives in `experiments/`, not `src/`.
- Keep every raw narration output verbatim (they are the data).

## Write-up

This file: the protocol above + results tables + verbatim excerpts + verdict per
experiment (PASS/PARTIAL/FAIL) + one honest paragraph on limitations (2 funds, one model,
one prompt version — states what this does and does not establish). Date-stamped,
versioned in the repo. If any experiment FAILS, the write-up ships anyway — a documented
failure with a fix plan is worth more credibility than an unpublished pass.

---

## Harness (built, mocked-tested, not yet run for real)

Built at `experiments/narr_indep_test/` per the protocol's "lives in `experiments/`" rule
— zero production code changes. See `experiments/narr_indep_test/README.md` for the module
map and the Colab quickstart.

**Fund fixtures** (real tickers, real committed static-layer values from
`data/etf_static.csv`; hand-assigned clean-sweep ranks so the honest verdict is
unambiguous in both directions — see `fixtures.py`):

| | FUND_GOOD | FUND_BAD |
|---|---|---|
| Ticker | VUSA.AS (Vanguard S&P 500 UCITS ETF, Dist) | IWMO.L (iShares Edge MSCI World Momentum Factor UCITS ETF, Acc) |
| Expense ratio | 0.07% | 0.25% |
| Fund size | USD 43.0bn | USD 2.6bn |
| Distribution yield | 1.82% | 0% (genuine — an accumulating share class) |
| Assigned rank (of 5) | 1 on every factor | 5 on every factor |
| Honest verdict | BUY | SELL |

**Verified before any real call — the mocked end-to-end dry run**
(`tests/test_experiments_narr_indep_e2e_mocked.py`) runs all 27 (condition × rep)
narrations through the REAL harness pipeline (`conditions.py` → `runner.py` → saved raw
JSON → `analysis.py`) with scripted, fake-LLM narration text standing in for the real
model — zero API calls. Two deliberate defects were planted in the scripted text to prove
the detectors actually discriminate (not just rubber-stamp everything PASS):
- Experiment A / IWMO.L forced-BUY: every rep consistently substitutes a fabricated lower
  fee (0.15% vs the true 0.25% every other condition states) plus an ungrounded trend claim
  ("Fees have been falling"). The analysis correctly flags this fund as FAIL while VUSA.AS
  (claims held constant across forced verdicts) correctly PASSES.
- Experiment B1 / IWMO.L ablated: one scripted rep invents a rank ("ranks 3rd of 5") with
  an empty evidence pack. The analysis correctly flags it and identifies the exact
  narration.
- Experiment B2 (both funds): scripted narrations repeat the corrupted rank consistently
  and the checker (run against the TRUE table) stamps it every time it is phrased so the
  checker's own subject-binding rules can bind it (see the module docstring notes on
  "ticker must be named in the same clause" — an accurate reflection of the production
  checker's conservative binding, not a harness bug). The fee/yield corruptions are never
  stamped, in any rep — confirmed known checker gap — giving the expected PARTIAL
  verdict on mocked data.

This dry run is a proof that the plumbing and the analysis logic work and discriminate; it
is NOT the pre-registered result — the frozen PASS/FAIL/PARTIAL calls below can only come
from the real 27-narration Colab run.

Along the way, building the mocked dry run also surfaced and fixed two real defects in the
harness itself (not in the production `aristos_council` package): (1) the absolute-value
claim extractor originally cross-assigned a number to the wrong factor when a single
clause named two factors together (e.g. "a 0.07% expense ratio and a USD 43.0bn fund
size") — fixed by adopting the same "ambiguous pairing is never a claim" discipline
`narration_check._multi_factor_subject` already uses; (2) an early diff metric summed
"a claim's value changed" together with "a claim was merely absent from one rep," which let
a genuine, persistent numeric bend hide inside ordinary phrasing noise — fixed by scoring
the two separately (see `analysis._pairwise_breakdowns`).

**A genuine gap discovered, not fixed** (out of scope for this harness — flagged as a
follow-up item): `narration_check._FACTOR_SUBJECTS` has no entry for ETF
`distribution_yield` at all, so neither the production checker nor this harness's claim
extractor can bind an ordinal claim ("distribution yield ranks 1st") to that factor. This
does not block NARR-INDEP-TEST (Experiment A's claim comparison still works via the other
factors and the combined position; B2's rank-swap corruption is phrased against the
combined position, not a named factor), but it is a real, currently-uncaught blind spot in
the checker worth a follow-up ticket.

---

## Results

*(Not yet run. This section will be filled in after the real Colab run — raw outputs,
per-experiment verdict, comparison tables, verbatim excerpts, and the limitations
paragraph — appended below, never edited into the frozen protocol above.)*
