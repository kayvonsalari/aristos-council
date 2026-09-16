"""CAPTION-1 — one plain-English line under every lens name.

Two runs on 2026-09-16 (oil_dividend_v1, 135 names; defensive_income_16_v1, 16) showed the
lenses answer DIFFERENT QUESTIONS, so a BUY on one beside a SELL on another is not a
contradiction — and the grid gave the reader nothing to tell them apart with. A verdict
without its question is not information.

``asks`` is deliberately distinct from every neighbouring string: ``name``/``display_name``
are what the lens is CALLED, ``role`` is where it sits in the line-up, ``description`` is
the paragraph and ``rationale`` is the argument for the method. This is the QUESTION.

Absent ``asks`` renders NOTHING, everywhere — so a strategy whose YAML has not gained the
field is byte-identical to before. That is asserted here and demonstrated inside the
report goldens, where the legacy ``magic_formula_v1`` carries no line.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aristos_council.pipeline import lens_asks
from aristos_council.strategy.discovery import visible_rank_strategies
from aristos_council.strategy.rank_loader import load_rank_strategy

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"

# The verbatim text each shipped lens asks. Pinned so a reword is a deliberate edit here,
# not a silent drift in a YAML — these sentences are what a reader meets beside a verdict.
EXPECTED = {
    "magic_formula_raw_v1":
        "Cheap and good: high profit on the money invested, a low price for that "
        "profit, and a rising share.",
    "magic_formula_momentum_v1":
        "The same as Magic Formula RAW, but only for companies earning at least 12% on "
        "their capital.",
    "growth_garp_v2":
        "Growing fast at a fair price: sales up at least 10% a year, and not overpaying "
        "for that growth.",
    "conservative_plus_v1":
        "Steady income with the trend intact: a calm share, a covered dividend raised "
        "for 10 years, and no recent fall.",
    "forensic_v1":
        "Are the profits real: cash behind the earnings, a safe balance sheet, and "
        "improving health checks.",
    "financials_v1":
        "Banks and insurers on their own terms: return on equity against price-to-book.",
    "etf_dividend_v1": "Distributing funds: payout, fee, size and trend.",
    "etf_growth_v1": "Growth funds: fee, trend and scale, no yield.",
    "etf_core_v1": "Index trackers: fee, size and trend.",
    # Added when feat/cyclical-income-1 was rebased onto SHORTLIST-1: the guard
    # `test_every_visible_lens_has_one` caught the new lens arriving without a question,
    # which is exactly what it is for.
    "cyclical_income_v1":
        "Income that survives the cycle: a covered dividend not cut in five years, with "
        "manageable debt.",
}


def _load(sid):
    return load_rank_strategy(STRAT_DIR / f"{sid}.yaml")


@pytest.mark.parametrize("sid", sorted(EXPECTED))
def test_each_shipped_lens_asks_its_question_verbatim(sid):
    path = STRAT_DIR / f"{sid}.yaml"
    if not path.exists():                      # a lens not on this branch yet
        pytest.skip(f"{sid} not present")
    assert load_rank_strategy(path).asks == EXPECTED[sid]


def test_every_visible_lens_has_one():
    """A lens a reader can PICK must say what it asks. Hidden/legacy configs may not."""
    missing = [s.id for s in visible_rank_strategies(STRAT_DIR)
               if not (load_rank_strategy(s.path).asks or "").strip()]
    assert missing == [], f"visible lenses with no `asks`: {missing}"


def test_the_sentences_are_plain_english_not_field_names():
    """The point is a reader who has not read the YAML. A sentence naming factor ids
    would be the thing it replaces."""
    for sid in sorted(EXPECTED):
        path = STRAT_DIR / f"{sid}.yaml"
        if not path.exists():
            continue
        asks = load_rank_strategy(path).asks
        assert asks[0].isupper() and asks.endswith("."), sid
        assert "_" not in asks, sid                      # no snake_case factor/criterion
        assert len(asks) <= 130, sid                     # one line, not a paragraph


def test_asks_is_distinct_from_the_strings_it_sits_beside():
    s = _load("conservative_plus_v1")
    assert s.asks and s.asks != s.description.strip()
    assert s.asks != s.rationale.strip()
    assert s.asks != s.role and s.asks != s.display_name


def test_a_strategy_without_asks_is_empty_not_an_error():
    """The legacy config carries none, and loading it must stay unremarkable."""
    assert _load("magic_formula_v1").asks == ""


# --------------------------------------------------------------------------- #
# The accessor every surface reads
# --------------------------------------------------------------------------- #
def test_lens_asks_reads_the_strategy_that_actually_ran():
    class _Res:
        rank_strategy = _load("forensic_v1")

    assert lens_asks(_Res()) == EXPECTED["forensic_v1"]


def test_lens_asks_is_empty_rather_than_raising_on_anything_missing():
    """One accessor, used by five surfaces — it must never be the thing that breaks a
    report. Empty string renders nothing, everywhere."""
    class _NoStrategy:
        rank_strategy = None

    class _Nothing:
        pass

    assert lens_asks(_NoStrategy()) == ""
    assert lens_asks(_Nothing()) == ""
    assert lens_asks(None) == ""


def test_lens_asks_strips_yaml_folding_whitespace():
    """`asks: >-` folds to a single line; a trailing newline must not reach a report."""
    for sid in ("forensic_v1", "etf_core_v1"):
        s = _load(sid)
        assert s.asks == s.asks.strip()
        assert "\n" not in s.asks


# --------------------------------------------------------------------------- #
# CAPTION-2 — the definition where the reader CHOOSES, not only after
# --------------------------------------------------------------------------- #
def test_the_caption_builder_returns_the_asks_text_or_empty():
    """CAPTION-1 put this sentence under lenses already chosen, which is the wrong moment:
    the question a lens asks is what you need in order to pick it."""
    pytest.importorskip("streamlit")
    import app

    assert app.lens_caption(_load("forensic_v1")) == EXPECTED["forensic_v1"]
    assert app.lens_caption(_load("magic_formula_v1")) == ""     # legacy, declares none
    assert app.lens_caption(None) == ""                          # never raises
