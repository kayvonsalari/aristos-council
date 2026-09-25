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


def test_members_csv_carries_the_usd_cap_and_the_flags_last_and_an_old_file_still_reads(tmp_path):
    cand = Candidate(ticker="X.US", exchange="US", industry="Steel", name="X", market_cap=5e9,
                     market_cap_usd=5e9, currency="USD", source=PATH_INDEX)
    path = write_members(tmp_path / "members.csv", [cand])
    header = next(csv.reader(path.open(encoding="utf-8")))
    assert header[-2:] == ["market_cap_usd", "flags"] and read_members(path)[0].market_cap_usd == 5e9
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


# =========================================================================== #
# 9. narrowing a wide cohort to a GICS sub-industry (never by truncating)
# =========================================================================== #
def _sw(ticker, name, sub, cap_bn=5.0, market=None):
    return _row(ticker, name, industry="Software - Application", sector="Technology", sub=sub,
                cap_bn=cap_bn, market=market or ticker.rpartition(".")[2])


def test_a_sub_industry_narrows_the_cohort_and_each_removal_says_why():
    rows = ([_sw(f"APP{i:02d}.US", f"App Co {i}", "Application Software") for i in range(25)]
            + [_sw("RIDE.US", "Ride Hailing Inc", "Passenger Ground Transportation"),
               _sw("HR.US", "Hr Services Inc", "Human Resource & Employment Services"),
               _sw("NOLABEL.US", "Unlabelled Software Inc", "")])
    wide = _defn(name="Sw Wide", industry=["Software - Application"])
    narrow = _defn(name="Sw Narrow", industry=["Software - Application"],
                   gics_subindustry=["Application Software"])
    pool = clean_pool(rows)
    cands = build_pool_from_index(narrow, pool)[0]
    members, removals = clean(cands, narrow)
    assert len(members) == 25 and all(m.gics_subindustry == "Application Software" for m in members)
    reasons = {r.ticker: r.reason for r in removals}
    assert "'Passenger Ground Transportation' is not Application Software" in reasons["RIDE.US"]
    assert "no GICS sub-industry label" in reasons["NOLABEL.US"]
    assert "not counted as a match or a mismatch" in reasons["NOLABEL.US"]
    assert len(clean(build_pool_from_index(wide, pool)[0], wide)[0]) == 28   # nothing else changed


def test_a_sub_industry_narrowing_can_bring_a_wide_cohort_into_band_without_truncating():
    rows = ([_sw(f"APP{i:02d}.US", f"App Co {i}", "Application Software") for i in range(45)]
            + [_sw(f"OTH{i:02d}.US", f"Other Co {i}", "Systems Software") for i in range(20)])
    (wide,) = plan([_defn(industry=["Software - Application"])], clean_pool(rows),
                   root=Path("no/such"))
    (narrow,) = plan([_defn(industry=["Software - Application"],
                            gics_subindustry=["Application Software"])],
                     clean_pool(rows), root=Path("no/such"))
    assert wide.status == "wide" and len(wide.members) == 65      # never truncated
    assert narrow.status == "ok" and len(narrow.members) == 45


def test_the_wide_hint_names_the_sub_industries_when_the_code_has_several():
    rows = ([_sw(f"APP{i:02d}.US", f"App Co {i}", "Application Software") for i in range(45)]
            + [_sw(f"OTH{i:02d}.US", f"Other Co {i}", "Systems Software") for i in range(20)])
    (entry,) = plan([_defn(industry=["Software - Application"])], clean_pool(rows),
                    root=Path("no/such"))
    sentence = entry.size.sentence()
    assert "Narrower code:" in sentence and "gics_subindustry" in sentence
    assert "Application Software 45" in sentence and "Systems Software 20" in sentence
    assert "There is no narrower code" not in sentence


