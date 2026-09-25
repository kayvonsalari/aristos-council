"""COHORT-3 - cohorts built from the market index instead of S&P 500 + STOXX 600 constituents.

Every table is fabricated and nothing reaches the network or the real index: ``plan`` is asserted to
make no request at all, and the shipped definition file is read as a file. The COHORT-1 tests
(``test_cohorts.py``) are untouched and prove the old path still builds what it built.
"""
from __future__ import annotations

import csv
import socket
import urllib.request
from datetime import date
from pathlib import Path

import pytest

from aristos_council.cohorts import __main__ as cli
from aristos_council.cohorts import builder
from aristos_council.cohorts.builder import (DEFAULT_DEFINITIONS, DEFAULT_INDEX_DEFINITIONS, build,
                                             format_plan, plan, resolve_path)
from aristos_council.cohorts.cleanup import clean, size_verdict
from aristos_council.cohorts.definitions import (INDEX_EODHD_INDUSTRIES, DefinitionError,
                                                 definition_from_mapping, load_definitions,
                                                 watched)
from aristos_council.cohorts.freeze import MEMBERS_FILE, read_members, write_members
from aristos_council.cohorts.source import (PATH_CONSTITUENTS, PATH_INDEX, Candidate,
                                            build_pool_from_index, candidate_from_index_row)
from aristos_council.market_index import (SOURCE_EODHD_LISTING, IdentityAlias, IndexRow, clean_pool,
                                          peers)
from tests.test_cohorts import _fake_ranker, _history

SNAPSHOT = "2026-09-25"
ROOT = Path(__file__).resolve().parents[1]


def _row(ticker, name="", *, industry="Steel", sector="Basic Materials", cap_bn=5.0, market="",
         currency="USD", primary=None, isin=None, sub="Steel", usd=...) -> IndexRow:
    cap = None if cap_bn is None else cap_bn * 1e9
    market = market or ticker.rpartition(".")[2]
    return IndexRow(
        ticker=ticker, yahoo_ticker=ticker.rpartition(".")[0], name=name or f"{ticker} Corp",
        exchange=market, market=market, currency=currency, sector=sector, industry=industry,
        gics_industry=industry, gics_subindustry=sub, market_cap=cap,
        market_cap_usd=(cap if usd is ... else usd),
        market_cap_usd_source="computed" if cap is not None else "abstained",
        primary_ticker=ticker if primary is None else primary,
        isin=f"XX{abs(hash(ticker)) % 10**10:010d}" if isin is None else isin,
        fetched_at=SNAPSHOT, source=SOURCE_EODHD_LISTING)


def _defn(**over):
    base = {"name": "Steel Test", "industry": ["Steel"], "exchanges": ["ALL"],
            "min_market_cap_usd": 1e9, "min_history_years": 5, "exclude": ["financials", "reits"]}
    base.update(over)
    return definition_from_mapping(base)


def _many(n, *, industry="Steel", cap_bn=5.0, markets=("US",), start=0):
    return [_row(f"S{start + i:03d}.{markets[i % len(markets)]}", f"Steel Company {start + i}",
                 industry=industry, cap_bn=cap_bn + i * 0.01) for i in range(n)]


def _tickers(members):
    return sorted(m.ticker for m in members)


# =========================================================================== #
# 1. the source is the cleaned pool
# =========================================================================== #
def _dirty_table():
    return [
        _row("GOOD.US", "Good Steel Corp"),
        _row("0XYZ.LSE", "Foreign Steel London Line", market="LSE"),                 # secondary line
        _row("STLB34.SA", "Steel Receipt Inc", market="SA"),                          # a BDR
        _row("FNDX.US", "Steel Index ETF"),                                            # a fund
        _row("BANKY.US", "Steel Bank Holdings"),                                       # label vs name
        _row("NOCAP.US", "No Cap Steel", cap_bn=None),                                # no market cap
        _row("NOUSD.US", "No Usd Steel", usd=None),                                   # no conversion
        _row("HOME.US", "Twin Steel Corp", primary="HOME.US", isin="US0000000001"),
        _row("HOME.XETRA", "Twin Steel Corp", primary="HOME.US", isin="DE0000000001",
             market="XETRA"),                                                          # same company
        _row("ODD.US", "Odd Steel Inc", cap_bn=10.0, primary="ODD.US", isin="US0000000002"),
        _row("ODD.XETRA", "Odd Steel Inc", cap_bn=10.5, primary="ODD.US", isin="US0000000002",
             market="XETRA"),
        _row("ODD.PA", "Odd Steel Inc", cap_bn=0.4, primary="ODD.US", isin="US0000000002",
             market="PA"),                                                             # size suspect
    ]


