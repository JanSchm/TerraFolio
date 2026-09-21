"""The project statement file, as pydantic models.

This is the executable definition of the schema documented normatively in
``docs/pipeline-schema.md``. Field names, units, domains and the reject-derived
rule all come from there; where this module and that document disagree, one of
them is a defect and they are reconciled rather than forked.

Files are **UTF-8 JSON in camelCase**, money in **€m**, energy in **GWh**. The
conversion to euros happens once, in the loader (issue 2A); the conversion back
to €m happens once, at the HTTP boundary (issue 3A). Nowhere else.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, model_validator
from pydantic.alias_generators import to_camel

from terrafolio.domain import file_bounds as fb
from terrafolio.domain.conventions import YEARS
from terrafolio.domain.enums import (
    Confidence,
    Currency,
    EstimateBasis,
    ProvenanceGroup,
    Stage,
    Technology,
)
from terrafolio.domain.fields import (
    FREEZE_MAPPING,
    BalanceSeries30,
    Count,
    Flag,
    MagnitudeSeries30,
    Number,
    Series30,
    Series30Opt,
    Years30,
)

__all__ = [
    "Asset",
    "BalanceSheet",
    "CapitalStructure",
    "CashFlow",
    "DebtSchedule",
    "DeclaredAssumptions",
    "DerivedFieldError",
    "Execution",
    "IncomeStatement",
    "Location",
    "Physicals",
    "ProjectFile",
    "Provenance",
    "ProvenanceEntry",
    "Ratios",
    "RevenueTerms",
    "Statements",
]

FILE_CONFIG: Final = ConfigDict(
    frozen=True,
    # A misspelled field that is silently ignored is how a wrong number reaches
    # an investment committee (docs/pipeline-schema.md §3).
    extra="forbid",
    alias_generator=to_camel,
    validate_by_alias=True,
    # camelCase is the file format, full stop. Accepting snake_case as well
    # would mean two spellings of one schema, and the .xlsx path would then have
    # a third.
    validate_by_name=False,
    serialize_by_alias=True,
    # Defaults to True in pydantic, which would let a file carry NaN revenue and
    # destroy the one invariant that makes NaN meaningful: an undefined IRR is
    # the only NaN in the system (epic §5).
    allow_inf_nan=False,
)


# --------------------------------------------------------------------------
# The reject-derived-fields rule (docs/pipeline-schema.md §9)
# --------------------------------------------------------------------------
#
# These names live here and not in the assumption set, for two reasons. The
# dependency direction is config -> domain, so domain cannot read config without
# a cycle. And they are part of the schema contract, not calibration: moving
# them into the assumption set would make an editorial schema change alter
# assumption_set_id and invalidate the comparability of every stored run.
#
# This rule applies to ProjectFile alone. Holding and RunRecord must carry IRR,
# MOIC and payback -- they are results, computed under a mandate.

_MANDATE_DEPENDENT: Final[frozenset[str]] = frozenset(
    {
        "irr",
        "equityirr",
        "projectirr",
        "moic",
        "terminalvalue",
        "exitvalue",
        "exitproceeds",
        "payback",
        "paybackyear",
        "paybackperiod",
        "mindscr",
    }
)

_REDUNDANT: Final[frozenset[str]] = frozenset(
    {"lcoe", "leverage", "gearing", "equity", "capexperkw", "cashflowschedule"}
)

_CAPEX_HOME: Final[tuple[str, ...]] = ("statements", "cashFlow")
"""The one path where a key named ``capex`` is legitimate.

``capex`` is the annual cash-flow line; the project scalar is ``totalCapex``
(A-2). A top-level or capital-structure ``capex`` is the naming collision the
decision resolved, so it is rejected by name rather than by the generic
unknown-key rule.
"""

_KEYED_MAPS: Final[frozenset[tuple[str, ...]]] = frozenset({("provenance", "fields")})
"""Paths whose immediate keys are vocabulary, not field names.

