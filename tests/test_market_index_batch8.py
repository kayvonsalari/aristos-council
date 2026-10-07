"""BATCH 8 (2026-09-26) - data corrections, Sao Paulo as one setting, possible foreign lines.

Measured on the live index (24,999 rows):

    SHEL.LSE filed by EODHD's GICS copy as Oil & Gas Exploration & Production, so ConocoPhillips,
        EOG, CNQ, CNOOC and PETR3 stood in Shell's peer group.
    LISN.SW (registered shares) read USD 118bn and LISP.SW (participation certificates) USD 24bn:
        two ISINs, each its own primary, 5x apart - both in the Packaged Foods & Confectionery cohort.
    LTOD.LSE (Larsen & Toubro's London GDR, filed as Diversified Metals & Mining) sat in BHP's peers.
    ROG.TO (Roche's Toronto line, no "CDR" in its name) read USD 184bn against Roche's USD 361bn.
    24 identity-less Toronto rows share a name with a row that has an identity (Coeur, Teck A ...).

Every table here is fabricated except the shipped correction FILES, which are read as data.
"""
from __future__ import annotations

import pytest

from aristos_council import market_index as mi
from aristos_council.market_index import (SOURCE_EODHD_LISTING, IndexRow, IndexStore, load_config,
                                          load_identity_aliases, load_label_overrides,
                                          load_size_corrections, peers, possible_foreign_lines,
                                          status)

SNAPSHOT = "2026-09-26"


def _row(ticker, name="", *, cap_bn=100.0, market="US", sub="Semiconductors",
         eodhd="Semiconductors", primary=None, isin=None, currency="USD") -> IndexRow:
    cap = None if cap_bn is None else cap_bn * 1e9
    return IndexRow(
        ticker=ticker, yahoo_ticker=ticker.rpartition(".")[0] or ticker, name=name or ticker,
        exchange=market, market=market, currency=currency, sector="Technology", industry=eodhd,
        gics_industry="Semiconductors & Semiconductor Equipment", gics_subindustry=sub,
        market_cap=cap, market_cap_usd=cap,
        market_cap_usd_source="computed" if cap is not None else "abstained",
        primary_ticker=ticker if primary is None else primary,
        isin=f"XX{abs(hash(ticker)) % 10**10:010d}" if isin is None else isin,
        fetched_at=SNAPSHOT, source=SOURCE_EODHD_LISTING)


def _table(n_us=13, n_sa=3):
    subject = _row("SUBJ.US", "Subject Corp", cap_bn=100.0)
    us = [_row(f"US{i:02d}.US", f"Rival {i}", cap_bn=90.0 + i) for i in range(n_us)]
    sa = [_row(f"BR{i:02d}3.SA", f"Brasil {i}", cap_bn=100.0 + i, market="SA") for i in range(n_sa)]
    return [subject] + us + sa


# =========================================================================== #
# the shipped correction files carry the batch-8 entries, each dated and reasoned
# =========================================================================== #
def test_shell_is_corrected_to_integrated_oil_and_gas_with_a_dated_public_source():
    shipped = {o.ticker: o for o in load_label_overrides()}
    shell = shipped["SHEL.LSE"]
    assert shell.gics_subindustry == "Integrated Oil & Gas" and shell.date == "2026-09-26"
    assert "msci.com" in shell.reason and "Integrated Oil & Gas" in shell.reason


def test_lindt_participation_certificate_is_aliased_and_its_size_is_stated_not_guessed():
    aliases = {a.ticker: a for a in load_identity_aliases()}
    assert aliases["LISP.SW"].primary == "LISN.SW" and aliases["LISP.SW"].date == "2026-09-26"
    corrections = {c.ticker: c for c in load_size_corrections()}
    lisn = corrections["LISN.SW"]
    assert lisn.action == "set" and lisn.date == "2026-09-26"
    # CHF 20.16bn at the index's own CHF rate (about 1.218) - the cited figure, not 118bn or 24.0bn
    assert 24.0e9 < lisn.market_cap_usd < 25.0e9
    assert "stockanalysis.com" in lisn.reason


