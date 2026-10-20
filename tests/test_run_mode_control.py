"""RUNMODE-1 — the pure run-mode mapping and the guarantee that a ranker-only multi-lens run
never reaches the LLM. (The 3-way "Run mode" radio and the AppTest checks that drove it went with
the "Show validation tools" toggle, VALIDATION-TOOLS-REMOVE-1; the run mode is now derived from
the "Council opinion" tick box.)"""

from __future__ import annotations

import pytest

# CI installs test deps only — no streamlit, no network providers. ``app`` imports
# streamlit at module scope, so importing it unguarded turns a skip into a COLLECTION
# ERROR and takes the whole module down with it. Every other UI test module already
# guards it this way; this one did not, and CI caught what a 3.14 dev box could not.
pytest.importorskip("streamlit")

import app  # noqa: E402


# --------------------------------------------------------------------------- #
# 1. The pure mapping — unit-tested rather than eyeballed in a browser
#    (the discipline `run_problems` already established).
# --------------------------------------------------------------------------- #
def test_a_run_mode_is_never_locked():
    """NARR-UNION-1 relaxed the multi-lens LOCK: nothing forces the mode."""
    for n in (1, 2, 3, 5):
        assert app.run_mode_locked(n) is False


def test_each_mode_maps_onto_the_existing_pipeline_arguments():
    """UI layer only — the pipeline's signature and behaviour are untouched."""
    assert app.run_mode_arguments(app.RUN_MODE_RANKER) == (True, "narrator")
    assert app.run_mode_arguments(app.RUN_MODE_NARRATOR) == (False, "narrator")
    # ranker-only passes the SAME inert council_mode the old disabled selectbox handed
    # the pipeline, so that call is byte-unchanged.
    assert app.run_mode_arguments(app.RUN_MODE_RANKER)[1] == "narrator"


def test_only_the_narrating_modes_spend():
    assert app.run_mode_narrates(app.RUN_MODE_RANKER) is False
    assert app.run_mode_narrates(app.RUN_MODE_NARRATOR) is True


def test_the_button_says_what_will_happen_and_what_it_costs():
    assert app.run_button_label(app.RUN_MODE_RANKER, n_strategies=5) == \
        "▶ Run 5 lenses · free"
    assert app.run_button_label(app.RUN_MODE_RANKER, n_strategies=1) == \
        "▶ Run · free"
    # UPDATED (2026-08-26): this button runs the FREE ranking and charges nothing —
    # narration is a SECOND button carrying the exact figure. The old label read
    # "Run — narrated, est. $0.42", which reads as this button's price, and a second
    # button then appeared at a different one. "free" now sits against the action that is
    # free, and the upper bound reads as the conditional it is.
    assert app.run_button_label(app.RUN_MODE_NARRATOR, n_strategies=1, est_cost=0.42) == \
        "▶ Run — free · then choose whether to narrate · ≤ $0.42 total"
    # no estimate available (empty/oversized list) -> the claim is omitted, not invented
    assert app.run_button_label(app.RUN_MODE_NARRATOR, n_strategies=1) == \
        "▶ Run — free · then choose whether to narrate"
    # ...and in EVERY narrating shape the word "free" describes THIS click, while the
    # estimate is explicitly conditional on a choice not yet offered.
    for label in (app.run_button_label(app.RUN_MODE_NARRATOR, n_strategies=1,
                                       est_cost=0.42),
                  app.run_button_label(app.RUN_MODE_NARRATOR, n_strategies=3,
                                       est_cost=2.28, narrated_count=12)):
        assert "free" in label
        assert "then choose whether to" in label
        assert "≤ $" in label and "est. $" not in label
        # COST-2: every figure names its SCOPE — never a bare number that could be read
        # as per name or per lens.
        assert "total" in label


# --------------------------------------------------------------------------- #
# NO LLM MAY EVER BE INVOKED BY A MULTI-LENS RUN
# --------------------------------------------------------------------------- #
def test_a_multi_lens_RANKER_ONLY_run_never_reaches_the_narration_entry_point(monkeypatch):
    """The guardrail that survives NARR-UNION-1: a multi-lens run may narrate now, but a
    RANKER-ONLY one (still the default) must make ZERO LLM calls — asserted by making the
    narration stage EXPLODE if it is ever entered."""
    from datetime import date

    import aristos_council.agents.runners as runners_mod
    from aristos_council import pipeline
    from tests.test_multi_strategy_run import (
        RAW, SCREENED, STRAT_DIR, TODAY, UNIVERSE, _Adapter,
    )

    def _boom(*a, **kw):                       # pragma: no cover - must never run
        raise AssertionError("a multi-lens run reached the council/narration stage")

    monkeypatch.setattr(pipeline, "_council_stage", _boom)
    # The second barrier goes on runners_mod, not on pipeline. ``pipeline`` imports
    # production_runners INSIDE the function body (``from .agents.runners import
    # production_runners``), so the name it uses is looked up on that module at call time
    # and ``pipeline.production_runners`` is never read — it does not even exist, which is
    # why patching it there needed ``raising=False`` to avoid an error. The barrier was
    # decorative: it invented an attribute nobody looks at. test_narration_schema and
    # test_confirm_spend patch runners_mod, and that is the module the lookup resolves
    # through.
    monkeypatch.setattr(runners_mod, "production_runners", _boom)
    # ...and prove the barrier still reaches, by making the SAME import the pipeline makes.
    # If production_runners is ever imported eagerly at the top of pipeline.py, or moved to
    # another module, this fails loudly instead of the guard going quiet again.
    from aristos_council.agents.runners import production_runners as _as_pipeline_sees_it
    assert _as_pipeline_sees_it is _boom,         "the barrier no longer reaches the lookup pipeline.py makes"

    multi = pipeline.run_multi_strategy_pipeline(
        UNIVERSE, [SCREENED, RAW], strategies_dir=STRAT_DIR, adapter=_Adapter(),
        today=TODAY)

    assert multi.meta["council_mode"] == "ranker-only"
    for sid in multi.strategy_ids:
        res = multi.results[sid]
        assert res.meta["ranker_only"] is True
        assert res.narratives == {}
        assert res.council == []
    assert isinstance(date, type)              # (import used, keeps the fixture honest)
