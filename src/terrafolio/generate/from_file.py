"""Read a project file into the numeric core's inputs.

The numeric core takes arrays and scalars and may not import pydantic, so
something has to turn a loaded file into :class:`~terrafolio.model.project
.ProjectInputs` and :class:`~terrafolio.model.project.ProjectStatements`. That
adapter lives here, above the boundary, where it is allowed to know what a file
looks like.

It works from the plain mapping rather than from a validated model on purpose:
the variance report's whole job is to say something useful about a file, and a
file worth reporting on is one that has already loaded.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.domain.enums import Technology
from terrafolio.model.project import ProjectInputs, ProjectStatements
from terrafolio.model.rounding import js_round

__all__ = ["inputs_from_file", "statements_from_file"]

_SERIES: dict[str, tuple[str, str]] = {
    "generation_gwh": ("physicals", "generationGwh"),
    "achieved_price": ("physicals", "achievedPrice"),
    "revenue": ("incomeStatement", "revenue"),
    "opex": ("incomeStatement", "opex"),
    "ebitda": ("incomeStatement", "ebitda"),
    "depreciation": ("incomeStatement", "depreciation"),
    "ebit": ("incomeStatement", "ebit"),
    "interest_expense": ("incomeStatement", "interestExpense"),
    "pbt": ("incomeStatement", "pbt"),
    "tax_expense": ("incomeStatement", "taxExpense"),
    "net_income": ("incomeStatement", "netIncome"),
    "capex": ("cashFlow", "capex"),
    "debt_drawdown": ("cashFlow", "debtDrawdown"),
    "equity_drawdown": ("cashFlow", "equityDrawdown"),
    "fcfe": ("cashFlow", "fcfe"),
    "debt_opening": ("debtSchedule", "opening"),
    "debt_repayment": ("debtSchedule", "repayment"),
    "debt_closing": ("debtSchedule", "closing"),
    "ppe": ("balanceSheet", "ppe"),
}


def inputs_from_file(file: dict[str, Any], assumptions: AssumptionSet) -> ProjectInputs:
    """The model's inputs, taken from a file's own declared fields.

    Everything here is declared except the ramp fraction and the hours-per-year
    constant, which are house-model parameters rather than anything an analyst
    states -- so they come from the assumption set.

    The capture price is recovered by rounding ``countryBaseloadPrice x
    captureFactor`` back to the whole euro the generator produced. Files carry
    the *effective* factor (1C-17) so that §4.3's identity reproduces the
    revenue beside it, and the product lands a hair off the integer for markets
    where the rounding bit.
    """
    asset, revenue = file["asset"], file["revenue"]
    capital, declared = file["capitalStructure"], file["assumptions"]
    generator = assumptions.generator
    return ProjectInputs(
        capacity_mw=float(asset["capacityMw"]),
        cod_year=int(asset["codYear"]),
        base_year=int(declared["baseYear"]),
        net_capacity_factor=float(asset["netCapacityFactor"]),
        opex_per_kw_year=float(asset["opexPerKwYear"]),
        ppa_share=float(revenue["ppaShare"]),
        ppa_price=float(revenue["ppaPrice"]),
        ppa_tenor_years=int(revenue["ppaTenorYears"]),
        capture_price=js_round(revenue["countryBaseloadPrice"] * revenue["captureFactor"]),
        total_capex=float(capital["totalCapex"]),
        senior_debt=float(capital["seniorDebt"]),
        tax_rate=float(declared["taxRate"]),
        depreciation_years=int(declared["depreciationYears"]),
        debt_rate=float(declared["debtRate"]),
        debt_tenor_years=int(declared["debtTenorYears"]),
        degradation_rate=float(declared["degradationRate"]),
        price_escalation=float(declared["priceEscalation"]),
        merchant_escalation=float(declared["merchantEscalation"]),
        opex_escalation=float(declared["opexEscalation"]),
        ramp_factor=generator.ramp_factor,
        hours_per_year_gwh=generator.hours_per_year_gwh,
    )


def statements_from_file(file: dict[str, Any]) -> ProjectStatements:
    """A file's own statements, as the arrays the core compares against.

    ``dscr`` carries nulls, which become NaN -- the same representation the
    model produces outside the debt life, so the two are comparable without a
    sentinel.
    """
    statements = file["statements"]
    arrays = {
        name: np.array(statements[block][key], dtype=np.float64)
        for name, (block, key) in _SERIES.items()
    }
    arrays["dscr"] = np.array(
        [np.nan if value is None else value for value in statements["ratios"]["dscr"]],
        dtype=np.float64,
    )
    return ProjectStatements(**arrays)


def technology_of(file: dict[str, Any]) -> Technology:
    """The file's technology as the enum, for keying an assumption-set table."""
    return Technology(file["asset"]["technology"])