def test_the_pool_a_cohort_draws_from_is_the_cleaned_pool_not_the_raw_index():
    pool = clean_pool(_dirty_table())
    kept = {r.ticker for r in pool.rows}
    assert kept == {"GOOD.US", "HOME.US", "ODD.US", "ODD.XETRA"} or \
        kept == {"GOOD.US", "HOME.US", "ODD.US"}
    for gone in ("0XYZ.LSE", "STLB34.SA", "FNDX.US", "BANKY.US", "NOCAP.US", "NOUSD.US",
                 "HOME.XETRA", "ODD.PA"):
        assert gone not in kept, gone
    text = " ".join(pool.lines())
    assert "secondary trading line" in text and "fund, not a company" in text
    assert "classification suspect" in text and "size suspect" in text


def test_a_cohort_built_from_the_pool_holds_one_row_per_company_and_none_of_the_excluded_kinds():
    candidates, path, log = build_pool_from_index(_defn(), clean_pool(_dirty_table()))
    assert path == PATH_INDEX
    tickers = _tickers(candidates)
    assert "GOOD.US" in tickers and "HOME.US" in tickers
    assert not any(t in tickers for t in ("HOME.XETRA", "0XYZ.LSE", "FNDX.US", "NOCAP.US"))
    assert all(c.source == PATH_INDEX and c.security_type == "Common Stock" for c in candidates)
    assert any("No request was made" in line for line in log)


def test_the_cohort_pool_is_the_same_pool_the_peer_groups_use():
    """The claim COHORT-3 rests on, as a property: a row the cohort pool drops is never a peer, and
    every peer is in the pool."""
    table = _dirty_table() + [_row(f"PEER{i:02d}.US", f"Peer Steel {i}", cap_bn=5.0 + i * 0.1)
                              for i in range(14)]
    pool = {r.ticker for r in clean_pool(table).rows}
    subject_cap = next(r for r in table if r.ticker == "GOOD.US")
    group = peers("GOOD.US", rows=table, aliases=[], overrides=[])
    assert group.available and subject_cap
    members = {m.ticker for m in group.members}
    assert members <= pool
    dropped = {r.ticker for r in table} - pool
    assert not (members & dropped)


def test_an_identity_alias_collapses_an_orphaned_adr_in_a_cohort_too():
    """GSK.US names itself as primary with a different name: only the alias joins it to GSK.LSE."""
    home = _row("GSK.LSE", "GSK plc", industry="Drug Manufacturers - General", cap_bn=102.5,
                sector="Healthcare", sub="Pharmaceuticals", isin="GB00BN7SWP63")
    adr = _row("GSK.US", "GlaxoSmithKline PLC ADR", industry="Drug Manufacturers - General",
               cap_bn=100.6, sector="Healthcare", sub="Pharmaceuticals", isin="US37733W2044")
    defn = _defn(name="Pharma Test", industry=["Drug Manufacturers - General"])
    without = build_pool_from_index(defn, clean_pool([home, adr], aliases=[]))[0]
    with_alias = build_pool_from_index(defn, clean_pool(
        [home, adr], aliases=[IdentityAlias("GSK.US", "GSK.LSE", "2026-09-25", "an ADR")]))
    assert _tickers(without) == ["GSK.LSE", "GSK.US"]
    assert _tickers(with_alias[0]) == ["GSK.LSE"]
    assert any("identity alias" in line for line in with_alias[2])


