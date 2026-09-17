"""READER-1 — the FACTS PACK: everything the summary writer is allowed to know.

The model never sees the report. It sees a compact JSON object built here, deterministically,
from the run's own artefacts. That is the whole safety design, and it buys three things:

* **Nothing can be invented from context it should not have.** The writer cannot quote a
  price, a ticker or a rule that is not in this object, because it never saw one.
* **The validator has something to check against.** ``reader_check`` asserts that every
  number in the prose appears here; that assertion is only meaningful because this object
  is the complete and only source.
* **The pack is auditable.** It is plain data, so a reader who doubts a sentence can be
  shown the fact it came from.

Built AFTER the shortlist and the band exist, from verdicts and counts that are already
decided. Nothing here computes a judgement, ranks anything, or reads a model.

Deliberately NOT included: per-name detail beyond what the shortlist already carries.
A summary of a 134-name run has no business quoting the 60th name's payout ratio, and a
writer handed that list will reach for it.
"""

from __future__ import annotations

import json
from typing import Optional

# Percentile buckets for the band, in the words the prompt is allowed to use. The
# boundaries match the band's own gloss (tools/valuation_band.percentile_gloss) so the
# summary and the band table can never describe the same name differently.
_BUCKETS = (("cheapest", 0.0, 20.0), ("cheap", 20.0, 40.0), ("mid", 40.0, 60.0),
            ("dear", 60.0, 80.0), ("dearest", 80.0, 100.01))


def _bucket(percentile: float) -> str:
    for name, lo, hi in _BUCKETS:
        if lo <= percentile < hi:
            return name
    return "dearest"


def _verdict_counts(result) -> dict:
    out = {"buy": 0, "hold": 0, "sell": 0}
    for r in result.ranked:
        if not r.excluded and r.verdict in out:
            out[r.verdict] += 1
    return out


def _rule_tallies(result) -> list[dict]:
    """Per-rule pass/fail/not-tested for one lens, from the record of what actually ran."""
    from .pipeline import rules_applied

    rules = rules_applied(result)
    if rules is None:
        return []
    return [{"rule": r.label, "limit": r.threshold_phrase, "passed": r.passed,
             "failed": r.failed, "not_tested": r.not_tested} for r in rules.rules]


def _dominant_rule(tallies: list[dict]) -> Optional[dict]:
    """The rule that failed MORE names than all the others combined, or None.

    A cohort is often decided by one rule, and saying which is the single most useful
    sentence in a summary (Defensive Income on the oil cohort: a ten-year dividend-streak
    rule failed 126 of 128 tested, and the other five rules were survivable). The test is
    deliberately strict — "more than all others combined" — so "no single rule dominated"
    is the honest answer whenever the cohort was shaped by several."""
    if not tallies:
        return None
    worst = max(tallies, key=lambda t: t["failed"])
    others = sum(t["failed"] for t in tallies if t is not worst)
    return worst if worst["failed"] > others and worst["failed"] > 0 else None


def _factor_abstentions(result) -> dict:
    """factor -> how many ranked names could not compute it. A rank built on a factor
    that abstained for half the cohort is a different claim from one that did not."""
    out: dict[str, int] = {}
    for r in result.ranked:
        if r.excluded:
            continue
        for factor, source in (getattr(r, "factor_sources", None) or {}).items():
            if str(source).startswith("abstained"):
                out[factor] = out.get(factor, 0) + 1
    return out



# --------------------------------------------------------------------------- #
# READER-6 — every test says whether it VOTES
# --------------------------------------------------------------------------- #
# READER-4 gave each test a `role` — primary picker, second picker, check — because the
# 09:10 summary had inferred one and got it wrong. SHORTLIST-3 removed the hierarchy those
# three phrases described: there is no primary lens, so "second picker (not used for the
# shortlist)" describes a thing that no longer exists.
#
# What survives the change is the distinction that was doing the work: a test either VOTES
# or it MARKS. That is one boolean, it is exactly what the agreement table turns on, and it
# cannot go stale the way a three-way role could.


def lens_votes(kind: str) -> bool:
    """True for a lens whose verdicts are VOTES on the shortlist; False for a check.

    A check lens does not pick. Its SELL is a doubt about someone else's pick and its BUY
    says only that it found nothing to doubt, so neither is a vote."""
    return (kind or "selector") != "check"


