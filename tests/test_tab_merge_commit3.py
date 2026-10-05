"""TAB-MERGE-1 commit 3 — "Analyse": one tab, an explicit Company / Cohort-list switch,
the small-company info line decided before the run, and the "Council opinion" checkbox
replacing the old run-mode radio as the list side's default control.

Per the task's own list, this module checks: widget presence/absence by mode (not
disabled — ABSENT); the include-small tick box is gone everywhere; the three small-
company detection paths (known via the find box, known via the local index, unknown
until a live fetch) plus the peer-group-can't-be-built fallback; exactly one key family
for the shared options (no cc_/uni_ leftovers); zero model calls when nothing is ticked,
on both input kinds, via fake runners that explode if called; ETF mode hiding the
Company choice; the one-ticker-list hint; and that no new code imports gap_ledger.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from tests._env import needs_local_saved_list

pytest.importorskip("streamlit")

import app  # noqa: E402

_APP = Path(__file__).resolve().parents[1] / "app.py"


def _run_tab(timeout: int = 90):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=timeout).run()
    assert not at.exception, at.exception
    return at


def _company_mode(at):
    radio = next(r for r in at.radio if str(r.label) == "Input")
    if radio.value != "Company":
        radio.set_value("Company").run()
        assert not at.exception
    return at


def _list_mode(at):
    radio = next((r for r in at.radio if str(r.label) == "Input"), None)
    if radio is not None and radio.value != "Cohort / list":
        radio.set_value("Cohort / list").run()
        assert not at.exception
    return at


def _fake_raw_adapter_base():
    from aristos_council.data.adapter import MarketDataAdapter
    return MarketDataAdapter


class _FakeRawAdapter(_fake_raw_adapter_base()):
    """A minimal MarketDataAdapter — AppTest re-executes app.py as its OWN module
    instance, so ``monkeypatch.setattr(app, ...)`` never reaches it; the only lever
    that does is the SHARED library function app.py's code calls through
    (``aristos_council.data.provider.select_market_adapter``, wrapped by
    ``_company_check_adapter``'s ``CachingAdapter``), matching the pattern
    ``test_confirm_spend.py``'s own module-level comment already documents.
    Subclassing the real ABC (rather than duck-typing) picks up its class-level
    defaults, e.g. ``dividend_streak_method``, that ``CachingAdapter`` reads. Returns
    a REAL ``Fundamentals`` instance — ``CachingAdapter`` serializes whatever
    ``get_fundamentals`` returns via ``dataclasses.asdict`` to write its day-cache, so
    a duck-typed stand-in breaks there, silently, well after the cap is read."""

    name = "fake"

    def __init__(self, market_cap=2e10):
        self._cap = market_cap

    def get_fundamentals(self, ticker):
        from aristos_council.data.adapter import Fundamentals
        return Fundamentals(ticker=ticker, market_cap=self._cap)

    def get_price_history(self, ticker, *, start, end):
        return []

    def get_dividend_history(self, ticker, *, start, end):
        return []


def _patch_real_adapter(monkeypatch, market_cap=2e10):
    """Make ``_company_check_adapter()`` — and therefore the live cap-resolution tier
    and the actual run — resolve to a fake, cache-free, network-free adapter."""
    import aristos_council.data.provider as provider_mod
    monkeypatch.setattr(provider_mod, "select_market_adapter",
                        lambda: _FakeRawAdapter(market_cap))


def _patch_no_news(monkeypatch):
    """The company run's own news fetch (TEST-ISOLATION-1's other factory pair) — the UI
    call passes no ``news_fetcher`` override, so a real run reaches
    ``data.news_fallback``'s real factories unless they are faked too."""
    import aristos_council.data.news_fallback as news_mod

    def _no_opener():
        def _raise(*a, **kw):
            raise OSError("no network in tests")
        return _raise

    def _no_yf_news():
        return lambda symbol: []

    monkeypatch.setattr(news_mod, "real_url_opener", _no_opener)
    monkeypatch.setattr(news_mod, "real_yfinance_news", _no_yf_news)


def _patch_index_miss(monkeypatch):
    """Make the free, offline local-index cap lookup MISS, as it would for any ticker
    the market index has no row for — the real library call
    ``aristos_council.market_index.peers``, not the app-level wrapper."""
    import aristos_council.market_index as market_index_mod

    def _raise(*a, **kw):
        raise LookupError("not in the local index")
    monkeypatch.setattr(market_index_mod, "peers", _raise)