def test_korean_preference_lines_never_reach_a_cohort():
    rows = [_row("005930.KO", "Samsung Electronics Co Ltd", market="KO", cap_bn=1376),
            _row("005935.KO", "Samsung Electronics Co Pref", market="KO", cap_bn=1065)]
    got = build_pool_from_index(_defn(), clean_pool(rows))[0]
    assert _tickers(got) == ["005930.KO"]


def test_other_preference_shares_are_removed_by_the_existing_cohort_rule_from_their_name():
    """2002A.TW 'China Steel Corp Pref' stood beside 2002.TW in the live Steel cohort."""
    # 1.5x apart: beyond the pool's name-link tolerance, as on the live index
    rows = [_row("2002.TW", "China Steel Corp", market="TW", cap_bn=18.0),
            _row("2002A.TW", "China Steel Corp Pref", market="TW", cap_bn=12.0,
                 isin="TW0002002A04")]
    candidates = build_pool_from_index(_defn(), clean_pool(rows))[0]
    assert {c.ticker: c.security_type for c in candidates} == {
        "2002.TW": "Common Stock", "2002A.TW": "Preferred"}
    members, removals = clean(candidates, _defn())
    assert _tickers(members) == ["2002.TW"]
    assert "Preferred" in removals[0].reason and removals[0].ticker == "2002A.TW"


# =========================================================================== #
# 2. Sao Paulo, and the exchanges
# =========================================================================== #
def _global_table():
    return [_row("A1.US", "Us Steel"), _row("A2.LSE", "Uk Steel"), _row("A3.HK", "Hk Steel"),
            _row("A4.KO", "Korea Steel"), _row("A5.TW", "Taiwan Steel"),
            _row("A6.AU", "Aus Steel"), _row("A7.TO", "Canada Steel"),
            _row("VALE3.SA", "Brazilian Steel Sa", market="SA", currency="BRL")]


def test_sao_paulo_is_left_out_of_every_cohort_by_decision():
    candidates, _p, log = build_pool_from_index(_defn(), clean_pool(_global_table()))
    assert "VALE3.SA" not in _tickers(candidates)
    assert {c.exchange for c in candidates} == {"US", "LSE", "HK", "KO", "TW", "AU", "TO"}
    assert any("Sao Paulo" in line and "left out" in line for line in log)


def test_all_means_every_index_market_except_sao_paulo_and_a_named_list_is_honoured():
    only_us_uk = _defn(exchanges=["US", "LSE"])
    got = build_pool_from_index(only_us_uk, clean_pool(_global_table()))[0]
    assert {c.exchange for c in got} == {"US", "LSE"}
    korea = _defn(exchanges=["KOREA", "TAIWAN"])
    assert {c.exchange for c in build_pool_from_index(korea, clean_pool(_global_table()))[0]} == {
        "KO", "TW"}


# =========================================================================== #
# 3. the USD floor
# =========================================================================== #
def test_the_floor_is_applied_in_usd_to_the_indexs_converted_cap():
    rows = [_row("BIG.US", "Big Steel", cap_bn=1.0),
            _row("EDGE.US", "Edge Steel", cap_bn=0.999),
            _row("SMALL.US", "Small Steel", cap_bn=0.4)]
    candidates = build_pool_from_index(_defn(), clean_pool(rows))[0]
    members, removals = clean(candidates, _defn())
    assert _tickers(members) == ["BIG.US"]
    assert sorted(r.ticker for r in removals) == ["EDGE.US", "SMALL.US"]
    assert "USD, from the index" in removals[0].reason and "floor of $1bn" in removals[0].reason


def test_a_local_cap_that_is_huge_in_yen_but_small_in_dollars_is_judged_in_dollars():
    """The old own-currency floor would have passed 900bn JPY against a 1bn floor."""
    row = _row("7203.T", "Yen Steel", market="TW", currency="JPY", cap_bn=900.0, usd=6.0e9 * 0.1)
    candidates = build_pool_from_index(_defn(min_market_cap_usd=1e9), clean_pool([row]))[0]
    members, removals = clean(candidates, _defn(min_market_cap_usd=1e9))
    assert members == [] and "USD" in removals[0].reason


