"""CONFIRM-SPEND-1 — never spend without a confirmation carrying the EXACT number.

The pre-run estimate could only ever be an upper bound: the narrated set is the UNION of
every lens's BUYs, and how far five lenses overlap is not predictable (24 verdicts over 14
names on growth_40 — a property of those lenses on that cohort, not a constant). Quoting a
ceiling as though it were the price is the thing this closes.

The fix surfaces a seam that already existed. The ranking pass runs before any narration
anyway, it is free, and it is what turns the bound into the figure. So:

  phase one  — click Run in a narrating mode: rank immediately, no confirmation;
  the seam   — show the EXACT count, the EXACT estimate and the names;
  phase two  — spend only from a button carrying that figure, CONSUMING phase one's
               result: no re-rank, no re-fetch.

"Keep the free ranking" reports the run as a normal ranker-only run — the ranking is done
and is never thrown away. Ranker-only mode never sees any of this.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from aristos_council import pipeline
from aristos_council.pipeline import (
    narrate_multi_strategy,
    narrate_rank_result,
    narrated_union,
    narration_plan,
    run_multi_strategy_pipeline,
    run_rank_pipeline,
)
from aristos_council.reproducibility import estimate_cost

from tests.test_multi_strategy_run import (
    RAW, SCREENED, STRAT_DIR, TODAY, UNIVERSE, _Adapter,
)
from tests.test_narration_union import MOMENTUM, _CountingRunners

_APP = Path(__file__).resolve().parents[1] / "app.py"
_RUN = datetime(2026, 8, 24, 11, 49, tzinfo=timezone.utc)


def _ranked_multi(ids=(SCREENED, RAW, MOMENTUM)):
    return run_multi_strategy_pipeline(
        UNIVERSE, list(ids), strategies_dir=STRAT_DIR, adapter=_Adapter(), today=TODAY)


def _ranked_single(sid=SCREENED):
    return run_rank_pipeline(UNIVERSE, sid, ranker_only=True, strategies_dir=STRAT_DIR,
                             adapter=_Adapter(), today=TODAY)


# --------------------------------------------------------------------------- #
# 1. PHASE ONE — the ranking pass is free
# --------------------------------------------------------------------------- #
def test_the_ranking_pass_makes_zero_llm_calls():
    runners = _CountingRunners()
    multi = run_multi_strategy_pipeline(
        UNIVERSE, [SCREENED, RAW], strategies_dir=STRAT_DIR, adapter=_Adapter(),
        today=TODAY, runners=runners)
    single = run_rank_pipeline(UNIVERSE, SCREENED, ranker_only=True,
                               strategies_dir=STRAT_DIR, adapter=_Adapter(),
                               today=TODAY, runners=runners)
    assert runners.call_count == 0
    assert multi.narratives == {} and single.narratives == {}


def test_the_confirm_step_shows_a_count_equal_to_the_TRUE_union():
    """Exact, because the ranking has already happened — no bound, no coefficient."""
    result = _ranked_multi()
    plan = narration_plan(result)
    union = narrated_union(result)

    assert plan["count"] == len(union)
    assert plan["names"] == union
    assert plan["est_cost"] == estimate_cost(len(union))
    assert plan["basis"] == "every name rated BUY by at least one lens"
    # ...and it is genuinely smaller than the verdict count, which is the whole point
    verdicts = sum(1 for row in result.rows for c in row.cells.values()
                   if c.status == "ranked" and c.verdict == "buy")
    assert plan["count"] < verdicts


def test_the_single_lens_plan_is_that_lenses_own_shortlist():
    result = _ranked_single()
    plan = narration_plan(result)
    assert plan["names"] == list(result.meta["shortlist"])
    assert plan["count"] == len(result.meta["shortlist"])
    assert plan["est_cost"] == estimate_cost(plan["count"])


# --------------------------------------------------------------------------- #
# 2. PHASE TWO — consumes phase one, never re-ranks
# --------------------------------------------------------------------------- #
def test_confirming_makes_exactly_one_call_per_distinct_name():
    result = _ranked_multi()
    runners = _CountingRunners()
    narrated = narrate_multi_strategy(result, adapter=_Adapter(), runners=runners)

    plan = narration_plan(result)
    assert runners.call_count == plan["count"]
    assert sorted(narrated.narratives) == sorted(plan["names"])


def test_confirming_does_NOT_re_run_the_ranker(monkeypatch):
    """The ranking is phase one's; phase two consumes it. Asserted by making a second
    ranking pass EXPLODE."""
    result = _ranked_multi([SCREENED, RAW])

    def _boom(*a, **kw):                                   # pragma: no cover
        raise AssertionError("phase two re-ran the ranker")

    monkeypatch.setattr(pipeline, "run_multi_strategy_pipeline", _boom)
    monkeypatch.setattr(pipeline, "_rank_stage", _boom)
    monkeypatch.setattr(pipeline, "_build_adapter", _boom)   # nor re-fetched anything

    narrated = narrate_multi_strategy(result, adapter=_Adapter(),
                                      runners=_CountingRunners())
    assert narrated.narratives


def test_phase_two_returns_the_SAME_grading_it_was_confirmed_on():
    """The figure the user confirmed and the run they paid for must describe the same
    ranking — so the verdicts, ranks and rows are the same objects."""
    result = _ranked_multi()
    narrated = narrate_multi_strategy(result, adapter=_Adapter(),
                                      runners=_CountingRunners())

    assert narrated.rows is result.rows
    assert narrated.results is result.results
    assert narrated.strategy_ids == result.strategy_ids
    assert narration_plan(narrated)["names"] == narration_plan(result)["names"]
    assert narrated.meta["ranker_only"] is False
    assert narrated.meta["est_cost"] == narration_plan(result)["est_cost"]


def test_single_lens_phase_two_also_consumes_rather_than_re_ranks(monkeypatch):
    result = _ranked_single()

    def _boom(*a, **kw):                                   # pragma: no cover
        raise AssertionError("phase two re-ran the ranker")

    monkeypatch.setattr(pipeline, "_rank_stage", _boom)
    monkeypatch.setattr(pipeline, "_build_adapter", _boom)

    runners = _CountingRunners()
    narrated = narrate_rank_result(result, adapter=_Adapter(), runners=runners)
    assert runners.call_count == len(result.meta["shortlist"])
    assert sorted(narrated.narratives) == sorted(result.meta["shortlist"])
    assert [r.ticker for r in narrated.ranked] == [r.ticker for r in result.ranked]
    assert [r.verdict for r in narrated.ranked] == [r.verdict for r in result.ranked]


def test_narrating_an_empty_plan_spends_nothing_and_changes_nothing():
    result = run_multi_strategy_pipeline(
        ["DEAD"], [SCREENED], strategies_dir=STRAT_DIR, adapter=_Adapter(), today=TODAY)
    runners = _CountingRunners()
    assert narration_plan(result)["count"] == 0
    out = narrate_multi_strategy(result, adapter=_Adapter(), runners=runners)
    assert runners.call_count == 0
    assert out is result


# --------------------------------------------------------------------------- #
# 3. THE UI — the seam, the two buttons, and the untouched ranker-only path
# --------------------------------------------------------------------------- #
def _redirect_reports(monkeypatch, tmp_path):
    """Send the run sink's output to ``tmp_path``.

    AppTest re-executes app.py as a separate module object, so patching
    ``app.UNIVERSE_RUNS_DIR`` never reaches the script under test. ``save_universe_run``
    is a shared import, so wrapping THAT does."""
    from aristos_council.persistence import universe_runs as sink

    real = sink.save_universe_run

    def _to_tmp(md_bytes, html_bytes, *, md_name, html_name, out_dir):
        return real(md_bytes, html_bytes, md_name=md_name, html_name=html_name,
                    out_dir=tmp_path)

    monkeypatch.setattr(sink, "save_universe_run", _to_tmp)


def _ss(at, key, default=None):
    """AppTest's session_state has no .get() — this is the equivalent."""
    return at.session_state[key] if key in at.session_state else default


