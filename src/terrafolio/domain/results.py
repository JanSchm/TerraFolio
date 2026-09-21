"""What a run produces: the twelve headline tiles, the holdings and the audit trail.

Field names and nullability follow ``docs/api.md`` §8, so these models *are* the
stored result rather than a second spelling of it.

**Undefined values are ``None`` here, never ``0.0``.** The numeric core works in
numpy and represents an undefined IRR as ``NaN`` with a defined-mask; the
conversion to ``None`` happens once, where arrays become models, and from there
it serialises to JSON ``null`` and renders as an em dash. A zero would be a
different, plausible, wrong number — §13 requires the distinction, and it is
easy to lose at each hand-off.

Money carries an explicit ``_m`` suffix, per epic §5's rule for the HTTP
boundary: the numeric core is in euros, everything here is in €m.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Final

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer
from pydantic.alias_generators import to_camel

from terrafolio.domain.enums import (
    Effort,
    RunStatus,
    Stage,
    Technology,
    WarningCode,
    WarningSeverity,
)
from terrafolio.domain.mandate import Mandate

__all__ = [
    "ConvergencePoint",
    "FeasibilityWarning",
    "Holding",
    "PortfolioAggregates",
    "RunProvenance",
    "RunRecord",
    "WarningCodeName",
]

RESULT_CONFIG: Final = ConfigDict(
    frozen=True,
    extra="forbid",
    alias_generator=to_camel,
    validate_by_alias=True,
    validate_by_name=True,
    serialize_by_alias=True,
    # A NaN reaching a result model means the NaN -> None conversion was missed
    # upstream. Rejecting it here turns a silent zero-or-nonsense into a failure
    # at the boundary that owns the conversion.
    allow_inf_nan=False,
)


def _warning_code_by_name(value: Any) -> Any:
    """Accept the name, which is what the wire and the store carry."""
    if isinstance(value, str) and not value.isdigit():
        try:
            return WarningCode[value]
        except KeyError:
            known = ", ".join(code.name for code in WarningCode)
            raise ValueError(f"unknown warning code {value!r}; expected one of {known}") from None
    return value


WarningCodeName = Annotated[
    WarningCode,
    BeforeValidator(_warning_code_by_name),
    PlainSerializer(lambda code: code.name, return_type=str),
]
"""A :class:`WarningCode` that crosses every boundary as its **name**.

``docs/api.md`` pins ``{"code": "CAPACITY_BELOW_TARGET"}``, and the reason is
durability rather than readability: runs are immutable and addressable
indefinitely (§11), so an ordinal that shifted when a code was inserted would
silently rewrite the meaning of every run already stored. The integer exists
only to make the enum's ordering the severity ordering.
"""


class FeasibilityWarning(BaseModel):
    """One §5.4 warning, as the preview and the result both report it."""

    model_config = RESULT_CONFIG

    code: WarningCodeName
    severity: WarningSeverity
    message: str


class Holding(BaseModel):
    """One selected project, carrying exactly the §7.4 columns.

    The table and ``holdings.csv`` are generated from this same model so they
    cannot disagree (``docs/api.md`` §8.2).
    """

    model_config = RESULT_CONFIG

    id: str
    name: str
    country: str
    technology: Technology
    stage: Stage
    capacity_mw: float
    cod_year: int
    total_capex_m: float = Field(alias="totalCapex_m")
    equity_m: float = Field(alias="equity_m")
    gearing: float
    net_capacity_factor: float
    annual_generation_gwh: float
    lcoe: float
    ppa_share: float
    equity_irr: float | None
    """``None`` where the project's equity cash flow has no sign change (§13)."""
    min_dscr: float | None
    """``None`` for an unlevered project, which has no debt service to cover."""
    development_risk_score: float
    locked: bool
    selected: bool


