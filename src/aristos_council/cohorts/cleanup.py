"""COHORT-1 — the four cleanup rules, applied in order, every removal logged.

Order matters and is part of the contract. Rule 1 collapses a company to one line BEFORE
anything is measured, so a size or history test is never applied twice to the same
business and never decides which listing survives. Rules 2 and 3 then remove names for
what they ARE. Rule 4 removes names the lenses could not say anything about anyway, and
it runs last so that its log reads as "this one had nothing to score", not as a
catch-all for names three earlier rules would have taken.

Nothing here is a judgment about a company. Every removal names the rule and the value
that tripped it, so the report can be read as an argument rather than a result.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .definitions import MAX_MEMBERS, MIN_MEMBERS, CohortDefinition, excluded_by
from .source import Candidate

RULE_ONE_LINE = 1
RULE_SIZE_AND_HISTORY = 2
RULE_EXCLUSIONS = 3
RULE_NO_DATA = 4

RULE_NAMES = {
    RULE_ONE_LINE: "one line per company",
    RULE_SIZE_AND_HISTORY: "size and history",
    RULE_EXCLUSIONS: "sector exclusions",
    RULE_NO_DATA: "nothing for the lenses to read",
}

# Security types that are never a primary line, whatever else is true of them.
_NOT_A_PRIMARY_LINE = ("ADR", "GDR", "Preferred", "Unit", "Right", "Warrant", "Fund", "ETF")

# The fields every lens in the repo needs before it can say anything at all. A name
# missing these does not fail the screen — it ABSTAINS, which is a row of blanks in the
# grid and a name in the UNRATEABLE list. Keeping it would push the cohort's abstention
# rate up for a reason that is about data coverage, not about the market.
REQUIRED_FIELDS = ("market_cap", "industry")


@dataclass(frozen=True)
class Removal:
    ticker: str
    rule: int
    reason: str

    def line(self) -> str:
        return f"rule {self.rule} ({RULE_NAMES[self.rule]}): {self.ticker} — {self.reason}"


@dataclass(frozen=True)
class SizeVerdict:
    """Whether the built cohort is usable, and if not, what would fix it."""

    status: str                       # "ok" | "thin" | "wide"
    count: int
    suggestion: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def sentence(self) -> str:
        if self.status == "ok":
            return f"{self.count} names, inside the {MIN_MEMBERS}–{MAX_MEMBERS} band."
        if self.status == "thin":
            return f"TOO THIN: {self.count} names, under {MIN_MEMBERS}. {self.suggestion}"
        return f"TOO WIDE: {self.count} names, over {MAX_MEMBERS}. {self.suggestion}"


def _company_key(cand: Candidate) -> str:
    """What counts as "the same company".

    ISIN when there is one — it is the identifier that actually means this. Otherwise a
    flattened name, which catches the share-class case (``Alphabet Inc Class A`` /
    ``Class C``) that ISINs deliberately keep apart.
    """
    if cand.isin:
        return f"isin:{cand.isin[:6]}{cand.isin[6:]}"
    flat = re.sub(r"[^a-z0-9]+", " ", (cand.name or cand.code).lower())
    flat = re.sub(r"\b(class [abc]|inc|corp|corporation|plc|sa|se|nv|ag|as|asa|ab|oyj|"
                  r"spa|co|ltd|group|holdings?|the)\b", " ", flat)
    return "name:" + re.sub(r"\s+", " ", flat).strip()


def _share_class_key(cand: Candidate) -> str:
    """Same company AND same listing venue — what a duplicate share class looks like.

    An ISIN is per share class, so two classes of one company have two ISINs and survive
    ``_company_key``. Collapsing them needs the name with its class suffix removed.
    """
    flat = re.sub(r"[^a-z0-9]+", " ", (cand.name or cand.code).lower())
    flat = re.sub(r"\b(class [abc]|series [abc]|[abc] shares?)\b", " ", flat)
    return re.sub(r"\s+", " ", flat).strip()


def _preference(cand: Candidate, defn: CohortDefinition) -> tuple:
    """Sort key deciding which line of a company survives. Lower is better.

    In order: the issuer's own primary ticker beats everything; then the exchange order
    the definition itself lists, because a cohort that names ``[US, XETRA, LSE]`` has
    stated a preference; then the larger market cap, which on a genuine dual listing is
    the more liquid line; then the symbol, so the result never depends on dict order.
    """
    is_primary = 0 if (cand.primary_ticker and cand.primary_ticker == cand.ticker) else 1
    codes = list(defn.exchange_codes)
    try:
        venue = codes.index(cand.exchange)
    except ValueError:
        venue = len(codes)
    return (is_primary, venue, -(cand.market_cap or 0.0), cand.ticker)


def rule_one_line(candidates: list[Candidate], defn: CohortDefinition
                  ) -> tuple[list[Candidate], list[Removal]]:
    """One line per company: drop ADRs, secondary listings and duplicate share classes."""
    kept: list[Candidate] = []
    removals: list[Removal] = []

    # (a) types that are never a primary line
    survivors: list[Candidate] = []
    for cand in candidates:
        bad = next((t for t in _NOT_A_PRIMARY_LINE
                    if t.lower() in (cand.security_type or "").lower()), "")
        if bad:
            removals.append(Removal(cand.ticker, RULE_ONE_LINE,
                                    f"security type is {cand.security_type!r}, not a "
                                    f"primary common line"))
        else:
            survivors.append(cand)

    # (b) one line per company, then (c) one line per share-class family
    for key_fn, label in ((_company_key, "secondary listing of"),
                          (_share_class_key, "duplicate share class of")):
        groups: dict[str, list[Candidate]] = {}
        for cand in survivors:
            groups.setdefault(key_fn(cand), []).append(cand)
        survivors = []
        for group in groups.values():
            group.sort(key=lambda c: _preference(c, defn))
            winner, losers = group[0], group[1:]
            survivors.append(winner)
            for loser in losers:
                removals.append(Removal(loser.ticker, RULE_ONE_LINE,
                                        f"{label} {winner.ticker} "
                                        f"({winner.name or winner.code})"))
    kept = sorted(survivors, key=lambda c: c.ticker)
    return kept, removals


def rule_size_and_history(candidates: list[Candidate], defn: CohortDefinition
                          ) -> tuple[list[Candidate], list[Removal]]:
    """Below the cap floor, or too short a history to rank against its own past.

    The cap floor is applied in the name's OWN currency, deliberately and without
    conversion — see ``docs/COHORTS.md`` - EXCEPT for a cohort built from the market index
    (COHORT-3), whose floor is in USD and is tested against the index's converted cap. A missing
    cap is NOT a failure here: it is a gap, and rule 4 is where gaps are removed, with a reason
    that says so.
    """
    kept, removals = [], []
    for cand in candidates:
        if defn.uses_index and cand.market_cap_usd is not None:
            if cand.market_cap_usd < defn.min_market_cap_usd:
                removals.append(Removal(
                    cand.ticker, RULE_SIZE_AND_HISTORY,
                    f"market cap ${cand.market_cap_usd / 1e9:,.2f}bn (USD, from the index) is "
                    f"under the floor of ${defn.min_market_cap_usd / 1e9:g}bn"))
                continue
        elif cand.market_cap is not None and cand.market_cap < defn.min_market_cap:
            removals.append(Removal(
                cand.ticker, RULE_SIZE_AND_HISTORY,
                f"market cap {cand.market_cap:,.0f} {cand.currency or '?'} is under the "
                f"floor of {defn.min_market_cap:,.0f} (name's own currency, no FX)"))
            continue
        if cand.history_years is not None and cand.history_years < defn.min_history_years:
            removals.append(Removal(
                cand.ticker, RULE_SIZE_AND_HISTORY,
                f"{cand.history_years:.1f}y of history is under the "
                f"{defn.min_history_years:.0f}y floor"))
            continue
        kept.append(cand)
    return kept, removals


def rule_exclusions(candidates: list[Candidate], defn: CohortDefinition
                    ) -> tuple[list[Candidate], list[Removal]]:
    """Financials and REITs, when the definition says so - and, for a cohort narrowed to a GICS
    sub-industry (COHORT-3), the names outside it, each removal saying which and why."""
    kept, removals = [], []
    wanted = {s.lower() for s in defn.gics_subindustry}
    for cand in candidates:
        keyword = excluded_by(defn, cand.sector, cand.industry)
        if keyword:
            removals.append(Removal(
                cand.ticker, RULE_EXCLUSIONS,
                f"excluded as {keyword} (sector {cand.sector or '?'}, industry "
                f"{cand.industry or '?'})"))
        elif wanted and not cand.gics_subindustry:
            removals.append(Removal(
                cand.ticker, RULE_EXCLUSIONS,
                f"no GICS sub-industry label, so it cannot be shown to belong to "
                f"{', '.join(defn.gics_subindustry)} (not counted as a match or a mismatch)"))
        elif wanted and cand.gics_subindustry.lower() not in wanted:
            removals.append(Removal(
                cand.ticker, RULE_EXCLUSIONS,
                f"GICS sub-industry {cand.gics_subindustry!r} is not "
                f"{', '.join(defn.gics_subindustry)} (industry {cand.industry or '?'})"))
        else:
            kept.append(cand)
    return kept, removals


def rule_no_data(candidates: list[Candidate], defn: CohortDefinition
                 ) -> tuple[list[Candidate], list[Removal]]:
    """Names with nothing for the lenses to read — they would abstain anyway."""
    kept, removals = [], []
    for cand in candidates:
        missing = [f for f in REQUIRED_FIELDS if not getattr(cand, f, None)]
        if missing:
            removals.append(Removal(
                cand.ticker, RULE_NO_DATA,
                f"no {', '.join(missing)} from any source — every lens would abstain"))
        else:
            kept.append(cand)
    return kept, removals


# The order IS the contract. Tests assert this tuple, not a hand-rolled sequence of calls.
RULES = (
    (RULE_ONE_LINE, rule_one_line),
    (RULE_SIZE_AND_HISTORY, rule_size_and_history),
    (RULE_EXCLUSIONS, rule_exclusions),
    (RULE_NO_DATA, rule_no_data),
)


def clean(candidates: list[Candidate], defn: CohortDefinition
          ) -> tuple[list[Candidate], list[Removal]]:
    """Run all four rules in order. Returns the survivors and every removal, in order."""
    kept = list(candidates)
    removals: list[Removal] = []
    for _, rule in RULES:
        kept, dropped = rule(kept, defn)
        removals.extend(dropped)
    return kept, removals


def wide_hint_for_index(members: list[Candidate], defn: CohortDefinition) -> str:
    """What would narrow a too-wide cohort built from the index, from the members themselves.

    The floor is NOT offered as the fix: it is set per cohort by a stated rule and is never tuned
    to a count. A narrower CODE is, when the definition has more than one; a single EODHD industry
    is already the finest label the index carries, and the sentence says so instead of inventing
    one. The exchanges a narrower rule could name come with their counts.
    """
    by_code: dict[str, int] = {}
    by_market: dict[str, int] = {}
    for m in members:
        by_code[m.industry] = by_code.get(m.industry, 0) + 1
        by_market[m.exchange] = by_market.get(m.exchange, 0) + 1
    if len(by_code) > 1:
        parts = ", ".join(f"{code} {n}" for code, n in
                          sorted(by_code.items(), key=lambda kv: (-kv[1], kv[0])))
        code_hint = (f"Narrower code: this cohort spans {len(by_code)} codes ({parts}); any one "
                     f"of them, or a subset, is a narrower cohort.")
    else:
        by_sub: dict[str, int] = {}
        for m in members:
            if m.gics_subindustry:
                by_sub[m.gics_subindustry] = by_sub.get(m.gics_subindustry, 0) + 1
        if len(by_sub) > 1:
            parts = ", ".join(f"{s} {n}" for s, n in
                              sorted(by_sub.items(), key=lambda kv: (-kv[1], kv[0]))[:6])
            code_hint = (f"Narrower code: {next(iter(by_code))!r} is one EODHD industry, but its "
                         f"GICS sub-industries are {parts}; naming one under gics_subindustry "
                         f"gives a narrower cohort.")
        else:
            code_hint = (f"There is no narrower code: {next(iter(by_code))!r} is already the "
                         f"finest industry label the index carries.")
    venues = ", ".join(f"{m} {n}" for m, n in
                       sorted(by_market.items(), key=lambda kv: (-kv[1], kv[0]))[:6])
    return (f"{code_hint} By venue: {venues}. Naming fewer exchanges in the definition would "
            f"also cut it. The USD floor is not the lever - it is set by a stated rule, not "
            f"tuned to a count. Not truncated.")


def size_verdict(members: list[Candidate], defn: CohortDefinition,
                 pool_by_exchange: dict[str, int] | None = None, *,
                 index_path: bool = False) -> SizeVerdict:
    """Thin, wide, or usable — and what would fix it. Never pads and never truncates.

    A thin cohort is offered the exchange that would fill it, chosen from what the pool
    actually contained rather than from a guess: ``pool_by_exchange`` counts the names the
    rule matched on exchanges the definition did NOT list, so the suggestion is a number
    the owner can check.
    """
    n = len(members)
    if n < MIN_MEMBERS:
        need = MIN_MEMBERS - n
        extra = sorted((pool_by_exchange or {}).items(), key=lambda kv: -kv[1])
        if index_path and defn.all_index_exchanges:
            hint = ("It already draws on every index market except Sao Paulo, so no other "
                    "exchange can fill it - the industry code is the thing to widen, not the "
                    "venue, and the USD floor is not the lever (it is set by a stated rule, "
                    "not tuned to a count).")
        elif extra:
            best, count = extra[0]
            enough = "would fill it" if count >= need else f"would add {count}, still short"
            hint = (f"Adding {best} {enough} ({count} more name(s) matched the same "
                    f"industry there).")
        else:
            hint = ("No other exchange in the source held a matching name — the industry "
                    "code is the thing to widen, not the venue.")
        return SizeVerdict("thin", n, f"Need {need} more. {hint} Not padded.")
    if n > MAX_MEMBERS and index_path:
        return SizeVerdict("wide", n, wide_hint_for_index(members, defn))
    if n > MAX_MEMBERS:
        hint = (f"Narrow the industry: this cohort names "
                f"{', '.join(defn.industry_as_written or defn.industry)}. Splitting the "
                f"broadest of those into its sub-industry would cut it. Not truncated.")
        return SizeVerdict("wide", n, hint)
    return SizeVerdict("ok", n)
