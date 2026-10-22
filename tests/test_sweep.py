"""SWEEP-1 - the sweep command: set file, orchestration, manifest, summary and every check rule.

Offline: the producers are fakes. The real producers call the live pipeline and are exercised by
running the sweep (docs/SWEEP.md), which is the shipping duty's "see it work".
"""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest
import yaml

from aristos_council.sweep import checks
from aristos_council.sweep import runner as sw
from aristos_council.sweep.__main__ import main as sweep_main
from aristos_council.sweep.sets import (DEFAULT_PATH, Entry, SweepSet, SweepSetError, get_set,
                                        load_sets)
from aristos_council.sweep.summary import summary_markdown


# --------------------------------------------------------------------------- #
# the set file
# --------------------------------------------------------------------------- #
def _write(tmp_path, rows, name="awkward"):
    p = tmp_path / "sets.yaml"
    p.write_text(yaml.safe_dump({"version": 1, "sets": {name: rows}}), encoding="utf-8")
    return p


def test_the_tracked_awkward_set_parses_in_the_order_the_owner_named():
    s = get_set("awkward", DEFAULT_PATH)
    assert [e.id for e in s.entries] == [
        "KO.US", "F.US", "VKTX.US", "NVCR.US", "1211.HK", "JPM.US", "MSFT.US",
        "list_diversified_mining", "list_nvda_msft_aapl", "list_oil_6", "list_ko_1"]
    by = {e.id: e for e in s.entries}
    assert by["KO.US"].council is True and not by["F.US"].council
    assert all(e.lenses == "all_stock" for e in s.entries)
    assert by["list_diversified_mining"].cohort == "materials_diversified_mining"
    assert by["list_nvda_msft_aapl"].tickers == ("NVDA", "MSFT", "AAPL")
    assert by["list_oil_6"].tickers == ("XOM", "CVX", "SHEL.L", "TTE.PA", "EQNR.OL", "CNQ.TO")
    assert by["list_ko_1"].tickers == ("KO",)


def test_companies_are_ordered_before_lists():
    s = SweepSet("x", (Entry("a", "list", tickers=("X",)), Entry("b", "company", ticker="B"),
                       Entry("c", "list", tickers=("Y",)), Entry("d", "company", ticker="D")))
    assert [e.id for e in s.ordered()] == ["b", "d", "a", "c"]


@pytest.mark.parametrize("row,fragment", [
    ({"id": "a", "kind": "fund", "ticker": "X"}, "kind"),
    ({"id": "a", "kind": "company"}, "ticker"),
    ({"id": "a b", "kind": "company", "ticker": "X"}, "folder name"),
    ({"id": "a", "kind": "list"}, "exactly one"),
    ({"id": "a", "kind": "list", "cohort": "c", "tickers": ["X"]}, "exactly one"),
    ({"id": "a", "kind": "list", "tickers": ["X"], "council": True}, "council"),
    ({"id": "a", "kind": "company", "ticker": "X", "lenses": "everything"}, "lenses"),
    ({"id": "a", "kind": "company", "ticker": "X", "colour": "red"}, "unknown"),
])
def test_a_bad_entry_is_refused_by_name(tmp_path, row, fragment):
    with pytest.raises(SweepSetError, match=fragment):
        load_sets(_write(tmp_path, [row]))


def test_duplicate_ids_and_unknown_sets_are_refused(tmp_path):
    rows = [{"id": "a", "kind": "company", "ticker": "X"}, {"id": "a", "kind": "company", "ticker": "Y"}]
    with pytest.raises(SweepSetError, match="duplicate"):
        load_sets(_write(tmp_path, rows))
    with pytest.raises(SweepSetError, match="no set named"):
        get_set("nope", _write(tmp_path, rows[:1]))


