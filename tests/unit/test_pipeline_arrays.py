"""``ProjectArrays``: the units, the derived columns and the ordering contract.

Also exports :func:`sample_arrays`, a two-project set other 2A test modules build on.
There is no ``conftest.py`` in this repository and ``tests/`` is not a package, so
helpers travel by bare-module import, as ``test_domain_results`` already does with
``test_domain_mandate.VALID``.
"""

from __future__ import annotations

import numpy as np
import pytest

from terrafolio.domain.conventions import EUR_PER_EUR_MILLION, YEARS, canonical_order
from terrafolio.domain.enums import Stage, Technology
from terrafolio.pipeline.arrays import (
    KW_PER_MW,
    AssetArrays,
    BalanceSheetArrays,
    CapitalStructureArrays,
    CashFlowArrays,
    DebtScheduleArrays,
    DeclaredAssumptionArrays,
    ExecutionArrays,
    IncomeStatementArrays,
    LocationArrays,
    Matrix,
    PhysicalsArrays,
    ProjectArrays,
    RatiosArrays,
    RevenueArrays,
    StatementArrays,
)

BASE_YEAR = 2027


def _matrix(rows: int, fill: float = 0.0) -> Matrix:
    return np.full((rows, YEARS), fill, dtype=np.float64)


def _declining(rows: int, first: float, per_year: float) -> Matrix:
    """A series that actually varies by year.

    Flat fixtures hide real bugs: a terminal value read from the wrong exit year is
    indistinguishable from one read from the right year when every year is identical.
    """
    years = np.arange(YEARS, dtype=np.float64)
    return np.tile(np.maximum(first - per_year * years, 0.0), (rows, 1))


def sample_statements(rows: int) -> StatementArrays:
    """Statement blocks of the right shape, carrying values nothing here asserts on."""
    return StatementArrays(
        years=np.tile(np.arange(BASE_YEAR, BASE_YEAR + YEARS, dtype=np.int64), (rows, 1)),
        physicals=PhysicalsArrays(
            generation_gwh=_matrix(rows, 100.0),
            achieved_price=_matrix(rows, 50.0),
        ),
        income=IncomeStatementArrays(
            revenue=_matrix(rows, 5.0e6),
            opex=_matrix(rows, 1.0e6),
            ebitda=_declining(rows, 4.0e6, 2.0e4),
            depreciation=_matrix(rows, 1.0e6),
            ebit=_matrix(rows, 3.0e6),
            interest_expense=_matrix(rows, 5.0e5),
            pbt=_matrix(rows, 2.5e6),
            tax_expense=_matrix(rows, 5.0e5),
            net_income=_matrix(rows, 2.0e6),
        ),
        cash_flow=CashFlowArrays(
            interest_paid=_matrix(rows, 5.0e5),
            debt_repayment=_matrix(rows, 1.0e6),
            tax_paid=_matrix(rows, 5.0e5),
            capex=_matrix(rows),
            debt_drawdown=_matrix(rows),
            equity_drawdown=_matrix(rows),
            fcfe=_declining(rows, 2.0e6, 1.0e4),
        ),
        debt=DebtScheduleArrays(
            opening=_declining(rows, 1.0e7, 5.0e5),
            drawdown=_matrix(rows),
            repayment=_matrix(rows, 1.0e6),
            closing=_declining(rows, 9.5e6, 5.0e5),
        ),
        balance=BalanceSheetArrays(ppe=_matrix(rows, 1.0e8)),
        ratios=RatiosArrays(dscr=_matrix(rows, 1.4)),
    )


def sample_arrays() -> ProjectArrays:
    """Two projects — one solar, one onshore wind — in canonical order."""
    return ProjectArrays(
        ids=("P01", "P02"),
        base_year=BASE_YEAR,
        location=LocationArrays(
            latitude=np.array([39.25, 53.1], dtype=np.float64),
            longitude=np.array([-6.52, 8.4], dtype=np.float64),
            country_codes=("ES", "DE"),
        ),
        asset=AssetArrays(
            technologies=(Technology.SOLAR, Technology.ONSHORE_WIND),
            stages=(Stage.READY_TO_BUILD, Stage.GREENFIELD),
            capacity_mw=np.array([180.0, 90.0], dtype=np.float64),
            cod_year=np.array([2028, 2029], dtype=np.int64),
            net_capacity_factor=np.array([0.23, 0.28], dtype=np.float64),
            opex_per_kw_year=np.array([12.8, 46.0], dtype=np.float64),
        ),
        revenue=RevenueArrays(
            ppa_share=np.array([0.55, 0.30], dtype=np.float64),
            ppa_price=np.array([34.0, 61.0], dtype=np.float64),
            ppa_tenor_years=np.array([10, 15], dtype=np.int64),
            country_baseload_price=np.array([58.0, 73.0], dtype=np.float64),
            capture_factor=np.array([0.672, 0.88], dtype=np.float64),
        ),
        execution=ExecutionArrays(
            development_risk_score=np.array([2.7, 3.4], dtype=np.float64),
            grid_secured=np.array([True, False]),
            om_contracted=np.array([True, True]),
            currencies=("EUR", "EUR"),
        ),
        capital=CapitalStructureArrays(
            total_capex=np.array([103.32, 140.0], dtype=np.float64) * EUR_PER_EUR_MILLION,
            senior_debt=np.array([74.39, 91.0], dtype=np.float64) * EUR_PER_EUR_MILLION,
            max_gearing=np.array([0.72, 0.65], dtype=np.float64),
        ),
        assumptions=DeclaredAssumptionArrays(
            tax_rate=np.array([0.20, 0.30], dtype=np.float64),
            depreciation_years=np.array([25, 25], dtype=np.int64),
            debt_rate=np.array([0.055, 0.055], dtype=np.float64),
            debt_tenor_years=np.array([18, 18], dtype=np.int64),
            degradation_rate=np.array([0.005, 0.002], dtype=np.float64),
            price_escalation=np.array([0.005, 0.005], dtype=np.float64),
            merchant_escalation=np.array([0.021, 0.021], dtype=np.float64),
            opex_escalation=np.array([0.021, 0.021], dtype=np.float64),
            target_dscr=np.array([1.40, 1.40], dtype=np.float64),
        ),
        statements=sample_statements(2),
    )