class PortfolioAggregates(BaseModel):
    """The twelve §7.1 headline tiles, with what each is compared against.

    Every field a mandate constrains is reported **with its breach** rather than
    hidden: a locked set that breaches a concentration cap still runs, and the
    breach surfaces on the tile (§13).
    """

    model_config = RESULT_CONFIG

    project_count: int
    solar_count: int
    wind_count: int
    capacity_mw: float
    solar_share: float
    """Capacity-weighted, so offshore wind counts as wind (§5.1)."""
    total_capex_m: float = Field(alias="totalCapex_m")
    senior_debt_m: float = Field(alias="seniorDebt_m")
    equity_m: float = Field(alias="equity_m")
    gearing: float
    capital_deployed: float
    equity_irr: float | None
    """Solved **once**, on the winning chromosome, over the hold-truncated series.

    Distinct from the equity-weighted approximation the objective optimises
    (§10.3): the two differ, and this is the one the tile shows.
    """
    moic: float | None
    weighted_lcoe: float
    annual_generation_gwh: float
    co2_avoided_kt: float
    merchant_share: float
    """Capex-weighted share of revenue not under contract."""
    weighted_risk_score: float
    worst_min_dscr: float | None
    country_shares: dict[str, float]
    """``countryCode`` -> capex share, ordered by code."""
    largest_country_code: str | None
    largest_country_share: float
    thirty_year_fcfe_m: float = Field(alias="thirtyYearFcfe_m")
    fitness: float
    """The §10.2 score of the winning chromosome, quantised to 6 dp."""


class ConvergencePoint(BaseModel):
    """One generation's best and mean fitness, for the §6 convergence chart."""

    model_config = RESULT_CONFIG

    generation: int
    best_fitness: float
    mean_fitness: float


class RunProvenance(BaseModel):
    """Everything needed to reproduce a run exactly (§12).

    A run missing its resolved ``seed`` is not a valid run (epic §5), so the
    field is required rather than optional: a run that cannot explain its own
    numbers should not be storable.
    """

    model_config = RESULT_CONFIG

    seed: int
    """The **resolved** seed, whether the caller supplied it or the server drew it."""
    pipeline_hash: str
    file_hashes: dict[str, str]
    """``id`` -> content hash, so an edit to any file is visible in the record."""
    assumption_set_id: str
    assumption_set_hash: str
    engine_version: str
    numpy_version: str
    blas_threads: int
    """Recorded because it changes reduction order, and so the result."""
    python_version: str
    platform: str


class RunRecord(BaseModel):
    """A stored run. Immutable and addressable: the same id always reopens it (§11).

    Holds the mandate that produced it, so a result is never orphaned from the
    question it answers — and the run controls that the mandate itself does not
    carry (``docs/api.md`` §6.2).
    """

    model_config = RESULT_CONFIG

    run_id: str
    run_ref: str
    """The short human label shown on the result screen, e.g. ``A-4``."""
    status: RunStatus
    created_at: dt.datetime
    duration_ms: int | None
    mandate: Mandate
    locked_ids: tuple[str, ...]
    excluded_ids: tuple[str, ...]
    effort: Effort
    selected_ids: tuple[str, ...]
    aggregates: PortfolioAggregates | None
    """Absent while the run is still in flight (``docs/api.md`` §8)."""
    holdings: tuple[Holding, ...]
    cashflow_30y_m: tuple[float, ...] = Field(alias="cashflow30Y_m")
    """The 30-year portfolio FCFE, **excluding** any terminal value.

    Kept rigorously apart from :attr:`cashflow_hold_m`. This is the series the
    chart and the CSV show and the one ``thirtyYearFcfe_m`` sums; conflating the
    two is the single most likely silent bug in the feature, which is why they
    are named apart.
    """
    cashflow_hold_m: tuple[float, ...] = Field(alias="cashflowHold_m")
    """The hold-truncated portfolio FCFE, **including** the terminal value.

    The series the portfolio IRR and MOIC are solved over.
    """
    convergence: tuple[ConvergencePoint, ...]
    provenance: RunProvenance
