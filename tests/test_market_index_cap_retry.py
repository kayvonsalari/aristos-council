"""INDEX-CAP-RETRY-1 — stop paying to be told "NA" every build.

MEASURED, 2026-09-24: 2,566 rows carry no market cap and 1,767 no classification, and EVERY
one was fetched under the listing parser WITH A NAME — the provider answered, it simply had
nothing in it. A direct probe of ``/fundamentals/0052.TW`` returns
``"MarketCapitalization": "NA"``: not a blank, not a timeout, an explicit *not available*. The
2026-09-23 build refetched 1,502 such rows and filled **none**, for roughly 15,000 charged
units.

So a row the provider has already answered emptily twice waits a month. Three things have to
hold, and each is a way this could have gone wrong:

* **the schedule** — below the threshold ask freely, above it only once the days have passed;
* **the bookkeeping** — "empty" means the refetch changed nothing. A refetch that FILLS
  something resets the count, because that row is making progress and deserves asking again;
* **the backlog** — rows written before the counter existed must not all read as DUE, or the
  first build after this change spends the very units the change exists to save. The evidence
  is already on the row.
"""
from __future__ import annotations

from datetime import date

import pytest

from aristos_council import market_index as mi

TODAY = date(2026, 9, 24)


def _row(**kwargs) -> mi.IndexRow:
    base = dict(ticker="X.US", name="Example Inc", market="US", exchange="NYSE",
                industry="Software", source=mi.SOURCE_EODHD_LISTING,
                fetched_at=TODAY.isoformat(), primary_ticker="X.US", isin="US0000000001")
    base.update(kwargs)
    return mi.IndexRow(**base)


# --------------------------------------------------------------------------- #
# what counts as missing
# --------------------------------------------------------------------------- #
def test_a_complete_row_is_missing_nothing():
    assert _row(market_cap=1.0e9).missing_fields == frozenset()


def test_a_capless_row_is_missing_its_cap():
    assert _row(market_cap=None).missing_fields == frozenset({"market_cap"})


def test_a_classification_less_row_is_missing_its_classification():
    row = _row(market_cap=1.0e9, industry="", gics_industry="", gics_subindustry="")
    assert row.missing_fields == frozenset({"classification"})


def test_a_row_from_the_old_parser_is_missing_its_listing_fields():
    assert "listing" in _row(market_cap=1.0e9, source=mi.SOURCE_EODHD).missing_fields


def test_a_row_can_be_missing_several_things_at_once():
    row = _row(market_cap=None, industry="", gics_industry="", gics_subindustry="")
    assert row.missing_fields == frozenset({"market_cap", "classification"})


# --------------------------------------------------------------------------- #
# the schedule
# --------------------------------------------------------------------------- #
def test_below_the_threshold_a_row_is_always_due():
    row = _row(market_cap=None, empty_attempts=1, last_attempt_at=TODAY.isoformat())
    assert row.retry_due(TODAY, after_attempts=2, every_days=30) is True


def test_at_the_threshold_the_row_waits():
    """The first empty answers may be a bad day at the provider; the third is a pattern."""
    row = _row(market_cap=None, empty_attempts=2, last_attempt_at=TODAY.isoformat())
    assert row.retry_due(TODAY, after_attempts=2, every_days=30) is False
    assert row.waiting(TODAY, after_attempts=2, every_days=30) is True


@pytest.mark.parametrize("days, due", [(0, False), (29, False), (30, True), (365, True)])
def test_a_waiting_row_becomes_due_once_the_days_have_passed(days, due):
    from datetime import timedelta

    row = _row(market_cap=None, empty_attempts=5, last_attempt_at=TODAY.isoformat())
    assert row.retry_due(TODAY + timedelta(days=days), after_attempts=2,
                         every_days=30) is due


def test_both_knobs_are_honoured():
    row = _row(market_cap=None, empty_attempts=3, last_attempt_at=TODAY.isoformat())
    assert row.retry_due(TODAY, after_attempts=5, every_days=30) is True   # under threshold
    assert row.retry_due(TODAY, after_attempts=2, every_days=0) is True    # no wait at all


def test_an_unreadable_attempt_date_means_ask_once_rather_than_wait_forever():
    """A row that cannot say when it was asked must not be silently frozen."""
    row = _row(market_cap=None, name="", empty_attempts=9, last_attempt_at="not a date")
    assert row.retry_due(TODAY, after_attempts=2, every_days=30) is True


# --------------------------------------------------------------------------- #
# the backlog: rows written before the counter existed
# --------------------------------------------------------------------------- #
def test_a_row_the_provider_already_answered_emptily_is_recognised():
    """Fetched under the listing parser AND carrying a name: the provider returned a document
    and it had no cap in it."""
    assert _row(market_cap=None).asked_and_got_nothing is True


