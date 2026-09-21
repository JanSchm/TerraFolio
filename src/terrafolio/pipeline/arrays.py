"""``ProjectArrays`` — the pipeline as a struct of arrays, for the numeric core.

**Units.** Money is **euros**, energy **GWh**, prices **€/MWh**, capacity **MW**, all
``float64``. Files are in €m (``docs/pipeline-schema.md`` §2); the conversion happens
once, in :mod:`terrafolio.pipeline.loader`, and once more on the way out at the HTTP
boundary. There is no third conversion site.

**Ordering.** Rows are in canonical order — ``id`` ascending, per
:func:`terrafolio.domain.conventions.canonical_order`. The genetic algorithm's PRNG
draws are indexed by position, so this is a determinism requirement rather than
tidiness (epic §5, decision C-5).

**Shape.** The blocks mirror ``docs/pipeline-schema.md`` §4 and §5 one for one, so a
field is where the schema says it is. Columns that are one arithmetic step from a
stored one — equity, gearing, capex per kW, capture price, the technology masks — are
**properties**, not fields. §9 rejects them from files so there is a single source for
each; computing them here keeps it that way. They allocate on access and are consumed
once per run, not per generation, so callers bind them to a local.

**No pydantic.** ``economics`` and ``optimiser`` import this module, and the
import-boundary guard makes the numeric core's *transitive* third-party closure
``{numpy}``. Anything this module touches must therefore be numpy, the standard
library, or one of the pydantic-free ``domain`` leaves. The loader holds the
``ProjectFile`` models and hands the arrays over already converted.

Descriptive fields — name, country, ISO3, provenance — are deliberately absent. The
core never needs them, and carrying them would make this a second spelling of the file
schema. The loader keeps the validated ``ProjectFile`` objects in the same canonical
order, so a presentation layer joins by position.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

from terrafolio.domain.enums import Stage, Technology

__all__ = [
    "KW_PER_MW",
    "AssetArrays",
    "BalanceSheetArrays",
    "CapitalStructureArrays",
    "CashFlowArrays",
    "DeclaredAssumptionArrays",
    "ExecutionArrays",
    "IncomeStatementArrays",
    "LocationArrays",
    "PhysicalsArrays",
    "ProjectArrays",
    "RatiosArrays",
    "RevenueArrays",
    "StatementArrays",
]

KW_PER_MW: Final = 1000  # structural: unit definition, not a calibration
"""Definitional, not configuration: a kilowatt is not a tunable.

``domain.conventions`` publishes the two conversions it owns — €m to euros, GWh to MWh
— but not this one, and capex per kW needs it in both the plausibility band
(``docs/pipeline-schema.md`` §10) and the reported scalar.
"""

Vector = NDArray[np.float64]
"""A ``(n,)`` per-project scalar column."""

Matrix = NDArray[np.float64]
"""A ``(n, 30)`` statement series: one row per project, one column per year."""

IntVector = NDArray[np.int64]
IntMatrix = NDArray[np.int64]
BoolVector = NDArray[np.bool_]


# ---------------------------------------------------------------------------
# §4 — the per-project scalars
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class LocationArrays:
    """``location`` — siting, for the map and the country concentration cap.

    ``country_codes`` are alpha-2 and already normalised: a file written ``UK`` is
    carried as ``GB``, which is the code the market tables and the mandate chips use.
    """

    latitude: Vector
    longitude: Vector
    country_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class AssetArrays:
    """``asset`` — what is being built, and how much of it."""

    technologies: tuple[Technology, ...]
    stages: tuple[Stage, ...]
    capacity_mw: Vector
    cod_year: IntVector
    net_capacity_factor: Vector
    opex_per_kw_year: Vector


@dataclass(frozen=True, slots=True, kw_only=True)
class RevenueArrays:
    """``revenue`` — the contracted and merchant price terms.

    ``capture_factor`` is the file's own, and it is **effective**: 1C's corpus carries
    ``capturePrice / countryBaseloadPrice`` so that §4.3's identity holds exactly
    against a capture price the reference rounded to a whole €/MWh (decision 1C-17).
    Re-rounding it here would put revenue 1.13% out.
    """

    ppa_share: Vector
    ppa_price: Vector
    ppa_tenor_years: IntVector
    country_baseload_price: Vector
    capture_factor: Vector

    @property
    def capture_price(self) -> Vector:
        """€/MWh achieved on merchant volume, before escalation."""
        return self.country_baseload_price * self.capture_factor


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionArrays:
    """``execution`` — the risk and execution screens of §5.3.

    ``currencies`` is the currency the project's **revenue** is earned in, not the
    denomination of its statements, which are always euros (decision A-12). It drives
    the EUR-only screen and the detail sheet's hedging flag; it is never a unit.
    """

    development_risk_score: Vector
    grid_secured: BoolVector
    om_contracted: BoolVector
    currencies: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class CapitalStructureArrays:
    """``capitalStructure``, in **euros**.

    Neither equity nor gearing is stored: §9 rejects both from a file because each is
    one step from what is here, and the stored copy is always the one that drifts.
    """

    total_capex: Vector
    senior_debt: Vector
    max_gearing: Vector

    @property
    def equity(self) -> Vector:
        """Total project cost less senior debt, in euros."""
        return self.total_capex - self.senior_debt

    @property
    def gearing(self) -> Vector:
        """Senior debt over total project cost, a fraction of one."""
        return self.senior_debt / self.total_capex


@dataclass(frozen=True, slots=True, kw_only=True)
class DeclaredAssumptionArrays:
    """``assumptions`` — what each analyst declared, which is what disperses.

    Every field here is per-file and may legitimately differ between files; the
    cross-file dispersion report (§11) is what makes that visible. ``base_year`` is the
    exception and lives on :class:`ProjectArrays`, because portfolio series are summed
    by position and a varying base year is a broken index, not a disagreement about
    modelling (decision A-13).
    """

    tax_rate: Vector
    depreciation_years: IntVector
    debt_rate: Vector
    debt_tenor_years: IntVector
    degradation_rate: Vector
    price_escalation: Vector
    merchant_escalation: Vector
    opex_escalation: Vector
    target_dscr: Vector


# ---------------------------------------------------------------------------
# §5 — the statement series
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class PhysicalsArrays:
    """``statements.physicals`` — GWh and €/MWh, never money."""

    generation_gwh: Matrix
    achieved_price: Matrix


@dataclass(frozen=True, slots=True, kw_only=True)
class IncomeStatementArrays:
    """``statements.incomeStatement``, in euros."""

    revenue: Matrix
    opex: Matrix
    ebitda: Matrix
    depreciation: Matrix
    ebit: Matrix
    interest_expense: Matrix
    pbt: Matrix
    tax_expense: Matrix
    net_income: Matrix


@dataclass(frozen=True, slots=True, kw_only=True)
class CashFlowArrays:
    """``statements.cashFlow``, in euros.

    ``fcfe`` is the **30-year** series and carries **no** terminal value. The
    hold-truncated series that does is built per mandate in
    :mod:`terrafolio.economics.returns`, and the two never share a name (decision A-6).
    """

    interest_paid: Matrix
    debt_repayment: Matrix
    tax_paid: Matrix
    capex: Matrix
    debt_drawdown: Matrix
    equity_drawdown: Matrix
    fcfe: Matrix


@dataclass(frozen=True, slots=True, kw_only=True)
class DebtScheduleArrays:
    """``statements.debtSchedule``, in euros. ``opening`` excludes the year's drawdown."""

    opening: Matrix
    drawdown: Matrix
    repayment: Matrix
    closing: Matrix


