"""Batch 17 UI behaviour, driven through the real app (Streamlit AppTest, fake adapters, no
network, no model calls): SHARED-OPTIONS-1, CLICKTHROUGH-LENSES-1, ONE-TICKER-BUTTON-1,
PICKER-NAMES-1, SIDEBAR-ETF-TEXT-1, JARGON-UI-1 and the stale-results label."""
from __future__ import annotations

import pytest

pytest.importorskip("streamlit")

from tests.test_tab_merge2 import _list_run_result, _run_tab        # noqa: E402
from tests.test_tab_merge_commit3 import _company_mode, _list_mode   # noqa: E402

VM = "magic_formula_momentum_v1"
RAW = "magic_formula_raw_v1"


def _ticked(at, kind):
    return sorted(str(c.key).split(f"opt_lens_{kind}_", 1)[1] for c in at.checkbox
                  if str(getattr(c, "key", "")).startswith(f"opt_lens_{kind}_") and c.value)


def _set_lens(at, kind, sid, value):
    next(c for c in at.checkbox if str(c.key) == f"opt_lens_{kind}_{sid}").set_value(value).run()
    assert not at.exception, at.exception


# --------------------------------------------------------------------------- #
# SHARED-OPTIONS-1
# --------------------------------------------------------------------------- #
def test_lens_ticks_and_the_list_text_survive_company_list_and_stocks_etfs_switches():
    at = _company_mode(_run_tab())
    assert _ticked(at, "company") == [VM]                    # the seeded default
    _set_lens(at, "company", RAW, True)
    assert _ticked(at, "company") == sorted([RAW, VM])
    _set_lens(at, "company", VM, False)
    assert _ticked(at, "company") == [RAW]

    _list_mode(at)                                           # Company -> list: ticks follow
    assert _ticked(at, "list") == [RAW]
    at.text_area(key="uni_tickers").set_value("AAPL\nMSFT").run()
    assert not at.exception

    at.radio(key="asset_mode").set_value("ETFs").run()       # Stocks -> ETFs: its own lens set
    assert not at.exception, at.exception
    etf_boxes = [c for c in at.checkbox if str(getattr(c, "key", "")).startswith("opt_lens_list_")]
    assert etf_boxes and not any(str(c.key).endswith(RAW) for c in etf_boxes)
    assert at.text_area(key="uni_tickers").value == ""       # the ETF list is its own box

    at.radio(key="asset_mode").set_value("Stocks").run()     # ...and back: nothing was lost
    assert not at.exception, at.exception
    assert _ticked(at, "list") == [RAW]
    assert at.text_area(key="uni_tickers").value == "AAPL\nMSFT"

    at.radio(key="run_input_kind").set_value("Company").run()
    assert _ticked(at, "company") == [RAW]                   # list -> Company: same ticks


def test_the_company_ticker_text_survives_a_trip_through_list_mode():
    at = _company_mode(_run_tab())
    at.text_input(key="cc_ticker").set_value("MU").run()
    _list_mode(at)
    at.radio(key="run_input_kind").set_value("Company").run()
    assert at.text_input(key="cc_ticker").value == "MU"


# --------------------------------------------------------------------------- #
# CLICKTHROUGH-LENSES-1, PICKER-NAMES-1, JARGON-UI-1, stale results
# --------------------------------------------------------------------------- #
def test_click_through_carries_the_lists_lenses_and_the_picker_shows_names(monkeypatch):
    from tests.test_multi_strategy_run import UNIVERSE

    from aristos_council import pipeline
    from aristos_council.data.adapter import Fundamentals
    from tests.test_multi_strategy_run import _Adapter

    class Named(_Adapter):
        def get_fundamentals(self, ticker):
            f = super().get_fundamentals(ticker)
            return Fundamentals(**{**f.__dict__, "company_name": f"{ticker} Motor Company"})                 if ticker != "DEAD" else f

    real_build = pipeline._build_adapter
    at = _list_run_result(monkeypatch)       # installs the plain fake adapter first...
    monkeypatch.setattr(pipeline, "_build_adapter", lambda *a, **kw: Named())
    next(b for b in at.button if str(b.key) == "uni_run").click().run()     # ...re-run named
    assert not at.exception, at.exception
    assert _ticked(at, "list") == [RAW]                      # what the list was run with
    pick = next(s for s in at.selectbox if str(s.key) == "single_open_company_pick")
    # PICKER-NAMES-1: "Name (TICKER)", not the bare ticker the box used to list
    assert pick.options[0] == "A Motor Company (A)", pick.options
    pick.set_value(UNIVERSE[0]).run()
    next(b for b in at.button if str(b.key) == "single_open_company_button").click().run()
    assert not at.exception, at.exception
    assert at.session_state["run_input_kind"] == "Company"
    assert _ticked(at, "company") == [RAW]                   # not "No lens is ticked"
    run = next(b for b in at.button if str(b.key) == "cc_run")
    assert not run.disabled and "cc_report" not in at.session_state


