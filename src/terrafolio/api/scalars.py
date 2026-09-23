"""The €m boundary, and the join that fills in what the numeric core never sees.

Two jobs, both of which exist exactly once in the system.

**The unit boundary.** Files are €m; the loader converts to euros and everything
downstream — ``ProjectArrays``, the economics, the optimiser — is euros and GWh
in float64. This module converts back, and ``docs/api.md`` §1.2's rule is that
every amount so converted carries an ``_m`` suffix. Epic §5 permits exactly two
conversion points; this is the second one.

**The join.** ``ProjectArrays`` deliberately carries no prose: the core never
needs a project's name, its country's full name, its ISO3 code or its provenance
block, so none of them is in the arrays. ``docs/api.md`` §2's wire record needs
all four. ``LoadResult.files`` holds the validated models in the same order as
``arrays.ids``, so the join is by position — and a test pins those two orderings
together, which is what makes that safe.

Doing both in one place is deliberate: ``GET /pipeline`` and a stored run's
``holdings`` carry the **same** per-project record (§8.2), and building it twice
is how the drawer ends up showing a different capture price from the table.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.domain.conventions import EUR_PER_EUR_MILLION
from terrafolio.domain.project_file import ProjectFile
from terrafolio.domain.results import Holding, ProjectScalars, ProvenanceSummary
from terrafolio.economics.lcoe import lcoe
from terrafolio.economics.returns import ProjectReturns
from terrafolio.pipeline.arrays import ProjectArrays, Vector
from terrafolio.pipeline.derive import annual_generation_gwh, min_dscr

__all__ = ["Candidates", "DerivedColumns", "holding_records", "project_scalars"]


def _millions(euros: float) -> float:
    """Euros to €m. The only arithmetic in this module that changes a unit."""
    return euros / EUR_PER_EUR_MILLION


def _optional(value: float) -> float | None:
    """``NaN`` becomes ``None`` — never ``0.0``, never a sentinel (§1.4).

    The chain is engine ``NaN`` -> API ``null`` -> UI em dash, and it is easy to
    lose at each hand-off. ``RESULT_CONFIG`` sets ``allow_inf_nan=False``, so a
    missed conversion fails here rather than shipping a plausible wrong number.
    """
    return None if np.isnan(value) else float(value)


class DerivedColumns:
    """The mandate-**in**dependent columns, computed once per pipeline load.

    ``lcoe`` and ``minDscr`` do not move with the hold period (§1.6), so they are
    computed once and shared across every ``holdYears`` a client asks for, while
    :class:`~terrafolio.economics.returns.ProjectReturns` is rebuilt per hold.
    Splitting them this way is what stops a cache being keyed without the hold
    period, which is the failure mode epic §5 names.
    """

    __slots__ = ("generation_gwh", "lcoe", "min_dscr", "thirty_year_fcfe")

    def __init__(self, arrays: ProjectArrays, assumptions: AssumptionSet) -> None:
        self.lcoe: Vector = lcoe(arrays, assumptions)
        self.min_dscr: Vector = min_dscr(arrays)
        self.generation_gwh: Vector = annual_generation_gwh(arrays)
        # Undiscounted, no terminal value — `cashflow30Y_m`'s per-project sum,
        # never the hold-truncated series (A-6).
        self.thirty_year_fcfe: Vector = arrays.statements.cash_flow.fcfe.sum(axis=-1)


@dataclass(frozen=True, slots=True, kw_only=True)
class Candidates:
    """The three mandate-independent halves of a loaded pipeline, travelling together.

    ``arrays`` and ``files`` are two views of the same projects in the same
    order, and ``derived`` is computed from the first. Passing them separately
    let a call site pair one load's arrays with another's files, which is a
    silent mis-join rather than an error.
    """

    arrays: ProjectArrays
    files: Sequence[ProjectFile]
    derived: DerivedColumns


def _provenance(file: ProjectFile) -> Mapping[str, ProvenanceSummary]:
    """§2's abbreviated block: basis and confidence per group, without the note.

    The notes are prose sentences and the larger half of the block; they come
    with the statements, where §7.5's detail sheet fetches them on demand. The
    labels the holdings table and drawer need are all in this form.
    """
    return {
        group.value: ProvenanceSummary(
            estimate_basis=entry.estimate_basis, confidence=entry.confidence
        )
        for group, entry in file.provenance.fields.items()
    }


def _fields(candidates: Candidates, returns: ProjectReturns, index: int) -> dict[str, object]:
    """One project's wire record, in €m, as a mapping both models accept."""
    arrays, derived = candidates.arrays, candidates.derived
    file = candidates.files[index]
    payback = returns.payback[index]
    return {
        "id": arrays.ids[index],
        "name": file.name,
        "country": file.location.country,
        "country_code": arrays.location.country_codes[index],
        "iso3": file.location.iso3,
        "lat": float(arrays.location.latitude[index]),
        "lon": float(arrays.location.longitude[index]),
        "technology": arrays.asset.technologies[index],
        "stage": arrays.asset.stages[index],
        "capacity_mw": float(arrays.asset.capacity_mw[index]),
        "cod_year": int(arrays.asset.cod_year[index]),
        "net_capacity_factor": float(arrays.asset.net_capacity_factor[index]),
        "annual_generation_gwh": float(derived.generation_gwh[index]),
        "opex_per_kw_year": float(arrays.asset.opex_per_kw_year[index]),
        "ppa_share": float(arrays.revenue.ppa_share[index]),
        "ppa_tenor_years": int(arrays.revenue.ppa_tenor_years[index]),
        "ppa_price": float(arrays.revenue.ppa_price[index]),
        "country_baseload_price": float(arrays.revenue.country_baseload_price[index]),
        "capture_factor": float(arrays.revenue.capture_factor[index]),
        "capture_price": float(arrays.revenue.capture_price[index]),
        "development_risk_score": float(arrays.execution.development_risk_score[index]),
        "grid_secured": bool(arrays.execution.grid_secured[index]),
        "om_contracted": bool(arrays.execution.om_contracted[index]),
        "currency": arrays.execution.currencies[index],
        "total_capex_m": _millions(float(arrays.capital.total_capex[index])),
        "senior_debt_m": _millions(float(arrays.capital.senior_debt[index])),
        "equity_m": _millions(float(arrays.capital.equity[index])),
        "gearing": float(arrays.capital.gearing[index]),
        "max_gearing": float(arrays.capital.max_gearing[index]),
        # €/kW is a per-unit price, not an amount, so it takes no `_m` (§1.2).
        "capex_per_kw": float(arrays.capex_per_kw[index]),
        "debt_rate": float(arrays.assumptions.debt_rate[index]),
        "debt_tenor_years": int(arrays.assumptions.debt_tenor_years[index]),
        "lcoe": float(derived.lcoe[index]),
        "min_dscr": _optional(float(derived.min_dscr[index])),
        "thirty_year_fcfe_m": _millions(float(derived.thirty_year_fcfe[index])),
        "equity_irr": _optional(float(returns.equity_irr[index])),
        "moic": _optional(float(returns.moic[index])),
        # A period becomes a calendar year here: period 1 is the base year.
        "payback_year": None if np.isnan(payback) else arrays.base_year + int(payback) - 1,
        "provenance": _provenance(file),
    }


def project_scalars(candidates: Candidates, returns: ProjectReturns) -> tuple[ProjectScalars, ...]:
    """Every candidate as ``GET /pipeline``'s ``projects`` entry, ordered by id."""
    return tuple(
        ProjectScalars.model_validate(_fields(candidates, returns, index))
        for index in range(candidates.arrays.count)
    )


def holding_records(
    candidates: Candidates,
    returns: ProjectReturns,
    *,
    eligible: Sequence[bool],
    selected: Sequence[bool],
    locked: Sequence[bool],
) -> tuple[Holding, ...]:
    """The run's own candidate snapshot — §8.2's ``holdings``.

    **One entry per eligible candidate, not per selected project.** §7.4's *show
    all candidates* has to say what the optimiser rejected, and merging against
    the live ``GET /pipeline`` cannot reproduce that once the pipeline has moved.
    Carrying the whole eligible set is what makes a stored run self-contained,
    which is what §11's "a run id reopens the exact result" actually requires.
    """
    return tuple(
        Holding.model_validate(
            {
                **_fields(candidates, returns, index),
                "selected": bool(selected[index]),
                "locked": bool(locked[index]),
            }
        )
        for index in range(candidates.arrays.count)
        if eligible[index]
    )
