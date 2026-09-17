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
# READER-4 — every test's ROLE, stated in the pack
# --------------------------------------------------------------------------- #
# The 2026-09-17 09:10 summary called Magic Formula RAW "a check". It is a second
# SELECTOR: it picks, it just did not pick FOR the shortlist, because only one lens can.
# The same summary said the other tests "look for value and growth", which is not what
# Forensic does at all — it asks whether the profits are real.
#
# Both errors have the same shape: the writer inferred a test's job instead of being told
# it. So the pack now says it, in the three phrasings the summary is allowed to use, and
# the checker holds the text to them. A writer that cannot invent a role cannot get one
# wrong.
ROLE_PRIMARY = "primary picker"
ROLE_SECOND = "second picker (not used for the shortlist)"
ROLE_CHECK = "check"


def lens_role(kind: str, *, is_primary: bool) -> str:
    """One of the three role phrases. A check is a check whether or not it is primary —
    a check lens chosen as primary cannot select, which the run says elsewhere."""
    if (kind or "selector") == "check":
        return ROLE_CHECK
    return ROLE_PRIMARY if is_primary else ROLE_SECOND



def _unanimous_facts(multi_result, sl) -> list:
    """``[{name, outcome}]`` for every name EVERY test rated BUY.

    The outcome is read from the shortlist the run already built, never re-derived: a
    unanimous name is on it, on it with a price warning, or dropped — and if it was
    dropped, the shortlist's own reason says why. A unanimous name the PRIMARY did not
    rate BUY is not a shortlist candidate at all, which is its own outcome and is said
    plainly rather than left out."""
    from .pipeline import unanimous_buys

    tickers = unanimous_buys(multi_result)
    if not tickers:
        return []
    kept = {r.ticker: r for r in (sl.kept if sl is not None else [])}
    dropped = {r.ticker: r for r in (sl.dropped if sl is not None else [])}
    results = getattr(multi_result, "results", None) or {}

    def _display(ticker):
        for res in results.values():
            name = (getattr(res, "names", None) or {}).get(ticker)
            if name:
                return f"{name} ({ticker})"
        return ticker

    out = []
    for ticker in sorted(tickers):
        row = kept.get(ticker)
        if row is not None:
            outcome = ("on the shortlist, with a price warning"
                       if row.price_caution is not None else "on the shortlist")
        elif ticker in dropped:
            outcome = f"dropped from the shortlist — {dropped[ticker].dropped_by}"
        else:
            outcome = ("not a shortlist candidate — the primary picker did not rate it "
                       "BUY")
        out.append({"name": _display(ticker), "outcome": outcome})
    return out


def build_facts_pack(multi_result, *, cohort_name: str = "",
                     cohort_thesis: str = "") -> dict:
    """The complete, ordered set of facts the summary may draw on (READER-1)."""
    from .pipeline import cohort_fit_line, evidence_gaps

    results = getattr(multi_result, "results", None) or {}
    ids = list(getattr(multi_result, "strategy_ids", None) or [])
    names = getattr(multi_result, "strategy_names", None) or {}
    meta = getattr(multi_result, "meta", None) or {}
    sl = getattr(multi_result, "shortlist", None)

    primary_sid = meta.get("shortlist_primary_id", ids[0] if ids else "")
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
            # READER-4 — stated, never inferred. The prompt may describe a test ONLY by
            # this phrase and a plain paraphrase of its `asks`.
            "role": lens_role(getattr(strat, "kind", "selector"),
                              is_primary=(sid == primary_sid)),
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

    shortlist_facts: dict = {"available": False, "reason": "not computed"}
    if sl is not None:
        shortlist_facts = {
            "available": sl.available,
            "reason": sl.reason,
            "rule": sl.rule_sentence if sl.available else "",
            "candidates": sl.candidates,
            # The COUNTS as well as the lists. The prompt forbids computing a number the
            # pack does not contain, and "19 of 21 survived" is the most natural sentence
            # a summary of a shortlist can write — so the 19 has to be here, not left for
            # the writer to count. (Found by the validator rejecting exactly that
            # sentence, which is the guard working before a live call was ever made.)
            "kept_count": len(sl.kept),
            "dropped_count": len(sl.dropped),
            # READER-4 — the price warning's own percentile. Without it the writer cannot
            # state the number the warning is about, and the number check would rightly
            # refuse the sentence if it tried. None on every ordinary kept row.
            "kept": [{"name": r.display, "rank": r.rank_position,
                      "price_warning_percentile": (round(r.price_caution)
                                                   if r.price_caution is not None
                                                   else None)}
                     for r in sl.kept],
            "dropped": [{"name": r.display, "why": r.dropped_by} for r in sl.dropped],
        }

    primary_id = primary_sid
    primary = getattr(results.get(primary_id), "rank_strategy", None)
    return {
        "cohort": {
            "name": cohort_name or meta.get("universe_name", "") or
                    meta.get("universe_id", ""),
            "size": meta.get("universe_size", 0),
            "built_for": cohort_thesis or "not stated",
        },
        "primary_lens": names.get(primary_id, "") or primary_id,
        "fit_note": cohort_fit_line(primary, cohort_thesis),
        "lenses": lenses,
        "shortlist": shortlist_facts,
        # READER-4 — the names EVERY test rated BUY, and what became of each. The 09:10
        # summary never mentioned that Suncor was BUY on all three, which was the single
        # most interesting fact the run produced; the shortlist facts could not carry it,
        # because a unanimous name may be kept, kept with a warning, or dropped by a rule
        # the shortlist applies before the band.
        "unanimous_buy": _unanimous_facts(multi_result, sl),
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
