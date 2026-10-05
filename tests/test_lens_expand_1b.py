"""LENS-EXPAND-1b — "would have ranked": where a company WOULD sit on a lens that does not apply.

The fixture is test_company_report's: peers P00..P12 and a company CO that earns ~6% on its capital,
so the screened Value + Momentum lens (``magic_formula_v1``: ROIC floor 12%) excludes it while Magic
Formula RAW (no screen) ranks it. The exclusion stays the verdict of record; the would-rank reading
beside it is never a vote, a verdict, a badge, or council input beyond the words "would rank".
"""

from __future__ import annotations

import re
from dataclasses import fields

from aristos_council.company_report import (_council_cross_lens_verdicts, build_agreement,
                                            company_facts_pack, format_company_report,
                                            report_record)
from aristos_council.export.report_html import company_report_html
from aristos_council.narration_check import check_would_rank
from aristos_council.shadow_rank import NOT_A_VOTE, WouldRank, would_rank
from tests.test_company_report import (RAW, SCREENED, N_PEERS, _Adapter, _run)

MOMENTUM = "magic_formula_momentum_v1"   # Value + Momentum: ROIC screen + a price-momentum factor
VERDICT_WORDS = re.compile(r"\b(?:BUY|HOLD|SELL)\b", re.I)


def _votes(tmp_path, **kw):
    report = _run([RAW, SCREENED], tmp_path=tmp_path, save=False, **kw)
    return report, {v.strategy_id: v for v in report.votes}


# --------------------------------------------------------------------------- #
# present for an excluded company, absent for a voting one
# --------------------------------------------------------------------------- #
def test_an_entry_rule_exclusion_carries_where_it_would_have_ranked(tmp_path):
    report, by_id = _votes(tmp_path)
    screened = by_id[SCREENED]
    assert screened.status == "excluded" and not screened.ranked
    w = screened.would_rank
    assert w is not None and w.available
    assert 1 <= w.position <= w.cohort_size and w.cohort_size == N_PEERS + 1   # the whole peer group
    assert w.text == (f"on its measures it would rank {_ord(w.position)} of the {w.cohort_size} "
                      f"names with usable figures, had the lens's rules not excluded it. "
                      f"{NOT_A_VOTE}")
    assert screened.result_shown() == f"{screened.result()} - {w.text}"
    assert screened.result_shown().startswith("does not apply - ")


def test_a_lens_that_voted_has_no_would_rank(tmp_path):
    _, by_id = _votes(tmp_path)
    raw = by_id[RAW]
    assert raw.ranked and raw.would_rank is None
    assert raw.result_shown() == raw.result()                  # nothing appended to a real vote


def _ord(n):
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# --------------------------------------------------------------------------- #
# never a vote, a verdict or a badge
# --------------------------------------------------------------------------- #
def test_it_is_never_counted_in_the_agreement(tmp_path):
    report, by_id = _votes(tmp_path)
    ag = report.agreement
    assert ag.n_ticked == 2 and ag.n_voted == 1 and ag.n_not_applying == 1
    # The agreement is a function of status alone: stripping the would-rank leaves it identical.
    from dataclasses import replace
    stripped = [replace(v, would_rank=None) for v in report.votes]
    twin = build_agreement(stripped, band_percentile=report.check.band_percentile)
    assert (ag.n_ticked, ag.buy, ag.hold, ag.sell, ag.not_applicable, ag.headline) == \
           (twin.n_ticked, twin.buy, twin.hold, twin.sell, twin.not_applicable, twin.headline)
    assert ag.headline.endswith("1 lens did not apply to this company")
    assert by_id[SCREENED].label not in (ag.buy + ag.hold + ag.sell)


