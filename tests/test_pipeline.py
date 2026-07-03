"""Integrated pipeline (Aristos v2) — ranker is verdict-of-record, council is the
independent second opinion. Deterministic: fake adapter + fake runners, no network.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from aristos_council.agents.schemas import (
    CriticOutput,
    DecisionOutput,
    SpecialistOutput,
)
from aristos_council.data.adapter import (
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
)
from aristos_council.pipeline import (
    agreement_table,
    resolve_council_screen_id,
    run_pipeline,
)
from aristos_council.state import Recommendation, SpecialistName, Stance
from aristos_council.strategy.loader import load_strategy
from aristos_council.strategy.rank_loader import load_rank_strategy

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"
MAGIC = load_rank_strategy(STRAT_DIR / "magic_formula_v1.yaml")     # fundamentals-only
GROWTH = load_strategy(STRAT_DIR / "growth_v1.yaml")               # council substrate

# A clear ranking from fundamentals alone: A best earnings-yield + ROIC, then B, C.
_FUND = {
    "A": dict(market_cap=2e10, sector="Technology", ebit=[3000.0], pe_ratio=10.0,
              operating_income=[3000.0, 2800, 2600, 2400], tax_provision=[600.0, 560, 520, 480],
              pretax_income=[2900.0, 2700, 2500, 2300], invested_capital=[5000.0] * 4,
              total_revenue=[200.0, 170, 150, 120]),
    "B": dict(market_cap=2e10, sector="Technology", ebit=[1500.0], pe_ratio=20.0,
              operating_income=[1500.0, 1450, 1400, 1350], tax_provision=[300.0, 290, 280, 270],
              pretax_income=[1450.0, 1400, 1350, 1300], invested_capital=[5000.0] * 4,
              total_revenue=[150.0, 140, 130, 120]),
    "C": dict(market_cap=2e10, sector="Technology", ebit=[500.0], pe_ratio=40.0,
              operating_income=[500.0, 490, 480, 470], tax_provision=[100.0, 98, 96, 94],
              pretax_income=[480.0, 470, 460, 450], invested_capital=[5000.0] * 4,
              total_revenue=[125.0, 120, 115, 110]),
}


class _Adapter(MarketDataAdapter):
    name = "fake"

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, name=ticker, **_FUND[ticker])

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=10)
            for i in range(220)])

    def get_dividend_history(self, ticker, *, start, end):
        return []


class _SpecialistRunner:
    """Role-aware (reads the system prompt): RISK dissents, SENTIMENT abstains, the
    rest agree. Counts invocations."""

    def __init__(self):
        self.calls = 0

    def invoke(self, system, user):
        self.calls += 1
        if "RISK specialist" in system:
            return SpecialistOutput(stance=Stance.BEARISH, confidence=0.7,
                                    thesis="forward risk", agrees_with_ranker=False,
                                    dissent_note="patent-cliff headline not yet in price")
        if "SENTIMENT specialist" in system:
            # abstains, but (wrongly) returns agrees True -> node must force it to None
            return SpecialistOutput(stance=Stance.ABSTAIN, confidence=0.0,
                                    thesis="no sentiment data", agrees_with_ranker=True,
                                    dissent_note="should be nulled")
        return SpecialistOutput(stance=Stance.BULLISH, confidence=0.8, thesis="up",
                                agrees_with_ranker=True)


class _CountingDecisionRunner:
    def __init__(self, out):
        self._out = out
        self.calls = 0

    def invoke(self, system, user):
        self.calls += 1
        self.last_system = system
        return self._out


def _runners(decision_out):
    return {"specialist": _SpecialistRunner(),
            "critic": _CountingDecisionRunner(CriticOutput(counter_thesis="c")),
            "decision": _CountingDecisionRunner(decision_out)}


# The council-WIRING tests isolate from the (new) growth screen-as-prefilter, which
# would exclude the fixtures on GROWTH's floors — orthogonal to what they test.
MAGIC_NO_PREFILTER = MAGIC.model_copy(update={"prefilter_screen": False})


def _run(decision_out, *, council_mode=None, council_runs_on=None):
    runners = _runners(decision_out)
    result = run_pipeline(
        universe=["A", "B", "C"], rank_strategy=MAGIC_NO_PREFILTER,
        screen_strategy=GROWTH, adapter=_Adapter(), runners=runners,
        today=date(2026, 6, 30),
        council_mode=council_mode, council_runs_on=council_runs_on)
    return result, runners


# --------------------------------------------------------------------------- #
# Stage-1 verdict-of-record + shortlist gating (only shortlisted enter council)
# --------------------------------------------------------------------------- #
def test_only_shortlisted_names_enter_the_council():
    result, runners = _run(DecisionOutput(recommendation=Recommendation.BUY,
                                          confidence=0.8, rationale="r"))
    # 3 names, quintile -> top fifth = 1 BUY (the best-ranked, A).
    assert result.shortlist == ["A"]
    assert result.ranked[0].ticker == "A" and result.ranked[0].verdict == "buy"
    # the council ran on the shortlist ONLY: decision called once, NOT 3x.
    assert runners["decision"].calls == 1
    assert runners["specialist"].calls == 4        # 4 specialists, ONE name


def test_council_runs_on_all_overrides_the_shortlist():
    result, runners = _run(DecisionOutput(recommendation=Recommendation.HOLD,
                                          confidence=0.6, rationale="r"),
                           council_runs_on="all")
    assert set(result.shortlist) == {"A", "B", "C"}
    assert runners["decision"].calls == 3


# --------------------------------------------------------------------------- #
# Option B — independent second opinion, agreement, dissent surfaced
# --------------------------------------------------------------------------- #
def test_option_b_disagreement_surfaces_dissent():
    # ranker BUYs A on factors; council SELLs (a forward risk it can't see) -> DISAGREE.
    # Option B is now behind the flag (narrator is the default), so request it.
    result, _ = _run(DecisionOutput(recommendation=Recommendation.SELL,
                                    confidence=0.7, rationale="forward risk"),
                     council_mode="second_opinion")
    a = result.council[0]
    assert a.ranker_verdict == "buy" and a.council_verdict == "sell"
    assert a.agreement == "DISAGREE"
    assert any("patent-cliff" in d for d in a.dissent_notes)
    assert "DISAGREE" in agreement_table(result)


def test_option_b_agreement_when_council_concurs():
    result, _ = _run(DecisionOutput(recommendation=Recommendation.BUY,
                                    confidence=0.8, rationale="agree"),
                     council_mode="second_opinion")
    assert result.council[0].agreement == "AGREE"


# --------------------------------------------------------------------------- #
# A/B toggle — same node, flag-driven
# --------------------------------------------------------------------------- #
def test_narrator_mode_emits_no_independent_verdict():
    # In narrator mode the council echoes the ranker (BUY) and sets no second opinion.
    result, _ = _run(DecisionOutput(recommendation=Recommendation.SELL,  # would-be call
                                    confidence=0.7, rationale="narrate"),
                     council_mode="narrator")
    a = result.council[0]
    assert a.council_verdict is None            # no independent verdict in narrator
    assert a.agreement is None
    assert a.report.council_mode == "narrator"
    assert a.report.decision.narration_only is True
    assert a.report.decision.recommendation == Recommendation.BUY   # echoes ranker


def test_narrator_is_the_default_mode():
    # The experiment's verdict: default flipped to narrator (verdict = ranker; LLM
    # narrates, no independent second opinion).
    result, _ = _run(DecisionOutput(recommendation=Recommendation.HOLD,
                                    confidence=0.6, rationale="r"))
    assert result.council_mode == "narrator"
    assert result.council[0].report.decision.narration_only is True
    assert result.council[0].council_verdict is None      # no independent verdict


# --------------------------------------------------------------------------- #
# Abstention rule — a data-less specialist never inflates consensus
# --------------------------------------------------------------------------- #
def test_abstaining_specialist_agreement_is_forced_null():
    # agrees_with_ranker only exists in second_opinion mode -> request it.
    result, _ = _run(DecisionOutput(recommendation=Recommendation.BUY,
                                    confidence=0.8, rationale="r"),
                     council_mode="second_opinion")
    rep = result.council[0].report
    sentiment = next(o for o in rep.specialist_opinions
                     if o.specialist.value == "sentiment")
    assert sentiment.stance == Stance.ABSTAIN
    assert sentiment.agrees_with_ranker is None      # forced None despite model True
    # supports/challenges count NON-abstained only; sentiment is in 'abstained'
    sup = rep.specialist_support
    assert sup["abstained"] == 1
    assert sup["challenges"] == 1                    # the RISK specialist
    # the abstainer is NOT counted as a 'support'
    assert sup["supports"] == 2                       # fundamental + technical


# --------------------------------------------------------------------------- #
# matrix-skip — the ranker supersedes the matrix in the pipeline
# --------------------------------------------------------------------------- #
def test_matrix_node_skipped_in_pipeline_but_runs_standalone():
    from aristos_council.graph import build_council
    from aristos_council.state import ResearchState

    runners = _runners(DecisionOutput(recommendation=Recommendation.BUY,
                                      confidence=0.8, rationale="r"))
    # pipeline path: run_matrix=False -> no matrix verdict on the state
    app = build_council(_Adapter(), GROWTH, runners, run_matrix=False)
    st = ResearchState.model_validate(app.invoke(
        ResearchState(ticker="A", strategy_id=GROWTH.id)))
    assert st.matrix_decision is None
    # standalone screen run (back-compat): matrix node runs
    app2 = build_council(_Adapter(), GROWTH, runners, run_matrix=True)
    st2 = ResearchState.model_validate(app2.invoke(
        ResearchState(ticker="A", strategy_id=GROWTH.id)))
    assert st2.matrix_decision is not None


# --------------------------------------------------------------------------- #
# Lens alignment — the council judges against the rank strategy's philosophy
# --------------------------------------------------------------------------- #
def test_council_screen_derives_from_rank_strategy_unless_overridden():
    cons = load_rank_strategy(STRAT_DIR / "conservative_plus_v1.yaml")
    # derived (no explicit screen) -> the strategy's same-philosophy lens
    assert resolve_council_screen_id(cons) == "conservative_screen_v1"
    assert resolve_council_screen_id(MAGIC) == "magic_value_screen_v1"
    # explicit --screen-strategy still overrides
    assert resolve_council_screen_id(cons, "growth_v1") == "growth_v1"


def test_pipeline_runs_council_on_the_paired_screen_not_growth():
    # magic_formula's paired lens is the QUALITY-VALUE screen, not GARP growth_v1.
    screen = load_strategy(STRAT_DIR / f"{resolve_council_screen_id(MAGIC)}.yaml")
    runners = _runners(DecisionOutput(recommendation=Recommendation.BUY,
                                      confidence=0.8, rationale="r"))
    result = run_pipeline(universe=["A", "B", "C"], rank_strategy=MAGIC,
                          screen_strategy=screen, adapter=_Adapter(),
                          runners=runners, today=date(2026, 6, 30))
    assert result.council[0].report.strategy_id == "magic_value_screen_v1"


def test_conservative_screen_passes_sound_defensive_fails_thin_coverage():
    from aristos_council.tools.criteria.registry import Evidence, run_screen
    screen = load_strategy(STRAT_DIR / "conservative_screen_v1.yaml")
    # crucially NO growth criteria that would auto-fail a defensive name
    names = [c.name for c in screen.criteria]
    assert "min_revenue_cagr" not in names and "max_peg_ratio" not in names

    sound = Fundamentals(ticker="JNJ", market_cap=1e10, dividend_per_share=2.0,
                         payout_ratio=0.6)
    by = {c.name: c for c in run_screen(
        screen.criteria,
        Evidence(fundamentals=sound, last_close=100.0, return_12m=0.05),
        ticker="JNJ").criteria}
    assert by["min_dividend_yield"].passed is True       # 2/100 = 2% >= 1.5%
    assert by["max_payout_ratio"].passed is True
    assert by["min_price_momentum"].passed is True       # not in a downtrend

    thin = Fundamentals(ticker="XYZ", market_cap=1e10, dividend_per_share=2.0,
                        payout_ratio=0.95)               # uncovered payout
    r2 = {c.name: c for c in run_screen(
        screen.criteria,
        Evidence(fundamentals=thin, last_close=100.0, return_12m=0.05),
        ticker="XYZ").criteria}
    assert r2["max_payout_ratio"].passed is False        # a REAL defensive concern


def test_specialist_prompt_is_strategy_relative_and_garp_free():
    from aristos_council.agents.prompts import specialist_system
    cons = load_strategy(STRAT_DIR / "conservative_screen_v1.yaml")
    tech = specialist_system(SpecialistName.TECHNICAL, cons)
    assert "GARP" not in tech                            # GARP wording removed
    assert cons.name in tech                             # active strategy named
    assert "defensive" in tech.lower()                   # its intent injected
    assert "candidate" in tech                           # strategy-relative question


# --------------------------------------------------------------------------- #
# Payout-coverage exclusion gate — the yield-trap guard the council surfaced
# --------------------------------------------------------------------------- #
def test_is_payout_uncovered_confirmed_only():
    from aristos_council.factors import is_payout_uncovered
    assert is_payout_uncovered(1.31, 0.85) is True       # PFE-shaped uncovered
    assert is_payout_uncovered(0.60, 0.85) is False      # covered
    assert is_payout_uncovered(None, 0.85) is False      # non-dividend -> not dropped
    assert is_payout_uncovered(1.31, None) is False      # no gate -> excludes nothing


def _payout_rank_stage(payouts: dict, max_payout):
    """Run the pipeline's rank stage over a fake universe with given payout ratios."""
    from aristos_council.pipeline import _rank_stage

    class _Strat:
        factors = MAGIC.factors
        cut, k, percentile, missing = "quintile", 6, 0.2, "worst"
        min_market_cap = None
        exclude_sectors: list = []
        max_payout_ratio = max_payout

    class _A(MarketDataAdapter):
        name = "fake"
        def get_fundamentals(self, ticker):
            return Fundamentals(ticker=ticker, market_cap=2e10, sector="Technology",
                                payout_ratio=payouts[ticker], ebit=[1000.0],
                                operating_income=[1000.0] * 4, tax_provision=[200.0] * 4,
                                pretax_income=[950.0] * 4, invested_capital=[5000.0] * 4)
        def get_price_history(self, ticker, *, start, end):
            return PriceHistory(ticker=ticker, bars=[])
        def get_dividend_history(self, ticker, *, start, end):
            return []

    return _rank_stage(list(payouts), _Strat(), _A(), today=date(2026, 6, 30))


