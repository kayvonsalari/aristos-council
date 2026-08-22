"""Tests for Council Station (app.py) pure handlers.

app.py imports streamlit, which lives in the optional ``ui`` extra and is NOT a
test dependency — so this module skips cleanly when streamlit isn't installed,
and runs the assertions where it is. Only pure, non-Streamlit helpers are
exercised here (the UI rendering itself is integration-tested by running it).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")

import app  # noqa: E402
from aristos_council.data.adapter import DataUnavailable  # noqa: E402


def test_friendly_error_maps_data_unavailable_to_message():
    msg = app._friendly_error(DataUnavailable("delisted / empty frame"), "ZZZZ")
    assert msg == "No data found for ZZZZ — check the symbol."


def test_friendly_error_uses_the_ticker_in_the_message():
    assert "BRK-B" in app._friendly_error(DataUnavailable("x"), "BRK-B")


def test_friendly_error_passes_through_unexpected_exceptions():
    # Non-DataUnavailable errors return None so the UI shows the full traceback
    # rather than masking a real bug behind a friendly message.
    assert app._friendly_error(ValueError("boom"), "JNJ") is None
    assert app._friendly_error(RuntimeError("no key"), "JNJ") is None


# --------------------------------------------------------------------------- #
# Provenance prose stripper — patterns taken from saved JNJ/MO reports.
# Display-only: stored text keeps the call_ids; this just cleans the view.
# --------------------------------------------------------------------------- #
def test_strip_provenance_removes_real_callid_parentheticals():
    cases = [
        # (call_id: <id>, <field>) — colon form
        ("Payout is healthy (call_id: f53d7013627c, payout_ratio) and stable.",
         "Payout is healthy and stable."),
        # rich field reference with assignments and semicolons
        ("Yield misses (call_id: 8d39404e0e90, criteria[0].passed = false; "
         "observed = 0.02248982485973189; threshold = 0.025) the floor.",
         "Yield misses the floor."),
        # bare (call_id <id>) — no colon
        ("Streak unverifiable (call_id 8d39404e0e90).",
         "Streak unverifiable."),
        # [call_id <id>] — bracket form
        ("Coverage constructive [call_id 1db8ae4fbf65].",
         "Coverage constructive."),
    ]
    for raw, want in cases:
        assert app.strip_provenance(raw) == want


def test_strip_provenance_handles_nested_parens_in_citation():
    # A quoted headline inside the citation contains its own '(...)'.
    raw = ('News skews positive (call_id 11a7564d5ce2, item 2: "Assessing '
           'Johnson & Johnson (JNJ) prospects") overall.')
    assert app.strip_provenance(raw) == "News skews positive overall."


def test_strip_provenance_is_noop_on_clean_text():
    assert app.strip_provenance("No citations here.") == "No citations here."
    assert app.strip_provenance("") == ""
    assert app.strip_provenance(None) is None


def test_run_label_shape():
    from datetime import datetime, timezone

    from aristos_council.persistence.reports import RunReport
    from aristos_council.state import Decision, Recommendation

    r = RunReport(
        ticker="MO", run_at=datetime(2026, 6, 12, 13, 42, tzinfo=timezone.utc),
        strategy_id="dividend_aristocrats_v1",
        decision=Decision(recommendation=Recommendation.HOLD, confidence=0.55,
                          rationale="r"),
    )
    label = app._run_label(r)
    assert label.startswith("MO · ")
    assert "HOLD 0.55" in label
    # local time (Europe/Berlin = UTC+2 in June): 13:42 UTC -> 15:42
    assert "15:42" in label
    # verdict color dot present (selector can't render hex)
    assert app._VERDICT_DOT["HOLD"] in label


def test_verdict_hex_is_the_only_semantic_palette():
    assert app._verdict_hex("BUY") == "#2E7D32"
    assert app._verdict_hex("hold") == "#B8860B"   # case-insensitive
    assert app._verdict_hex("SELL") == "#B23B3B"
    assert app._verdict_hex(None) == "#8A8A8A"     # neutral fallback
    # INSUFFICIENT_EVIDENCE has its own NON-directional slate (not green/amber/red)
    ie = app._verdict_hex("INSUFFICIENT_EVIDENCE")
    assert ie == "#5B6B7B"
    assert ie not in {"#2E7D32", "#B8860B", "#B23B3B"}
    assert "INSUFFICIENT_EVIDENCE" in app._VERDICT_DOT


def test_favicon_is_svg_data_uri():
    uri = app._favicon()
    assert uri.startswith("data:image/svg+xml;base64,")


# --------------------------------------------------------------------------- #
# Rendering fixes — tested against the REAL saved BRK-B and MO reports.
# --------------------------------------------------------------------------- #
import glob  # noqa: E402

from aristos_council.persistence.reports import load_report  # noqa: E402

_REPORTS = Path(__file__).resolve().parents[1] / "reports"


def _saved(ticker):
    return load_report(sorted(glob.glob(str(_REPORTS / ticker / "*.json")))[-1])


def test_brkb_rationale_preserves_markdown_line_structure():
    out = app._prose(_saved("BRK-B").decision.rationale, show_provenance=False)
    # NOT flattened into one paragraph
    assert out.count("\n") > 10
    assert "**Strategy Mandate" in out
    assert "\n\n**Screen Results**" in out          # bold "header" on its own line
    assert "\n1. **min_dividend_yield**" in out      # numbered list survives
    assert "\n2. **max_payout_ratio**" in out


def test_brkb_field_paths_move_behind_provenance_toggle():
    r = _saved("BRK-B")
    default = app._prose(r.decision.rationale, show_provenance=False)
    raw = app._prose(r.decision.rationale, show_provenance=True)
    assert "criteria[0].passed" not in default       # stripped from default view
    assert "call_id" not in default                  # and no call_id plumbing
    assert "criteria[0].passed = false" in raw       # kept in the toggle view
    # the plain statement around it survives
    assert "observed 0.0 against a threshold of 0.025" in default


def test_dollar_signs_escaped_so_currency_survives_markdown():
    disp = app._render_prose(_saved("BRK-B").decision.rationale, False)
    assert "\\$1.048 trillion" in disp               # escaped, not eaten
    assert disp.count("$") == disp.count("\\$")       # every $ is escaped


def test_mo_rationale_preserves_table_and_strips_inline_path():
    r = _saved("MO")
    out = app._prose(r.decision.rationale, show_provenance=False)
    assert "| Criterion | Result | Observed | Threshold |" in out  # table header
    assert "\n| min_dividend_yield |" in out                       # a table row
    assert "criteria[1].passed" not in out                         # inline path gone
    assert "call_id" not in out
    disp = app._render_prose(r.decision.rationale, False)
    assert disp.count("$") == disp.count("\\$")


def test_strategy_dir_is_absolute_and_anchored_to_the_app_file():
    assert app.STRATEGIES_DIR.is_absolute()
    assert app.STRATEGIES_DIR == app.ROOT / "strategies"
    assert app.ROOT == _APP.parent


def test_strategy_discovery_is_cwd_independent(monkeypatch, tmp_path):
    # Launch cwd must not matter: both strategies are found from anywhere.
    monkeypatch.chdir(tmp_path)
    ids = [s.id for _, _, s in app.list_strategy_options(app.STRATEGIES_DIR)]
    assert {"dividend_aristocrats_v1", "growth_v1"} <= set(ids)


def test_dropdown_lists_all_live_strategies():
    # 4C: growth_v1 is lit up — every live strategy is selectable.
    options = app.list_strategy_options(app.STRATEGIES_DIR)
    ids = [s.id for _, _, s in options]
    assert "dividend_aristocrats_v1" in ids
    assert "growth_v1" in ids


_APP = Path(__file__).resolve().parents[1] / "app.py"


def _markdown_blob(at) -> str:
    return "\n".join(m.value for m in at.markdown if isinstance(m.value, str))


def _legacy_app(timeout: int = 60):
    """Run the app with the 'Show legacy tools' toggle ON — legacy surfaces (the
    single-ticker council, Report/History, Strategy editor) are hidden by DEFAULT, so
    any test that exercises them must opt in. Session state persists across at.run()."""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(_APP), default_timeout=timeout)
    at.session_state["show_legacy"] = True
    return at.run()


# --------------------------------------------------------------------------- #
# Run tab — schema-split pickers + pure render helpers (Sprint)
# --------------------------------------------------------------------------- #
def test_rank_strategy_options_lists_only_rank_strategies():
    ids = [s.id for _, _, s in app.list_rank_strategy_options(app.STRATEGIES_DIR)]
    assert {"conservative_plus_v1", "magic_formula_v1",
            "magic_formula_momentum_v1"} <= set(ids)
    # council + lens strategies never appear in the rank picker
    assert "growth_v1" not in ids and "magic_value_screen_v1" not in ids


def test_single_ticker_dropdown_excludes_rank_and_lens():
    ids = [s.id for _, _, s in app.list_strategy_options(app.STRATEGIES_DIR)]
    assert "magic_formula_v1" not in ids               # rank -> Run tab only
    assert "conservative_screen_v1" not in ids         # lens -> hidden


# --------------------------------------------------------------------------- #
# FUND-UI-2 — ONE run flow: the guards are pure, and a list label names one list
# --------------------------------------------------------------------------- #
def test_run_problems_is_empty_for_a_runnable_single_strategy_narrator_run():
    assert app.run_problems(["AAPL"], n_strategies=1, deterministic=False,
                            has_key=True) == []


def test_run_problems_names_every_blocker():
    problems = app.run_problems([], n_strategies=0, deterministic=False, has_key=False)
    blob = " ".join(problems)
    assert "at least one strategy" in blob
    assert "at least one ticker" in blob
    assert "ANTHROPIC_API_KEY" in blob


def test_run_problems_enforces_one_shared_cap():
    over = ["T%d" % i for i in range(app.UNIVERSE_CAP + 1)]
    assert any("too large" in p for p in
               app.run_problems(over, n_strategies=1, deterministic=True, has_key=True))
    at_cap = ["T%d" % i for i in range(app.UNIVERSE_CAP)]
    assert app.run_problems(at_cap, n_strategies=1, deterministic=True,
                            has_key=True) == []


def test_a_deterministic_run_never_asks_for_a_key():
    # Ranker-only, and several strategies (ranker-only by construction), cannot spend.
    assert app.run_problems(["AAPL"], n_strategies=1, deterministic=True,
                            has_key=False) == []
    assert app.run_problems(["AAPL"], n_strategies=3, deterministic=True,
                            has_key=False) == []


def test_saved_list_labels_disambiguate_a_shared_label():
    from aristos_council.universe import Universe
    a = Universe(id="mine_v1", display_name="My List", tickers=["AAPL"], local=True)
    b = Universe(id="mine_v2", display_name="My List", tickers=["MSFT"], local=True)
    solo = Universe(id="other_v1", display_name="Other", tickers=["NVDA"], local=True)
    labels = app.saved_list_labels([a, b, solo])
    assert len(set(labels)) == 3                       # a label names exactly one list
    assert "mine_v1" in labels[0] and "mine_v2" in labels[1]
    assert labels[2] == "Other (local) · 1 names"      # unique label left alone


def test_estimate_shortlist_size_tracks_the_cut():
    from aristos_council.strategy.rank_loader import load_rank_strategy
    magic = load_rank_strategy(app.STRATEGIES_DIR / "magic_formula_v1.yaml")
    assert app._estimate_shortlist_size(0, magic) == 0
    assert app._estimate_shortlist_size(20, magic) == 4   # quintile ~ n/5
    assert app._estimate_shortlist_size(2, magic) == 1     # never below 1 for n>0


def test_ranked_rows_marks_imputed_factors_with_a_star():
    from aristos_council.rank_engine import RankedTicker
    rt = RankedTicker(
        ticker="A", factor_ranks={"earnings_yield": 1.0, "net_payout_yield": 2.0},
        factor_values={}, combined_rank=3.0, universe_size=3, verdict="buy",
        imputed_factors=["net_payout_yield"])
    rows, factors = app._ranked_rows([rt])
    assert factors == ["earnings_yield", "net_payout_yield"]
    row = rows[0]
    assert row["Verdict"] == "BUY"
    # RANK-DISPLAY-1: ordinal position first, rank-SUM as detail against its bounds
    # (2 factors, cohort of 1 -> best 2 · worst 2; score = combined 3).
    assert row["Position (score)"] == "#1 of 1 · score 3 (best 2 · worst 2)"
    assert row["earnings_yield"] == "1"                 # present, no star
    assert row["net_payout_yield"] == "2*"              # imputed -> star


def test_universe_markdown_has_sections_from_the_result():
    from aristos_council.pipeline import RankPipelineResult
    from aristos_council.rank_engine import RankedTicker
    rt = RankedTicker(ticker="A", factor_ranks={"earnings_yield": 1.0},
                      factor_values={}, combined_rank=1.0, universe_size=2,
                      verdict="buy")
    result = RankPipelineResult(
        ranked=[rt], excluded=[("C", "screen: min_roic (observed 0.08 vs 0.12)")],
        unrateable=[("DEAD", "UNRATEABLE: no data — possibly delisted")],
        narratives={"A": "ranked #1 on ROIC."},
        header="Verdict: deterministic ranker.  Narrative: LLM (non-judging).",
        meta={"rank_strategy_id": "magic_formula_v1",
              "screen_strategy_id": "magic_value_screen_v1",
              "council_mode": "narrator", "ranker_only": False,
              "universe_size": 3, "ranked_count": 1, "shortlist": ["A"],
              "est_cost": 0.19},
        council_mode="narrator")
    md = app._universe_markdown(result)
    assert "# Universe run — magic_formula_v1" in md
    assert "## Ranked (verdict of record)" in md
    assert "Position (score)" in md                     # RANK-DISPLAY-1 header
    # the ranked row for A: ordinal position first (1 factor, cohort of 1), then verdict
    assert "#1 of 1 · score 1 (best 1 · worst 1) | A | BUY |" in md
    assert "## Excluded" in md and "min_roic" in md
    assert "## Unrateable" in md and "DEAD" in md
    assert "## Narrative" in md and "ranked #1 on ROIC." in md


def _strategy_picker(at):
    """The Run tab's PRIMARY strategy picker (FUND-UI-2 item 5) — a dropdown, because
    narration is single-strategy and the primary is the verdict the narrator explains.
    Extra lenses are the checkbox group below it (`_lens_checkbox`), not more dropdown."""
    return next(s for s in at.selectbox
                if str(s.label).startswith("Primary strategy"))


def _lens_checkbox(at, needle):
    """One "Also grade with" lens checkbox, by its exact label or a fragment of it (exact
    wins, so "Growth" never resolves to "Growth ETFs (US)"). The whole set is visible at
    once — that is the point of the checkbox group replacing the multiselect."""
    return (next((c for c in at.checkbox if str(c.label) == needle), None)
            or next(c for c in at.checkbox if needle in str(c.label)))


def _lens_checkbox_labels(at):
    """Every extra-lens checkbox label on the Run tab, as rendered — a lens checkbox is
    labelled with a strategy the picker offers, so the run-mode boxes never leak in."""
    offered = set(_strategy_picker(at).options)
    return [str(c.label) for c in at.checkbox if str(c.label) in offered]


def test_rank_picker_order_baseline_label_and_no_v2_heading():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    opts = list(_strategy_picker(at).options)
    assert "momentum" in opts[0].lower()                        # flagship first
    # the baseline (magic_formula_v1) is HIDDEN by default now (ITEM 2) — it carries its
    # 'baseline — for comparison' label only under the validation toggle (asserted in
    # test_validation_assets_revealed_when_toggle_on).
    assert not any("baseline" in o.lower() for o in opts)
    heads = " ".join(str(getattr(e, "value", "")) for e in at.subheader)
    assert "Run — pick strategies, pick tickers, run" in heads and "v2" not in heads


def test_confirmation_line_states_strategy_universe_and_mode():
    m = {"rank_strategy_id": "magic_formula_v1", "universe_id": "growth_40_v1",
         "council_mode": "ranker-only"}
    assert app._confirmation_line(m) == \
        "Running magic_formula_v1 on growth_40_v1 in ranker-only."
    # ad-hoc universe id (with its hash) is carried through
    m2 = {"rank_strategy_id": "s", "universe_id": "adhoc:abcd1234",
          "council_mode": "narrator"}
    assert "adhoc:abcd1234" in app._confirmation_line(m2)


def test_confirmation_line_is_in_the_persisted_markdown():
    from aristos_council.pipeline import RankPipelineResult
    result = RankPipelineResult(
        ranked=[], excluded=[], unrateable=[], narratives={},
        header="Verdict: deterministic ranker.  Narrative: none (ranker-only — no LLM ran).",
        meta={"rank_strategy_id": "magic_formula_momentum_v1",
              "screen_strategy_id": "magic_value_screen_v1",
              "universe_id": "growth_40_v1", "council_mode": "ranker-only",
              "ranker_only": True, "universe_size": 40, "ranked_count": 0,
              "shortlist": [], "est_cost": 0.0}, council_mode="ranker-only")
    md = app._universe_markdown(result)
    assert "Running magic_formula_momentum_v1 on growth_40_v1 in ranker-only." in md


def test_run_tab_renders_with_the_one_flow():
    # The app renders (all tabs) with the Run tab present: ONE strategy picker, ONE list
    # selector, ONE ticker box, ONE run button — no run triggered, nothing hits the network.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    # The "Primary strategy" dropdown only exists inside render_universe_tab, so its
    # presence proves the tab rendered.
    picker = _strategy_picker(at)
    # Options are the FRIENDLY display names (ITEM 1) — the technical id is demoted to a
    # caption, so it never appears in the label (no ids/underscores/_v1).
    assert any("Value + Momentum" in o for o in picker.options)
    assert not any("_" in o for o in picker.options)
    assert picker.value                                  # the flagship is pre-selected
    # Every OTHER lens is a checkbox, visible without opening anything (item 5) — exactly
    # one box each, and none of them ticked, so the default run is the narrated primary.
    boxes = _lens_checkbox_labels(at)
    assert sorted(boxes) == sorted(o for o in picker.options if o != picker.value)
    assert len(boxes) == len(set(boxes))
    assert not any(_lens_checkbox(at, o).value for o in boxes)
    # ...and the flow is exactly: strategies, a list, its tickers, run.
    assert any(s.label == "List" for s in at.selectbox)
    assert any("Tickers" in str(t.label) for t in at.text_area)
    # ONE run button on this tab (Company Check has its own; the flow used to have two).
    assert [b.label for b in at.button if b.label == "▶ Run"] == ["▶ Run"]


# --------------------------------------------------------------------------- #
# Legacy surfaces hidden by default behind the "Show legacy tools" toggle
# --------------------------------------------------------------------------- #
def _info_blob(at) -> str:
    return " ".join(str(getattr(e, "value", "")) for e in getattr(at, "info", []))


def _header_blob(at) -> str:
    return " ".join(str(getattr(e, "value", "")) for e in getattr(at, "header", []))


def test_legacy_hidden_by_default_and_toggle_defaults_off():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    # the toggle exists and defaults OFF
    legacy_toggle = next(t for t in at.toggle
                         if t.label == "Show validation & legacy tools")
    assert legacy_toggle.value is False
    assert at.session_state["show_legacy"] is False
    # NO legacy surface rendered: no council-run button, no legacy sidebar header, no
    # Strategy-editor scope banner, no legacy tabs — the legacy render paths never ran.
    assert not any("Run council" in b.label for b in at.button)
    assert "Run a council" not in _header_blob(at)
    assert "Edits council-strategy YAMLs" not in _info_blob(at)
    assert not any("Legacy" in str(t.label) for t in at.tabs)
    # the v2 product IS the landing (the Run tab's strategy picker renders)
    assert any(str(s.label).startswith("Primary strategy") for s in at.selectbox)


def test_legacy_surfaces_appear_when_toggle_on():
    at = _legacy_app(60)
    assert not at.exception
    assert any("Run council" in b.label for b in at.button)          # council flow back
    assert "Run a council" in _header_blob(at)                       # legacy sidebar back
    assert any("Report · Legacy" in str(t.label) for t in at.tabs)   # legacy tabs back


def _dropdown(at, label):
    return next(s for s in at.selectbox if s.label == label)


def test_validation_assets_hidden_by_default(monkeypatch, tmp_path):
    # ITEM 2 + UNI-1: toggle OFF (default) -> the List selector offers "New list" plus the
    # front-stage saved lists; the strategy picker offers the live strategies. The
    # never-graded trap bench + the ui:hidden baseline stay hidden.
    #
    # Isolate the universes the app-under-test sees: copy the SHIPPED manifests (top-level
    # only — NOT universes/local/) into a tmp dir and point list_universes there, so the
    # List selector shows a KNOWN set. Without this, the assertions below depend on whatever
    # personal lists sit in this machine's gitignored universes/local/ — and a personal list
    # may legitimately reuse a deleted cohort's id (e.g. growth_40_v1), which would wrongly
    # trip the "Growth 40 absent" checks. app.py imports list_universes locally per call, so
    # patching the module attribute reaches the running app (verified).
    import shutil
    import aristos_council.universe as _univ
    _real_list_universes = _univ.list_universes
    for _p in (_APP.parent / "universes").glob("*.yaml"):
        shutil.copy(_p, tmp_path / _p.name)
    monkeypatch.setattr(_univ, "list_universes",
                        lambda _dir: _real_list_universes(tmp_path))

    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception

    uni = _dropdown(at, "List").options
    assert uni[0] == "New list"                                      # FUND-UI-2: start blank
    # FUND-UI-2 deleted the shipped stock cohorts — a list is one YOU save now.
    assert not any("Growth 40" in o for o in uni)
    assert not any("Defensive Income 16" in o for o in uni)
    assert not any("Financials 16" in o for o in uni)
    assert not any("Validation Bench" in o for o in uni)             # trap bench (fixture now)
    assert not any("Energy Watch" in o for o in uni)                 # observation (fixture now)
    # The ETF lists stay: they carry fund tickers nobody types from memory.
    assert any("Dividend ETFs (US)" in o for o in uni)               # ETF-1 exploratory cohort
    assert any("Growth ETFs (US)" in o for o in uni)                 # ETF-1 exploratory cohort
    assert any("ETF Index Tracker — UCITS" in o for o in uni)        # ETFCORE-1 cohort
    assert not any("Core Market ETFs" in o for o in uni)             # UI-RENAME-1: old label gone
    # New list + 2 US ETF lists + 3 UCITS ETF lists (dividend + growth [UCITS-1] + core
    # [ETFCORE-1]) = 6. The universes are isolated to the SHIPPED set above, so this is now
    # exact. (These app tests skip in CI — streamlit is not in dev.)
    assert len(uni) == 6
    # Everything shipped is an ETF list (local/ was excluded by the isolation), so every
    # option is "New list" or an ETF list.
    assert all(o == "New list" or "ETF" in o for o in uni)

    rank = _strategy_picker(at).options
    assert not any("Classic Value" in o for o in rank)              # baseline hidden (ui: hidden)
    assert "Growth" in rank                                         # growth is live (4C)
    assert any("RAW" in o for o in rank)                            # canonical raw (RAW-1)
    assert any("Financials" in o for o in rank)                     # financials lens (FIN-1)
    assert any("Dividend ETFs" in o for o in rank)                  # ETF-1 dividend lens
    assert any("Growth ETFs" in o for o in rank)                    # ETF-1 growth lens
    assert any("ETF Index Tracker" in o for o in rank)              # ETFCORE-1 lens (UI-RENAME-1)
    assert not any("Core Market ETFs" in o for o in rank)           # old label gone
    # 5 stock lenses + 3 visible ETF lenses (dividend, growth, core [ETFCORE-1]).
    assert len(rank) == 8


def test_both_strategy_pickers_list_the_live_strategies():
    # 4C ITEM 2 + FUND-UI-2: the Run tab's picker AND Company Check's both come from the
    # ONE picker module, so they offer the SAME set with the same friendly display names.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    rank = _strategy_picker(at).options
    cc = _dropdown(at, "Strategy (lens screen + factors)").options
    assert list(rank) == list(cc)                                    # one picker, one set
    for opts in (rank, cc):
        assert "Growth" in opts                                      # plain names now
        assert any("RAW" in o for o in opts)                         # canonical raw
        assert any("Financials" in o for o in opts)                  # financials lens (FIN-1)
        assert not any("_" in o for o in opts)                       # display names, no ids
        # 5 stock lenses + 3 visible ETF lenses (dividend, growth, core [ETFCORE-1]).
        assert len(opts) == 8


def test_the_same_lists_are_offered_in_both_selectors():
    # UNI-1 ITEM 1's contract survives the cohort deletion: BOTH the Run tab's List
    # selector and the Company Check reference selector discover from universes/ through
    # the same role-derived visible_universes, so they offer the same lists (each with its
    # own extra entry — "New list" / "(none …)").
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    uni = _dropdown(at, "List").options                              # Run tab
    ref = _dropdown(at, "Reference universe (for factor context)").options  # Company Check
    def _names(opts):
        return {o.lstrip("⭐ ").split(" · ")[0] for o in opts
                if o != "New list" and not o.startswith("(none")}
    assert _names(uni) == _names(ref)
    # the never-graded trap bench stays backstage in both (default toggle off) — and it is
    # a fixture now, so it is not in universes/ at all
    assert not any("Validation Bench" in o for o in uni)
    assert not any("Validation Bench" in o for o in ref)


def test_suggested_universe_renders_first_in_the_reference_selector():
    # UNI-1 ITEM 2 survives FUND-UI-2 where it still means something: Company Check's
    # REFERENCE cohort is a manifest picked for factor context, so the selected strategy's
    # suggested cohort still heads it with the ⭐ marker, every other cohort selectable
    # below (a hierarchy, never a lock). The Run tab's List selector is no longer a
    # manifest picker — it is your saved lists — so it carries no suggestion ordering.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    cc_dd = _dropdown(at, "Strategy (lens screen + factors)")
    etf = next(o for o in cc_dd.options if "ETF Index Tracker" in o)
    cc_dd.set_value(etf).run()
    assert not at.exception
    ref = _dropdown(at, "Reference universe (for factor context)").options
    assert ref[0] == "⭐ ETF Index Tracker — UCITS · 5 names"        # suggested group first
    assert not any(o.startswith("⭐") for o in ref[1:])             # only the suggested one
    assert any(o.startswith("Dividend ETFs (US) ·") for o in ref)   # cross-lens selectable


def test_the_run_tab_list_selector_offers_no_suggestion_ordering():
    # FUND-UI-2: no per-section "relevant" filtering or steering in the ONE run flow — the
    # List selector is a flat list of what you saved, and every strategy is offered for it.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    uni = _dropdown(at, "List").options
    assert not any(str(o).startswith("⭐") for o in uni)


def test_validation_assets_revealed_when_toggle_on():
    # The universe half of this is gone with the demo cohorts (the trap bench is a fixture
    # now, not a shipped list); the STRATEGY half is what the toggle still reveals.
    from streamlit.testing.v1 import AppTest
    default = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not default.exception
    at = _legacy_app(60)
    assert not at.exception
    rank = _strategy_picker(at).options
    assert any("Classic Value" in o for o in rank)                  # baseline revealed
    assert len(rank) > len(_strategy_picker(default).options)       # strictly more offered


_MSFT_PRE_4E = _REPORTS / "MSFT" / "2026-06-14T13-29-49Z.json"


def test_bare_callid_in_key_figures_block_is_stripped():
    # The Decision rationale's "Key Figures (Provenance)" block uses bare
    # "call_id <hex>, <field>" — must be stripped from displayed prose.
    rat = load_report(_MSFT_PRE_4E).decision.rationale
    assert "call_id" in rat                       # present in the raw/toggle view
    out = app.strip_provenance(rat)
    assert "call_id" not in out                   # gone from the default view
    assert "0.1242" in out                        # the observed value survives


def test_data_quality_summary_line():
    audit = load_report(_MSFT_PRE_4E).provenance_audit
    assert app._dq_summary(audit) == \
        "7 provenance issues: 5 mismatches, 2 unresolvable"


def test_data_quality_violations_grouped_by_tool():
    audit = load_report(_MSFT_PRE_4E).provenance_audit
    groups = app._group_violations(audit["violations"])
    # every violation is accounted for, and repeats collapse into fewer lines
    assert sum(len(items) for _, items in groups) == 7
    assert len(groups) < 7
    # headers are "<count> <kind> citing <tool>"
    assert all(h.split()[0].isdigit() for h, _ in groups)


def test_data_quality_banner_renders_summary_with_expander():
    # Integration: browsing the pre-4E MSFT run, the data_quality flag shows the
    # one-line summary and a "Show provenance issues" expander (not a raw dump).
    at = _legacy_app(90)                              # Report browser is a legacy surface
    next(s for s in at.selectbox if s.label.startswith("Runs for")).set_value("MSFT")
    at.run()
    run_sel = next(s for s in at.selectbox if s.label == "Run")
    run_sel.set_value(len(run_sel.options) - 1)      # oldest = pre-4E (7 issues)
    at.run()
    assert not at.exception
    assert "7 provenance issues" in _markdown_blob(at)
    assert any(e.label == "Show provenance issues" for e in at.expander)


def test_screen_chrome_css_keeps_controls_reachable():
    # On screen the ONLY hide is the footer; the menu + sidebar toggle are
    # explicitly forced visible (never hidden). Aggressive hides are allowed
    # inside @media print, which is excluded here.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    blob = _markdown_blob(at)
    screen_css = blob.split("@media print")[0]   # on-screen rules only
    assert "footer {visibility: hidden;}" in screen_css   # footer hide is fine
    assert "display: none" not in screen_css              # nothing else hidden
    # controls are explicitly forced visible
    assert "visibility: visible !important;" in screen_css
    assert "#MainMenu" in screen_css                      # menu kept reachable
    assert "Sidebar" in screen_css                        # sidebar toggle kept


def test_toolbar_mode_keeps_menu_reachable():
    # The ⋮ menu is gated server-side by toolbarMode: "viewer"/"minimal" hide it
    # entirely (no CSS restores it). Only "auto" (localhost) / "developer" render
    # the menu + its Settings/theme switch, so config.toml must use one of those.
    import tomllib
    cfg = tomllib.loads(
        (_APP.parent / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert cfg["client"]["toolbarMode"] in ("auto", "developer")


def test_human_number_formats_large_thresholds():
    assert app._human_number(10_000_000_000) == "10,000,000,000 ($10B)"
    assert app._human_number(5_000_000_000) == "5,000,000,000 ($5B)"
    assert app._human_number(0.025) is None          # small values: no humanizing
    assert app._human_number(25) is None


def test_strategy_tab_renders_criteria_and_provenance_by_default():
    # 4C ITEM 3: the dynamic viewer renders the selected strategy's screen criteria and
    # the provenance footer, all from YAML.
    at = _legacy_app(60)
    assert not at.exception
    txt = _strategy_tab_text(at)
    assert "Screen criteria" in txt
    assert "configs are versioned; strategies are never mutated" in txt   # provenance


def test_strategy_tab_switches_strategy_via_its_own_selector():
    # Switching the viewer's own "Strategy config" selector to the GARP rank strategy
    # renders rank factors + the verdict cut generically (no strategy-specific code).
    at = _legacy_app(60)
    sb = next(s for s in at.selectbox if s.label == "Strategy config")
    sb.set_value(next(o for o in sb.options if "growth_garp_v1" in o))
    at.run()
    assert not at.exception
    txt = _strategy_tab_text(at)
    assert "Rank factors + verdict cut" in txt         # rank-only section rendered
    assert "quintile" in txt                          # the configured cut rule
    assert "growth_garp_v1" in txt                    # header names the switched-to strategy


def test_growth_run_is_triggerable_and_reports_browsable_under_growth():
    # End-to-end UI check without a real API call: selecting growth + a ticker +
    # the cost ack makes the Run button live (run_council would receive the
    # growth path — see the routing test), and the report browser still works
    # (it is strategy-independent, so any saved ticker is browsable).
    at = _legacy_app(60)
    sb = next(s for s in at.selectbox if s.label == "Strategy")
    sb.set_value(next(o for o in sb.options if "growth_v1" in o))
    next(t for t in at.text_input if t.label == "Ticker").set_value("JNJ")
    next(c for c in at.checkbox if "costs real credits" in c.label).set_value(True)
    at.run()
    assert not at.exception
    run_btn = next(b for b in at.button if "Run council" in b.label)
    assert run_btn.disabled is False                  # growth run is triggerable
    assert any(s.label.startswith("Runs for") for s in at.selectbox)  # browsable


def test_selecting_growth_routes_the_growth_strategy_path():
    # The label->path map (what the sidebar selectbox drives) must route the
    # growth label to growth_v1.yaml, which loads the growth strategy.
    from aristos_council.strategy.loader import load_strategy

    options = app.list_strategy_options(app.STRATEGIES_DIR)
    label_to_path = {label: path for label, path, _ in options}
    growth_label = next(label for label, _, s in options if s.id == "growth_v1")
    path = label_to_path[growth_label]
    assert path.name == "growth_v1.yaml"
    assert load_strategy(path).id == "growth_v1"


# --------------------------------------------------------------------------- #
# Strategy tab cleanup: one strategy at a time, distinct sections, locked params
# --------------------------------------------------------------------------- #
def _strategy_tab_text(at) -> str:
    """All textual output (headings, markdown, captions) — for asserting the
    tab's structure regardless of which element type carries each string."""
    parts = []
    for attr in ("title", "header", "subheader", "markdown", "caption"):
        for el in getattr(at, attr, []):
            v = getattr(el, "value", None)
            if isinstance(v, str):
                parts.append(v)
    return "\n".join(parts)