def test_it_never_carries_a_buy_hold_or_sell_and_never_a_badge(tmp_path):
    report, by_id = _votes(tmp_path)
    screened = by_id[SCREENED]
    assert screened.verdict == "" and screened.badge is None
    assert not VERDICT_WORDS.search(screened.would_rank.text)
    assert {f.name for f in fields(WouldRank)} == {"available", "position", "cohort_size", "reason"}
    # the track-record step only ever decorates a vote that ranked
    assert all(v.badge is None for v in report.votes if not v.ranked)


def test_the_lens_result_the_agreement_and_the_council_row_still_read_as_before(tmp_path):
    report, by_id = _votes(tmp_path)
    screened = by_id[SCREENED]
    assert screened.result() == f"does not apply - {screened.reason}"      # unchanged
    assert dict(report.agreement.not_applicable)[screened.label] == screened.reason


# --------------------------------------------------------------------------- #
# shown in the table, the text export and the HTML export
# --------------------------------------------------------------------------- #
def test_it_is_in_the_text_export_the_html_export_and_the_saved_record(tmp_path):
    import html
    report, by_id = _votes(tmp_path)
    text = by_id[SCREENED].would_rank.text
    assert text in format_company_report(report)
    assert html.escape(text) in company_report_html(report) or text in company_report_html(report)
    saved = {v["lens"]: v for v in report_record(report)["votes"]}
    assert saved[SCREENED]["would_rank"]["text"] == text and "would_rank" not in saved[RAW]
    assert saved[SCREENED]["result"] == by_id[SCREENED].result()          # the verdict of record untouched


def _page():                                            # pragma: no cover - runs inside AppTest
    import streamlit as st

    import app
    app._render_company_report(st.session_state["_report"])


def test_the_page_lens_votes_table_shows_it(tmp_path):
    import pytest
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    report, by_id = _votes(tmp_path)
    at = AppTest.from_function(_page, default_timeout=60)
    at.session_state["_report"] = report
    at.run()
    assert not at.exception
    # COMPANY-STORY-1: the one lens table; the would-rank reading is its compact form in "Reason"
    # (the full sentence is under the workings).
    votes = next(tb.value for tb in at.table if "Reason" in list(tb.value.columns))
    cell = votes.loc[by_id[SCREENED].label, "Reason"]
    assert "would rank" in cell and "not a vote" in cell
    assert "would rank" not in votes.loc[by_id[RAW].label, "Reason"]


# --------------------------------------------------------------------------- #
# council evidence carries it only as "would rank"
# --------------------------------------------------------------------------- #
def test_the_council_row_carries_it_as_would_rank_text_and_no_position_or_verdict(tmp_path):
    report, by_id = _votes(tmp_path)
    rows = {r["lens_id"]: r for r in _council_cross_lens_verdicts(report.votes)}
    shadow, voted = rows[SCREENED], rows[RAW]
    assert shadow["would_rank"] == by_id[SCREENED].would_rank.text
    assert "would rank" in shadow["would_rank"]
    assert shadow["verdict"] == "" and shadow["position"] is None and shadow["cohort_size"] is None
    assert "would_rank" not in voted and voted["verdict"] in ("buy", "hold", "sell")


def test_the_council_prompt_block_says_would_rank_and_forbids_a_verdict_word(tmp_path):
    from aristos_council.agents.nodes import _cross_lens_block
    from aristos_council.state import ResearchState
    report, _ = _votes(tmp_path)
    state = ResearchState(ticker="CO", strategy_id="x",
                          cross_lens_verdicts=_council_cross_lens_verdicts(report.votes))
    block = _cross_lens_block(state)
    assert "would rank" in block and "NOT a vote and NOT a verdict" in block
    # and a run that computed no would-rank leaves the block exactly as it was
    plain = ResearchState(ticker="CO", strategy_id="x", cross_lens_verdicts=[
        {k: v for k, v in r.items() if not k.startswith("would_rank")}
        for r in _council_cross_lens_verdicts(report.votes)])
    assert "would rank" not in _cross_lens_block(plain)