def test_uncovered_payout_name_is_excluded_from_ranking():
    ranked, excluded = _payout_rank_stage(
        {"PFE": 1.31, "JNJ": 0.60, "KO": 0.70}, max_payout=0.85)
    tickers = [t for t, _ in [(r.ticker, r) for r in ranked]]
    assert "PFE" not in tickers                           # excluded, never ranked
    assert "JNJ" in tickers and "KO" in tickers           # covered -> ranked
    assert any(t == "PFE" and "payout uncovered" in reason
               for t, reason in excluded)


def test_non_dividend_and_no_gate_are_not_excluded_for_payout():
    # payout None (non-dividend growth name) with a gate -> NOT excluded
    ranked, excluded = _payout_rank_stage(
        {"NVDA": None, "JNJ": 0.60}, max_payout=0.85)
    assert {r.ticker for r in ranked} == {"NVDA", "JNJ"}
    assert not any("payout" in reason for _, reason in excluded)
    # no gate at all -> an uncovered name ranks normally (magic_formula unaffected)
    ranked2, excluded2 = _payout_rank_stage({"PFE": 1.31, "JNJ": 0.60}, max_payout=None)
    assert {r.ticker for r in ranked2} == {"PFE", "JNJ"}
    assert not any("payout" in reason for _, reason in excluded2)