def test_a_sub_industry_no_company_carries_is_an_error_line_in_the_plan():
    rows = [_sw(f"APP{i:02d}.US", f"App Co {i}", "Application Software") for i in range(25)]
    (entry,) = plan([_defn(industry=["Software - Application"],
                           gics_subindustry=["Made Up Sub-Industry"])], clean_pool(rows),
                    root=Path("no/such"))
    assert entry.status == "error" and "carries the GICS sub-industry Made Up" in entry.error


def test_the_sub_industry_field_is_validated_and_index_only():
    assert _defn(gics_subindustry="Semiconductors").gics_subindustry == ("Semiconductors",)
    assert _defn(gics_subindustry=["A", " B "]).gics_subindustry == ("A", "B")
    assert _defn().gics_subindustry == ()
    with pytest.raises(DefinitionError, match="is empty"):
        _defn(gics_subindustry=[" "])
    legacy = {"name": "Old", "industry": ["Steel"], "exchanges": ["US"], "min_market_cap": 1e9,
              "min_history_years": 5, "gics_subindustry": ["X"]}
    with pytest.raises(DefinitionError, match="needs a min_market_cap_usd"):
        definition_from_mapping(legacy)


def test_a_label_override_reaches_the_sub_industry_a_cohort_narrows_on():
    """Micron's US line is filed under the wrong sub-industry; the override that fixes peers fixes
    the cohort too, because both read the same cleaned pool."""
    from aristos_council.market_index import LabelOverride
    mu = _row("MU.US", "Micron Technology Inc", industry="Semiconductors", sector="Technology",
              sub="Semiconductor Materials & Equipment", cap_bn=1100.0)
    defn = _defn(name="Semi Test", industry=["Semiconductors"], gics_subindustry=["Semiconductors"])
    fix = LabelOverride("MU.US", "Semiconductors", "2026-09-25", "memory chips")
    without = clean(build_pool_from_index(defn, clean_pool([mu], overrides=[]))[0], defn)[0]
    with_it = clean(build_pool_from_index(defn, clean_pool([mu], overrides=[fix]))[0], defn)[0]
    assert without == [] and _tickers(with_it) == ["MU.US"]


def test_the_shipped_narrowed_cohorts_name_their_sub_industry_and_document_why():
    text = SHIPPED.read_text(encoding="utf-8")
    defs = {d.name: d for d in load_definitions(SHIPPED)}
    assert defs["Tech - Semiconductors"].gics_subindustry == ("Semiconductors",)
    assert defs["Tech - Application Software"].gics_subindustry == ("Application Software",)
    assert defs["Industrials - Industrial Machinery"].gics_subindustry == (
        "Industrial Machinery & Supplies & Components",)
    grid = defs["Industrials - Grid & Electrical Machinery"]
    assert set(grid.gics_subindustry) == {"Heavy Electrical Equipment",
                                          "Electrical Components & Equipment"}
    assert grid.industry == defs["Industrials - Industrial Machinery"].industry
    assert text.count("NARROWED to the GICS sub-industry") == 3


# =========================================================================== #
# 10. one seat per company: a depositary receipt never beats an ordinary line
# =========================================================================== #
def _sap_shaped():
    """SAP: the ADR names its OWN home; the Xetra line names a venue the index does not track. They
    tie on home and country, and the old ticker tiebreak (SAP.US < SAP.XETRA) seated the ADR."""
    adr = _row("SAP.US", "SAP SE ADR", industry="Software - Application", sector="Technology",
               sub="Application Software", cap_bn=241.7, primary="SAPA.F", isin="US8030542042")
    ordinary = _row("SAP.XETRA", "SAP SE", industry="Software - Application", sector="Technology",
                    sub="Application Software", cap_bn=242.6, primary="SAP.F",
                    isin="DE0007164600", market="XETRA")
    adr.country, ordinary.country = "US", "DE"
    return adr, ordinary


