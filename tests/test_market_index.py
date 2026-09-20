"""MARKET-INDEX-1 — the local table and the peer ladder, on a fabricated index.

No network, no key, no parquet: ``peers`` takes the rows directly, which is the seam that
makes the ladder testable at all. The store is exercised separately and only where the
round-trip is the point.

The fabricated index is ~200 rows shaped like the real thing: several sub-industries of
different depths, caps spread over three orders of magnitude, a financial sector, and
rows with holes. The ladder's whole job is to widen only as far as it must and to say how
far it went, so every test here is really one question — did it stop at the right rung,
and did it say so?
"""
from __future__ import annotations

from datetime import date

import pytest

from aristos_council.market_index import (DEFAULT_CAP, DEFAULT_FLOOR, RUNG_INDUSTRY_WIDE,
                                          RUNG_NONE, RUNG_SUBINDUSTRY_TIGHT,
                                          RUNG_SUBINDUSTRY_WIDE, SOURCE_EODHD_LISTING,
                                          IndexRow, IndexStore, company_key,
                                          is_financial, is_home_listing,
                                          one_row_per_company, peer_snapshot, peers,
                                          status)

SNAPSHOT = date(2026, 9, 18).isoformat()


def _row(ticker, *, cap=10e9, sub="Semiconductors", industry="Semiconductors",
         sector="Technology", currency="USD", exchange="US", name=None,
         usd=..., primary=None, isin=None):
    """A fabricated index row.

    MARKET-INDEX-3 changed the contract these rows have to meet, and the fixture moves
    with it rather than the assertions being relaxed:
      * the ladder bands on ``market_cap_usd``, so a row without one is not comparable
        and is excluded by design - ``usd`` defaults to the local figure because these
        fixtures are USD-quoted;
      * a row is a HOME listing when its ticker equals its PrimaryTicker, so the default
        is its own ticker; pass ``primary`` to make it a cross-listing.
    """
    return IndexRow(ticker=ticker, yahoo_ticker=ticker.split(".")[0], name=name or ticker,
                    exchange=exchange, currency=currency, sector=sector,
                    industry=industry, gics_industry=industry, gics_subindustry=sub,
                    market_cap=cap,
                    market_cap_usd=(cap if usd is ... else usd),
                    market_cap_usd_source=("computed" if (cap if usd is ... else usd)
                                           is not None else "abstained"),
                    primary_ticker=(primary if primary is not None else ticker),
                    isin=(isin if isin is not None else f"XX{abs(hash(ticker)) % 10**10:010d}"),
                    fetched_at=SNAPSHOT, source=SOURCE_EODHD_LISTING)


def _index() -> list[IndexRow]:
    """~200 rows. Depths chosen so each rung of the ladder is reachable by some subject."""
    rows: list[IndexRow] = []
    # 1. A DEEP sub-industry, caps tightly clustered -> rung (a) for a mid-sized member.
    for i in range(40):
        rows.append(_row(f"SEMI{i:02d}.US", cap=10e9 * (1.0 + i * 0.05)))
    # 2. A sub-industry deep only when the band widens: 6 near, 14 far away in size.
    for i in range(6):
        rows.append(_row(f"MED{i:02d}.US", cap=5e9 * (1.0 + i * 0.05),
                         sub="Medical Devices", industry="Healthcare Equipment",
                         sector="Healthcare"))
    for i in range(14):
        rows.append(_row(f"MEDFAR{i:02d}.US", cap=5e9 * 7.0 * (1.0 + i * 0.05),
                         sub="Medical Devices", industry="Healthcare Equipment",
                         sector="Healthcare"))
    # 3. A thin sub-industry inside a deep INDUSTRY -> rung (c).
    for i in range(4):
        rows.append(_row(f"AIR{i:02d}.US", cap=8e9 * (1.0 + i * 0.05),
                         sub="Airlines", industry="Transport", sector="Industrials"))
    for i in range(20):
        rows.append(_row(f"RAIL{i:02d}.US", cap=8e9 * (1.0 + i * 0.05),
                         sub="Rail", industry="Transport", sector="Industrials"))
    # 4. A sub-industry too thin at every rung -> abstain.
    for i in range(3):
        rows.append(_row(f"LONE{i:02d}.US", cap=2e9, sub="Space Tourism",
                         industry="Space Tourism", sector="Industrials"))
    # 5. Financials, deep, so a financial subject has peers and a non-financial never
    #    sees them.
    for i in range(30):
        rows.append(_row(f"BANK{i:02d}.US", cap=10e9 * (1.0 + i * 0.05),
                         sub="Diversified Banks", industry="Banks - Diversified",
                         sector="Financial Services"))
    # 6. Rows with holes — a missing cap and a missing classification.
    for i in range(6):
        rows.append(_row(f"NOCAP{i:02d}.US", cap=None))
    # Deliberately classification-less, but otherwise COMPLETE: it must get past the
    # size checks so that the thing it is testing - the classification abstention - is
    # what it actually hits. MARKET-INDEX-3 added a USD check before that one.
    rows.append(IndexRow(ticker="BLANK.US", yahoo_ticker="BLANK", market_cap=3e9,
                         market_cap_usd=3e9, market_cap_usd_source="computed",
                         primary_ticker="BLANK.US", isin="XX0000000009",
                         fetched_at=SNAPSHOT, source=SOURCE_EODHD_LISTING))
    return rows


INDEX = _index()


def _peers(ticker, **kw):
    return peers(ticker, rows=INDEX, **kw)