def _agreement_facts(ag) -> dict:
    """The agreement table, as the writer may read it (SHORTLIST-3).

    Replaces the shortlist facts and the ``unanimous_buy`` list, which together said the
    same thing in two shapes: how many lenses agreed on a name, and what the rule then did
    to it. There is no rule to do anything now — the count IS the answer — so the pack
    carries the count, the buckets, and the top of the table.
    """
    if ag is None:
        return {"available": False, "reason": "not computed"}
    if not ag.available:
        return {"available": False, "reason": ag.rule_sentence}
    top = max((r.buy_votes for r in ag.rows), default=0)
    return {
        "available": True,
        "voting_lenses": [ag.voting_labels.get(s, s) for s in ag.voting_ids],
        "check_lenses": [ag.check_labels.get(s, s) for s in ag.check_ids],
        "rule": ag.rule_sentence,
        # How many names each vote count holds: {3: 1, 2: 4} reads "one name on all
        # three, four on two of three".
        "buckets": ag.buckets(),
        "no_buy_count": ag.no_buy_count,
        # The names with the MAXIMUM vote count, which is the strongest agreement the run
        # produced and the thing a summary must not leave out.
        "top_agreement": [r.display for r in ag.rows if r.buy_votes == top and top],
        # The top of the table — up to ten, because a summary that lists forty names is
        # not a summary. Each carries WHO voted for it and every mark against it.
        "rows": [{"name": r.display,
                  "buy_votes": r.buy_votes,
                  "buy_lenses": list(r.buy_lenses),
                  "sell_lenses": list(r.sell_lenses),
                  "not_ranked": [f"{label} ({why})" for label, why in r.not_ranked],
                  "marks": r.marks}
                 for r in ag.rows[:10]],
        "overlap_note": ag.overlap_note,
    }


def build_facts_pack(multi_result, *, cohort_name: str = "",
                     cohort_thesis: str = "") -> dict:
    """The complete, ordered set of facts the summary may draw on (READER-1)."""
    from .pipeline import evidence_gaps

    results = getattr(multi_result, "results", None) or {}
    ids = list(getattr(multi_result, "strategy_ids", None) or [])
    names = getattr(multi_result, "strategy_names", None) or {}
    meta = getattr(multi_result, "meta", None) or {}

    lenses = []
    for sid in ids:
        res = results[sid]
        strat = getattr(res, "rank_strategy", None)
        tallies = _rule_tallies(res)
        dominant = _dominant_rule(tallies)
        lenses.append({
            "name": names.get(sid, "") or sid,
            "asks": (getattr(strat, "asks", "") or "").strip(),
            "kind": getattr(strat, "kind", "selector"),
            # READER-6 — stated, never inferred. The prompt describes a test by its own
            # `asks` sentence and by whether it votes, and by nothing else.
            "votes": lens_votes(getattr(strat, "kind", "selector")),
            "ranked": len([r for r in res.ranked if not r.excluded]),
            "excluded": len(res.excluded),
            "no_data": len(res.unrateable),
            "verdicts": _verdict_counts(res),
            "rules": tallies,
            "dominant_rule": ({"rule": dominant["rule"], "failed": dominant["failed"]}
                              if dominant else None),
            "factor_abstentions": _factor_abstentions(res),
        })

    # The band, once for the cohort: it is per-NAME and does not vary by lens.
    evaluated, withheld, buckets = 0, 0, {b[0]: 0 for b in _BUCKETS}
    not_evaluated = 0
    seen: set = set()
    for sid in ids:
        for r in results[sid].ranked:
            if r.excluded or r.ticker in seen:
                continue
            band = getattr(r, "valuation_band", None)
            if band is None:
                continue
            seen.add(r.ticker)
            if band.available:
                evaluated += 1
                buckets[_bucket(band.percentile)] += 1
            else:
                not_evaluated += 1
            rev = getattr(r, "reversion", None)
            # BAND-3: a reversion value withheld by the sanity bound is a DIFFERENT thing
            # from a band that could not be computed, and a summary that conflates them
            # would overstate how much was missing.
            if rev is not None and not rev.available and "sanity bound" in (rev.note or ""):
                withheld += 1

    return {
        "cohort": {
            "name": cohort_name or meta.get("universe_name", "") or
                    meta.get("universe_id", ""),
            "size": meta.get("universe_size", 0),
            "built_for": cohort_thesis or "not stated",
        },
        "lenses": lenses,
        # SHORTLIST-3 — the agreement table. It replaces both the shortlist facts and the
        # separate ``unanimous_buy`` list: those said the same thing in two shapes, and
        # the second existed only because the first could not carry it. ``top_agreement``
        # is what the old list was for, and it is now simply the top of this table.
        "agreement": _agreement_facts(getattr(multi_result, "lens_agreement", None)),
        "valuation_band": {
            "evaluated": evaluated,
            "not_evaluated": not_evaluated,
            "withheld_as_implausible": withheld,
            "buckets": buckets,
        },
        "could_not_see": [{"channel": g["channel"], "why": g["reason"],
                           "names": len(g["names"])} for g in evidence_gaps(multi_result)],
        "overrides": meta.get("overrides", {}),
    }


def facts_pack_json(pack: dict) -> str:
    """The pack as the compact JSON the model is handed. Sorted keys so an identical run
    produces an identical prompt — which is what makes temp-0 reproducibility meaningful
    and what lets a provider cache the stable half of the prompt."""
    return json.dumps(pack, indent=1, sort_keys=True, ensure_ascii=False)