@dataclass(frozen=True, slots=True, kw_only=True)
class BalanceSheetArrays:
    """``statements.balanceSheet``, in euros."""

    ppe: Matrix


@dataclass(frozen=True, slots=True, kw_only=True)
class RatiosArrays:
    """``statements.ratios``.

    ``dscr`` is the only nullable series a file may carry. A null becomes ``NaN``
    here — never ``0.0``, which would read as a total failure to cover rather than as
    "there is no debt service in this year".
    """

    dscr: Matrix


@dataclass(frozen=True, slots=True, kw_only=True)
class StatementArrays:
    """The six statement blocks, mirroring ``docs/pipeline-schema.md`` §5."""

    years: IntMatrix
    """``(n, 30)`` calendar years, identical across rows. The loader fails a pipeline
    whose files disagree on ``baseYear``, because portfolio series are summed by
    position (§7.9, decision A-13)."""

    physicals: PhysicalsArrays
    income: IncomeStatementArrays
    cash_flow: CashFlowArrays
    debt: DebtScheduleArrays
    balance: BalanceSheetArrays
    ratios: RatiosArrays


# ---------------------------------------------------------------------------
# The whole pipeline
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectArrays:
    """Every loaded project, column-wise, in canonical ``id`` order."""

    ids: tuple[str, ...]

    base_year: int
    """Year zero for every series, shared by every file in the pipeline."""

    location: LocationArrays
    asset: AssetArrays
    revenue: RevenueArrays
    execution: ExecutionArrays
    capital: CapitalStructureArrays
    assumptions: DeclaredAssumptionArrays
    statements: StatementArrays

    @property
    def count(self) -> int:
        """Number of projects loaded."""
        return len(self.ids)

    @property
    def is_solar(self) -> BoolVector:
        """Solar PV. The objective's technology-split term is capacity-weighted on this."""
        return np.array(
            [tech is Technology.SOLAR for tech in self.asset.technologies], dtype=np.bool_
        )

    @property
    def is_wind(self) -> BoolVector:
        """Onshore and offshore both: §5.1 counts offshore wind as wind."""
        return ~self.is_solar

    @property
    def capex_per_kw(self) -> Vector:
        """Total project cost per installed kilowatt, for §10's technology band."""
        return self.capital.total_capex / (self.asset.capacity_mw * KW_PER_MW)

    def index_map(self) -> dict[str, int]:
        """``id`` to row position, for resolving locked and excluded ids.

        A method rather than a property because it allocates a dictionary; callers in
        the optimiser bind it once, outside their loops.
        """
        return {project_id: position for position, project_id in enumerate(self.ids)}
