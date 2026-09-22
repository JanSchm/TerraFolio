"""The assumption set: every rate, weight, floor, clamp, tolerance and band.

Frozen dataclasses, not pydantic models — deliberately. ``optimiser`` imports
``config`` and ``config`` imports ``domain.enums``, so anything pydantic on this
path would put pydantic inside the numeric core through an edge the dependency
rules allow, and the import-boundary test would pass while the boundary was a
fiction. Issue 1A's "one frozen model" is satisfied by a frozen dataclass tree
and a hand-written loader; see ``docs/decisions.md``.

Standard library and ``domain.enums`` only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from terrafolio.domain.enums import Effort, RiskAppetite, Stage, Technology

__all__ = [
    "AssumptionSet",
    "Band",
    "EffortParams",
    "FeasibilityThresholds",
    "GaParams",
    "GeneratorParams",
    "Interpretation",
    "IrrBracket",
    "Metadata",
    "ObjectiveWeights",
    "Range",
    "RiskCaps",
    "ValidationParams",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class Band:
    """An inclusive low/high pair, for a bound something is **tested** against."""

    low: float
    high: float


@dataclass(frozen=True, slots=True, kw_only=True)
class Range:
    """A range a value is **drawn** from, as ``low + u x span``.

    Distinct from :class:`Band`, and the difference is not stylistic. A draw
    range needs its width to be exactly the number the model multiplies by, and
    subtracting two endpoints does not always give it: ``1.06 - 0.94`` is
    ``0.1200000000000001``, not ``0.12``. That gap lands in
    ``netCapacityFactor``, which is emitted at full precision and multiplies
    every year of generation, so it is the difference between reproducing the
    reference corpus and merely agreeing with it.

    So a range states what it is drawn with and derives the endpoint, rather
    than the other way round.
    """

    low: float
    span: float

    @property
    def high(self) -> float:
        """The top of the range. Derived, because the span is what is drawn with."""
        return self.low + self.span


@dataclass(frozen=True, slots=True, kw_only=True)
class IrrBracket:
    """The bisection bracket IRR is searched in, and how finely.

    Its width decides which cash flows have an IRR at all: a series whose NPV
    does not change sign inside it is undefined, which §13 requires be rendered
    as an em dash and never coerced to zero.
    """

    low: float
    high: float
    iterations: int


@dataclass(frozen=True, slots=True, kw_only=True)
class Metadata:
    """Who wrote this calibration and what it replaces.

    Excluded from ``assumption_set_id``: fixing a typo in a label must not
    invalidate the comparability of every run stored against it.
    """

    label: str
    version: int
    created_by: str
    supersedes: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ObjectiveWeights:
    """The nine §10.2 terms, plus the two override scores.

    Floors and clamps apply to the **normalised** term, before the weight
    multiplies. Penalty weights are stated signed, so the objective adds
    ``weight * breach`` throughout rather than alternating ``+`` and ``-``.
    """

    capacity_weight: float
    capacity_score_floor: float
    tech_split_weight: float
    tech_split_score_floor: float
    tech_split_tolerance: float
    return_weight: float
    return_clamp: float
    return_scale: float
    utilisation_weight: float
    leverage_weight: float
    merchant_weight: float
    country_concentration_weight: float
    project_concentration_weight: float
    risk_weight: float
    empty_portfolio_score: float
    reject_base: float
    reject_slope: float
    equity_cap_tolerance_eur: float
    quantisation_dp: int


@dataclass(frozen=True, slots=True, kw_only=True)
class EffortParams:
    """Population and generations for one search effort (§10.1)."""

    population: int
    generations: int


@dataclass(frozen=True, slots=True, kw_only=True)
class GaParams:
    """Operator parameters, and epic §6.1's capital-aware initialisation bounds."""

    mutation_rate: float
    tournament_size: int
    elite_count: int
    inclusion_ceiling: float
    inclusion_floor: float
    effort: Mapping[Effort, EffortParams]


