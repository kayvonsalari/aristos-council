"""LIST-UI-1 (Batch 19B) — the "Cohort / list" half asks ONE question with three answers.

Pure logic first (``list_input``), then the page driven with AppTest. Nothing here reaches the
real adapter or the owner's market index: the resolver's lookup and the cohorts root are injected.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from aristos_council import list_input as li

_APP = Path(__file__).resolve().parents[1] / "app.py"


# --------------------------------------------------------------------------- #
# the resolver
# --------------------------------------------------------------------------- #
def _lookup():
    rows = [
        SimpleNamespace(ticker="NVDA.US", yahoo_ticker="NVDA", name="NVIDIA Corporation"),
        SimpleNamespace(ticker="AMD.US", yahoo_ticker="AMD", name="Advanced Micro Devices, Inc."),
        SimpleNamespace(ticker="INTC.US", yahoo_ticker="INTC", name="Intel Corporation"),
        SimpleNamespace(ticker="SHEL.LSE", yahoo_ticker="SHEL.L", name="Shell PLC"),
        SimpleNamespace(ticker="1211.HK", yahoo_ticker="1211.HK", name="BYD Company Limited"),
        SimpleNamespace(ticker="JPM.US", yahoo_ticker="JPM", name="JPMorgan Chase & Co."),
    ]
    return li.IndexLookup.from_rows(rows)


def test_the_resolver_line_names_what_it_recognised():
    res = li.resolve_tickers(["NVDA", "AMD", "INTC"], _lookup())
    assert res.line() == "3 recognised: NVIDIA, Advanced Micro Devices, Intel"
    assert res.send == ["NVDA", "AMD", "INTC"]


def test_dot_us_is_translated_automatically():
    res = li.resolve_tickers(["NVDA.US", "nvda"], _lookup())
    assert [r.send for r in res.recognised] == ["NVDA", "NVDA"]
    assert res.send == ["NVDA"]                       # de-duplicated, one name once
    assert not res.unrecognised


def test_an_eodhd_style_symbol_is_sent_in_the_yfinance_form():
    res = li.resolve_tickers(["SHEL.LSE"], _lookup())
    assert res.send == ["SHEL.L"]


def test_an_unrecognised_ticker_is_named_with_a_suggestion_and_never_sent():
    res = li.resolve_tickers(["NVDA", "NVDA.XX", "ZZZZZ"], _lookup())
    assert res.send == ["NVDA"]                       # nothing unrecognised reaches the provider
    by = {u.typed: u for u in res.unrecognised}
    assert by["NVDA.XX"].suggestion == "NVDA"
    assert by["ZZZZZ"].suggestion == ""
    line = res.line()
    assert "1 recognised: NVIDIA" in line
    assert "NVDA.XX not recognised - did you mean NVDA?" in line
    assert "ZZZZZ not recognised" in line


def test_a_typo_close_to_a_listed_ticker_is_suggested():
    res = li.resolve_tickers(["JPMM"], _lookup())
    assert res.unrecognised[0].suggestion == "JPM"


def test_an_exchange_the_index_does_not_cover_says_so():
    res = li.resolve_tickers(["7203.T"], _lookup())
    assert not res.recognised
    assert "Tokyo is not covered" in res.line()


def test_with_no_index_nothing_is_judged():
    res = li.resolve_tickers(["NVDA.US", "WHATEVER"], li.IndexLookup())
    assert res.checked is False and res.line() == ""
    assert res.send == ["NVDA", "WHATEVER"]           # passes through, ".US" still translated


def test_short_names_cut_at_the_comma_or_a_legal_suffix_only():
    assert li._short_name("JPMorgan Chase & Co.") == "JPMorgan Chase &"   # cut at suffix, not "&"
    assert li._short_name("Advanced Micro Devices, Inc.") == "Advanced Micro Devices"
    assert li._short_name("Intel Corporation") == "Intel"


# --------------------------------------------------------------------------- #
# the sentence above Run
# --------------------------------------------------------------------------- #
def test_the_sentence_for_a_cohort():
    assert li.run_sentence(li.SOURCE_COHORT, n_names=28, n_lenses=9, deterministic=True,
                           cohort_industry="Auto Manufacturers") == (
        "Rank 28 Auto Manufacturers under 9 lenses, deterministic, no model calls.")


def test_the_sentence_for_a_pasted_list():
    text = li.run_sentence(li.SOURCE_PASTE, n_names=3, n_lenses=9, deterministic=True)
    assert text.startswith("Rank these 3 names against each other")
    assert ("a pasted list is its own peer group and carries no track-record badges"
            in text)


def test_the_sentence_counts_the_names_left_out():
    text = li.run_sentence(li.SOURCE_PASTE, n_names=2, n_lenses=1, deterministic=True,
                           n_left_out=1)
    assert "1 ticker not recognised and left out" in text


def test_floor_formatting():
    assert li.format_floor(1e9) == "$1bn+"
    assert li.format_floor(2.5e9) == "$2.5bn+"
    assert li.format_floor(None) == ""


def test_a_saved_list_label_carries_the_built_for_tag_and_ends_with_the_count():
    assert li.saved_list_label("Income picks", 16, "income") == "Income picks · income · 16 names"
    assert li.saved_list_label("Mine", 1) == "Mine · 1 name"


# --------------------------------------------------------------------------- #
# the cohort dropdown reads the real definitions and the frozen members
# --------------------------------------------------------------------------- #
_MEMBER_HEADER = ("ticker,yahoo_ticker,exchange,industry,market_cap,currency,isin,name,source,"
                  "filled,market_cap_usd,flags\n")


def _build(root: Path, slug: str, rows: list[tuple[str, str]]) -> None:
    d = root / slug / "v1"
    d.mkdir(parents=True)
    body = "".join(f"{t},{t.split('.')[0]},X,Ind,1,USD,,{n},eodhd,,2000000000,\n" for t, n in rows)
    (d / "members.csv").write_text(_MEMBER_HEADER + body, encoding="utf-8")


@pytest.fixture
def cohorts_root(tmp_path, monkeypatch):
    root = tmp_path / "cohorts"
    _build(root, "consumer_auto_manufacturers",
           [("F.US", "Ford"), ("GM.US", "General Motors"), ("TSLA.US", "Tesla")])
    _build(root, "energy_midstream_pipelines", [("KMI.US", "Kinder Morgan")])
    import aristos_council.cohorts.builder as builder
    monkeypatch.setattr(builder, "DEFAULT_ROOT", root)
    return root


def test_cohort_options_are_grouped_by_sector_with_count_and_floor(cohorts_root):
    opts = li.cohort_options(cohorts_root=cohorts_root)
    assert [o.slug for o in opts] == ["consumer_auto_manufacturers", "energy_midstream_pipelines"]
    auto = opts[0]
    assert auto.industry == "Auto Manufacturers" and auto.sector == "Consumer"
    assert len(auto.members) == 3
    assert auto.label == "Consumer · Auto Manufacturers · 3 names · $1bn+"


def test_a_cohort_that_was_never_built_is_not_offered(cohorts_root):
    slugs = {o.slug for o in li.cohort_options(cohorts_root=cohorts_root)}
    assert "health_biotechnology" not in slugs
    assert li.unbuilt_cohort_count(cohorts_root=cohorts_root) > 40   # the rest are defined only


def test_run_tickers_are_in_the_form_the_ranker_runs(cohorts_root):
    auto = li.cohort_options(cohorts_root=cohorts_root)[0]
    assert auto.members == ("F", "GM", "TSLA")        # ".US" -> bare, as every consumer runs it


# --------------------------------------------------------------------------- #
# the page
# --------------------------------------------------------------------------- #
def _page():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    radio = next(r for r in at.radio if str(r.label) == "Input")
    radio.set_value("Cohort / list").run()
    assert not at.exception
    return at


def _source(at, choice):
    next(r for r in at.radio if str(r.label) == "List source").set_value(choice).run()
    assert not at.exception
    return at


def _captions(at) -> str:
    return "\n".join(c.value for c in at.caption if isinstance(c.value, str))


def _markdown(at) -> str:
    return "\n".join(m.value for m in at.markdown if isinstance(m.value, str))


def test_the_page_asks_one_question_with_three_answers():
    at = _page()
    assert "What set of companies do you want ranked against each other?" in _markdown(at)
    radio = next(r for r in at.radio if str(r.label) == "List source")
    assert list(radio.options) == ["Built cohort", "Saved list", "Paste tickers"]
    assert radio.value == "Paste tickers"


def test_the_old_adhoc_caption_and_advanced_title_are_gone():
    at = _page()
    blob = _captions(at) + _markdown(at)
    assert "Ad-hoc cohort" not in blob and "no declared asset class" not in blob
    labels = [str(e.label) for e in at.expander]
    assert "One-off overrides (this run only)" in labels
    assert not any("Advanced" in label for label in labels)


def test_the_paste_box_states_its_dialect():
    at = _page()
    box = next(t for t in at.text_area if "Tickers" in str(t.label))
    assert "yfinance style: NVDA, SHEL.L, 1211.HK" in str(box.placeholder)


def test_no_save_panel_until_something_is_pasted():
    at = _page()
    assert not any("Save these" in str(e.label) for e in at.expander)
    at.session_state["uni_tickers"] = "AAPL\nMSFT"
    at.run()
    assert not at.exception
    saves = [e for e in at.expander if "Save these" in str(e.label)]
    assert [str(e.label) for e in saves] == ["Save these 2 names as a list (optional)"]


def test_a_pasted_list_with_one_unrecognised_ticker(monkeypatch):
    monkeypatch.setitem(li._LOOKUP_CACHE, "__default__", _lookup())
    at = _page()
    at.session_state["uni_tickers"] = "NVDA.US\nAMD\nZZZZZ"
    at.run()
    assert not at.exception
    caps = _captions(at)
    assert "2 recognised: NVIDIA, Advanced Micro Devices" in caps
    assert "ZZZZZ not recognised" in caps
    md = _markdown(at)
    assert "Rank these 2 names against each other" in md           # the unrecognised one is out
    assert "1 ticker not recognised and left out" in md
    assert any(str(e.label) == "Save these 2 names as a list (optional)" for e in at.expander)


def test_the_cohort_answer_lists_built_cohorts_and_states_the_run(cohorts_root):
    at = _source(_page(), "Built cohort")
    dd = next(s for s in at.selectbox if str(s.label) == "Cohort")
    assert dd.options == ["Consumer · Auto Manufacturers · 3 names · $1bn+",
                          "Energy · Midstream & Pipelines · 1 name · $2bn+"]
    assert not any("Tickers" in str(t.label) for t in at.text_area)   # no box to type in
    dd.set_value(dd.options[0]).run()
    assert not at.exception
    assert ("Rank 3 Auto Manufacturers under 1 lens, deterministic, no model calls."
            in _markdown(at))
    assert not any("Save these" in str(e.label) for e in at.expander)  # nothing to save


def test_with_no_cohort_built_the_page_says_so(tmp_path, monkeypatch):
    import aristos_council.cohorts.builder as builder
    monkeypatch.setattr(builder, "DEFAULT_ROOT", tmp_path / "none")
    at = _source(_page(), "Built cohort")
    assert any("No cohort has been built" in str(i.value) for i in at.info)


def test_the_saved_list_answer_has_its_own_dropdown_and_loads_the_box(monkeypatch, tmp_path):
    import aristos_council.universe as _univ
    import yaml
    (tmp_path / "mine_v1.yaml").write_text(yaml.safe_dump({
        "id": "mine_v1", "display_name": "Mine", "created": "2026-10-05",
        "rationale": "t", "thesis": "income", "tickers": ["AAPL", "MSFT"]}), encoding="utf-8")
    # list_universes is read per call by the app; hand it the one list directly.
    def fake(_d):
        from aristos_council.universe import load_universe
        return [load_universe(tmp_path / "mine_v1.yaml")]
    monkeypatch.setattr(_univ, "list_universes", fake)
    at = _source(_page(), "Saved list")
    dd = next(s for s in at.selectbox if str(s.label) == "My lists")
    assert dd.options == ["Mine · income · 2 names"]
    dd.set_value(dd.options[0]).run()
    assert not at.exception
    assert at.session_state["uni_tickers"].splitlines() == ["AAPL", "MSFT"]
    assert not any("Save these" in str(e.label) for e in at.expander)  # unedited: nothing to save


def test_etf_mode_offers_no_cohort_answer():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    next(r for r in at.radio if str(r.label) == "Asset type").set_value("ETFs").run()
    assert not at.exception
    radio = next(r for r in at.radio if str(r.label) == "List source")
    assert list(radio.options) == ["Saved list", "Paste tickers"]
