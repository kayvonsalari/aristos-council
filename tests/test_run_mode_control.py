"""RUNMODE-1 — the run controls must never display a value they are not using.

THE BUG: with two or more lenses ticked, the code forced ``ranker_only = True``
internally while the "Ranker only — no LLM, no cost" checkbox rendered DISABLED AND
UNCHECKED and "Council mode" still displayed "narrator". The screen said narration would
run; the run was deterministic. The VALUES were right — nothing narrated and nothing was
charged — but a disabled widget showing a value other than the one in force is worse than
no widget, because a reader has no way to know which half is lying.

"Ranker only" and "Council mode" were two controls for ONE decision. They are now one
selector, and the invariant these tests pin is blunt: **displayed == effective**, for
every control, in every state.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app

_APP = Path(__file__).resolve().parents[1] / "app.py"


# --------------------------------------------------------------------------- #
# 1. The pure mapping — unit-tested rather than eyeballed in a browser
#    (the discipline `run_problems` already established).
# --------------------------------------------------------------------------- #
def test_several_lenses_DEFAULT_to_ranker_only_but_are_no_longer_forced():
    """NARR-UNION-1 relaxed the multi-lens LOCK to a DEFAULT: a cross-lens narration now
    has a defined meaning (one section per NAME over the union of every lens's BUYs), so
    the mode became the user's to choose. Ranker-only stays the default because it is
    free — but choosing Narrator is honoured, not silently overridden."""
    for n in (2, 3, 5):
        assert app.default_run_mode(n) == app.RUN_MODE_RANKER
        assert app.run_mode_locked(n) is False
        for mode in app.RUN_MODES:                    # whatever is chosen is what runs
            assert app.effective_run_mode(mode, n_strategies=n) == mode


def test_one_lens_keeps_whatever_the_user_chose():
    for mode in app.RUN_MODES:
        assert app.effective_run_mode(mode, n_strategies=1) == mode
    assert app.run_mode_locked(1) is False
    assert app.default_run_mode(1) == app.RUN_MODE_NARRATOR


def test_an_unknown_mode_falls_back_to_the_default_rather_than_crashing():
    assert app.effective_run_mode("", n_strategies=1) == app.RUN_MODE_NARRATOR
    assert app.effective_run_mode("nonsense", n_strategies=1) == app.RUN_MODE_NARRATOR


def test_each_mode_maps_onto_the_existing_pipeline_arguments():
    """UI layer only — the pipeline's signature and behaviour are untouched."""
    assert app.run_mode_arguments(app.RUN_MODE_RANKER) == (True, "narrator")
    assert app.run_mode_arguments(app.RUN_MODE_NARRATOR) == (False, "narrator")
    assert app.run_mode_arguments(app.RUN_MODE_SECOND_OPINION) == \
        (False, "second_opinion")
    # ranker-only passes the SAME inert council_mode the old disabled selectbox handed
    # the pipeline, so that call is byte-unchanged.
    assert app.run_mode_arguments(app.RUN_MODE_RANKER)[1] == "narrator"


def test_only_the_narrating_modes_spend():
    assert app.run_mode_narrates(app.RUN_MODE_RANKER) is False
    assert app.run_mode_narrates(app.RUN_MODE_NARRATOR) is True
    assert app.run_mode_narrates(app.RUN_MODE_SECOND_OPINION) is True


def test_the_button_says_what_will_happen_and_what_it_costs():
    assert app.run_button_label(app.RUN_MODE_RANKER, n_strategies=5) == \
        "▶ Run 5 lenses — deterministic, free"
    assert app.run_button_label(app.RUN_MODE_RANKER, n_strategies=1) == \
        "▶ Run — deterministic, free"
    assert app.run_button_label(app.RUN_MODE_NARRATOR, n_strategies=1,
                                est_cost=0.42) == "▶ Run — narrated, est. $0.42"
    assert app.run_button_label(app.RUN_MODE_SECOND_OPINION, n_strategies=1,
                                est_cost=1.2) == "▶ Run — second opinion, est. $1.20"
    # no estimate available (empty/oversized list) -> the claim is omitted, not invented
    assert app.run_button_label(app.RUN_MODE_NARRATOR, n_strategies=1) == \
        "▶ Run — narrated"


def test_every_mode_has_a_label_and_they_are_distinct():
    assert set(app.RUN_MODE_LABELS) == set(app.RUN_MODES)
    assert len(set(app.RUN_MODE_LABELS.values())) == len(app.RUN_MODES)
    assert all(label.strip() for label in app.RUN_MODE_LABELS.values())


# --------------------------------------------------------------------------- #
# 2. THE INVARIANT, in the rendered app: displayed == effective.
#    This is the test that fails on the old two-control flow.
# --------------------------------------------------------------------------- #
def _run_tab(*, extra_lens: str | None = None, timeout: int = 90):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(_APP), default_timeout=timeout).run()
    assert not at.exception
    if extra_lens is not None:
        _lens_checkbox(at, extra_lens).set_value(True).run()
        assert not at.exception
    return at