def test_strategy_tab_shows_gates_rationale_and_policy_glossary():
    # Switch to the flagship: its sector gate + rationale (post-send ITEM 2) and a policy
    # flag with a plain glossary meaning render — all from YAML, one strategy at a time.
    at = _legacy_app(60)
    sb = next(s for s in at.selectbox if s.label == "Strategy config")
    sb.set_value(next(o for o in sb.options if "magic_formula_momentum_v1" in o))
    at.run()
    assert not at.exception
    txt = _strategy_tab_text(at)
    assert "Gates" in txt and "Policy" in txt
    assert "not computable on a comparable basis for financials" in txt   # sector rationale
    assert "hard prefilter" in txt                     # prefilter_screen glossary meaning
    # only ONE strategy on screen at a time (the header names it)
    assert "growth_garp_v1" not in txt


def test_strategy_tab_lists_a_synthetic_strategy_with_zero_ui_changes(tmp_path):
    # ACCEPTANCE: a brand-new strategy YAML dropped into strategies/ renders fully via
    # strategy_detail with NO UI-code changes — every section derives from the YAML.
    from aristos_council.strategy.detail import strategy_detail

    (tmp_path / "synthetic_screen_v1.yaml").write_text(
        "\n".join([
            "id: synthetic_screen_v1",
            "name: Synthetic screen",
            "version: 1",
            "criteria:",
            "  - name: min_roic",
            "    threshold: 0.12",
            "  - name: min_price_momentum",
            "    threshold: 0.0",
            "",
        ]), encoding="utf-8")
    (tmp_path / "synthetic_v1.yaml").write_text(
        "\n".join([
            "id: synthetic_v1",
            "name: Synthetic",
            "display_name: Synthetic Demo",
            "version: 3",
            "created: '2026-07-09'",
            "description: A synthetic strategy for the acceptance test.",
            "factors:",
            "  - name: roic",
            "  - name: momentum_12m",
            "cut: top_k",
            "k: 5",
            "min_market_cap: 3000000000",
            "exclude_sectors:",
            "  - Financials",
            "sector_exclusion_rationale: banks are out.",
            "council_screen_strategy: synthetic_screen_v1",
            "prefilter_screen: true",
            "",
        ]), encoding="utf-8")

    d = strategy_detail("synthetic_v1", tmp_path)
    # 1 header
    assert d.display_name == "Synthetic Demo" and d.version == 3 and d.created == "2026-07-09"
    # 2 description
    assert d.description == "A synthetic strategy for the acceptance test."
    # 3 criteria (resolved from the referenced lens)
    assert d.screen_source == "lens: synthetic_screen_v1"
    assert any(c.name == "min_roic" for c in d.criteria)
    # 4 gates (sector + rationale, market cap)
    assert any(g.name == "sector" and "banks are out" in g.rationale for g in d.gates)
    assert any(g.name == "min_market_cap" for g in d.gates)
    # 5 factors + cut
    assert {f.name for f in d.factors} == {"roic", "momentum_12m"}
    assert "top_k" in d.cut_rule and "5" in d.cut_rule
    # 6 policy (glossary-sourced meaning)
    assert any(p.name == "prefilter_screen" and "hard prefilter" in p.meaning
               for p in d.policy)
    # 7 provenance
    assert d.path.endswith("synthetic_v1.yaml")