def test_larsen_and_toubro_london_line_is_excluded_not_given_a_figure():
    corrections = {c.ticker: c for c in load_size_corrections()}
    ltod = corrections["LTOD.LSE"]
    assert ltod.action == "exclude" and ltod.date == "2026-09-26" and ltod.reason
    assert ltod.market_cap_usd is None


def test_roches_toronto_line_is_aliased_to_its_home_line():
    aliases = {a.ticker: a for a in load_identity_aliases()}
    assert aliases["ROG.TO"].primary == "RO.SW" and aliases["ROG.TO"].date == "2026-09-26"


def test_an_aliased_roche_toronto_line_is_one_company_with_roche():
    """Before: ROG.TO ($184bn) stood beside RO.SW ($361bn) as a second Roche."""
    home = _row("RO.SW", "Roche Holding AG", cap_bn=361.0, market="SW", sub="Pharmaceuticals",
                eodhd="Drug Manufacturers - General")
    toronto = _row("ROG.TO", "Roche Holding AG", cap_bn=184.0, market="TO", sub="Pharmaceuticals",
                   eodhd="Drug Manufacturers - General", primary="", isin="")
    subject = _row("AZN.LSE", "AstraZeneca PLC", cap_bn=260.0, market="LSE", sub="Pharmaceuticals",
                   eodhd="Drug Manufacturers - General")
    rivals = [_row(f"PH{i:02d}.US", f"Pharma {i}", cap_bn=200.0 + i, sub="Pharmaceuticals",
                   eodhd="Drug Manufacturers - General") for i in range(12)]
    rows = [subject, home, toronto] + rivals
    aliases = [a for a in load_identity_aliases() if a.ticker == "ROG.TO"]
    group = peers("AZN.LSE", rows=rows, aliases=aliases, overrides=[], size_corrections=[])
    names = [m.name for m in group.members]
    assert names.count("Roche Holding AG") == 1
    assert "ROG.TO" not in [m.ticker for m in group.members]


# =========================================================================== #
# Sao Paulo: ONE setting, read by peers and by cohorts
# =========================================================================== #
def test_the_shipped_config_excludes_sao_paulo_through_one_setting():
    assert load_config()["peer_exclude_markets"] == ["SA"]
    assert mi.excluded_markets() == ("SA",)


def test_deleting_the_line_allows_a_market_back(tmp_path):
    (tmp_path / "market_index.yaml").write_text("exchanges: [US]\n", encoding="utf-8")
    assert mi.excluded_markets(load_config(tmp_path / "market_index.yaml")) == ()
    (tmp_path / "with.yaml").write_text("peer_exclude_markets: [sa, ' hk ']\n", encoding="utf-8")
    assert mi.excluded_markets(load_config(tmp_path / "with.yaml")) == ("SA", "HK")