``provenance.fields`` is keyed by provenance group, and one of those groups is
called ``capex`` (§8). Without this the rule would reject the template's own
example for naming a group after the thing it describes.
"""


class DerivedFieldError(ValueError):
    """Raised when a file supplies a value that is computed, not declared."""


def _banned_reason(key: str, parent: tuple[str, ...]) -> str | None:
    folded = key.casefold()
    if folded == "capex":
        if parent == _CAPEX_HOME:
            return None
        return (
            "`capex` is the annual cash-flow line inside statements.cashFlow; the "
            "project scalar is `totalCapex`"
        )
    if folded in _MANDATE_DEPENDENT:
        return (
            "depends on the mandate's hold period and the assumption set's exit "
            "multiples, so it is computed per run and recorded on the run, never "
            "stored in a file"
        )
    if folded in _REDUNDANT:
        return (
            "is one arithmetic step from fields already present; two sources for "
            "one number is one too many, and the stored one is the one that drifts"
        )
    return None


def _collect_banned(node: Any, path: tuple[str, ...], found: list[str]) -> None:
    if isinstance(node, Mapping):
        keys_are_vocabulary = path in _KEYED_MAPS
        for key, value in node.items():
            if not isinstance(key, str):
                continue
            reason = None if keys_are_vocabulary else _banned_reason(key, path)
            if reason is not None:
                pointer = "/".join((*path, key))
                found.append(f"  {pointer} — {reason}")
            _collect_banned(value, (*path, key), found)
    elif isinstance(node, Sequence) and not isinstance(node, str | bytes):
        for index, item in enumerate(node):
            _collect_banned(item, (*path, str(index)), found)


# --------------------------------------------------------------------------
# Blocks
# --------------------------------------------------------------------------


class Location(BaseModel):
    """Where the park is (§4.1)."""

    model_config = FILE_CONFIG

    country: str = Field(min_length=1, max_length=fb.COUNTRY_NAME_MAX_LEN)
    country_code: str = Field(pattern=fb.COUNTRY_CODE_PATTERN)
    iso3: str = Field(pattern=fb.ISO3_PATTERN)
    lat: Number = Field(ge=-fb.LATITUDE_ABS_MAX, le=fb.LATITUDE_ABS_MAX)
    lon: Number = Field(ge=-fb.LONGITUDE_ABS_MAX, le=fb.LONGITUDE_ABS_MAX)


class Asset(BaseModel):
    """What the park is, physically (§4.2)."""

    model_config = FILE_CONFIG

    technology: Technology
    stage: Stage
    capacity_mw: Number = Field(gt=0, le=fb.CAPACITY_MW_MAX)
    cod_year: Count
    net_capacity_factor: Number = Field(gt=0, le=fb.NET_CAPACITY_FACTOR_MAX)
    opex_per_kw_year: Number = Field(gt=0, le=fb.OPEX_PER_KW_YEAR_MAX)


class RevenueTerms(BaseModel):
    """How the park is paid (§4.3)."""

    model_config = FILE_CONFIG

    ppa_share: Number = Field(ge=0, le=fb.SHARE_MAX)
    ppa_price: Number = Field(ge=0, le=fb.PRICE_EUR_PER_MWH_MAX)
    ppa_tenor_years: Count = Field(ge=0, le=fb.PPA_TENOR_YEARS_MAX)
    country_baseload_price: Number = Field(gt=0, le=fb.PRICE_EUR_PER_MWH_MAX)
    capture_factor: Number = Field(gt=0, le=fb.CAPTURE_FACTOR_MAX)

    @model_validator(mode="after")
    def _tenor_agrees_with_share(self) -> RevenueTerms:
        """A zero tenor means fully merchant, so it cannot carry a contracted share (§4.3)."""
        if self.ppa_tenor_years == 0 and self.ppa_share != 0:
            raise ValueError(
                f"ppaTenorYears is 0, which means fully merchant, but ppaShare is "
                f"{self.ppa_share}; a contracted share needs a tenor to run over"
            )
        return self


class Execution(BaseModel):
    """Execution risk and the screens that read it (§4.4)."""

    model_config = FILE_CONFIG

    development_risk_score: Number = Field(
        ge=fb.DEVELOPMENT_RISK_SCORE_MIN, le=fb.DEVELOPMENT_RISK_SCORE_MAX
    )
    grid_secured: Flag
    om_contracted: Flag
    currency: Currency

    @model_validator(mode="after")
    def _score_is_one_decimal(self) -> Execution:
        score = self.development_risk_score
        if round(score, fb.DEVELOPMENT_RISK_SCORE_DECIMALS) != score:
            raise ValueError(f"developmentRiskScore is stated to one decimal; got {score!r}")
        return self


class CapitalStructure(BaseModel):
    """What the analyst assumed the park is financed with (§4.5).

    Not what asset quality supports: with statements ingested, gearing describes
    a declared capital structure rather than a derived one (A-8). Project equity
    and gearing are deliberately absent — both are one subtraction or one
    division away, and storing them invites drift (§9).
    """

    model_config = FILE_CONFIG

    total_capex: Number = Field(gt=0)
    senior_debt: Number = Field(ge=0)
    max_gearing: Number = Field(ge=0, le=fb.GEARING_MAX)

    @model_validator(mode="after")
    def _debt_within_cost(self) -> CapitalStructure:
        if self.senior_debt > self.total_capex:
            raise ValueError(
                f"seniorDebt ({self.senior_debt}) exceeds totalCapex "
                f"({self.total_capex}); a project cannot be more than fully debt-funded"
            )
        return self


class DeclaredAssumptions(BaseModel):
    """The analyst's own assumptions (§4.6).

    Inputs, not configuration: the investment team does not turn these, and the
    cross-file dispersion report surfaces disagreement between files.

    The last five are 1A additions (decisions C-2, C-3, C-4). The three
    escalators are required because epic §2 and issue 2A both specify a
    dispersion report covering them, and nothing else in the file lets a
    consumer recover them; ``degradationRate`` and ``targetDscr`` are what 2C's
    variance report needs to re-derive generation and the debt sizing. None of
    them participates in a tie-out.
    """

    model_config = FILE_CONFIG

    base_year: Count = Field(ge=fb.BASE_YEAR_MIN, le=fb.BASE_YEAR_MAX)
    tax_rate: Number = Field(ge=0, le=fb.TAX_RATE_MAX)
    depreciation_years: Count = Field(ge=fb.DEPRECIATION_YEARS_MIN, le=fb.DEPRECIATION_YEARS_MAX)
    debt_rate: Number = Field(ge=0, le=fb.DEBT_RATE_MAX)
    debt_tenor_years: Count = Field(ge=0, le=fb.DEBT_TENOR_YEARS_MAX)
    degradation_rate: Number = Field(ge=0, le=fb.DEGRADATION_RATE_MAX)
    price_escalation: Number = Field(ge=fb.ESCALATION_MIN, le=fb.ESCALATION_MAX)
    merchant_escalation: Number = Field(ge=fb.ESCALATION_MIN, le=fb.ESCALATION_MAX)
    opex_escalation: Number = Field(ge=fb.ESCALATION_MIN, le=fb.ESCALATION_MAX)
    target_dscr: Number = Field(ge=fb.TARGET_DSCR_MIN, le=fb.TARGET_DSCR_MAX)


# --------------------------------------------------------------------------
# Statements (§5)
# --------------------------------------------------------------------------


class Physicals(BaseModel):
    """Volume and price, the two drivers revenue must tie back to (§5.2)."""

    model_config = FILE_CONFIG

    generation_gwh: MagnitudeSeries30
    achieved_price: MagnitudeSeries30


class IncomeStatement(BaseModel):
    """Accrual basis (§5.3). Every line but ``pbt`` and ``netIncome`` is positive."""

    model_config = FILE_CONFIG

    revenue: Series30
    opex: MagnitudeSeries30
    ebitda: Series30
    depreciation: MagnitudeSeries30
    ebit: Series30
    interest_expense: MagnitudeSeries30
    pbt: Series30
    tax_expense: MagnitudeSeries30
    net_income: Series30


class CashFlow(BaseModel):
    """Cash basis (§5.4). ``fcfe`` is signed; every other line is a magnitude."""

    model_config = FILE_CONFIG

    interest_paid: MagnitudeSeries30
    debt_repayment: MagnitudeSeries30
    tax_paid: MagnitudeSeries30
    capex: MagnitudeSeries30
    debt_drawdown: MagnitudeSeries30
    equity_drawdown: MagnitudeSeries30
    fcfe: Series30


class DebtSchedule(BaseModel):
    """Senior debt roll-forward (§5.5). ``opening`` excludes that year's drawdown."""

    model_config = FILE_CONFIG

    opening: BalanceSeries30
    drawdown: MagnitudeSeries30
    repayment: MagnitudeSeries30
    closing: BalanceSeries30


