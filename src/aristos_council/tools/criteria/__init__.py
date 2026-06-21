"""Criterion registry — named, pure screen criteria strategies select by name.

See registry.py. The generic screen runner (run_screen) is the strategy-agnostic
replacement for the hardcoded run_strategy_screen (formerly
run_dividend_aristocrat_screen).
"""

from .registry import (  # noqa: F401
    REGISTRY,
    AVAILABLE_EVIDENCE,
    Criterion,
    CriterionSelection,
    Evidence,
    ParamSpec,
    consumed_fundamentals_fields,
    required_evidence,
    run_screen,
    validate_selections,
)
