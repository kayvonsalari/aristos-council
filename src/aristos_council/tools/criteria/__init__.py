"""Criterion registry — named, pure screen criteria strategies select by name.

See registry.py. The generic screen runner (run_screen) is the strategy-agnostic
replacement for the hardcoded run_dividend_aristocrat_screen.
"""

from .registry import (  # noqa: F401
    REGISTRY,
    AVAILABLE_EVIDENCE,
    Criterion,
    CriterionSelection,
    Evidence,
    run_screen,
    validate_selections,
)
