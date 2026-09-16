# Why the dividend-history rules fail names that never cut

**Date:** 2026-09-16 · **Read-only diagnosis.** No code changed.
**Verdict:** the rules are sound; the **calendar-year totals they are both built on are the
wrong measure**. Not one of the eight failures is caused by a dividend actually being cut
inside the window.

The two rules agree on a wrong answer because they read the same series through the same
lens: `_dividend_streak_from_ticker` sums `tk.dividends` per calendar year, and both
`min_dividend_streak` (via `dividend_streak_years`) and `max_dividend_cuts` (via
`dividend_year_totals`) then compare those year totals. One defect, two symptoms.

**Where the data came from.** The figures below are read from the same cache the two runs
used — `.aristos_cache/yfinance_<TICKER>_2026_09_16_fundamentals.json`, provider `yfinance`,
cached 2026-09-16 — cross-checked against a live fetch of `Ticker.dividends` on the same day.
The cached `dividend_streak_years` reproduce the 15:45 run exactly (CNQ 0, E 0, EOG 1,
SHEL 3, TTE 2, SLB 4), so this is the data those runs saw.

---

## 1. Hypothesis (a), splits — **ruled out**

`_dividend_streak_from_ticker`'s docstring claims `tk.dividends` is "already split-adjusted".
It is. Canadian Natural split 2-for-1 on **2024-06-11**, and the per-payment amounts run
straight through it with no halving:

```
2024-03-14  0.3900      <- before the split
2024-06-17  0.3820      <- after; an unadjusted series would read ~0.19
2024-09-13  0.3870
2024-12-13  0.3900
2025-03-21  0.4100
```

The `auto_adjust=False` history call returns the **same** dividend column as `tk.dividends`
for all eight names, so there is no adjusted/unadjusted choice being made wrongly. Splits
explain **0 of 8**. CNQ is the only name in the set with a split in the window.

## 2. Hypothesis (b), currency — **ruled out as the cause**, with one exception that is not FX

For the four names with a home listing, the home-currency series shows the **same** false
cuts:

| | ADR total | home total | home currency |
|---|---|---|---|
| TTE 2022→23 | −19.3% | −22.1% | EUR |
| CNQ 2022→23 | −22.1% | −19.0% | CAD |
| SHEL 2020→21 | −14.3% | −19.0% | GBp |
| EQNR 2024→25 | −51.3% | −40.4% | NOK |

If the exchange rate were the cause, the home series would rise where the ADR falls. It does
not. Currency explains **0 of 8**.

**The exception is a data gap, not FX.** The ADR series for CNQ and E simply **stops
mid-2025** while the home listing continues:

```
CNQ  (US ADR)  ... 2025-03-21, 2025-06-13          <- ends here, 2 payments in 2025
CNQ.TO (home)  ... 2025-03-21, 2025-06-13, 2025-09-19, 2025-12-12   <- 4 payments

E    (US ADR)  ... 2025-03-25, 2025-05-20          <- ends here, 2 payments in 2025
ENI.MI (home)  ... 2025-03-24, 2025-05-19, 2025-09-22, 2025-11-24   <- 4 payments
```

Two payments are missing from each ADR record, so 2025 reads as a ~50% cut against a
complete 2024. That is a **provider completeness defect on the ADR line**, and it is the
single largest false figure in the set (CNQ 45.6%, E 48.1%).

## 3. Hypothesis (c), payment count and date shift — **the main cause, 5 of 8**

The year total moves with **how many times** a company paid, which has nothing to do with
what it paid. Every one of these is a year with one fewer payment than the year before:

| | payments | total | **median per payment** |
|---|---|---|---|
| CNQ 2022 → 2023 | 5 → 4 | −22.1% | **+15.6%** |
| SHEL 2020 → 2021 | 4 → 4 | −14.3% | **+26.6%** |
| SHEL 2021 → 2022 | 4 → 3 | −8.5% | **+20.9%** |
| TTE 2020 → 2021 | 5 → 4 | −19.0% | **+2.0%** |
| TTE 2022 → 2023 | 5 → 4 | −19.3% | **+11.2%** |
| CNQ 2024 → 2025 | 4 → 2 | −45.6% | **+8.4%** (the ADR gap above) |
| E 2024 → 2025 | 4 → 2 | −48.1% | **+2.7%** (the ADR gap above) |

In every row the amount per payment **rose** while the total "fell".

**SLB is the same defect at a year boundary.** Its 2020 total is 0.50 + 0.125 × 3 — one
pre-cut payment and three post-cut. 2021 is 0.125 × 4. So the total falls 42.9% while the
per-payment rate is **exactly flat** (0.1250 → 0.1250). That is the tail of the *2020* cut
being read as a *2021* cut.

## 4. Hypothesis (d), variable and special dividends — **2 of 8, and the hardest**

EOG's payment record shows the regular dividend rising while the specials shrink:

```
2022: 0.75  1.00  0.75  1.80  0.75  1.50  0.75  1.50   (8 payments, total 8.80)
2023: 0.825 1.00  0.825       0.825       0.825 1.50   (6 payments, total 5.80)
      ^^^^^ the REGULAR dividend rose 0.75 -> 0.825 (+10%)
```

COP is the same shape — its variable return-of-cash tier gave 8 payments in 2022, 7 in 2023,
4 in 2024, so the total fell 21.6% then 20.2% while the median per payment rose **+19.6%**
then **+34.5%**.

**Equinor is the one genuine decline.** Its extraordinary tranche was wound down through
2024-25, and the fall shows in NOK on the home listing too (2024 −8.4%, 2025 −40.4%). The
median falls with it (−22.2%, −47.1%). The *ordinary* dividend rose; the free data does not
label which payments were extraordinary, so no measure built on these payments can separate
them.

## 5. The table