def test_available_tickers_lists_every_ticker_on_disk():
    tickers = app._available_tickers(app.REPORTS_DIR)
    assert tickers == sorted(tickers)                 # sorted
    # all tickers with saved reports are browsable, regardless of the sidebar
    assert {"BRK-B", "JNJ", "MO"} <= set(tickers)


def test_screen_table_rows_map_pass_fail_noteval():
    rows = app._screen_table_rows({"criteria": [
        {"name": "min_dividend_yield", "passed": True, "observed": 0.05,
         "threshold": 0.025},
        {"name": "max_payout_ratio", "passed": False, "observed": 0.9,
         "threshold": 0.75},
        {"name": "min_market_cap", "passed": None, "observed": None,
         "threshold": 1e10},
    ]})
    assert [r["Status"] for r in rows] == ["PASS", "FAIL", "NOT-EVAL"]
    assert rows[0]["Criterion"] == "min_dividend_yield"
    assert rows[2]["Observed"] is None


def test_screen_table_rows_empty_when_no_screen():
    assert app._screen_table_rows(None) == []
    assert app._screen_table_rows({}) == []


# --------------------------------------------------------------------------- #
# The ONE run flow (FUND-UI-2) — the editor IS the ticker box; no second section
# --------------------------------------------------------------------------- #
def test_the_separate_universe_edit_section_is_gone():
    # FUND-UI-2: there was a bottom "Universe Editor" expander with its own clone
    # selector, its own ticker box, its own Run-once button and its own save — a second
    # run flow wearing one tab. It is deleted; the ONE ticker box is the editor.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    assert not any(s.label == "Start from" for s in at.selectbox)
    assert not any("Universe Editor" in str(e.label) for e in at.expander)
    assert not any("Run once" in b.label for b in at.button)
    assert not any("Save to universes/local/" in b.label for b in at.button)
    assert not hasattr(app, "render_universe_editor")
    assert not hasattr(app, "_parse_universe")           # parse_ticker_lines serves the box


