"""COHORT-1 — rule-defined cohorts: built mechanically, checked, frozen, versioned.

A cohort is a RULE. It lives in ``data/local/cohorts/definitions.yaml``, it is built from a
source by four cleanup rules applied in a fixed order, it is quality-checked before anyone
is allowed to trust it, and then it is frozen as ``members.csv`` and versioned. Nothing
downstream reads the definition — everything reads the frozen membership, so editing a
rule can never silently change what a past run graded.

No part of this package imports langchain, langgraph, an agent or a runner. The quality
report ranks with the deterministic ranker only, and the pipeline it needs for that is
imported inside the function that uses it. ``tests/test_cohorts_no_llm.py`` asserts it.

    python -m aristos_council.cohorts build --all
    python -m aristos_council.cohorts check --name "Pharma EU-US"
    python -m aristos_council.cohorts diff  --name "Pharma EU-US"
"""
from __future__ import annotations

from .cleanup import RULES, Removal, SizeVerdict, clean, size_verdict
from .definitions import (DEFAULT_EXCLUDE, MAX_MEMBERS, MIN_MEMBERS, CohortDefinition,
                          DefinitionError, find_definition, load_definitions)
from .freeze import (FrozenCohort, current_version, existing_versions, next_version,
                     read_members, register_universe, write_members)
from .quality import Check, QualityReport, RankSetup, run_checks
from .source import Candidate, EODHDSource, SourceProbe, build_pool
from .symbols import yahoo_symbol, yahoo_symbols

__all__ = [
    "CohortDefinition", "DefinitionError", "load_definitions", "find_definition",
    "DEFAULT_EXCLUDE", "MIN_MEMBERS", "MAX_MEMBERS",
    "Candidate", "EODHDSource", "SourceProbe", "build_pool",
    "RULES", "Removal", "SizeVerdict", "clean", "size_verdict",
    "Check", "QualityReport", "RankSetup", "run_checks",
    "FrozenCohort", "current_version", "existing_versions", "next_version",
    "read_members", "register_universe", "write_members",
    "yahoo_symbol", "yahoo_symbols",
]
