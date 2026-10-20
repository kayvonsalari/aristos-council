"""Batch 27A - honest evidence wording and the numbers behind it.

BADGE-WORDS-2 (renamed labels, verdicts untouched, pinned against a before-snapshot of every committed
pair), CHANCE-LINE-1 (the chance sentence, from the CSVs), UNTESTED-WHY-1 (the cause of an untested
badge), CONC-READ-1 (the drop-best sentence and the basket-overlap check). Everything runs on a temp
backtests dir except the pinned baseline, which reads the committed one.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from aristos_council import backtest as bt
from aristos_council.backtest import (BacktestResult, Badge, Round, add_months, evidence_summary,
                                      overlap_report, to_csv, track_record)

BASELINE = Path(__file__).parent / "fixtures" / "backtest_badges_baseline_2026-10-20.json"
COHORT = "Test Cohort"
SLUG = "test_cohort"


# --------------------------------------------------------------------------- #
# 27A-1 - the rename changes words only
# --------------------------------------------------------------------------- #
def test_every_committed_pair_keeps_its_verdict_and_badge_assignment():
    """98 (cohort, lens) pairs, snapshotted from main BEFORE the rename (old labels). After mapping
    the old words through the one dict, verdict and assignment are identical."""
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert len(baseline) == 98
    cache = bt._load_backtests()
    assert {f"{c}/{l}" for c, l in cache} == set(baseline)
    for (cohort, lens), result in cache.items():
        want = baseline[f"{cohort}/{lens}"]
        assert bt.verdict(result, max_luck=result.max_luck) == want["verdict"], (cohort, lens)
        assert track_record(cohort, lens).label == bt.canonical_label(want["label"]), (cohort, lens)


def test_the_legacy_mapping_is_one_dict_and_covers_every_renamed_label():
    assert bt.LEGACY_LABELS == {"proven here": "passed the test here",
                                "promising here": "passed the bar here, luck not ruled out",
                                "worked against you here": "behind random picks here"}
    assert bt.canonical_label("no edge shown here") == "no edge shown here"
    assert bt.canonical_label("untested here") == "untested here"
    assert Badge("proven here", "proven", 0.03, 0.02, 8, 10, 100).label == "passed the test here"


def test_no_old_wording_survives_in_the_meanings():
    for sentence in bt.BADGE_MEANINGS.values():
        assert "proven" not in sentence.lower()
    assert "not proof of a repeatable edge" in bt.BADGE_MEANINGS["passed the test here"]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _rounds(excess_by_year, *, n_buys=3, n_ranked=10, n_eligible=15, tickers=("A", "B", "C"),
            drop_best=None):
    out = []
    for year, ex in excess_by_year.items():
        for m in range(1, 13):
            d = date(year, m, 28)
            held = n_buys >= bt.MIN_BUYS
            out.append(Round(d, n_buys, 0.1 if held else None, 0.1 - ex if held else None,
                             ex if held else None, tickers if held else (), add_months(d, 12),
                             n_ranked, n_eligible, excess_drop_best=drop_best if held else None))
    return out


def _write(root, lens, years, *, luck=0.01, cohort=COHORT, luck_pass=0.1, vs_random=None, **kw):
    result = BacktestResult(cohort, lens, date(2016, 9, 1), date(2026, 9, 1),
                            rounds=_rounds(years, **kw), random_baskets=500, luck_pct_mean=luck,
                            luck_pct_pass=luck_pass, drop_best_vs_random=vs_random)
    to_csv(result, root)


PASS = {y: 0.03 for y in range(2016, 2026)}
FAIL = {y: -0.01 for y in range(2016, 2026)}


# --------------------------------------------------------------------------- #
# 27A-2 - the chance sentence
# --------------------------------------------------------------------------- #
def test_evidence_summary_counts_and_the_chance_expectations(tmp_path):
    _write(tmp_path, "a_v1", PASS, luck=0.02)                      # proven
    _write(tmp_path, "b_v1", PASS, luck=0.40)                      # bar, not beyond luck
    _write(tmp_path, "c_v1", FAIL, luck=0.50)                      # not proven
    _write(tmp_path, "d_v1", {2024: 0.05})                         # insufficient (1 year)
    ev = evidence_summary(tmp_path)
    assert (ev.measured, ev.bar_passed, ev.luck_passed) == (3, 2, 1)
    assert ev.bar_expected == pytest.approx(0.3)                   # 3 tests x luck_pct_pass 0.1
    assert ev.luck_expected == pytest.approx(3 * bt.MAX_LUCK)
    assert "2 of 3 cleared the bar" in ev.sentence() and "1 also passed the luck test" in ev.sentence()
    assert "exploratory, not proof of a repeatable edge" in ev.sentence()


def test_the_tails_flag_the_luck_test_as_approximate_only_past_twice_expectation(tmp_path):
    for i in range(10):                                            # all in the <5% tail: 10 vs 0.5 expected
        _write(tmp_path, f"l{i}_v1", PASS, luck=0.01)
    ev = evidence_summary(tmp_path)
    assert ev.low_tail == 10 and ev.approximate
    assert "approximate" in ev.sentence()
    calm = tmp_path / "calm"
    for i, luck in enumerate([0.5, 0.5, 0.5, 0.3, 0.7, 0.6, 0.4, 0.5, 0.5, 0.5]):
        _write(calm, f"m{i}_v1", FAIL, luck=luck)
    ev2 = evidence_summary(calm)
    assert not ev2.approximate and "approximate" not in ev2.sentence()


def test_the_committed_record_reads_12_of_68(tmp_path):
    ev = evidence_summary()
    assert (ev.measured, ev.bar_passed, ev.luck_passed) == (68, 12, 5)
    assert "(about 11 would by chance)" in ev.sentence() and "(about 3 to 4 would by chance" in ev.sentence()


def test_an_empty_directory_says_so(tmp_path):
    assert "No cohort" in evidence_summary(tmp_path).sentence()


# --------------------------------------------------------------------------- #
# 27A-3 - the cause of an untested badge
# --------------------------------------------------------------------------- #
def test_too_few_picks_when_the_lens_ranks_plenty_but_buys_one_or_two(tmp_path):
    _write(tmp_path, "few_v1", PASS, n_buys=2, n_ranked=8, n_eligible=15)
    b = track_record(SLUG, "few_v1", root=tmp_path)
    assert b.label == "untested here"
    assert b.note == "its picks were too few to count (2 names a month; the test needs 3)"
    assert b.note in b.detail_line().lower()


def test_rarely_applies_when_it_ranks_under_five_of_a_large_pool(tmp_path):
    _write(tmp_path, "rare_v1", PASS, n_buys=0, n_ranked=2, n_eligible=20)
    assert track_record(SLUG, "rare_v1", root=tmp_path).note == "rarely applies in this industry"


def test_too_little_history_otherwise(tmp_path):
    _write(tmp_path, "thin_v1", PASS, n_buys=0, n_ranked=2, n_eligible=6)
    assert track_record(SLUG, "thin_v1", root=tmp_path).note == "too little history"


# --------------------------------------------------------------------------- #
# 27A-4 - concentration reading and overlap
# --------------------------------------------------------------------------- #
def _badge(drop, vs_random):
    return Badge("no edge shown here", "not proven", 0.01, 0.4, 5, 10, 100,
                 drop_best_excess=drop, drop_best_vs_random=vs_random)


def test_detail_line_gains_the_drop_best_comparison():
    line = _badge(-0.051, 0.117).detail_line()
    assert ("without its best pick each year it trailed the group by 5.1 points; random baskets "
            "without theirs trailed by 16.8, so it holds up by 11.7") in line


def test_a_negative_comparison_reads_does_not_hold_up():
    assert "so it does not hold up" in _badge(-0.10, -0.03).detail_line()


def test_no_drop_best_figure_adds_nothing():
    assert "without its best pick" not in _badge(None, None).detail_line()


def test_overlap_counts_two_bar_passers_with_the_same_picks_as_one(tmp_path):
    _write(tmp_path, "x_v1", PASS, luck=0.01, tickers=("A", "B", "C"))
    _write(tmp_path, "y_v1", PASS, luck=0.01, tickers=("A", "B", "C"))
    _write(tmp_path, "z_v1", PASS, luck=0.01, tickers=("D", "E", "F"))
    _write(tmp_path, "w_v1", FAIL, luck=0.5, tickers=("A", "B", "C"))        # did not clear the bar
    pairs = {(o.lens_a, o.lens_b): o for o in overlap_report(tmp_path)}
    assert set(pairs) == {("x_v1", "y_v1"), ("x_v1", "z_v1"), ("y_v1", "z_v1")}
    xy = pairs[("x_v1", "y_v1")]
    assert xy.mean_jaccard == 1.0 and xy.identical_rounds == xy.shared_rounds == 120 and xy.counts_as_one
    assert "count as one" in xy.sentence()
    xz = pairs[("x_v1", "z_v1")]
    assert xz.mean_jaccard == 0.0 and not xz.counts_as_one and "count as one" not in xz.sentence()

