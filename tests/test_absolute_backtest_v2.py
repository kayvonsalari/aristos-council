"""ABSOLUTE-BACKTEST-2: the pure pieces of the new analyses (definitions frozen in backtests_absolute/v2/PREREGISTRATION.md)."""
from datetime import date

import numpy as np

from aristos_council.absolute_backtest import v2
from aristos_council.absolute_backtest.v2 import Rec


def rec(t, y=0.05, cap=1e9, gp=None, ret=0.1, gate="qualifies", bt="qualifies_below_hurdle"):
    return Rec(t, gate, bt, y, cap, gp, ret)


def test_size_thirds_cut_by_rank_into_three_equal_groups():
    th = v2.size_thirds({f"T{i}": float(i) for i in range(1, 10)})
    assert [th[f"T{i}"] for i in range(1, 10)] == [0, 0, 0, 1, 1, 1, 2, 2, 2]
    assert v2.size_thirds({}) == {}
    assert set(v2.size_thirds({"A": 1.0, "B": 2.0}).values()) <= {0, 1, 2}


def test_form4_keeps_the_higher_gross_profit_half_of_the_cheapest_third():
    # 12 passers -> cheapest third = 4 names (highest yields); half = 2; fewer than 3 chosen -> skipped
    recs = [rec(f"T{i}", y=0.20 - i * 0.01, gp=0.1 * i) for i in range(12)]
    names, status = v2.select_form4(recs)
    assert (names, status) == ((), "fewer than 3 chosen")
    # 18 passers -> third = 6, half = 3, taken by gross profit high first
    recs = [rec(f"T{i:02d}", y=0.30 - i * 0.01, gp=float(i)) for i in range(18)]
    names, status = v2.select_form4(recs)
    cheap = [f"T{i:02d}" for i in range(6)]
    assert status == "chosen" and names == tuple(sorted(cheap[::-1][:3]))


def test_form4_a_missing_reading_cannot_be_chosen_but_is_not_failed():
    recs = [rec(f"T{i:02d}", y=0.30 - i * 0.01, gp=(None if i < 3 else float(i))) for i in range(18)]
    names, status = v2.select_form4(recs)
    assert status == "chosen" and set(names) == {"T03", "T04", "T05"}
    recs = [rec(f"T{i:02d}", y=0.30 - i * 0.01, gp=(None if i < 5 else float(i))) for i in range(18)]
    assert v2.select_form4(recs) == ((), "fewer than 3 chosen")


def test_form4_without_passers_has_no_cheap_third():
    assert v2.select_form4([rec("A", gate="excluded")]) == ((), "no cheap third")


def test_a_basket_holding_the_whole_pool_earns_zero_against_every_benchmark():
    recs = [rec(f"T{i}", ret=0.01 * i, cap=1e9 * (i + 1)) for i in range(9)]
    rd = v2.build_round(date(2020, 1, 31), recs)
    rd.sel[2] = tuple(f"T{i}" for i in range(9))
    s = v2.score_form([rd], 2)[0]
    assert s.positioned and abs(s.ex["industry"]) < 1e-12 and abs(s.ex["passers"]) < 1e-12 and abs(s.ex["size"]) < 1e-12


def test_size_benchmark_uses_the_picks_own_third():
    caps = {f"T{i}": 1e9 * (i + 1) for i in range(9)}
    rets = {f"T{i}": (0.30 if i >= 6 else 0.0) for i in range(9)}       # only the biggest third earns
    rd = v2.build_round(date(2020, 1, 31), [rec(t, ret=rets[t], cap=caps[t]) for t in caps])
    rd.sel[2] = ("T6", "T7", "T8")
    s = v2.score_form([rd], 2)[0]
    assert abs(s.ex["size"]) < 1e-12                    # the picks equal their own third
    assert s.ex["industry"] > 0.1                       # but beat the whole industry


def test_gate_passer_benchmark_needs_three_priced_passers():
    recs = [rec("A"), rec("B"), rec("C", gate="excluded"), rec("D", gate="excluded")]
    rd = v2.build_round(date(2020, 1, 31), recs)
    assert v2.bench_values(rd, ["A"])["passers"] is None


def test_trim_by_yield_keeps_the_highest_yields():
    rd = v2.build_round(date(2020, 1, 31), [rec("A", y=0.10), rec("B", y=0.07), rec("C", y=0.12), rec("D", y=0.01)])
    assert v2.trim_by_yield(rd, ("A", "B", "C", "D"), 2) == ("A", "C")


def test_head_to_head_matches_basket_sizes_and_differences_are_form3_minus_form2():
    recs = [rec(f"T{i}", y=0.01 * (i + 1), ret=0.02 * i, cap=1e9) for i in range(8)]
    rd = v2.build_round(date(2020, 1, 31), recs)
    rd.sel[2] = ("T0", "T1", "T2", "T3", "T4")
    rd.sel[3] = ("T5", "T6", "T7")
    s2, s3 = v2.score_form([rd], 2), v2.score_form([rd], 3)
    rows = v2.head_to_head([rd], s2, s3)
    assert len(rows) == 1 and rows[0]["n2"] == 5 and rows[0]["n3"] == 3 and rows[0]["mn2"] == rows[0]["mn3"] == 3
    d = v2.h2h_diffs(rows)[0]
    assert d == rows[0]["ret3"] - rows[0]["ret2"] and d > 0


def test_spread_is_cheap_third_median_minus_dear_third_median_and_needs_six_names():
    recs = [rec(f"T{i}", y=y) for i, y in enumerate([0.12, 0.10, 0.06, 0.05, 0.03, 0.02])]
    assert abs(v2.spread_of(recs) - ((0.12 + 0.10) / 2 - (0.03 + 0.02) / 2)) < 1e-12
    assert v2.spread_of(recs[:5]) is None


def test_block_bootstrap_is_seeded_and_brackets_a_constant():
    r = v2.block_bootstrap(np.full(60, 0.02))
    assert abs(r["lo"] - 0.02) < 1e-12 and abs(r["hi"] - 0.02) < 1e-12 and abs(r["point"] - 0.02) < 1e-12
    a = v2.block_bootstrap(np.linspace(-0.1, 0.1, 80))
    b = v2.block_bootstrap(np.linspace(-0.1, 0.1, 80))
    assert a == b and a["lo"] < a["point"] < a["hi"]


def test_random_baskets_centre_near_zero_against_each_benchmark():
    rng = np.random.default_rng(1)
    recs_by = []
    for m in range(24):
        d = date(2020 + m // 12, m % 12 + 1, 28)
        recs_by.append((d, [rec(f"T{i}", ret=float(rng.normal(0.08, 0.3)), cap=1e9 * (1 + i)) for i in range(15)]))
    rounds = [v2.build_round(d, r) for d, r in recs_by]
    for rd in rounds:
        rd.sel[2] = tuple(f"T{i}" for i in range(5))
    sc = v2.score_form(rounds, 2)
    out = v2.random_excess(rounds, sc, 2, "synthetic", n_baskets=200)
    for b in v2.BENCHES:
        years, mat = out[b]
        assert abs(float(mat.mean())) < 1e-6
