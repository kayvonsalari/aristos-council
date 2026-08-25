"""NARR-UNION-1 — narrate the UNION of every lens's BUYs, ONE section per NAME.

Five lenses over forty names produced 18 BUY verdicts across only 13 DISTINCT names on
the 2026-08-24 run. The obvious implementation — loop the lenses, narrate each lens's
BUYs — would have billed 18 narrations for 13 names and printed a name three lenses
bought three times. So the two guards that carry this module are:

  * the narrated set is the UNION, deduplicated (section 1);
  * the number of LLM invocations equals the number of DISTINCT names (section 2).

The third is doctrinal. Handing one writer several lenses' verdicts makes a failure mode
available that never existed on a single-lens run: reconciling them into a net view. The
narrator ATTRIBUTES; it never ADJUDICATES. Disagreement between lenses is reported as a
fact and left standing (section 4).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from aristos_council import pipeline
from aristos_council.agents.schemas import CriticOutput, DecisionOutput, SpecialistOutput
from aristos_council.data.adapter import (
    Fundamentals, MarketDataAdapter, PriceBar, PriceHistory)
from aristos_council.narration_check import check_cross_lens
from aristos_council.pipeline import (
    buying_lenses,
    cross_lens_reasons,
    cross_lens_verdicts,
    narrated_union,
    run_multi_strategy_pipeline,
)
from aristos_council.state import Recommendation, Stance
from aristos_council.tools.criteria.registry import consumed_fields_for_strategies

from tests.test_multi_strategy_run import (  # the established multi-lens fixture
    RAW,
    SCREENED,
    STRAT_DIR,
    TODAY,
    UNIVERSE,
    _Adapter,
)

MOMENTUM = "magic_formula_momentum_v1"


# --------------------------------------------------------------------------- #
# A counting narrator: every invocation is recorded, so the cost guard is exact.
# --------------------------------------------------------------------------- #
class _CountingRunners(dict):
    """Fake runners that COUNT decision (narration) invocations per ticker."""

    def __init__(self):
        self.decisions: list[str] = []
        self.rationale = "Placeholder narration."
        outer = self

        class _Spec:
            def invoke(self, system, user):
                return SpecialistOutput(stance=Stance.BULLISH, confidence=0.7,
                                        thesis="up")

        class _Critic:
            def invoke(self, system, user):
                return CriticOutput(counter_thesis="c")

        class _Decision:
            def invoke(self, system, user):
                # the ticker is in the user prompt; record ONE call per invocation
                outer.decisions.append(user)
                return DecisionOutput(recommendation=Recommendation.BUY, confidence=0.7,
                                      rationale=outer.rationale)

        super().__init__(specialist=_Spec(), critic=_Critic(), decision=_Decision())

    @property
    def call_count(self) -> int:
        return len(self.decisions)


def _narrated(ids, *, coverage="buys_only", runners=None):
    runners = runners or _CountingRunners()
    result = run_multi_strategy_pipeline(
        UNIVERSE, ids, strategies_dir=STRAT_DIR, adapter=_Adapter(), today=TODAY,
        ranker_only=False, narrate_coverage=coverage, runners=runners)
    return result, runners


def _ranked(ids):
    return run_multi_strategy_pipeline(UNIVERSE, ids, strategies_dir=STRAT_DIR,
                                       adapter=_Adapter(), today=TODAY)


# --------------------------------------------------------------------------- #
# 1. THE UNION — each name once
# --------------------------------------------------------------------------- #
def test_the_narrated_set_is_the_union_and_a_shared_name_appears_once():
    """Lens A buys {X,Y}, lens B buys {Y,Z} -> narrate exactly {X,Y,Z}, and Y ONCE."""
    result = _ranked([SCREENED, RAW])
    buys = {sid: {r.ticker for r in result.results[sid].ranked if r.verdict == "buy"}
            for sid in (SCREENED, RAW)}
    union = narrated_union(result)

    assert set(union) == buys[SCREENED] | buys[RAW]
    assert len(union) == len(set(union))                     # no name twice
    shared = buys[SCREENED] & buys[RAW]
    for ticker in shared:
        assert union.count(ticker) == 1
    # the union is genuinely smaller than the verdict count when lenses agree
    assert len(union) <= sum(len(v) for v in buys.values())


def test_the_union_is_in_the_verdict_tables_own_order():
    result = _ranked([SCREENED, RAW, MOMENTUM])
    union = narrated_union(result)
    order = [row.ticker for row in result.rows]
    assert union == [t for t in order if t in set(union)]


def test_coverage_all_narrates_every_name_ranked_by_any_lens():
    result = _ranked([SCREENED, RAW])
    union = narrated_union(result, "all")
    ranked_anywhere = {r.ticker for sid in result.strategy_ids
                       for r in result.results[sid].ranked}
    assert set(union) == ranked_anywhere
    assert len(union) >= len(narrated_union(result, "buys_only"))


def test_a_name_no_lens_bought_is_not_narrated():
    result = _ranked([SCREENED, RAW])
    union = set(narrated_union(result))
    for row in result.rows:
        bought = any(c.status == "ranked" and c.verdict == "buy"
                     for c in row.cells.values())
        assert (row.ticker in union) == bought


# --------------------------------------------------------------------------- #
# 2. THE COST GUARD — one call per NAME, never per name-and-lens
# --------------------------------------------------------------------------- #
def test_exactly_one_narration_call_per_distinct_name():
    """THE cost guard. Looping lenses would bill one call per BUY VERDICT."""
    result, runners = _narrated([SCREENED, RAW, MOMENTUM])
    union = narrated_union(result)
    verdict_count = sum(1 for row in result.rows for c in row.cells.values()
                        if c.status == "ranked" and c.verdict == "buy")

    assert runners.call_count == len(union)
    assert len(result.narratives) == len(union)
    assert sorted(result.narratives) == sorted(union)
    # ...and the fixture really does have a name more than one lens bought, so the
    # guard is not vacuous.
    assert verdict_count > len(union), (verdict_count, len(union))


def test_a_ranker_only_multi_lens_run_makes_zero_narration_calls():
    runners = _CountingRunners()
    result = run_multi_strategy_pipeline(
        UNIVERSE, [SCREENED, RAW], strategies_dir=STRAT_DIR, adapter=_Adapter(),
        today=TODAY, runners=runners)                     # ranker_only=True is the default
    assert runners.call_count == 0
    assert result.narratives == {} and result.council == []
    assert result.meta["ranker_only"] is True
    assert result.meta["est_cost"] == 0.0


def test_the_cost_estimate_is_the_distinct_name_count_times_the_per_name_cost():
    from aristos_council.reproducibility import estimate_cost
    result, _ = _narrated([SCREENED, RAW, MOMENTUM])
    n = result.meta["narrated_count"]
    assert n == len(narrated_union(result))
    assert result.meta["est_cost"] == estimate_cost(n)


def test_the_narration_stage_is_never_entered_when_the_union_is_empty(monkeypatch):
    """No BUYs -> no council built, no runners constructed, nothing spent."""
    def _boom(*a, **kw):                                   # pragma: no cover
        raise AssertionError("built a council for an empty union")

    monkeypatch.setattr(pipeline, "_multi_narration_stage", _boom)
    result = run_multi_strategy_pipeline(
        ["DEAD"], [SCREENED], strategies_dir=STRAT_DIR, adapter=_Adapter(),
        today=TODAY, ranker_only=False, runners=_CountingRunners())
    assert narrated_union(result) == []
    assert result.narratives == {}


# --------------------------------------------------------------------------- #
# 3. WHAT EACH SECTION CONTAINS — the full row first, then per-lens reasons
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# A DISAGREEING cohort: two lenses whose sector scopes are disjoint, so every name
# is ranked by exactly one of them and excluded by the other. That is the shape the
# verdict row has to render honestly — a BUY beside an exclusion.
# --------------------------------------------------------------------------- #
FINANCIALS = "financials_v1"
DISAGREE_UNIVERSE = ["TECH1", "TECH2", "BANK1", "BANK2"]

_SPLIT_FUND = {
    "TECH1": dict(sector="Technology", ebit=[3000.0]),
    "TECH2": dict(sector="Technology", ebit=[1500.0]),
    "BANK1": dict(sector="Financial Services", ebit=[3000.0]),
    "BANK2": dict(sector="Financial Services", ebit=[1500.0]),
}


class _SplitAdapter(MarketDataAdapter):
    """Two techs and two banks, all rateable by both lenses' FACTORS — only the sector
    scope separates them, so the disagreement is structural rather than accidental."""

    name = "fake-split"

    def get_fundamentals(self, ticker):
        spec = _SPLIT_FUND[ticker]
        ebit = spec["ebit"][0]
        return Fundamentals(
            ticker=ticker, name=ticker, sector=spec["sector"], market_cap=2e10,
            currency="USD", quote_type="EQUITY", ebit=[ebit], pe_ratio=10.0,
            price_to_book=1.2, return_on_equity=0.14,
            operating_income=[ebit, ebit * 0.9, ebit * 0.8, ebit * 0.7],
            tax_provision=[ebit * 0.2] * 4,
            pretax_income=[ebit * 0.95] * 4, invested_capital=[5000.0] * 4,
            total_revenue=[200.0, 170, 150, 120],
            shareholders_equity=[8000.0] * 4, net_income=[ebit * 0.7] * 4,
            total_debt=1000.0, total_cash=500.0)

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=10)
            for i in range(220)])

    def get_dividend_history(self, ticker, *, start, end):
        return []


def _split_run():
    return run_multi_strategy_pipeline(
        DISAGREE_UNIVERSE, [RAW, FINANCIALS], strategies_dir=STRAT_DIR,
        adapter=_SplitAdapter(), today=TODAY)


def test_the_verdict_row_carries_every_lens_including_the_ones_that_did_not_buy():
    """A reader must never be shown a one-sided case."""
    result = _split_run()
    ticker = next(t for t in narrated_union(result)
                  if len(buying_lenses(result, t)) < len(result.strategy_ids))
    row = cross_lens_verdicts(result, ticker)

    assert [r["lens_id"] for r in row] == result.strategy_ids   # EVERY lens, in order
    assert any(r["verdict"] == "buy" for r in row)
    assert any(r["verdict"] != "buy" for r in row)              # ...and the dissenters
    for r in row:
        assert r["cell"]                                        # each states its outcome


def test_a_name_bought_by_one_lens_and_excluded_by_another_shows_both():
    result = _split_run()
    excluded_somewhere = [
        t for t in narrated_union(result)
        if any(c["status"] == "excluded" for c in cross_lens_verdicts(result, t))]
    assert excluded_somewhere, "the fixture must contain such a name"
    row = cross_lens_verdicts(result, excluded_somewhere[0])
    assert any(c["verdict"] == "buy" for c in row)
    assert any(c["status"] == "excluded" and c["cell"].startswith("excluded — ")
               for c in row)


def test_each_buying_lens_gets_its_own_attributed_reasons():
    result = _ranked([SCREENED, RAW, MOMENTUM])
    shared = next((t for t in narrated_union(result)
                   if len(buying_lenses(result, t)) >= 2), None)
    assert shared, "the fixture must contain a name two lenses bought"

    reasons = cross_lens_reasons(result, shared)
    assert [r["lens_id"] for r in reasons] == buying_lenses(result, shared)
    assert len(reasons) >= 2
    for r in reasons:
        assert r["lens"] and r["explain"]                       # named AND explained
        assert shared in r["explain"]
    # a lens that did NOT buy it contributes no reasons — its verdict is in the row above
    for r in reasons:
        assert r["lens_id"] in buying_lenses(result, shared)


def test_the_evidence_block_states_every_lens_before_any_reason():
    from aristos_council.agents.nodes import _cross_lens_block
    from aristos_council.state import ResearchState

    result = _ranked([SCREENED, RAW])
    ticker = narrated_union(result)[0]
    block = _cross_lens_block(ResearchState(
        ticker=ticker, strategy_id="multi_lens_run",
        cross_lens_verdicts=cross_lens_verdicts(result, ticker),
        cross_lens_reasons=cross_lens_reasons(result, ticker)))

    assert "EVERY SELECTED LENS'S VERDICT" in block
    assert block.index("EVERY SELECTED LENS'S VERDICT") < block.index("WHY EACH BUYING")
    for cell in cross_lens_verdicts(result, ticker):
        assert cell["cell"] in block


# --------------------------------------------------------------------------- #
# 4. THE DOCTRINE BOUNDARY — attribute, never adjudicate
# --------------------------------------------------------------------------- #
def test_the_narrator_prompt_forbids_weighing_the_lenses_against_each_other():
    from aristos_council.agents.prompts import CROSS_LENS_CONSTRAINT, decision_system
    from aristos_council.strategy.loader import Strategy

    frame = Strategy.model_construct(id="multi_lens_run", name="3-lens comparison",
                                     version=1, criteria=[], description="", rationale="",
                                     notes="", lens_kind="", lens_factor_labels=[])
    prompt = decision_system(frame, council_mode="narrator")
    assert CROSS_LENS_CONSTRAINT in prompt
    for banned in ("on balance", "the weight of evidence", "which lens is right",
                   "ATTRIBUTE, you do not ADJUDICATE", "LEAVE IT STANDING"):
        assert banned.lower() in prompt.lower()


@pytest.mark.parametrize("sentence", [
    "On balance the lenses favour this name.",
    "Taken together, Classic Value and Magic Formula RAW point the same way.",
    "The weight of evidence across the lenses is positive.",
    "Three of the five strategies rated it BUY, so on balance it is a buy.",
])
def test_the_fact_checker_flags_a_synthesised_cross_lens_judgement(sentence):
    verdicts = [{"lens": "Classic Value"}, {"lens": "Magic Formula RAW"}]
    marks = check_cross_lens(sentence, verdicts)
    assert marks, sentence
    assert "attributes, it does not adjudicate" in marks[0]


@pytest.mark.parametrize("sentence", [
    "Classic Value bought it; Magic Formula RAW excluded it.",
    "Classic Value ranked it #3 of 22 on earnings yield.",
    "Magic Formula RAW excluded it on the utilities sector gate.",
    "Under Classic Value it ranks first on ROIC.",
])
def test_honest_attribution_and_reported_disagreement_pass_untouched(sentence):
    """Reporting a disagreement resolves nothing and must NOT be flagged — the check
    never invents a contradiction."""
    verdicts = [{"lens": "Classic Value"}, {"lens": "Magic Formula RAW"}]
    assert check_cross_lens(sentence, verdicts) == []


def test_a_synthesised_narration_is_annotated_in_the_finished_report():
    runners = _CountingRunners()
    runners.rationale = ("Classic Value ranked it first. On balance the lenses favour "
                         "this name.")
    result, _ = _narrated([SCREENED, RAW], runners=runners)
    assert result.narratives
    stamped = [t for t, text in result.narratives.items()
               if "does not adjudicate" in text]
    assert stamped, result.narratives
    for text in result.narratives.values():
        # the prose itself is never rewritten — the original sentence survives verbatim
        assert "On balance the lenses favour this name" in text


# --------------------------------------------------------------------------- #
# 5. THE EVIDENCE PACKET — the union of the BUYING lenses' consumed fields
# --------------------------------------------------------------------------- #
def test_the_packet_is_the_union_of_the_buying_lenses_consumed_fields():
    result = _ranked([SCREENED, RAW])
    ticker = narrated_union(result)[0]
    strategies = pipeline.narration_evidence_strategies(result, ticker)
    fields = consumed_fields_for_strategies(strategies)

    from aristos_council.tools.criteria.registry import consumed_fundamentals_fields
    expected: set[str] = set()
    for s in strategies:
        expected |= consumed_fundamentals_fields(getattr(s, "criteria", None) or [])
    assert fields == expected

    # ...and it is SCOPED: a lens that did not buy the name contributes nothing.
    non_buyers = [sid for sid in result.strategy_ids
                  if sid not in buying_lenses(result, ticker)]
    for sid in non_buyers:
        screen = getattr(result.results[sid], "screen_strategy", None)
        if screen is None or not getattr(screen, "criteria", None):
            continue
        extra = consumed_fundamentals_fields(screen.criteria) - fields
        assert extra, "a non-buying lens's exclusive fields must NOT be in the packet"


def test_the_scoping_helper_extends_rather_than_bypasses_the_existing_one():
    from aristos_council.strategy.loader import Strategy
    from aristos_council.tools.criteria.registry import consumed_fundamentals_fields

    result = _ranked([SCREENED, RAW])
    screen = result.results[SCREENED].screen_strategy
    assert consumed_fields_for_strategies([screen]) == \
        consumed_fundamentals_fields(screen.criteria)
    assert consumed_fields_for_strategies([]) == set()
    # a screen-LESS strategy contributes nothing — it consumed nothing
    empty = Strategy.model_construct(id="x", name="x", version=1, criteria=[],
                                     description="", rationale="", notes="",
                                     lens_kind="", lens_factor_labels=[])
    assert consumed_fields_for_strategies([empty]) == set()


# --------------------------------------------------------------------------- #
# 6. THE REPORT
# --------------------------------------------------------------------------- #
def test_one_narration_section_per_name_in_both_surfaces():
    from aristos_council.export.report_html import multi_strategy_report_html

    result, _ = _narrated([SCREENED, RAW])
    pytest.importorskip("streamlit")
    import app

    md = app._multi_strategy_markdown(result)
    doc = multi_strategy_report_html(result)
    union = narrated_union(result)

    assert md.count("## Narration") == 1
    assert doc.count("<h2>Narration</h2>") == 1
    for ticker in union:
        display = next(r.display for r in result.rows if r.ticker == ticker)
        assert md.count(f"### {display}") == 1               # ONE section per name
        assert doc.count(f"<summary>{display}</summary>") == 1


def test_the_header_states_how_many_names_were_narrated_and_on_what_basis():
    from aristos_council.export.report_html import multi_strategy_report_html

    result, _ = _narrated([SCREENED, RAW])
    pytest.importorskip("streamlit")
    import app

    basis = "every name rated BUY by at least one lens"
    assert result.meta["narration_basis"] == basis
    for surface in (app._multi_strategy_markdown(result),
                    multi_strategy_report_html(result)):
        assert basis in surface
        assert f"{result.meta['narrated_count']} name" in surface


def test_a_ranker_only_run_renders_no_narration_section():
    from aristos_council.export.report_html import multi_strategy_report_html
    pytest.importorskip("streamlit")
    import app

    result = _ranked([SCREENED, RAW])
    assert "## Narration" not in app._multi_strategy_markdown(result)
    assert "<h2>Narration</h2>" not in multi_strategy_report_html(result)


def test_a_narrated_multi_lens_run_still_writes_exactly_one_md_and_one_html(
        tmp_path, monkeypatch):
    from datetime import datetime, timezone
    pytest.importorskip("streamlit")
    import app

    monkeypatch.setattr(app, "UNIVERSE_RUNS_DIR", tmp_path)
    result, _ = _narrated([SCREENED, RAW])
    md, html = app._persist_multi_strategy_run(
        result, datetime(2026, 8, 24, 11, 49, tzinfo=timezone.utc), "Growth 40")
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([md.name, html.name])


# --------------------------------------------------------------------------- #
# 7. NOTHING ELSE MOVED
# --------------------------------------------------------------------------- #
def test_narration_changes_no_verdict_rank_or_exclusion():
    plain = _ranked([SCREENED, RAW])
    narrated, _ = _narrated([SCREENED, RAW])

    def snap(res):
        return {sid: ([(r.ticker, r.verdict, r.combined_rank)
                       for r in res.results[sid].ranked],
                      list(res.results[sid].excluded))
                for sid in res.strategy_ids}

    assert snap(plain) == snap(narrated)
    assert [r.ticker for r in plain.rows] == [r.ticker for r in narrated.rows]


def test_the_record_layer_stays_per_strategy_when_narrating(tmp_path):
    runs = tmp_path / "runs"
    result, _ = _narrated_with_freeze(runs)
    ids = [result.results[sid].meta["run_id"] for sid in result.strategy_ids]
    assert all(ids) and len(set(ids)) == len(ids)
    assert sorted(p.name for p in runs.iterdir()) == sorted(ids)
    for sid in result.strategy_ids:
        assert result.results[sid].meta["universe_members"] == list(UNIVERSE)


def _narrated_with_freeze(runs):
    runners = _CountingRunners()
    return run_multi_strategy_pipeline(
        UNIVERSE, [SCREENED, RAW], strategies_dir=STRAT_DIR, adapter=_Adapter(),
        today=TODAY, ranker_only=False, runners=runners, freeze_dir=runs), runners


def test_single_lens_narration_is_unchanged():
    """The single-lens path does not go through the union stage at all."""
    from aristos_council.pipeline import run_rank_pipeline
    runners = _CountingRunners()
    res = run_rank_pipeline(UNIVERSE, SCREENED, ranker_only=False,
                            council_mode="narrator", strategies_dir=STRAT_DIR,
                            adapter=_Adapter(), today=TODAY, runners=runners)
    buys = [r.ticker for r in res.ranked if r.verdict == "buy"]
    assert sorted(res.narratives) == sorted(buys)
    assert runners.call_count == len(buys)
    assert res.meta["council_mode"] == "narrator"


# --------------------------------------------------------------------------- #
# 8. THE FACT-CHECKER MUST NOT STAMP A TRUE CROSS-LENS CITATION
#
# Caught on the FIRST live narrated run (2026-08-24): ADBE's section correctly cited
# "Magic Formula RAW ranked it #1 of 6 with a rank-sum of 9" and "Growth ranked it #1 of
# 2 with a rank-sum of 6" — and both were stamped as contradicting the rank table,
# because every sentence was judged against the LEAD lens's 5-name cohort. A cross-lens
# section quotes several tables by design, so each claim is routed to the table of the
# lens it NAMES (the discipline NARR-CHK-FP-2 already applies to peer names).
# --------------------------------------------------------------------------- #
_LEAD_TABLE = {"N": 5, "combined_position": 1, "ticker": "ADBE", "score": 3.0,
               "factors": {"roic": 2.0, "earnings_yield": 1.0},
               "boundary_tie": {}, "peers": {}}
_RAW_TABLE = {"N": 6, "combined_position": 1, "ticker": "ADBE", "score": 9.0,
              "factors": {"roic": 2.0, "earnings_yield": 1.0, "momentum_12m": 6.0},
              "boundary_tie": {}, "peers": {}}
_TABLES = {"Classic Value": _LEAD_TABLE, "Magic Formula RAW": _RAW_TABLE}


def test_a_true_citation_of_another_lenses_table_is_not_stamped():
    """The live false positive, pinned."""
    from aristos_council.narration_check import check_narration, check_narration_by_lens

    claim = ("Magic Formula RAW lens ranked ADBE first of 6 with a combined rank-sum "
             "of 9.")
    # judged against the LEAD lens's cohort it looks wrong — that was the bug...
    assert check_narration(claim, _LEAD_TABLE)
    # ...and routed to the lens it NAMES it is simply true.
    assert check_narration_by_lens(claim, _TABLES, _LEAD_TABLE) == []


def test_routing_changes_only_WHICH_table_is_used_never_the_verdict():
    """The invariant: a sentence naming lens L gets exactly what the base checker would
    say about it against L's own table."""
    from aristos_council.narration_check import check_narration, check_narration_by_lens

    for sentence in (
        "Under Magic Formula RAW, ADBE has the best momentum in the cohort.",
        "Under Magic Formula RAW, ADBE has the worst momentum in the cohort.",
        "Under Classic Value, ADBE has the best earnings yield in the cohort.",
        "Under Classic Value, ADBE has the worst earnings yield in the cohort.",
    ):
        lens = "Magic Formula RAW" if "RAW" in sentence else "Classic Value"
        routed = bool(check_narration_by_lens(sentence, _TABLES, _LEAD_TABLE))
        direct = bool(check_narration(sentence, _TABLES[lens]))
        assert routed == direct, sentence