# --------------------------------------------------------------------------- #
# Screen-as-prefilter — rank only names that pass the council screen's floors
# --------------------------------------------------------------------------- #
CONS_SCREEN = load_strategy(STRAT_DIR / "conservative_screen_v1.yaml")


def _fi(ticker, *, last_close=100.0, return_12m=0.05, **fund_kw):
    from aristos_council.factors import FactorInputs
    return FactorInputs(ticker=ticker,
                        fundamentals=Fundamentals(ticker=ticker, **fund_kw),
                        last_close=last_close, return_12m=return_12m)


def test_screen_prefilter_fails_thin_yield_passes_covered_abstains_on_missing():
    from aristos_council.factors import screen_prefilter_fail
    crit = CONS_SCREEN.criteria
    # thin yield (0.4%) < 1.5% floor -> CONFIRMED fail, reason names the criterion
    thin = screen_prefilter_fail(crit, _fi(
        "AAPL", market_cap=1e10, dividend_per_share=0.4, payout_ratio=0.5))
    assert thin is not None and "min_dividend_yield" in thin
    # covered defensive (2% yield, 60% payout) -> passes everything
    assert screen_prefilter_fail(crit, _fi(
        "JNJ", market_cap=1e10, dividend_per_share=2.0, payout_ratio=0.6)) is None
    # MISSING dps (data gap) -> min_yield ABSTAINS (passed None) -> NOT excluded
    assert screen_prefilter_fail(crit, _fi(
        "X", market_cap=1e10, dividend_per_share=None, payout_ratio=None)) is None
    # genuine NON-payer (dps 0) -> FAILS the income requirement (intended here)
    nonpayer = screen_prefilter_fail(crit, _fi(
        "NVDA", market_cap=1e10, dividend_per_share=0.0))
    assert nonpayer is not None and "min_dividend_yield" in nonpayer