@dataclass(frozen=True, slots=True, kw_only=True)
class RiskCaps:
    """Two different caps by appetite (§5.3).

    ``project`` is a hard pre-screen on one project's development risk score;
    ``portfolio`` is a soft, capex-weighted penalty in the objective.
    """

    project: Mapping[RiskAppetite, float]
    portfolio: Mapping[RiskAppetite, float]


@dataclass(frozen=True, slots=True, kw_only=True)
class FeasibilityThresholds:
    """The two tolerances §5.4's advisory warnings trigger on."""

    solar_divergence_tolerance: float
    capital_absorption_floor: float


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidationParams:
    """Tie-out tolerances and plausibility bands (``docs/pipeline-schema.md`` §7, §10)."""

    tolerance_abs_m: float
    tolerance_rel: float
    capacity_factor: Band
    min_dscr_band: Band
    capex_per_kw: Mapping[Technology, Band]
    stage_gearing_ceiling: Mapping[Stage, float]


@dataclass(frozen=True, slots=True, kw_only=True)
class Interpretation:
    """The two modelling choices the specification leaves open.

    Flags rather than code so issue 2C's sweep can test each combination
    against the reference and pin the one that matches.
    """

    exit_year_fcfe_included: bool
    """Whether the exit year's own FCFE counts alongside the terminal value."""
    lcoe_opex_basis: str
    """One of :data:`terrafolio.model.returns.LCOE_BASES`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class GeneratorParams:
    """Everything the seed generator and the house model need (§9.1-9.4)."""

    base_year: int
    cod_first_year: int
    cod_last_year: int
    hours_per_year_gwh: float
    ramp_factor: float
    tax_rate: float
    depreciation_years: int
    debt_rate: float
    debt_tenor_years: int
    target_dscr: float
    price_escalation: float
    merchant_escalation: float
    opex_escalation: float
    entry_yield_jitter: Range
    contracted_share_floor: float
    capacity_factor_jitter: Range
    contract_price_factor: Range
    contract_tenor_choices: tuple[int, ...]

    degradation: Mapping[Technology, float]
    capture_factor: Mapping[Technology, float]
    entry_yield: Mapping[Stage, float]
    entry_yield_offshore_override: float
    opex_per_kw_year: Mapping[Technology, Range]
    offshore_capacity_factor: Range
    contracted_share: Mapping[Stage, Range]
    development_risk_base: Mapping[Stage, float]
    development_risk_offshore_premium: float
    development_risk_jitter: float
    development_risk: Band
    grid_secured_greenfield_probability: float
    om_contracted_probability: float

    dscr_resample_attempts: int

    technology_mix: Mapping[Technology, float]
    stage_mix: Mapping[Stage, float]
    capacity_mw: Mapping[Technology, Range]
    cod_offset: Mapping[Stage, Range]

    baseload_price: Mapping[str, float]
    capacity_factor: Mapping[Technology, Mapping[str, float]]


@dataclass(frozen=True, slots=True, kw_only=True)
class AssumptionSet:
    """One resolved calibration.

    :attr:`assumption_set_id` is the digest of the calibration **values**, so it
    moves when and only when a number moves. That is what lets a stored run say
    which numbers produced it, and what makes changing one an auditable event
    (§9.4, §10.2).
    """

    name: str
    """The file stem, e.g. ``default-2026``. A human handle, not an identity."""
    assumption_set_id: str
    """``sha256(canonical_json(values))`` truncated. The content identity."""
    content_hash: str
    """The full prefixed digest, for the run record."""

    meta: Metadata
    exit_multiples: Mapping[Technology, float]
    lcoe_real_discount_rate: float
    irr: IrrBracket
    co2_t_per_mwh: float
    objective: ObjectiveWeights
    ga: GaParams
    risk_caps: RiskCaps
    feasibility: FeasibilityThresholds
    validation: ValidationParams
    interpretation: Interpretation
    generator: GeneratorParams
