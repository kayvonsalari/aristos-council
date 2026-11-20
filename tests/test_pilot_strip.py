"""PILOT-STRIP-1 - pilot mode (Part A), the report wording (Part B) and the Coca-Cola page's errors (Part C).

Anchored to the live page of 2026-11-20 16:34 (KO): a summary that said "no backtest of this method exists yet"
above a backtest section; "dividend cover of 1.3 times sits in the range sources call thin to comfortable" when the
range is 1.5 to 2.0; a trailing P/E of 28.7 beside $96.09 / $3.39 = 28.3; a size from the 2026-10-15 snapshot read
as today's; "LOréal"; and a search for "Coca-Cola" that listed the bottler first.

No test reaches a real adapter or a model. The rest of the suite runs with pilot mode OFF (tests/conftest.py), so the
hidden controls keep their own tests; the tests here turn it on.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from aristos_council import company_page as cp
from aristos_council import pilot
from aristos_council import summary_contradictions as sc
from aristos_council.gate.engine import NOT_APPLICABLE, PASS, WARN, GateResult
from tests.test_absolute_page import (absolute, case, fake_report, gate_ok, rule)
from tests.test_company_search import _row

pytestmark = pytest.mark.min_group(3)
ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Part A - the one setting
# --------------------------------------------------------------------------- #
def test_pilot_mode_is_on_by_default_and_off_only_when_switched_off():
    assert pilot.pilot_mode({}) is True
    assert pilot.pilot_mode({"ARISTOS_PILOT_MODE": ""}) is True
    assert pilot.pilot_mode({"ARISTOS_PILOT_MODE": "1"}) is True
    for off in ("0", "false", "OFF", "no", " False "):
        assert pilot.pilot_mode({"ARISTOS_PILOT_MODE": off}) is False


def test_the_suite_itself_runs_with_pilot_mode_off_so_the_hidden_controls_stay_tested():
    assert pilot.pilot_mode() is False


def _app(monkeypatch, on: bool):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv(pilot.ENV, "1" if on else "0")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    return at


def _labels(widgets) -> list[str]:
    return [str(getattr(w, "label", "")) for w in widgets]


def test_pilot_mode_hides_the_seven_controls(monkeypatch):
    at = _app(monkeypatch, on=True)
    radios, boxes = _labels(at.radio), _labels(at.checkbox)
    assert "Asset type" not in radios                                  # item 1
    assert "Show lens descriptions" not in _labels(at.toggle)          # item 2
    assert not at.tabs                                                 # item 3: no Scoreboard tab, one page
    assert "Input" not in radios and "List source" not in radios       # item 4: company only
    assert "Council opinion" not in boxes                              # item 5
    assert not any("judge" in b.lower() for b in boxes)                # item 6
    assert "Plain-English summary" not in boxes                        # item 7: always on, no tick box
    assert not boxes                                                   # no tick box of any kind on the pilot page
    shown = " ".join(str(m.value) for m in list(at.markdown) + list(at.caption)
                     if "<style" not in str(m.value))                # the CSS block's own comments are not on screen
    assert "Find a company" in shown
    assert "Cohort / list" not in shown and "Scoreboard" not in shown and "Display" not in shown


def test_pilot_mode_always_writes_the_summary_and_the_button_says_so(monkeypatch):
    at = _app(monkeypatch, on=True)
    assert "▶ Run company check + summary (one model call)" in " ".join(_labels(at.button))
    assert any("summary is written with every run" in str(c.value) for c in at.caption)


def test_with_pilot_mode_off_every_hidden_control_is_back(monkeypatch):
    at = _app(monkeypatch, on=False)
    radios, boxes = _labels(at.radio), _labels(at.checkbox)
    assert "Asset type" in radios and "Input" in radios
    assert "Show lens descriptions" in _labels(at.toggle)
    assert [t.label for t in at.tabs] == ["Analyse", "Scoreboard"]
    assert "Plain-English summary" in boxes and "Council opinion" in boxes
    assert any("judge" in b.lower() for b in boxes)


def test_pilot_mode_forces_stocks_and_the_company_path(monkeypatch):
    import app
    monkeypatch.setenv(pilot.ENV, "1")
    assert app.asset_mode() == app.DEFAULT_ASSET_MODE
    assert app.company_path_active() is True


# --------------------------------------------------------------------------- #
# item 7 - the summary's model
# --------------------------------------------------------------------------- #
def test_the_company_summary_runs_on_the_cheapest_haiku_and_the_run_summary_is_unchanged(monkeypatch):
    from aristos_council.agents import runners
    from aristos_council.company_report import COMPANY_READER_TIER
    from aristos_council.costs import MODEL_PRICES, price_call
    monkeypatch.delenv("ARISTOS_MODEL_COMPANY_READER", raising=False)
    monkeypatch.delenv("ARISTOS_MODEL_READER", raising=False)
    assert runners._model_for(COMPANY_READER_TIER) == "anthropic:claude-haiku-5-5"
    assert runners._model_for("reader") == "anthropic:claude-sonnet-4-6"        # what it was set to before
    haiku = [m for m in MODEL_PRICES if "haiku" in m]
    assert min(haiku, key=lambda m: MODEL_PRICES[m]) == "claude-haiku-5-5"       # the cheapest Haiku priced here
    assert price_call("anthropic:claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.60)


def test_no_temperature_is_sent_to_a_model_that_rejects_one(monkeypatch):
    """claude-haiku-5-5 answers a request that carries ``temperature`` with a 400, so the runner sends none."""
    pytest.importorskip("langchain")
    from aristos_council.agents import runners
    from aristos_council.agents.schemas import ReaderSummary
    seen = {}

    class _Model:
        def with_structured_output(self, schema, include_raw=False):
            return self

    def fake_init(model_id, **kwargs):
        seen[model_id] = kwargs
        return _Model()

    import langchain.chat_models as lcm
    monkeypatch.setattr(lcm, "init_chat_model", fake_init)
    monkeypatch.delenv("ARISTOS_MODEL_COMPANY_READER", raising=False)
    monkeypatch.delenv("ARISTOS_MODEL_READER", raising=False)
    new = runners.LangChainRunner("company_reader", ReaderSummary)
    old = runners.LangChainRunner("reader", ReaderSummary)
    assert seen["anthropic:claude-haiku-5-5"] == {} and new.temperature is None
    assert seen["anthropic:claude-sonnet-4-6"] == {"temperature": 0.0} and old.temperature == 0.0
    assert runners._accepts_temperature("anthropic:claude-haiku-4-5")
    assert not runners._accepts_temperature("claude-opus-5-5")


# --------------------------------------------------------------------------- #
# item 8 - the main company comes first
# --------------------------------------------------------------------------- #
def _coke_rows():
    return [_row("CCEP.LSE", "Coca-Cola Europacific Partners PLC", cap_bn=44.4, market="LSE", country="GB"),
            _row("KOF.US", "Coca-Cola Femsa SAB de CV ADR", cap_bn=23.6, market="US", country="US"),
            _row("CCH.LSE", "Coca Cola HBC AG", cap_bn=20.9, market="LSE", country="GB"),
            _row("KO.US", "The Coca-Cola Company", cap_bn=379.7, market="US", country="US")]


@pytest.mark.parametrize("query", ["Coca-Cola", "coca-cola", "coca cola", "cocacola"])
def test_typing_coca_cola_shows_ko_before_the_bottler(query):
    from aristos_council.company_search import search_companies
    got = [m.ticker for m in search_companies(query, rows=_coke_rows(), cohorts_root=Path("/nope")).matches]
    assert got[0] == "KO.US", got
    assert got.index("KO.US") < got.index("CCEP.LSE")


def test_a_leading_the_does_not_outrank_a_better_ticker_or_change_other_searches():
    from aristos_council.company_search import search_companies
    rows = [_row("BYD.US", "Boyd Gaming Corporation", cap_bn=5.3, market="US", country="US"),
            _row("1211.HK", "BYD Company Limited", cap_bn=93.4, market="HK", country="HK"),
            _row("HD.US", "The Home Depot Inc", cap_bn=299.0, market="US", country="US")]
    assert [m.ticker for m in search_companies("byd", rows=rows, cohorts_root=Path("/nope")).matches][0] == "1211.HK"
    assert [m.ticker for m in search_companies("home depot", rows=rows, cohorts_root=Path("/nope")).matches] == ["HD.US"]


# --------------------------------------------------------------------------- #
# Part B - the report wording
# --------------------------------------------------------------------------- #
def test_item_9_the_answer_is_said_in_plain_words_first_with_the_official_label_beside_it():
    rep = case("below")
    plain, official = cp.answer_shown(rep)
    assert plain == "Sound company, but too expensive at today's price to earn the 9.93% a year this report asks for."
    assert official.startswith("Official label: Qualifies, below hurdle: the price returns 4.6% to 8.6% a year")
    assert "0 of 4 cases clear" in official
    assert cp.answer_lines(rep)[0].startswith("Qualifies, below hurdle:")     # the official pair is unchanged


@pytest.mark.parametrize("name, starts", [
    ("clears", "Sound company, and cheap enough at today's price to earn the 9.93% a year this report asks for"),
    ("excluded", "Ruled out: it fails a safety check"),
    ("bank", "No answer: banks and insurers"),
    ("loss maker", "No answer: the data needed"),
])
def test_item_9_every_verdict_state_has_a_plain_sentence(name, starts):
    assert cp.plain_answer(case(name)).startswith(starts)


def test_item_9_sound_company_is_not_said_over_a_fired_warning_and_target_is_never_said():
    gate = gate_ok(warn=True)
    said = cp.plain_answer(fake_report(gate, absolute(gate)))
    assert said.startswith("Passes the safety checks, with 1 warning, but too expensive")
    for name in ("below", "clears", "excluded", "bank", "loss maker"):
        assert "target" not in cp.plain_answer(case(name)).lower()


def _renderings(rep) -> dict:
    from aristos_council.company_markdown import company_report_markdown
    from aristos_council.company_report import format_company_report
    from aristos_council.export.report_html import company_report_html
    from aristos_council.report_sweep import visible_text
    return {"text": format_company_report(rep), "markdown": company_report_markdown(rep),
            "html": visible_text(company_report_html(rep))}


@pytest.fixture(scope="module")
def full_report(tmp_path_factory):
    from tests.test_company_report import _run
    return _run(["magic_formula_raw_v1"], tmp_path=tmp_path_factory.mktemp("pilot"))


def test_item_10_what_this_is_and_how_to_read_it_open_every_rendering(full_report):
    from aristos_council.company_report import HOW_TO_READ, HOUSE_LINE
    assert len(HOW_TO_READ) == 2 and HOW_TO_READ[0].startswith("What this is:")
    assert HOW_TO_READ[1].startswith("How to read it:")
    for name, body in _renderings(full_report).items():
        first, second, house = (body.find(HOW_TO_READ[0][:60]), body.find(HOW_TO_READ[1][:60]),
                                body.find(HOUSE_LINE[:60]))
        assert 0 <= first < second < house, name


def test_item_11_no_issues_found_is_never_printed_above_a_summary(full_report):
    from aristos_council.company_story import _summary_check_line, story_page
    assert _summary_check_line(True) == "" and _summary_check_line(False) == ""
    from aristos_council.reader import READER_SECTION_NOTE, ReaderResult
    written = ReaderResult(summary=SimpleNamespace(asked="Asked.", happened="Happened.", survived="Survived.",
                                                   doubt="Doubt.", cannot_say="Cannot say."))
    before = full_report.summary
    full_report.summary = written                     # a model summary that passed its checks
    try:
        page = story_page(full_report)
        assert page.model_summary and page.summary_check == ""
        assert page.answer[0] == cp.plain_answer(full_report)          # the plain sentence leads the real page too
        for name, body in _renderings(full_report).items():
            assert "no issues found" not in body, name
            assert READER_SECTION_NOTE in body, name                   # the summary itself is there
    finally:
        full_report.summary = before


def test_item_11_a_problem_still_shows_a_line_where_the_summary_would_be():
    from aristos_council.reader import write_summary_from_pack

    class _Bad:
        model_id, temperature = "fake:reader", None

        def invoke(self, system, user):
            return dict(asked="a", happened="b", survived="It grew 777 percent.", doubt="d", cannot_say="e")

    res = write_summary_from_pack({"company": {"name": "Example (EX)"}}, runner=_Bad(), prompt="p")
    assert not res.available and res.note.startswith("Summary withheld: number not in the facts: 777")


def test_item_12_the_four_exclusion_rules_are_named_in_plain_words():
    text = cp._gate_found(case("below"))
    assert text.startswith("The gate has four rules that can rule a company out.")
    for plain in cp.RULE_PLAIN.values():
        assert plain in text
    assert set(cp.RULE_PLAIN) == {"earnings_quality", "solvency", "accounting_health", "cash_runway"}
    from aristos_council.company_report import company_facts_pack
    rules = company_facts_pack(case("below"))["gate"]["rules"]
    assert [r["plain"] for r in rules] == [cp.RULE_PLAIN[k] for k in
                                           ("earnings_quality", "solvency", "accounting_health", "cash_runway")]


def test_item_13_what_the_price_requires_runs_in_the_asked_order():
    text = cp._requires(case("below"))
    marks = ["of spare cash a year", "The price paid for a share is", "That is a yield of",
             "To reach the hurdle, that spare cash must grow", "The growth it actually achieved was"]
    places = [text.find(m) for m in marks]
    assert all(p >= 0 for p in places) and places == sorted(places), places


def test_item_14_the_consensus_sentence_is_gone_from_the_story_and_from_the_prompt():
    from aristos_council.company_report import company_prompt_text
    assert "no past data can test" not in cp._doubt(case("below"))
    prompt = company_prompt_text()
    assert "Do not say that the consensus case rests on analyst figures" in prompt
    assert "> **What to doubt.** The consensus case" not in prompt


def test_item_15_lens_notes_is_called_lens_descriptions(full_report):
    for name, body in _renderings(full_report).items():
        assert "lens notes" not in body.lower(), name
    import app
    src = Path(app.__file__).read_text(encoding="utf-8")
    assert '"**Lens descriptions**"' in src and '"**Lens notes**"' not in src


# --------------------------------------------------------------------------- #
# Part C - the Coca-Cola page
# --------------------------------------------------------------------------- #
THIN = "below 1.0 the dividend is not covered; 1.5 to 2.0 is the range sources call thin to comfortable"


def _ko_gate() -> GateResult:
    base = gate_ok()
    warnings = tuple(w for w in base.warnings if w.key != "dividend_cover") + (
        rule("dividend_cover", "Dividend cover", "1.3 times", THIN, PASS,
             "average net income of 3 years / dividends paid", 1.3074),)
    return GateResult("qualifies", base.reason, base.rules, warnings, debt_line=base.debt_line,
                      definition_id="absolute_gate_v2")


def _ko(slug="consumer_beverages_tobacco", votes=()):
    gate = _ko_gate()
    rep = fake_report(gate, absolute(gate), votes=votes)
    rep.cohort_slug = slug
    return rep


def _summary(**over):
    base = dict(
        asked="Would a buyer of Microsoft at today's price, holding for ten years, more likely than not beat the "
              "ten-year US bond yield plus 5 points?",
        happened="All four exclusion rules pass. No warnings fired.",
        survived="The four cases return 4.6% to 8.6%, so 0 of 4 clear the hurdle. It qualifies, below hurdle.",
        doubt="Dividend cover is 1.3 times and passed its warning.",
        cannot_say="This is not a forecast and not advice.")
    base.update(over)
    return base


def _pack(rep, monkeypatch, *, backtest: bool):
    from aristos_council.absolute_backtest import lookup
    from aristos_council.company_report import company_facts_pack
    monkeypatch.setattr(lookup, "workings_lines", lambda slug, root=None: ["Form 3: +0.0 points"] if backtest else [])
    monkeypatch.setattr(lookup, "cannot_sentence", lambda slug, root=None: (
        "In this industry over ten years, names that passed the gate and cleared the hurdle returned +0.0 points a "
        "year against the industry basket." if backtest else None))
    return company_facts_pack(rep)


def test_item_16_the_pack_says_whether_the_report_has_a_backtest(monkeypatch):
    from aristos_council.absolute_backtest.lookup import NO_RESULT
    has = _pack(_ko(), monkeypatch, backtest=True)["backtest"]
    assert has["exists"] is True and has["statement"].startswith("In this industry over ten years")
    none = _pack(_ko(), monkeypatch, backtest=False)["backtest"]
    assert none == {"exists": False, "statement": NO_RESULT}


def test_item_16_and_22_no_backtest_exists_is_withheld_when_the_report_has_one(monkeypatch):
    """The live sentence: "...and no backtest of this method exists yet", above a backtest section."""
    from aristos_council.reader_check import check_summary
    live = _summary(cannot_say="These figures show what the price returns if the stated growth repeats; they are "
                               "not a forecast or advice, the method knows nothing of the company's plans, "
                               "management or news, and no backtest of this method exists yet.")
    check = check_summary(live, _pack(_ko(), monkeypatch, backtest=True))
    assert not check.ok
    assert "contradicts the report: it says no backtest exists, but the report has a backtest section" in check.reason
    # the same sentence is TRUE on a page with no backtest, and passes there
    assert check_summary(live, _pack(_ko(), monkeypatch, backtest=False)).ok


@pytest.mark.parametrize("claim", [
    "No backtest of this method exists yet.", "There is no backtest of this method.",
    "This method has not yet been backtested.", "A backtest of this method does not exist."])
def test_item_22_other_ways_of_saying_no_backtest_are_caught_too(claim):
    assert sc.backtest_problems(claim, {"backtest": {"exists": True}})
    assert not sc.backtest_problems(claim, {"backtest": {"exists": False}})


def test_item_22_a_summary_that_reports_the_backtest_is_not_a_contradiction():
    said = ("In this industry over ten years, names that passed the gate and cleared the hurdle returned +0.0 points "
            "a year against the industry basket. The test cannot see companies that failed, so it is not proof.")
    assert sc.backtest_problems(said, {"backtest": {"exists": True}}) == []


def test_item_17_the_pack_states_where_the_reading_sits_against_its_range(monkeypatch):
    assert sc.range_position("1.3 times", THIN) == ("below", "1.3", "1.5", "2.0")
    assert sc.range_position("1.7 times", THIN)[0] == "inside"
    assert sc.range_position("2.4 times", THIN)[0] == "above"
    assert sc.range_position("no dividend paid", THIN) is None
    assert sc.range_position("7 of 9", "excluded at 1 or lower") is None
    cover = next(w for w in _pack(_ko(), monkeypatch, backtest=True)["gate"]["warnings"]
                 if w["name"] == "Dividend cover")
    assert cover["position"] == "Dividend cover of 1.3 is below the range 1.5 to 2.0."


def test_item_17_and_22_in_the_range_is_withheld_when_the_reading_is_below_it(monkeypatch):
    """The live sentence: "Dividend cover is 1.3 times, which sits in the range sources call thin to comfortable"."""
    from aristos_council.reader_check import check_summary
    pack = _pack(_ko(), monkeypatch, backtest=True)
    live = _summary(doubt="Dividend cover is 1.3 times, which sits in the range sources call thin to comfortable; "
                          "it passed its warning but is worth noting.")
    check = check_summary(live, pack)
    assert not check.ok
    assert ("contradicts the report: it says dividend cover of 1.3 is inside the range 1.5 to 2.0; it is below "
            "that range") in check.reason
    true = _summary(doubt="Dividend cover is 1.3 times, which is below the range sources call thin to comfortable.")
    assert check_summary(true, pack).ok, check_summary(true, pack).reason
    assert check_summary(_summary(), pack).ok                       # says nothing about the range: fine


def test_item_22_the_range_rule_reads_negation_and_the_other_sides():
    pack = {"gate": {"rules": [], "warnings": [{"name": "Dividend cover", "reading": "1.7 times", "limit": THIN}]}}
    assert sc.range_problems("Dividend cover of 1.7 times sits within the range.", pack) == []
    assert sc.range_problems("Dividend cover is below the range sources call thin.", pack)
    assert sc.range_problems("Dividend cover is not in the range.", pack)
    low = {"gate": {"rules": [], "warnings": [{"name": "Dividend cover", "reading": "1.3 times", "limit": THIN}]}}
    assert sc.range_problems("Dividend cover is not in the range.", low) == []
    assert sc.range_problems("Dividend cover is outside the range.", low) == []
    assert sc.range_problems("Dividend cover is above the range.", low)
    assert sc.contradiction_problems("Its cover sits in the range.", {}) == []       # a pack with no ranged rule


def test_item_22_a_list_reports_pack_is_checked_by_no_contradiction_rule():
    assert sc.contradiction_problems("No backtest of this method exists yet. It sits in the range.",
                                     {"lenses": [], "agreement": {}}) == []


def test_item_18_the_gate_reading_names_its_measure(monkeypatch):
    from aristos_council.gate import render as grender
    row = next(r for r in grender.rows(_ko_gate()) if r[0] == "Dividend cover")
    assert row[2] == "1.3 times, on profit (average net income of 3 years divided by the dividends paid)"
    plain = next(r for r in grender.rows(gate_ok()) if r[0] == "Dividend cover")
    assert plain[2] == "no dividend paid"                                           # nothing to qualify


def test_item_18_the_page_says_which_money_each_dividend_figure_uses():
    vote = SimpleNamespace(reason="dividends took 120% of free cash flow; the rule allows at most 80%. Measured on "
                                  "4-year average free cash flow.", ranked=False, label="Defensive Income",
                           measures="", position=None, cohort_size=None, result=lambda: "")
    note = cp.dividend_measures_note(_ko(votes=[vote]))
    assert "dividend cover (1.3 times) is on profit: average net income of 3 years divided by the dividends paid" in note
    assert "dividends took 120% of free cash flow) is on cash" in note and "4-year average free cash flow" in note
    assert ("note", note) in cp.context_blocks(_ko(votes=[vote]))
    assert cp.dividend_measures_note(_ko()) == ""                                   # no lens said it: no note
    other = fake_report(gate_ok(), absolute(gate_ok()), votes=[vote])
    assert cp.dividend_measures_note(other) == ""                                   # no cover reading: no note


def test_item_19_the_trailing_pe_is_the_pages_own_close_over_its_own_eps():
    """KO: close $96.09, trailing EPS $3.39, and the provider's ratio 28.68 (taken on its own intraday price)."""
    from aristos_council.abs_readings import price_and_cash
    from tests.test_price_and_cash import _fundamentals, _technical
    f = _fundamentals(ticker="KO", name="The Coca-Cola Company", currency="USD", eps=3.39, pe_ratio=28.684364,
                      trailing_period_end="2026-09-30")
    pc = price_and_cash(_technical(last_close=96.09), f)
    assert pc.trailing_pe.value == pytest.approx(96.09 / 3.39)
    assert pc.trailing_pe.label == "trailing P/E 28.3, twelve months to Sep 2026 ($96.09 / $3.39 EPS)"
    # with no close to divide, the provider's figure is shown and says whose it is
    no_close = price_and_cash(None, f)
    assert "28.7" in no_close.trailing_pe.label and "data provider's own figure" in no_close.trailing_pe.label