def test_an_adr_never_takes_a_companys_seat_from_its_ordinary_line():
    adr, ordinary = _sap_shaped()
    kept = clean_pool([adr, ordinary]).rows
    assert [r.ticker for r in kept] == ["SAP.XETRA"]
    assert [r.ticker for r in clean_pool([ordinary, adr]).rows] == ["SAP.XETRA"]   # order-free


def test_an_adr_that_is_the_only_line_still_keeps_its_seat():
    adr, _ordinary = _sap_shaped()
    assert [r.ticker for r in clean_pool([adr]).rows] == ["SAP.US"]


def test_the_receipt_rule_reads_the_name_and_not_a_substring_of_it():
    """'Madrid', 'Adrian' and 'Loads' must not read as ADR/ADS."""
    plain = [_row("A.US", "Madrid Steel Corp"), _row("B.US", "Adrian Metals Inc"),
             _row("C.US", "Loads Steel Ltd")]
    from aristos_council.market_index import _listing_rank
    assert all(_listing_rank(r)[0] == 0 for r in plain)
    assert _listing_rank(_row("D.US", "Some Co American Depositary Shares"))[0] == 1


# =========================================================================== #
# 11. Sao Paulo is excluded BEFORE one row per company, so a Brazilian miner keeps its seat
# =========================================================================== #
def _vale_shaped():
    """A Brazilian miner's ordinary shares are on Sao Paulo; its US line is a self-named ADR."""
    ordinary = _row("MINE3.SA", "Minera Brasileira S.A.", industry="Other Industrial Metals & Mining",
                    market="SA", currency="BRL", primary="MINE3.SA", isin="BRMINEACNOR0",
                    cap_bn=60.5)
    adr = _row("MINE.US", "Minera Brasileira SA ADR", industry="Other Industrial Metals & Mining",
               primary="MINE3.SA", isin="US6000001055", cap_bn=60.5)
    return ordinary, adr


def test_a_company_whose_ordinary_line_is_on_sao_paulo_keeps_its_us_line_in_a_cohort():
    ordinary, adr = _vale_shaped()
    rows = [ordinary, adr]
    # the plain pool (what peers use) seats the ordinary line, on Sao Paulo...
    assert [r.ticker for r in clean_pool(rows).rows] == ["MINE3.SA"]
    # ...and a cohort, which excludes Sao Paulo FIRST, must not lose the company with it
    cohort_pool = clean_pool(rows, exclude_markets=("SA",))
    assert [r.ticker for r in cohort_pool.rows] == ["MINE.US"]
    assert any("excluded market" in line for line in cohort_pool.lines())
    defn = _defn(name="Mining Test", industry=["Other Industrial Metals & Mining"])
    assert _tickers(build_pool_from_index(defn, cohort_pool)[0]) == ["MINE.US"]


def test_the_default_cohort_pool_excludes_sao_paulo_up_front(monkeypatch):
    seen = {}

    def fake_clean_pool(*a, **k):
        seen.update(k)
        return clean_pool([])
    monkeypatch.setattr("aristos_council.market_index.clean_pool", fake_clean_pool)
    monkeypatch.setattr("aristos_council.market_index.IndexStore", lambda root: None)
    builder.default_index_pool()
    assert seen["exclude_markets"] == ("SA",)


# =========================================================================== #
# 12. the local watch overlay: personal, never in the tracked file
# =========================================================================== #
from aristos_council.cohorts.definitions import (apply_watch_overlay,  # noqa: E402
                                                 load_watch_overlay)


def test_the_overlay_switches_named_cohorts_on_and_leaves_the_rest_as_written(tmp_path):
    overlay = tmp_path / "watch.yaml"
    overlay.write_text("watch:\n  - Steel Test\n  - b_steel\n", encoding="utf-8")
    defs = [_defn(name="Steel Test"), _defn(name="B Steel"), _defn(name="C Steel"),
            _defn(name="D Steel", watch=True)]
    out = apply_watch_overlay(defs, load_watch_overlay(overlay))
    assert {d.name: d.watch for d in out} == {"Steel Test": True, "B Steel": True,
                                              "C Steel": False, "D Steel": True}
    assert [d.watch for d in defs] == [False, False, False, True]      # the input is untouched