def test_rank_stage_prefilter_excludes_failing_names_pre_rank():
    from aristos_council.pipeline import _rank_stage
    cons = load_rank_strategy(STRAT_DIR / "conservative_plus_v1.yaml")

    class _A(MarketDataAdapter):
        name = "fake"
        _F = {"AAPL": dict(market_cap=1e10, dividend_per_share=0.4, payout_ratio=0.5),
              "JNJ": dict(market_cap=1e10, dividend_per_share=4.0, payout_ratio=0.6)}
        def get_fundamentals(self, t):
            return Fundamentals(ticker=t, **self._F[t])
        def get_price_history(self, t, *, start, end):
            return PriceHistory(ticker=t, bars=[
                PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                         close=100 + 0.05 * i, adj_close=100 + 0.05 * i, volume=10)
                for i in range(260)])
        def get_dividend_history(self, t, *, start, end):
            return []

    ranked, excluded = _rank_stage(["AAPL", "JNJ"], cons, _A(),
                                   today=date(2026, 6, 30),
                                   prefilter_criteria=CONS_SCREEN.criteria)
    assert {r.ticker for r in ranked} == {"JNJ"}         # AAPL prefiltered out
    assert any(t == "AAPL" and "min_dividend_yield" in reason
               for t, reason in excluded)


