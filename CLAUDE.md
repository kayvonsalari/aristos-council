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
  block), specialist/critic/decision nodes, shallow figure validation.
- `src/aristos_council/audit/provenance.py` — deep provenance audit
  (post-run): resolves every cited figure's field_path against the ledger and
  compares values. Violations feed the DATA_QUALITY veto.
- `src/aristos_council/tools/` — deterministic tools (screen, technical &
  sentiment snapshots). ALL arithmetic happens here, never in agents.
- `strategies/dividend_aristocrats_v1.yaml` — the active strategy.
- `examples/run_council.py` — the demo entrypoint (run in Colab, not here).

## Hard project rules (learned the expensive way — do not relax)

1. Agents NEVER do arithmetic. Any derived number must come from a
   deterministic tool and be cited with call_id + field_path.
2. One figure = one field_path. Composite paths like `output[0].a + b` are
   provenance violations.
3. `passed` on screen criteria is true/false/null; null means NOT EVALUATED,
   false means evaluated-and-failed. Agents conflating these was a
   10-occurrence live bug class; the audit now catches it.
4. Prompt-side summarization of ledger objects requires an entry in
   `_PROMPT_VIEW_ALIASES` (audit/provenance.py), or honest citations get
   flagged as unresolvable.
5. The streak figure from the screen is a FLOOR (provider data undercounts:
   ADP/KMB/MO measured 3-of-10 false fails). Never present it as verified.
6. Tests run with `python -m pytest` (pythonpath=src configured). 90 tests
   green as of 2026-06-11. New behavior ships with regression tests, ideally
   anchored to documented live-run incidents.

## Environment & billing split (important)

- Claude Code (here): dev only, subscription auth. NEVER set
  ANTHROPIC_API_KEY in this environment.
- Colab: runtime only — council runs bill API credits via the key in Colab
  secrets. Default models: Haiku specialists, Sonnet critic/decision
  (validated config; do not silently change).
- Local Python setup if needed: `pip install -e ".[test]"`.

## Current state (2026-06-11, end of Sprint 1)

Deep provenance audit shipped and proven: first production run caught 3 live
None-vs-False misquotes + 1 composite-path violation, zero false positives.
Verdict history on JNJ: BUY 0.62 → BUY 0.65 → HOLD 0.62 on near-identical
data — a live run-to-run inconsistency incident that motivates Sprint 2.

## Sprint 2 (next build)

1. Verdict persistence: `verdicts/<TICKER>.json` — append per run: date,
   verdict, confidence, per-specialist stances, veto flags. Load prior
   verdict and pass as prior_recommendation so the existing
   recommendation_flip veto trigger can finally fire.
2. Fifth veto trigger: Decision verdict contradicts the stance-majority of
   non-abstaining specialists → human review flag. (Would have fired on the
   JNJ HOLD-vs-3-bullish run.)
3. Prompt fixes: one-figure-one-field_path rule; passed=null semantics.
   Test fixture: a JNJ rerun after this build should fire BOTH new signals.

## Backlog (in order)

- Nightly watchlist: GitHub Actions cron, ~5 tickers, dated verdict JSONs,
  cost logging. Requires Console auto-reload (user action).
- EODHD adapter: replaces yfinance, fixes streak-floor undercounting.
- EDGAR RAG: filings → balance sheet/debt data (the Critic's #1 recurring
  open question).
- v2 strategy YAML knob: is a price-appreciation-driven yield miss waivable
  or governing? The council has argued both sides; make it explicit policy.
