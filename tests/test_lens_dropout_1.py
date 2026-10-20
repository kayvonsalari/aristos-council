"""LENS-DROPOUT-1 - a ticked lens that cannot load or run stops the run with an error naming it.

Symptom (2026-10-20 23:44): nine stock lenses ticked on Ford, the report named eight, Cyclical Income was
in neither table and its tick box came back unticked. A strategy file that failed to load while the server
was running (a commit was editing the files) was dropped from the picker by ``except Exception: continue``
and Streamlit dropped the tick with it. The rule these tests hold: a ticked lens is in the report or the
run stops with a visible error that names the lens and the reason - never neither, in list runs or company
runs.

Offline: fake adapters, no model calls."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from aristos_council.judges import answer, config as judge_config, render
from aristos_council.strategy import discovery, picker
from aristos_council.strategy.discovery import LoadFailure, load_failures, visible_rank_strategies
from aristos_council.strategy.picker import LensDropped, missing_lenses, require_all_lenses
from tests.test_company_report import STRAT_DIR, UNIV_DIR, TODAY, _Adapter, _no_news, _run, _table

ROOT = Path(__file__).resolve().parents[1]
NINE_STOCK_LENSES = ("magic_formula_momentum_v1", "conservative_plus_v1", "cyclical_income_v1", "epv_v1",
                     "financials_v1", "forensic_v1", "growth_garp_v2", "magic_formula_raw_v1", "quality_v1")
OFFERED = sorted(s.id for s in visible_rank_strategies(STRAT_DIR))


def _strategies_copy(tmp_path) -> Path:
    d = tmp_path / "strategies"
    shutil.copytree(STRAT_DIR, d)
    return d


# =========================================================================== #
# (i) every strategy the picker offers loads, validates and runs end to end
# =========================================================================== #
def test_the_nine_stock_lenses_are_all_offered():
    assert set(NINE_STOCK_LENSES) <= set(OFFERED), sorted(set(NINE_STOCK_LENSES) - set(OFFERED))


@pytest.mark.parametrize("sid", OFFERED)
def test_every_offered_strategy_loads_validates_and_runs_end_to_end(sid, tmp_path):
    from aristos_council.strategy.rank_loader import load_rank_strategy
    info = next(s for s in visible_rank_strategies(STRAT_DIR) if s.id == sid)
    try:
        load_rank_strategy(info.path)                              # loads and validates against the registries
        report = _run([sid], tmp_path=tmp_path, save=False)         # runs end to end on the offline fixture
    except Exception as exc:                                       # noqa: BLE001 - re-raised with the name
        pytest.fail(f"strategy {sid} ({info.path.name}) did not load/run: {type(exc).__name__}: {exc}")
    assert [v.strategy_id for v in report.votes] == [sid], f"strategy {sid} is missing from the report"


def test_no_strategy_file_in_the_repo_fails_to_load():
    assert load_failures(STRAT_DIR) == []


# =========================================================================== #
# (ii) a ticked lens is in the lens table, or the run stops with an error - never neither
# =========================================================================== #
def test_all_nine_ticked_lenses_appear_in_the_lens_table_the_story_and_the_peers_table(tmp_path):
    from aristos_council.company_story import table_rows
    from aristos_council.peer_table import rank_columns
    report = _run(list(NINE_STOCK_LENSES), tmp_path=tmp_path, save=False)
    assert [v.strategy_id for v in report.votes] == list(NINE_STOCK_LENSES)
    assert len(table_rows(report)) == 9
    labels = {r.lens for r in table_rows(report)}
    assert len(labels) == 9 and "Cyclical Income" in labels
    for lens in ("Cyclical Income",):
        v = next(v for v in report.votes if v.label == lens)
        assert v.status in {"ranked", "excluded", "too_few", "unrateable", "no_group", "absent", "fetch_error"}


def test_a_lens_whose_file_is_corrupt_stops_the_company_run_and_the_error_names_it(tmp_path):
    from aristos_council.company_report import run_company_report
    d = _strategies_copy(tmp_path)
    (d / "cyclical_income_v1.yaml").write_text("id: cyclical_income_v1\nfactors: [\n  broken", encoding="utf-8")
    with pytest.raises(LensDropped) as err:
        run_company_report("CO", ["magic_formula_raw_v1", "cyclical_income_v1"], adapter=_Adapter(),
                           strategies_dir=d, universes_dir=UNIV_DIR, runs_dir=tmp_path / "runs", today=TODAY,
                           store=_table(), save=False, news_fetcher=_no_news)
    assert "cyclical_income_v1" in str(err.value) and "could not run" in str(err.value)
    assert "ParserError" in str(err.value)                           # the reason, not just the name


def test_a_ticked_lens_with_no_file_stops_the_list_run_and_the_error_names_it(tmp_path):
    from aristos_council.pipeline import run_multi_strategy_pipeline
    with pytest.raises(LensDropped) as err:
        run_multi_strategy_pipeline(["A", "B", "C", "D", "E", "F"], ["magic_formula_raw_v1", "no_such_lens_v1"],
                                    strategies_dir=STRAT_DIR, adapter=_Adapter(), today=TODAY,
                                    universes_dir=UNIV_DIR)
    assert "no_such_lens_v1" in str(err.value)


def test_the_guard_stops_a_run_whose_result_lacks_a_requested_lens(tmp_path, monkeypatch):
    """Defence in depth: even if some future code path dropped a vote, the company run raises."""
    from aristos_council import company_report
    real = company_report.votes_from_multi
    monkeypatch.setattr(company_report, "votes_from_multi",
                        lambda multi, ticker: [v for v in real(multi, ticker) if v.strategy_id != "quality_v1"])
    with pytest.raises(LensDropped) as err:
        _run(["magic_formula_raw_v1", "quality_v1"], tmp_path=tmp_path, save=False)
    assert "quality_v1" in str(err.value) and "ticked" in str(err.value)


def test_require_all_lenses_names_every_missing_lens_in_request_order_and_passes_a_complete_set():
    assert missing_lenses(["a", "b", "c", "b"], ["c", "a"]) == ["b"]
    require_all_lenses(["a", "b"], {"b": 1, "a": 2}, where="x")                 # complete: silent
    with pytest.raises(LensDropped) as err:
        require_all_lenses(["a", "b", "c"], ["b"], where="Multi-lens run")
    assert str(err.value).startswith("Multi-lens run: the lenses a, c were ticked but")


# =========================================================================== #
# (iii) AppTest: tick all nine lenses, rerun, the ticks are unchanged
# =========================================================================== #
pytest.importorskip("streamlit")


def _ticked(at, kind):
    return sorted(str(c.key).split(f"opt_lens_{kind}_", 1)[1] for c in at.checkbox
                  if str(getattr(c, "key", "")).startswith(f"opt_lens_{kind}_") and c.value)


@pytest.mark.parametrize("kind", ["company", "list"])
def test_all_nine_ticks_survive_reruns_and_a_switch_of_input(kind):
    from tests.test_tab_merge2 import _run_tab
    from tests.test_tab_merge_commit3 import _company_mode, _list_mode
    at = (_company_mode if kind == "company" else _list_mode)(_run_tab())
    for sid in NINE_STOCK_LENSES:
        box = next(c for c in at.checkbox if str(c.key) == f"opt_lens_{kind}_{sid}")
        if not box.value:
            box.set_value(True).run()
            assert not at.exception, at.exception
    assert _ticked(at, kind) == sorted(NINE_STOCK_LENSES)
    for _ in range(3):
        at.run()
        assert not at.exception, at.exception
        assert _ticked(at, kind) == sorted(NINE_STOCK_LENSES)
    other = "Cohort / list" if kind == "company" else "Company"
    next(r for r in at.radio if str(r.label) == "Input").set_value(other).run()
    next(r for r in at.radio if str(r.label) == "Input").set_value(
        "Company" if kind == "company" else "Cohort / list").run()
    assert not at.exception, at.exception
    assert _ticked(at, kind) == sorted(NINE_STOCK_LENSES)


# =========================================================================== #
# (iv) the loader never swallows an exception silently
# =========================================================================== #
@pytest.mark.parametrize("name,text,fragment", [
    ("broken_yaml_v1", "id: broken_yaml_v1\nfactors: [\n  oops", "ParserError"),
    ("bad_factor_v1", "id: bad_factor_v1\nname: x\nversion: 1\nfactors:\n  - name: not_a_factor\n",
     "unknown factors"),
    ("no_version_v1", "id: no_version_v1\nname: x\nfactors:\n  - name: roic\n", "version"),
    ("scalar_v1", "just some words\n", "not a strategy mapping"),
    ("empty_v1", "", "not a strategy mapping"),
    ("typo_v1", "id: typo_v1\nname: x\nversion: 1\nfactor:\n  - name: roic\n", "neither a factors"),
    ("bad_screen_v1", "id: bad_screen_v1\nname: x\nversion: 1\ncriteria:\n  - name: no_such_criterion\n"
                      "    threshold: 1\n", "no_such_criterion"),
])
def test_a_corrupt_strategy_file_leaves_the_picker_but_is_recorded_with_the_loaders_reason(
        tmp_path, name, text, fragment):
    d = _strategies_copy(tmp_path)
    before = {s.id for s in discovery.discover_strategies(d)}
    (d / f"{name}.yaml").write_text(text, encoding="utf-8")
    assert {s.id for s in discovery.discover_strategies(d)} == before          # it is not offered ...
    fails = load_failures(d)                                                   # ... and it is never silent
    assert [f.path.name for f in fails] == [f"{name}.yaml"], fails
    assert fragment in fails[0].reason, fails[0].reason
    assert fails[0].strategy_id in (name, name.rsplit("_v", 1)[0] + "_v1")


def test_a_hidden_file_that_fails_is_not_reported_because_it_is_not_offered(tmp_path):
    d = _strategies_copy(tmp_path)
    (d / "hidden_broken_v1.yaml").write_text("id: hidden_broken_v1\nui: hidden\nfactors:\n  - name: nope\n",
                                             encoding="utf-8")
    assert load_failures(d) == []


def test_the_analyse_tab_shows_the_failure_and_blocks_both_run_buttons(tmp_path, monkeypatch):
    from tests.test_tab_merge2 import _run_tab
    from tests.test_tab_merge_commit3 import _company_mode, _list_mode
    d = _strategies_copy(tmp_path)
    (d / "cyclical_income_v1.yaml").write_text("id: cyclical_income_v1\nfactors: [\n  broken", encoding="utf-8")
    real = discovery.load_failures
    monkeypatch.setattr(discovery, "load_failures", lambda _dir: real(d))

    at = _company_mode(_run_tab())
    errors = [e.value for e in at.error]
    assert any("cyclical_income_v1" in e and "cannot be loaded" in e and "ParserError" in e
               and "Runs are blocked" in e for e in errors), errors
    at.text_input(key="cc_ticker").set_value("F").run()
    assert next(b for b in at.button if str(b.key) == "cc_run").disabled

    at = _list_mode(at)
    at.text_area(key="uni_tickers").set_value("AAPL\nMSFT\nGOOG\nAMZN\nMETA\nNVDA").run()
    assert not at.exception, at.exception
    assert any("cyclical_income_v1" in e.value for e in at.error)
    assert next(b for b in at.button if str(b.key) == "uni_run").disabled


def test_with_no_corrupt_file_there_is_no_load_error_and_the_company_run_button_is_live(monkeypatch):
    from tests.test_tab_merge2 import _run_tab
    from tests.test_tab_merge_commit3 import _company_mode
    at = _company_mode(_run_tab())
    assert not any("cannot be loaded" in e.value for e in at.error)
    at.text_input(key="cc_ticker").set_value("F").run()
    assert not next(b for b in at.button if str(b.key) == "cc_run").disabled


# =========================================================================== #
# (v) judge prompt v3 and the doubled full stop
# =========================================================================== #
def _reply(**over):
    from tests.test_judge_panel import _reply as base
    return base(**over)


def test_the_panel_uses_judge_prompt_v3_and_it_says_tags_are_for_factual_claims_only():
    assert judge_config.PROMPT_VERSION == "judge_prompt_v3"
    text = judge_config.PROMPT_PATH.read_text(encoding="utf-8")
    assert judge_config.PROMPT_PATH.name == "judge_prompt_v3.md"
    assert "The tags are for factual claims only." in text
    assert '"key_risk", "unexplained" and "would_change_my_mind" are not claims' in text
    assert "put it in \"would_change_my_mind\"" in text
    # v1 and v2 stay on disk unchanged: a past run's prompt can still be read
    assert (judge_config.PROMPT_PATH.parent / "judge_prompt_v2.md").is_file()


def test_stray_tags_in_the_free_fields_are_stripped_but_the_labelled_fields_keep_theirs():
    parsed, why = answer.parse_answer(_reply(risk="A tariff shock could hit margins. [pack]",
                                             unexplained="The swing in other income. [own knowledge]",
                                             change="A cash recovery above $9bn. [pack]"))
    assert parsed is not None, why
    for field in (parsed.key_risk, parsed.unexplained, parsed.would_change_my_mind):
        assert "[pack]" not in field and "[own knowledge]" not in field
    assert parsed.key_risk == "A tariff shock could hit margins."
    assert parsed.unexplained == "The swing in other income."
    assert parsed.driving_fact.endswith("[pack].") or parsed.driving_fact.endswith("[pack]")
    assert parsed.reasoning.endswith("[pack].") or parsed.reasoning.endswith("[pack]")


def test_a_free_field_with_no_tag_at_all_is_valid():
    parsed, why = answer.parse_answer(_reply(risk="Margins could fall.", unexplained="nothing",
                                             change="A dividend cut."))
    assert parsed is not None, why


def test_a_would_change_my_mind_sentence_is_not_counted_as_a_claim():
    text = ("Free cash flow fell to $5.3bn. [pack] Net debt is $27.9bn. [pack] "
            "A recovery in free cash flow above $9bn would change my mind. [pack] "
            "Management has bought back stock for years. [own knowledge]")
    assert answer.count_claims(text) == (2, 1)
    assert answer.is_condition("A pullback to the low $80s would change my view.")
    assert answer.is_condition("Two quarters of profit would change the verdict.")
    assert not answer.is_condition("Free cash flow fell to $5.3bn.")


@pytest.mark.parametrize("unexplained,expected", [
    ("It is not fully explained in the pack. [pack]", "It is not fully explained in the pack."),
    ("It is not fully explained in the pack..", "It is not fully explained in the pack."),
    ("Why did cash fall?", "Why did cash fall?"),
    ('The filing says "no change."', 'The filing says "no change."'),
    ("The cause of the swing", "The cause of the swing."),
])
def test_the_story_sentence_ends_with_exactly_one_stop(unexplained, expected):
    from types import SimpleNamespace
    from aristos_council.judges.panel import STABLE
    call = SimpleNamespace(unexplained=unexplained)
    judge = SimpleNamespace(label="Claude", status=STABLE, first_ok=call)
    line = render.unexplained_line(SimpleNamespace(available=True, judges=[judge]))
    assert line == f"The judge panel could not account for: Claude: {expected}"
    assert ".." not in line and "[pack]" not in line


def test_two_judges_are_joined_with_a_semicolon_and_one_final_stop():
    from types import SimpleNamespace
    from aristos_council.judges.panel import STABLE
    judges = [SimpleNamespace(label="Claude", status=STABLE, first_ok=SimpleNamespace(unexplained="Cash fell. [pack]")),
              SimpleNamespace(label="OpenAI", status=STABLE, first_ok=SimpleNamespace(unexplained="nothing")),
              SimpleNamespace(label="Gemini", status=STABLE, first_ok=SimpleNamespace(unexplained="Debt rose."))]
    line = render.unexplained_line(SimpleNamespace(available=True, judges=judges))
    assert line == "The judge panel could not account for: Claude: Cash fell; Gemini: Debt rose."
