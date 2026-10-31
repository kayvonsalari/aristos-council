"""HOLDINGS-CHECK-1 - the private holdings check. Fake data only; no adapter, no network, no model."""

from __future__ import annotations

import csv
import json
import re
import subprocess
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from aristos_council import holdings_check as hc

REPO = Path(__file__).resolve().parents[1]


def _vote(label="Value + Momentum", verdict="buy", position=2, size=14, status="ranked"):
    return SimpleNamespace(label=label, verdict=verdict, position=position, cohort_size=size,
                           status=status, ranked=status == "ranked", votes=True, word=verdict.upper(),
                           small_group_note="", reason="")


def _report(*, unrateable=False, votes=None, outside=False, band="cheap vs own history"):
    votes = [_vote()] if votes is None else votes
    agreement = SimpleNamespace(headline="BUY on 1 of 1 vote")
    di = SimpleNamespace(note="no price history", abstained_criteria=["min_roic"],
                         not_evaluated_factors=[], implausible=[])
    check = SimpleNamespace(data_integrity=di, valuation_band=band)
    return SimpleNamespace(unrateable=unrateable, votes=votes, agreement=agreement, check=check,
                           peer_group=SimpleNamespace(members=list(range(13)), band="0.25x-4x",
                                                      thin=False),
                           cohort_slug="software", outside_tested_range=outside,
                           size_matched_peers=False, no_vote_reason="")


@pytest.fixture
def local(tmp_path):
    root = tmp_path / "data" / "local"
    root.mkdir(parents=True)
    return root


@pytest.fixture(autouse=True)
def _fake_story(monkeypatch):
    import aristos_council.company_story as cs
    monkeypatch.setattr(cs, "story_page", lambda r: SimpleNamespace(answer=("BUY - fake", "x")))


def _run(holdings, local, **kw):
    seen = []

    def runner(ticker, *, runs_dir, with_council, progress=None):
        seen.append((ticker, with_council))
        if ticker == "NOPE":
            return _report(unrateable=True)
        if ticker == "BOOM":
            raise RuntimeError("boom")
        return _report()
    result = hc.run_check(holdings, local / "holdings", runner=runner, local_root=local,
                          markdown=lambda r: "# fake report\n", today=date(2026, 11, 1), **kw)
    return result, seen


# ------------------------------------------------------------------ CSV parsing
def test_parse_reads_columns_by_header_in_any_order_and_ignores_blank_lines():
    text = "Type,Ticker,Name,Broker,Note\nstock,AAA,Alpha Co,B1,\n\n,,,,\netf,BBB,Beta ETF,B2,x\n"
    rows, problems = hc.parse_holdings(text)
    assert [(h.name, h.ticker, h.type, h.broker) for h in rows] == [
        ("Alpha Co", "AAA", "stock", "B1"), ("Beta ETF", "BBB", "etf", "B2")]
    assert problems == []


def test_parse_reports_a_missing_column_and_a_row_with_nothing_in_it():
    assert hc.parse_holdings("name,broker\nA,B\n")[0] == []
    assert "ticker" in hc.parse_holdings("name,broker\nA,B\n")[1][0]
    rows, problems = hc.parse_holdings("name,ticker,type\n,,stock\nZed,ZZZ,stock\n")
    assert [h.ticker for h in rows] == ["ZZZ"] and len(problems) == 1


def test_parse_has_no_amount_column_in_the_template(tmp_path):
    hc.write_template(tmp_path / "h.csv")
    header = (tmp_path / "h.csv").read_text().splitlines()[0]
    assert header == "name,ticker,broker,type,note"
    assert len((tmp_path / "h.csv").read_text().splitlines()) == 3


def test_a_missing_file_is_seeded_from_a_local_holdings_json_without_amounts(local):
    (local / "holdings.json").write_text(json.dumps(
        [{"ticker": "AAA", "name": "Alpha", "shares": 10, "value": 999}]))
    msg = hc.seed_or_template(local / "holdings" / "holdings.csv", local_root=local)
    text = (local / "holdings" / "holdings.csv").read_text()
    assert "AAA" in text and "999" not in text and "10" not in text.split("\n", 1)[1].replace("AAA", "")
    assert "holdings.json" in msg


