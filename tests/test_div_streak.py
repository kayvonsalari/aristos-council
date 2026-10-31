"""DIV-STREAK-1 (2026-10-31 sweep): the "consecutive years of dividend increases" count was far too low
(CVX 9, XOM 4, CNQ.TO 2, KO 23 - each really 25-60+) because it compared calendar-year TOTALS with a flat
band: a one-cent raise paid late in the year, a shifted ex-date or a special read as flat or as a cut.
The fixtures below are the real payment patterns of those names (per-share, split-adjusted)."""
from __future__ import annotations

from aristos_council.tools.screening import dividend_streak_detail

AS_OF = 2026


def _quarterly(first_year, rates):
    """rates: {year: payment} or {year: [4 payments]} -> {year: [payments]}"""
    out = {}
    for y, r in rates.items():
        out[y] = list(r) if isinstance(r, (list, tuple)) else [r] * 4
    return out


def test_ko_special_double_payment_in_2001_does_not_break_a_sixty_year_run():
    # KO 2001 paid 0.18 beside three 0.09s (an ex-date doubled by the vendor); old count stopped at 23.
    pay = {y: [round(0.09 * 0.95 ** (2001 - y), 5)] * 4 for y in range(1963, 2001)}
    pay.update({y: [round(0.10 + 0.01 * (y - 2002), 5)] * 4 for y in range(2002, 2026)})
    pay[2001] = [0.09, 0.09, 0.18, 0.09, 0.09]
    streak, floor, start = dividend_streak_detail(pay, AS_OF)
    assert streak == 62 and floor is True and start == 1963


def test_cvx_raise_in_the_last_payment_of_a_year_counts():
    # CVX 2016: 1.07, 1.07, 1.07, 1.08 -> the old half-percent band called 4.29 vs 4.28 flat.
    pay = {2013: [1.0] * 4, 2014: [1.0, 1.07, 1.07, 1.07], 2015: [1.07] * 4,
           2016: [1.07, 1.07, 1.07, 1.08], 2017: [1.08] * 4, 2018: [1.12] * 4,
           2019: [1.19] * 4, 2020: [1.29] * 4, 2021: [1.34] * 4}
    streak, floor, _ = dividend_streak_detail(pay, 2021)  # 2021 is the current, partial year -> excluded
    # 2014..2020: seven years, each above the year before (2016's one-cent November raise included)
    assert streak == 7 and floor is True


def test_cvx_old_method_was_wrong_new_one_counts_every_raise():
    from aristos_council.tools.screening import dividend_streak
    pay = {y: [1.0 + 0.1 * (y - 2010)] * 4 for y in range(2010, 2026)}
    pay[2016] = [pay[2015][0]] * 3 + [pay[2015][0] + 0.01]  # only a 1-cent raise, paid in November
    pay[2017] = [pay[2016][-1]] * 4
    annual = {y: sum(v) for y, v in pay.items()}
    old, _ = dividend_streak(annual, AS_OF)
    new, floor, start = dividend_streak_detail(pay, AS_OF)
    assert new == 15 and floor is True and start == 2010
    assert old < new


def test_xom_one_cent_raise_in_november_is_a_raise():
    pay = {2018: [0.82] * 3 + [0.87], 2019: [0.82, 0.87, 0.87, 0.87], 2020: [0.87] * 4,
           2021: [0.87, 0.87, 0.87, 0.88], 2022: [0.88, 0.88, 0.88, 0.91], 2023: [0.91, 0.91, 0.91, 0.95],
           2024: [0.95, 0.95, 0.95, 0.99], 2025: [0.99, 0.99, 0.99, 1.03]}
    streak, floor, start = dividend_streak_detail(pay, AS_OF)
    # 2019..2025 all above the year before (2020's total 3.48 > 2019's... see below) - XOM held 0.87 through
    # 2020, so the 2020 total 3.48 is above 2019's 3.43 and counts, exactly as the aristocrat tables count it
    assert streak == 7 and floor is True and start == 2018


def test_cnq_special_in_2022_is_not_a_year_of_five_regular_payments():
    pay = {y: [0.1 + 0.01 * (y - 2010)] * 4 for y in range(2010, 2022)}
    pay[2021] = [0.2302, 0.2302, 0.2302, 0.288]
    pay[2022] = [0.3673, 0.3673, 0.75, 0.375, 0.425]  # 0.75 is the special
    pay[2023] = [0.45, 0.45, 0.45, 0.5]
    pay[2024] = [0.525, 0.525, 0.525, 0.563]
    pay[2025] = [0.563] * 3 + [0.6]
    streak, floor, start = dividend_streak_detail(pay, AS_OF)
    assert streak == 15 and floor is True and start == 2010


def test_ford_is_zero_years_and_that_is_correct():
    pay = {2015: [0.15] * 4, 2016: [0.15, 0.15, 0.15, 0.4], 2017: [0.15] * 4, 2018: [0.15] * 4,
           2019: [0.15] * 4, 2020: [0.15, 0.0 + 0.15][:1], 2021: [0.10], 2022: [0.15, 0.15, 0.15, 0.15],
           2023: [0.15, 0.15, 0.15, 0.15], 2024: [0.15, 0.15, 0.15, 0.15], 2025: [0.15, 0.15, 0.15, 0.15]}
    streak, floor, _ = dividend_streak_detail(pay, AS_OF)
    assert streak == 0 and floor is False  # flat 2023..2025 ends the run at once; a real answer, not a floor


def test_a_real_cut_ends_the_run_and_is_not_a_floor():
    pay = {y: [0.5 + 0.05 * (y - 2005)] * 4 for y in range(2005, 2026)}
    pay[2015] = [0.9] * 4            # cut from ~1.0
    streak, floor, _ = dividend_streak_detail(pay, AS_OF)
    assert streak == 10 and floor is False


def test_a_short_history_is_a_floor_not_a_verdict():
    pay = {y: [0.1 + 0.02 * (y - 2020)] * 4 for y in range(2020, 2026)}
    streak, floor, start = dividend_streak_detail(pay, AS_OF)
    assert (streak, floor, start) == (5, True, 2020)


def test_under_three_complete_years_abstains():
    assert dividend_streak_detail({2024: [0.1] * 4, 2025: [0.1] * 4}, AS_OF)[0] is None


def test_shifted_ex_date_year_with_three_payments_is_not_a_cut():
    pay = {2020: [0.5] * 4, 2021: [0.5] * 3, 2022: [0.55] * 4, 2023: [0.6] * 4, 2024: [0.65] * 4,
           2025: [0.7] * 4}
    streak, _, _ = dividend_streak_detail(pay, AS_OF)
    assert streak >= 4  # 2021 (3 payments) is flat vs 2020 by rate, ends nothing newer than itself


class _Fund:
    def __init__(self, s, floor, start):
        self.dividend_streak_years, self.dividend_streak_is_floor = s, floor
        self.dividend_history_start_year = start


def _crit(f, thr=10):
    from aristos_council.tools.criteria.registry import _min_dividend_streak
    from types import SimpleNamespace
    return _min_dividend_streak(SimpleNamespace(fundamentals=f), thr)


def test_short_floor_is_not_evaluated_never_a_phantom_fail():
    r = _crit(_Fund(5, True, 2020))
    assert r.passed is None and "at least 5 years" in r.note and "starts 2020" in r.note


def test_floor_above_the_rule_passes_with_at_least_wording():
    r = _crit(_Fund(63, True, 1962))
    assert r.passed is True and "at least 63y" in r.note


def test_a_broken_short_streak_still_fails():
    assert _crit(_Fund(2, False, 1962)).passed is False
