"""ABSOLUTE-VALUE-1 - the arithmetic of the price test and the return range (docs/DESIGN_absolute.md,
sections d, e, f). Every number the new company-page section prints is computed here and nowhere else
(house rule 1). Pure functions: no I/O, no model, no vote.

The one equation. A company with free cash flow per share ``B`` whose cash flow grows by the yearly
rates ``g_1 .. g_10`` and then stays flat for ever is worth, at a discount rate ``d``:

    value(d) = sum for t = 1..10 of [ B * prod(1+g_i, i<=t) / (1+d)^t ]  +  [ B * prod(1+g_i, i<=10) / d ] / (1+d)^10

Two questions are the same equation:
  * implied growth: which constant ``g`` makes value(hurdle) equal to the price?  (value rises with g)
  * the return:     which discount rate ``r`` makes value(r) equal to the price, for a given path of
                    growth rates?  (value falls as r rises)
Both are solved by bisection inside a stated range; outside it the answer is None ("not solvable").
"""
from __future__ import annotations

from typing import Optional, Sequence

HORIZON = 10
IMPLIED_GROWTH_RANGE = (-0.10, 0.40)
RETURN_RANGE = (0.0001, 0.60)
ITERATIONS = 200


def path_value(base: float, rates: Sequence[float], discount: float) -> float:
    """Present value of the growth path at ``discount``: the yearly cash flows, then a flat perpetuity
    from the last one valued at the same rate. ``discount`` must be positive."""
    cash, total = float(base), 0.0
    for t, g in enumerate(rates, start=1):
        cash *= 1.0 + g
        total += cash / (1.0 + discount) ** t
    return total + cash / discount / (1.0 + discount) ** len(rates)


def _bisect(fn, lo: float, hi: float, target: float, *, increasing: bool, iterations: int) -> Optional[float]:
    """The ``x`` in [lo, hi] with fn(x) == target for a monotone ``fn``, or None when the target is
    outside fn(lo)..fn(hi)."""
    f_lo, f_hi = fn(lo), fn(hi)
    inside = (f_lo <= target <= f_hi) if increasing else (f_hi <= target <= f_lo)
    if not inside:
        return None
    for _ in range(iterations):
        mid = (lo + hi) / 2.0
        below = fn(mid) < target
        if below == increasing:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def implied_growth(base: float, price: float, hurdle: float, *, horizon: int = HORIZON,
                   bounds: tuple = IMPLIED_GROWTH_RANGE, iterations: int = ITERATIONS) -> Optional[float]:
    """The constant yearly growth, for ``horizon`` years, that the price requires if the buyer earns
    exactly ``hurdle``. None when ``base``, ``price`` or ``hurdle`` is not positive, or the answer lies
    outside ``bounds`` (not solvable)."""
    if base is None or price is None or hurdle is None or base <= 0 or price <= 0 or hurdle <= 0:
        return None
    return _bisect(lambda g: path_value(base, [g] * horizon, hurdle), bounds[0], bounds[1], price,
                   increasing=True, iterations=iterations)


def path_return(base: float, price: float, rates: Sequence[float], *,
                bounds: tuple = RETURN_RANGE, iterations: int = ITERATIONS) -> Optional[float]:
    """The yearly rate ``r`` at which the growth path is worth exactly ``price``. None when ``base`` or
    ``price`` is not positive, or ``r`` lies outside ``bounds`` (0.01% to 60%)."""
    if base is None or price is None or base <= 0 or price <= 0 or not rates:
        return None
    return _bisect(lambda r: path_value(base, rates, r), bounds[0], bounds[1], price,
                   increasing=False, iterations=iterations)


def constant_path(growth: float, horizon: int = HORIZON) -> list[float]:
    return [float(growth)] * horizon


def downside_path(fast_rate: float, bond_yield: float, *, horizon: int = HORIZON,
                  fast_years: int = 2) -> list[float]:
    """The downside case: ``fast_years`` years at ``fast_rate``, then a straight line to ``bond_yield``
    reached in the last year of the horizon (flat after, which the value formula already assumes).
    Year t beyond the fast years is  fast + (bond - fast) * (t - fast_years) / (horizon - fast_years)."""
    span = horizon - fast_years
    return [float(fast_rate) if t <= fast_years
            else float(fast_rate) + (float(bond_yield) - float(fast_rate)) * (t - fast_years) / span
            for t in range(1, horizon + 1)]


def free_cash_flow_per_share(operating_cash_flow: Optional[float], capital_spending: Optional[float],
                             shares: Optional[float]) -> Optional[float]:
    """(operating cash flow minus capital spending) / shares. Capital spending is taken as a positive
    outflow whatever sign the provider writes it with. None when a figure is missing or the share count
    is not positive."""
    if operating_cash_flow is None or capital_spending is None or shares is None or shares <= 0:
        return None
    return (operating_cash_flow - abs(capital_spending)) / shares


def compound_rate(start: Optional[float], end: Optional[float], steps: int) -> Optional[float]:
    """(end / start) ** (1 / steps) - 1; None unless both are positive and steps >= 1."""
    if start is None or end is None or steps < 1 or start <= 0 or end <= 0:
        return None
    return (end / start) ** (1.0 / steps) - 1.0