def test_without_a_legacy_file_a_two_row_template_is_written(local):
    msg = hc.seed_or_template(local / "holdings" / "holdings.csv", local_root=local)
    assert "template" in msg
    assert len(hc.parse_holdings((local / "holdings" / "holdings.csv").read_text())[0]) == 2


# ------------------------------------------------------------------ routing
def test_only_stocks_reach_the_engine_and_the_rest_are_skipped_with_a_reason(local):
    hs = [hc.Holding("Alpha", "AAA"), hc.Holding("Beta ETF", "BBB", type="etf"),
          hc.Holding("Gamma Fund", "GGG", type="fund"), hc.Holding("Cash", "CCC", type="other"),
          hc.Holding("Weird", "WWW", type="warrant")]
    result, seen = _run(hs, local)
    assert seen == [("AAA", False)]
    by = {r.ticker: r for r in result.rows}
    assert by["AAA"].status == "graded"
    for t in ("BBB", "GGG", "CCC", "WWW"):
        assert by[t].status == "skipped" and by[t].reason.startswith("skipped:")


def test_an_unresolved_ticker_is_listed_with_its_reason_and_never_guessed(local):
    hs = [hc.Holding("No ticker", ""), hc.Holding("Path", "examples/x.py"),
          hc.Holding("Gone", "NOPE"), hc.Holding("Crashy", "BOOM"), hc.Holding("Ok", "AAA")]
    result, seen = _run(hs, local)
    assert seen == [("NOPE", False), ("BOOM", False), ("AAA", False)]
    by = {r.name: r for r in result.rows}
    assert by["No ticker"].status == "unresolved" and "never guessed" in by["No ticker"].reason
    assert by["Path"].status == "unresolved"
    assert by["Gone"].status == "unresolved" and "no data" in by["Gone"].reason
    assert by["Crashy"].status == "failed" and "boom" in by["Crashy"].reason
    assert by["Ok"].status == "graded"      # one bad name never stops the rest


def test_names_filter_matches_name_or_ticker(local):
    hs = [hc.Holding("Alpha", "AAA"), hc.Holding("Beta", "BBB")]
    _, seen = _run(hs, local, names=["bbb"])
    assert seen == [("BBB", False)]


def test_council_is_off_unless_asked(local):
    _, seen = _run([hc.Holding("Alpha", "AAA")], local)
    assert seen == [("AAA", False)]
    _, seen = _run([hc.Holding("Alpha", "AAA")], local, with_council=True)
    assert seen == [("AAA", True)]


# ------------------------------------------------------------------ summary
def test_summary_csv_has_one_row_per_holding_with_the_named_columns(local):
    hs = [hc.Holding("Alpha", "AAA"), hc.Holding("Beta ETF", "BBB", type="etf")]
    _run(hs, local)
    with (local / "holdings" / "summary.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == ["name", "ticker", "status", "cohort_or_peer_group", "verdict",
                             "rank_in_group", "lenses_voting", "valuation_band", "warnings",
                             "report"]
    assert len(rows) == 2
    a = rows[0]
    assert a["cohort_or_peer_group"] == "cohort software" and a["verdict"].startswith("BUY")
    assert a["rank_in_group"] == "2 of 14 (Value + Momentum)"
    assert a["valuation_band"] == "cheap vs own history"
    assert "min_roic" in a["warnings"] and a["report"] == "reports/aaa.md"
    assert rows[1]["verdict"].startswith("skipped")
    md = (local / "holdings" / "summary.md").read_text(encoding="utf-8")
    assert "[report](reports/aaa.md)" in md and (local / "holdings" / "reports" / "aaa.md").is_file()