def test_entry_flags_override_the_command_line():
    plain = Entry("a", "company", ticker="A")
    ko = Entry("k", "company", ticker="K", council=True)
    off = Entry("o", "company", ticker="O", judges=False)
    lst = Entry("l", "list", tickers=("X",))
    assert sw.effective_flags(plain, judges=True, council=False) == {"judges": True, "council": False}
    assert sw.effective_flags(plain, judges=True, council=True)["council"] is True
    assert sw.effective_flags(ko, judges=True, council=False)["council"] is True
    assert sw.effective_flags(off, judges=True, council=False)["judges"] is False
    assert sw.effective_flags(lst, judges=True, council=True)["council"] is False   # company-page option


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
def _set(*specs):
    return SweepSet("t", tuple(Entry(i, k, ticker=i if k == "company" else "", tickers=("X",)
                                     if k == "list" else ()) for i, k in specs))


def _fake(files_for=None, fail=(), delay=0.0, facts=None, log=None):
    def producer(entry, flags, say):
        if log is not None:
            log.append((entry.id, time.monotonic(), "start"))
        say("working")
        if entry.id in fail:
            raise RuntimeError(f"boom in {entry.id}")
        time.sleep(delay)
        files = files_for(entry) if files_for else {"report.md": f"# {entry.id}\n\nclean text.\n"}
        if log is not None:
            log.append((entry.id, time.monotonic(), "end"))
        return sw.Products(files=files, facts=facts or {"verdict": "BUY", "votes": "BUY 3 / HOLD 0 / SELL 0",
                                                         "judge_split": "BUY 2", "council": "no"},
                           calls=2, cost=0.05)
    return producer


def test_one_entry_failing_does_not_stop_the_sweep(tmp_path):
    s = _set(("A", "company"), ("B", "company"), ("C", "company"), ("L", "list"))
    manifest, results = sw.run_sweep(s, tmp_path, {"company": _fake(fail={"B"}), "list": _fake()},
                                     workers=2, say=lambda m: None)
    status = {e["id"]: e["status"] for e in manifest["entries"]}
    assert status == {"A": "ok", "B": "failed", "C": "ok", "L": "ok"}
    err = (tmp_path / "B" / "error.txt").read_text(encoding="utf-8")
    assert "RuntimeError: boom in B" in err and "Traceback" in err
    assert (tmp_path / "C" / "report.md").exists() and (tmp_path / "L" / "report.md").exists()
    assert sw.exit_code(results) == 0
    assert "failed" in sw.status_line(manifest)
    assert "Failed" in (tmp_path / "summary.md").read_text(encoding="utf-8")


def test_exit_code_is_non_zero_only_when_every_entry_failed(tmp_path):
    s = _set(("A", "company"), ("L", "list"))
    _m, results = sw.run_sweep(s, tmp_path, {"company": _fake(fail={"A"}), "list": _fake(fail={"L"})},
                               say=lambda m: None)
    assert sw.exit_code(results) == 1
    assert sw.exit_code([]) == 0


def test_concurrent_entries_do_not_interleave_their_files(tmp_path):
    ids = [f"CO{i}" for i in range(6)]
    s = _set(*[(i, "company") for i in ids])
    body = lambda e: {"report.md": f"# {e.id}\n" + "\n".join(f"{e.id} line {n}" for n in range(200)),
                      "report.txt": f"{e.id}\n" * 50}
    log: list = []
    sw.run_sweep(s, tmp_path, {"company": _fake(files_for=body, delay=0.05, log=log), "list": _fake()},
                 workers=4, say=lambda m: None)
    for i in ids:
        md = (tmp_path / i / "report.md").read_text(encoding="utf-8").splitlines()
        assert len(md) == 201 and md[0] == f"# {i}"
        assert all(ln.startswith(i) for ln in md[1:]), f"{i}'s report holds another entry's lines"
        assert set((tmp_path / i / "report.txt").read_text(encoding="utf-8").split()) == {i}
        assert not (tmp_path / i / "error.txt").exists()
    starts = [t for _i, t, kind in log if kind == "start"]
    ends = [t for _i, t, kind in log if kind == "end"]
    assert min(ends) > sorted(starts)[1], "the entries really overlapped (workers=4)"