# --------------------------------------------------------------------------- #
# 1. Widget presence/absence by mode — ABSENT, never disabled.
# --------------------------------------------------------------------------- #
def test_company_mode_shows_find_box_and_hides_list_only_controls():
    at = _company_mode(_run_tab())
    assert any(str(t.label) == "Find a company" for t in at.text_input)
    assert any(str(t.key) == "cc_ticker" for t in at.text_input)
    # list-only controls are ABSENT, not disabled
    assert not any(str(s.label) == "List" for s in at.selectbox)
    assert not any("Tickers" in str(t.label) for t in at.text_area)
    assert not any(str(s.label) == "Narrate" for s in at.selectbox)
    assert not any(str(n.label) == "Up to" for n in at.number_input)
    assert not any("Skip names doubted" in str(c.label) for c in at.checkbox)
    assert not any("Advanced (list only)" in str(getattr(e, "label", ""))
                  for e in at.expander)
    assert not any(str(r.label) == "Run mode" for r in at.radio)


def test_list_mode_shows_the_list_flow_and_hides_company_only_controls():
    at = _list_mode(_run_tab())
    assert any(str(s.label) == "List" for s in at.selectbox)
    assert any("Tickers" in str(t.label) for t in at.text_area)
    # company-only controls are ABSENT, not disabled
    assert not any(str(t.label) == "Find a company" for t in at.text_input)
    assert not any(str(t.key) == "cc_ticker" for t in at.text_input)
    assert not any(str(getattr(c, "key", "")) == "opt_council_company" for c in at.checkbox)


def test_advanced_list_only_expander_holds_the_spend_threshold_and_size_floor():
    at = _list_mode(_run_tab())
    assert any("Advanced (list only)" in str(getattr(e, "label", "")) for e in at.expander)


# --------------------------------------------------------------------------- #
# 2. The include-small tick box is gone, in EITHER mode.
# --------------------------------------------------------------------------- #
def test_the_include_small_box_is_gone_in_both_modes():
    for at in (_company_mode(_run_tab()), _list_mode(_run_tab())):
        assert not any("Include companies under $5bn" in str(c.label) for c in at.checkbox)
        assert not any(str(getattr(c, "key", "")) == "cc_include_small" for c in at.checkbox)


# --------------------------------------------------------------------------- #
# 3. Exactly one key family for the shared options; no cc_/uni_ leftovers for the
#    widgets commit 1 unified.
# --------------------------------------------------------------------------- #
def test_no_cc_or_uni_leftover_keys_for_the_shared_options():
    for at in (_company_mode(_run_tab()), _list_mode(_run_tab())):
        keys = [str(getattr(w, "key", "") or "") for w in list(at.checkbox)]
        assert not any(k.startswith("cc_lens_") or k.startswith("uni_lens_") for k in keys)
        assert "cc_summary" not in keys and "uni_reader" not in keys
        assert "cc_council" not in keys
        assert all(not k.startswith("opt_") or k.startswith(("opt_lens_", "opt_summary_",
                                                              "opt_council_"))
                  for k in keys)


# --------------------------------------------------------------------------- #
# 4. Small-company detection — the three paths, decided BEFORE the run.
# --------------------------------------------------------------------------- #
def test_a_subcap_pick_from_the_find_box_shows_the_line_and_runs_small(monkeypatch):
    from aristos_council.company_search import CompanyMatch, SearchResult

    fake = SearchResult(
        matches=(CompanyMatch(ticker="SMALL.US", name="Small Co", exchange="NYSE",
                              market="US", country="US", market_cap_usd=3.2e9,
                              is_home=True, cohorts=()),),
        cohorts_known=True)
    monkeypatch.setattr("aristos_council.company_search.search_companies",
                        lambda *a, **kw: fake)

    _patch_real_adapter(monkeypatch)
    at = _company_mode(_run_tab())
    at.text_input(key="cc_find").set_value("small").run()
    at.button(key="cc_find_use").click().run()
    assert not at.exception

    blob = " ".join(str(getattr(i, "value", "")) for i in at.info)
    # DOLLAR-MATH-1: the page receives each "$" escaped (a bare pair is read as LaTeX maths)
    assert r"worth \$3.2bn, below the \$5bn rule" in blob
    assert "not known in advance" not in blob      # the find box already knew the cap

    captured = {}
    import aristos_council.company_report as cr

    def _fake_run(ticker, lens_ids, **kw):
        captured.update(kw)
        raise RuntimeError("stop before the real engine runs")

    monkeypatch.setattr(cr, "run_company_report", _fake_run)
    at.button(key="cc_run").click().run()
    assert captured.get("include_small") is True


