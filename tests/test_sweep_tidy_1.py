"""SWEEP-TIDY-1 - three fixes to the sweep tool's own output, from the 2026-10-22 awkward sweep.

(i)   JPM.US Peers: "'JPMorgan Chase & Co.' appears as 'JPMorgan Chase &'" - a false positive. The
      peers table prints the index's own spelling "JPMorgan Chase & Co" (no full stop), which is
      the whole name. Real cuts still fire.
(ii)  summary.md: "1 names in the list" (and "1 finding(s)") - counts go through plurals.plural.
(iii) 62 "a repeated paragraph" findings, most of them the list report's per-lens boilerplate
      ("Used as a prefilter - ...", "This lens screens nothing: ...", "⚠ = the share price is up
      more than 30% ...", "Ranked 0 of 23 names. Excluded 23: by rule below, worst miss first.").
      Those are the layout; real repeats still fire.
"""
from __future__ import annotations

from types import SimpleNamespace

from aristos_council.pipeline import (NO_SCREEN_NOTE, NON_PREFILTER_NOTE, PREFILTER_NOTE,
                                      detail_badge_line)
from aristos_council.sweep import checks
from aristos_council.sweep.producers import list_verdict_fact
from aristos_council.sweep.summary import summary_markdown

JPM = "JPMorgan Chase & Co."


# (i) ---------------------------------------------------------------------
def test_the_index_spelling_without_the_full_stop_is_not_a_cut():
    peers = "| JPM | JPMorgan Chase & Co | NYSE | $800.0bn |\n"
    assert checks.truncated_name_findings(peers.splitlines(), [JPM]) == []
    assert checks.truncated_name_findings(["JPMorgan Chase & Co. reported."], [JPM]) == []


def test_a_real_cut_of_the_same_name_still_fires():
    for line in ("JPMorgan Chase & is a bank.", "| JPMorgan Chase & | HOLD |",
                 "JPMorgan Chase & Cohen merged."):
        assert checks.truncated_name_findings([line], [JPM]), line


def test_other_names_are_unchanged():
    found = checks.truncated_name_findings(["Analysts expect The Coca-Cola to earn"],
                                           ["The Coca-Cola Company"])
    assert found and "appears as 'The Coca-Cola'" in found[0][1]


# (ii) --------------------------------------------------------------------
def test_one_name_in_the_list_is_singular():
    assert list_verdict_fact(1, "", 0) == "1 name in the list; 0 with at least one BUY"
    assert list_verdict_fact(6, "Oil", 4) == "6 names in the list (Oil); 4 with at least one BUY"


def _result(n_findings):
    finding = checks.SweepFinding("A", "report.md", "S", 1, checks.R_MONEY, "x")
    return SimpleNamespace(entry=SimpleNamespace(id="A"), status="ok", facts={}, findings=[finding] * n_findings,
                           seconds=1, calls=0, cost=0.0, error="")


def _manifest(n):
    return {"totals": {"ok": 1, "entries": 1, "failed": 0, "model_calls": 0, "approx_cost_usd": 0.0,
                       "findings": n}, "git": {"commit": "abc", "branch": "b", "dirty": False},
            "started": "now", "flags": {"judges": False, "council": False}, "set": "s"}


def test_summary_counts_findings_in_words():
    one = summary_markdown(_manifest(1), [_result(1)])
    assert "1 finding." in one and "### A: 1 finding\n" in one and "finding(s)" not in one
    two = summary_markdown(_manifest(2), [_result(2)])
    assert "2 findings." in two and "### A: 2 findings\n" in two


# (iii) -------------------------------------------------------------------
def _rules_section(note: str) -> str:
    return "".join(f"### {lens}\n\n{note}\n\n| Rule | Limit |\n| --- | --- |\n| X | Y |\n\n"
                   for lens in ("Value + Momentum", "Defensive Income", "Growth"))


def test_the_rules_applied_notes_are_not_repeats():
    for note in (PREFILTER_NOTE, NO_SCREEN_NOTE, NON_PREFILTER_NOTE):
        assert checks.repeated_paragraph_findings(_rules_section(note).splitlines()) == [], note


def test_the_detail_legend_and_headline_are_not_repeats():
    text = "".join(f"### {lens} — detail\n\n**Ranked 0 of 23 names. Excluded 23: by rule below, "
                   f"worst miss first.**\n\n{detail_badge_line()}\n\n"
                   for lens in ("Value + Momentum", "Defensive Income"))
    assert checks.repeated_paragraph_findings(text.splitlines()) == []


def test_a_real_repeat_still_fires_beside_the_boilerplate():
    para = "This company has been rated on fewer factors than the others because its accounts are thin."
    text = _rules_section(PREFILTER_NOTE) + f"## Quality\n\n{para}\n\n## Value\n\n{para}\n"
    found = checks.repeated_paragraph_findings(text.splitlines())
    assert len(found) == 1 and "fewer factors" in found[0][1]