def test_prefilter_is_one_definition_no_duplicated_threshold():
    cons = load_rank_strategy(STRAT_DIR / "conservative_plus_v1.yaml")
    assert cons.prefilter_screen is True
    # the standalone payout gate is DROPPED — the screen is the single source now
    assert cons.max_payout_ratio is None
    payout = next(c for c in CONS_SCREEN.criteria if c.name == "max_payout_ratio")
    assert payout.threshold == 0.85                      # the ONE coverage threshold


def test_growth_strategies_prefilter_on_the_quality_value_screen():
    # The BMY fix: growth rank strategies now prefilter on their absolute-floor lens
    # (magic_value_screen_v1), so a name failing min_roic is excluded pre-rank.
    for sid in ("magic_formula_v1", "magic_formula_momentum_v1"):
        s = load_rank_strategy(STRAT_DIR / f"{sid}.yaml")
        assert s.prefilter_screen is True
        assert s.council_screen_strategy == "magic_value_screen_v1"


def test_bmy_class_low_roic_name_excluded_pre_rank_by_growth_prefilter():
    from aristos_council.factors import screen_prefilter_fail, FactorInputs
    lens = load_strategy(STRAT_DIR / "magic_value_screen_v1.yaml")
    # BMY-shape: ROIC 10.6% < the lens's own 12% floor -> excluded, reason named
    bmy = FactorInputs(ticker="BMY", fundamentals=Fundamentals(
        ticker="BMY", market_cap=1e11, operating_income=[10.6] * 4,
        tax_provision=[0.0] * 4, pretax_income=[10.6] * 4, invested_capital=[100.0] * 4))
    reason = screen_prefilter_fail(lens.criteria, bmy)
    assert reason is not None and "min_roic" in reason