def test_a_sentence_naming_no_lens_still_checks_against_the_lead():
    from aristos_council.narration_check import check_narration_by_lens
    assert check_narration_by_lens("ADBE has the worst earnings yield in the cohort.",
                                   _TABLES, _LEAD_TABLE)
    assert check_narration_by_lens("ADBE has the best earnings yield in the cohort.",
                                   _TABLES, _LEAD_TABLE) == []


def test_a_sentence_naming_SEVERAL_lenses_is_left_alone():
    """No single table can adjudicate it, and the check never invents a contradiction."""
    from aristos_council.narration_check import check_narration_by_lens
    assert check_narration_by_lens(
        "Classic Value and Magic Formula RAW both rank it best on earnings yield.",
        _TABLES, _LEAD_TABLE) == []


def test_the_multi_lens_stage_routes_the_check_per_lens():
    """End to end through the stage: a narration that cites each lens's OWN cohort
    correctly comes back unstamped."""
    result = _ranked([SCREENED, RAW])
    ticker = narrated_union(result)[0]
    columns = pipeline.multi_strategy_columns(result)
    citations = []
    for sid in result.strategy_ids:
        row = next((x for x in result.results[sid].ranked if x.ticker == ticker), None)
        if row is not None:
            citations.append(f"{columns[sid]} ranked it {row.explain()}")

    runners = _CountingRunners()
    runners.rationale = " ".join(citations)
    narrated, _ = _narrated([SCREENED, RAW], runners=runners)
    assert "narration check" not in narrated.narratives[ticker], \
        narrated.narratives[ticker]


