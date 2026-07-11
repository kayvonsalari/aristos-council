# Aristos Council — The Scoreboard

How the prospective scoreboard works: what is frozen, how it is bucketed, and how it will
be graded. The pledge is that verdicts are recorded **before** the outcome is known and
scored against **pre-committed** tests — no bucket, threshold, or test is redrawn after
returns are in. This document matches the register of [The Calculations](CALCULATIONS.md);
where they disagree, the recorded CSV and the code win.

## 1. Snapshot mechanics

A snapshot is a quarterly, append-only **freeze** (a saved, immutable copy of the inputs
and verdicts as they stood that day, so the scored call can never be quietly rewritten)
written to `snapshots/verdict_consensus.csv`. Each **row** is one name in one strategy on
one snapshot date: the Aristos **verdict of record** (the deterministic ranker's call — the
one the system stands behind), its combined rank, the price, and the street's
`recommendationMean` (sell-side analysts' mean rating, 1 = strong buy … 5 = sell), analyst count,
and mean target as observed that day. Every recorded verdict is **ranker-only** — the
deterministic core, no LLM in the loop for any scored call. **EXCLUDED** rows (screen / cap
/ sector fail) and **UNRATEABLE** rows (no data) are part of the record, not omissions: an
exclusion is a call and gets scored like any other. Rows are only ever appended; a past
snapshot is never rewritten.

## 2. Buckets

- **Aristos** buckets are the recorded verdict — BUY / HOLD / SELL — taken as issued. A SELL
  row carries the reading "bottom quintile of N — relative rank, not a short thesis"; it
  ranks last within its universe, it is not a borrow-and-short recommendation.
- **Street** buckets are **terciles (thirds) of `recommendationMean` WITHIN the snapshot
  universe** — most-loved / middle / least-loved third. Absolute rating bands are not used because they
  are structurally all-BUY: the observed calibration on 2026-07-05 spanned 1.30–2.71 across
  38 names with zero sells, so an absolute cut has no discriminating power. Tercile ties go
  to the more-loved bucket.

## 3. Sticky-label flag

A row is flagged **STICKY-LABEL** when the street rates the name in its most-loved tercile
while the street's *own* mean target sits at or below the current price — a rating the
price target no longer supports. Observed on 2026-07-05: AMD, GE, UNH. The flag is a
diagnostic on the street's internal consistency, not an Aristos verdict.

## 4. Scoring

Each row is scored on **forward TOTAL return** (adjusted closes) at **6 and 12 months**,
measured relative to the **equal-weight** return of the snapshot universe over the same
window. Delistings are **UNRESOLVED** and reported separately — never silently assumed to
be −100%. The pre-committed test is bucket **ORDERING**, not any single name's return:
Aristos BUY > HOLD > SELL, and street loved > middle > unloved. First freeze: 2026-07-05;
first scoring: January 2027.

> One snapshot is an anecdote with arithmetic; ordering across repeated snapshots is the evidence.
