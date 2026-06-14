"""Run the council for real against one ticker, under a chosen strategy.

Requires:  pip install -e ".[yfinance,llm]"  and ANTHROPIC_API_KEY set.

Usage:
    python examples/run_council.py [TICKER] [STRATEGY] [-s/--strategy ID_OR_PATH]

    TICKER     ticker symbol (default: JNJ)
    STRATEGY   optional second positional: a strategy id (resolved against
               strategies/) or a path to a YAML file
    -s/--strategy ID_OR_PATH   same as the positional; overrides it if both given

Examples:
    python examples/run_council.py JNJ
    python examples/run_council.py MO growth_v1
    python examples/run_council.py AAPL --strategy growth_v1
    python examples/run_council.py AAPL --strategy strategies/growth_v1.yaml

The strategy defaults to dividend_aristocrats_v1 when omitted. The chosen
strategy flows through to build_council and is recorded as strategy_id in the
persisted verdict + report (so the log reflects the actual strategy used).
"""

import argparse
import os
import textwrap
from pathlib import Path

from aristos_council.agents.runners import production_runners
from aristos_council.data.yfinance_adapter import YFinanceAdapter
from aristos_council.graph import build_council
from aristos_council.persistence.reports import report_from_state, save_report
from aristos_council.persistence.verdicts import (
    append_record,
    load_latest,
    record_from_state,
)
from aristos_council.state import ResearchState
from aristos_council.strategy.loader import load_strategy

ROOT = Path(__file__).resolve().parents[1]
VERDICTS_DIR = ROOT / "verdicts"
REPORTS_DIR = ROOT / "reports"
STRATEGIES_DIR = ROOT / "strategies"
DEFAULT_STRATEGY_ID = "dividend_aristocrats_v1"


def block(text: str, indent: str = "      ") -> str:
    """Wrap long prose to 90 cols with a hanging indent — full text, no cuts."""
    return "\n".join(
        textwrap.fill(line, width=90, initial_indent=indent,
                      subsequent_indent=indent)
        for line in text.splitlines() if line.strip()
    )


def resolve_strategy_path(arg: str | None,
                          strategies_dir: Path = STRATEGIES_DIR) -> Path:
    """Resolve a strategy argument to a YAML path.

    - None/empty -> the default strategy (dividend_aristocrats_v1).
    - a `.yaml`/`.yml` path -> used as given (relative or absolute).
    - anything else -> treated as a strategy id, resolved as
      ``strategies/<id>.yaml``.

    The file's existence is left to ``load_strategy`` (which raises a clear
    FileNotFoundError), so a typo'd id/path fails loudly.
    """
    if not arg:
        return strategies_dir / f"{DEFAULT_STRATEGY_ID}.yaml"
    p = Path(arg)
    if p.suffix.lower() in (".yaml", ".yml"):
        return p
    return strategies_dir / f"{arg}.yaml"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_council.py",
        description="Run the Aristos Council on one ticker under a strategy.",
        epilog=textwrap.dedent("""\
            examples:
              python examples/run_council.py JNJ
              python examples/run_council.py MO growth_v1
              python examples/run_council.py AAPL --strategy growth_v1
              python examples/run_council.py AAPL -s strategies/growth_v1.yaml

            strategy is a strategy id (resolved against strategies/) or a YAML
            path; defaults to dividend_aristocrats_v1 when omitted.
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("ticker", nargs="?", default="JNJ",
                        help="ticker symbol (default: JNJ)")
    parser.add_argument("strategy", nargs="?", default=None,
                        help="strategy id or YAML path (optional)")
    parser.add_argument("-s", "--strategy", dest="strategy_opt", default=None,
                        metavar="ID_OR_PATH",
                        help="strategy id or YAML path (overrides the positional)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    ticker = args.ticker
    strategy = load_strategy(
        resolve_strategy_path(args.strategy_opt or args.strategy)
    )
    print(f"(strategy: {strategy.id} — {strategy.name})")

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
        print(f"(prior verdict: "
              f"{prior.verdict.value.upper() if prior.verdict else 'n/a'} "
              f"@ {prior.run_at.date()})")
    result = ResearchState.model_validate(
        app.invoke(ResearchState(
            ticker=ticker,
            strategy_id=strategy.id,
            prior_recommendation=prior.verdict if prior else None,
        ))
    )

    print(f"\n=== Aristos Council verdict on {ticker} "
          f"({strategy.id}) ===\n")

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

    # Persist the run (IO at the edge, after the run): the thin verdict log for
    # the next run's vetoes, and the full report for Council Station.
    saved = append_record(record_from_state(result), VERDICTS_DIR)
    print(f"\n  verdict recorded -> {saved}")
    report_saved = save_report(report_from_state(result), REPORTS_DIR)
    print(f"  full report saved -> {report_saved}")


if __name__ == "__main__":
    main()