def test_a_run_outside_the_tested_range_carries_the_warning(local):
    rep = _report(outside=True)
    row = hc.row_from_report(hc.Holding("Small", "SML"), rep, "reports/sml.md")
    assert "outside tested range" in row.warnings


# ------------------------------------------------------------------ dated
def test_dated_keeps_a_copy_and_lists_what_changed_since_the_last_dated_run(local):
    hs = [hc.Holding("Alpha", "AAA")]
    hc.run_check(hs, local / "holdings", runner=lambda t, **k: _report(), local_root=local,
                 markdown=lambda r: "x", dated=True, today=date(2026, 10, 1))
    assert (local / "holdings" / "dated" / "2026-10-01" / "summary.csv").is_file()
    sell = _report(votes=[_vote(verdict="sell", position=13)])
    result = hc.run_check(hs, local / "holdings", runner=lambda t, **k: sell, local_root=local,
                          markdown=lambda r: "x", dated=True, today=date(2026, 11, 1))
    assert any("rank was '2 of 14" in c for c in result.changes)
    md = (local / "holdings" / "summary.md").read_text(encoding="utf-8")
    assert "## Changed since 2026-10-01" in md


def test_without_dated_there_is_no_changes_section(local):
    _run([hc.Holding("Alpha", "AAA")], local)
    assert "Changed since" not in (local / "holdings" / "summary.md").read_text(encoding="utf-8")
    assert not (local / "holdings" / "dated").exists()


# ------------------------------------------------------------------ containment
def test_nothing_is_written_outside_data_local(tmp_path, local):
    with pytest.raises(hc.OutsideLocalError):
        hc.run_check([hc.Holding("Alpha", "AAA")], tmp_path / "elsewhere", local_root=local,
                     runner=lambda t, **k: _report(), markdown=lambda r: "x")
    assert not (tmp_path / "elsewhere").exists()


def test_a_run_leaves_only_data_local_changed(tmp_path, local):
    before = {p for p in tmp_path.rglob("*")}
    _run([hc.Holding("Alpha", "AAA")], local, dated=True)
    new = {p for p in tmp_path.rglob("*")} - before
    assert new and all(local in p.parents for p in new)


def test_the_default_locations_are_under_the_gitignored_local_folder():
    assert hc.HOLDINGS_DIR.resolve().is_relative_to(hc.LOCAL_ROOT.resolve())
    probe = subprocess.run(["git", "check-ignore", "-q", "data/local/holdings/holdings.csv"],
                           cwd=REPO, capture_output=True)
    assert probe.returncode == 0


# ------------------------------------------------------------------ confidentiality tripwire
def _tracked() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True,
                             check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [p for p in out.splitlines() if p]


def test_no_scout_report_or_holdings_file_is_tracked():
    """The retired scout job committed the owner's holdings and a private sheet URL to this public
    repo. These paths must never come back (the growth-scanner reports are not holdings)."""
    banned = ("reports/scout/holdings/", "reports/scout/ft/", "reports/scout/economist/",
              "reports/scout/latest.json", "data/local/holdings/")
    bad = [p for p in _tracked() if p.startswith(banned) and not p.endswith(".gitkeep")]
    holdings_files = [p for p in _tracked()
                      if re.search(r"(^|/)holdings[^/]*\.(csv|txt|json)$", p, re.I)
                      and not p.startswith(("tests/", "docs/"))]
    assert not bad and not holdings_files, (bad, holdings_files)


def test_no_google_sheet_url_or_id_is_tracked():
    url = re.compile(r"docs\.google\.com/spreadsheets/d/" + r"[A-Za-z0-9_-]{20,}")
    ident = re.compile(r"(?:SHEET_ID|spreadsheet-id)\W{1,3}1[A-Za-z0-9_-]{40,}")
    hits = []
    for p in _tracked():
        if p == "tests/test_holdings_check.py" or p.endswith((".png", ".ico", ".parquet", ".pdf")):
            continue
        try:
            text = (REPO / p).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if url.search(text) or ident.search(text):
            hits.append(p)
    assert not hits, hits
