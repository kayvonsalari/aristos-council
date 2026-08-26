"""COST-3 — what a run ACTUALLY cost, measured rather than estimated.

Until now every figure the app showed was ``estimate_cost(n) = n × $0.19`` — a flat
per-name guess. Nothing ever recorded the real number, so nobody could tell whether the
estimate was any good, and a systematically wrong estimate would have gone on being
quoted for ever.

Two rules, both of them the house discipline applied to money:

* **Measured, not modelled.** The token counts come from the provider's own
  ``usage_metadata`` on each response. If the runner cannot supply them (every test fake,
  and any provider that stops returning them) the meter records the call and reports the
  cost as UNKNOWN — never zero, and never the estimate wearing the actual's label.
* **A price we do not have is not a price of zero.** A model missing from the table
  prices to ``None`` and the run says so. Rates change and this table is hand-maintained;
  silently pricing an unknown model at $0 would understate a bill, which is the one
  direction an error here must never go.

Rates are USD per MILLION tokens and are overridable per model without a code change:

    ARISTOS_PRICE_<MODEL>="<input_per_mtok>,<output_per_mtok>"

where ``<MODEL>`` is the model id upper-cased with non-alphanumerics turned to
underscores (``claude-haiku-4-5`` -> ``ARISTOS_PRICE_CLAUDE_HAIKU_4_5``).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

# USD per 1,000,000 tokens, (input, output). Hand-maintained: verify against the current
# price list before trusting a total, and override via env rather than editing in a hurry.
# A model absent from here prices to None — the run reports "not priced", not "$0.00".
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-opus-4-1": (15.00, 75.00),
}

_PER_MTOK = 1_000_000.0


def _normalise(model: str) -> str:
    """``anthropic:claude-haiku-4-5`` -> ``claude-haiku-4-5``. The provider prefix is a
    LangChain routing detail, not part of the model's identity for pricing."""
    return (model or "").split(":", 1)[-1].strip()


def _env_override(model: str) -> Optional[tuple[float, float]]:
    key = "ARISTOS_PRICE_" + re.sub(r"[^A-Za-z0-9]", "_", _normalise(model)).upper()
    raw = os.environ.get(key)
    if not raw:
        return None
    try:
        inp, out = (float(x) for x in raw.split(",", 1))
    except (TypeError, ValueError):
        return None
    return inp, out


def rate_for(model: str) -> Optional[tuple[float, float]]:
    """``(input, output)`` USD per million tokens, or ``None`` if the model is unpriced."""
    return _env_override(model) or MODEL_PRICES.get(_normalise(model))


def price_call(model: str, input_tokens: int, output_tokens: int) -> Optional[float]:
    """USD for one call, or ``None`` when the model has no rate. Never guesses."""
    rate = rate_for(model)
    if rate is None:
        return None
    inp, out = rate
    return (input_tokens * inp + output_tokens * out) / _PER_MTOK


@dataclass
class CallCost:
    model: str
    input_tokens: int
    output_tokens: int
    usd: Optional[float]          # None = the model is not in the price table


@dataclass
class CostMeter:
    """Accumulates what the model calls actually cost.

    ONE meter is shared by every tier of a runner set, because the bill for narrating a
    name is the WHOLE council pass for it — specialists, critic and the narrator — not
    the narrator's call alone. ``since`` lets a caller price one PHASE of a run without
    resetting anything the rest of the run is still counting."""

    calls: list[CallCost] = field(default_factory=list)

    # -- recording ---------------------------------------------------------- #
    def record(self, model: str, usage) -> None:
        """Record one call from a provider ``usage_metadata`` mapping.

        A call with no usable usage is STILL recorded (so the call count stays true) with
        ``usd=None`` — an unmeasured call must not silently read as a free one."""
        inp = out = 0
        if isinstance(usage, dict):
            inp = int(usage.get("input_tokens") or 0)
            out = int(usage.get("output_tokens") or 0)
        usd = price_call(model, inp, out) if (inp or out) else None
        self.calls.append(CallCost(model=model, input_tokens=inp,
                                   output_tokens=out, usd=usd))

    # -- reading ------------------------------------------------------------ #
    def mark(self) -> int:
        """A cursor into the call log, for pricing one phase of a longer run."""
        return len(self.calls)

    def since(self, mark: int) -> "CostTotal":
        return _total(self.calls[mark:])

    def total(self) -> "CostTotal":
        return _total(self.calls)