| Ticker | Streak rule | Cut rule | True history (owner) | Cause |
|---|---|---|---|---|
| **CNQ** | 0 yrs → FAIL | FAIL 45.6% | raised 25 straight years | **(c)** 5→4 payments in 2023; ADR record stops mid-2025 |
| **SHEL** | 3 yrs → FAIL | FAIL 14.3% | cut 2020, raised every year 2021-25 | **(c)** 4→3 payments in 2022; 2020 cut is outside the window |
| **TTE** | 2 yrs → FAIL | FAIL 19.3% | raised every year 2021-25 | **(c)** 5→4 payments in 2021 and 2023 |
| **E** | 0 yrs → FAIL | FAIL 48.1% | raised every year 2021-25 | **(c)** ADR record stops mid-2025 (2 of 4 payments missing) |
| **SLB** | 4 yrs → FAIL | FAIL 42.9% | cut 2020, raised every year 2021-25 | **(c)** year-boundary: 2020 holds one pre-cut payment; per-payment rate flat |
| **EOG** | 1 yr → FAIL | FAIL 37.2% | raised every year 2021-25 | **(d)** specials, 8→6→4 payments; regular dividend rose +10% |
| **COP** | 1 yr → FAIL | FAIL 21.6% | raised every year 2021-25 | **(d)** variable return-of-cash tier, 8→7→4 payments |
| **EQNR** | 0 yrs → FAIL | FAIL 51.3% | ordinary raised; extraordinary wound down | **(e)/(d)** total distributions really fell, in NOK too |

**Tally: (a) 0 · (b) 0 · (c) 5 · (d) 2 · (e) 1.**

---

## 6. The display bug — confirmed, and worse than it looks

`observed` is a **fraction**, but the render site formats it with the unit of the
**threshold** parameter — and this criterion's threshold is a count of *years*, so the unit
is `count`:

```
observed=0.32  -> renders "0"      should read "32%"
observed=0.55  -> renders "1"      should read "55%"
```

So a 32% cut prints as "the largest fall was 0 of the prior year's total" and a 55% cut as
"was 1". This is not only ugly — **"0" reads as *no cut*, the opposite of what fired the
rule.** It is the one item here that is purely cosmetic in cause and materially misleading
in effect.

---

## 7. Proposals, ranked by what they actually fix

Each was run against all eight names over the same window.

| | Proposal | Fixes | Notes |
|---|---|---|---|
| **P1** | median per payment vs prior year | **4 of 8** — CNQ, SHEL, SLB, COP | |
| **P1+P2** | median **plus a 10% tolerance** | **6 of 8** — adds TTE, EOG | |
| **P5** | render `observed` as a percentage | display only | fixes a misleading line for every failing name |
| **P4** | fetch the home listing for ADRs | **1 of 8** — E only | far weaker than expected |
| **P3** | split-adjust the series | **0 of 8** | the series is already adjusted |

**P1 is not a strict improvement, and this is the finding I would not have expected.** The
median is robust to specials and to payment-count drift, but it is *vulnerable to a cadence
change*, which the total handles correctly. Eni moved from 2 payments a year to 4: the total
rose +14.4% while the median fell **−44.1%**. So P1 alone would newly break names the
current rule gets right. Whichever measure is chosen, one of these two failure modes
survives — which is the real argument for P2, a tolerance, doing the load-bearing work
rather than the choice of measure.

**P4 is much weaker than the brief expects.** The home listing does not fix CNQ (still
−19.0%), SHEL (−19.0%), TTE (−22.1%) or EQNR (−40.4%), because the payment-count artefact
exists on the home line too. It fixes only Eni, whose ADR record is incomplete. At roughly
one extra fetch per ADR per run, it buys one name — it is a **data-completeness fix for a
specific provider defect**, not a rule fix, and it should be scoped that way.

### On P2's threshold: the brief's premise does not hold

> *"every real oil-sector cut on record was 40% or more"*

Not so, and the number matters because a tolerance set on that belief would wave real cuts
through:

| Real 2020 cut | ADR | home currency |
|---|---|---|
| Shell | −49.1% | −49.8% (GBp) |
| SLB | −56.2% | — |
| **Eni** | **−35.6%** | **−34.5% (EUR)** |
| **Equinor** | **−29.7%** | **−24.8% (NOK)** |

Equinor's genuine 2020 cut was **24.8%** in its own currency. A 40% tolerance would pass it;
so would 30%. **10% is the value I would pick** — it clears every false cut in section 3
(the largest median-based false reading is TTE's 8.3%) and sits well below the smallest real
cut on record (24.8%), leaving a factor-of-two margin on both sides.

### What I would do first

**P5, then P1+P2 together.**

P5 first because it is a one-line change to a line that currently tells the reader the
opposite of the truth ("the largest fall was 0"), and it is independent of every decision
below it.

Then P1 and P2 *as one change*, not separately: the median alone fixes 4 and breaks Eni; the
tolerance alone leaves the count-drift failures at 14-22%; together they fix 6 of 8 and the
two survivors are honest — EQNR's distributions genuinely fell, and Eni needs the data fix,
not a rule fix.

P4 only afterwards, scoped as "the ADR dividend record is incomplete for some names",
because that is what it is. P3 should be dropped: there is nothing to fix.

### One thing none of these proposals addresses

Five of the eight failures come from **comparing calendar years at all**. A trailing
twelve-month window, rolled to each payment date, has no year boundary for a payment to slip
across and no dependence on how many times a company chose to pay. It would fix the same
names P1+P2 fixes, for a reason that is about the measurement rather than about tolerating
its noise — and it would leave the specials problem (EOG, COP) exactly where it is. It is a
larger change and I am not proposing it here, but if the year totals are going to be
revisited a second time, that is the version worth pricing.