def test_a_row_that_was_never_answered_is_not():
    assert _row(market_cap=None, name="").asked_and_got_nothing is False
    assert _row(market_cap=None, source=mi.SOURCE_EODHD).asked_and_got_nothing is False


def test_the_backlog_does_not_all_read_as_due_on_day_one():
    """Otherwise the first build after this change spends the ~25,000 units the change exists
    to save. The evidence is on the row: it was asked (name + listing parser) and ``fetched_at``
    says when."""
    row = _row(market_cap=None, empty_attempts=0, last_attempt_at="")
    assert row.retry_due(TODAY, after_attempts=2, every_days=30) is False


def test_the_backlog_becomes_due_a_month_after_its_last_fetch():
    from datetime import timedelta

    row = _row(market_cap=None, empty_attempts=0, last_attempt_at="")
    assert row.retry_due(TODAY + timedelta(days=29)) is False
    assert row.retry_due(TODAY + timedelta(days=30)) is True


def test_a_row_that_was_never_asked_is_still_due():
    """The inference is only for rows the provider demonstrably answered."""
    row = _row(market_cap=None, name="", empty_attempts=0, last_attempt_at="")
    assert row.retry_due(TODAY, after_attempts=2, every_days=30) is True


def test_a_recorded_attempt_takes_over_from_the_inference():
    """Once a real build writes the numbers down, the derivation stops applying."""
    row = _row(market_cap=None, empty_attempts=1, last_attempt_at=TODAY.isoformat())
    assert row.retry_due(TODAY, after_attempts=2, every_days=30) is True   # 1 < 2


# --------------------------------------------------------------------------- #
# the bookkeeping
# --------------------------------------------------------------------------- #
def test_an_empty_attempt_increments_the_count_and_dates_it():
    previous = _row(market_cap=None, empty_attempts=1, last_attempt_at="2026-08-01")
    fresh = mi._record_attempt(_row(market_cap=None), previous, today=TODAY)
    assert fresh.empty_attempts == 2
    assert fresh.last_attempt_at == TODAY.isoformat()


def test_an_attempt_that_fills_something_resets_the_count():
    """A row that is making progress deserves to be asked again next build."""
    previous = _row(market_cap=None, empty_attempts=4, last_attempt_at="2026-08-01")
    fresh = mi._record_attempt(_row(market_cap=1.0e9), previous, today=TODAY)
    assert fresh.empty_attempts == 0
    assert fresh.last_attempt_at == ""


def test_partial_progress_also_resets_the_count():
    """Cap filled, classification still missing: fewer gaps than before is progress."""
    previous = _row(market_cap=None, industry="", gics_industry="", gics_subindustry="",
                    empty_attempts=3)
    fresh = mi._record_attempt(_row(market_cap=1.0e9, industry="", gics_industry="",
                                    gics_subindustry=""), previous, today=TODAY)
    assert fresh.missing_fields == frozenset({"classification"})
    assert fresh.empty_attempts == 0


def test_a_first_attempt_on_an_unknown_row_counts_as_one():
    fresh = mi._record_attempt(_row(market_cap=None), None, today=TODAY)
    assert fresh.empty_attempts == 1


def test_a_complete_row_carries_no_counter_at_all():
    previous = _row(market_cap=None, empty_attempts=7)
    fresh = mi._record_attempt(_row(market_cap=1.0e9), previous, today=TODAY)
    assert (fresh.empty_attempts, fresh.last_attempt_at) == (0, "")


# --------------------------------------------------------------------------- #
# the status split
# --------------------------------------------------------------------------- #
def _store(tmp_path, rows):
    store = mi.IndexStore(tmp_path)
    store.save(rows)
    return store


def test_status_splits_capless_rows_into_due_and_waiting(tmp_path):
    pytest.importorskip("pandas")
    rows = [_row(ticker="DUE.US", market_cap=None, empty_attempts=1,
                 last_attempt_at=TODAY.isoformat()),
            _row(ticker="WAIT.US", market_cap=None, empty_attempts=2,
                 last_attempt_at=TODAY.isoformat()),
            _row(ticker="OK.US", market_cap=1.0e9)]
    out = mi.status(_store(tmp_path, rows), today=TODAY, empty_retry_after=2,
                    empty_retry_days=30)
    assert out.missing_cap == 2
    assert (out.cap_due, out.cap_waiting) == (1, 1)