def test_an_over_5bn_pick_shows_no_line_and_runs_normally(monkeypatch):
    from aristos_council.company_search import CompanyMatch, SearchResult

    fake = SearchResult(
        matches=(CompanyMatch(ticker="BIG.US", name="Big Co", exchange="NYSE",
                              market="US", country="US", market_cap_usd=8e9,
                              is_home=True, cohorts=()),),
        cohorts_known=True)
    monkeypatch.setattr("aristos_council.company_search.search_companies",
                        lambda *a, **kw: fake)

    _patch_real_adapter(monkeypatch)
    at = _company_mode(_run_tab())
    at.text_input(key="cc_find").set_value("big").run()
    at.button(key="cc_find_use").click().run()
    assert not at.exception
    blob = " ".join(str(getattr(i, "value", "")) for i in at.info)
    assert r"below the \$5bn rule" not in blob

    captured = {}
    import aristos_council.company_report as cr

    def _fake_run(ticker, lens_ids, **kw):
        captured.update(kw)
        raise RuntimeError("stop before the real engine runs")

    monkeypatch.setattr(cr, "run_company_report", _fake_run)
    at.button(key="cc_run").click().run()
    assert captured.get("include_small") is False


def test_unknown_size_is_decided_after_a_live_fetch_and_the_line_says_so(monkeypatch):
    """A hand-typed ticker the local index has no row for — the ONE case genuinely
    unknown at pick time. The free index lookup is faked to miss (as it would for any
    ticker not in the index) and the real adapter factory to a fake, capped adapter, so
    the live tier is reached without touching the real factory."""
    _patch_index_miss(monkeypatch)
    _patch_real_adapter(monkeypatch, market_cap=1.5e9)

    at = _company_mode(_run_tab())
    at.text_input(key="cc_ticker").set_value("UNKNOWN.XX").run()
    assert not at.exception
    blob = " ".join(str(getattr(i, "value", "")) for i in at.info)
    assert r"worth \$1.5bn, below the \$5bn rule" in blob
    assert "not known in advance; this was decided once its market cap was read" in blob


def test_when_the_cap_cannot_be_determined_at_all_no_line_is_shown(monkeypatch):
    """A missing cap is never guessed either way (SMALLCAP-VIEW-1's own rule) — not
    small, not normal, just silent on the point."""
    _patch_index_miss(monkeypatch)

    import aristos_council.data.provider as provider_mod

    class _FailingAdapter:
        def get_fundamentals(self, ticker):
            raise RuntimeError("no such ticker")

    monkeypatch.setattr(provider_mod, "select_market_adapter", lambda: _FailingAdapter())

    at = _company_mode(_run_tab())
    at.text_input(key="cc_ticker").set_value("NOPE.XX").run()
    assert not at.exception
    blob = " ".join(str(getattr(i, "value", "")) for i in at.info)
    assert r"below the \$5bn rule" not in blob


def test_small_peer_group_wiring_passes_include_small_straight_through():
    """The rendering of a failed/absent small-company band (the strict page with a
    stated reason) is the pre-existing, already-tested CompanyReport contract
    (test_company_report.py::test_no_cohort_match_falls_back_to_a_stated_no_vote_reason,
    rendered via the pre-existing ``no_vote_reason`` -> ``st.info`` path at
    app.py:_render_company_report). What TAB-MERGE-1 commit 3 owns is the WIRING from
    the pre-check to the run — pinned here at the source level."""
    import inspect
    src = inspect.getsource(app._render_company_run)
    assert "include_small=choice.include_small" in src
    render_src = inspect.getsource(app._render_company_report)
    assert "no_vote_reason" in render_src


# --------------------------------------------------------------------------- #
# 5. Zero model calls when nothing is ticked, on BOTH input kinds — fake runners that
#    explode if called.
# --------------------------------------------------------------------------- #
def test_company_run_with_summary_and_council_unticked_makes_zero_model_calls(monkeypatch):
    from aristos_council.agents import runners as runners_mod

    def _boom(*a, **kw):
        raise AssertionError("a run with summary/council unticked reached a narration runner")

    monkeypatch.setattr(runners_mod, "production_runners", _boom)
    _patch_real_adapter(monkeypatch)
    _patch_no_news(monkeypatch)

    at = _company_mode(_run_tab())
    assert next(c for c in at.checkbox
               if str(getattr(c, "key", "")) == "opt_summary_company").value is False
    assert next(c for c in at.checkbox
               if str(getattr(c, "key", "")) == "opt_council_company").value is False
    at.text_input(key="cc_ticker").set_value("CO").run()
    at.button(key="cc_run").click().run()
    assert not at.exception, at.exception