@dataclass
class CostTotal:
    """What a set of calls cost. ``usd is None`` means NOT MEASURABLE — distinct from
    ``0.0``, which would claim the calls were free."""

    calls: int
    input_tokens: int
    output_tokens: int
    usd: Optional[float]
    unpriced_calls: int

    @property
    def complete(self) -> bool:
        """True when every call in the set carried a price."""
        return self.calls > 0 and self.unpriced_calls == 0


def _total(calls: list[CallCost]) -> CostTotal:
    priced = [c for c in calls if c.usd is not None]
    return CostTotal(
        calls=len(calls),
        input_tokens=sum(c.input_tokens for c in calls),
        output_tokens=sum(c.output_tokens for c in calls),
        # A partially-priced set reports the part it COULD price, and says how many it
        # could not, rather than collapsing to None and losing a real measurement.
        usd=(sum(c.usd for c in priced) if priced else None),
        unpriced_calls=sum(1 for c in calls if c.usd is None),
    )


# --------------------------------------------------------------------------- #
# Wording — one place, so every surface says the same thing about the same number
# --------------------------------------------------------------------------- #
def cost_phrase(usd: float, n_names: int) -> str:
    """``"$0.95 total (one charge, about $0.19 a name)"``.

    A bare "est. $0.95" cannot be read: it could be the total, the per-name rate, or the
    per-lens rate. It is the TOTAL, once, for all of the sections — so the wording says
    total, says one charge, and gives the per-name figure explicitly rather than leaving
    the reader to divide."""
    if n_names <= 0:
        return f"${usd:.2f} total"
    each = usd / n_names
    return f"${usd:.2f} total (one charge, about ${each:.2f} a name)"


# A divergence past this is worth saying out loud: the estimate is a flat per-name
# constant, so a persistent gap means the constant is wrong and should be re-derived.
DIVERGENCE_LIMIT = 0.25


def divergence(actual: Optional[float], estimated: Optional[float]) -> Optional[float]:
    """Signed relative gap of actual vs estimate, or ``None`` if either is unavailable."""
    if actual is None or not estimated:
        return None
    return (actual - estimated) / estimated


def final_cost_phrase(actual: Optional[float], n_names: int = 0) -> str:
    """What the REPORT says a finished run cost — the final figure, and nothing else.

    COST-4. The header used to stack two parentheticals and three numbers:
    "actual $1.04 total (one charge, about $0.21 a name) (estimated $0.95)". The estimate
    is a DECISION INPUT — it belongs on the button and in the confirm panel, before the
    spend — and has no bearing on the record of what happened. It keeps its place in the
    run flow (see ``actual_vs_estimate``); it leaves the document."""
    if actual is None:
        return "cost not measured"
    return cost_phrase(actual, n_names).replace(" total ", " ", 1)


def actual_vs_estimate(actual: Optional[float], estimated: Optional[float],
                       n_names: int = 0) -> str:
    """The one-line spend report the RUN FLOW shows — actual against estimate, and the
    divergence flag. Estimator feedback, deliberately NOT in the report (COST-4)."""
    if actual is None:
        return (f"estimated ${estimated:.2f} total — actual cost not measured"
                if estimated else "actual cost not measured")
    lead = f"actual {cost_phrase(actual, n_names)}"
    if not estimated:
        return lead
    gap = divergence(actual, estimated)
    tail = f" (estimated ${estimated:.2f})"
    if gap is not None and abs(gap) > DIVERGENCE_LIMIT:
        direction = "over" if gap > 0 else "under"
        tail += (f" — that is {abs(gap) * 100:.0f}% {direction} the estimate; "
                 "the per-name estimate looks wrong")
    return lead + tail