def test_a_lens_named_in_a_HEADING_governs_the_sentences_that_follow_it():
    """A lens-by-lens section names its lens once and then says "This lens". Checking
    those follow-on sentences against the LEAD lens's cohort stamped AAPL's CORRECT
    "#1 of 6 … rank-sum 9" under Magic Formula RAW on the live 2026-08-25 run, because
    the lead was Value + Momentum's five-name cohort."""
    from aristos_council.narration_check import check_narration_by_lens

    vm = {"N": 5, "combined_position": 1, "ticker": "AAPL", "score": 7.0,
          "boundary_tie": {}, "peers": {},
          "factors": {"roic": 1.0, "earnings_yield": 4.0, "momentum_12m": 2.0}}
    raw = {"N": 6, "combined_position": 1, "ticker": "AAPL", "score": 9.0,
           "boundary_tie": {}, "peers": {},
           "factors": {"roic": 1.0, "earnings_yield": 5.0, "momentum_12m": 3.0}}
    tables = {"Value + Momentum": vm, "Magic Formula RAW": raw}

    true_passage = (
        "Magic Formula RAW lens — BUY, #1 of 6 (tied). This lens also placed AAPL first "
        "in its (larger) cohort with a combined rank-sum of 9. It awarded AAPL the best "
        "ROIC in the cohort.")
    assert check_narration_by_lens(true_passage, tables, vm) == []

    # ...and a FALSE follow-on under the same heading is still caught, against that
    # lens's own table.
    false_passage = ("Magic Formula RAW lens. This lens gave AAPL the worst ROIC in "
                     "the cohort.")
    assert check_narration_by_lens(false_passage, tables, vm)