def test_the_floor_differs_per_cohort_and_is_read_from_the_definition():
    rows = _many(30, cap_bn=1.5)
    low = clean(build_pool_from_index(_defn(min_market_cap_usd=1e9), clean_pool(rows))[0],
                _defn(min_market_cap_usd=1e9))[0]
    high = clean(build_pool_from_index(_defn(min_market_cap_usd=2e9), clean_pool(rows))[0],
                 _defn(min_market_cap_usd=2e9))[0]
    assert len(low) == 30 and len(high) == 0


@pytest.mark.parametrize("value", [0.99e9, 999_999_999, 10.01e9, 5e10, 1e6])
def test_a_floor_outside_one_to_ten_billion_is_refused(value):
    with pytest.raises(DefinitionError, match=r"\$1bn - \$10bn"):
        _defn(min_market_cap_usd=value)


@pytest.mark.parametrize("value", [1e9, 2e9, 5e9, 10e9])
def test_floors_from_one_to_ten_billion_inclusive_are_accepted(value):
    assert _defn(min_market_cap_usd=value).min_market_cap_usd == value


# =========================================================================== #
# 4. the band: thin and wide are reported, never padded or truncated
# =========================================================================== #
def _planned(n, *, codes=("Steel",), rows=None, **over):
    defn = _defn(industry=list(codes), **over)
    table = rows if rows is not None else _many(n, industry=codes[0])
    (entry,) = plan([defn], clean_pool(table), root=Path("no/such/root"))
    return entry


@pytest.mark.parametrize("n, status", [(19, "thin"), (20, "ok"), (60, "ok"), (61, "wide")])
def test_the_band_is_twenty_to_sixty_inclusive(n, status):
    assert _planned(n).status == status


def test_a_thin_cohort_says_so_honestly_and_is_not_padded():
    entry = _planned(19)
    assert len(entry.members) == 19
    sentence = entry.size.sentence()
    assert sentence.startswith("TOO THIN: 19 names, under 20")
    assert "Need 1 more" in sentence and "Not padded" in sentence
    assert "every index market except Sao Paulo" in sentence and "not the lever" in sentence


def test_a_wide_single_code_cohort_says_there_is_no_narrower_code_and_keeps_every_name():
    entry = _planned(75)
    assert len(entry.members) == 75                        # never truncated
    sentence = entry.size.sentence()
    assert sentence.startswith("TOO WIDE: 75 names, over 60")
    assert "There is no narrower code" in sentence and "'Steel'" in sentence
    assert "By venue: US 75" in sentence and "Not truncated" in sentence


def test_a_wide_multi_code_cohort_suggests_the_narrower_codes_with_their_counts():
    rows = _many(40, industry="Steel") + _many(30, industry="Aluminum", start=100)
    entry = _planned(0, codes=("Steel", "Aluminum"), rows=rows)
    assert len(entry.members) == 70 and entry.status == "wide"
    sentence = entry.size.sentence()
    assert "Narrower code: this cohort spans 2 codes (Steel 40, Aluminum 30)" in sentence


def test_wide_and_thin_use_the_size_verdict_with_the_index_hints_only_on_the_index_path():
    members = [Candidate(ticker=f"T{i}.US", exchange="US", industry="Steel") for i in range(61)]
    legacy = size_verdict(members, _defn())
    indexed = size_verdict(members, _defn(), index_path=True)
    assert "Narrow the industry" in legacy.suggestion                 # COHORT-1 wording untouched
    assert "no narrower code" in indexed.suggestion


# =========================================================================== #
# 5. watch
# =========================================================================== #
def test_watch_defaults_to_false_and_parses_true_and_false():
    assert _defn().watch is False
    assert _defn(watch=True).watch is True
    assert _defn(watch=False).watch is False
    assert _defn(watch=None).watch is False


@pytest.mark.parametrize("bad", ["yes", "true", 1, 0, "no"])
def test_watch_must_be_a_real_boolean(bad):
    with pytest.raises(DefinitionError, match="watch must be true or false"):
        _defn(watch=bad)