def test_the_one_ticker_box_saves_in_place_or_as_a_new_list():
    # "Type a name, press save" — and editing your own list updates it rather than forking.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    assert any("Save this list" in str(e.label) for e in at.expander)
    assert any(b.label == "Save changes" for b in at.button)
    assert any(b.label == "Save as new list" for b in at.button)
    assert any(t.label == "List name" for t in at.text_input)


def test_selecting_a_saved_list_loads_its_tickers_into_the_one_box():
    # Load-on-select (no "Load into editor" button any more): picking a list seeds the box,
    # where it is edited before running.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    list_dd = _dropdown(at, "List")
    assert list_dd.options[0] == "New list"
    first_saved = list_dd.options[1]                     # whatever ships / is saved
    list_dd.set_value(first_saved).run()
    assert not at.exception
    loaded = at.session_state["uni_tickers"].splitlines()
    # the box now holds that list, one ticker per line — the count is in its own label
    assert len(loaded) == int(first_saved.split("·")[-1].strip().split()[0])


def test_editing_a_shipped_list_forks_and_never_writes_back_to_the_manifest():
    # FUND-UI-2 item 2: editing is ALWAYS a fork. The box is editable for every list, but
    # an edit to one you do not own (shipped / scoreboard-graded) may not reach
    # universes/*.yaml — it runs as an ad-hoc cohort, the file stays byte-identical, and the
    # UI says so naming the list it came from.
    from streamlit.testing.v1 import AppTest
    src = app.UNIVERSES_DIR / "etf_core_ucits_v1.yaml"
    before = src.read_bytes()
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    _pick_list(at, "ETF Index Tracker — UCITS")
    assert not at.exception
    box = next(t for t in at.text_area if "Tickers" in str(t.label))
    box.set_value(box.value + "\nAAPL").run()               # an edit nobody saved
    assert not at.exception
    blob = _caption_blob(at)
    assert "ad-hoc copy" in blob                            # this run grades the fork
    assert "ETF Index Tracker — UCITS" in blob              # named: where the edit came from
    assert "on disk is untouched" in blob                   # and the original is intact
    assert "Save as new list" in blob                       # the only way to keep it
    # in-place rewrite is not even offered for a list that is not yours
    assert next(b for b in at.button if b.label == "Save changes").disabled
    assert src.read_bytes() == before                       # byte-unchanged, no write-back