def _strategy_picker(at):
    return next(s for s in at.selectbox if "strateg" in str(s.label).lower())


def _lens_checkbox(at, label: str):
    return next(c for c in at.checkbox if str(c.label) == label)


def _run_mode_widget(at):
    return next(r for r in at.radio if str(r.label) == "Run mode")


def _run_button(at):
    return next(b for b in at.button if b.label.startswith("▶ Run"))


def test_a_second_lens_defaults_to_ranker_only_AND_displays_it_as_selected():
    """The regression this control exists for. On the old flow the effective value was
    ranker-only while the control displayed an UNCHECKED "Ranker only" box and a
    "narrator" mode — displayed != effective, in both controls at once. It is now the
    DEFAULT rather than a lock (NARR-UNION-1), and the invariant is unchanged: what is
    shown is what runs."""
    pytest.importorskip("streamlit")
    at = _run_tab()
    raw = next(o for o in _strategy_picker(at).options if "RAW" in o)
    at = _run_tab(extra_lens=raw)

    widget = _run_mode_widget(at)
    displayed = widget.value
    effective = app.effective_run_mode(displayed, n_strategies=2)

    assert displayed == app.RUN_MODE_RANKER          # SHOWN as selected...
    assert displayed == effective                    # ...and it is the value in force
    assert widget.disabled is False                  # ...and the user may change it


def test_no_disabled_control_in_the_run_flow_displays_a_value_it_is_not_using():
    """The general form of the bug: a greyed widget is still read. If it is disabled, the
    value it shows must be the value in force."""
    pytest.importorskip("streamlit")
    at = _run_tab()
    raw = next(o for o in _strategy_picker(at).options if "RAW" in o)
    at = _run_tab(extra_lens=raw)

    effective = app.RUN_MODE_RANKER
    ranker_only, council_mode = app.run_mode_arguments(effective)
    assert ranker_only is True

    for widget in list(at.radio) + list(at.selectbox) + list(at.checkbox):
        if not getattr(widget, "disabled", False):
            continue
        if str(widget.label) == "Run mode":
            assert widget.value == effective
        else:
            # no OTHER disabled control in the flow carries a run-mode value at all
            assert str(widget.value) not in ("narrator", "second_opinion"), widget.label


def test_narration_coverage_is_hidden_not_greyed_when_nothing_narrates():
    """A greyed control still invites a reading it cannot support."""
    pytest.importorskip("streamlit")
    at = _run_tab()
    raw = next(o for o in _strategy_picker(at).options if "RAW" in o)
    at = _run_tab(extra_lens=raw)
    assert _run_mode_widget(at).value == app.RUN_MODE_RANKER      # nothing narrates
    assert not any(str(s.label) == "Narration coverage" for s in at.selectbox)


def test_choosing_narrator_on_a_multi_lens_run_brings_coverage_back():
    """NARR-UNION-1: several lenses CAN narrate now, so the coverage control returns the
    moment the mode does."""
    pytest.importorskip("streamlit")
    at = _run_tab()
    raw = next(o for o in _strategy_picker(at).options if "RAW" in o)
    at = _run_tab(extra_lens=raw)
    _run_mode_widget(at).set_value(app.RUN_MODE_NARRATOR).run()
    assert _run_mode_widget(at).value == app.RUN_MODE_NARRATOR     # honoured, not forced
    assert any(str(s.label) == "Narration coverage" for s in at.selectbox)
    captions = " ".join(str(getattr(c, "value", "")) for c in at.caption)
    assert "ONE section per NAME" in captions or "narrated once" in captions