# =========================================================================== #
# 1. the ladder stops at the first rung that clears the floor
# =========================================================================== #
def test_a_deep_tightly_sized_sub_industry_stops_at_the_first_rung():
    group = _peers("SEMI20.US")
    assert group.rung == RUNG_SUBINDUSTRY_TIGHT
    # MARKET-INDEX-3: the bands compare USD, and the label says so.
    assert group.band == "0.25x-4x market cap (USD)"
    assert len(group.members) >= DEFAULT_FLOOR


def test_it_widens_the_BAND_before_it_widens_the_CLASSIFICATION():
    """Six near-sized peers is under the floor; the same sub-industry holds fourteen more
    at seven times the size. Widening the band keeps the industry, which is the cheaper
    concession, so it must be tried first."""
    group = _peers("MED00.US")
    assert group.rung == RUNG_SUBINDUSTRY_WIDE
    assert all(r.gics_subindustry == "Medical Devices" for r in group.members)


def test_it_widens_to_the_INDUSTRY_only_when_the_sub_industry_cannot_fill():
    group = _peers("AIR00.US")
    assert group.rung == RUNG_INDUSTRY_WIDE
    assert {r.gics_subindustry for r in group.members} == {"Airlines", "Rail"}
    assert all(r.gics_industry == "Transport" for r in group.members)


def test_the_rungs_it_tried_and_failed_are_in_the_reasons():
    group = _peers("AIR00.US")
    tried = " | ".join(group.reasons)
    assert RUNG_SUBINDUSTRY_TIGHT in tried and RUNG_SUBINDUSTRY_WIDE in tried
    assert "comparable companies found" in tried


# =========================================================================== #
# 2. abstention
# =========================================================================== #
def test_a_company_with_no_comparables_abstains_rather_than_widening_forever():
    group = _peers("LONE00.US")
    assert group.rung == RUNG_NONE
    assert group.members == [] and group.available is False
    assert any("only" in r and "comparable companies found" in r for r in group.reasons)


def test_a_subject_with_no_market_cap_abstains_and_says_which_is_missing():
    group = _peers("NOCAP00.US")
    assert not group.available
    assert any("no market cap" in r for r in group.reasons)


def test_a_subject_with_no_classification_abstains():
    group = _peers("BLANK.US")
    assert not group.available
    assert any("no industry classification" in r for r in group.reasons)


def test_a_ticker_that_is_not_in_the_index_says_so():
    group = _peers("NOTLISTED.US")
    assert not group.available
    assert "not in the market index" in group.reasons[0]


def test_an_empty_index_tells_the_reader_how_to_build_one():
    group = peers("ANY", rows=[])
    assert not group.available
    assert "market index is empty" in group.reasons[0]
    assert "market_index build" in group.reasons[0]


# =========================================================================== #
# 3. who is excluded, and whether it is said
# =========================================================================== #
def test_the_subject_is_never_its_own_peer():
    group = _peers("SEMI20.US")
    assert "SEMI20.US" not in {r.ticker for r in group.members}


def test_a_non_financial_never_gets_financial_peers():
    group = _peers("SEMI20.US")
    assert not any(is_financial(r) for r in group.members)
    assert not any(r.ticker.startswith("BANK") for r in group.members)


def test_a_financial_gets_financial_peers_and_only_those():
    group = _peers("BANK10.US")
    assert group.available
    assert all(is_financial(r) for r in group.members)


def test_is_financial_reads_the_sector_then_the_industry_prefix():
    assert is_financial(_row("A.US", sector="Financial Services", industry="Whatever"))
    assert is_financial(_row("B.US", sector="Other", industry="Banks - Regional"))
    assert is_financial(_row("C.US", sector="Other", industry="Insurance - Life"))
    assert not is_financial(_row("D.US", sector="Technology", industry="Semiconductors"))


def test_candidates_with_no_market_cap_are_skipped_and_COUNTED():
    group = _peers("SEMI20.US")
    assert any("no market cap in the index" in r for r in group.reasons)
    assert not any(r.market_cap is None for r in group.members)


# =========================================================================== #
# 4. the cap, and the trim
# =========================================================================== #
def test_the_group_is_trimmed_to_the_cap_and_says_it_trimmed():
    group = _peers("SEMI20.US", cap=10)
    assert len(group.members) == 10
    assert any("trimmed to 10 nearest in size" in r for r in group.reasons)


def test_the_trim_keeps_the_nearest_in_size_on_a_LOG_scale():
    """Linear distance would keep a peer ten times the size over one at half — the giant
    is 'closer' in absolute terms and nowhere near in kind."""
    subject = _row("SUBJ.US", cap=10e9)
    near_small = _row("SMALL.US", cap=5e9)          # half: log distance 0.69
    far_big = _row("BIG.US", cap=40e9)              # 4x:   log distance 1.39
    rows = [subject, near_small, far_big] + [
        _row(f"FILL{i:02d}.US", cap=10e9 * (1 + i * 0.01)) for i in range(10)]
    # 12 match the tight band (10 fillers + SMALL + BIG), so the floor is met and the cap
    # forces exactly one to be dropped. Log distance: fillers ~0.0, SMALL 0.69, BIG 1.39.
    group = peers("SUBJ.US", rows=rows, cap=11)
    kept = {r.ticker for r in group.members}
    assert len(kept) == 11
    assert "SMALL.US" in kept and "BIG.US" not in kept


def test_no_cap_means_everything_that_matched():
    tight = _peers("SEMI20.US", cap=DEFAULT_CAP)
    assert len(tight.members) <= DEFAULT_CAP