def _run_tab(timeout: int = 120):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=timeout).run()
    assert not at.exception, at.exception
    return at


def _pick_cohort(at):
    lists = next(s for s in at.selectbox if str(s.label) == "List")
    lists.set_value(next(o for o in lists.options if "names" in o)).run()
    return at


def _mode(at):
    return next(r for r in at.radio if str(r.label) == "Run mode")


def test_ranker_only_mode_never_shows_the_confirmation_step():
    """One click, no confirmation, no extra state — unchanged."""
    pytest.importorskip("streamlit")
    import app

    at = _pick_cohort(_run_tab())
    _mode(at).set_value(app.RUN_MODE_RANKER).run()
    assert _mode(at).value == app.RUN_MODE_RANKER
    heads = " ".join(str(getattr(h, "value", "")) for h in at.subheader)
    assert "confirm the narration spend" not in heads
    assert not any(b.label.startswith("▶ Narrate") for b in at.button)
    assert _ss(at, "uni_pending_narration") is None


def test_the_confirmation_carries_the_exact_count_and_estimate_and_the_names():
    """The rendered step, driven from a completed ranking held in session state."""
    pytest.importorskip("streamlit")
    import app

    result = _ranked_multi()
    plan = narration_plan(result)
    at = _run_tab()
    at.session_state["uni_pending_narration"] = {
        "kind": "multi", "result": result, "mode": "narrator",
        "coverage": "buys_only"}
    at.session_state["uni_run_start"] = _RUN
    at.run()
    assert not at.exception

    blob = " ".join(str(getattr(m, "value", "")) for m in at.markdown)
    assert f"{plan['count']} names rated BUY by at least one lens" in blob
    # COST-2: the figure names its scope — total, one charge, and the per-name rate.
    assert f"narrate all {plan['count']} for" in blob
    assert "total (one charge, about \$" in blob
    assert f"\${plan['est_cost']:.2f} total" in blob      # $-escaped for markdown
    assert "up to" not in blob                              # no bound, no coefficient
    # the NAMES are listed
    written = " ".join(str(getattr(w, "value", "")) for w in at.markdown) + \
        " ".join(str(getattr(w, "value", "")) for w in getattr(at, "text", []))
    labels = {row.ticker: row.display for row in result.rows}
    body = written + " ".join(str(getattr(e, "value", "")) for e in at.get("markdown"))
    for ticker in plan["names"]:
        assert labels[ticker] in body or ticker in body

    assert any(b.label == f"▶ Narrate — \${plan['est_cost']:.2f} total"
               for b in at.button)
    assert any(b.label == "Keep the free ranking" for b in at.button)


