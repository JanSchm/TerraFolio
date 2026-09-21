"""What a run produces: the twelve headline tiles, the candidate snapshot and the audit trail.

Field names, nullability and shapes follow ``docs/api.md`` §8, so these models
*are* the stored result rather than a second spelling of it.

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

from collections.abc import Mapping
from typing import Annotated, Any, Final

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    model_validator,
)
from pydantic.alias_generators import to_camel

from terrafolio.domain import file_bounds as fb
from terrafolio.domain.conventions import YEARS, canonical_order
from terrafolio.domain.enums import (
    Confidence,
    Currency,
    Effort,
    EstimateBasis,
    ProvenanceGroup,
    RunStatus,
    Stage,
    Technology,
    WarningCode,
    WarningSeverity,
)
from terrafolio.domain.fields import (
    FREEZE_MAPPING,
    Count,
    Flag,
    Fraction,
    Magnitude,
    Number,
    Positive,
)
from terrafolio.domain.mandate import Mandate

__all__ = [
    "ConvergencePoint",
    "FeasibilityWarning",
    "Holding",
    "PortfolioAggregates",
    "ProjectScalars",
    "ProvenanceSummary",
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


def _sorted_frozen[K: str, V](value: Mapping[K, V]) -> Mapping[K, V]:
    """Freeze a mapping and put it in canonical key order.

    ``docs/api.md`` specifies country shares "ordered by code", and the same
    argument applies to file hashes: a stored run is compared against other
    stored runs, and two identical results should not differ by dictionary
    insertion order.
    """
    return {key: value[key] for key in sorted(value)}


FrozenShares = Annotated[
    Mapping[str, float],
    AfterValidator(_sorted_frozen),
    FREEZE_MAPPING,
    PlainSerializer(dict, return_type=dict[str, float]),
]
FrozenHashes = Annotated[
    Mapping[str, str],
    AfterValidator(_sorted_frozen),
    FREEZE_MAPPING,
    PlainSerializer(dict, return_type=dict[str, str]),
]


IdList = Annotated[tuple[str, ...], AfterValidator(lambda ids: tuple(sorted(set(ids))))]
"""A set of project ids, normalised the way ``Mandate`` normalises its own.

Two runs that differ only in the order ids were collected are the same run, and
§12 compares stored results across releases — an ordering difference that
carries no meaning should not read as a behavioural change.
"""


# --------------------------------------------------------------------------
# Warnings
# --------------------------------------------------------------------------


def _warning_code_by_name(value: Any) -> Any:
    """Accept a code by name, and refuse its ordinal.

    The integer is an implementation detail that makes the enum's ordering the
    severity ordering; it is not an identity. Accepting ``1010`` here would let
    a record written before a code was inserted be reinterpreted as a different
    warning after one was — which is exactly what serialising by name prevents
    on the way out, and the guarantee is only worth having in both directions.
    """
    if isinstance(value, WarningCode):
        return value
    if isinstance(value, str) and not value.isdigit():
        try:
            return WarningCode[value]
        except KeyError:
            known = ", ".join(code.name for code in WarningCode)
            raise ValueError(f"unknown warning code {value!r}; expected one of {known}") from None
    raise ValueError(f"warning codes cross every boundary by name, not by ordinal; got {value!r}")


WarningCodeName = Annotated[
    WarningCode,
    BeforeValidator(_warning_code_by_name),
    PlainSerializer(lambda code: code.name, return_type=str),
]
"""A :class:`WarningCode` that crosses every boundary as its **name**.

