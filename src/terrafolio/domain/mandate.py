"""The mandate: what the investor asks the optimiser for.

Field names, units and ranges are those ``docs/api.md`` §6.1 pins for the wire,
so this model *is* the request body rather than a second spelling of it. Shares
and rates are fractions of one; only ``availableCapital_m`` carries money, in
€m, with the explicit suffix the epic requires at the HTTP boundary.

Run controls — locks, exclusions, effort, seed — are deliberately **not** here.
``docs/api.md`` §6.2 makes them siblings of the mandate, and they belong to a
run rather than to the question being asked; they live on
:class:`~terrafolio.domain.results.RunRecord`.
"""

from __future__ import annotations

from typing import Annotated, Final

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from terrafolio.domain import mandate_bounds as mb
from terrafolio.domain.enums import RiskAppetite, Stage

__all__ = ["Mandate"]

MANDATE_CONFIG: Final = ConfigDict(
    frozen=True,
    extra="forbid",
    alias_generator=to_camel,
    validate_by_alias=True,
    validate_by_name=True,
    serialize_by_alias=True,
    allow_inf_nan=False,
)
"""Unlike the file config this accepts field names as well as aliases.

A mandate is constructed in Python by the CLI, the preview path and every test;
a project file is only ever parsed from JSON. There is no second spelling of the
file format to worry about here.
"""


def _sorted_unique[T: (str, Stage)](values: tuple[T, ...]) -> tuple[T, ...]:
    """Normalise a set-like field to sorted, deduplicated order.

    Two mandates that differ only in the order someone clicked the country chips
    are the same mandate: they must hash alike, produce the same ETag and reuse
    the same stored run. Normalising on the way in is what makes that true.
    """
    return tuple(sorted(set(values)))


CountrySet = Annotated[
    tuple[str, ...],
    Field(min_length=1),
    AfterValidator(_sorted_unique),
]
StageSet = Annotated[
    tuple[Stage, ...],
    Field(min_length=1),
    AfterValidator(_sorted_unique),
]


class Mandate(BaseModel):
    """Objective, hard constraints and screens — spec §5.1-5.3.

    Ranges are enforced; **steps are not**. Each step is published as
    ``multipleOf`` JSON-schema metadata so the controls and any generated client
    get it for free, but a mandate landing between steps is accepted. See
    :meth:`terrafolio.domain.mandate_bounds.Bound.multiple_of` for why.
    """

    model_config = MANDATE_CONFIG

    # §5.1 Objective.
    available_capital_m: float = Field(
        alias="availableCapital_m",
        ge=mb.AVAILABLE_CAPITAL_M.lo,
        le=mb.AVAILABLE_CAPITAL_M.hi,
        json_schema_extra=mb.AVAILABLE_CAPITAL_M.multiple_of,
    )
    capacity_target_mw: float = Field(
        ge=mb.CAPACITY_TARGET_MW.lo,
        le=mb.CAPACITY_TARGET_MW.hi,
        json_schema_extra=mb.CAPACITY_TARGET_MW.multiple_of,
    )
    solar_share: float = Field(
        ge=mb.SOLAR_SHARE.lo,
        le=mb.SOLAR_SHARE.hi,
        json_schema_extra=mb.SOLAR_SHARE.multiple_of,
    )
    target_irr: float = Field(
        ge=mb.TARGET_IRR.lo,
        le=mb.TARGET_IRR.hi,
        json_schema_extra=mb.TARGET_IRR.multiple_of,
    )
    hold_years: int = Field(
        ge=int(mb.HOLD_YEARS.lo),
        le=int(mb.HOLD_YEARS.hi),
        json_schema_extra=mb.HOLD_YEARS.multiple_of,
    )

    # §5.2 Hard constraints.
    countries: CountrySet
    stages: StageSet
    min_leverage: float = Field(
        ge=mb.MIN_LEVERAGE.lo,
        le=mb.MIN_LEVERAGE.hi,
        json_schema_extra=mb.MIN_LEVERAGE.multiple_of,
    )
    min_dscr: float = Field(
        ge=mb.MIN_DSCR.lo,
        le=mb.MIN_DSCR.hi,
        json_schema_extra=mb.MIN_DSCR.multiple_of,
    )
    max_merchant_share: float = Field(
        ge=mb.MAX_MERCHANT_SHARE.lo,
        le=mb.MAX_MERCHANT_SHARE.hi,
        json_schema_extra=mb.MAX_MERCHANT_SHARE.multiple_of,
    )
    max_country_share: float = Field(
        ge=mb.MAX_COUNTRY_SHARE.lo,
        le=mb.MAX_COUNTRY_SHARE.hi,
        json_schema_extra=mb.MAX_COUNTRY_SHARE.multiple_of,
    )
    max_project_share: float = Field(
        ge=mb.MAX_PROJECT_SHARE.lo,
        le=mb.MAX_PROJECT_SHARE.hi,
        json_schema_extra=mb.MAX_PROJECT_SHARE.multiple_of,
    )
    cod_from: int = Field(
        ge=int(mb.COD_FROM.lo),
        le=int(mb.COD_FROM.hi),
        json_schema_extra=mb.COD_FROM.multiple_of,
    )
    cod_to: int = Field(
        ge=int(mb.COD_TO.lo),
        le=int(mb.COD_TO.hi),
        json_schema_extra=mb.COD_TO.multiple_of,
    )

    # §5.3 Risk and execution screens.
    risk_appetite: RiskAppetite
    grid_secured_only: bool = False
    eur_revenue_only: bool = False
    om_contracted_only: bool = False

    @model_validator(mode="after")
    def _cod_window_is_ordered(self) -> Mandate:
        """An inverted COD window is an error, not a silently empty result.

        Spec §5.2 gives one window with two handles. Letting ``codFrom > codTo``
        through would screen out every project and present it as "no candidates
        pass", which sends the user hunting through the other eight screens.
        """
        if self.cod_from > self.cod_to:
            raise ValueError(
                f"codFrom ({self.cod_from}) is after codTo ({self.cod_to}); the "
                f"COD window would exclude every project"
            )
        return self
