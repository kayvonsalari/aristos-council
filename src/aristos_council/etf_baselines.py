"""ETF-1 ITEM 5 — baseline + mirror (kind-leak) machinery.

Two report kinds, both driven off the ONE shared entrypoint ``run_rank_pipeline`` (no
new orchestration):

- **baseline** — a ranker-only run of an ETF lens on its own universe, formatted as an
  exploratory markdown report with the sanity anchors reported (never asserted).
- **mirror** — a deliberate cross-class run proving the asset-kind wall: a flagship stock
  lens on an ETF universe (expect 0 ranked, all kind-gated) and the ETF dividend lens on
  a stock universe (expect 0 ranked, all kind-gated, delisted names UNRATEABLE).

The pipeline does NOT persist reports (Universe Run tab convention), so these are written
to ``reports/exploratory/`` by ``examples/etf_baselines.py`` (which needs the network).
This module holds only the pure summary + formatting, unit-tested offline with mocked
adapters against the REAL pipeline — the acceptance the PR carries.
"""

from __future__ import annotations

from dataclasses import dataclass

from .pipeline import RankPipelineResult, format_cli_report

# The asset-kind gate's exclusion reason begins with this literal (ETF-1 ITEM 2). Distinct
# from the sector-scope reason ("sector '<X>' outside this strategy's scope"), so counting
# kind-gated names never double-counts a sector gate.
KIND_GATE_MARKER = "asset kind '"


@dataclass(frozen=True)
class MirrorSummary:
    ranked: int
    kind_gated: int
    unrateable: int
    other_excluded: int
    kind_gated_names: list[str]
    unrateable_names: list[str]


def mirror_summary(result: RankPipelineResult) -> MirrorSummary:
    """Partition a run into ranked / kind-gated / unrateable / other-excluded — the
    numbers the mirror acceptance checks (0 ranked, all kind-gated, delisted UNRATEABLE)."""
    kind = [(t, w) for t, w in result.excluded if w.startswith(KIND_GATE_MARKER)]
    other = [(t, w) for t, w in result.excluded if not w.startswith(KIND_GATE_MARKER)]
    return MirrorSummary(
        ranked=len(result.ranked),
        kind_gated=len(kind),
        unrateable=len(result.unrateable),
        other_excluded=len(other),
        kind_gated_names=[t for t, _ in kind],
        unrateable_names=[t for t, _ in result.unrateable])


def sanity_anchor_lines(result: RankPipelineResult) -> list[str]:
    """Observed sanity anchors for a baseline — REPORTED, never asserted (ETF-1 ITEM 5):
    the expense-ratio range, the top names by distribution yield, and the largest fund."""
    ranked = result.ranked
    if not ranked:
        return ["- (no names ranked)"]
    lines: list[str] = []

    def _vals(factor):
        return {r.ticker: r.factor_values.get(factor) for r in ranked
                if r.factor_values.get(factor) is not None}

    er = _vals("expense_ratio")
    if er:
        lo = min(er.values())
        hi = max(er.values())
        lines.append(f"- expense ratio range: {lo:g}–{hi:g} "
                     f"(low {min(er, key=er.get)}, high {max(er, key=er.get)})")
    dy = _vals("distribution_yield")
    if dy:
        top = sorted(dy, key=dy.get, reverse=True)[:3]
        lines.append("- top by distribution yield: "
                     + ", ".join(f"{t} ({dy[t]:.2%})" for t in top))
    fs = _vals("fund_size")
    if fs:
        biggest = max(fs, key=fs.get)
        lines.append(f"- largest by fund size: {biggest} ({fs[biggest]:,.0f})")
    return lines or ["- (no factor values to anchor on)"]


def format_baseline_markdown(result: RankPipelineResult) -> str:
    """An exploratory baseline report: the CLI report (shared formatter) + the reported
    sanity anchors. NO new storage format — a committed markdown artifact."""
    m = result.meta
    lines = [f"# ETF baseline — {m['rank_strategy_id']} on {m.get('universe_id', '?')}",
             "", "_Exploratory ranker-only baseline (ETF-1 ITEM 5). Never on the "
             "prospective scoreboard until deliberately frozen._", "",
             "## Sanity anchors (reported, not asserted)", ""]
    lines += sanity_anchor_lines(result)
    lines += ["", "## Ranker report", "", "```", format_cli_report(result), "```"]
    return "\n".join(lines)


def format_mirror_markdown(result: RankPipelineResult, *, expectation: str) -> str:
    """A mirror (kind-leak) report: the summary counts vs the stated expectation, the
    named kind-gated / unrateable partitions, then the full ranker report."""
    s = mirror_summary(result)
    m = result.meta
    lines = [f"# ETF kind-leak mirror — {m['rank_strategy_id']} on "
             f"{m.get('universe_id', '?')}", "",
             f"_Expectation: {expectation}_", "",
             "## Result", "",
             f"- ranked: **{s.ranked}**",
             f"- kind-gated: **{s.kind_gated}**"
             + (f" ({', '.join(s.kind_gated_names)})" if s.kind_gated_names else ""),
             f"- unrateable: **{s.unrateable}**"
             + (f" ({', '.join(s.unrateable_names)})" if s.unrateable_names else ""),
             f"- other exclusions: {s.other_excluded}",
             "", "## Ranker report", "", "```", format_cli_report(result), "```"]
    return "\n".join(lines)