def test_saved_local_universe_appears_in_both_selectors():
    # UNIED-1 Item 3: a saved local list is discovered front-stage (default toggle off) in
    # BOTH the Run tab's List selector and the Company Check reference selector, tagged
    # "(local)". Written into the real (gitignored) universes/local/ then removed.
    from streamlit.testing.v1 import AppTest
    local_dir = app.UNIVERSES_DIR / "local"
    local_dir.mkdir(parents=True, exist_ok=True)
    f = local_dir / "apptest_local_v1.yaml"
    f.write_text(
        "id: apptest_local_v1\ndisplay_name: Apptest Local\n"
        "created: '2026-07-15'\nrationale: test\ntickers:\n  - AAPL\n  - MSFT\n",
        encoding="utf-8")
    try:
        at = AppTest.from_file(str(_APP), default_timeout=60).run()
        assert not at.exception
        uni = _dropdown(at, "List").options
        ref = _dropdown(at, "Reference universe (for factor context)").options
        assert any("Apptest Local (local)" in o for o in uni)
        assert any("Apptest Local (local)" in o for o in ref)
    finally:
        f.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# UI-FIX-1 — auto-persist run outputs, self-describing filenames, Scoreboard tab
# --------------------------------------------------------------------------- #
def _fixture_universe_result(universe_id="growth_40_v1"):
    from aristos_council.pipeline import RankPipelineResult
    from aristos_council.rank_engine import RankedTicker

    rt = RankedTicker(ticker="A", factor_ranks={"earnings_yield": 1.0},
                      factor_values={}, combined_rank=1.0, universe_size=1,
                      verdict="buy")
    return RankPipelineResult(
        ranked=[rt], excluded=[], unrateable=[], narratives={"A": "ranked #1."},
        header="Verdict: deterministic ranker.  Narrative: LLM (non-judging).",
        meta={"rank_strategy_id": "magic_formula_v1",
              "screen_strategy_id": "magic_value_screen_v1",
              "universe_id": universe_id, "council_mode": "narrator",
              "ranker_only": False, "universe_size": 1, "ranked_count": 1,
              "shortlist": ["A"], "est_cost": 0.05}, council_mode="narrator")