def test_only_watched_cohorts_are_what_a_watcher_would_run_and_plan_prints_the_flag():
    defs = [_defn(name="A Steel"), _defn(name="B Steel", watch=True)]
    assert [d.name for d in watched(defs)] == ["B Steel"]
    entries = plan(defs, clean_pool(_many(25)), root=Path("no/such/root"))
    text = format_plan(entries)
    assert "A Steel  [in band]  watch: no" in text and "B Steel  [in band]  watch: yes" in text
    assert "1 flagged watch" in text


def test_the_flag_is_stored_in_the_frozen_definition_snapshot(tmp_path):
    outcome = build(_defn(watch=True), root=tmp_path, index_pool=clean_pool(_many(25)),
                    history_provider=_history(), ranker=_fake_ranker, today=date(2026, 9, 25))
    snap = (outcome.directory / "definition.yaml").read_text(encoding="utf-8")
    assert "watch: true" in snap and "min_market_cap_usd: 1000000000.0" in snap


# =========================================================================== #
# 6. plan: the index only, no network, no writes
# =========================================================================== #
def _forbid_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("plan reached for the network")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(builder, "build_pool", refuse)
    from aristos_council.cohorts import source
    monkeypatch.setattr(source.EODHDSource, "__init__", refuse)
    monkeypatch.setattr(source.EODHDSource, "_get", refuse)
    monkeypatch.setattr(builder, "default_history_provider", refuse)
    monkeypatch.setattr(builder, "default_ranker", refuse)


def test_plan_makes_no_network_call_and_writes_nothing(monkeypatch, tmp_path, capsys):
    _forbid_network(monkeypatch)
    defs = tmp_path / "defs.yaml"
    defs.write_text("cohorts:\n  - name: Steel Test\n    industry: [Steel]\n    exchanges: [ALL]\n"
                    "    min_market_cap_usd: 1000000000\n    min_history_years: 5\n",
                    encoding="utf-8")
    monkeypatch.setattr(builder, "default_index_pool", lambda: clean_pool(_many(25)))
    monkeypatch.setattr(cli, "default_index_pool", lambda: clean_pool(_many(25)))
    before = sorted(p.name for p in tmp_path.rglob("*"))
    assert cli.main(["--definitions", str(defs), "--root", str(tmp_path / "cohorts"), "plan"]) == 0
    out = capsys.readouterr().out
    assert "no network" in out and "Steel Test  [in band]" in out
    assert sorted(p.name for p in tmp_path.rglob("*")) == before        # nothing created


def test_plan_prints_codes_floor_count_band_top_five_and_exchanges():
    rows = _many(25, cap_bn=3.0, markets=("US", "LSE", "HK"))
    text = format_plan(plan([_defn(min_market_cap_usd=2e9)], clean_pool(rows),
                            root=Path("no/such/root")))
    assert "codes:     Steel" in text and "floor:     $2.0bn USD" in text
    assert "members:   25" in text and "band:      25 names, inside the 20" in text
    assert "exchanges: US 9, HK 8, LSE 8" in text
    top = next(line for line in text.splitlines() if "top 5:" in line)
    assert top.count(";") == 4                                     # five names
    assert "$3.2bn" in top                                          # sorted by size, largest first


def test_plan_orders_the_top_five_by_usd_size_and_says_when_a_slug_is_already_frozen(tmp_path):
    (tmp_path / "steel_test" / "v3").mkdir(parents=True)
    rows = _many(25)
    (entry,) = plan([_defn()], clean_pool(rows), root=tmp_path)
    assert [m.ticker for m in entry.top] == ["S024.US", "S023.US", "S022.US", "S021.US", "S020.US"]
    assert entry.frozen_version == 3
    assert "already frozen at v3 and will not be touched" in format_plan([entry])


def test_a_code_no_company_carries_is_an_error_line_not_an_empty_cohort():
    (entry,) = plan([_defn(industry=["Gold"])], clean_pool(_many(25)), root=Path("no/such"))
    assert entry.status == "error" and "no company in the index carries" in entry.error
    assert "[ERROR]" in format_plan([entry])