def test_company_mode_with_no_lens_ticked_blocks_the_run_with_the_list_sentence():
    at = _company_mode(_run_tab())
    _set_lens(at, "company", VM, False)
    assert _ticked(at, "company") == []
    assert next(b for b in at.button if str(b.key) == "cc_run").disabled
    assert any("Pick at least one strategy." in str(i.value) for i in at.info)


def test_the_list_result_shows_no_ids_and_no_lens_jargon_block_for_readers(monkeypatch):
    at = _list_run_result(monkeypatch)
    shown = " ".join(str(getattr(e, "value", "")) for group in (at.markdown, at.caption)
                     for e in group)
    assert "adhoc:" not in shown
    assert f"({RAW})" not in shown and f"`{RAW}`" not in shown
    # the id / role / long-description caption block under the lens grid is a validation view
    assert not any(str(c.value).startswith(f"`{RAW}`") for c in at.caption)
    # ...and it comes back behind the toggle
    at.toggle(key="show_legacy").set_value(True).run()
    assert not at.exception, at.exception
    with_toggle = " ".join(str(getattr(e, "value", "")) for group in (at.markdown, at.caption)
                           for e in group)
    assert RAW in with_toggle


def test_editing_the_list_after_a_run_labels_the_old_results(monkeypatch):
    at = _list_run_result(monkeypatch)
    assert not any("Press Run to refresh" in str(w.value) for w in at.warning)
    at.text_area(key="uni_tickers").set_value("A\nB").run()
    assert any("Press Run to refresh" in str(w.value) for w in at.warning)


# --------------------------------------------------------------------------- #
# ONE-TICKER-BUTTON-1
# --------------------------------------------------------------------------- #
def test_the_one_ticker_hint_has_an_open_as_company_button_that_does_not_run():
    at = _list_mode(_company_mode(_run_tab()))
    _set_lens(at, "list", RAW, True)
    at.text_area(key="uni_tickers").set_value("EL.PA").run()
    assert any("open this as a company page instead?" in str(c.value) for c in at.caption)
    button = next(b for b in at.button if str(b.key) == "uni_open_single_as_company")
    assert button.label == "Open as company page"
    button.click().run()
    assert not at.exception, at.exception
    assert at.session_state["run_input_kind"] == "Company"
    assert at.text_input(key="cc_ticker").value == "EL.PA"
    assert _ticked(at, "company") == sorted([RAW, VM])               # the ticked lenses were kept
    assert "cc_report" not in at.session_state               # nothing ran


# --------------------------------------------------------------------------- #
# SIDEBAR-ETF-TEXT-1
# --------------------------------------------------------------------------- #
def test_the_sidebar_carries_no_hidden_lists_note_in_either_mode():
    """B22-U8: the line "ETF lists and lenses are hidden while Stocks is selected." (and its ETFs mirror,
    SIDEBAR-ETF-TEXT-1) is gone - the switch says what it does."""
    at = _run_tab()
    side = " ".join(str(c.value) for c in at.sidebar.caption)
    assert "are hidden while" not in side
    at.radio(key="asset_mode").set_value("ETFs").run()
    side = " ".join(str(c.value) for c in at.sidebar.caption)
    assert "are hidden while" not in side


def test_stale_results_rule_is_pure():
    from app import stale_results_note
    assert stale_results_note(None, ("Stocks", "MU"), what="x", now="MU") == ""
    assert stale_results_note(("Stocks", "MU"), ("Stocks", "MU"), what="x", now="MU") == ""
    assert "Press Run to refresh" in stale_results_note(
        ("Stocks", "MU"), ("Stocks", "GM"), what="the company report for MU", now="GM")