def test_persist_universe_run_writes_files_matching_the_download_bytes(monkeypatch,
                                                                        tmp_path):
    # A completed named-manifest run auto-persists .md + .html BEFORE any download
    # click — the fix for the two lost paid runs. Bytes must match what the download
    # buttons would serve (same builder functions), and the names must carry the
    # universe display-name slug (ITEM 2).
    from datetime import datetime, timezone

    monkeypatch.setattr(app, "UNIVERSE_RUNS_DIR", tmp_path / "universe_runs")
    result = _fixture_universe_result()
    run_start = datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc)

    md_path, html_path = app._persist_universe_run(result, run_start, "Growth 40")

    assert md_path.exists() and html_path.exists()
    assert md_path.parent == tmp_path / "universe_runs"
    assert "growth-40" in md_path.name and "growth-40" in html_path.name
    assert md_path.read_text(encoding="utf-8") == app._universe_markdown(result)

    from aristos_council.export.report_html import universe_report_html
    assert html_path.read_text(encoding="utf-8") == \
        universe_report_html(result, run_start=run_start)


def test_persist_universe_run_ad_hoc_run_persists_too(monkeypatch, tmp_path):
    # An ad-hoc run (a new or EDITED list — FUND-UI-2) carries universe_display_name=""
    # — SCOPE item 1 explicitly requires it persists exactly like a saved-list run, just
    # without the slug segment.
    from datetime import datetime, timezone

    monkeypatch.setattr(app, "UNIVERSE_RUNS_DIR", tmp_path / "universe_runs")
    result = _fixture_universe_result(universe_id="adhoc:deadbeef")
    run_start = datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc)

    md_path, html_path = app._persist_universe_run(result, run_start, "")

    assert md_path.exists() and html_path.exists()
    assert md_path.name == "universe_magic_formula_v1_narrator_2026-08-05_1200.md"
    assert html_path.name == "universe_magic_formula_v1_narrator_2026-08-05_1200.html"