class BalanceSheet(BaseModel):
    """Closing property, plant and equipment (§5.6).

    v1 validates the PP&E roll-forward only; the senior debt balance lives in
    ``debtSchedule`` and a fuller balance sheet is deferred.
    """

    model_config = FILE_CONFIG

    ppe: BalanceSeries30


class Ratios(BaseModel):
    """Cover ratios (§5.7).

    ``dscr`` is the only place a null is permitted in a file: it is non-null
    exactly within the debt life, including the ramp year, whose figure is
    typically well below 1.0. Excluding the ramp year from the *minimum* is the
    consumer's job, not the file's.
    """

    model_config = FILE_CONFIG

    dscr: Series30Opt


class Statements(BaseModel):
    """Thirty contiguous years of financials, indexed from ``assumptions.baseYear``."""

    model_config = FILE_CONFIG

    years: Years30
    physicals: Physicals
    income_statement: IncomeStatement
    cash_flow: CashFlow
    debt_schedule: DebtSchedule
    balance_sheet: BalanceSheet
    ratios: Ratios


# --------------------------------------------------------------------------
# Provenance (§8)
# --------------------------------------------------------------------------


class ProvenanceEntry(BaseModel):
    """How firm one group of declared numbers is."""

    model_config = FILE_CONFIG

    estimate_basis: EstimateBasis
    confidence: Confidence
    note: str | None = Field(default=None, max_length=fb.NOTE_MAX_LEN)