def test_keeping_the_free_ranking_reports_it_and_re_runs_NOTHING(
        tmp_path, monkeypatch):
    """The ranking is DONE and, as of 2026-08-26, already REPORTED by the time the offer
    is on screen. "Keep" therefore only dismisses the offer: it must not re-rank, must
    not re-persist, and must not touch the files already written."""
    pytest.importorskip("streamlit")
    import app

    _redirect_reports(monkeypatch, tmp_path)
    result = _ranked_multi()
    at = _run_tab()
    at.session_state["uni_pending_narration"] = {
        "kind": "multi", "result": result, "mode": "narrator",
        "coverage": "buys_only"}
    at.session_state["uni_run_start"] = _RUN
    at.session_state["uni_universe_display_name"] = "Growth 40"
    at.run()
    # phase one's half of the state: the ranking reported and its pair on disk
    paths = app._persist_multi_strategy_run(result, _RUN, "Growth 40")
    at.session_state["uni_multi_result"] = result
    at.session_state["uni_multi_persisted"] = paths
    at.run()

    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert len(before) == 2
    # the finished ranking is downloadable HERE, beside the narrate/keep choice
    assert len([d for d in at.get("download_button")
                if "the free ranking" in str(d.label)]) == 2

    next(b for b in at.button if b.label == "Keep the free ranking").click().run()
    assert not at.exception

    assert _ss(at, "uni_pending_narration") is None          # the offer is dismissed...
    published = _ss(at, "uni_multi_result")
    assert published is result                               # ...the SAME ranking stands
    assert published.narratives == {}                        # ...nothing was narrated
    after = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert after == before, "keeping re-wrote the report instead of leaving it alone"

    md = next(v for k, v in after.items() if k.endswith(".md")).decode("utf-8")
    for heading in ("Rules applied", "Verdict by lens"):
        assert heading in md
    assert "## Narration" not in md


