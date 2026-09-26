"""COHORT-1 — the quality report, in plain sentences with one figure each.

Deliberately not a dashboard. Every check gets a line a person can read aloud, a flagged
check is marked and says which line it crossed, and the removals are listed in full
underneath — a cohort you cannot argue with is a cohort you cannot trust.
"""
from __future__ import annotations

from datetime import date

from .cleanup import RULE_NAMES, Removal, SizeVerdict
from .definitions import CohortDefinition
from .quality import QualityReport
from .flags import legend_lines, symbols
from .source import PATH_INDEX, Candidate
from ..market_index import MINOR_UNIT_MARKET_CAP
from ..tools.price_context import format_money


def cap_cell(cand: Candidate) -> str:
    """A member's market cap through the ONE money formatter: the USD figure first, and the local
    one beside it when the name is quoted in another currency ("$24.6bn (CHF 20.2bn)").

    A London cap from the index is in POUNDS although its quote code says GBX (INDEX-GBX-SCALE-1),
    so it reads "£76.5bn", never "76,547,235,840 GBX". No cap at all is a dash, never a zero."""
    code = (cand.currency or "").strip()
    if cand.source == PATH_INDEX and code.upper() in MINOR_UNIT_MARKET_CAP:
        code = MINOR_UNIT_MARKET_CAP[code.upper()]
    local = (None if cand.market_cap is None
             else format_money(cand.market_cap, code or None, abbreviate=True))
    usd = (None if cand.market_cap_usd is None
           else format_money(cand.market_cap_usd, "USD", abbreviate=True))
    if usd is None:
        return local or "—"
    return usd if (local is None or code.upper() == "USD") else f"{usd} ({local})"


def render_report(*, defn: CohortDefinition, version: int, built_on: date,
                  members: list[Candidate], removals: list[Removal],
                  size: SizeVerdict, quality: QualityReport | None,
                  source_log: list[str], strategy_id: str = "", excluded=()) -> str:
    out: list[str] = []
    add = out.append

    add(f"# {defn.name} — v{version}")
    add("")
    add(f"Built {built_on.isoformat()} from the rule below. {size.sentence()}")
    add("")

    # -- the rule ---------------------------------------------------------- #
    add("## The rule")
    add("")
    add(f"- **Industry**: {', '.join(defn.industry_as_written or defn.industry)}"
        + (f" (resolved to {', '.join(defn.industry)})"
           if tuple(defn.industry_as_written) != tuple(defn.industry) else ""))
    if defn.gics_subindustry:
        add(f"- **GICS sub-industry** (narrows the industry): {', '.join(defn.gics_subindustry)}")
    add(f"- **Exchanges**: {', '.join(defn.exchanges)} "
        f"({', '.join(defn.exchange_codes)}) — main listings only")
    if defn.uses_index:
        add(f"- **Minimum market cap**: ${defn.min_market_cap_usd / 1e9:g}bn, in USD as "
            f"converted by the market index (a per-cohort floor set by a stated rule, never "
            f"tuned to a count)")
    else:
        add(f"- **Minimum market cap**: {defn.min_market_cap:,.0f} in each name's OWN "
            f"currency, deliberately unconverted")
    add(f"- **Minimum history**: {defn.min_history_years:.0f} years")
    add(f"- **Excluded**: {', '.join(defn.exclude) or 'nothing'}")
    add(f"- **Anchors**: {', '.join(defn.anchors) or 'none'}")
    add("")

    # -- how it was built --------------------------------------------------- #
    add("## Where the names came from")
    add("")
    for line in source_log:
        add(f"- {line}")
    add("")

    # -- the checks --------------------------------------------------------- #
    add("## Quality checks")
    add("")
    if quality is None:
        add("Not run — the cohort did not reach a usable size, so ranking it would "
            "describe a list nobody should use.")
    else:
        if quality.lens:
            add(f"Ranked under `{quality.lens}` ({quality.lens_source or 'lens not attributed'}).")
            add("")
        for check in quality.checks:
            add(f"**{check.name}** — {check.line()}")
            add("")
        flags = quality.flags
        add(f"{len(flags)} check(s) flagged"
            + (f": {', '.join(c.name for c in flags)}." if flags else "."))
    add("")

    # -- membership --------------------------------------------------------- #
    add(f"## Members ({len(members)})")
    add("")
    add("| Ticker | Name | Exchange | Industry | Market cap | Source |")
    add("|---|---|---|---|---|---|")
    for cand in sorted(members, key=lambda c: c.ticker):
        anchor = " ⚓" if cand.code.upper() in {a.split(".")[0].upper()
                                               for a in defn.anchors} else ""
        cap = cap_cell(cand)
        marks = f" {symbols(cand.flags)}" if cand.flags else ""      # COHORT-3: see the legend
        add(f"| {cand.ticker}{marks}{anchor} | {cand.name} | {cand.exchange} | {cand.industry} "
            f"| {cap} | {cand.source}"
            + (f" (+{'/'.join(cand.filled)}: yfinance)" if cand.filled else "") + " |")
    add("")
    if defn.anchors:
        add("⚓ marks an anchor. An anchor is included only because it passed the same "
            "rules as everything else.")
        add("")

    # -- removals ----------------------------------------------------------- #
    add(f"## Removed ({len(removals)})")
    add("")
    if not removals:
        add("Nothing was removed.")
    else:
        for rule_id, rule_name in RULE_NAMES.items():
            group = [r for r in removals if r.rule == rule_id]
            if not group:
                continue
            add(f"### Rule {rule_id} — {rule_name} ({len(group)})")
            add("")
            for r in group:
                add(f"- `{r.ticker}` — {r.reason}")
            add("")
    add("")

    # -- corrections: flagged, never hidden (COHORT-3) ----------------------- #
    add("## Corrections and exclusions")
    add("")
    legend = legend_lines(members, excluded)
    if not legend:
        add("No member of this cohort carries a correction flag, and no company was excluded "
            "for its size.")
    else:
        add("Every company a correction touched appears once, with a symbol above; this says what "
            "was done to each. Nothing here changes who is a member.")
        add("")
        for line in legend:
            add(f"    {line}" if line else "")
    add("")
    if strategy_id:
        add(f"_Ranker-only run under `{strategy_id}`. No model was called._")
    return "\n".join(out).rstrip() + "\n"


def render_diff(*, defn: CohortDefinition, version: int, added: list[Candidate],
                removed: list[Candidate], unchanged: int) -> str:
    out = [f"# {defn.name} — what a rebuild would change (against v{version})", ""]
    if not added and not removed:
        out += [f"Nothing. All {unchanged} member(s) would be rebuilt identically.", ""]
        return "\n".join(out)
    out += [f"{unchanged} member(s) unchanged, {len(added)} would join, "
            f"{len(removed)} would leave.", ""]
    if added:
        out += [f"## Would join ({len(added)})", ""]
        out += [f"- `{c.ticker}` {c.name} — {c.industry}" for c in sorted(added, key=lambda c: c.ticker)]
        out += [""]
    if removed:
        out += [f"## Would leave ({len(removed)})", ""]
        out += [f"- `{c.ticker}` {c.name}" for c in sorted(removed, key=lambda c: c.ticker)]
        out += [""]
    out += ["_Nothing was written. Run `build --name ... --rebuild` to cut the next "
            "version._"]
    return "\n".join(out)