def test_a_legacy_definition_is_reported_in_the_plan_with_the_way_out():
    legacy = definition_from_mapping({"name": "Old Rule", "industry": ["Steel"],
                                      "exchanges": ["US"], "min_market_cap": 1e9,
                                      "min_history_years": 5})
    (entry,) = plan([legacy], clean_pool(_many(25)), root=Path("no/such"))
    assert entry.status == "error" and "--constituents" in entry.error


# =========================================================================== #
# 7. the two paths, and the flag that keeps the old one
# =========================================================================== #
def _legacy():
    return definition_from_mapping({"name": "Old Rule", "industry": ["Steel"],
                                    "exchanges": ["US"], "min_market_cap": 1e9,
                                    "min_history_years": 5})


def test_the_index_is_the_default_and_the_old_path_is_behind_a_flag():
    assert resolve_path(_defn(), constituents=False) == PATH_INDEX
    with pytest.raises(DefinitionError, match="pass --constituents"):
        resolve_path(_legacy(), constituents=False)
    assert resolve_path(_legacy(), constituents=True) == PATH_CONSTITUENTS
    assert resolve_path(_legacy(), constituents=None) == PATH_CONSTITUENTS   # COHORT-1 callers
    assert resolve_path(_defn(), constituents=None) == PATH_INDEX


def test_the_old_path_needs_its_own_currency_floor():
    with pytest.raises(DefinitionError, match="no own-currency min_market_cap"):
        resolve_path(_defn(), constituents=True)
    both = _defn(min_market_cap=1e9)
    assert resolve_path(both, constituents=True) == PATH_CONSTITUENTS
    assert resolve_path(both, constituents=False) == PATH_INDEX


def test_the_cli_picks_the_definition_file_by_the_flag():
    parser = cli.build_parser()
    index_args = parser.parse_args(["plan"])
    old_args = parser.parse_args(["--constituents", "build", "--all"])
    assert cli._definitions_path(index_args) == str(DEFAULT_INDEX_DEFINITIONS)
    assert cli._definitions_path(old_args) == str(DEFAULT_DEFINITIONS)
    explicit = parser.parse_args(["--definitions", "x.yaml", "plan"])
    assert cli._definitions_path(explicit) == "x.yaml"


def test_plan_refuses_the_constituents_flag_because_it_has_no_dry_run():
    args = cli.build_parser().parse_args(["--constituents", "plan"])
    with pytest.raises(DefinitionError, match="plan reads the market index"):
        cli.cmd_plan(args)


def test_the_index_path_builds_with_no_source_and_no_probe(tmp_path):
    """The whole build, end to end, from a fabricated index: no EODHDSource, no probe."""
    outcome = build(_defn(), root=tmp_path, index_pool=clean_pool(_many(25)),
                    history_provider=_history(), ranker=_fake_ranker, today=date(2026, 9, 25))
    assert outcome.frozen and outcome.version == 1 and outcome.path_used == PATH_INDEX
    assert len(outcome.members) == 25
    report = (outcome.directory / "report.md").read_text(encoding="utf-8")
    assert "$1bn, in USD as converted by the market index" in report
    assert "the local market index" in report


def test_an_already_frozen_cohort_is_never_overwritten_by_a_plain_build(tmp_path):
    first = build(_defn(), root=tmp_path, index_pool=clean_pool(_many(25)),
                  history_provider=_history(), ranker=_fake_ranker, today=date(2026, 9, 25))
    members = first.directory / MEMBERS_FILE
    before = members.read_bytes()
    again = build(_defn(), root=tmp_path, index_pool=clean_pool(_many(30)),
                  history_provider=_history(), ranker=_fake_ranker, today=date(2026, 9, 26))
    assert again.skipped and "already built at v1" in again.skipped
    assert members.read_bytes() == before
    assert sorted(p.name for p in (tmp_path / "steel_test").iterdir()) == ["v1"]


def test_a_thin_index_cohort_is_not_frozen(tmp_path):
    outcome = build(_defn(), root=tmp_path, index_pool=clean_pool(_many(19)),
                    history_provider=_history(), ranker=_fake_ranker)
    assert outcome.size.status == "thin" and outcome.frozen is False
    assert not list(tmp_path.glob("**/members.csv"))