``docs/api.md`` pins ``{"code": "CAPACITY_BELOW_TARGET"}``, and the reason is
durability rather than readability: runs are immutable and addressable
indefinitely (§11), so an ordinal that shifted when a code was inserted would
silently rewrite the meaning of every run already stored.
"""


class FeasibilityWarning(BaseModel):
    """One §5.4 warning, as the preview and the result both report it.

    ``severity`` belongs to the code, not to the occurrence. It is on the wire
    because ``docs/api.md`` puts it there, and it may be omitted when building
    one in Python — but it can never contradict the code, or a client would
    render a blocking condition in an informational tone while the rest of the
    system still treated it as blocking.
    """

    model_config = RESULT_CONFIG

    code: WarningCodeName
    severity: WarningSeverity
    message: str

    @model_validator(mode="before")
    @classmethod
    def _default_severity_from_code(cls, data: Any) -> Any:
        if not isinstance(data, Mapping) or "severity" in data:
            return data
        raw = data.get("code")
        code = raw if isinstance(raw, WarningCode) else None
        if code is None and isinstance(raw, str):
            code = WarningCode.__members__.get(raw)
        if code is None:
            return data
        return {**data, "severity": code.severity}

    @model_validator(mode="after")
    def _severity_matches_the_code(self) -> FeasibilityWarning:
        if self.severity is not self.code.severity:
            raise ValueError(
                f"{self.code.name} is {self.code.severity.value}, not "
                f"{self.severity.value}; severity is a property of the code"
            )
        return self


# --------------------------------------------------------------------------
# The candidate snapshot
# --------------------------------------------------------------------------


class ProvenanceSummary(BaseModel):
    """Provenance as the wire carries it: basis and confidence, without the note.

    ``docs/api.md`` §2 abbreviates it here because the free-text notes are the
    larger half of the block and the detail sheet fetches them with the
    statements. The labels the table and drawer need are in this form.
    """

    model_config = RESULT_CONFIG

    estimate_basis: EstimateBasis
    confidence: Confidence


FrozenProvenance = Annotated[
    Mapping[ProvenanceGroup, ProvenanceSummary],
    AfterValidator(_sorted_frozen),
    FREEZE_MAPPING,
    PlainSerializer(dict, return_type=dict[ProvenanceGroup, ProvenanceSummary]),
]


class ProjectScalars(BaseModel):
    """One candidate's full scalar record — ``docs/api.md`` §2's ``projects`` entry.

    Scalars only, never the 30-year arrays: at 300 projects the arrays are 1.8 MB
    against 400 KB for everything else, and §7.5's detail sheet needs only these.
    The arrays live at ``GET /projects/{id}/statements``.

    Derived fields that a *file* may not carry — ``gearing``, ``equity_m``,
    ``capexPerKw``, ``lcoe``, ``minDscr``, ``equityIrr``, ``moic``,
    ``paybackYear`` — are all present here, and that is the point of the rule
    rather than an exception to it: they are computed per run, under a mandate,
    and recorded with the run that computed them.
    """

    model_config = RESULT_CONFIG

    id: str = Field(pattern=fb.ID_PATTERN)
    name: str = Field(min_length=1, max_length=fb.NAME_MAX_LEN)
    country: str = Field(min_length=1, max_length=fb.COUNTRY_NAME_MAX_LEN)
    country_code: str = Field(pattern=fb.COUNTRY_CODE_PATTERN)
    iso3: str = Field(pattern=fb.ISO3_PATTERN)
    lat: Number = Field(ge=-fb.LATITUDE_ABS_MAX, le=fb.LATITUDE_ABS_MAX)
    lon: Number = Field(ge=-fb.LONGITUDE_ABS_MAX, le=fb.LONGITUDE_ABS_MAX)
    technology: Technology
    stage: Stage
    capacity_mw: Positive = Field(le=fb.CAPACITY_MW_MAX)
    cod_year: Count = Field(ge=fb.BASE_YEAR_MIN, le=fb.BASE_YEAR_MAX)
    net_capacity_factor: Fraction
    annual_generation_gwh: Magnitude
    opex_per_kw_year: Positive = Field(le=fb.OPEX_PER_KW_YEAR_MAX)
    ppa_share: Fraction
    ppa_tenor_years: Count = Field(ge=0, le=fb.PPA_TENOR_YEARS_MAX)
    ppa_price: Magnitude = Field(le=fb.PRICE_EUR_PER_MWH_MAX)
    country_baseload_price: Positive = Field(le=fb.PRICE_EUR_PER_MWH_MAX)
    capture_factor: Positive = Field(le=fb.CAPTURE_FACTOR_MAX)
    capture_price: Magnitude = Field(le=fb.PRICE_EUR_PER_MWH_MAX)
    development_risk_score: Number = Field(
        ge=fb.DEVELOPMENT_RISK_SCORE_MIN, le=fb.DEVELOPMENT_RISK_SCORE_MAX
    )
    grid_secured: Flag
    om_contracted: Flag
    currency: Currency
    """The **revenue** currency, which drives the EUR-only screen."""
    total_capex_m: Positive = Field(alias="totalCapex_m")
    senior_debt_m: Magnitude = Field(alias="seniorDebt_m")
    equity_m: Magnitude = Field(alias="equity_m")
    gearing: Fraction
    max_gearing: Fraction
    capex_per_kw: Positive
    debt_rate: Magnitude = Field(le=fb.DEBT_RATE_MAX)
    debt_tenor_years: Count = Field(ge=0, le=fb.DEBT_TENOR_YEARS_MAX)
    lcoe: Magnitude
    min_dscr: Magnitude | None
    """Over the debt life, **excluding the ramp year**. ``None`` without debt."""
    thirty_year_fcfe_m: Number = Field(alias="thirtyYearFcfe_m")
    """Undiscounted sum, carrying **no** terminal value. Signed."""
    equity_irr: Number | None
    """At the mandate's hold period. ``None`` where the series has no sign change.

    Signed: a project can return less than the equity put into it.
    """
    moic: Magnitude | None
    payback_year: Count | None = Field(default=None, ge=fb.BASE_YEAR_MIN, le=fb.BASE_YEAR_MAX)
    provenance: FrozenProvenance

    @model_validator(mode="after")
    def _debt_within_cost(self) -> ProjectScalars:
        if self.senior_debt_m > self.total_capex_m:
            raise ValueError(
                f"seniorDebt_m ({self.senior_debt_m}) exceeds totalCapex_m "
                f"({self.total_capex_m}); a project cannot be more than fully "
                f"debt-funded"
            )
        return self

    @model_validator(mode="after")
    def _provenance_covers_every_group(self) -> ProjectScalars:
        """§7.5's drawer reads a basis per group, so all seven have to be here.

        The same rule ``ProjectFile.Provenance`` enforces on the way in. Without
        it a run stored with a partial block raises ``KeyError`` at render time,
        a long way from whatever produced it.
        """
        missing = [group.value for group in ProvenanceGroup if group not in self.provenance]
        if missing:
            raise ValueError(
                "provenance must cover every group; missing " + ", ".join(sorted(missing))
            )
        return self


class Holding(ProjectScalars):
    """A candidate as a stored run saw it (``docs/api.md`` §8.2).

    ``holdings`` carries **every project in the run's eligible candidate set**,
    not only the selected ones, because that is what makes a stored run
    self-contained — which is what §11's "a run ID reopens the exact result"
    actually requires. §7.4's *show all candidates* toggle, §7.3's map and
    §7.5's drawer each need more than the selected rows, and merging against the
    live pipeline cannot reproduce them once it has moved. A project deleted
    from the pipeline after the run still appears here, with the figures the run
    saw.

    ``holdings.csv`` is this array filtered to :attr:`selected` and projected
    onto the sixteen §7.4 columns, so the table and the export cannot disagree.
    """

    selected: Flag
    """In ``selectedIds``."""
    locked: Flag
    """Forced into the population for this run.

    A locked project is still subject to the screens, so one that fails a screen
    never reaches the population and does not appear here.
    """


# --------------------------------------------------------------------------
# Aggregates and the run
# --------------------------------------------------------------------------


class PortfolioAggregates(BaseModel):
    """The twelve §7.1 headline tiles, with what each is compared against.

    Every field a mandate constrains is reported **with its breach** rather than
    hidden: a locked set that breaches a concentration cap still runs, and the
    breach surfaces on the tile (§13).
    """

    model_config = RESULT_CONFIG

    project_count: Count = Field(ge=0)
    solar_count: Count = Field(ge=0)
    wind_count: Count = Field(ge=0)
    capacity_mw: Magnitude
    solar_share: Fraction
    """Capacity-weighted, so offshore wind counts as wind (§5.1)."""
    total_capex_m: Magnitude = Field(alias="totalCapex_m")
    senior_debt_m: Magnitude = Field(alias="seniorDebt_m")
    equity_m: Magnitude = Field(alias="equity_m")
    gearing: Fraction
    capital_deployed: Magnitude
    """Not a fraction: a locked set may breach the cap, and §13 shows the breach."""
    equity_irr: Number | None
    """Solved **once**, on the winning chromosome, over the hold-truncated series.

    Distinct from the equity-weighted approximation the objective optimises
    (§10.3): the two differ, and this is the one the tile shows.
    """
    moic: Magnitude | None
    weighted_lcoe: Magnitude
    annual_generation_gwh: Magnitude
    co2_avoided_kt: Magnitude
    merchant_share: Fraction
    """Capex-weighted share of revenue not under contract."""
    weighted_risk_score: Magnitude
    worst_min_dscr: Magnitude | None
    country_shares: FrozenShares
    """``countryCode`` -> capex share, ordered by code."""
    largest_country_code: str | None = Field(default=None, pattern=fb.COUNTRY_CODE_PATTERN)
    largest_country_share: Fraction
    thirty_year_fcfe_m: Number = Field(alias="thirtyYearFcfe_m")
    fitness: Number
    """The §10.2 score of the winning chromosome, quantised to 6 dp."""


class ConvergencePoint(BaseModel):
    """One generation's best and mean fitness, for the §6 convergence chart."""

    model_config = RESULT_CONFIG

    generation: Count
    best_fitness: Number
    mean_fitness: Number