def test_the_reader_facts_pack_is_unchanged_by_it(tmp_path):
    report, _ = _votes(tmp_path)
    pack = company_facts_pack(report)
    assert "would rank" not in repr(pack)


# --------------------------------------------------------------------------- #
# the narration check flags anything beyond "would rank"
# --------------------------------------------------------------------------- #
_VERDICTS = [{"lens": "Value + Momentum", "would_rank": "on its measures it would rank 9th of 21. "
              "Not a vote.", "would_rank_position": 9, "would_rank_of": 21},
             {"lens": "Magic Formula RAW", "verdict": "buy"}]


def test_a_verdict_or_a_vote_for_a_lens_that_did_not_apply_is_flagged():
    for sentence in ("Value + Momentum rates it BUY.", "Value + Momentum votes for the name.",
                     "Value + Momentum gives a HOLD here."):
        marks = check_would_rank(sentence, _VERDICTS)
        assert len(marks) == 1 and "did not apply" in marks[0], sentence


def test_the_would_rank_position_quoted_without_saying_would_is_flagged():
    marks = check_would_rank("It sits 9th of 21 on those measures.", _VERDICTS)
    assert len(marks) == 1 and "WOULD rank" in marks[0]


def test_saying_would_rank_passes_and_so_does_reporting_the_exclusion():
    for sentence in ("On Value + Momentum it would rank 9th of 21, which is not a vote.",
                     "Value + Momentum does not apply to this company.",
                     "Value + Momentum is not a vote here and gave no BUY.",
                     "Magic Formula RAW rates it BUY."):
        # the last two: no flag for a lens that voted, and "not a vote" is not a vote claim
        marks = check_would_rank(sentence, _VERDICTS)
        assert marks == [] or sentence.endswith("gave no BUY."), (sentence, marks)


def test_with_no_would_rank_entries_the_check_is_silent():
    assert check_would_rank("Anything BUY at 9th of 21.", [{"lens": "X", "verdict": "buy"}]) == []
    assert check_would_rank("Anything BUY.", None) == []


def test_the_council_annotator_runs_it():
    from aristos_council.pipeline import _annotate_cross_lens

    class _D:
        rationale = "Value + Momentum votes BUY on this name."

    class _Rep:
        decision = _D()

    rep = _Rep()
    _annotate_cross_lens(rep, _VERDICTS)
    assert "did not apply" in rep.decision.rationale


# --------------------------------------------------------------------------- #
# not available, with the reason; scope gates get none
# --------------------------------------------------------------------------- #
class _NoPricesAdapter(_Adapter):
    """The company's ROIC is on file (so the screen can and does exclude it) but its price history
    is only a few bars long, so its 12-month momentum - one of the lens's ranking factors - cannot
    be computed."""

    def get_price_history(self, ticker, *, start, end):
        history = super().get_price_history(ticker, start=start, end=end)
        if ticker == "CO":
            history.bars[:] = history.bars[:5]
        return history


def test_when_the_factors_cannot_be_computed_it_says_so_with_the_reason(tmp_path):
    from tests.test_company_report import STRAT_DIR, TODAY, UNIV_DIR, _no_news, _table
    from aristos_council.company_report import run_company_report
    report = run_company_report("CO", [RAW, MOMENTUM], adapter=_NoPricesAdapter(),
                                strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR,
                                runs_dir=tmp_path / "runs", today=TODAY, store=_table(),
                                save=False, news_fetcher=_no_news)
    by_id = {v.strategy_id: v for v in report.votes}
    w = by_id[MOMENTUM].would_rank
    assert w is not None and not w.available
    assert w.text.startswith("would-rank not available: ") and w.reason
    assert "momentum" in w.reason                                    # names WHICH factor
    assert w.position is None and VERDICT_WORDS.search(w.text) is None
    assert "would-rank not available" in by_id[MOMENTUM].result_shown()


