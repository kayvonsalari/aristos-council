# Aristos Council

Multi-agent equity research system: specialist agents (Fundamental, Technical,
Sentiment, Risk) deliberate on a ticker under a YAML-defined strategy, an
adversarial Critic attacks the consensus, a Decision agent issues
BUY/HOLD/SELL with confidence, and a veto layer escalates to human review.
LangGraph orchestration, Anthropic models, pydantic state.

## Architecture (read in this order when orienting)

- `src/aristos_council/state.py` — ResearchState, Figure/Provenance, ToolCall
  ledger, VetoTrigger. The schema is the contract; change it last.
- `src/aristos_council/graph.py` — node wiring:
  gather → specialists → critic → decision → audit → veto.
- `src/aristos_council/agents/nodes.py` — gather (tool calls + evidence
  block), specialist/critic/decision nodes, shallow figure validation. The
  decision node applies the deterministic disposition gate AFTER the LLM verdict.
- `src/aristos_council/agents/disposition.py` — the deterministic disposition
  ceiling (is_gating build): `disposition_ceiling(screen_criteria, gating_names)`
  returns SELL if any criterion the strategy marks `is_gating` is a CONFIRMED
  fail (`passed is False`; NOT-EVAL/null does not cap), else None. The decision
  node caps the LLM's verdict to it and records the override on `Decision`
  (`original_recommendation`, `gate_override_applied`, `gating_criterion_fired`).
  This is CODE, not a prompt: `partial_pass_allows_hold` was a soft hint and
  proved evadable (T/MSFT/ASML/ARM overrode screen fails on Critic input-quality
  arguments).
- `src/aristos_council/audit/provenance.py` — deep provenance audit
  (post-run): resolves every cited figure's field_path against the ledger and
  compares values. Violations feed the DATA_QUALITY veto.
- `src/aristos_council/tools/` — deterministic tools (screen, technical &
  sentiment snapshots). ALL arithmetic happens here, never in agents.
- `src/aristos_council/tools/criteria/registry.py` — the criterion registry:
  named pure screen criteria + the generic `run_screen` runner that strategies
  drive by name (see "Criterion registry" below).
- `strategies/dividend_aristocrats_v1.yaml` — the active strategy.
- `src/aristos_council/persistence/` — IO-at-the-edge sinks: verdicts.py (thin
  append-only log for the next run's vetoes) and reports.py (full per-run
  deliberation for the UI to re-render). `load_latest` takes a `strategy_id` so
  the recommendation_flip veto compares within the SAME ticker+strategy (a
  growth BUY never flips against a dividend HOLD).
- `examples/run_council.py` — the demo entrypoint (run in Colab, not here).
- `app.py` — Council Station, the local Streamlit UI (`streamlit run app.py`).

## Hard project rules (learned the expensive way — do not relax)

1. Agents NEVER do arithmetic. Any derived number must come from a
   deterministic tool and be cited with call_id + field_path.
2. One figure = one field_path. Composite paths like `output[0].a + b` are
   provenance violations.
3. `passed` on screen criteria is true/false/null; null means NOT EVALUATED,
   false means evaluated-and-failed. Agents conflating these was a
   10-occurrence live bug class; the audit now catches it. The SAME null≠false
   discipline applies to inputs: a MISSING figure is NOT-EVAL, never a phantom
   FAIL. (Live: yfinance's `dividendRate` AND `payoutRatio` came back None for
   genuine payers PG/JNJ/MO/T/MMM — empty summaryDetail block — so
   `min_yield_criterion` treated null dps as a 0/FAIL and payout went
   null/NOT-EVAL. Fixed by DERIVING, not trusting the flaky fields: the adapter
   falls back `dividendRate -> trailingAnnualDividendRate` for
   `dividend_per_share`, derives `payout_ratio = dividend_per_share / trailingEps`
   when `payoutRatio` is None, and the criteria NOT-EVAL a true gap (null dps, or
   non-positive EPS) while still FAILing a genuine zero — e.g. INTC's suspended
   `trailingAnnualDividendRate == 0`.)
