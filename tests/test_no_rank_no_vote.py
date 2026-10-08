"""NO-RANK-NO-VOTE-1 — a lens that kept fewer than 5 names casts no vote (it was 3 until B25-1, the
MIN-GROUP-5 ruling).

Seen: car-maker list TM, GM, F, STLA, HMC, TSLA. Value + Momentum kept 2 names and gave no
positions, yet its BUY still counted in the shortlist. Such a lens is treated exactly like
"does not apply". VM-COUNT-WORDING-1 folded in: one wording, "2 passed its rules, too few to
rank", wherever the count is said."""
from datetime import date

from aristos_council.company_report import LensVote, build_agreement
from aristos_council.pipeline import (comparable_names_line, lens_agreement,
                                      lens_agreement_table, run_multi_strategy_pipeline,
                                      run_rank_pipeline)
from aristos_council.rank_engine import MIN_RANKABLE_COHORT, passed_too_few_text
from aristos_council.report_language import format_summary_line

from tests.test_agreement import A, B, _Row, _multi
from tests.test_multi_strategy_run import RAW, SCREENED, STRAT_DIR, TODAY, UNIVERSE, _Adapter


def test_the_one_wording():
    assert passed_too_few_text(2) == "2 passed its rules, too few to rank"
    assert passed_too_few_text(1) == "1 passed its rules, too few to rank"


def test_a_two_name_lens_casts_no_vote_in_the_agreement_table():
    ag = lens_agreement(_multi(
        a_v1=[_Row("TM", "buy", 1), _Row("GM", "sell", 2)],                       # kept 2
        b_v1=[_Row(t, v, i) for i, (t, v) in enumerate(
            [("TM", "buy"), ("GM", "hold"), ("F", "hold"), ("STLA", "hold"),
             ("HMC", "hold"), ("TSLA", "sell")], 1)]))
    rows = {r.ticker: r for r in ag.rows}
    assert list(rows) == ["TM"]                      # only the 6-name lens's BUY is a vote
    assert rows["TM"].buy_lenses == ("Magic Formula RAW",)
    assert rows["TM"].sell_lenses == () and rows["TM"].voted == 1
    assert rows["TM"].not_ranked == (("Cyclical Income", "2 passed its rules, too few to rank"),)
    # GM's SELL from the 2-name lens is not a vote either
    cols, table = lens_agreement_table(ag)
    assert "(1 did not apply)" in table[0]["BUY votes"]


def test_a_lens_with_five_names_still_votes():
    ag = lens_agreement(_multi(
        a_v1=[_Row("TM", "buy", 1), _Row("GM", "hold", 2), _Row("F", "hold", 3), _Row("STLA", "hold", 4),
              _Row("HMC", "sell", 5)]))
    assert [r.ticker for r in ag.rows] == ["TM"] and ag.rows[0].voted == 1


def test_a_lens_with_three_or_four_names_casts_no_vote():
    """B25-1 (MIN-GROUP-5): the line moved from 3 to 5."""
    for n in (3, 4):
        names = ["TM", "GM", "F", "STLA"][:n]
        verdicts = ["buy"] + ["hold"] * (n - 2) + ["sell"]
        ag = lens_agreement(_multi(a_v1=[_Row(t, v, i) for i, (t, v) in enumerate(zip(names, verdicts), 1)]))
        assert ag.rows == [] or all(r.voted == 0 for r in ag.rows), n


def test_a_lens_that_kept_two_is_a_does_not_apply_on_the_company_page():
    thin = LensVote(strategy_id=A, label="Value + Momentum", status="too_few", cohort_size=2)
    ok = LensVote(strategy_id=B, label="Magic Formula RAW", status="ranked",
                  verdict="buy", position=1, cohort_size=6)
    ag = build_agreement([thin, ok])
    assert ag.buy == ("Magic Formula RAW",) and ag.n_voted == 1
    assert ag.n_not_applying == 1
    assert ag.headline == "BUY on 1 of 1 vote; 1 lens did not apply to this company"


def test_the_count_sentence_and_the_summary_line_use_the_one_wording():
    multi = run_multi_strategy_pipeline(UNIVERSE, [SCREENED, RAW], strategies_dir=STRAT_DIR,
                                        adapter=_Adapter(), today=TODAY)
    line = comparable_names_line(multi)
    assert "2 passed its rules, too few to rank" in line
    assert "kept only" not in line and "too few (under" not in line
    thin = multi.results[SCREENED]
    assert 0 < len([r for r in thin.ranked if not r.excluded]) < MIN_RANKABLE_COHORT
    from aristos_council.pipeline import summary_line
    assert summary_line(thin).startswith("2 passed its rules, too few to rank")
    assert "BUY" not in summary_line(thin) and "HOLD" not in summary_line(thin)
    # and the shortlist did not count that lens's raw BUY
    assert all(SCREENED not in r.buy_lenses and "Classic Value" not in r.buy_lenses
               for r in multi.lens_agreement.rows)


def test_summary_line_of_five_or_more_is_unchanged_and_under_five_says_too_few():
    class R:
        def __init__(self, v): self.verdict, self.excluded = v, False
    assert format_summary_line([R("buy"), R("hold"), R("hold"), R("hold"), R("sell")], universe_size=5,
                               excluded=0) == "1 BUY · 3 HOLD · 1 SELL — 5 of 5 names ranked"
    assert format_summary_line([R("buy"), R("hold"), R("sell")], universe_size=3, excluded=0) \
        == "3 passed its rules, too few to rank"


def test_a_single_lens_that_kept_two_names_narrates_nothing():
    res = run_rank_pipeline(UNIVERSE, SCREENED, ranker_only=True, strategies_dir=STRAT_DIR,
                            adapter=_Adapter(), today=TODAY)
    assert res.meta["ranked_count"] == 2
    assert res.meta.get("shortlist", []) == []