def test_saved_to_banner_shows_the_persisted_paths():
    # UI-FIX-1: the paths are printed prominently in the result header, before the rest
    # of the run renders — the first thing a user sees is where the run landed on disk.
    from datetime import datetime, timezone

    from streamlit.testing.v1 import AppTest

    md_p = app.ROOT / "reports" / "universe_runs" / "x.md"
    html_p = app.ROOT / "reports" / "universe_runs" / "x.html"
    at = AppTest.from_file(str(_APP), default_timeout=60)
    at.session_state["uni_result"] = _fixture_universe_result()
    at.session_state["uni_run_start"] = datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc)
    at.session_state["uni_persisted_paths"] = (md_p, html_p)
    at.session_state["uni_universe_display_name"] = "Growth 40"
    at = at.run()
    assert not at.exception
    blob = " ".join(str(getattr(s, "value", "")) for s in at.success)
    assert "Saved to:" in blob
    assert "reports" in blob and "universe_runs" in blob and "x.md" in blob
    assert "x.html" in blob


def test_scoreboard_is_its_own_top_level_tab():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    assert any(str(t.label) == "Scoreboard" for t in at.tabs)
    # The real committed snapshots/verdict_consensus.csv has rows, so the panel itself
    # (unchanged content — an expander with the persisted-snapshots table) renders.
    assert any("Persisted snapshots" in str(e.label) for e in at.expander)