def test_the_floor_is_what_decides_the_rung():
    """Raise the floor above what the tight band holds and the ladder must widen."""
    assert len(_peers("SEMI20.US", floor=39).members) > 0          # 39 peers exist
    high = _peers("SEMI20.US", floor=40)                            # one more than exists
    assert high.rung != RUNG_SUBINDUSTRY_TIGHT


# =========================================================================== #
# 5. determinism
# =========================================================================== #
def test_the_same_snapshot_always_gives_the_same_group():
    first = _peers("SEMI20.US")
    second = _peers("SEMI20.US")
    assert [r.ticker for r in first.members] == [r.ticker for r in second.members]
    assert first.rung == second.rung and first.band == second.band


def test_the_group_does_not_depend_on_the_order_of_the_index():
    forward = peers("SEMI20.US", rows=list(INDEX))
    backward = peers("SEMI20.US", rows=list(reversed(INDEX)))
    assert [r.ticker for r in forward.members] == [r.ticker for r in backward.members]
    assert forward.rung == backward.rung


def test_the_members_are_listed_in_ticker_order_not_in_distance_order():
    group = _peers("SEMI20.US")
    assert [r.ticker for r in group.members] == sorted(r.ticker for r in group.members)


# =========================================================================== #
# 6. what a report saves
# =========================================================================== #
def test_the_snapshot_records_what_a_rerun_would_need():
    snap = peer_snapshot(_peers("SEMI20.US"))
    assert snap["subject"] == "SEMI20.US"
    assert snap["snapshot"] == SNAPSHOT
    assert snap["rung"] == RUNG_SUBINDUSTRY_TIGHT and snap["band"]
    assert len(snap["members"]) == len(snap["yahoo_members"]) > 0
    assert "SEMI20.US" not in snap["members"]


def test_the_snapshot_of_an_abstention_carries_the_reasons():
    snap = peer_snapshot(_peers("LONE00.US"))
    assert snap["members"] == [] and snap["reasons"]


# =========================================================================== #
# 7. the store and status
# =========================================================================== #
def test_status_on_an_empty_index_answers_instead_of_raising(tmp_path):
    out = status(IndexStore(tmp_path / "nothing"))
    assert out.rows == 0
    text = "\n".join(out.lines())
    assert "EMPTY" in text and "market_index build" in text


def test_the_table_round_trips_through_parquet(tmp_path):
    pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    store = IndexStore(tmp_path)
    store.save(INDEX)
    back = store.load()
    assert len(back) == len(INDEX)
    by_ticker = {r.ticker: r for r in back}
    assert by_ticker["SEMI20.US"].gics_subindustry == "Semiconductors"
    assert by_ticker["NOCAP00.US"].market_cap is None      # a hole stays a hole
    # ...and the ladder gives the same answer off the restored table
    assert [r.ticker for r in peers("SEMI20.US", rows=back).members] == \
        [r.ticker for r in _peers("SEMI20.US").members]


def test_status_counts_the_holes_it_found(tmp_path):
    pytest.importorskip("pyarrow")
    store = IndexStore(tmp_path)
    store.save(INDEX)
    out = status(store)
    assert out.rows == len(INDEX)
    assert out.missing_cap == 6
    assert out.missing_classification == 1                 # BLANK.US
    # BLANK.US has no exchange either, so it is counted under "?" rather than guessed
    # into US — the same null-is-not-a-value discipline the rest of the row follows.
    assert out.per_exchange["US"] == len(INDEX) - 1
    assert out.per_exchange["?"] == 1
    assert sum(out.per_exchange.values()) == len(INDEX)
    assert out.oldest == SNAPSHOT


# =========================================================================== #
# 8. the Company Check tab renders either way
# =========================================================================== #
def _company_result(ticker="SEMI20.US"):
    """The two fields the new sections read, on a stand-in for CompanyCheckResult."""
    from aristos_council.abs_readings import debt_and_cash, growth_record
    from aristos_council.data.adapter import Fundamentals

    f = Fundamentals(ticker=ticker, currency="USD", total_debt=50e9, total_cash=10e9,
                     operating_cash_flow=8e9, free_cash_flow=5e9, operating_income=12e9,
                     aligned_annual={"interest_expense": [1.5e9],
                                     "total_revenue": [200.0, 180, 160, 140, 120, 100.0]})

    class _Result:
        pass
    out = _Result()
    out.ticker = ticker
    out.debt_and_cash = debt_and_cash(f)
    out.growth_record = growth_record(f)
    return out


def test_the_peers_section_shows_build_instructions_when_there_is_no_index(tmp_path,
                                                                          monkeypatch):
    """An empty index must not take the tab down — the first thing anyone sees is a tab
    with no index behind it."""
    pytest.importorskip("streamlit")
    import app
    from aristos_council import market_index

    monkeypatch.setattr(market_index, "load_config",
                        lambda *a, **k: {"root": str(tmp_path / "nothing"),
                                         "exchanges": [], "max_age_days": 30})
    app._render_peers(_company_result())          # must not raise