def test_a_missing_overlay_is_no_overlay_and_a_typo_is_an_error(tmp_path):
    assert load_watch_overlay(tmp_path / "nothing.yaml") == []
    assert [d.watch for d in apply_watch_overlay([_defn()], [])] == [False]
    with pytest.raises(DefinitionError, match="no cohort named"):
        apply_watch_overlay([_defn()], ["Not A Cohort"])
    bad = tmp_path / "bad.yaml"
    bad.write_text("watch: nope\n", encoding="utf-8")
    with pytest.raises(DefinitionError, match="expected a 'watch:' list"):
        load_watch_overlay(bad)


def test_the_cli_applies_the_overlay_from_the_cohort_root_and_can_ignore_it(tmp_path):
    defs = tmp_path / "defs.yaml"
    defs.write_text("cohorts:\n  - name: Steel Test\n    industry: [Steel]\n    exchanges: [ALL]\n"
                    "    min_market_cap_usd: 1000000000\n    min_history_years: 5\n"
                    "  - name: Other Steel\n    industry: [Steel]\n    exchanges: [ALL]\n"
                    "    min_market_cap_usd: 1000000000\n    min_history_years: 5\n",
                    encoding="utf-8")
    root = tmp_path / "cohorts"
    root.mkdir()
    (root / "watch.yaml").write_text("watch:\n  - Steel Test\n", encoding="utf-8")
    parser = cli.build_parser()
    with_it = parser.parse_args(["--definitions", str(defs), "--root", str(root), "plan"])
    without = parser.parse_args(["--definitions", str(defs), "--root", str(root),
                                 "--no-local-overlay", "plan"])
    assert {d.name: d.watch for d in cli._load(with_it)} == {"Steel Test": True,
                                                             "Other Steel": False}
    assert {d.name: d.watch for d in cli._load(without)} == {"Steel Test": False,
                                                             "Other Steel": False}


def test_the_overlay_file_is_git_ignored_and_the_tracked_list_watches_nothing():
    import subprocess
    ignored = subprocess.run(["git", "check-ignore", "-q", "data/local/cohorts/watch.yaml"],
                             cwd=ROOT, capture_output=True)
    assert ignored.returncode == 0                      # git says: ignored
    assert all(d.watch is False for d in load_definitions(SHIPPED))
    assert not any(d.watch for d in load_definitions(DEFAULT_DEFINITIONS))


def test_the_tracked_files_name_no_company_the_owner_watches():
    """The overlay lists cohort names only; and neither it nor anything tracked in the cohort
    definitions carries a personal watch list. The shipped file is a rule list, not a portfolio."""
    text = SHIPPED.read_text(encoding="utf-8").lower()
    assert "watchlist" not in text and "portfolio" not in text
    assert "anchors: []" in text


# =========================================================================== #
# 13. two duplicates the by-eye check found in the live plan
# =========================================================================== #
def test_a_company_stamped_common_stock_on_one_venue_is_the_same_company_as_its_plain_name():
    """Life360 stood twice in Application Software: 'LIFE360 Inc' (AU) and 'Life360, Inc. Common
    Stock' (US), a few per cent apart in size."""
    from aristos_council.market_index import _name_key
    assert _name_key("Life360, Inc. Common Stock") == _name_key("LIFE360 Inc") == "life360"
    au = _sw("360.AU", "LIFE360 Inc", "Application Software", cap_bn=3.36)
    us = _sw("LIF.US", "Life360, Inc. Common Stock", "Application Software", cap_bn=3.23)
    assert [r.ticker for r in clean_pool([au, us]).rows] in (["360.AU"], ["LIF.US"])
    assert len(clean_pool([au, us]).rows) == 1


