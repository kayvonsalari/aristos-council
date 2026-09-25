"""INDEX-GBX-SCALE-1 - London market caps were about 100x too low.

EODHD codes most London lines "GBX" (pence) - right for the PRICE - but the market cap from
/fundamentals is in POUNDS. The converter scaled it by 0.01 anyway, so every GBX row read a hundred
times too small: RR.LSE $1.6bn against $157.9bn for Rolls-Royce on Xetra. Measured on the built
table: 48 of the 49 comparable GBX rows sat at exactly -2.0 dex; the GBP-coded rows were right.

No test here reaches a real adapter: the FX helper is fed by a fake that records what it was asked.
"""
from __future__ import annotations

import shutil
from datetime import date
from types import SimpleNamespace

from aristos_council.market_index import (SOURCE_EODHD_LISTING, USD_ABSTAINED, USD_COMPUTED,
                                          USD_COMPUTED_MAJOR_UNIT, IndexRow, IndexStore,
                                          MarketIndexError, _UsdConverter, build_log_path,
                                          build_seems_active, fix_gbx_scale, peers,
                                          rederive_minor_unit_usd, size_suspects)

GBPUSD, EURUSD = 1.3389, 1.1476


class _FxAdapter:
    """Answers the pair tickers the converter asks for, and records every one."""

    def __init__(self, **rates):
        self.rates, self.asked = rates, []

    def get_price_history(self, pair, start, end):
        self.asked.append(pair)
        rate = self.rates.get(pair.removesuffix("=X"))
        return SimpleNamespace(closes=[] if rate is None else [rate])


def _converter():
    adapter = _FxAdapter(GBPUSD=GBPUSD, EURUSD=EURUSD, SEKUSD=0.1)
    return _UsdConverter(adapter, today=date(2026, 9, 25)), adapter


def _row(ticker, currency, cap, *, name="", usd=None, source=USD_COMPUTED, primary=None,
         isin="", market=""):
    return IndexRow(ticker=ticker, name=name or ticker, currency=currency, market=market,
                    market_cap=cap, market_cap_usd=usd, market_cap_usd_source=source,
                    primary_ticker=ticker if primary is None else primary, isin=isin,
                    fetched_at="2026-09-25", source=SOURCE_EODHD_LISTING)


# =========================================================================== #
# the converter
# =========================================================================== #
def test_a_gbx_row_converts_its_market_cap_as_pounds_not_pence():
    """RR.LSE: 119.4bn as served. It is GBP 119.4bn, so about $160bn - not $1.6bn."""
    conv, adapter = _converter()
    row = conv.apply(_row("RR.LSE", "GBX", 119_400_947_712.0))
    assert abs(row.market_cap_usd - 119_400_947_712.0 * GBPUSD) < 1.0
    assert row.market_cap_usd > 150e9
    assert row.market_cap_usd_source == USD_COMPUTED_MAJOR_UNIT
    assert adapter.asked == ["GBPUSD=X"]              # the pound's rate; never a "GBX" pair


def test_a_gbp_quoted_row_converts_as_it_always_did_and_agrees_with_a_gbx_row():
    """BP.LSE is coded GBP and was always right. The same pounds figure under either code must
    give the same dollars."""
    conv, _ = _converter()
    gbp = conv.apply(_row("BP.LSE", "GBP", 80e9))
    gbx = conv.apply(_row("XXX.LSE", "GBX", 80e9))
    assert gbp.market_cap_usd == gbx.market_cap_usd == 80e9 * GBPUSD
    assert gbp.market_cap_usd_source == USD_COMPUTED           # not a minor-unit code


def test_other_currencies_are_unchanged():
    conv, _ = _converter()
    assert conv.apply(_row("SAP.XETRA", "EUR", 100e9)).market_cap_usd == 100e9 * EURUSD
    assert conv.apply(_row("AAPL.US", "USD", 1e12)).market_cap_usd == 1e12


def test_rolls_royces_london_and_xetra_lines_agree_within_25_percent_in_usd():
    """The pair that exposed the defect. Stored as served: RR.LSE GBX 119.4bn, RRU.XETRA EUR 137.6bn."""
    conv, _ = _converter()
    london = conv.apply(_row("RR.LSE", "GBX", 119_400_947_712.0, name="Rolls-Royce Holdings PLC"))
    xetra = conv.apply(_row("RRU.XETRA", "EUR", 137_582_837_760.0,
                            name="Rolls-Royce Holdings PLC", primary="RR.LSE"))
    ratio = london.market_cap_usd / xetra.market_cap_usd
    assert 0.8 <= ratio <= 1.25, ratio