def test_item_20_the_size_columns_carry_the_snapshot_date():
    from aristos_council import peer_table as pt
    group = SimpleNamespace(snapshot="2026-10-15")
    assert pt.cap_headers(group) == ("Market cap (USD, on 2026-10-15)", "Market cap (local, on 2026-10-15)")
    assert "index snapshot of 2026-10-15, for every company including this one" in pt.cap_snapshot_note(group)
    assert pt.cap_headers(SimpleNamespace(snapshot="")) == (pt.USD_COLUMN, pt.LOCAL_COLUMN)
    assert pt.cap_snapshot_note(SimpleNamespace()) == ""


def test_item_21_the_debt_lines_say_net_of_cash_and_whose_free_cash_flow(full_report):
    """KO: "4.7 years of free cash flow to repay its debt" is net debt ($25.0bn) over the LATEST year's free cash
    flow ($5.3bn); it read as if it were total debt over some other figure. The line now says both."""
    lines = full_report.check.debt_and_cash.lines()
    years = [ln for ln in lines if "years of" in ln]
    assert years, lines
    for ln in years:
        assert ln.endswith("to repay its debt after cash"), ln
    for ln in years:
        if "free cash flow" in ln:
            assert "of the latest year's free cash flow" in ln, ln


def test_item_21_the_old_majority_line_says_it_decides_nothing(full_report):
    from aristos_council.company_story import OLD_METHOD_LEAD, story_page
    page = story_page(full_report)
    assert page.relative_answer[0].startswith(OLD_METHOD_LEAD)
    assert "decides nothing now" in OLD_METHOD_LEAD


