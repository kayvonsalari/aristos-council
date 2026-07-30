"""DEPRECATED internal alias for :mod:`aristos_council.fund_profile`.

The feature formerly called "Company Check" is user-visibly **Fund Profile**
(FUND-PROFILE-1). The rename covers every user-visible string — tab, headers, report
titles, download filenames — but the INTERNAL id is kept importable here so saved run
records, the acceptance script, and any external caller keep working without a migration.

New code imports from ``aristos_council.fund_profile``.
"""

from __future__ import annotations

from .fund_profile import (  # noqa: F401  (re-exported for backwards compatibility)
    CompanyCheckResult,
    CohortMember,
    DataIntegrity,
    FactorCell,
    FundProfileResult,
    GateCell,
    Identity,
    IdentityField,
    ScreenCell,
    _expense_ratio_gloss,
    _fmt_num,
    _gate_cells,
    _latest_reference_run,
    _position_phrase,
    cohort_fit,
    cohort_median,
    cohort_member_line,
    detect_asset_kind,
    fee_display,
    fit_warning,
    format_company_check,
    format_factor_value,
    format_fund_profile,
    format_median,
    fund_size_display,
    identity_rows,
    median,
    run_company_check,
    run_fund_profile,
    strategies_for_asset_kind,
)

__all__ = [
    "CohortMember",
    "CompanyCheckResult",
    "DataIntegrity",
    "FactorCell",
    "FundProfileResult",
    "GateCell",
    "Identity",
    "IdentityField",
    "ScreenCell",
    "cohort_fit",
    "cohort_median",
    "cohort_member_line",
    "detect_asset_kind",
    "fee_display",
    "fit_warning",
    "format_company_check",
    "format_factor_value",
    "format_fund_profile",
    "format_median",
    "fund_size_display",
    "identity_rows",
    "median",
    "run_company_check",
    "run_fund_profile",
    "strategies_for_asset_kind",
]
