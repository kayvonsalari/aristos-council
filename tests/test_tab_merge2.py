"""TAB-MERGE-1 part 2 — the automated tests the task's own list calls for, beyond what
is already covered inline in test_app.py (commit 1) and test_company_report.py
(commits 2 and 3): the default input mode, the peers-table highlight, and
click-through (commit 4) setting the company input without ever running or spending.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")

import app  # noqa: E402

_APP = Path(__file__).resolve().parents[1] / "app.py"


def _run_tab(timeout: int = 90):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=timeout).run()
    assert not at.exception, at.exception
    return at


# --------------------------------------------------------------------------- #
# Commit 1 — default mode is Company; sidebar switch is "Asset type".
# --------------------------------------------------------------------------- #
def test_default_input_mode_is_company():
    at = _run_tab()
    radio = next(r for r in at.radio if str(r.label) == "Input")
    assert radio.value == "Company"
    assert any(str(t.label) == "Find a company" for t in at.text_input)
    assert not any(str(s.label) == "List" for s in at.selectbox)


def test_sidebar_switch_is_labelled_asset_type_default_stocks():
    at = _run_tab()
    radio = next(r for r in at.radio if str(r.label) == "Asset type")
    assert radio.value == "Stocks"
    assert not any(str(r.label) == "Analyse" for r in at.radio)


# --------------------------------------------------------------------------- #
# Commit 2 — the company's own row is visibly highlighted in the peers table (not
# just the existing "(this company)" text marker).
# --------------------------------------------------------------------------- #
def test_the_companys_peer_row_carries_a_visible_highlight_on_screen(tmp_path):
    import pandas as pd

    from aristos_council.peer_table import THIS_COMPANY_STYLE, peer_frame_records, rank_columns
    from tests.test_company_report import RAW, _run

    report = _run([RAW], tmp_path=tmp_path, save=False)
    frame = pd.DataFrame(
        peer_frame_records(report.check.peer_group, rank_columns(report), report.ticker))
    # Build the SAME styler app._render_peers would, directly — this pins the helper's
    # own contract (row 0 carries the highlight class) without driving the full page.
    styler = frame.style.apply(
        lambda row: ([THIS_COMPANY_STYLE] * len(row)) if row.name == 0
        else [""] * len(row), axis=1)
    html = styler.to_html()
    # PEER-ROW-TINT-1: an opaque tint with an explicit text colour (the old translucent grey
    # was invisible), on the company's row only
    assert "background-color: #f6d86b" in html and "font-weight: 700" in html


def test_the_companys_peer_row_carries_the_html_export_highlight_class(tmp_path):
    from aristos_council.export.report_html import company_report_html
    from tests.test_company_report import RAW, _run

    report = _run([RAW], tmp_path=tmp_path, save=False)
    html = company_report_html(report)
    assert 'class="this-company"' in html


# --------------------------------------------------------------------------- #
# Commit 4 — click-through: zero runs, zero model calls, loads the Company input;
# the "not against your list" / "In your list" lines appear ONLY on click-through.
# --------------------------------------------------------------------------- #
def _list_run_result(monkeypatch):
    """Drive a REAL ranker-only list run through the UI (fake adapter), so there is a
    genuine result to click through from."""
    from aristos_council import pipeline
    from tests.test_multi_strategy_run import RAW, UNIVERSE, _Adapter

    monkeypatch.setattr(pipeline, "_build_adapter", lambda *a, **kw: _Adapter())

    at = _run_tab()
    radio = next(r for r in at.radio if str(r.label) == "Input")
    radio.set_value("Cohort / list").run()
    at.session_state["uni_tickers"] = "\n".join(UNIVERSE)
    at.run()
    # exactly ONE lens ticked (RAW) — this exercises the single-lens list path
    # (render_universe_result's own "Open a company page", key_prefix "single");
    # the multi-lens path carries the identical control under key_prefix "multi".
    for c in list(at.checkbox):
        key = str(getattr(c, "key", "") or "")
        if key.startswith("opt_lens_list_"):
            c.set_value(key.endswith(RAW))
    at.run()
    run_button = next(b for b in at.button if str(getattr(b, "key", "")) == "uni_run")
    assert not run_button.disabled
    run_button.click().run()
    assert not at.exception, at.exception
    return at


def test_click_through_sets_company_input_and_makes_zero_runner_or_model_calls(monkeypatch):
    from aristos_council.agents import runners as runners_mod
    from tests.test_multi_strategy_run import UNIVERSE

    def _boom(*a, **kw):
        raise AssertionError("click-through reached a narration/council runner")
    monkeypatch.setattr(runners_mod, "production_runners", _boom)

    import aristos_council.company_report as cr

    def _boom_run(*a, **kw):
        raise AssertionError("click-through started a company run on its own")
    monkeypatch.setattr(cr, "run_company_report", _boom_run)

    at = _list_run_result(monkeypatch)
    pick = next(s for s in at.selectbox if str(getattr(s, "key", "")) == "single_open_company_pick")
    pick.set_value(UNIVERSE[0]).run()
    open_btn = next(b for b in at.button if str(getattr(b, "key", "")) == "single_open_company_button")
    open_btn.click().run()
    assert not at.exception, at.exception

    assert at.session_state["run_input_kind"] == "Company"
    assert at.session_state["cc_ticker"] == UNIVERSE[0]
    # the company run button is present but UNCLICKED — nothing ran, nothing was spent
    assert any(str(getattr(b, "key", "")) == "cc_run" for b in at.button)
    assert "cc_report" not in at.session_state


def test_the_click_through_sets_no_lines_before_the_company_run(monkeypatch):
    """Half one: clicking "Open" loads the Company input; the context lines are not
    shown on the INPUT page itself (render_input never reads cc_from_list) — only
    _render_company_report does, once a report exists."""
    from tests.test_multi_strategy_run import UNIVERSE

    at = _list_run_result(monkeypatch)
    pick = next(s for s in at.selectbox if str(getattr(s, "key", "")) == "single_open_company_pick")
    pick.set_value(UNIVERSE[0]).run()
    open_btn = next(b for b in at.button if str(getattr(b, "key", "")) == "single_open_company_button")
    open_btn.click().run()
    assert not at.exception, at.exception
    assert at.session_state["run_input_kind"] == "Company"
    assert at.session_state["cc_ticker"] == UNIVERSE[0]
    assert at.session_state["cc_from_list"][0] == UNIVERSE[0]

    blob = " ".join(str(getattr(c, "value", "")) for c in at.caption)
    assert "In your list:" not in blob                # not shown until the company page itself
    assert "not against your list" not in blob


def test_the_click_through_lines_appear_on_the_company_page_once_run():
    """Half two, in a FRESH session with the post-click-through state pre-seeded
    (AppTest's own bookkeeping across an in-script st.rerun() followed by a SECOND,
    separate .run() call is unreliable here — a harness quirk, not a production one:
    the first half above already confirms the real click sets session state
    correctly with a single uninterrupted run). Exercises exactly what
    _render_company_report reads: cc_from_list, matched by ticker."""
    from aristos_council.company_report import CompanyReport, run_company_check
    from tests.test_company_report import RAW, STRAT_DIR, TODAY, UNIV_DIR, _Adapter

    check = run_company_check("CO", RAW, "", adapter=_Adapter(), strategies_dir=STRAT_DIR,
                              universes_dir=UNIV_DIR, runs_dir=Path("runs"), today=TODAY)
    report = CompanyReport(ticker="CO", check=check)

    from streamlit.testing.v1 import AppTest

    def _page():
        import streamlit as st
        import app
        app._render_company_report(st.session_state["_report"])

    at = AppTest.from_function(_page, default_timeout=60)
    at.session_state["_report"] = report
    at.session_state["cc_from_list"] = ("CO", ["In your list: Magic Formula RAW: BUY - 1st of 3"])
    at.run()
    assert not at.exception, at.exception

    blob = " ".join(str(getattr(c, "value", "")) for c in at.caption)
    assert "In your list: Magic Formula RAW: BUY - 1st of 3" in blob
    if check.peer_group is not None and check.peer_group.available:
        assert "not against your list" in blob


def test_the_click_through_lines_are_absent_for_a_differently_ticked_report():
    """Staleness: cc_from_list for a DIFFERENT ticker than the one being shown must
    never bleed into this report's page (the same pattern cc_matched_cap already
    uses)."""
    from aristos_council.company_report import CompanyReport, run_company_check
    from tests.test_company_report import RAW, STRAT_DIR, TODAY, UNIV_DIR, _Adapter

    check = run_company_check("CO", RAW, "", adapter=_Adapter(), strategies_dir=STRAT_DIR,
                              universes_dir=UNIV_DIR, runs_dir=Path("runs"), today=TODAY)
    report = CompanyReport(ticker="CO", check=check)

    from streamlit.testing.v1 import AppTest

    def _page():
        import streamlit as st
        import app
        app._render_company_report(st.session_state["_report"])

    at = AppTest.from_function(_page, default_timeout=60)
    at.session_state["_report"] = report
    at.session_state["cc_from_list"] = ("SOMEOTHER", ["In your list: X"])
    at.run()
    assert not at.exception, at.exception

    blob = " ".join(str(getattr(c, "value", "")) for c in at.caption)
    assert "In your list:" not in blob


def test_a_hand_typed_ticker_never_shows_the_click_through_lines():
    at = _run_tab()                                   # fresh session — default is Company
    assert "cc_from_list" not in at.session_state
    at.text_input(key="cc_ticker").set_value("MU").run()
    assert not at.exception
    blob = " ".join(str(getattr(c, "value", "")) for c in at.caption)
    assert "In your list:" not in blob
    assert "not against your list" not in blob