def test_members_csv_carries_the_usd_cap_last_and_an_old_file_still_reads(tmp_path):
    cand = Candidate(ticker="X.US", exchange="US", industry="Steel", name="X", market_cap=5e9,
                     market_cap_usd=5e9, currency="USD", source=PATH_INDEX)
    path = write_members(tmp_path / "members.csv", [cand])
    header = next(csv.reader(path.open(encoding="utf-8")))
    assert header[-1] == "market_cap_usd" and read_members(path)[0].market_cap_usd == 5e9
    old = tmp_path / "old.csv"
    old.write_text("ticker,yahoo_ticker,exchange,industry,market_cap,currency,isin,name,source,"
                   "filled\nX.US,X,US,Steel,5000000000,USD,,X,constituents,\n", encoding="utf-8")
    assert read_members(old)[0].market_cap_usd is None


def test_a_label_with_a_non_breaking_space_still_matches_its_code():
    """Two live labels carry one ('Aerospace &\xa0Defense'); an exact match would miss them."""
    row = _row("AD.US", "Defense Corp", industry="Aerospace &\xa0Defense", sector="Industrials")
    assert candidate_from_index_row(row).industry == "Aerospace & Defense"
    defn = _defn(name="A&D Test", industry=["Aerospace & Defense"])
    assert _tickers(build_pool_from_index(defn, clean_pool([row]))[0]) == ["AD.US"]


# =========================================================================== #
# 8. the shipped list
# =========================================================================== #
SHIPPED = ROOT / "data" / "cohort_definitions.yaml"


def test_the_shipped_list_loads_and_is_about_fifty_cohorts():
    defs = load_definitions(SHIPPED)
    assert 45 <= len(defs) <= 65
    assert len({d.slug for d in defs}) == len(defs)


def test_every_shipped_cohort_is_an_index_cohort_with_a_floor_between_one_and_ten_billion():
    for d in load_definitions(SHIPPED):
        assert d.uses_index and d.all_index_exchanges and d.exchanges == ("ALL",), d.name
        assert 1e9 <= d.min_market_cap_usd <= 10e9, d.name
        assert d.min_market_cap_usd in {1e9, 2e9, 3e9, 5e9, 10e9}, d.name   # the stated tiers
        assert d.exclude == ("financials", "reits"), d.name
        assert d.watch is False, d.name          # which to watch is the owner's call
        assert set(d.industry) <= INDEX_EODHD_INDUSTRIES, d.name


def test_the_shipped_list_covers_every_sector_the_brief_names():
    names = [d.name for d in load_definitions(SHIPPED)]
    for sector in ("Energy", "Utilities", "Materials", "Industrials", "Consumer", "Health",
                   "Tech", "Comms"):
        assert sum(1 for n in names if n.startswith(f"{sector} - ")) >= 3, sector


def test_no_shipped_cohort_collides_with_a_cohort_one_definition_or_a_frozen_version():
    """The ten COHORT-1 slugs are frozen local data that this list must never touch."""
    legacy = {d.slug for d in load_definitions(DEFAULT_DEFINITIONS)}
    new = {d.slug for d in load_definitions(SHIPPED)}
    assert not (legacy & new), sorted(legacy & new)
    frozen = ROOT / "data" / "local" / "cohorts"
    if frozen.exists():
        existing = {p.name for p in frozen.iterdir() if p.is_dir()}
        assert not (existing & new), sorted(existing & new)


def test_the_shipped_header_states_the_floor_rule_and_the_watch_flag_and_what_was_merged():
    text = SHIPPED.read_text(encoding="utf-8")
    for needle in ("n1 <=  40", "NOT a knob for hitting", "watch: false", "MERGED", "Sao Paulo",
                   "no watcher in this repository"):
        assert needle in text, needle
    assert text.count("# n1 = ") == len(load_definitions(SHIPPED))    # each choice documented


def test_the_cohort_one_definition_file_is_untouched_and_still_loads_as_legacy():
    legacy = load_definitions(DEFAULT_DEFINITIONS)
    assert len(legacy) == 10 and all(not d.uses_index and d.min_market_cap > 0 for d in legacy)
