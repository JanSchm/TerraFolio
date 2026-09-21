"""Levelised cost of energy: ``(capex + PV opex) / PV generation``, at the real rate.

Mandate-independent — it turns on the assumption set's discount rate and nothing the
user sets on the mandate screen — which is exactly why ``docs/pipeline-schema.md`` §9
refuses to let a file store it: an assumption-set change has to move it, and a stored
copy would not.

**Two bases, because §9.4 does not say which.** The assumption set carries the choice
as ``interpretation.lcoe_opex_basis`` rather than burying it here:

``real_from_cod``
    Opex in real terms — the declared ``opexPerKwYear`` rate, unescalated — with both
    opex and generation discounted from the project's **own COD**. This is what #6
    specifies and what the shipped set selects.

``nominal_from_base``
    The file's actual escalated opex series, with both legs discounted from the
    pipeline's base year.

Either way capex is undiscounted at ``t0``, as §9.4 writes it, and the discounting is
ordinary — the first operating period is discounted once. See
:mod:`terrafolio.economics.annuity`.

The reference does a third thing: unescalated opex discounted from the **base year**
rather than from COD (1C-10). That is neither of the documented options, so the golden
``lcoeEURPerMWh`` figures are a reference oracle rather than a target for this module;
the variance is reported rather than designed around.
"""

from __future__ import annotations

from typing import Final

import numpy as np

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.domain.conventions import MWH_PER_GWH
from terrafolio.pipeline.arrays import KW_PER_MW, Matrix, ProjectArrays, Vector

__all__ = ["NOMINAL_FROM_BASE", "REAL_FROM_BASE", "REAL_FROM_COD", "lcoe"]

REAL_FROM_COD: Final = "real_from_cod"
"""Unescalated opex, discounted from the project's own COD. The shipped value."""

NOMINAL_FROM_BASE: Final = "nominal_from_base"
"""The file's escalated opex series, discounted from the pipeline base year."""

REAL_FROM_BASE: Final = "real_from_base"
"""Unescalated opex, discounted from the **base year** — what the reference does.

Not one of the two the assumption set's comment describes, and that is the finding:
neither documented basis reproduces ``derived_expectations.lcoeEURPerMWh``, while this
one reproduces all 48 exactly once rounded to the integer the reference stores. It is
supported so that 2C's interpretation sweep has the combination to select, and so the
gap between the shipped default and the reference is a configuration difference rather
than an unexplained variance.
"""


def _discount(periods: Matrix, rate: float) -> Matrix:
    """``(1 + r)^-period``, zero where the period is not an operating one."""
    operating = periods >= 0
    safe = np.where(operating, periods, 0.0)
    factors: Matrix = (1.0 + rate) ** -(safe + 1.0)
    return np.where(operating, factors, 0.0)


def lcoe(arrays: ProjectArrays, assumptions: AssumptionSet) -> Vector:
    """``(n,)`` levelised cost in **€/MWh**.

    ``NaN`` for a project that generates nothing over its life, which no valid file
    describes but which a zero-capacity edge case would otherwise turn into a division
    by zero.
    """
    rate = assumptions.lcoe_real_discount_rate
    basis = assumptions.interpretation.lcoe_opex_basis
    age = (arrays.statements.years - arrays.asset.cod_year[:, None]).astype(np.float64)

    from_base = np.broadcast_to(
        np.arange(arrays.statements.years.shape[-1], dtype=np.float64),
        arrays.statements.years.shape,
    )
    real_opex = np.where(
        age >= 0,
        (arrays.asset.capacity_mw * KW_PER_MW * arrays.asset.opex_per_kw_year)[:, None],
        0.0,
    )

    if basis == REAL_FROM_COD:
        periods, opex = age, real_opex
    elif basis == REAL_FROM_BASE:
        periods, opex = from_base, real_opex
    elif basis == NOMINAL_FROM_BASE:
        periods, opex = from_base, arrays.statements.income.opex
    else:
        raise ValueError(
            f"interpretation.lcoe_opex_basis is {basis!r}; expected one of "
            f"{REAL_FROM_COD!r}, {REAL_FROM_BASE!r} or {NOMINAL_FROM_BASE!r}"
        )

    factors = _discount(periods, rate)
    present_opex = (opex * factors).sum(axis=-1)
    present_generation_mwh = (arrays.statements.physicals.generation_gwh * factors).sum(
        axis=-1
    ) * MWH_PER_GWH

    produces = present_generation_mwh > 0.0
    cost = arrays.capital.total_capex + present_opex
    return np.where(produces, cost / np.where(produces, present_generation_mwh, 1.0), np.nan)