def test_count_and_index_map() -> None:
    arrays = sample_arrays()
    assert arrays.count == 2
    assert arrays.index_map() == {"P01": 0, "P02": 1}


def test_ids_are_in_canonical_order() -> None:
    """The GA indexes its PRNG draws by position, so this is a determinism rule."""
    arrays = sample_arrays()
    assert arrays.ids == canonical_order(arrays.ids)


def test_equity_is_derived_not_declared() -> None:
    capital = sample_arrays().capital
    assert np.array_equal(capital.equity, capital.total_capex - capital.senior_debt)


def test_gearing_is_derived_from_the_two_stored_figures() -> None:
    capital = sample_arrays().capital
    assert np.array_equal(capital.gearing, capital.senior_debt / capital.total_capex)


def test_capex_per_kw_uses_euros_and_kilowatts() -> None:
    """€103.32m over 180 MW is about €574/kW — the unit §10's band is written in."""
    arrays = sample_arrays()
    assert arrays.capex_per_kw[0] == pytest.approx(103.32e6 / (180.0 * KW_PER_MW))
    assert arrays.capex_per_kw[0] == pytest.approx(574.0, abs=1.0)


def test_capture_price_uses_the_files_own_factor_unrounded() -> None:
    """Re-rounding to the nominal 0.68 would put revenue 1.13% out (1C-17)."""
    revenue = sample_arrays().revenue
    expected = revenue.country_baseload_price * revenue.capture_factor
    assert np.array_equal(revenue.capture_price, expected)


def test_technology_masks_partition_the_pipeline() -> None:
    """Offshore counts as wind (§5.1), so the two masks are exact complements."""
    arrays = sample_arrays()
    assert arrays.is_solar.tolist() == [True, False]
    assert np.array_equal(arrays.is_wind, ~arrays.is_solar)


def test_money_is_euros_not_millions() -> None:
    capital = sample_arrays().capital
    assert capital.total_capex[0] == pytest.approx(103.32 * EUR_PER_EUR_MILLION)
    assert capital.total_capex[0] > EUR_PER_EUR_MILLION


def test_every_statement_series_is_thirty_years_wide() -> None:
    statements = sample_arrays().statements
    matrices = [
        statements.years,
        statements.physicals.generation_gwh,
        statements.physicals.achieved_price,
        statements.income.ebitda,
        statements.cash_flow.fcfe,
        statements.debt.closing,
        statements.balance.ppe,
        statements.ratios.dscr,
    ]
    assert all(matrix.shape == (2, YEARS) for matrix in matrices)


def test_statement_matrices_are_float64() -> None:
    """float32 is confined to the GA's fitness hot path (epic §5)."""
    statements = sample_arrays().statements
    assert statements.income.ebitda.dtype == np.float64
    assert statements.cash_flow.fcfe.dtype == np.float64
    assert statements.years.dtype == np.int64


def test_arrays_are_frozen() -> None:
    arrays = sample_arrays()
    with pytest.raises(AttributeError):
        arrays.base_year = 2028  # type: ignore[misc]


def test_arrays_carry_no_descriptive_fields() -> None:
    """Names, countries and provenance stay on the files, joined by position.

    Carrying them here would make ``ProjectArrays`` a second spelling of the file
    schema, and the numeric core never reads one.
    """
    fields = set(ProjectArrays.__dataclass_fields__)
    assert not fields & {"names", "countries", "iso3", "provenance"}


def test_no_derived_column_is_also_a_stored_field() -> None:
    """§9's rule, applied to the arrays: one source per number."""
    derived = {"equity", "gearing", "capex_per_kw", "capture_price", "is_solar", "is_wind"}
    stored = (
        set(ProjectArrays.__dataclass_fields__)
        | set(CapitalStructureArrays.__dataclass_fields__)
        | set(RevenueArrays.__dataclass_fields__)
        | set(AssetArrays.__dataclass_fields__)
    )
    assert not derived & stored