def test_confirming_narrates_and_publishes_without_re_ranking(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    import app

    _redirect_reports(monkeypatch, tmp_path)
    result = _ranked_multi()
    runners = _CountingRunners()
    plan = narration_plan(result)

    # phase two is invoked with the HELD result; the fake runners stand in for the LLM
    real = pipeline.narrate_multi_strategy
    seen = {}

    def _spy(res, **kw):
        seen["result"] = res
        return real(res, adapter=_Adapter(), runners=runners,
                    coverage=kw.get("coverage", "buys_only"))

    monkeypatch.setattr(app_pipeline_ref(), "narrate_multi_strategy", _spy)

    at = _run_tab()
    at.session_state["uni_pending_narration"] = {
        "kind": "multi", "result": result, "mode": "narrator",
        "coverage": "buys_only"}
    at.session_state["uni_run_start"] = _RUN
    at.session_state["uni_universe_display_name"] = "Growth 40"
    at.run()
    next(b for b in at.button
         if b.label == f"▶ Narrate — \${plan['est_cost']:.2f} total").click().run()
    assert not at.exception

    assert seen["result"] is result                         # consumed, not re-ranked
    assert runners.call_count == plan["count"]              # one call per distinct name
    published = _ss(at, "uni_multi_result")
    assert sorted(published.narratives) == sorted(plan["names"])
    md = next(p for p in tmp_path.iterdir() if p.suffix == ".md").read_text(
        encoding="utf-8")
    assert "## Narration" in md


def app_pipeline_ref():
    from aristos_council import pipeline as p
    return p


def test_a_plan_with_nothing_to_narrate_never_asks_for_a_confirmation(monkeypatch,
                                                                      tmp_path):
    """No spend to confirm -> do not ask; report the ranking and say so."""
    pytest.importorskip("streamlit")
    import app

    _redirect_reports(monkeypatch, tmp_path)
    result = run_multi_strategy_pipeline(
        ["DEAD"], [SCREENED], strategies_dir=STRAT_DIR, adapter=_Adapter(), today=TODAY)
    at = _run_tab()
    at.session_state["uni_pending_narration"] = {
        "kind": "multi", "result": result, "mode": "narrator",
        "coverage": "buys_only"}
    at.session_state["uni_run_start"] = _RUN
    at.run()
    assert not at.exception
    assert _ss(at, "uni_pending_narration") is None
    assert not any(b.label.startswith("▶ Narrate") for b in at.button)
    infos = " ".join(str(getattr(i, "value", "")) for i in at.info)
    assert "nothing was charged" in infos


# --------------------------------------------------------------------------- #
# 4. THE 2026-08-25 LIVE FAILURE — the flow through the BUTTONS, end to end
# --------------------------------------------------------------------------- #
# Everything above drives phase two by seeding ``uni_pending_narration`` directly, which
# is exactly why the flow shipped broken: the RUN -> CONFIRM transition was never
# exercised, and that is where it failed. A 21-name adhoc cohort under three lenses with
# Narrator selected produced a ranker-only report, no narration section and ZERO LLM
# calls, twice.
#
# The cause was neither of the obvious candidates. Phase two was wired correctly and
# re-persisted correctly; it was never REACHED, because the run mode in force was not the
# one the user had picked. ``uni_run_mode_touched`` was set from the radio's on_change,
# and Streamlit fires on_change only when the value CHANGES — so selecting the option
# already displayed (Narrator, on a one-lens run) left the control marked untouched, and
# ticking a second lens silently re-defaulted it to ranker-only.
#
# These tests drive the widgets in that order, so a re-default can never again hide
# behind a directly-seeded session state.
def _tick_two_lenses(at):
    boxes = [c for c in at.checkbox if c.key and c.key.startswith("uni_lens_")]
    for c in boxes[:2]:
        at.session_state[c.key] = True
    at.run()
    return at


def test_selecting_narrator_then_adding_lenses_KEEPS_narrator():
    """The live bug, at its root. Selecting the option already shown fires no on_change,
    so intent must not depend on having changed the value."""
    pytest.importorskip("streamlit")
    import app

    at = _run_tab()
    at.session_state["uni_coverage"] = "buys_only"
    _mode(at).set_value(app.RUN_MODE_NARRATOR).run()
    assert _mode(at).value == app.RUN_MODE_NARRATOR

    _tick_two_lenses(at)
    assert _mode(at).value == app.RUN_MODE_NARRATOR, (
        "ticking a lens silently re-defaulted the run mode")
    assert app.run_mode_narrates(_mode(at).value)
    # ...and the button offers narration, rather than reading "deterministic, free"
    assert "narrate" in next(b for b in at.button if b.key == "uni_run").label


def test_the_mode_is_never_re_defaulted_by_the_lens_count_in_either_direction():
    """Seeded once, then the user's — ticking AND unticking."""
    pytest.importorskip("streamlit")
    import app

    at = _run_tab()
    at.session_state["uni_coverage"] = "buys_only"
    _mode(at).set_value(app.RUN_MODE_NARRATOR).run()
    _tick_two_lenses(at)
    assert _mode(at).value == app.RUN_MODE_NARRATOR

    for c in [c for c in at.checkbox if c.key and c.key.startswith("uni_lens_")][:2]:
        at.session_state[c.key] = False
    at.run()
    assert _mode(at).value == app.RUN_MODE_NARRATOR


def _drive_two_phase(monkeypatch, tmp_path, *, confirm: bool):
    """Click Run in narrator mode over three lenses, then click one of the two buttons.
    Returns (AppTest, counting runners, files written)."""
    import app
    from aristos_council.agents import runners as runners_mod
    from aristos_council.persistence import universe_runs as sink

    counter = _CountingRunners()
    monkeypatch.setattr(pipeline, "_build_adapter", lambda *a, **kw: _Adapter())
    monkeypatch.setattr(runners_mod, "production_runners", lambda *a, **kw: counter)
    real = sink.save_universe_run
    monkeypatch.setattr(sink, "save_universe_run", lambda md, html, *, md_name,
                        html_name, out_dir: real(md, html, md_name=md_name,
                                                 html_name=html_name, out_dir=tmp_path))

    at = _run_tab(timeout=300)
    at.session_state["uni_coverage"] = "buys_only"
    # COST-2: this fixture is ABOUT the confirm panel, so it pins the threshold at 0
    # ("always ask"). The under-threshold path — where one click both ranks and narrates
    # — is exercised by its own tests below.
    at.session_state["uni_confirm_threshold"] = 0.0
    at.session_state["uni_tickers"] = "\n".join(UNIVERSE)
    _mode(at).set_value(app.RUN_MODE_NARRATOR).run()
    _tick_two_lenses(at)
    assert app.run_mode_narrates(_mode(at).value)

    next(b for b in at.button if b.key == "uni_run").click().run()
    assert not at.exception, at.exception
    # PHASE ONE is free and REPORTS the ranking (2026-08-26): downloading the finished
    # ranking and narrating it are not alternatives, so the pair is written and offered
    # in the confirm panel. It still costs nothing, and it is still ONE pair.
    assert counter.call_count == 0
    phase_one = sorted(p.name for p in tmp_path.glob("*"))
    assert len(phase_one) == 2, phase_one
    assert all("_ranker_" in n for n in phase_one), phase_one
    # ...and those files are downloadable right there, before any narration decision
    offered = [d for d in at.get("download_button")
               if "the free ranking" in str(d.label)]
    assert len(offered) == 2, [str(d.label) for d in at.get("download_button")]

    key = "uni_confirm_narrate" if confirm else "uni_keep_ranking"
    next(b for b in at.button if b.key == key).click().run()
    assert not at.exception, at.exception
    return at, counter, sorted(tmp_path.glob("*"))


def test_confirming_narrates_and_the_FILE_ON_DISK_carries_the_narration(monkeypatch,
                                                                       tmp_path):
    """Read the file back — not session state. The live failure was invisible in memory:
    the report on disk was the thing that stayed ranker-only."""
    pytest.importorskip("streamlit")
    at, counter, files = _drive_two_phase(monkeypatch, tmp_path, confirm=True)

    expected = narration_plan(_ranked_multi([SCREENED, RAW, MOMENTUM]))["count"]
    assert counter.call_count == expected > 0

    md = next(p for p in files if p.suffix == ".md")
    text = md.read_text(encoding="utf-8")
    assert "## Narration" in text
    # every narrated name has its own section
    for ticker in narrated_union(at.session_state["uni_multi_result"]):
        assert ticker in text

    # ...and the document says what actually ran, in the header AND in the filename
    assert "ranker only" not in text
    assert "Narrative: none" not in text
    assert "_ranker_" not in md.name
    assert "_narrator_" in md.name


def test_one_run_leaves_exactly_one_md_and_one_html(monkeypatch, tmp_path):
    pytest.importorskip("streamlit")        # drives the UI; CI has test deps only
    _, _, files = _drive_two_phase(monkeypatch, tmp_path, confirm=True)
    assert [p.suffix for p in files].count(".md") == 1
    assert [p.suffix for p in files].count(".html") == 1
    assert len(files) == 2


def test_keeping_the_free_ranking_writes_a_ranker_report_with_zero_calls(monkeypatch,
                                                                        tmp_path):
    pytest.importorskip("streamlit")
    _, counter, files = _drive_two_phase(monkeypatch, tmp_path, confirm=False)

    assert counter.call_count == 0
    assert len(files) == 2
    md = next(p for p in files if p.suffix == ".md")
    assert "_ranker_" in md.name
    text = md.read_text(encoding="utf-8")
    assert "## Narration" not in text
    assert "no LLM ran" in text          # and it SAYS so, rather than claiming narration
