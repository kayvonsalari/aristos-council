"""Run the council for real against one ticker.

Requires:  pip install -e ".[yfinance,llm]"  and ANTHROPIC_API_KEY set.
Usage:     python examples/run_council.py JNJ
"""

import os
import sys
import textwrap
from pathlib import Path

from aristos_council.agents.runners import production_runners
from aristos_council.data.yfinance_adapter import YFinanceAdapter
from aristos_council.graph import build_council
from aristos_council.persistence.verdicts import (
    append_record,
    load_latest,
    record_from_state,
)
from aristos_council.state import ResearchState
from aristos_council.strategy.loader import load_strategy

VERDICTS_DIR = Path(__file__).resolve().parents[1] / "verdicts"


def block(text: str, indent: str = "      ") -> str:
    """Wrap long prose to 90 cols with a hanging indent — full text, no cuts."""
    return "\n".join(
        textwrap.fill(line, width=90, initial_indent=indent,
                      subsequent_indent=indent)
        for line in text.splitlines() if line.strip()
    )


ticker = sys.argv[1] if len(sys.argv) > 1 else "JNJ"
strategy = load_strategy(
    Path(__file__).resolve().parents[1] / "strategies" / "dividend_aristocrats_v1.yaml"
)

sentiment = None
if os.environ.get("FINNHUB_API_KEY"):
    from aristos_council.data.finnhub_adapter import FinnhubAdapter
    sentiment = FinnhubAdapter()
    print("(sentiment: Finnhub enabled)")
else:
    print("(sentiment: no FINNHUB_API_KEY — Sentiment specialist will abstain)")

app = build_council(YFinanceAdapter(), strategy, production_runners(),
                    sentiment_adapter=sentiment)

# IO at the edge: load the prior verdict (if any) so the recommendation_flip
# veto has something to compare against, then run the (disk-free) graph.
prior = load_latest(ticker, VERDICTS_DIR)
if prior is not None:
    print(f"(prior verdict: {prior.verdict.value.upper() if prior.verdict else 'n/a'} "
          f"@ {prior.run_at.date()})")
result = ResearchState.model_validate(
    app.invoke(ResearchState(
        ticker=ticker,
        strategy_id=strategy.id,
        prior_recommendation=prior.verdict if prior else None,
    ))
)

print(f"\n=== Aristos Council verdict on {ticker} ===\n")

for op in result.specialist_opinions:
    print(f"  {op.specialist.value.upper()}  —  {op.stance.value}  "
          f"(confidence {op.confidence:.2f})")
    print(block(op.thesis))
    for fig in op.figures:
        print(f"        · {fig.label}: {fig.value}"
              f"{' ' + fig.unit if fig.unit else ''}  "
              f"[{fig.provenance.tool_name} → {fig.provenance.field_path}]")
    for c in op.caveats:
        print(f"        ! caveat: {c}")
    print()

if result.critic_report:
    cr = result.critic_report
    print(f"  CRITIC  —  arguing against the {cr.targets_stance.value} consensus")
    print(block(cr.counter_thesis))
    for fig in cr.figures:
        print(f"        · {fig.label}: {fig.value}"
              f"{' ' + fig.unit if fig.unit else ''}  "
              f"[{fig.provenance.tool_name} → {fig.provenance.field_path}]")
    for w in cr.weaknesses_found:
        print(f"        · weakness: {w}")
    for q in cr.open_questions:
        print(f"        ? open question (for human resolution): {q}")
    print()

if result.decision:
    d = result.decision
    print(f"  DECISION: {d.recommendation.value.upper()}  "
          f"(confidence {d.confidence:.2f})")
    print(block(d.rationale))
    if d.dissent:
        print(f"\n      Dissent recorded: "
              f"{', '.join(s.value for s in d.dissent)}")
    print()

if result.provenance_audit:
    pa = result.provenance_audit
    print(f"  PROVENANCE AUDIT: {pa['figures_audited']} figures — "
          f"{pa['verified']} verified, {pa['mismatch']} mismatched, "
          f"{pa['unresolvable']} unresolvable, "
          f"{pa['unverifiable']} unverifiable (non-numeric), "
          f"{pa['unit_scaled']} unit-scaled")
    for v in pa["violations"]:
        print(block(f"✗ {v}", indent="      "))
    for n in pa["unit_scaled_notes"]:
        print(block(f"~ {n}", indent="      "))
    print()

if result.veto_flags:
    print("  ⚠ HUMAN REVIEW REQUIRED:")
    for f in result.veto_flags:
        print(f"      - {f.trigger.value}: {f.detail}")
else:
    print("  No veto triggers — auto-proceed permitted.")

# Append this run to the append-only history (IO at the edge, after the run).
saved = append_record(record_from_state(result), VERDICTS_DIR)
print(f"\n  verdict recorded -> {saved}")