4. Prompt-side summarization of ledger objects requires an entry in
   `_PROMPT_VIEW_ALIASES` (audit/provenance.py), or honest citations get
   flagged as unresolvable.
5. The streak figure from the screen is a FLOOR (yfinance history starts
   ~1986, so a true 68-yr streak reads as ~39). Never present it as verified;
   the true streak may be LONGER, never shorter. The COUNTING method compares
   the per-payment dividend RATE (median of each year's payments) year over
   year — NOT the calendar-year SUM, which false-broke on ex-date timing (PG's
   2002 had 5 ex-dates -> 2003 looked like a cut -> genuine 68-yr aristocrat
   FAILed at 22). Per-payment counting recovers PG to 38 and KO to 39 while
   genuine cutters still break (T 2022 cut, INTC suspension -> streak 0). The
   remaining undercount is DATA DEPTH only (the parked EODHD adapter), not the
   method.
6. Tests run with `python -m pytest` (pythonpath=src configured). 278 tests
   green as of 2026-06-16. New behavior ships with regression tests, ideally
   anchored to documented live-run incidents.
7. Published strategy files are IMMUTABLE. Editing a strategy in the UI writes
   a new `<id>_v<n+1>.yaml` and refuses to overwrite — recorded verdicts and
   run reports reference their `strategy_id` and must stay reproducible
   (strategy/versioning.py).
   - One deliberate exemption: the Sprint 4A registry migration rewrote
     `dividend_aristocrats_v1.yaml` in place (same `strategy_id`, screen output
     byte-identical — proven by the equivalence test; see the 4A.2 commit).
8. Foreign-listing currency safety: criteria that compare an ABSOLUTE money
   amount against a USD-denominated threshold (today only `min_market_cap`)
   must NOT-EVAL with a note when `Fundamentals.currency` is a known non-USD
   currency — never silently pass/fail (SK Hynix's 1.69e15 KRW cap would "pass"
   a 1e10 USD floor for the wrong reason). Honest abstention only — NO FX
   conversion, consistent with insufficient-history handling. A MISSING currency
   evaluates normally (don't manufacture abstention from absent data). Ratio
   criteria (yield, payout, revenue CAGR, ROIC, PEG) are currency-INVARIANT and
   evaluate normally regardless. The guard lives in the screening primitive
   (`_non_usd_currency` in tools/screening.py), so `run_screen` stays equivalent
   to `run_dividend_aristocrat_screen`.

## Criterion registry (how the screen works, Sprint 4A)

The screen is a registry of named, pure criterion functions; strategies select
and parameterize them. There is NO dividend-specific logic in the runner.

- A **criterion** (`tools/criteria/registry.py`) is a pure deterministic
  function `fn(Evidence, threshold) -> CriterionResult` with a name, a tuple of
  required `Evidence` fields, and threshold bounds. `Evidence` bundles the
  gathered inputs (fundamentals, dividends, last_close). Three-valued `passed`
  (rule 3) and the streak floor (rule 5) are preserved exactly.
- A **strategy** lists criteria by registry name with thresholds (YAML):
  ```
  criteria:
    - name: min_dividend_yield
      threshold: 0.025
    - name: min_dividend_growth_streak
      threshold: 25
      unverifiable_blocks: true   # per-criterion successor to the old
                                  # policy.unverifiable_streak_is_blocking
  ```
  The loader validates every selection against the registry UP FRONT
  (`validate_selections`): unknown name, out-of-range threshold, or
  required-but-unavailable evidence → `ValidationError` at load.
- `run_screen(strategy.criteria, evidence, ticker=...)` runs each and assembles
  the `ScreenResult` (+ `unverifiable:<name>:<note>` flags). `gather` logs it
  under the historical tool_name `run_dividend_aristocrat_screen`, so the
  ledger/audit/reports are unchanged.

**Registered criteria** (`_CRITERIA` in registry.py):
- Dividend (4A): `min_dividend_yield`, `max_payout_ratio`, `min_market_cap`,
  `min_dividend_growth_streak`.
- Growth/quality (4B): `min_revenue_cagr` (in-house revenue CAGR over a 3y
  window), `min_roic` (NOPAT / PROVIDED invested_capital — not reconstructed
  from debt+equity, so negative-equity names stay sane), `max_peg_ratio` (P/E ÷
  in-house CAGR×100 — auditable, no provider forward estimate). All three
  degrade to NOT-EVAL on short history / negative earnings / missing inputs.
  They read the annual series on `Fundamentals` (total_revenue,
  operating_income, ebit, tax_provision, pretax_income, invested_capital),
  sourced newest-first from yfinance financials/balance_sheet (Sprint 4B; NO
  EODHD — yfinance confirmed sufficient).

Each criterion also **self-describes**: a human `label` and a `params` spec (per
parameter: name, type float/int/bool, bounds, step, default; policy flags are
bool). The Council Station **Strategy tab reads this metadata** (Sprint 4C) and
renders the right widget per parameter — number field per threshold (declared
bounds/step), checkbox per bool flag — so dividend and growth show different
fields with NO strategy-specific UI code. Params a strategy can't yet set
(min_revenue_cagr's `years`) render read-only.

**Strategy-scoped evidence (Sprint 4D)**: agents are handed a strategy-scoped
evidence packet so dividend framing can't leak into growth runs (live leak on
NVDA/ASML). Two display-only changes in `agents/nodes.py:_evidence_block` —
the ledger is never altered:
- The screen tool shows a neutral label `run_screen`; the STORED tool_name
  stays `run_dividend_aristocrat_screen` (rule 4 / audit / saved reports match
  on it).
- `get_fundamentals` renders only the fields the active strategy's criteria
  relate to (each criterion's `fundamentals_fields`) plus a fixed core
  (ticker, name, market_cap, pe_ratio, free_cash_flow, eps). So a growth run
  surfaces revenue/ROIC fields and NOT dividend ones. Specialist prompt roles
  are unchanged — the generic prompts adapt to the evidence shape.

**Strategy-scoped tool selection (Sprint 4E)**: completing the 4D fix, `gather`
only invokes a data-gathering tool when the active strategy needs its evidence.
`registry.required_evidence(strategy.criteria)` (union of each criterion's
`requires`) gates the call: a growth run does NOT call `get_dividend_history`
(no dividend events in the packet at all), a dividend run still does. Core tools
(fundamentals, technical, sentiment) are always called. This closed the live
MSFT growth leak (dividend-citation provenance violations).

**To add a criterion**: write the pure `fn(Evidence, threshold)` (math here or in
`tools/screening.py`, never in an agent), add one `Criterion(...)` entry to
`_CRITERIA` declaring its name, `label`, `params` (param specs incl. the
threshold's bounds/step/default), required evidence, and `fundamentals_fields`
(what to surface in the evidence) — then any strategy can select it by name, a UI
can render its inputs, and the evidence scopes correctly, with no runner changes.

**Safety net**: `tests/test_criteria_registry.py` pins `run_screen` ==
the original `run_dividend_aristocrat_screen` field-for-field across JNJ/MO/
BRK-B/O shapes. `run_dividend_aristocrat_screen` is retained unchanged as that
reference; if you touch criterion math, that equivalence test must be updated
deliberately.

## Environment & billing split (important)

- Claude Code (here): dev only, subscription auth. NEVER set
  ANTHROPIC_API_KEY in this environment.
- Colab: runtime only — council runs bill API credits via the key in Colab
  secrets. Default models: Haiku specialists, Sonnet critic/decision
  (validated config; do not silently change).
- Local Python setup if needed: `pip install -e ".[dev]"`.
- Run artifacts are PROJECT DATA, committed (not gitignored): `verdicts/
  <TICKER>.json` and `reports/<TICKER>/<run_at>.json` are checked in after each
  session. The verdict history feeds the recommendation_flip veto; the reports
  back Council Station's past-run browsing. Timestamps are stored in UTC; the
  UI converts to Europe/Berlin for display only.
- Council Station (the local Streamlit UI): `pip install -e ".[ui,yfinance,llm]"`
  then `streamlit run app.py`. Browsing past runs needs only `.[ui]`; LAUNCHING
  a council from the UI bills credits and needs the runtime extras + keys
  (ANTHROPIC_API_KEY, optionally FINNHUB_API_KEY) in the environment or a local
  `.env` (gitignored). Do NOT launch runs from the Claude Code dev environment.

## Current state (2026-06-12, end of Sprint 3)

Sprint 3 shipped Council Station — a local Streamlit UI (`app.py`) over the
council — plus the full run-report sink and strategy versioning it stands on
(126 tests green):

- `reports/<TICKER>/<run_at>.json` (persistence/reports.py) — one immutable
  file per run holding the ENTIRE deliberation (every specialist
  thesis/figures/caveats, the critic counter-case, the decision rationale, the
  FULL provenance audit including violation prose). The fat counterpart to the
  thin verdict log: it exists so the UI can re-render any past run without
  re-spending API credits. Same IO-at-the-edge pattern — run_council writes it
  after invoke; the graph stays disk-free.
- `app.py` (run `streamlit run app.py`) — sidebar (ticker, strategy dropdown,
  cost-gated Run); Report view (verdict banner, human-review flags,
  per-specialist expanders, critic + provenance panels, browsable past runs);
  History view (verdict/confidence chart + stance-per-specialist across runs
  off verdicts/<TICKER>.json); Strategy tab (read-only form + edit-as-new-
  version). Runs the council in-process; never launch it from here.
- strategy/versioning.py — make_new_version (bumps id+version, validates edits
  up front) + save_strategy (refuses to overwrite; published files immutable,
  rule 7).

### Sprint 2 (prior)

Sprint 2 shipped: verdict persistence, the MAJORITY_OVERRIDE veto, and two
prompt hard rules.

- `verdicts/<TICKER>.json` — append-only history (persistence/verdicts.py):
  run_at, strategy_id, verdict, confidence, per-specialist stances, veto
  triggers fired, provenance audit counts. IO is at the edge: run_council
  loads the latest prior record into prior_recommendation (so the existing
  recommendation_flip veto can finally fire) and appends after the run; the
  graph stays disk-free.
- Fifth veto MAJORITY_OVERRIDE (agents/veto.py): fires when the Decision
  verdict contradicts a STRICT (>50%) stance-majority of non-abstaining
  specialists (bullish→buy, neutral→hold, bearish→sell). Ties/no-majority
  silent; no confidence condition.
- Prompt hard rules (agents/nodes.py, rule 3): one-figure-one-field_path (no
  composite paths) and three-valued `passed` (null=NOT EVALUATED ≠ false).

Recall the deep provenance audit (Sprint 1) first proved itself when the first
production run caught 3 live None-vs-False misquotes + 1 composite-path
violation, zero false positives.

`verdicts/JNJ.json` is seeded with the 2026-06-11 HOLD 0.62 record (fundamental
/technical/sentiment bullish, risk neutral). Verdict history on JNJ ran BUY 0.62
→ BUY 0.65 → HOLD 0.62 on near-identical data — the run-to-run inconsistency
that motivated this sprint. The seeded HOLD means the next live JNJ run should
exercise BOTH new signals: recommendation_flip if the verdict moves off HOLD,
and majority_override if the council stays 3-bullish under a HOLD.

## Sprint 4A (shipped 2026-06-14)

Criterion registry refactor — architecture only, NO new criteria, NO behavior
change. The hardcoded `run_dividend_aristocrat_screen` is generalized into a
registry of named criterion functions that strategies select by name (see
"Criterion registry" above). Screen output is byte-identical (equivalence test).
The four dividend criteria are registered unchanged; the strategy YAML moved to
a criteria list. `run_dividend_aristocrat_screen` is kept as the equivalence
reference. 185 tests green.

## Sprint 4B (shipped 2026-06-14)

Growth criteria + growth strategy on the 4A registry substrate (NO EODHD —
yfinance sufficient). Data layer extended with annual income/balance series on
`Fundamentals`; three new registry criteria (`min_revenue_cagr`, `min_roic`,
`max_peg_ratio`) with the MO negative-equity / AMZN negative-income /
short-history edges covered; `strategies/growth_v1.yaml` assembled. Growth is
NOT yet selectable in the UI (hidden via `_DROPDOWN_HIDDEN_STRATEGY_IDS`).
Council prompts/agents untouched. 211 tests green.

## Sprint 4D (shipped 2026-06-14)

Strategy-scoped evidence — root-cause fix for dividend framing leaking into
growth runs (live on NVDA/ASML). The screen shows a neutral `run_screen` label
to agents (stored tool_name unchanged), and `get_fundamentals` is rendered
scoped to the active strategy's criteria + a fixed core, so growth runs no
longer see dividend fields. Display-only; ledger/audit untouched. Prompts not
modified. `examples/run_council.py` also gained a strategy argument (id or YAML
path) so growth_v1 can be run from the CLI. 223 tests green.

## Sprint 4C (shipped 2026-06-14)

Lit up growth in Council Station. The sidebar dropdown lists ALL live strategies
(dividend_aristocrats_v1 + growth_v1; the "coming soon" placeholder is gone) and
drives both the Run button and the Strategy tab. The Strategy tab renders
generically from the criterion registry (label + ParamSpec per criterion) — no
strategy-specific UI — so switching the dropdown re-renders the right fields, and
big-number thresholds get a readable caption. `examples/run_council.py` also
takes a strategy arg (Sprint 4B follow-on). 228 tests green.

**Strategy tab cleanup (visual/structural only, no verdict-logic change):** the
tab shows ONE strategy at a time (the sidebar selection) under a prominent
`📋 Viewing: <name> (<id>)` header, split into three boxed sections —
**Criteria** (generic, editable thresholds), **Policy** (the lone
`partial_pass_allows_hold` checkbox, lifted into its own card so it isn't
confused with the per-criterion `unverifiable_blocks` boxes — its behavior is
unchanged: still only a Decision-agent prompt hint, `nodes.py`), and
**Veto gate** (min_confidence). Locked params (not strategy-configurable) are
SHOWN but disabled + tagged 🔒, so no verdict-affecting input is invisible.
The in-house revenue-CAGR window (`_REVENUE_CAGR_YEARS`, read by BOTH
`_min_revenue_cagr` and `_max_peg_ratio` — one source of truth, can never
diverge) is surfaced ONCE, read-only, under `min_revenue_cagr`; PEG reuses the
same window, so it is NOT a PEG parameter and is not shown redundantly there.

## Sprint 4E (shipped 2026-06-14)

Strategy-scoped tool selection — closed the 4D residual. `gather` gates
data-gathering tools on `registry.required_evidence(strategy.criteria)`: a growth
run no longer calls `get_dividend_history` (no dividend events in the packet), a
dividend run is unchanged. Fixed the live MSFT dividend-citation violations.
235 tests green.

## Sprint 4F (next build)

- Nightly watchlist: GitHub Actions cron, ~5 tickers, dated verdict JSONs,
  cost logging. Requires Console auto-reload (user action). Verdict
  persistence (Sprint 2) is the substrate this builds on.

## Backlog (in order)

- EODHD adapter: replaces yfinance, fixes streak-floor undercounting.
- EDGAR RAG: filings → balance sheet/debt data (the Critic's #1 recurring
  open question).
- v2 strategy YAML knob: is a price-appreciation-driven yield miss waivable
  or governing? The council has argued both sides; make it explicit policy.