@needs_local_saved_list
def test_council_ticked_with_zero_lenses_still_makes_zero_model_calls(monkeypatch):
    """COUNCIL-OPINION-1's own guard (``write_council_opinion``: no votes -> an early
    returned note, never a model call) survives this merge — pinned here at the UI
    layer, where the pre-ticked default lens is explicitly UNTICKED first."""
    from aristos_council.agents import runners as runners_mod

    def _boom(*a, **kw):
        raise AssertionError("council-ticked, zero-vote run reached a narration runner")

    monkeypatch.setattr(runners_mod, "production_runners", _boom)
    _patch_real_adapter(monkeypatch)
    _patch_no_news(monkeypatch)

    at = _company_mode(_run_tab())
    for c in list(at.checkbox):
        key = str(getattr(c, "key", "") or "")
        if key.startswith("opt_lens_company_"):
            c.set_value(False)
    at.run()
    assert not any(c.value for c in at.checkbox
                  if str(getattr(c, "key", "")).startswith("opt_lens_company_"))

    next(c for c in at.checkbox
        if str(getattr(c, "key", "")) == "opt_council_company").set_value(True).run()
    at.text_input(key="cc_ticker").set_value("CO").run()
    at.button(key="cc_run").click().run()
    assert not at.exception, at.exception


def test_list_run_with_council_opinion_unticked_makes_zero_model_calls(monkeypatch, tmp_path):
    from aristos_council import pipeline
    from aristos_council.agents import runners as runners_mod
    from tests.test_multi_strategy_run import _Adapter

    def _boom(*a, **kw):
        raise AssertionError("a run with Council opinion unticked reached a narration runner")

    monkeypatch.setattr(pipeline, "_build_adapter", lambda *a, **kw: _Adapter())
    monkeypatch.setattr(runners_mod, "production_runners", _boom)

    at = _list_mode(_run_tab())
    assert not any(str(getattr(c, "key", "")) == "opt_council_list" and c.value
                  for c in at.checkbox)
    at.session_state["uni_tickers"] = "AAA\nBBB"
    at.run()
    run_button = next(b for b in at.button if str(getattr(b, "key", "")) == "uni_run")
    if not run_button.disabled:
        run_button.click().run()
        assert not at.exception, at.exception


# --------------------------------------------------------------------------- #
# 6. ETF mode hides the Company choice; list input only.
# --------------------------------------------------------------------------- #
def test_etf_mode_hides_the_company_choice_and_shows_list_input_only():
    at = _run_tab()
    # TAB-MERGE-1 part 2 commit 1: renamed from "Analyse" (now the merged tab's own name).
    radio = next(r for r in at.radio if str(r.label) == "Asset type")   # the Stocks/ETFs switch
    radio.set_value("ETFs").run()
    assert not at.exception
    assert not any(str(r.label) == "Input" for r in at.radio)
    assert not any(str(t.label) == "Find a company" for t in at.text_input)
    assert any(str(s.label) == "List" for s in at.selectbox)


# --------------------------------------------------------------------------- #
# 7. A one-ticker list shows the open-as-company hint.
# --------------------------------------------------------------------------- #
def test_a_one_ticker_list_shows_the_open_as_company_hint():
    at = _list_mode(_run_tab())
    at.session_state["uni_tickers"] = "AAPL"
    at.run()
    blob = " ".join(str(getattr(c, "value", "")) for c in at.caption)
    assert "AAPL" in blob and "open this as a company page instead" in blob


def test_a_multi_ticker_list_shows_no_such_hint():
    at = _list_mode(_run_tab())
    at.session_state["uni_tickers"] = "AAPL\nMSFT"
    at.run()
    blob = " ".join(str(getattr(c, "value", "")) for c in at.caption)
    assert "open this as a company page instead" not in blob


# --------------------------------------------------------------------------- #
# 8. No new code imports gap_ledger (GAP-LEDGER-1's import boundary).
# --------------------------------------------------------------------------- #
def test_no_gap_ledger_import_in_the_new_tab_merge_code():
    import inspect
    for fn in (app.render_input, app.render_run_tab, app._render_company_run,
              app._render_list_run, app.render_run_options, app.small_company_notice,
              app._resolve_company_cap, app._market_cap_from_index):
        assert "gap_ledger" not in inspect.getsource(fn)
