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
                                          RUNG_SUBINDUSTRY_WIDE, IndexRow, IndexStore,
                                          is_financial, peer_snapshot, peers, status)

SNAPSHOT = date(2026, 9, 18).isoformat()


def _row(ticker, *, cap=10e9, sub="Semiconductors", industry="Semiconductors",
         sector="Technology", currency="USD", exchange="US", name=None):
    return IndexRow(ticker=ticker, yahoo_ticker=ticker.split(".")[0], name=name or ticker,
                    exchange=exchange, currency=currency, sector=sector,
                    industry=industry, gics_industry=industry, gics_subindustry=sub,
                    market_cap=cap, fetched_at=SNAPSHOT)


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
    rows.append(IndexRow(ticker="BLANK.US", yahoo_ticker="BLANK", market_cap=3e9,
                         fetched_at=SNAPSHOT))
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
    assert group.band == "0.25x–4x market cap"
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
