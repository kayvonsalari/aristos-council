"""AGREEMENT-SELL-1 — SELL votes are stated wherever the agreement is.

Seen: Ford's company page had 3 SELL votes (Earnings Power Value, Magic Formula RAW, Quality), yet
the agreement line said only "BUY on 0 of 3 votes; 1 lens did not apply"."""
from aristos_council.company_report import LensVote, build_agreement
from aristos_council.pipeline import lens_agreement
from tests.test_agreement import _Row, _multi
import pytest  # noqa: E402

# B25-1: built under the old three-name rule; every test ABOUT the five-name rule is in test_min_group_5.py
pytestmark = pytest.mark.min_group(3)



def _v(label, verdict):
    return LensVote(strategy_id=label, label=label, status="ranked", verdict=verdict,
                    position=1, cohort_size=10)


def _na(label):
    return LensVote(strategy_id=label, label=label, status="excluded", reason="r")


def test_ford_three_sells_no_buy_one_did_not_apply():
    ag = build_agreement([_v("Earnings Power Value", "sell"), _v("Magic Formula RAW", "sell"),
                          _v("Quality", "sell"), _na("Growth")])
    assert ag.headline == "SELL on 3 of 3 votes; no BUY; 1 lens did not apply to this company"


def test_mixed_votes_state_both():
    ag = build_agreement([_v("A", "buy"), _v("B", "buy"), _v("C", "sell")])
    assert ag.headline == "BUY on 2 of 3 votes; SELL on 1"


def test_buys_only_and_holds_only_read_as_before():
    assert build_agreement([_v("A", "buy"), _v("B", "hold")]).headline == "BUY on 1 of 2 votes"
    assert build_agreement([_v("A", "hold")]).headline == "BUY on 0 of 1 vote"


def test_the_company_table_row_carries_the_sell_votes():
    ag = build_agreement([_v("A", "sell"), _v("B", "sell"), _v("C", "hold")])
    row = ag.table_row("Ford Motor Company (F)")
    assert row["SELL votes"] == "2 of 3" and row["Voted SELL"] == "A, B"


def test_a_list_states_names_that_are_sold_and_not_bought():
    ag = lens_agreement(_multi(
        a_v1=[_Row("X", "buy", 1), _Row("Y", "sell", 2), _Row("Z", "hold", 3)],
        b_v1=[_Row("X", "buy", 1), _Row("Y", "sell", 2), _Row("Z", "hold", 3)]))
    assert ag.sell_no_buy_count == 1
    assert "SELL on 1 name no lens bought" in ag.summary_clause


def test_all_sell_list_does_not_read_as_an_empty_shortlist():
    ag = lens_agreement(_multi(a_v1=[_Row("X", "sell", 1), _Row("Y", "hold", 2), _Row("W", "hold", 3)]))
    assert ag.summary_clause == " — shortlist: no name BUY, SELL on 1 name no lens bought"