ProvenanceFields = Annotated[
    Mapping[ProvenanceGroup, ProvenanceEntry],
    FREEZE_MAPPING,
    PlainSerializer(dict, return_type=dict[ProvenanceGroup, ProvenanceEntry]),
]
"""The seven provenance groups, read-only once validated.

A plain ``dict`` here would still accept ``file.provenance.fields.pop(...)`` on
a model whose whole point is that it is frozen — and the snapshot hash is taken
over the file, so a mutated file is one the hash no longer describes.
"""


class Provenance(BaseModel):
    """Who produced the file, when, and how much of it is prediction (§8).

    A weak provenance block is reported, never rejected: gating on it would make
    the house model authoritative again and defeat the input design (§8.4).
    """

    model_config = FILE_CONFIG

    prepared_by: str = Field(min_length=1, max_length=fb.TEXT_MAX_LEN)
    prepared_on: dt.date
    model_version: str = Field(min_length=1, max_length=fb.TEXT_MAX_LEN)
    fields: ProvenanceFields

    @model_validator(mode="after")
    def _covers_every_group(self) -> Provenance:
        missing = [g.value for g in ProvenanceGroup if g not in self.fields]
        if missing:
            raise ValueError(
                "provenance.fields must cover every group; missing " + ", ".join(sorted(missing))
            )
        return self


# --------------------------------------------------------------------------
# The file
# --------------------------------------------------------------------------


class ProjectFile(BaseModel):
    """One park, as its analyst models it.

    Frozen and hashable. Nothing mandate-dependent is stored: see
    :data:`_MANDATE_DEPENDENT` and ``docs/pipeline-schema.md`` §2.1.
    """

    model_config = FILE_CONFIG

    schema_version: str
    id: str = Field(pattern=fb.ID_PATTERN)
    name: str = Field(min_length=1, max_length=fb.NAME_MAX_LEN)
    location: Location
    asset: Asset
    revenue: RevenueTerms
    execution: Execution
    capital_structure: CapitalStructure
    assumptions: DeclaredAssumptions
    statements: Statements
    provenance: Provenance

    @model_validator(mode="before")
    @classmethod
    def _reject_derived_fields(cls, data: Any) -> Any:
        """Reject computed values before any field validation runs.

        A root before-validator sees the raw mapping, so this message beats
        ``extra="forbid"``'s generic one at every depth. Every offender is
        collected and reported at once: a file with three derived blocks should
        take one edit to fix, not three.
        """
        if not isinstance(data, Mapping):
            return data
        found: list[str] = []
        _collect_banned(data, (), found)
        if found:
            raise DerivedFieldError(
                "a project file carries what an analyst declares, not what a run "
                "computes; these keys are results:\n"
                + "\n".join(sorted(found))
                + "\n(see docs/pipeline-schema.md §9)"
            )
        return data

    @model_validator(mode="after")
    def _schema_version_matches(self) -> ProjectFile:
        if self.schema_version != fb.SCHEMA_VERSION:
            raise ValueError(
                f"schemaVersion is {self.schema_version!r}; this build reads {fb.SCHEMA_VERSION!r}"
            )
        return self

    @model_validator(mode="after")
    def _years_anchor_to_base_year(self) -> ProjectFile:
        """``years`` must run ``baseYear … baseYear + 29``, contiguous and ascending (§7.8).

        Redundant against ``baseYear`` by construction, and present on purpose: it
        makes a mis-indexed file fail loudly instead of quietly.
        """
        base = self.assumptions.base_year
        expected = tuple(range(base, base + YEARS))
        if self.statements.years != expected:
            raise ValueError(
                f"statements.years must run {base}…{base + YEARS - 1} contiguously "
                f"from assumptions.baseYear; got {self.statements.years[0]}…"
                f"{self.statements.years[-1]}"
            )
        return self

    @model_validator(mode="after")
    def _cod_within_window(self) -> ProjectFile:
        """COD must fall inside the window the arrays can describe (§4.2)."""
        base = self.assumptions.base_year
        low = base - fb.COD_YEARS_BEFORE_BASE
        high = base + YEARS - 1
        if not low <= self.asset.cod_year <= high:
            raise ValueError(
                f"asset.codYear {self.asset.cod_year} is outside {low}…{high}, the "
                f"window a 30-year file from base year {base} can describe"
            )
        return self
