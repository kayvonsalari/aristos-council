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

Ephemeral per-run overrides (same mechanism as the Streamlit sidebar; the YAML
is never touched) let the override matrix be scripted:
    --gating CRITERION        set is_gating=True  on CRITERION (repeatable)
    --no-gating CRITERION      set is_gating=False on CRITERION (repeatable)
    --threshold CRITERION=N    override CRITERION's threshold (repeatable)
    --partial-pass / --no-partial-pass   set partial_pass_allows_hold
The applied delta is recorded on the report/verdict (empty for a baseline run),
and an override run is NOT a flip baseline — identical to the app.
"""

import argparse
import os
import textwrap
from pathlib import Path

from aristos_council.agents.runners import production_runners, runner_metadata
from aristos_council.data.adapter import normalize_ticker
from aristos_council.data.provider import select_market_adapter
from aristos_council.graph import build_council
from aristos_council.persistence.reports import report_from_state, save_report
from aristos_council.presentation import degraded_banner, run_health_line
from aristos_council.persistence.verdicts import (
    append_record,
    load_latest,
    record_from_state,
)
from aristos_council.state import ResearchState
from aristos_council.strategy.loader import load_strategy
from aristos_council.strategy.overrides import applied_overrides, effective_strategy
from aristos_council.tracing import status_line as tracing_status_line
from aristos_council.tracing import trace_config

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


def _threshold_arg(s: str) -> tuple[str, float]:
    """Parse one ``--threshold CRITERION=VALUE`` into (name, float). Raises a
    clear argparse error on malformed input (no '=' or non-numeric VALUE)."""
    if "=" not in s:
        raise argparse.ArgumentTypeError(
            f"--threshold expects CRITERION=VALUE, got {s!r} (missing '=')")
    name, _, raw = s.partition("=")
    name = name.strip()
    if not name:
        raise argparse.ArgumentTypeError(
            f"--threshold is missing the criterion name: {s!r}")
    try:
        value = float(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--threshold value must be numeric, got {raw!r} in {s!r}")
    return name, value


def build_override_kwargs(args: argparse.Namespace) -> dict:
    """Turn the parsed per-run flags into the SAME override kwargs the app uses:
    ``partial_pass_allows_hold`` (bool|None), ``is_gating`` ({crit: bool}|None),
    ``thresholds`` ({crit: float}|None). Empty/absent flags -> None, so a baseline
    run produces no overrides (and stays a valid flip baseline)."""
    is_gating: dict[str, bool] = {}
    for name in (args.gating or []):
        is_gating[name] = True
    for name in (args.no_gating or []):     # an explicit --no-gating wins if both given
        is_gating[name] = False
    thresholds = dict(args.threshold or [])  # list of (name, value) tuples
    return {
        "partial_pass_allows_hold": args.partial_pass,   # True / False / None
        "is_gating": is_gating or None,
        "thresholds": thresholds or None,
    }


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

    # --- ephemeral per-run overrides (same mechanism as the Streamlit sidebar) -- #
    ov = parser.add_argument_group("per-run overrides (this run only; YAML untouched)")
    ov.add_argument("--gating", action="append", metavar="CRITERION", default=[],
                    help="set is_gating=True on CRITERION this run (repeatable)")
    ov.add_argument("--no-gating", action="append", metavar="CRITERION", default=[],
                    help="set is_gating=False on CRITERION this run (repeatable)")
    ov.add_argument("--threshold", action="append", type=_threshold_arg, default=[],
                    metavar="CRITERION=VALUE",
                    help="override CRITERION's threshold this run (repeatable)")
    pp = ov.add_mutually_exclusive_group()
    pp.add_argument("--partial-pass", dest="partial_pass", action="store_true",
                    default=None, help="set partial_pass_allows_hold=True this run")
    pp.add_argument("--no-partial-pass", dest="partial_pass", action="store_false",
                    help="set partial_pass_allows_hold=False this run")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    # Normalize at the input edge: strips a stray trailing dot ("000660.KS.")
    # that otherwise breaks retrieval AND names the persisted verdict/report files.
    ticker = normalize_ticker(args.ticker)
    base = load_strategy(
        resolve_strategy_path(args.strategy_opt or args.strategy)
    )
    # Ephemeral per-run overrides via the SAME path as the app: build the effective
    # strategy and record exactly what differs. delta is EMPTY for a baseline run
    # (valid flip baseline) and populated for an override run (never a baseline).
    strategy = effective_strategy(base, **build_override_kwargs(args))
    delta = applied_overrides(base, strategy)
    print(f"(strategy: {base.id} — {base.name})")
    if delta:
        print("(overrides: "
              + "; ".join(f"{k}={v}" for k, v in delta.items()) + ")")

    sentiment = None
    sentiment_missing_key = False
    if os.environ.get("FINNHUB_API_KEY"):
        from aristos_council.data.finnhub_adapter import FinnhubAdapter
        sentiment = FinnhubAdapter()
        print("(sentiment: Finnhub enabled)")
    else:
        sentiment_missing_key = True
        print("(sentiment: no FINNHUB_API_KEY — Sentiment specialist will abstain)")

    # Provider chosen by $ARISTOS_MARKET_PROVIDER (default yfinance).
    adapter = select_market_adapter()
    print(f"(market provider: {adapter.name})")
    # Optional LangSmith tracing (env-gated, no-key no-op). Honest on/off line;
    # LangChain auto-instruments when the env vars are present — no agent changes.
    print(tracing_status_line())
    runners = production_runners()
    app = build_council(adapter, strategy, runners,
                        sentiment_adapter=sentiment,
                        sentiment_missing_key=sentiment_missing_key)

    # IO at the edge: load the prior verdict for the same ticker AND strategy
    # (recommendation_flip key), keyed off the BASE id. load_latest skips prior
    # OVERRIDE runs, so an experiment never becomes the flip baseline.
    prior = load_latest(ticker, VERDICTS_DIR, strategy_id=base.id)
    if prior is not None:
        print(f"(prior verdict: "
              f"{prior.verdict.value.upper() if prior.verdict else 'n/a'} "
              f"@ {prior.run_at.date()})")
    result = ResearchState.model_validate(
        app.invoke(
            ResearchState(
                ticker=ticker,
                strategy_id=base.id,             # always the BASE id (overrides ride in delta)
                prior_recommendation=prior.verdict if prior else None,
                applied_overrides=delta,         # empty for baseline; populated for overrides
            ),
            # Trace metadata so a LangSmith run is filterable (harmless when off).
            config=trace_config(ticker, base.id, adapter.name, bool(delta)),
        )
    )

    print(f"\n=== Aristos Council verdict on {ticker} "
          f"({base.id}) ===\n")

    # Run health FIRST: a degraded run (a fixable tool failure) gets a loud banner
    # at the very top before the verdict; every run gets a one-glance health line.
    banner = degraded_banner(result.run_issues)
    if banner:
        print(banner + "\n")
    print(run_health_line(result) + "\n")

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
    report = report_from_state(result)
    report.models = runner_metadata(runners)   # record model + temperature per tier
    report_saved = save_report(report, REPORTS_DIR)
    print(f"  full report saved -> {report_saved}")


if __name__ == "__main__":
    main()