def test_the_carried_lens_is_dropped_when_a_sentence_names_several():
    """Ambiguity resets the context rather than silently attributing to the last one."""
    from aristos_council.narration_check import check_narration_by_lens

    vm = {"N": 5, "combined_position": 1, "ticker": "AAPL", "score": 7.0,
          "boundary_tie": {}, "peers": {}, "factors": {"roic": 1.0}}
    raw = {"N": 6, "combined_position": 1, "ticker": "AAPL", "score": 9.0,
           "boundary_tie": {}, "peers": {}, "factors": {"roic": 1.0}}
    tables = {"Value + Momentum": vm, "Magic Formula RAW": raw}
    prose = ("Magic Formula RAW lens ranked it first. Value + Momentum and Magic "
             "Formula RAW both rank it best on ROIC. It has the best ROIC in the cohort.")
    # the middle sentence names two lenses and is skipped; the last falls back to the
    # LEAD table rather than to a lens it never named.
    assert check_narration_by_lens(prose, tables, vm) == []


# --------------------------------------------------------------------------- #
# 6. THE HEADER MUST NOT LIE ABOUT WHETHER A MODEL RAN
# --------------------------------------------------------------------------- #
# Found in the 2026-08-25 report, AFTER the rest of NARR-UNION-1 was green: the merged
# multi-lens header carried a HARDCODED "No LLM ran — narration stays a per-strategy
# run". That sentence was true only while multi-lens runs were locked to ranker-only.
# Lifting the lock turned it into a false claim printed above three narration sections
# the same run had just paid for — the exact class of dishonest surface the house rules
# exist to prevent. The line is now DERIVED from the result, so it cannot outlive the
# behaviour it describes.
def test_a_narrated_multi_lens_run_never_claims_no_LLM_ran():
    from aristos_council.pipeline import multi_header_line

    result = run_multi_strategy_pipeline(
        UNIVERSE, [SCREENED, RAW], strategies_dir=STRAT_DIR, adapter=_Adapter(),
        today=TODAY)
    # ranker-only: no narratives, so the header says so — and says it in the SAME words
    # the single-lens header has always used.
    assert multi_header_line(result) == pipeline._pipeline_header("ranker-only")
    assert "no LLM ran" in multi_header_line(result)

    narrated = replace(result, narratives={"AAPL": "…", "MSFT": "…"})
    line = multi_header_line(narrated)
    assert "no LLM ran" not in line.lower()
    assert "2 names narrated" in line
    assert "union of every lens's BUYs" in line


def test_the_honest_header_reaches_every_surface_that_prints_it():
    """One derived line, three renderers — the markdown, the HTML and the app caption.
    The bug shipped because the sentence was pasted into each of them separately."""
    pytest.importorskip("streamlit")
    import app

    from aristos_council.export.report_html import multi_strategy_report_html

    result = run_multi_strategy_pipeline(
        UNIVERSE, [SCREENED, RAW], strategies_dir=STRAT_DIR, adapter=_Adapter(),
        today=TODAY)
    narrated = replace(result, narratives={"AAPL": "Adobe is …"})

    md = app._multi_strategy_markdown(narrated, None)
    doc = multi_strategy_report_html(narrated, run_start=None)
    for surface in (md, doc):
        assert "No LLM ran" not in surface
        assert "1 name narrated" in surface