def test_a_hong_kong_rmb_counter_is_a_secondary_line_of_its_hkd_counter():
    """A carmaker stood twice in Auto Manufacturers: 1211.HK ($93bn) and 81211.HK ($130bn), 1.39x apart in
    the index - beyond the name-link tolerance. HKEX reserves 80000-89999 for RMB counters."""
    from aristos_council.market_index import RECEIPT_HK_RMB, receipt_kind
    hkd = _row("1211.HK", "Sunrise Motor Company Limited", industry="Auto Manufacturers", cap_bn=93.4)
    rmb = _row("81211.HK", "Sunrise Motor Company Limited", industry="Auto Manufacturers", cap_bn=130.1)
    assert receipt_kind(rmb) == RECEIPT_HK_RMB and receipt_kind(hkd) == ""
    assert [r.ticker for r in clean_pool([hkd, rmb]).rows] == ["1211.HK"]
    assert "Hong Kong RMB counter" in " ".join(clean_pool([hkd, rmb]).lines())


def test_a_hong_kong_gem_code_is_not_an_rmb_counter():
    """GEM (growth board) codes are four-digit 8xxx: 8442.HK is a company."""
    from aristos_council.market_index import receipt_kind
    for code in ("8442.HK", "8001.HK", "0008.HK", "80.HK"):
        assert receipt_kind(_row(code, "Some Co", market="HK")) == "", code
    for code in ("80016.HK", "89988.HK", "86618.HK"):
        assert receipt_kind(_row(code, "Some Co", market="HK")) == "Hong Kong RMB counter", code


def test_the_rmb_counter_rule_gives_a_company_its_hkd_seat_not_the_ticker_tiebreak_winner():
    """Alibaba was seated on 89988.HK because '8' sorts before '9'."""
    hkd = _row("9988.HK", "Alibaba Group Holding Limited", cap_bn=282.0, industry="Internet Retail")
    rmb = _row("89988.HK", "Alibaba Group Holding Limited", cap_bn=305.0, industry="Internet Retail")
    assert [r.ticker for r in clean_pool([rmb, hkd]).rows] == ["9988.HK"]


# =========================================================================== #
# 14. more duplicates the by-eye check found (name wording, and the alias file)
# =========================================================================== #
@pytest.mark.parametrize("name, key", [
    ("ZSCALER INC. DL-,001", "zscaler"),                 # German dollar-par quote wording
    ("ALMONTY INDUSTRY O.N.", "almonty industry"),       # ohne Nennwert
    ("Under Armour Inc C", "under armour"),              # a trailing share-class letter
    ("Under Armour Inc A", "under armour"),
    ("Stora Enso Oyj ser. R", "stora enso"),             # Nordic series
    ("Stora Enso Oyj A", "stora enso"),
    ("Schindler Ps", "schindler"),                       # Swiss participation certificate
    ("Zillow Group Inc Class C", "zillow"),
    ("BP p.l.c", "bp"),
])
def test_wording_that_says_how_a_line_is_quoted_is_not_part_of_a_company_name(name, key):
    from aristos_council.market_index import _name_key
    assert _name_key(name) == key


@pytest.mark.parametrize("a, b", [
    ("Kodi-S Co Ltd", "Kodi-M Co Ltd"),
    ("Cantor Equity Partners I, Inc.", "Cantor Equity Partners V, Inc."),
    ("Alpha Holdings S", "Alpha Holdings M"),
])
def test_a_trailing_letter_that_is_not_a_share_class_still_tells_two_companies_apart(a, b):
    """A rule that is sometimes wrong is not a rule: Kodi-S and Kodi-M, two SPACs numbered I and V."""
    from aristos_council.market_index import _name_key
    assert _name_key(a) != _name_key(b)
    x = _row("X1.US", a, cap_bn=0.02)
    y = _row("Y1.US", b, cap_bn=0.02)
    assert len(clean_pool([x, y]).rows) == 2


