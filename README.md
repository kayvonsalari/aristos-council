# Aristos Council

A multi-agent financial research analyst. Specialist agents deliberate over a single security, a dedicated Critic argues the opposite case before any verdict is reached, and a Decision agent issues a **buy / hold / sell** call with an explicit confidence score and noted dissent. A human holds the veto.

The name nods to the [Dividend Aristocrats](https://en.wikipedia.org/wiki/S%26P_500_Dividend_Aristocrats) — and to the idea that a recommendation should have to survive a council, not just one model's first instinct.

## How it works

<img src="docs/council_diagram.png" alt="Aristos Council — how a verdict is reached" width="900">

For how a verdict is reached and what each strategy screens on, read the full [Council Explainer](docs/COUNCIL_EXPLAINER.md).

---

## Why this design

Most "AI stock analyst" demos are a single prompt that emits a confident answer with no audit trail. This project is built around the opposite premise: **a recommendation is only as trustworthy as the disagreement it survived and the numbers it can trace.**

Three principles drive the architecture:

1. **Adversarial by construction.** A Critic agent is required to argue against the emerging consensus before the Decision agent rules. Dissent is recorded in the output, never smoothed over.
2. **Deterministic math, always.** No LLM does arithmetic. Every figure is produced by a pure, unit-tested tool and carries provenance back to the exact tool call that created it. A number that can't be traced is a hard failure.
3. **Humans hold the veto.** The pipeline pauses for human review whenever confidence is low, specialists conflict, data quality is questionable, the recommendation flips from a prior run, or the Decision overrides the majority of the panel.

## Architecture

- **Orchestration:** LangGraph, with `ResearchState` threaded through every node.
- **Strategy as config:** the investment thesis lives in versioned YAML — not in code. Changing strategy means adding a new versioned file, so past decisions stay reproducible. Two strategies are live: **dividend aristocrats** (income) and **growth** (growth at a reasonable price).
- **Data behind an adapter:** every tool talks to a provider-agnostic `MarketDataAdapter`, never a vendor SDK. Develops on yfinance; swaps to EODHD with a one-line change.
- **Observability:** LangSmith tracing; tiered models via `init_chat_model`.

## Project structure

```
aristos-council/
├── app.py                        # Council Station — local Streamlit UI (Sprint 3)
├── src/aristos_council/
│   ├── state.py                  # ResearchState + Figure/Provenance/veto types — the schema contract
│   ├── graph.py                  # LangGraph wiring: gather → specialists → critic → decision → audit → veto
│   ├── agents/                   # the deliberators (LLM-backed, behind a Runner seam)
│   │   ├── nodes.py              # gather + specialist/critic/decision nodes, prompts, figure validation
│   │   ├── runners.py            # model seam: tiered Runner protocol + LangChain impl
│   │   ├── schemas.py            # structured-output schemas (tolerant parsing)
│   │   └── veto.py               # deterministic five-trigger human-veto gate
│   ├── audit/                    # deep provenance audit (Sprint 1)
│   │   └── provenance.py         # resolve every cited figure's field_path against the ledger
│   ├── data/                     # provider-agnostic market & sentiment data
│   │   ├── adapter.py            # MarketDataAdapter interface + DTOs + DataUnavailable
│   │   ├── yfinance_adapter.py   # dev market-data provider
│   │   ├── eodhd_adapter.py      # planned market-data provider (stub)
│   │   ├── sentiment.py          # SentimentAdapter interface + DTOs
│   │   └── finnhub_adapter.py    # sentiment provider (news + analyst trends)
│   ├── persistence/              # IO-at-the-edge sinks (Sprint 2–3)
│   │   ├── verdicts.py           # append-only verdict log feeding the vetoes (Sprint 2)
│   │   └── reports.py            # full per-run deliberation for the UI (Sprint 3)
│   ├── strategy/                 # strategy config
│   │   ├── loader.py             # validated strategy YAML loader
│   │   └── versioning.py         # edit-as-new-version; never mutates published files (Sprint 3)
│   └── tools/                    # deterministic tools — ALL arithmetic lives here
│       ├── screening.py          # dividend-aristocrat screen math
│       ├── technical.py          # price / technical snapshot
│       └── sentiment_tools.py    # sentiment aggregation
├── strategies/                   # versioned strategy YAMLs (dividend_aristocrats_v1/v2, growth_v1)
├── verdicts/                     # committed run data — append-only verdict history per ticker
├── reports/                      # committed run data — full per-run reports (<TICKER>/<run_at>.json)
├── assets/                       # brand mark (SVG logo)
├── .streamlit/                   # Council Station theme (config.toml)
├── examples/run_council.py       # CLI entrypoint (single council run)
├── tests/                        # pytest suite
└── CLAUDE.md                     # working agreement + sprint log for contributors
```

Run artifacts under `verdicts/` and `reports/` are checked in as project data: the
verdict history feeds the recommendation-flip veto, and the reports back Council
Station's past-run browsing.

## Stack

| Concern | Choice |
|---|---|
| Orchestration | LangGraph |
| Market data (dev) | yfinance, behind a provider-agnostic adapter |
| Market data (prod) | EODHD *(planned)* |
| Sentiment | Finnhub (free tier) — company news + analyst recommendation trends, behind a provider-agnostic `SentimentAdapter` |
| Filings | SEC EDGAR → RAG *(planned)* |
| Vector store | ChromaDB *(planned)* |
| LLM routing | `init_chat_model` (tiered) |
| Monitoring | LangSmith |
| Tests / CI | pytest + GitHub Actions |

## Project status

**Phase 1 — data substrate (complete):** `ResearchState` schema with figure-level provenance, provider-agnostic adapter (yfinance + EODHD stub), deterministic screening tools, versioned strategy config + validating loader.

**Phase 2 — the council (complete):** full LangGraph pipeline — deterministic `gather` node (the only node that touches data or math), four specialists with enforced figure provenance, a provenance-bound Critic arguing the opposite case (unverifiable quantitative concerns become open questions for a human, never asserted facts), Decision agent with recorded dissent, and a fully deterministic five-trigger human-veto gate. LLMs sit behind a `Runner` seam with env-configurable model tiers, so the entire graph is tested end-to-end with fakes — no API keys in CI.

**Phase 3 — sentiment (complete):** Finnhub news + analyst recommendation trends behind a provider-agnostic `SentimentAdapter`, aggregated by a deterministic `sentiment_snapshot` tool. Without a `FINNHUB_API_KEY` the Sentiment specialist abstains exactly as before; a provider outage degrades to a data-quality veto flag, never a crash.

**Phase 4 — audit, persistence & Council Station (current, Sprint 3):** a deep post-run **provenance audit** that resolves every cited figure's `field_path` against the tool-call ledger and feeds the data-quality veto; an append-only **verdict history** (`verdicts/`) powering the recommendation-flip and majority-override vetoes; full per-run **reports** (`reports/`); **strategy versioning** (edit-as-new-version, never mutating a published file); and **Council Station** — a local Streamlit UI to run the council, read the full deliberation, browse past runs across tickers, chart verdict/confidence history, and edit strategies. See `CLAUDE.md` for the sprint log.

**285 unit tests**, green on Python 3.11+, run end-to-end with fakes — no API keys in CI. Try it live: **Council Station** via `pip install -e ".[ui,yfinance,llm]"` then `streamlit run app.py`, or a single run with `python examples/run_council.py JNJ` (both need an Anthropic API key for live runs).

**Next:** SEC EDGAR filings RAG for the Fundamental specialist, EODHD adapter (fixes the dividend-streak-floor undercount), LangSmith tracing, nightly watchlist runs via GitHub Actions cron.

## A note on honesty

The yfinance development provider cannot verify the canonical 25-year dividend-growth streak — its history is too short. The screen does **not** paper over this: the streak criterion returns *unverifiable* (distinct from pass or fail) and trips the data-quality veto by design. Confirming that streak is one of the concrete reasons EODHD is the planned upgrade.

## Running

Run the tests:

```bash
pip install -e ".[dev]"
pytest
```

Launch **Council Station** (the local Streamlit UI):

```bash
pip install -e ".[ui,yfinance,llm]"
streamlit run app.py
```

Browsing saved runs needs only `.[ui]`; launching a council from the UI bills API credits and additionally needs the runtime extras above plus `ANTHROPIC_API_KEY` (and optionally `FINNHUB_API_KEY`) in the environment or a local `.env`.

Or run a single council from the CLI:

```bash
python examples/run_council.py JNJ
```

---

*Portfolio project by Kayvon Salari.*
