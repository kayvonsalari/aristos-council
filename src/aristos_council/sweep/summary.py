"""SWEEP-1 - ``summary.md``: one table per entry, then every finding.

Plain text assembly over the results; nothing here judges. "No findings" is stated, never implied by
an empty list."""
from __future__ import annotations

from collections import Counter

from . import checks

NONE = "no findings"


def _cell(v) -> str:
    return str(v if v not in (None, "") else "-").replace("|", "/").replace("\n", " ")


def entry_table(r) -> list[str]:
    f = r.facts or {}
    lines = [f"### {r.entry.id}", ""]
    if r.status != "ok":
        reason = (r.error.strip().splitlines() or ["failed"])[-1]
        return lines + [f"**Failed** after {r.seconds}s: `{reason}` (traceback in `{r.entry.id}/error.txt`)", ""]
    lines += ["| | |", "| --- | --- |",
              f"| Verdict of record | {_cell(f.get('verdict'))} |",
              f"| Lens votes | {_cell(f.get('votes'))} |",
              f"| Judge split | {_cell(f.get('judge_split'))} |",
              f"| Council present | {_cell(f.get('council'))} |",
              f"| Finding count | {len(r.findings)} |",
              f"| Model calls / approx cost | {r.calls} / ${r.cost:.2f} |", ""]
    rows = f.get("rows")
    if rows:
        lines += ["| Name | BUY votes | SELL votes |", "| --- | --- | --- |"]
        lines += [f"| {_cell(x.get('name'))} | {_cell(x.get('buy'))} | {_cell(x.get('sell'))} |"
                  for x in rows]
        lines.append("")
    return lines


def summary_markdown(manifest: dict, results: list) -> str:
    t = manifest["totals"]
    out = [f"# Sweep summary - {manifest['set']}", "",
           f"Commit `{manifest['git'].get('commit', '?')[:10]}` "
           f"({manifest['git'].get('branch', '')}"
           f"{', uncommitted changes' if manifest['git'].get('dirty') else ''}); "
           f"started {manifest['started']}; flags: judges "
           f"{'on' if manifest['flags']['judges'] else 'off'}, council "
           f"{'on' if manifest['flags']['council'] else 'off'} (per-entry settings in the manifest).",
           "",
           f"{t['ok']} of {t['entries']} entries ran, {t['failed']} failed; {t['model_calls']} model "
           f"calls, about ${t['approx_cost_usd']:.2f}; {t['findings']} finding(s).", ""]
    for r in results:
        out += entry_table(r)
    all_findings = [f for r in results for f in r.findings]
    out += ["## Findings", ""]
    if not all_findings:
        out += [f"**{NONE.capitalize()}** in any of the {len(results)} entries.", ""]
    else:
        by_rule = Counter(f.rule for f in all_findings)
        out += ["| Rule | Count |", "| --- | --- |"]
        out += [f"| {rule} | {by_rule.get(rule, 0)} |" for rule in checks.ALL_RULES]
        out.append("")
    for r in results:
        mine = r.findings
        out.append(f"### {r.entry.id}: "
                   + (f"{len(mine)} finding(s)" if mine else
                      NONE if r.status == "ok" else "not run (the entry failed)"))
        out.append("")
        if mine:
            out += ["| Entry | Section | Line | Rule | Detail |", "| --- | --- | --- | --- | --- |"]
            out += [f"| {_cell(f.entry)} | {_cell(f.section or '(top)')} | "
                    f"{_cell(f.file + (':' + str(f.line) if f.line else ''))} | "
                    f"{_cell(f.rule)} | {_cell(f.detail)} |" for f in mine]
            out.append("")
    return "\n".join(out).rstrip() + "\n"