def test_scoreboard_panel_is_no_longer_wired_into_the_run_flow():
    # Source-level regression guard: the panel moved OFF the Run flow (ITEM 4).
    # AppTest can't distinguish "which tab" an element came from (all tab bodies execute
    # in one script run), so this pins the actual wiring instead.
    import inspect

    uni_src = inspect.getsource(app.render_universe_tab)
    assert "render_scoreboard_tab" not in uni_src
    assert "_render_snapshot_history" not in uni_src   # the old name is gone entirely
    assert not hasattr(app, "_render_snapshot_history")

    main_src = inspect.getsource(app.main)
    assert "render_scoreboard_tab" in main_src
    assert '"Scoreboard"' in main_src


# --------------------------------------------------------------------------- #
# STRAT-PICKER-1, as FUND-UI-2 leaves it — the picker NAMES the cohort's asset class and
# warns on a confirmed mismatch, but never trims itself: every compatible strategy is
# offered for ANY ticker list (the live 2026-08-10 bug was an ad-hoc stock cohort offered
# a single lens while five stock lenses sat unreachable).
# --------------------------------------------------------------------------- #
def _caption_blob(at) -> str:
    return "\n".join(c.value for c in at.caption if isinstance(c.value, str))


def _pick_list(at, needle):
    dd = _dropdown(at, "List")
    dd.set_value(next(o for o in dd.options if needle in o)).run()
    return at


def test_named_etf_cohort_states_its_derived_asset_class():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    _pick_list(at, "ETF Index Tracker — UCITS")
    assert not at.exception
    # derived by inversion from the lenses that declare this cohort (applicability.py)
    assert "Cohort asset class: **etf**" in _caption_blob(at)


def test_adhoc_cohort_filters_nothing_and_says_so():
    # "New list" is the default and declares nothing -> UNKNOWN, so nothing is hidden.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    blob = _caption_blob(at)
    assert "Ad-hoc cohort" in blob and "nothing is filtered out" in blob
    assert len(_strategy_picker(at).options) == 8      # every live lens (5 stock + 3 ETF)


def test_every_strategy_stays_offered_even_for_a_cohort_of_the_other_kind():
    # FUND-UI-2: no per-section "relevant" filtering. An ETF cohort still OFFERS the stock
    # lenses (they are runnable — the asset-kind gate excludes the names honestly); the
    # mismatch is surfaced as a warning, not as a hidden option.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    before = list(_strategy_picker(at).options)
    _pick_list(at, "ETF Index Tracker — UCITS")
    assert not at.exception
    assert list(_strategy_picker(at).options) == before      # nothing trimmed
    warnings = " ".join(str(getattr(w, "value", "")) for w in at.warning)
    assert "asset-kind gate" in warnings                     # the flagship is equity-only


# --------------------------------------------------------------------------- #
# FUND-RUN-1 through the ONE picker — several strategies over one list is deterministic
# and reports ONE combined grid.
# --------------------------------------------------------------------------- #
def test_ticking_a_second_lens_makes_the_run_deterministic():
    # FUND-UI-2 item 5: the second lens is now a CHECKBOX, not a second multiselect pick.
    # Same run underneath — deterministic, one combined grid, no key asked for.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    raw = next(o for o in _strategy_picker(at).options if "RAW" in o)
    _lens_checkbox(at, raw).set_value(True).run()
    assert not at.exception
    blob = _caption_blob(at)
    assert "Multi-lens re-grade" in blob and "no narration, no cost" in blob
    assert "ONE combined grid" in blob
    # the run button says how many lenses will run, and is not gated on an API key
    assert any("Run 2 strategies (free)" in b.label for b in at.button)
    assert not any("ANTHROPIC_API_KEY" in str(getattr(i, "value", "")) for i in at.info)


def test_a_zero_strategy_run_is_structurally_unreachable_and_still_refused():
    # Was test_deselecting_every_strategy_blocks_the_run: with the multiselect you could
    # deselect everything and had to be blocked by a guard. The primary is a REQUIRED
    # dropdown now (item 5), so the empty state cannot be reached at all — a strictly
    # stronger guarantee. Both halves are asserted: unreachable in the UI, and still
    # refused by the pure guard if any future surface manages it.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    picker = _strategy_picker(at)
    assert picker.value in picker.options            # always exactly one primary
    assert "" not in picker.options                  # no "none of them" option to pick
    assert "at least one strategy" in " ".join(
        app.run_problems(["AAPL"], n_strategies=0, deterministic=True, has_key=False))