def test_the_button_states_the_deterministic_free_run_when_lenses_are_ticked():
    pytest.importorskip("streamlit")
    at = _run_tab()
    raw = next(o for o in _strategy_picker(at).options if "RAW" in o)
    at = _run_tab(extra_lens=raw)
    assert _run_button(at).label == "▶ Run 2 lenses — deterministic, free"


def test_the_button_states_the_NAME_COUNT_and_cost_before_a_narrated_multi_lens_run():
    """NARR-UNION-1's cost guard, at the point of decision: the bill is proportional to
    the number of NAMES narrated, so that is what the button says — BEFORE the click."""
    pytest.importorskip("streamlit")
    at = _run_tab()
    raw = next(o for o in _strategy_picker(at).options if "RAW" in o)
    at = _run_tab(extra_lens=raw)
    # a cohort is needed for an estimate to exist at all (an empty list omits it rather
    # than inventing one) — pick the first saved list.
    lists = next(s for s in at.selectbox if str(s.label) == "List")
    lists.set_value(next(o for o in lists.options if "names" in o)).run()  # not "New list"
    _run_mode_widget(at).set_value(app.RUN_MODE_NARRATOR).run()
    label = _run_button(at).label
    assert label.startswith("▶ Run 2 lenses — ")
    # stated as the CEILING it is: the true union is only knowable after the free
    # ranking pass, and there is no non-invented way to predict how far the lenses overlap.
    assert "up to" in label and "names narrated" in label
    assert "est. ≤ $" in label


def test_a_single_lens_narrated_run_is_unchanged():
    pytest.importorskip("streamlit")
    at = _run_tab()
    widget = _run_mode_widget(at)
    assert widget.value == app.RUN_MODE_NARRATOR         # the default, as before
    assert widget.disabled is False
    assert any(str(s.label) == "Narration coverage" for s in at.selectbox)
    assert _run_button(at).label.startswith("▶ Run — narrated")
    assert app.run_mode_arguments(widget.value) == (False, "narrator")


def test_deselecting_the_extra_lens_restores_the_mode_rather_than_stranding_the_user():
    """The forced multi-lens control renders under its OWN key, so it never clobbers the
    single-lens choice — untick the lens and the mode you picked comes back."""
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(_APP), default_timeout=90).run()
    assert not at.exception
    raw = next(o for o in _strategy_picker(at).options if "RAW" in o)

    _run_mode_widget(at).set_value(app.RUN_MODE_SECOND_OPINION).run()
    assert _run_mode_widget(at).value == app.RUN_MODE_SECOND_OPINION

    # NARR-UNION-1: a second lens no longer OVERRIDES the choice, so second-opinion
    # simply stands — and still stands when the lens is unticked.
    _lens_checkbox(at, raw).set_value(True).run()
    assert _run_mode_widget(at).value == app.RUN_MODE_SECOND_OPINION

    _lens_checkbox(at, raw).set_value(False).run()
    assert _run_mode_widget(at).value == app.RUN_MODE_SECOND_OPINION


# --------------------------------------------------------------------------- #
# 3. NO LLM MAY EVER BE INVOKED BY A MULTI-LENS RUN
# --------------------------------------------------------------------------- #
def test_a_multi_lens_RANKER_ONLY_run_never_reaches_the_narration_entry_point(monkeypatch):
    """The guardrail that survives NARR-UNION-1: a multi-lens run may narrate now, but a
    RANKER-ONLY one (still the default) must make ZERO LLM calls — asserted by making the
    narration stage EXPLODE if it is ever entered."""
    from datetime import date

    from aristos_council import pipeline
    from tests.test_multi_strategy_run import (
        RAW, SCREENED, STRAT_DIR, TODAY, UNIVERSE, _Adapter,
    )

    def _boom(*a, **kw):                       # pragma: no cover - must never run
        raise AssertionError("a multi-lens run reached the council/narration stage")

    monkeypatch.setattr(pipeline, "_council_stage", _boom)
    monkeypatch.setattr(pipeline, "production_runners", _boom, raising=False)

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