def test_a_minor_unit_currency_nobody_has_measured_is_not_guessed():
    """ZAc (South African cents) is not in the index and not in the table of measured claims, so
    it is asked for under its own code and, finding no rate, abstains rather than being scaled."""
    conv, adapter = _converter()
    row = conv.apply(_row("XXX.JSE", "ZAc", 50e9))
    assert row.market_cap_usd is None and row.market_cap_usd_source == USD_ABSTAINED
    assert adapter.asked == ["ZACUSD=X"]


# =========================================================================== #
# the in-place repair of rows built under the old rule
# =========================================================================== #
def _old_gbx(ticker="RR.LSE", cap=119_400_947_712.0):
    """As the old converter stored it: cap x 0.01 x GBPUSD, tagged 'computed'."""
    return _row(ticker, "GBX", cap, usd=cap * 0.01 * GBPUSD)


def test_the_repair_multiplies_the_stored_figure_by_the_pence_factor_it_wrongly_applied():
    (fixed,), report = rederive_minor_unit_usd([_old_gbx()])
    assert abs(fixed.market_cap_usd - 119_400_947_712.0 * GBPUSD) < 1.0
    assert fixed.market_cap_usd_source == USD_COMPUTED_MAJOR_UNIT
    assert report.repaired == 1 and report.skipped == 0


def test_the_repair_is_idempotent():
    once, _ = rederive_minor_unit_usd([_old_gbx()])
    twice, report = rederive_minor_unit_usd(once)
    assert twice[0].market_cap_usd == once[0].market_cap_usd
    assert report.repaired == 0 and report.already_ok == 1


def test_the_repair_touches_only_gbx_rows_and_never_mutates_its_input():
    old = _old_gbx()
    gbp = _row("BP.LSE", "GBP", 80e9, usd=80e9 * GBPUSD)
    eur = _row("SAP.XETRA", "EUR", 100e9, usd=100e9 * EURUSD)
    before = old.market_cap_usd
    rows, report = rederive_minor_unit_usd([old, gbp, eur])
    assert old.market_cap_usd == before                                  # input untouched
    assert rows[1] is gbp and rows[2] is eur and report.repaired == 1


def test_a_gbx_row_whose_stored_rate_does_not_look_like_pence_is_skipped_and_counted():
    """Not every GBX row can be trusted to have been scaled the old way: a row whose implied rate
    is 1.3 (already in dollars) must not be multiplied by 100 on faith."""
    odd = _row("ODD.LSE", "GBX", 10e9, usd=10e9 * GBPUSD)
    rows, report = rederive_minor_unit_usd([odd])
    assert rows[0].market_cap_usd == 10e9 * GBPUSD
    assert report.skipped == 1 and report.skipped_examples == ["ODD.LSE"]


def test_a_gbx_row_with_no_cap_is_left_alone_and_not_counted_as_skipped():
    rows, report = rederive_minor_unit_usd([_row("NIL.LSE", "GBX", None)])
    assert rows[0].market_cap_usd is None and report.skipped == 0 and report.repaired == 0


def _store_with(tmp_path, rows):
    store = IndexStore(tmp_path)
    store.save(rows)
    return store


def test_fix_gbx_scale_backs_the_index_up_first_and_rewrites_it_in_place(tmp_path):
    store = _store_with(tmp_path, [_old_gbx(), _row("SAP.XETRA", "EUR", 100e9, usd=100e9 * EURUSD)])
    original = store.path.read_bytes()
    report = fix_gbx_scale(store, today=date(2026, 9, 25))

    backup = tmp_path / "index.parquet.pre-gbx-scale-20260925"
    assert report.backup == str(backup) and backup.read_bytes() == original
    rr = next(r for r in store.load() if r.ticker == "RR.LSE")
    assert rr.market_cap_usd > 150e9 and rr.market_cap_usd_source == USD_COMPUTED_MAJOR_UNIT
    assert next(r for r in store.load() if r.ticker == "SAP.XETRA").market_cap_usd == 100e9 * EURUSD


def test_a_second_run_changes_nothing_and_makes_no_second_backup(tmp_path):
    store = _store_with(tmp_path, [_old_gbx()])
    fix_gbx_scale(store, today=date(2026, 9, 25))
    after_first = store.path.read_bytes()
    again = fix_gbx_scale(store, today=date(2026, 9, 25))
    assert again.repaired == 0 and again.backup == ""
    assert store.path.read_bytes() == after_first
    assert [p.name for p in tmp_path.iterdir() if "pre-gbx" in p.name] == [
        "index.parquet.pre-gbx-scale-20260925"]