def test_lists_run_after_every_company_and_one_at_a_time(tmp_path):
    s = _set(("L1", "list"), ("A", "company"), ("L2", "list"), ("B", "company"))
    log: list = []
    sw.run_sweep(s, tmp_path, {"company": _fake(delay=0.05, log=log), "list": _fake(delay=0.05, log=log)},
                 workers=2, say=lambda m: None)
    t = {(i, kind): at for i, at, kind in log}
    assert max(t["A", "end"], t["B", "end"]) <= min(t["L1", "start"], t["L2", "start"])
    assert t["L1", "end"] <= t["L2", "start"]


def test_manifest_records_commit_times_flags_status_calls_and_cost(tmp_path):
    s = _set(("A", "company"), ("L", "list"))
    sw.run_sweep(s, tmp_path, {"company": _fake(), "list": _fake()}, judges=False, council=True,
                 workers=1, say=lambda m: None)
    m = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert m["finished"] is True and m["started"] and m["ended"]
    assert set(m["git"]) >= {"commit", "branch", "dirty"} and m["git"]["commit"]
    assert m["flags"] == {"judges": False, "council": True}
    assert [e["id"] for e in m["entries"]] == ["A", "L"]
    a, l = m["entries"]
    assert a["status"] == "ok" and a["files"] == ["report.md"] and a["model_calls"] == 2
    assert a["approx_cost_usd"] == 0.05 and a["flags"] == {"judges": False, "council": True}
    assert l["flags"]["council"] is False
    assert m["totals"] == {"entries": 2, "ok": 2, "failed": 0, "model_calls": 4,
                           "approx_cost_usd": 0.1, "findings": 0}