def test_a_malformed_setting_is_an_error_not_a_silent_no_op(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("peer_exclude_markets: {SA: true}\n", encoding="utf-8")
    with pytest.raises(mi.MarketIndexError):
        load_config(bad)


def test_peers_drops_excluded_markets_and_says_so_in_its_reasons():
    group = peers("SUBJ.US", rows=_table(), overrides=[], aliases=[], size_corrections=[],
                  exclude_markets=("SA",))
    assert group.available
    assert not any(m.market == "SA" for m in group.members)
    assert "3 candidates skipped: market excluded by setting (SA)" in group.reasons


def test_peers_without_the_setting_keeps_the_market():
    group = peers("SUBJ.US", rows=_table(), overrides=[], aliases=[], size_corrections=[],
                  exclude_markets=())
    assert any(m.market == "SA" for m in group.members)
    assert not any("market excluded by setting" in r for r in group.reasons)


def test_a_fabricated_table_never_meets_the_shipped_setting_by_accident():
    """Same convention as the correction files: read with the REAL index only."""
    group = peers("SUBJ.US", rows=_table(), overrides=[], aliases=[], size_corrections=[])
    assert any(m.market == "SA" for m in group.members)


def test_peers_reads_the_shipped_setting_with_the_real_index(monkeypatch):
    store_rows = _table()

    class _Store:
        def load(self):
            return list(store_rows)

    group = peers("SUBJ.US", store=_Store(), overrides=[], aliases=[], size_corrections=[])
    assert not any(m.market == "SA" for m in group.members)


def test_cohorts_read_the_same_setting_so_the_two_can_never_disagree(monkeypatch):
    from aristos_council.cohorts.definitions import index_excluded_markets

    assert index_excluded_markets() == mi.excluded_markets() == ("SA",)
    monkeypatch.setattr(mi, "load_config", lambda *a, **k: {"peer_exclude_markets": ["SA", "HK"]})
    assert index_excluded_markets() == ("SA", "HK") == mi.excluded_markets()
    monkeypatch.setattr(mi, "load_config", lambda *a, **k: {})
    assert index_excluded_markets() == () == mi.excluded_markets()


# =========================================================================== #
# status: possible foreign lines (report only)
# =========================================================================== #
def _toronto_rows():
    return [
        _row("CDE.US", "Coeur Mining, Inc", market="US", cap_bn=8.0),
        _row("CDE.TO", "Coeur Mining, Inc", market="TO", cap_bn=8.0, primary="", isin=""),
        _row("ONLY.TO", "Only Canada Corp", market="TO", cap_bn=3.0, primary="", isin=""),
        _row("HOME.TO", "Home Corp", market="TO", cap_bn=3.0),                    # has an identity
        _row("TECK-B.TO", "Teck Resources Limited Class B", market="TO", cap_bn=30.0),
        _row("TECK-A.TO", "Teck Resources Limited Class A", market="TO", cap_bn=30.0,
             primary="", isin=""),
        _row("TECK.US", "Teck Resources Ltd", market="US", cap_bn=30.0),
        _row("SAME.TO", "Twin Corp", market="TO", cap_bn=2.0),
        _row("TWIN.TO", "Twin Corp Class A", market="TO", cap_bn=2.0, primary="", isin=""),
    ]


def test_possible_foreign_lines_are_identity_less_toronto_rows_named_like_a_row_elsewhere():
    found = [r.ticker for r in possible_foreign_lines(_toronto_rows())]
    assert found == ["CDE.TO", "TECK-A.TO"]        # not ONLY.TO, not a twin on Toronto itself


def test_a_line_an_alias_already_resolves_is_not_reported():
    from aristos_council.market_index import IdentityAlias, apply_identity_aliases
    rows, _ = apply_identity_aliases(
        _toronto_rows(), [IdentityAlias("CDE.TO", "CDE.US", "2026-09-26", "same company")])
    assert [r.ticker for r in possible_foreign_lines(rows)] == ["TECK-A.TO"]


def test_status_lists_them_and_excludes_nothing(tmp_path):
    store = IndexStore(tmp_path)
    store.save(_toronto_rows())
    out = status(store, aliases=[])
    assert out.possible_foreign == ["CDE.TO", "TECK-A.TO"]
    text = " ".join(out.lines())
    assert "2 Toronto rows with no identity" in text and "report only" in text
    assert "CDE.TO" in text
    assert out.rows == len(_toronto_rows())          # the table is untouched


def test_shanghai_conant_optical_is_filed_as_supplies_not_equipment_with_a_dated_reason():
    """C10 (2026-10-06): it carried AtriCure's labels exactly, so no rule could tell it from device peers."""
    conant = {o.ticker: o for o in load_label_overrides()}["2276.HK"]
    assert conant.gics_subindustry == "Health Care Supplies" and conant.date == "2026-10-06"
    assert "spectacle lenses" in conant.reason
