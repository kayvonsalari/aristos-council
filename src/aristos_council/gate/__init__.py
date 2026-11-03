"""ABSOLUTE-GATE-1: the exclusion gate (docs/DESIGN_absolute.md, section b).

    from aristos_council.gate import evaluate_gate, load_gate

Pure functions over the ``Fundamentals`` the page already fetched. No model, no vote, no verdict.
``python -m aristos_council.gate calibrate`` runs it over the built cohort members.
"""
from .definition import GateDefinition, GateDefinitionError, load_gate, parse_gate
from .engine import (EXCLUDED, NOT_COVERED, NOT_ENOUGH_DATA, QUALIFIES, GateResult, RuleResult,
                     evaluate_gate)

__all__ = ["GateDefinition", "GateDefinitionError", "GateResult", "RuleResult", "evaluate_gate",
           "load_gate", "parse_gate", "EXCLUDED", "QUALIFIES", "NOT_ENOUGH_DATA", "NOT_COVERED"]