def test_the_manifest_is_written_as_entries_finish_so_an_interrupted_sweep_leaves_one(tmp_path):
    seen = []

    def producer(entry, flags, say):
        if entry.id == "B":
            seen.append(json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8")))
        return sw.Products(files={"report.md": "# x\n\nok.\n"})
    sw.run_sweep(_set(("A", "company"), ("B", "company")), tmp_path,
                 {"company": producer, "list": producer}, workers=1, say=lambda m: None)
    assert seen[0]["finished"] is False
    assert [e["id"] for e in seen[0]["entries"]] == ["A"]


def test_cli_runs_a_set_end_to_end_and_writes_all_three_files(tmp_path, capsys):
    p = _write(tmp_path, [{"id": "A", "kind": "company", "ticker": "A"},
                          {"id": "L", "kind": "list", "tickers": ["X"]}])
    out = tmp_path / "out"
    code = sweep_main(["run", "--sets-file", str(p), "--out", str(out), "--workers", "1", "--no-judges"],
                      producers={"company": _fake(), "list": _fake()})
    assert code == 0
    assert (out / "manifest.json").exists() and (out / "summary.md").exists()
    shown = capsys.readouterr().out
    assert "status: A ok | L ok" in shown
    assert sweep_main(["run", "--sets-file", str(p), "--set", "zzz", "--out", str(out)],
                      producers={}) == 2


# --------------------------------------------------------------------------- #
# the summary
# --------------------------------------------------------------------------- #
def test_summary_has_one_table_per_entry_and_says_no_findings_explicitly(tmp_path):
    s = _set(("A", "company"), ("L", "list"))
    facts = {"verdict": "BUY: BUY on 3 of 3", "votes": "BUY 3 / HOLD 0 / SELL 0",
             "judge_split": "BUY 2 / HOLD 1", "council": "yes",
             "rows": [{"name": "Acme", "buy": 2, "sell": 0}]}
    sw.run_sweep(s, tmp_path, {"company": _fake(facts=facts), "list": _fake(facts=facts)},
                 say=lambda m: None)
    text = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert text.count("| Verdict of record |") == 2 and "### A" in text and "### L" in text
    for label in ("Lens votes", "Judge split", "Council present", "Finding count"):
        assert label in text
    assert "BUY 2 / HOLD 1" in text and "| Acme | 2 | 0 |" in text
    assert "No findings** in any of the 2 entries" in text
    assert "### A: no findings" in text and "### L: no findings" in text


def test_summary_lists_every_finding_with_entry_section_line_and_rule(tmp_path):
    bad = lambda e: {"report.md": "# Title\n\n## Lens table\n\nIt is $-5bn here.\n"}
    sw.run_sweep(_set(("A", "company")), tmp_path, {"company": _fake(files_for=bad), "list": _fake()},
                 say=lambda m: None)
    text = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "| A | Lens table | report.md:5 | '$-' money formatting |" in text
    assert "### A: 1 finding\n" in text and "No findings**" not in text


def test_judge_rows_that_failed_are_reported_with_their_reason(tmp_path):
    panel = {"available": True, "judges": [
        {"label": "Gemini", "status": "error", "note": "503", "calls": []},
        {"label": "Claude", "status": "stable", "calls": [{"status": "ok", "reason": "fine"}]}]}

    def producer(entry, flags, say):
        return sw.Products(files={"report.md": "# x\n\nok.\n"}, judge_panel=panel)
    _m, results = sw.run_sweep(_set(("A", "company")), tmp_path, {"company": producer, "list": producer},
                               say=lambda m: None)
    (f,) = results[0].findings
    assert f.rule == checks.R_JUDGE_ROW and "503" in f.detail and f.section == "Gemini"


# --------------------------------------------------------------------------- #
# every rule: a positive and a negative fixture
# --------------------------------------------------------------------------- #
def _md(text, **kw):
    return checks.check_markdown(text, entry="E", **kw)


def _rules(text, **kw):
    return {f.rule for f in _md(text, **kw)}


CLEAN = ("# Report\n\n## The answer\n\nCoca-Cola is a steady dividend payer.\n\n"
         "| Lens | Verdict |\n| --- | --- |\n| Quality | BUY |\n")


def test_a_clean_report_has_no_findings():
    assert _md(CLEAN, ticked=["Quality"], names=["The Coca-Cola Company"]) == []


def test_rule_internal_ids():
    assert checks.R_INTERNAL_IDS in _rules("## A\n\nRanked by magic_formula_raw_v1 today.\n")
    assert checks.R_INTERNAL_IDS not in _rules("## A\n\nRanked by Magic Formula RAW today.\n")


def test_rule_money_minus():
    assert checks.R_MONEY in _rules("Free cash flow was $-147.8bn.\n")
    assert checks.R_MONEY not in _rules("Free cash flow was -$147.8bn.\n")


def test_rule_unit_scale_within_one_series():
    bad = "| Series | 2022 | 2023 | 2024 |\n| --- | --- | --- | --- |\n| Revenue | $900m | $1.1bn | $1.4bn |\n"
    ok = "| Series | 2022 | 2023 | 2024 |\n| --- | --- | --- | --- |\n| Revenue | $0.9bn | $1.1bn | $1.4bn |\n"
    companies = "| Name | Market cap |\n| --- | --- |\n| A | $800m |\n| B | $12bn |\n"
    assert checks.R_UNIT_SCALE in _rules(bad)
    assert checks.R_UNIT_SCALE not in _rules(ok)
    assert checks.R_UNIT_SCALE not in _rules(companies)       # a column of companies is not a series


def test_rule_singular_plural_slips():
    assert checks.R_PLURAL in _rules("1 name were left out.\n")           # report_sweep's own shape
    assert checks.R_PLURAL in _rules("It ranked 1 companies.\n")
    assert checks.R_PLURAL in _rules("It ranked 3 company in all.\n")
    assert checks.R_PLURAL in _rules("Pick the name(s) you like.\n")
    assert checks.R_PLURAL not in _rules("It ranked 1 company and 3 companies, 2 lenses.\n")
    assert checks.R_PLURAL not in _rules("Version 1.5 names the 12 largest.\n")


def test_rule_unmatched_markup():
    assert checks.R_MARKUP in _rules("This is **bold and never closed.\n")
    assert checks.R_MARKUP in _rules("A stray _emphasis here.\n")
    assert checks.R_MARKUP not in _rules("This is **bold** and _italic_.\n")


def test_rule_diagnostic_column_names_in_reader_text():
    assert checks.R_COLUMN_NAMES in _rules("The SELL votes column shows the lenses that disagreed.\n")
    assert checks.R_COLUMN_NAMES in _rules("It had a combined_rank of 3.\n")
    assert checks.R_COLUMN_NAMES not in _rules("| Name | SELL votes |\n| --- | --- |\n| A | - |\n")
    assert checks.R_COLUMN_NAMES not in _rules("Two lenses voted to sell it.\n")


def test_rule_truncated_company_names():
    assert checks.R_TRUNCATED in _rules("| The Coca-Cola | BUY |\n| --- | --- |\n",
                                        names=["The Coca-Cola Company"])
    assert checks.R_TRUNCATED in _rules("| Johnson & | BUY |\n| --- | --- |\n")
    assert checks.R_TRUNCATED in _rules("| Some Very Long Company Na… | BUY |\n| --- | --- |\n")
    assert checks.R_TRUNCATED in _rules("Johnson & is steady.\n", names=["Johnson & Johnson"])
    assert checks.R_TRUNCATED not in _rules("The Coca-Cola Company pays a dividend.\n",
                                            names=["The Coca-Cola Company"])
    assert checks.R_TRUNCATED not in _rules("| Johnson & Johnson | BUY |\n| --- | --- |\n",
                                            names=["Johnson & Johnson"])


def test_rule_repeated_paragraph():
    para = "This company has been rated on fewer factors than the others because its accounts are thin."
    assert checks.R_REPEATED in _rules(f"## Quality\n\n{para}\n\n## Value\n\n{para}\n")
    assert checks.R_REPEATED not in _rules(f"## Quality\n\n{para}\n\n## Value\n\nA different thing, said once.\n")
    notes = (f"- Quality did not apply: too few names. Treat this as a rough guide only, nothing more than that.\n"
             f"- Value did not apply: too small. Treat this as a rough guide only, nothing more than that.\n")
    assert checks.R_REPEATED in _rules(notes)
    assert checks.R_REPEATED not in _rules(
        "- Quality did not apply: too few names to rank.\n- Value did not apply: it is too small.\n")


def test_rule_ticked_lens_absent_from_the_lens_table():
    table = "| Lens | Verdict |\n| --- | --- |\n| Quality | BUY |\n"
    assert checks.R_LENS_ABSENT in _rules(table, ticked=["Quality", "Forensic"])
    assert checks.R_LENS_ABSENT not in _rules(table, ticked=["Quality"])
    assert checks.R_LENS_ABSENT not in _rules("# x\n", ticked=[])


def test_findings_carry_entry_section_line_and_rule():
    f = _md("# T\n\n## Money\n\nCash was $-5bn.\n")[0]
    assert (f.entry, f.file, f.section, f.line, f.rule) == ("E", "report.md", "Money", 5, checks.R_MONEY)
    assert checks.section_of(["a", "b"], 2) == ""


# structured rules ----------------------------------------------------------
def _ranked(*verdicts):
    return [SimpleNamespace(verdict=v, excluded=False, combined_rank=i) for i, v in enumerate(verdicts)]


def test_rule_buy_without_sell_needs_five_names():   # was "at small list sizes"; SWEEP-CHECK-TUNE-1
    multi = lambda ranked: SimpleNamespace(results={"s": SimpleNamespace(ranked=ranked)},
                                           strategy_names={"s": "Lens"}, lens_agreement=None)
    rules = lambda objs: {f.rule for f in checks.check_structured(objs, entry="E")}
    assert checks.R_BUY_NO_SELL not in rules({"multi": multi(_ranked("buy", "hold", "hold"))})   # 3 names never vote
    assert checks.R_BUY_NO_SELL not in rules({"multi": multi(_ranked("buy", "hold", "sell"))})
    assert checks.R_BUY_NO_SELL not in rules({"multi": multi(_ranked("hold", "hold", "hold"))})  # no BUY
    assert checks.R_BUY_NO_SELL not in rules({"multi": multi(_ranked("buy"))})                    # one name
    assert checks.R_BUY_NO_SELL in rules({"multi": multi(_ranked("buy", "hold", "hold", "hold", "hold"))})  # 5+
    assert checks.R_BUY_NO_SELL not in rules({"multi": multi(_ranked("buy", "hold", "hold", "hold", "sell"))})


def test_rules_sell_votes_missing_and_a_lens_stated_two_ways():
    from aristos_council.company_report import LensVote, build_agreement
    votes = [LensVote("a", "A", status="ranked", verdict="sell", position=3, cohort_size=3)]
    good = SimpleNamespace(agreement=build_agreement(votes), votes=votes, peer_group=None, lens_ranks={})
    assert checks.check_structured({"company": good}, entry="E") == []
    bad = SimpleNamespace(agreement=SimpleNamespace(sell=("A",), headline="BUY on 0 of 1 vote", checks={}),
                          votes=votes, peer_group=None, lens_ranks={})
    assert {f.rule for f in checks.check_structured({"company": bad}, entry="E")} == {checks.R_SELL_MISSING}
    forensic = LensVote("f", "Forensic", kind="check", status="excluded", reason="r")
    two_ways = SimpleNamespace(agreement=SimpleNamespace(sell=(), headline="h", checks={"Forensic": "clean"}),
                               votes=[forensic], peer_group=None, lens_ranks={})
    assert checks.R_LENS_TWO_WAYS in {f.rule for f in checks.check_structured({"company": two_ways}, entry="E")}


# judge rules ---------------------------------------------------------------
def _panel(**call):
    return {"available": True, "judges": [{"label": "Claude", "status": "stable",
                                           "calls": [{"status": "ok", "verdict": "HOLD", **call}]}]}


def test_rule_judge_field_starting_with_a_verdict_token():
    for text in ("HOLD: the lenses disagree", "SELL - too dear", "BUY.", "**BUY**: cheap"):
        found = checks.check_judges(_panel(driving_fact=text), entry="E")
        assert [f.rule for f in found] == [checks.R_JUDGE_VERDICT], text
    for text in ("The lenses say HOLD: they disagree", "Holding up well", "BUYBACKS were large", ""):
        assert checks.check_judges(_panel(driving_fact=text, reason=text), entry="E") == [], text
    reading = dict(_panel(), reading={"paragraph": "SELL: the panel is split"})
    assert [f.rule for f in checks.check_judges(reading, entry="E")] == [checks.R_JUDGE_VERDICT]


def test_rule_judge_error_or_incomplete_rows_carry_their_reason():
    bad = {"available": True, "judges": [
        {"label": "Gemini", "status": "error", "note": "503", "calls": []},
        {"label": "OpenAI", "status": "incomplete", "note": "", "calls": [
            {"status": "ok", "verdict": "BUY"}, {"status": "error", "error": "timeout"}]},
        {"label": "Claude", "status": "stable", "calls": [{"status": "ok", "verdict": "BUY"}]}]}
    found = checks.check_judges(bad, entry="E")
    assert [f.rule for f in found] == [checks.R_JUDGE_ROW] * 2
    assert "503" in found[0].detail and "timeout" in found[1].detail
    ok = {"available": True, "judges": [{"label": "Claude", "status": "stable", "calls": []},
                                        {"label": "Gemini", "status": "unstable", "calls": []},
                                        {"label": "OpenAI", "status": "not_run", "calls": []}]}
    assert checks.check_judges(ok, entry="E") == []
    assert checks.check_judges(None, entry="E") == []
    assert checks.check_judges({"available": False, "note": "no pack", "judges": []}, entry="E")[0].rule \
        == checks.R_JUDGE_ROW


def test_every_rule_has_a_name_and_the_list_is_complete():
    assert len(set(checks.ALL_RULES)) == len(checks.ALL_RULES) == 14


def test_the_cli_loads_dotenv_so_judge_and_council_keys_reach_the_run(tmp_path, monkeypatch):
    """The first real sweep ran with every judge 'not run - no key': the app loads .env in main(),
    the sweep CLI did not."""
    from aristos_council.sweep import __main__ as cli
    called = []
    monkeypatch.setattr(cli, "load_env", lambda: called.append(1))
    p = _write(tmp_path, [{"id": "A", "kind": "company", "ticker": "A"}])
    cli.main(["run", "--sets-file", str(p), "--out", str(tmp_path / "o")],
             producers={"company": _fake(), "list": _fake()})
    assert called == [1]


# SWEEP-CHECK-TUNE-1 ---------------------------------------------------------
def _rows(*pairs):
    return [SimpleNamespace(verdict=v, excluded=False, combined_rank=r) for v, r in pairs]


def test_buy_without_sell_ignores_lenses_under_five_names_and_ties_across_the_cut():
    multi = lambda ranked: {"multi": SimpleNamespace(results={"s": SimpleNamespace(ranked=ranked)},
                                                     strategy_names={"s": "Lens"}, lens_agreement=None)}
    rules = lambda objs: {f.rule for f in checks.check_structured(objs, entry="E")}
    # (a) 2-4 names never vote: no finding (F.US and 1211.HK, 2026-10-22)
    assert checks.R_BUY_NO_SELL not in rules(multi(_rows(("buy", 1), ("hold", 2))))
    assert checks.R_BUY_NO_SELL not in rules(multi(_rows(("buy", 1), ("hold", 2), ("hold", 3), ("hold", 4))))
    # (b) 5 names, bottom band = 1 name; the last two tied -> the tie straddles the SELL cut -> HOLD
    assert checks.R_BUY_NO_SELL not in rules(multi(_rows(("buy", 1), ("hold", 2), ("hold", 3), ("hold", 4), ("hold", 4))))
    # 10 names, bottom band = 2; tie of positions 8-9 (1-based) straddles the cut after 8
    ten = _rows(*[("buy" if i < 2 else "hold", r) for i, r in enumerate([1, 2, 3, 4, 5, 6, 7, 8, 8, 10])])
    assert checks.R_BUY_NO_SELL not in rules(multi(ten))
    # still flags a real asymmetry: a BUY, no tie touching the bottom band, no SELL
    assert checks.R_BUY_NO_SELL in rules(multi(_rows(("buy", 1), ("hold", 2), ("hold", 3), ("hold", 4), ("hold", 5))))
    # a tie wholly inside the bottom band is SELL by rule, so a missing SELL there is real
    ten_in = _rows(*[("buy" if i < 2 else "hold", r) for i, r in enumerate([1, 2, 3, 4, 5, 6, 7, 8, 9, 9])])
    assert checks.R_BUY_NO_SELL in rules(multi(ten_in))


def test_a_news_headline_or_quoted_title_is_not_a_truncated_name():
    md = ("# T\n\n## News\n\n- Recent news:\n"
          "  - 2026-10-19: What Does Ford Motor (F) Recall Of 3,700 Pickups Signal?\n"
          "A column called \"Ford Motor Co\" in quotes.\n")
    got = checks.truncated_name_findings(md.splitlines(), ["Ford Motor Company"])
    assert got == []
    assert checks.truncated_name_findings(["Our view of Ford Motor is mixed."], ["Ford Motor Company"])


def test_list_detail_rule_explanations_are_not_a_repeated_paragraph():
    block = ("## {} — detail\n\n**Company size** · rule: at least $5.0bn (applied before the screen) · **7 names**\n\n"
             "_Below a certain size a company's reported figures are thinner, its shares are harder to leave._\n\n"
             "Altius Minerals Corporation (ALS.TO), IGO Limited (IGO.AX), Liontown Limited (LTR.AX)\n\n")
    text = "# T\n\n" + block.format("Value") + block.format("Cyclical")
    assert checks.repeated_paragraph_findings(text.splitlines()) == []
    plain = "# T\n\n## A\n\n" + "The same long paragraph about something quite specific here. " * 2 + \
            "\n\n## B\n\n" + "The same long paragraph about something quite specific here. " * 2 + "\n"
    assert checks.repeated_paragraph_findings(plain.splitlines())