def test_item_21_the_two_enterprise_value_figures_explain_their_difference():
    from aristos_council.absolute_value import render as vr
    av = SimpleNamespace(context={"ebit_over_ev": 0.0398}, price=SimpleNamespace(quote_currency="USD"))
    lines = vr.context_lines(av)
    assert lines[0] == "Operating profit over enterprise value: 4.0%." and lines[1] == vr.EV_BASIS_NOTE
    assert "last closing price and the latest annual accounts" in vr.EV_BASIS_NOTE


def test_item_21_loreal_has_its_apostrophe():
    from aristos_council.company_search import search_companies
    from aristos_council.peer_table import clean_company_name, spelling_fixed
    assert clean_company_name("LOréal S.A.") == "L'Oréal S.A."
    assert spelling_fixed("Lowe's Companies") == "Lowe's Companies"                 # nothing else is touched
    rows = [_row("OR.PA", "LOréal S.A.", cap_bn=234.6, market="PA", country="FR")]
    assert search_companies("oreal", rows=rows, cohorts_root=Path("/nope")).matches[0].name == "L'Oréal S.A."


def test_the_new_prompt_is_v3_and_v2_is_still_on_disk():
    from aristos_council import company_report as cr
    assert cr.COMPANY_PROMPT_VERSION == "reader_company_v3"
    prompts = Path(cr.__file__).resolve().parent / "agents" / "prompts"
    assert (prompts / "reader_company_v2.md").exists() and (prompts / "reader_company_v3.md").exists()
    text = cr.company_prompt_text()
    assert "Nothing you write contradicts the report" in text and "backtest.exists" in text
    assert "in exactly this order" in text and "name all\n  four" in text


def test_the_summarys_facts_pack_holds_no_lens_or_rank_line(full_report):
    """Paid test of 2026-11-20: Haiku wrote "not ranked in the income lenses" (MSFT) and "outside two of the sector
    rankings" (KO). The lines came from the pack's own context block; the pack must not hold them."""
    from aristos_council.company_report import company_facts_pack
    assert cp.rivals_lines(full_report)                                  # the page does print them
    context = company_facts_pack(full_report)["context"]
    assert context and not any("where it sits among rivals" in ln for ln in context)
    assert not set(context) & set(cp.rivals_lines(full_report))