def test_a_two_letter_company_name_is_a_name_once_the_size_guard_is_there():
    """'BP' was excluded by a three-character floor: BP.LSE and BP.US stood as two companies."""
    lse = _row("BP.LSE", "BP PLC", industry="Oil & Gas Integrated", cap_bn=115.6)
    adr = _row("BP.US", "BP PLC ADR", industry="Oil & Gas Integrated", cap_bn=114.8)
    assert [r.ticker for r in clean_pool([lse, adr]).rows] == ["BP.LSE"]
    other = _row("XX.US", "BP Prudhoe Bay Royalty Trust", industry="Oil & Gas Integrated", cap_bn=0.1)
    assert len(clean_pool([lse, other]).rows) == 2       # a longer name is not "bp"


def test_two_share_lines_of_one_company_on_two_exchanges_are_one_company():
    """Stora Enso: A and R shares on Stockholm and on Helsinki."""
    rows = [_row("STE-A.ST", "Stora Enso Oyj ser. A", cap_bn=9.88, market="ST"),
            _row("STE-R.ST", "Stora Enso Oyj ser. R", cap_bn=10.07, market="ST"),
            _row("STEAV.HE", "Stora Enso Oyj A", cap_bn=9.90, market="HE"),
            _row("STERV.HE", "Stora Enso Oyj R", cap_bn=9.87, market="HE")]
    assert len(clean_pool(rows).rows) == 1


def test_preference_lines_stamped_with_the_whole_companys_cap_take_the_seat_without_an_alias():
    """BP-A / BP-B.LSE: the ordinary line's cap x1.27 and x1.23, and '-' sorts before '.'. They
    join the group transitively (146.8 is 1.04x from 141.7, which is 1.23x from 115.6) and win it."""
    ordinary = _row("BP.LSE", "BP PLC", cap_bn=115.6, isin="GB0007980591")
    pref_a = _row("BP-A.LSE", "BP p.l.c", cap_bn=146.8, isin="GB0001385250")
    pref_b = _row("BP-B.LSE", "BP p.l.c", cap_bn=141.7, primary="", isin="")
    table = [pref_a, pref_b, ordinary]
    assert [r.ticker for r in clean_pool(table, aliases=[]).rows] == ["BP-A.LSE"]
    fixes = [IdentityAlias("BP-A.LSE", "BP.LSE", "2026-09-25", "a preference line"),
             IdentityAlias("BP-B.LSE", "BP.LSE", "2026-09-25", "a second preference line")]
    assert [r.ticker for r in clean_pool(table, aliases=fixes).rows] == ["BP.LSE"]


def test_the_shipped_alias_file_carries_the_cohort_findings_each_with_its_evidence():
    from aristos_council.market_index import load_identity_aliases
    shipped = {a.ticker: a for a in load_identity_aliases()}
    expected = {"BP-A.LSE": "BP.LSE", "BP-B.LSE": "BP.LSE", "SNN.US": "SN.LSE",
                "SRT3.XETRA": "SRT.XETRA", "TAP-A.US": "TAP.US", "TPX-B.TO": "TAP.US",
                "QSP-UN.TO": "QSR.TO", "CSC.AU": "CS.TO", "CKI.LSE": "1038.HK",
                "ALI1.XETRA": "AII.AU", "GSK.US": "GSK.LSE", "SKHY.US": "000660.KO"}
    assert {t: shipped[t].primary for t in expected} == expected
    assert all(len(a.reason) > 40 and a.date == "2026-09-25" for a in shipped.values())


def test_integrated_oil_and_refining_is_one_cohort_because_integrated_alone_came_out_thin():
    defs = {d.name: d for d in load_definitions(SHIPPED)}
    merged = defs["Energy - Integrated Oil & Refining"]
    assert set(merged.industry) == {"Oil & Gas Integrated", "Oil & Gas Refining & Marketing"}
    assert "Energy - Integrated Oil & Gas" not in defs and "Energy - Refining & Marketing" not in defs
    assert "TOO THIN alone" in SHIPPED.read_text(encoding="utf-8")