# --------------------------------------------------------------------------- #
# Narrator default + UNRATEABLE guard (closing batch)
# --------------------------------------------------------------------------- #
def test_narrator_mode_specialists_do_not_emit_agreement():
    from aristos_council.agents.prompts import specialist_system
    from aristos_council.state import SpecialistName as _SN
    narrator = specialist_system(_SN.RISK, GROWTH, "narrator")
    second = specialist_system(_SN.RISK, GROWTH, "second_opinion")
    assert "agrees_with_ranker" not in narrator      # no agreement question in narrator
    assert "agrees_with_ranker" in second            # ...but present in second_opinion


def test_unrateable_delisted_name_is_not_ranked_on_any_path():
    from aristos_council.factors import is_unrateable, FactorInputs
    from aristos_council.pipeline import _rank_stage

    # PARA/WBA-shape: no fundamentals AND no price history (all fetches 404)
    assert is_unrateable(FactorInputs(ticker="PARA")) is True
    assert is_unrateable(FactorInputs(ticker="X", last_close=100.0)) is False  # has price

    class _A(MarketDataAdapter):
        name = "fake"
        def get_fundamentals(self, t):
            from aristos_council.data.adapter import DataUnavailable
            if t in ("PARA", "WBA"):
                raise DataUnavailable("404 delisted")
            return Fundamentals(ticker=t, market_cap=2e10, sector="Technology",
                                ebit=[1000.0], operating_income=[1000.0] * 4,
                                tax_provision=[200.0] * 4, pretax_income=[950.0] * 4,
                                invested_capital=[5000.0] * 4)
        def get_price_history(self, t, *, start, end):
            from aristos_council.data.adapter import DataUnavailable, PriceHistory
            if t in ("PARA", "WBA"):
                raise DataUnavailable("404 delisted")
            return PriceHistory(ticker=t, bars=[])
        def get_dividend_history(self, t, *, start, end):
            return []

    ranked, excluded = _rank_stage(["GOOD", "PARA", "WBA"], MAGIC_NO_PREFILTER, _A(),
                                   today=date(2026, 6, 30))
    assert {r.ticker for r in ranked} == {"GOOD"}        # delisted names NOT ranked
    for name in ("PARA", "WBA"):
        assert any(t == name and "UNRATEABLE" in reason for t, reason in excluded)