def test_status_splits_classification_the_same_way(tmp_path):
    """1,480 of the 1,767 classification-less rows are also cap-less — the same pattern, so
    the same rule."""
    pytest.importorskip("pandas")
    blank = dict(industry="", gics_industry="", gics_subindustry="")
    rows = [_row(ticker="DUE.US", market_cap=1.0e9, empty_attempts=0,
                 last_attempt_at="", name="", **blank),
            _row(ticker="WAIT.US", market_cap=1.0e9, empty_attempts=2,
                 last_attempt_at=TODAY.isoformat(), **blank)]
    out = mi.status(_store(tmp_path, rows), today=TODAY, empty_retry_after=2,
                    empty_retry_days=30)
    assert out.missing_classification == 2
    assert (out.classification_due, out.classification_waiting) == (1, 1)


def test_the_status_lines_say_what_the_next_build_will_spend(tmp_path):
    pytest.importorskip("pandas")
    rows = [_row(ticker=f"W{n}.US", market_cap=None, empty_attempts=2,
                 last_attempt_at=TODAY.isoformat()) for n in range(5)]
    rows.append(_row(ticker="DUE.US", market_cap=None, empty_attempts=0,
                     last_attempt_at="", name=""))
    text = "\n".join(mi.status(_store(tmp_path, rows), today=TODAY, empty_retry_after=2,
                               empty_retry_days=30).lines())
    assert "refetches 1 of them" in text
    assert f"~{mi.CHARGE_FUNDAMENTALS:,} charged units" in text
    assert "5 are waiting on the backoff" in text
    assert "(1 due, 5 waiting)" in text


def test_an_empty_index_still_answers(tmp_path):
    out = mi.status(mi.IndexStore(tmp_path / "nothing"), today=TODAY)
    assert out.rows == 0
    assert "EMPTY" in out.lines()[0]


# --------------------------------------------------------------------------- #
# the round trip
# --------------------------------------------------------------------------- #
def test_the_counter_survives_a_save_and_load_as_a_NUMBER(tmp_path):
    """Parquet round-trips through numpy and the loader stringifies whatever it is not told
    about — an attempt count of "2" would make the arithmetic silently wrong."""
    pytest.importorskip("pandas")
    store = _store(tmp_path, [_row(market_cap=None, empty_attempts=3,
                                   last_attempt_at="2026-09-01")])
    back = store.load()[0]
    assert back.empty_attempts == 3
    assert isinstance(back.empty_attempts, int)
    assert back.last_attempt_at == "2026-09-01"


def test_a_table_written_before_the_columns_existed_still_loads(tmp_path):
    """The columns are new; a parquet file without them must not become unreadable."""
    pandas = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    old_columns = [c for c in mi.COLUMNS if c not in ("empty_attempts", "last_attempt_at")]
    frame = pandas.DataFrame([{c: ("" if c not in ("market_cap", "market_cap_usd") else None)
                               for c in old_columns}])
    frame.loc[0, "ticker"] = "OLD.US"
    root = tmp_path / "old"
    root.mkdir()
    frame.to_parquet(root / mi.INDEX_FILE, index=False)
    rows = mi.IndexStore(root).load()
    assert len(rows) == 1
    assert rows[0].ticker == "OLD.US"
    assert rows[0].empty_attempts == 0            # absent, not a crash
    assert rows[0].last_attempt_at == ""


# --------------------------------------------------------------------------- #
# the config, and Milan
# --------------------------------------------------------------------------- #
def test_the_knobs_are_configurable(tmp_path):
    config = tmp_path / "market_index.yaml"
    config.write_text("exchanges: [US]\nempty_retry_after: 5\nempty_retry_days: 7\n",
                      encoding="utf-8")
    loaded = mi.load_config(config)
    assert loaded["empty_retry_after"] == 5
    assert loaded["empty_retry_days"] == 7


def test_the_knobs_have_the_measured_defaults(tmp_path):
    config = tmp_path / "absent.yaml"
    assert mi.load_config(config)["empty_retry_after"] == 2
    assert mi.load_config(config)["empty_retry_days"] == 30


def test_milan_is_gone_from_the_tracked_config():
    """Every candidate spelling 404s (MI, MTA, BIT, MIL, IT, XMIL, probed 2026-09-24), so
    there is no code to correct it to and a known-404 listing costs a request every build."""
    assert "MI" not in mi.load_config(mi.DEFAULT_CONFIG)["exchanges"]


def test_the_config_records_why_milan_went_and_why_the_others_stayed():
    text = mi.DEFAULT_CONFIG.read_text(encoding="utf-8")
    assert "MILAN IS REMOVED" in text
    assert "MTA -> 404" in text
    for code in ("T", "KS", "HK"):
        assert code in mi.load_config(mi.DEFAULT_CONFIG)["exchanges"]