class RunProvenance(BaseModel):
    """Everything needed to reproduce a run exactly (§12).

    A run missing its resolved ``seed`` is not a valid run (epic §5), so the
    field is required rather than optional: a run that cannot explain its own
    numbers should not be storable.
    """

    model_config = RESULT_CONFIG

    seed: Count
    """The **resolved** seed, whether the caller supplied it or the server drew it."""
    pipeline_hash: str
    file_hashes: FrozenHashes
    """``id`` -> content hash, so an edit to any file is visible in the record."""
    assumption_set_id: str
    assumption_set_hash: str
    engine_version: str
    numpy_version: str
    blas_threads: Count
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
    created_at: AwareDatetime
    duration_ms: Count | None = Field(default=None, ge=0)
    mandate: Mandate
    locked_ids: IdList
    excluded_ids: IdList
    effort: Effort
    selected_ids: IdList
    aggregates: PortfolioAggregates | None
    """Absent while the run is still in flight (``docs/api.md`` §8)."""
    holdings: tuple[Holding, ...]
    cashflow_30y_m: tuple[Number, ...] = Field(alias="cashflow30Y_m")
    """The 30-year portfolio FCFE, **excluding** any terminal value.

    Kept rigorously apart from :attr:`cashflow_hold_m`. This is the series the
    chart and the CSV show and the one ``thirtyYearFcfe_m`` sums; conflating the
    two is the single most likely silent bug in the feature, which is why they
    are named apart and why their lengths are checked separately.
    """
    cashflow_hold_m: tuple[Number, ...] = Field(alias="cashflowHold_m")
    """The hold-truncated portfolio FCFE, **including** the terminal value.

    The series the portfolio IRR and MOIC are solved over, so it runs the
    mandate's hold period rather than the file's thirty years.
    """
    convergence: tuple[ConvergencePoint, ...]
    provenance: RunProvenance

    @property
    def is_complete(self) -> bool:
        """Whether the run has produced a result (``docs/api.md`` §8)."""
        return self.status is RunStatus.SUCCEEDED

    @model_validator(mode="after")
    def _status_and_aggregates_agree(self) -> RunRecord:
        """Only the two documented shapes are representable.

        §8 returns a run in flight with ``status: running`` and no aggregates,
        and a finished one with both. A row carrying ``succeeded`` and null
        aggregates is a partial write, and reading ``is_complete`` off the
        aggregates rather than the status would let it skip every check below.
        """
        if self.is_complete and self.aggregates is None:
            raise ValueError(
                f"status is {self.status.value} but aggregates are absent; a "
                f"finished run carries its twelve headline metrics"
            )
        if not self.is_complete and self.aggregates is not None:
            raise ValueError(
                f"status is {self.status.value} but aggregates are present; only "
                f"a succeeded run has a result"
            )
        return self

    @model_validator(mode="after")
    def _cash_flow_series_have_their_documented_lengths(self) -> RunRecord:
        """The chart needs thirty years; the IRR needs the hold period.

        Only checked once the run has produced a result: ``docs/api.md`` §8
        returns a run still in flight with no aggregates, and it has no cash
        flows to be the wrong length yet.
        """
        if not self.is_complete:
            return self
        if len(self.cashflow_30y_m) != YEARS:
            raise ValueError(
                f"cashflow30Y_m must carry {YEARS} years for the chart and the CSV; "
                f"got {len(self.cashflow_30y_m)}"
            )
        expected = min(self.mandate.hold_years, YEARS)
        if len(self.cashflow_hold_m) != expected:
            raise ValueError(
                f"cashflowHold_m must run the mandate's hold period of {expected} "
                f"years; got {len(self.cashflow_hold_m)}"
            )
        return self

    @model_validator(mode="after")
    def _holdings_are_uniquely_identified_and_ordered(self) -> RunRecord:
        """Ordered by id, and each id appearing once.

        Sorting alone does not catch a repeat — ``sorted`` leaves duplicates
        adjacent and in order — and a duplicated candidate double-counts in the
        holdings table and the CSV, plots twice on the map, and makes the row
        count disagree with ``projectCount``.
        """
        ids = tuple(holding.id for holding in self.holdings)
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        if duplicates:
            raise ValueError(f"holdings carry duplicate ids: {', '.join(duplicates)}")
        if ids != canonical_order(ids):
            raise ValueError("holdings must be ordered by id ascending")
        return self

    @model_validator(mode="after")
    def _selected_flags_agree_with_selected_ids(self) -> RunRecord:
        """``selected`` means "in ``selectedIds``" (§8.2), so the two cannot drift."""
        flagged = tuple(holding.id for holding in self.holdings if holding.selected)
        if flagged != self.selected_ids:
            raise ValueError(
                "holdings flagged selected do not match selectedIds: "
                f"{sorted(set(flagged) ^ set(self.selected_ids))}"
            )
        return self
