"""TIE-CHECK-1 - a company that shares its combined rank-sum with others says so.

JPM, BAC, WFC and C were all exactly 19th of 27 on Financials (rank-sum 47.0 each - genuine ties,
not a tie-break bug): the page printed "19th of 27" for each with nothing to say they shared it."""
from types import SimpleNamespace

from aristos_council.company_report import LensVote
from aristos_council.pipeline import combine_rank_results
from aristos_council.rank_engine import RankedTicker


def _r(ticker, combined, verdict="hold"):
    return RankedTicker(ticker=ticker, factor_ranks={"f": combined}, factor_values={},
                        combined_rank=combined, universe_size=6, verdict=verdict)


def test_a_four_way_tie_is_counted_for_each_member():
    ranked = [_r("A", 1.0), _r("B", 2.0), _r("BAC", 3.0), _r("C", 3.0), _r("JPM", 3.0),
              _r("WFC", 3.0, "sell")]
    res = SimpleNamespace(ranked=ranked, names={}, excluded=[], unrateable=[], fetch_errors=[],
                          rank_strategy=None)
    rows = {r.ticker: r.cells["fin"] for r in combine_rank_results({"fin": res})}
    assert {t: (c.position, c.tied_with) for t, c in rows.items()} == {
        "A": (1, 0), "B": (2, 0), "BAC": (3, 3), "C": (3, 3), "JPM": (3, 3), "WFC": (3, 3)}


def test_the_vote_prints_the_tie():
    v = LensVote("financials_v1", "Financials", status="ranked", verdict="hold", position=19,
                 cohort_size=27, tied_with=3)
    assert v.result() == "HOLD - 19th of 27 (tied with 3)"
    assert LensVote("financials_v1", "Financials", status="ranked", verdict="hold", position=19,
                    cohort_size=27).result() == "HOLD - 19th of 27"