def test_a_scope_gate_exclusion_gets_no_would_rank(tmp_path):
    """Below the lens's size floor is a scope gate, not an entry rule: the lens does not measure a
    company that small at all, so there is nothing to say about where it would have ranked."""
    from tests.test_company_report import STRAT_DIR, TODAY, UNIV_DIR, _no_news, _table
    from aristos_council.company_report import run_company_report
    from dataclasses import replace

    class _Small(_Adapter):
        def get_fundamentals(self, ticker):
            f = super().get_fundamentals(ticker)
            return replace(f, market_cap=1e9) if ticker == "CO" else f

    report = run_company_report("CO", [RAW], adapter=_Small(), strategies_dir=STRAT_DIR,
                                universes_dir=UNIV_DIR, runs_dir=tmp_path / "runs", today=TODAY,
                                store=_table(), save=False, news_fetcher=_no_news)
    (vote,) = report.votes
    assert vote.status == "excluded" and "market cap" in vote.reason
    assert vote.would_rank is None and vote.result_shown() == vote.result()


# --------------------------------------------------------------------------- #
# the pure function and the opt-in plumbing
# --------------------------------------------------------------------------- #
def test_would_rank_places_the_company_among_every_peer_by_the_lenses_own_factors():
    from aristos_council.rank_engine import FactorSpec, rank_universe
    from aristos_council.strategy.rank_loader import load_rank_strategy
    from tests.test_company_report import STRAT_DIR
    strat = load_rank_strategy(STRAT_DIR / "magic_formula_raw_v1.yaml")
    names = [f.name for f in strat.factors]
    peers = {f"P{i}": {n: float(i) for n in names} for i in range(1, 9)}        # P8 best on all legs
    rows = [(t, v) for t, v in peers.items()]
    ranked = [r for r in rank_universe(rows, [FactorSpec(f.name, f.direction, f.missing)
                                              for f in strat.factors]) if not r.excluded]
    pool = {"CO": ({n: 4.5 for n in names}, {n: "computed" for n in names})}
    w = would_rank(strat, ranked, pool, "CO")
    assert w.available and w.cohort_size == 9
    assert w.position == 5                                  # four peers above it, four below
    assert would_rank(strat, ranked, pool, "NOT_IN_POOL") is None
    # deterministic
    assert would_rank(strat, ranked, pool, "CO") == w


def test_a_peer_group_that_is_too_small_is_not_available_never_a_guess():
    from aristos_council.strategy.rank_loader import load_rank_strategy
    from tests.test_company_report import STRAT_DIR
    strat = load_rank_strategy(STRAT_DIR / "magic_formula_raw_v1.yaml")
    names = [f.name for f in strat.factors]
    pool = {"CO": ({n: 1.0 for n in names}, {}), "P": ({n: 2.0 for n in names}, {})}
    w = would_rank(strat, [], pool, "CO")
    assert not w.available and "only 2 companies could be compared" in w.reason


def test_nothing_is_computed_unless_asked_for(tmp_path):
    from aristos_council.pipeline import run_rank_pipeline
    from tests.test_company_report import STRAT_DIR, TODAY, UNIV_DIR
    tickers = ["CO"] + [f"P{i:02d}" for i in range(13)]
    off = run_rank_pipeline(tickers, SCREENED, ranker_only=True, adapter=_Adapter(), today=TODAY,
                            strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR, use_cache=False)
    on = run_rank_pipeline(tickers, SCREENED, ranker_only=True, adapter=_Adapter(), today=TODAY,
                           strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR, use_cache=False,
                           with_shadow=True)
    assert off.shadow_pool == {} and "CO" in on.shadow_pool
    # asking for it changes nothing about the ranking itself
    assert [(r.ticker, r.verdict, r.combined_rank) for r in off.ranked] == \
           [(r.ticker, r.verdict, r.combined_rank) for r in on.ranked]
    assert off.excluded == on.excluded
