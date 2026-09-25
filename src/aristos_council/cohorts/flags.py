"""COHORT-3 - flag, never hide, every correction a cohort list carries.

The market index is read through corrections: lines are counted once under another line's company,
a label is corrected, a size is corrected or refused, an identity is read differently from the
provider's. Each is right often enough to be worth doing and wrong often enough to be worth seeing,
so a cohort list marks every company a correction touched, once, with a symbol, and a legend at the
bottom says what each symbol means FOR THAT COMPANY. A company excluded because its size cannot be
trusted is listed under the legend with its reported figure and the reason, never silently dropped.

    †  counted once, also listed as <other line(s)>; evidence: <reason>
    ‡  industry label corrected; from <old> to <new>; reason, date
    §  size corrected or excluded; reported <figure>; reason, date
    ¶  identity corrected: the provider's PrimaryTicker names a different company; reason, date

Nothing here changes who is in a cohort. It only says, next to a name, what was done to it.
"""
from __future__ import annotations

from dataclasses import dataclass

SYM_MERGED = "†"          # dagger
SYM_LABEL = "‡"           # double dagger
SYM_SIZE = "§"            # section sign
SYM_IDENTITY = "¶"        # pilcrow

LEGEND = (
    (SYM_MERGED, "counted once, also listed as <other line(s)>; evidence: <reason>"),
    (SYM_LABEL, "industry label corrected; from <old> to <new>; reason, date"),
    (SYM_SIZE, "size corrected or excluded; reported <figure>; reason, date"),
    (SYM_IDENTITY, "identity corrected: the provider's PrimaryTicker names a different company; "
                   "reason, date"),
)


@dataclass(frozen=True)
class Flag:
    symbol: str
    note: str


def money(value) -> str:
    return "n/a" if value is None else f"${value / 1e9:,.1f}bn"


def _norm(ticker: str) -> str:
    return (ticker or "").strip().upper()


def flags_for(ticker: str, pool) -> tuple[Flag, ...]:
    """Every correction that touched this company in ``pool`` (a ``market_index.CleanPool``)."""
    key = _norm(ticker)
    out: list[Flag] = []

    merged = pool.merged.get(key) or []
    if merged:
        others = ", ".join(f"{m.ticker} ({m.name})" for m in merged)
        evidence = list(dict.fromkeys(m.evidence for m in merged))
        said = (evidence[0] if len(evidence) == 1
                else "; ".join(f"{m.ticker}: {m.evidence}" for m in merged))
        out.append(Flag(SYM_MERGED, f"counted once, also listed as {others}; evidence: {said}"))

    if key in pool.overridden:
        override = pool.overridden[key]
        was = pool.label_was.get(key, "the provider's label")
        out.append(Flag(SYM_LABEL, f"industry label corrected; from {was} to "
                                   f"{override.gics_subindustry}; {override.reason}, {override.date}"))

    if key in pool.size_corrected:
        reported, correction = pool.size_corrected[key]
        out.append(Flag(SYM_SIZE, f"size corrected; reported {money(reported)}; "
                                  f"{correction.reason}, {correction.date}"))

    alias = pool.aliased.get(key)
    if alias is not None and _norm(alias.primary) == key:
        given = pool.primary_was.get(key) or "another line"
        out.append(Flag(SYM_IDENTITY, f"identity corrected; the provider names {given} as this "
                                      f"line's home, but that is a different company, so it is "
                                      f"counted as its own; {alias.reason}, {alias.date}"))
    return tuple(out)


def symbols(flags) -> str:
    return "".join(f.symbol for f in flags)


def excluded_for(defn, pool) -> list:
    """The companies refused for their SIZE that this cohort would otherwise have considered:
    the same code, the same exchanges, not a financial or a REIT, and - for a cohort narrowed to a
    GICS sub-industry - the same sub-industry. Sorted by ticker."""
    from .definitions import INDEX_EXCLUDED_MARKETS, excluded_by

    wanted_sub = {s.lower() for s in defn.gics_subindustry}
    codes = set(defn.industry)
    allowed = set(defn.exchange_codes)
    out = []
    for e in pool.size_excluded:
        if e.market in INDEX_EXCLUDED_MARKETS:
            continue
        if not defn.all_index_exchanges and e.market not in allowed:
            continue
        if e.industry not in codes or excluded_by(defn, e.sector, e.industry):
            continue
        if wanted_sub and e.sub.lower() not in wanted_sub:
            continue
        out.append(e)
    return sorted(out, key=lambda e: e.ticker)


def excluded_note(e) -> str:
    """One excluded company, as it reads under the legend."""
    head = f"{SYM_SIZE} {e.ticker} ({e.name}): reported {money(e.reported_usd)}; "
    if e.kind == "size correction":
        return head + f"excluded; {e.reason}, {e.date}"
    where = (f"the company remains in the pool as {', '.join(e.remains_as)}" if e.remains_as
             else "the company has no other line in the pool")
    return head + (f"this line was refused by the automatic size check ({e.reason}); {where}")


def legend_lines(members, excluded=()) -> list[str]:
    """The legend for a cohort: what each symbol means, per company, then the excluded companies.
    Empty when nothing was corrected and nothing was excluded."""
    flagged = [m for m in sorted(members, key=lambda c: c.ticker) if getattr(m, "flags", ())]
    if not flagged and not excluded:
        return []
    out = ["Symbols: " + "   ".join(f"{s} {text}" for s, text in LEGEND), ""]
    for m in flagged:
        out.append(f"{m.ticker} {symbols(m.flags)}  {m.name}")
        for f in m.flags:
            out.append(f"    {f.symbol} {f.note}")
    if excluded:
        if flagged:
            out.append("")
        out.append("Excluded, not silently dropped:")
        out.extend(f"    {excluded_note(e)}" for e in excluded)
    return out
