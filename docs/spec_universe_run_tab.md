ARISTOS — COUNCIL STATION: UNIVERSE RUN TAB (Sprint: UI catches up with the v2 pipeline).
Problem: app.py only drives run_council(ticker, strategy) — the pre-v2 single-ticker path.
The v2 product (universe -> screen-prefilter -> rank -> gates -> narrator) exists only in
examples/run_pipeline.py; the UI cannot run conservative_plus_v1 / magic_formula_v1 /
magic_formula_momentum_v1 at all. This sprint adds ONE tab that renders what the pipeline
already produces. No new logic; presentation only.

=== ITEM 1: extract a callable pipeline entry (no duplication, no subprocess) ===
Refactor examples/run_pipeline.py so its core is an importable function, e.g.
run_rank_pipeline(universe: list[str], strategy_id: str, *, council_mode: str = "narrator",
csv_path=None) -> PipelineResult {ranked: [RankedTicker], excluded: [(ticker, reason)],
unrateable: [(ticker, reason)], narratives: {ticker: str}, header: str, meta: {...}}.
The CLI script becomes a thin wrapper. The UI imports the same function. Tests: the function
returns the identical result the CLI printed for a fixed fake-adapter fixture.

=== ITEM 2: strategy discovery — split the schemas (fixes the dropdown complaint) ===
Classify YAMLs by shape: RANK strategies (have `factors:`), COUNCIL strategies (criteria,
runnable single-ticker), LENS screens (referenced by council_screen_strategy — hidden, as
now). Single-ticker page lists only COUNCIL strategies; the new tab lists only RANK
strategies. Unit tests over the current strategies/ directory: conservative_plus_v1,
magic_formula_v1, magic_formula_momentum_v1 -> rank; dividend_aristocrats_v1, growth_v1 ->
council; conservative_screen_v1, magic_value_screen_v1 -> hidden.

=== ITEM 3: the Universe Run tab ===
Inputs: universe textarea (whitespace/comma tickers; count shown), rank-strategy dropdown,
council_mode selector defaulting to narrator with a "ranker only — no LLM, no cost"
checkbox, run button showing the cost estimate the CLI already computes.
Guards: narrator mode without ANTHROPIC_API_KEY -> disable run with a clear message
(ranker-only stays available); empty/oversized universe (cap ~60) -> friendly error.
Output, in order (mirror the CLI, which is already the right report):
  1. the header line ("Verdict: deterministic ranker. Narrative: LLM (non-judging)");
  2. RANKED table — position, ticker, verdict (existing verdict color palette), combined
     rank, per-factor ranks (imputed marked *); sortable;
  3. Excluded — ticker + named reason (screen criterion, observed vs threshold);
  4. UNRATEABLE — ticker + reason, visually distinct from Excluded;
  5. NARRATIVE — one expander per BUY name, markdown rendered.
Persistence: reuse the existing reports/ sink if run_pipeline already persists; otherwise
add a "download run as markdown" button and do NOT invent a new storage format this sprint.
Progress: per-phase status (screening / ranking / narrating name k of n) — the narrator
phase is minutes, silence looks like a hang.

=== ITEM 4: env loading (likely the "finnhub error" root — but scope-fenced) ===
Load .env at app start (python-dotenv, already a pattern in the repo) so ANTHROPIC/FINNHUB
keys reach the Streamlit process regardless of launch shell. NO other Finnhub work this
sprint: sentiment failures must degrade to abstention per the Phase-3 contract; if a live
run still crashes on Finnhub, capture the traceback and STOP — that is a separate bug with
its own spec, not something to fix blind.

OUT OF SCOPE (explicit): redesigning the single-ticker page; strategy editing for rank
YAMLs; charts/history for rank runs; any Finnhub fix beyond env loading.
Validation: launch UI -> Universe Run -> 40-name growth universe, magic_formula_momentum_v1,
ranker-only -> table matches the last CLI validation run (same HEAD, same day). Then one
narrator run on a 5-name universe -> narratives render. Commit "Council Station: Universe
Run tab — the v2 rank pipeline in the UI (shared entrypoint, schema-split dropdowns,
narrator report rendering); .env loading at app start".
