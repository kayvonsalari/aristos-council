"""Run the council for real against one ticker.

Requires:  pip install -e ".[yfinance,llm]"  and ANTHROPIC_API_KEY set.
Usage:     python examples/run_council.py JNJ
"""

import sys
from pathlib import Path

from aristos_council.agents.runners import production_runners
from aristos_council.data.yfinance_adapter import YFinanceAdapter
from aristos_council.graph import build_council
from aristos_council.state import ResearchState
from aristos_council.strategy.loader import load_strategy

ticker = sys.argv[1] if len(sys.argv) > 1 else "JNJ"
strategy = load_strategy(
    Path(__file__).resolve().parents[1] / "strategies" / "dividend_aristocrats_v1.yaml"
)

app = build_council(YFinanceAdapter(), strategy, production_runners())
result = ResearchState.model_validate(
    app.invoke(ResearchState(ticker=ticker, strategy_id=strategy.id))
)

print(f"\n=== Aristos Council verdict on {ticker} ===")
for op in result.specialist_opinions:
    print(f"  {op.specialist.value:12s} {op.stance.value:8s} "
          f"conf={op.confidence:.2f}  {op.thesis[:80]}")
if result.critic_report:
    print(f"\n  CRITIC (vs {result.critic_report.targets_stance.value}): "
          f"{result.critic_report.counter_thesis[:120]}")
if result.decision:
    d = result.decision
    print(f"\n  DECISION: {d.recommendation.value.upper()} "
          f"(confidence {d.confidence:.2f})")
    if d.dissent:
        print(f"  Dissent: {', '.join(s.value for s in d.dissent)}")
if result.veto_flags:
    print("\n  ⚠ HUMAN REVIEW REQUIRED:")
    for f in result.veto_flags:
        print(f"    - {f.trigger.value}: {f.detail}")
else:
    print("\n  No veto triggers — auto-proceed permitted.")
