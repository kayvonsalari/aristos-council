"""Ephemeral per-run policy overrides — change a disposition setting for ONE run
without spawning a new strategy YAML.

Strategy files stay immutable (the "bump to a new version" guard is correct and
protects verdict reproducibility). This is the missing throwaway path: produce an
in-memory *effective* Strategy from the immutable base plus the UI's per-run
choices, and record exactly what differed so the run is reproducible. The on-disk
YAML is never touched.

Scope (this build): the two disposition controls only —
``policy.partial_pass_allows_hold`` and per-criterion ``is_gating``.
"""

from __future__ import annotations

from .loader import Strategy


def effective_strategy(
    base: Strategy,
    *,
    partial_pass_allows_hold: bool | None = None,
    is_gating: dict[str, bool] | None = None,
) -> Strategy:
    """A DEEP COPY of ``base`` with the given overrides applied.

    ``base`` is never mutated (``model_copy(deep=True)`` copies nested policy and
    criteria). ``None`` / omitted keeps the strategy's own value; an ``is_gating``
    entry only affects the named criterion.
    """
    eff = base.model_copy(deep=True)
    if partial_pass_allows_hold is not None:
        eff.policy.partial_pass_allows_hold = partial_pass_allows_hold
    if is_gating:
        for c in eff.criteria:
            if c.name in is_gating:
                c.is_gating = is_gating[c.name]
    return eff


def applied_overrides(base: Strategy, effective: Strategy) -> dict:
    """The flat record of what ``effective`` CHANGED vs the base file.

    Empty dict ⇒ the run used pure strategy defaults (a no-op). Shape::

        {"partial_pass_allows_hold": <bool>,
         "criteria.<name>.is_gating": <bool>}

    Computed by diffing, so it reflects REAL differences only (toggling a control
    back to the file value records nothing).
    """
    out: dict[str, object] = {}
    if (effective.policy.partial_pass_allows_hold
            != base.policy.partial_pass_allows_hold):
        out["partial_pass_allows_hold"] = effective.policy.partial_pass_allows_hold
    base_gating = {c.name: c.is_gating for c in base.criteria}
    for c in effective.criteria:
        if c.name in base_gating and c.is_gating != base_gating[c.name]:
            out[f"criteria.{c.name}.is_gating"] = c.is_gating
    return out
