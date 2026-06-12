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
6. Tests run with `python -m pytest` (pythonpath=src configured). 108 tests
   green as of 2026-06-12. New behavior ships with regression tests, ideally
   anchored to documented live-run incidents.

## Environment & billing split (important)

- Claude Code (here): dev only, subscription auth. NEVER set
  ANTHROPIC_API_KEY in this environment.
- Colab: runtime only — council runs bill API credits via the key in Colab
  secrets. Default models: Haiku specialists, Sonnet critic/decision
  (validated config; do not silently change).
- Local Python setup if needed: `pip install -e ".[dev]"`.

## Current state (2026-06-12, end of Sprint 2)

Sprint 2 shipped (108 tests green): verdict persistence, the MAJORITY_OVERRIDE
veto, and two prompt hard rules.

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

## Sprint 3 (next build)

- Nightly watchlist: GitHub Actions cron, ~5 tickers, dated verdict JSONs,
  cost logging. Requires Console auto-reload (user action). Verdict
  persistence (Sprint 2) is the substrate this builds on.

## Backlog (in order)

- EODHD adapter: replaces yfinance, fixes streak-floor undercounting.
- EDGAR RAG: filings → balance sheet/debt data (the Critic's #1 recurring
  open question).
- v2 strategy YAML knob: is a price-appreciation-driven yield miss waivable
  or governing? The council has argued both sides; make it explicit policy.