def test_the_peers_section_renders_a_group_off_a_fabricated_index(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    pytest.importorskip("pyarrow")
    import app
    from aristos_council import market_index

    IndexStore(tmp_path).save(INDEX)
    monkeypatch.setattr(market_index, "load_config",
                        lambda *a, **k: {"root": str(tmp_path), "exchanges": [],
                                         "max_age_days": 30})
    app._render_peers(_company_result())          # must not raise

    # ...and the group behind it is the real one, not an empty stand-in
    group = peers("SEMI20.US", store=IndexStore(tmp_path))
    assert group.available and group.rung == RUNG_SUBINDUSTRY_TIGHT


def test_the_absolute_readings_section_renders():
    pytest.importorskip("streamlit")
    import app

    app._render_absolute_readings(_company_result())        # must not raise


def test_the_absolute_readings_section_is_silent_when_there_is_nothing_to_show():
    pytest.importorskip("streamlit")
    import app

    class _Bare:
        ticker = "X"
        debt_and_cash = None
        growth_record = None
    app._render_absolute_readings(_Bare())

# =========================================================================== #
# 9. MARKET-INDEX-2 - the first build's two bugs
# =========================================================================== #
# A first US build fetched 526 rows. ALL 526 had market_cap None, and 337 of them were
# OTC / pink-sheet listings. Both were bugs in the build: the cap was requested from the
# wrong block, and nothing filtered the venue.
from aristos_council.market_index import (BUILD_LOG, CHARGE_FUNDAMENTALS, CHARGE_LISTING,
                                          DEFAULT_VENUES, MarketIndexError, build,
                                          build_log_path, read_market_cap, venue_allowed)


class _FakeSource:
    """A source with no socket. Counts requests and charged units exactly as the real one."""

    def __init__(self, listings=None, docs=None, raise_on=None):
        self._listings = listings if listings is not None else []
        self._docs = docs or {}
        self._raise_on = raise_on
        self.requests = 0
        self.charged = 0
        self.indexes = ()
        self.fetched: list[str] = []

    def common_stocks(self, exchange):
        self.requests += 1
        self.charged += CHARGE_LISTING
        return [dict(r) for r in self._listings]

    def general(self, symbol):
        self.requests += 1
        self.charged += CHARGE_FUNDAMENTALS
        self.fetched.append(symbol)
        if self._raise_on and symbol == self._raise_on:
            raise RuntimeError("payload exploded")
        return dict(self._docs.get(symbol, _doc()))


def _listing(code, venue="NYSE"):
    return {"Code": code, "Name": code, "Exchange": venue, "Type": "Common Stock"}


def _doc(cap=10e9, venue="NYSE", name="A Co"):
    """The COMBINED-filter response shape, as probed from the live API on 2026-09-18."""
    doc = {"General": {"Code": "X", "Name": name, "Exchange": venue,
                       "CurrencyCode": "USD", "Sector": "Technology",
                       "Industry": "Semiconductors", "GicSubIndustry": "Semiconductors"}}
    if cap is not None:
        doc["Highlights::MarketCapitalization"] = cap
    return doc


def _build(tmp_path, **kw):
    kw.setdefault("exchanges", ["US"])
    kw.setdefault("venues", dict(DEFAULT_VENUES))
    kw.setdefault("usd", _NoFx())
    kw.setdefault("today", date(2026, 9, 18))
    return build(store=IndexStore(tmp_path), **kw)


class _NoFx:
    """The USD converter, switched off: these tests are about the build, not about FX."""

    def apply(self, row):
        return row


# -- the cap, from the right block ----------------------------------------- #
def test_the_cap_is_read_from_HIGHLIGHTS_not_general():
    """THE bug: 526 of 526 rows had no cap because the request asked for General."""
    assert read_market_cap(_doc(cap=4_918_238_773_248)) == 4_918_238_773_248
    assert read_market_cap({"Highlights": {"MarketCapitalization": 123.0}}) == 123.0
    assert read_market_cap({"MarketCapitalization": 7.0}) == 7.0


def test_a_missing_or_zero_cap_is_a_real_abstention():
    assert read_market_cap(_doc(cap=None)) is None
    assert read_market_cap({"Highlights::MarketCapitalization": 0}) is None
    assert read_market_cap({"Highlights::MarketCapitalization": "lots"}) is None


def test_the_string_NA_is_not_a_market_cap():
    """EODHD sends the literal string 'NA', observed live on AACPR.US (a RIGHTS instrument
    that the provider types as "Common Stock"). A float() of it would raise; a truthiness
    test would pass it through as a number. It is an absence, and it is counted as one."""
    assert read_market_cap({"Highlights::MarketCapitalization": "NA"}) is None
    assert read_market_cap({"Highlights::MarketCapitalization": ""}) is None
    assert read_market_cap({"Highlights::MarketCapitalization": True}) is None


def test_the_general_block_is_read_in_both_layouts():
    """``filter=General`` flattens; the combined filter nests. Rows already on disk were
    fetched the first way, so both must parse."""
    combined = _row_from_doc(_doc(name="Nested Co"))
    flat = _row_from_doc({"Code": "X", "Name": "Flat Co", "Exchange": "NYSE"})
    assert combined.name == "Nested Co" and combined.market_cap is not None
    assert flat.name == "Flat Co" and flat.market_cap is None


def _row_from_doc(doc):
    from aristos_council.market_index import _row_from_general
    return _row_from_general("X.US", "US", doc, today=date(2026, 9, 18))


def test_a_row_knows_whether_it_is_complete():
    assert _row_from_doc(_doc()).complete
    assert not _row_from_doc(_doc(cap=None)).complete


# -- the venue filter ------------------------------------------------------- #
@pytest.mark.parametrize("venue", ["NYSE", "NASDAQ", "NYSE ARCA", "AMEX"])
def test_the_allowed_us_venues_are_kept(venue):
    assert venue_allowed(venue, DEFAULT_VENUES["US"])


@pytest.mark.parametrize("venue", ["PINK", "OTCQB", "OTCQX", "OTCGREY", "OTCMKTS",
                                   "BATS", "NYSE MKT", "US"])
def test_otc_and_unlisted_venues_are_dropped(venue):
    assert not venue_allowed(venue, DEFAULT_VENUES["US"])


def test_an_exchange_with_no_venue_list_is_unrestricted():
    assert venue_allowed("ANYTHING", [])


def test_otc_listings_cost_NOTHING_because_they_are_filtered_before_the_call(tmp_path):
    """Filtered on the LISTING row. 11,508 of 17,829 US common stocks were OTC; fetching
    them would be ~118,000 charged units, more than a whole day's budget."""
    listings = ([_listing(f"GOOD{i}", "NYSE") for i in range(3)]
                + [_listing(f"PINK{i}", "PINK") for i in range(20)]
                + [_listing("QB", "OTCQB")])
    source = _FakeSource(listings)

    outcome = _build(tmp_path, source=source)

    assert outcome.listed == 24 and outcome.eligible == 3
    assert sorted(source.fetched) == ["GOOD0.US", "GOOD1.US", "GOOD2.US"]
    assert not any(t.startswith("PINK") or t.startswith("QB") for t in source.fetched)


def test_every_venue_seen_is_counted_including_the_dropped_ones(tmp_path):
    listings = [_listing("A", "NYSE"), _listing("B", "PINK"), _listing("C", "PINK")]
    outcome = _build(tmp_path, source=_FakeSource(listings))
    assert outcome.venues_seen == {"NYSE": 1, "PINK": 2}
    text = "\n".join(outcome.venue_lines())
    assert "PINK" in text and "2" in text


# -- invalidation ----------------------------------------------------------- #
def test_a_capless_row_is_refetched_however_fresh_it_is(tmp_path):
    """All 526 rows on disk are capless and dated today. Age must not protect them: the
    freshness skip is for COMPLETE rows, and a capless row was never usable."""
    store = IndexStore(tmp_path)
    store.save([IndexRow(ticker="A.US", exchange="NYSE", market="US", market_cap=None,
                         fetched_at="2026-09-18", source="eodhd")])
    source = _FakeSource([_listing("A", "NYSE")])

    outcome = build(store=store, source=source, exchanges=["US"],
                    venues=dict(DEFAULT_VENUES), usd=_NoFx(), today=date(2026, 9, 18))

    assert source.fetched == ["A.US"]
    assert outcome.refetched_incomplete == 1 and outcome.skipped_fresh == 0
    assert store.load()[0].market_cap is not None


def test_a_COMPLETE_fresh_row_is_still_skipped_without_a_call(tmp_path):
    store = IndexStore(tmp_path)
    # MARKET-INDEX-3: "complete" now also means "fetched by a parser that ASKED for
    # PrimaryTicker and ISIN", so the fixture carries that generation tag. What is under
    # test is unchanged - a complete, fresh row costs no call.
    store.save([IndexRow(ticker="A.US", exchange="NYSE", market="US", market_cap=5e9,
                         primary_ticker="A.US", isin="US0000000001",
                         fetched_at="2026-09-18", source=SOURCE_EODHD_LISTING)])
    source = _FakeSource([_listing("A", "NYSE")])

    outcome = build(store=store, source=source, exchanges=["US"],
                    venues=dict(DEFAULT_VENUES), usd=_NoFx(), today=date(2026, 9, 18))

    assert source.fetched == [] and outcome.skipped_fresh == 1


def test_rows_on_a_disallowed_venue_are_dropped_from_the_store(tmp_path):
    """337 of the 526 were OTC. They are removed, not left to rot in the table."""
    store = IndexStore(tmp_path)
    store.save([
        IndexRow(ticker="GOOD.US", exchange="NYSE", market="US", market_cap=5e9,
                 primary_ticker="GOOD.US", fetched_at="2026-09-18",
                 source=SOURCE_EODHD_LISTING),
        IndexRow(ticker="PINKY.US", exchange="PINK", market="US", market_cap=1e9,
                 fetched_at="2026-09-18", source="eodhd"),
        IndexRow(ticker="QB.US", exchange="OTCQB", market="US", market_cap=1e9,
                 fetched_at="2026-09-18", source="eodhd"),
    ])
    outcome = build(store=store, source=_FakeSource([_listing("GOOD", "NYSE")]),
                    exchanges=["US"], venues=dict(DEFAULT_VENUES), usd=_NoFx(),
                    today=date(2026, 9, 18))

    assert outcome.dropped_venue == 2
    assert [r.ticker for r in store.load()] == ["GOOD.US"]


def test_a_row_on_another_exchange_is_not_dropped_by_a_US_venue_rule(tmp_path):
    store = IndexStore(tmp_path)
    store.save([IndexRow(ticker="SHEL.LSE", exchange="LSE", market="LSE",
                         market_cap=5e9, fetched_at="2026-09-18", source="eodhd")])
    build(store=store, source=_FakeSource([]), exchanges=["US"],
          venues=dict(DEFAULT_VENUES), usd=_NoFx(), today=date(2026, 9, 18))
    assert [r.ticker for r in store.load()] == ["SHEL.LSE"]


# -- accounting and budget -------------------------------------------------- #
def test_charged_units_are_ten_per_fundamentals_and_one_per_listing(tmp_path):
    source = _FakeSource([_listing("A"), _listing("B")])
    outcome = _build(tmp_path, source=source)
    assert outcome.requests == 3                      # 1 listing + 2 fundamentals
    assert outcome.charged == CHARGE_LISTING + 2 * CHARGE_FUNDAMENTALS == 21
    assert "3 request(s) = 21 charged" in outcome.summary()


def test_the_build_stops_before_the_request_that_would_exceed_the_budget(tmp_path):
    source = _FakeSource([_listing(f"T{i}") for i in range(10)])
    # 1 listing + 2 fundamentals = 21; a third would be 31.
    outcome = _build(tmp_path, source=source, budget=25)

    assert outcome.fetched == 2
    assert outcome.charged <= 25
    assert "budget reached" in outcome.stopped
    assert str(CHARGE_FUNDAMENTALS) in outcome.stopped


def test_the_budget_stop_still_flushes_what_it_had(tmp_path):
    store = IndexStore(tmp_path)
    build(store=store, source=_FakeSource([_listing(f"T{i}") for i in range(10)]),
          exchanges=["US"], venues=dict(DEFAULT_VENUES), usd=_NoFx(),
          today=date(2026, 9, 18), budget=25)
    assert len(store.load()) == 2


# -- no silent exits -------------------------------------------------------- #
def test_ANY_exception_flushes_the_store_and_writes_the_reason(tmp_path):
    """A build that dies quietly after 4,000 fetches loses 40,000 charged units and tells
    nobody why."""
    store = IndexStore(tmp_path)
    source = _FakeSource([_listing("A"), _listing("BOOM"), _listing("C")],
                         raise_on="BOOM.US")

    outcome = build(store=store, source=source, exchanges=["US"],
                    venues=dict(DEFAULT_VENUES), usd=_NoFx(), today=date(2026, 9, 18))

    assert outcome.stopped.startswith("RuntimeError: payload exploded")
    assert [r.ticker for r in store.load()] == ["A.US"]        # flushed, not lost
    log = build_log_path(store).read_text(encoding="utf-8")
    assert "STOPPED: RuntimeError: payload exploded" in log
    assert "Traceback" in log
    assert "request(s)" in log                                  # the summary went in too


def test_the_log_carries_the_progress_lines_too(tmp_path):
    store = IndexStore(tmp_path)
    build(store=store, source=_FakeSource([_listing("A")]), exchanges=["US"],
          venues=dict(DEFAULT_VENUES), usd=_NoFx(), today=date(2026, 9, 18))
    log = build_log_path(store).read_text(encoding="utf-8")
    assert "listing common stocks" in log
    assert build_log_path(store).name == BUILD_LOG


def test_the_log_is_appended_not_rewritten(tmp_path):
    store = IndexStore(tmp_path)
    for _ in range(2):
        build(store=store, source=_FakeSource([_listing("A")]), exchanges=["US"],
              venues=dict(DEFAULT_VENUES), usd=_NoFx(), today=date(2026, 9, 18))
    lines = build_log_path(store).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 4


def test_a_limit_stop_is_not_an_error_but_a_budget_stop_is(tmp_path):
    limited = _build(tmp_path, source=_FakeSource([_listing("A"), _listing("B")]),
                     limit=1)
    assert "limit" in limited.stopped
    budgeted = _build(tmp_path, source=_FakeSource([_listing(f"T{i}") for i in range(9)]),
                      budget=15)
    assert "limit" not in budgeted.stopped


def test_status_says_how_many_rows_cannot_be_peers(tmp_path):
    store = IndexStore(tmp_path)
    store.save([IndexRow(ticker="A.US", exchange="NYSE", market="US", market_cap=None,
                         fetched_at="2026-09-18", source=SOURCE_EODHD_LISTING),
                IndexRow(ticker="B.US", exchange="NYSE", market="US", market_cap=5e9,
                         primary_ticker="B.US", isin="US0000000002",
                         fetched_at="2026-09-18", source=SOURCE_EODHD_LISTING)])
    text = "\n".join(status(store).lines())
    assert "no market cap:              1" in text
    assert "complete (usable as peers): 1" in text
    assert "cannot be peers" in text

# =========================================================================== #
# 10. MARKET-INDEX-3 - one row per company, and one currency
# =========================================================================== #
# The 9,018-row build put AMD.US, AMD.TO and AMD.XETRA in one peer group and NVDA.US
# beside NVD.XETRA. And the bands compared local currency, so Tokyo and Korea - next in
# the build order - would have been compared in yen and won against dollars.
#
# The shapes below are PROBED, 2026-09-20:
#   AAPL.US    PrimaryTicker "AAPL.US"   ISIN US0378331005   home listing
#   AMD.XETRA  PrimaryTicker "AMD.US"    ISIN US0079031078   cross-listing, home's ISIN
#   NVO.US     PrimaryTicker "NOVO-B.CO" ISIN US6701002056   ADR of a Danish company
#   SAP.XETRA  PrimaryTicker "SAP.F"     ISIN DE0007164600   home venue NOT tracked here


def _listed(ticker, *, primary=None, isin=None, cap_usd=10e9, cap=None, currency="USD",
            sub="Semiconductors", country="US", name=None):
    return IndexRow(
        ticker=ticker, yahoo_ticker=ticker.split(".")[0], name=name or ticker,
        exchange=ticker.split(".")[-1], market=ticker.split(".")[-1], country=country,
        currency=currency, primary_ticker=(primary if primary is not None else ticker),
        isin=isin or "", sector="Technology", industry=sub, gics_industry=sub,
        gics_subindustry=sub, market_cap=(cap if cap is not None else cap_usd),
        market_cap_usd=cap_usd,
        market_cap_usd_source=("computed" if cap_usd is not None else "abstained"),
        fetched_at=SNAPSHOT, source=SOURCE_EODHD_LISTING)


# -- home-listing detection ------------------------------------------------- #
def test_a_row_whose_ticker_equals_its_primary_ticker_is_the_home_listing():
    assert is_home_listing(_listed("AAPL.US", primary="AAPL.US"))


def test_a_cross_listing_names_its_home_and_is_not_one():
    assert not is_home_listing(_listed("AMD.XETRA", primary="AMD.US"))
    assert not is_home_listing(_listed("NVD.XETRA", primary="NVDA.US"))


def test_the_comparison_ignores_case():
    assert is_home_listing(_listed("AAPL.US", primary="aapl.us"))


def test_a_row_with_no_primary_ticker_is_treated_as_a_home_listing():
    """Refusing to place it would silently drop the company. It is counted as unresolved
    instead - AMD.TO is the real case: EODHD publishes neither field for a CDR."""
    row = _listed("AMD.TO", primary="", isin="")
    assert is_home_listing(row) and row.unresolvable_listing


# -- the ISIN fallback ------------------------------------------------------ #
def test_rows_sharing_an_ISIN_are_one_company():
    us = _listed("AMD.US", primary="AMD.US", isin="US0079031078")
    de = _listed("AMD.XETRA", primary="AMD.US", isin="US0079031078")
    assert company_key(us) == company_key(de)


def test_the_isin_fallback_prefers_the_row_whose_country_matches_the_issuer():
    """No PrimaryTicker on either row, so the ISIN's issuer country decides: a US ISIN
    belongs to the US line."""
    us = _listed("ACME.US", primary="", isin="US1111111111", country="US")
    de = _listed("ACME.XETRA", primary="", isin="US1111111111", country="DE")
    kept, dropped = one_row_per_company([de, us])
    assert [r.ticker for r in kept] == ["ACME.US"] and dropped == 1


def test_when_no_country_matches_the_choice_is_still_deterministic():
    a = _listed("ACME.XETRA", primary="", isin="GB2222222222", country="DE")
    b = _listed("ACME.TO", primary="", isin="GB2222222222", country="CA")
    first = one_row_per_company([a, b])[0]
    second = one_row_per_company([b, a])[0]
    assert [r.ticker for r in first] == [r.ticker for r in second]


# -- the pool: one row per company ------------------------------------------ #
def test_three_listings_of_one_company_become_one_peer():
    """The acceptance case: AMD.US, AMD.TO and AMD.XETRA return AMD once."""
    rows = [_listed("AMD.US", primary="AMD.US", isin="US0079031078"),
            _listed("AMD.XETRA", primary="AMD.US", isin="US0079031078", country="DE"),
            _listed("AMD.TO", primary="AMD.US", isin="US0079031078", country="CA")]
    kept, dropped = one_row_per_company(rows)
    assert [r.ticker for r in kept] == ["AMD.US"] and dropped == 2


def test_the_home_listing_is_the_one_kept_whatever_order_they_arrive_in():
    rows = [_listed("AMD.XETRA", primary="AMD.US", isin="US0079031078", country="DE"),
            _listed("AMD.US", primary="AMD.US", isin="US0079031078")]
    assert one_row_per_company(rows)[0][0].ticker == "AMD.US"
    assert one_row_per_company(list(reversed(rows)))[0][0].ticker == "AMD.US"


def test_a_company_is_NEVER_lost_when_its_home_venue_is_not_tracked():
    """SAP names SAP.F as its primary and this index does not track Frankfurt. Four of
    ten German blue chips probed on 2026-09-20 were like this (SAP, MBG, RHM, VOW3), so a
    strict home-listings-only pool would delete them from every peer group - the same
    defect as duplicating a company, wearing a different hat."""
    kept, dropped = one_row_per_company(
        [_listed("SAP.XETRA", primary="SAP.F", isin="DE0007164600", country="DE")])
    assert [r.ticker for r in kept] == ["SAP.XETRA"] and dropped == 0


def test_a_cross_listing_is_excluded_from_a_real_peer_group_but_still_resolvable():
    subject = _listed("TSM.US", primary="TSM.US", isin="US8740391003")
    rows = [subject]
    rows += [_listed("AMD.US", primary="AMD.US", isin="US0079031078"),
             _listed("AMD.XETRA", primary="AMD.US", isin="US0079031078", country="DE"),
             _listed("AMD.TO", primary="AMD.US", isin="US0079031078", country="CA")]
    rows += [_listed(f"SEMI{i}.US", isin=f"US99999999{i:02d}") for i in range(11)]

    group = peers("TSM.US", rows=rows)

    assert group.available
    tickers = [r.ticker for r in group.members]
    assert tickers.count("AMD.US") == 1
    assert "AMD.XETRA" not in tickers and "AMD.TO" not in tickers
    assert any("cross-listing(s) collapsed" in r for r in group.reasons)
    # ...and the cross-listing is still IN the table, findable as a subject
    assert peers("AMD.XETRA", rows=rows).subject is not None


# -- subject resolution ----------------------------------------------------- #
def test_an_ADR_resolves_to_its_home_listing_and_says_so():
    home = _listed("NOVO-B.CO", primary="NOVO-B.CO", isin="DK0062498333",
                   country="DK", currency="DKK", cap=600e9, cap_usd=90e9,
                   sub="Pharmaceuticals")
    adr = _listed("NVO.US", primary="NOVO-B.CO", isin="US6701002056",
                  sub="Pharmaceuticals")
    group = peers("NVO.US", rows=[home, adr])
    assert group.subject.ticker == "NOVO-B.CO"
    assert any("NVO.US is a" in r and "listing of NOVO-B.CO" in r
               and "peers computed for the home listing" in r for r in group.reasons)


def test_when_the_home_listing_is_not_in_the_index_the_row_is_used_as_it_stands():
    adr = _listed("NVO.US", primary="NOVO-B.CO", isin="US6701002056")
    group = peers("NVO.US", rows=[adr])
    assert group.subject.ticker == "NVO.US"
    assert any("not in the index" in r and "as it stands" in r for r in group.reasons)


def test_a_home_listing_subject_gets_no_resolution_noise():
    group = peers("AAPL.US", rows=[_listed("AAPL.US", primary="AAPL.US")])
    assert not any("listing of" in r for r in group.reasons)


# -- size in one currency --------------------------------------------------- #
def test_a_yen_company_is_banded_on_its_USD_value_not_its_local_one():
    """900bn JPY is about 6bn USD. Banded in local units against a 9bn USD subject it
    would look a hundred times too large; in USD it is a peer."""
    subject = _listed("SUBJ.US", cap_usd=9e9)
    tokyo = _listed("7203.T", cap=900e9, cap_usd=6e9, currency="JPY", country="JP")
    rows = [subject, tokyo] + [_listed(f"F{i}.US", cap_usd=9e9) for i in range(11)]
    group = peers("SUBJ.US", rows=rows)
    assert "7203.T" in [r.ticker for r in group.members]
    assert "(USD)" in group.band


def test_a_row_that_is_900bn_in_LOCAL_units_and_huge_in_usd_is_NOT_a_peer():
    subject = _listed("SUBJ.US", cap_usd=9e9)
    giant = _listed("HUGE.T", cap=900e9, cap_usd=900e9, currency="JPY", country="JP")
    rows = [subject, giant] + [_listed(f"F{i}.US", cap_usd=9e9) for i in range(11)]
    assert "HUGE.T" not in [r.ticker for r in peers("SUBJ.US", rows=rows).members]


def test_a_row_with_no_USD_conversion_is_excluded_and_counted_SEPARATELY():
    """Counted apart from "no cap": this row HAS a figure, we simply cannot compare it.
    Folding the two together would hide a broken FX rate behind missing data."""
    subject = _listed("SUBJ.US", cap_usd=9e9)
    no_usd = _listed("ODD.T", cap=900e9, cap_usd=None, currency="JPY", country="JP")
    no_cap = IndexRow(ticker="NIL.US", market_cap=None, gics_subindustry="Semiconductors",
                      fetched_at=SNAPSHOT, source=SOURCE_EODHD_LISTING)
    rows = [subject, no_usd, no_cap] + [_listed(f"F{i}.US", cap_usd=9e9) for i in range(11)]

    group = peers("SUBJ.US", rows=rows)

    assert "ODD.T" not in [r.ticker for r in group.members]
    assert any("no USD conversion" in r for r in group.reasons)
    assert any("no market cap in the index" in r for r in group.reasons)


def test_a_subject_with_no_USD_conversion_abstains_and_says_which_is_missing():
    subject = _listed("SUBJ.T", cap=900e9, cap_usd=None, currency="JPY", country="JP")
    group = peers("SUBJ.T", rows=[subject] + [_listed(f"F{i}.US") for i in range(11)])
    assert not group.available
    assert any("no USD conversion" in r for r in group.reasons)


# -- refetch for the new fields --------------------------------------------- #
def test_a_row_fetched_before_the_listing_fields_existed_is_incomplete():
    """All 9,018 rows of the first build are like this: they carry a cap but were never
    ASKED for PrimaryTicker or ISIN."""
    legacy = IndexRow(ticker="A.US", market_cap=5e9, fetched_at="2026-09-18",
                      source="eodhd")
    assert not legacy.complete


def test_a_row_the_provider_has_no_listing_fields_for_is_COMPLETE_not_a_refetch_loop():
    """AMD.TO. Asking again every build would be a permanent 10-unit loop for an answer
    that will not change."""
    asked = IndexRow(ticker="AMD.TO", name="AMD CDR", market_cap=5e9,
                     fetched_at="2026-09-18", source=SOURCE_EODHD_LISTING)
    assert asked.complete and asked.unresolvable_listing


def test_rows_lacking_the_new_fields_are_refetched_whatever_their_age(tmp_path):
    store = IndexStore(tmp_path)
    store.save([IndexRow(ticker="A.US", exchange="NYSE", market="US", market_cap=5e9,
                         fetched_at="2026-09-18", source="eodhd")])
    source = _FakeSource([_listing("A", "NYSE")])
    outcome = build(store=store, source=source, exchanges=["US"],
                    venues=dict(DEFAULT_VENUES), usd=_NoFx(), today=date(2026, 9, 18))
    assert source.fetched == ["A.US"] and outcome.refetched_incomplete == 1


def test_status_counts_cross_listings_and_refetches(tmp_path):
    store = IndexStore(tmp_path)
    store.save([
        _listed("AMD.US", primary="AMD.US", isin="US0079031078"),
        _listed("AMD.XETRA", primary="AMD.US", isin="US0079031078", country="DE"),
        IndexRow(ticker="OLD.US", market_cap=1e9, fetched_at=SNAPSHOT, source="eodhd"),
    ])
    text = "\n".join(status(store).lines())
    assert "1 cross-listing(s), excluded from peer groups" in text
    assert "1 row(s) will be REFETCHED" in text