def test_an_existing_backup_is_never_overwritten(tmp_path):
    store = _store_with(tmp_path, [_old_gbx()])
    earlier = tmp_path / "index.parquet.pre-gbx-scale-20260925"
    shutil.copy2(store.path, earlier)
    earlier_bytes = earlier.read_bytes()
    report = fix_gbx_scale(store, today=date(2026, 9, 25))
    assert report.backup.endswith("pre-gbx-scale-20260925-2")
    assert earlier.read_bytes() == earlier_bytes


def test_a_dry_run_writes_nothing_at_all(tmp_path):
    store = _store_with(tmp_path, [_old_gbx()])
    before = store.path.read_bytes()
    report = fix_gbx_scale(store, today=date(2026, 9, 25), dry_run=True)
    assert report.repaired == 1 and report.dry_run and report.backup == ""
    assert store.path.read_bytes() == before
    assert not [p for p in tmp_path.iterdir() if "pre-gbx" in p.name]


# =========================================================================== #
# what the repair was for
# =========================================================================== #
def test_a_uk_company_is_no_longer_flagged_size_suspect_and_sits_in_its_real_size_band():
    """Two lines, home line first. Before the fix the home (GBX) line read 1/100th and, with the
    two-line rule on, the CORRECT Xetra line was the one flagged."""
    conv, _ = _converter()
    home = conv.apply(_row("RR.LSE", "GBX", 119_400_947_712.0, name="Rolls-Royce Holdings PLC",
                           isin="GB00B63H8491"))
    xetra = conv.apply(_row("RRU.XETRA", "EUR", 137_582_837_760.0, name="Rolls-Royce Holdings PLC",
                            primary="RR.LSE", isin="GB00B63H8491", market="XETRA"))
    assert size_suspects([home, xetra]) == {}


def test_a_uk_subject_is_banded_on_its_real_size(tmp_path):
    """A $158bn subject finds $150bn peers; at $1.6bn it would have found none of them."""
    conv, _ = _converter()
    subject = conv.apply(_row("RR.LSE", "GBX", 119_400_947_712.0))
    for row in (subject,):
        row.gics_subindustry = row.industry = "Aerospace & Defense"
    rivals = []
    for i in range(12):
        r = _row(f"RIV{i:02d}.US", "USD", 150e9 + i * 1e9)
        r.market_cap_usd, r.market_cap_usd_source = r.market_cap, USD_COMPUTED
        r.gics_subindustry = r.industry = "Aerospace & Defense"
        rivals.append(r)
    group = peers("RR.LSE", rows=[subject, *rivals], overrides=[])
    assert group.available and group.step == 1


# =========================================================================== #
# a running build owns the file
# =========================================================================== #
def test_the_repair_refuses_to_write_while_a_build_seems_to_be_running(tmp_path):
    """Measured 2026-09-25: a repair made between two flushes of a Korea build was overwritten two
    minutes later by the build's next whole-table flush."""
    store = _store_with(tmp_path, [_old_gbx()])
    build_log_path(store).write_text("2026-09-25T09:28:18+00:00 KO: 596/941\n", encoding="utf-8")
    before = store.path.read_bytes()
    try:
        fix_gbx_scale(store, today=date(2026, 9, 25))
    except MarketIndexError as exc:
        assert "build seems to be running" in str(exc)
    else:
        raise AssertionError("a repair must not write under a running build")
    assert store.path.read_bytes() == before
    assert not [p for p in tmp_path.iterdir() if "pre-gbx" in p.name]


def test_a_dry_run_and_an_explicit_override_are_still_allowed_under_a_build(tmp_path):
    store = _store_with(tmp_path, [_old_gbx()])
    build_log_path(store).write_text("x\n", encoding="utf-8")
    assert fix_gbx_scale(store, dry_run=True).repaired == 1
    assert fix_gbx_scale(store, today=date(2026, 9, 25), even_if_build_running=True).repaired == 1


def test_an_old_or_missing_build_log_does_not_block_the_repair(tmp_path):
    store = _store_with(tmp_path, [_old_gbx()])
    assert build_seems_active(store) is False                        # no log at all
    log = build_log_path(store)
    log.write_text("x\n", encoding="utf-8")
    mtime = log.stat().st_mtime
    assert build_seems_active(store, now=mtime + 60) is True
    assert build_seems_active(store, now=mtime + 3600) is False
