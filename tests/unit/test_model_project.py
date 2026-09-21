"""The house model against the 48 golden files, and against its own tie-outs.

Two kinds of check, and both are needed.

**Parity.** The model reproduces every statement series of every golden file
*bit for bit* -- 19 series x 30 years x 48 files, 27,360 doubles, `==` not
`approx`. A tolerance here would pass a model that had quietly stopped being the
reference's model, and the whole value of the corpus is that it is an oracle
rather than a smoke test. Reaching this took three fixes that a tolerance would
have hidden: the bracketing of the achieved-price terms, an unclamped debt
balance, and the PP&E roll-forward applied year by year.

**Tie-outs.** Parity says the model agrees with the reference. The tie-outs say
its output is internally coherent, which is what 2A's loader will demand of the
files the generator emits. They are asserted on the model's own arrays so that a
failure points at the model rather than at the serialiser.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import numpy as np
import pytest

from terrafolio.config.loader import load_default
from terrafolio.domain.conventions import EUR_PER_EUR_MILLION, MWH_PER_GWH, YEARS, ramp_index
from terrafolio.model.project import (
    DebtTerms,
    ProjectInputs,
    ProjectStatements,
    entry_capex_per_kw,
    min_dscr,
    project_statements,
    size_senior_debt,
    stabilised_ebitda,
)
from terrafolio.model.rounding import js_round

FIXTURES: Final = Path(__file__).resolve().parents[1] / "golden" / "fixtures" / "pipeline"

TOLERANCE_ABS_M: Final = 0.01
TOLERANCE_REL: Final = 0.001

SERIES: Final[tuple[tuple[str, str, str], ...]] = (
    ("generation_gwh", "physicals", "generationGwh"),
    ("achieved_price", "physicals", "achievedPrice"),
    ("revenue", "incomeStatement", "revenue"),
    ("opex", "incomeStatement", "opex"),
    ("ebitda", "incomeStatement", "ebitda"),
    ("depreciation", "incomeStatement", "depreciation"),
    ("ebit", "incomeStatement", "ebit"),
    ("interest_expense", "incomeStatement", "interestExpense"),
    ("pbt", "incomeStatement", "pbt"),
    ("tax_expense", "incomeStatement", "taxExpense"),
    ("net_income", "incomeStatement", "netIncome"),
    ("capex", "cashFlow", "capex"),
    ("debt_drawdown", "cashFlow", "debtDrawdown"),
    ("equity_drawdown", "cashFlow", "equityDrawdown"),
    ("fcfe", "cashFlow", "fcfe"),
    ("debt_opening", "debtSchedule", "opening"),
    ("debt_repayment", "debtSchedule", "repayment"),
    ("debt_closing", "debtSchedule", "closing"),
    ("ppe", "balanceSheet", "ppe"),
)


def golden_files() -> list[Path]:
    paths = sorted(FIXTURES.glob("*.json"))
    assert paths, "the golden corpus is missing"
    return paths


def inputs_from(file: dict[str, Any]) -> ProjectInputs:
    """Rebuild the model's inputs from a file's own declared fields.

    ``capture_price`` is recovered by rounding ``baseload x captureFactor`` back
    to the whole euro the generator produced. The factor a file carries is the
    *effective* one (1C-17), so the product is exactly the integer for 45 of the
    48 and lands on 63.00000000000001 for the three Italian projects, where
    92 x 0.68 rounds to 63.
    """
    asset, revenue = file["asset"], file["revenue"]
    capital, declared = file["capitalStructure"], file["assumptions"]
    generator = load_default().generator
    return ProjectInputs(
        capacity_mw=asset["capacityMw"],
        cod_year=asset["codYear"],
        base_year=declared["baseYear"],
        net_capacity_factor=asset["netCapacityFactor"],
        opex_per_kw_year=asset["opexPerKwYear"],
        ppa_share=revenue["ppaShare"],
        ppa_price=revenue["ppaPrice"],
        ppa_tenor_years=revenue["ppaTenorYears"],
        capture_price=js_round(revenue["countryBaseloadPrice"] * revenue["captureFactor"]),
        total_capex=capital["totalCapex"],
        senior_debt=capital["seniorDebt"],
        tax_rate=declared["taxRate"],
        depreciation_years=declared["depreciationYears"],
        debt_rate=declared["debtRate"],
        debt_tenor_years=declared["debtTenorYears"],
        degradation_rate=declared["degradationRate"],
        price_escalation=declared["priceEscalation"],
        merchant_escalation=declared["merchantEscalation"],
        opex_escalation=declared["opexEscalation"],
        ramp_factor=generator.ramp_factor,
        hours_per_year_gwh=generator.hours_per_year_gwh,
    )


@pytest.fixture(scope="module")
def modelled() -> list[tuple[Path, dict[str, Any], ProjectStatements]]:
    """Every golden file, alongside the model's own run of it."""
    built = []
    for path in golden_files():
        file = json.loads(path.read_text(encoding="utf-8"))
        built.append((path, file, project_statements(inputs_from(file))))
    return built


# --------------------------------------------------------------------------
# Parity
# --------------------------------------------------------------------------


def test_the_corpus_is_the_expected_size(
    modelled: list[tuple[Path, dict[str, Any], ProjectStatements]],
) -> None:
    """Guards the parity tests against silently checking nothing."""
    assert len(modelled) == 48


@pytest.mark.parametrize(("attribute", "block", "key"), SERIES)
def test_every_series_reproduces_the_corpus_bit_for_bit(
    attribute: str,
    block: str,
    key: str,
    modelled: list[tuple[Path, dict[str, Any], ProjectStatements]],
) -> None:
    for path, file, built in modelled:
        mine = getattr(built, attribute)
        reference = np.array(file["statements"][block][key], dtype=np.float64)
        assert np.array_equal(mine, reference), (
            f"{path.name} {block}.{key}: worst |diff| {float(np.max(np.abs(mine - reference))):.3e}"
        )


def test_dscr_reproduces_the_corpus_including_where_it_is_null(
    modelled: list[tuple[Path, dict[str, Any], ProjectStatements]],
) -> None:
    """Value *and* placement.

    §7.8 makes the placement a blocking check in its own right: ``dscr`` is
    non-null exactly inside the debt life, ramp year included.
    """
    for path, file, built in modelled:
        for index, reference in enumerate(file["statements"]["ratios"]["dscr"]):
            mine = built.dscr[index]
            if reference is None:
                assert np.isnan(mine), f"{path.name} year {index}: expected null"
            else:
                assert not np.isnan(mine), f"{path.name} year {index}: expected a value"
                assert float(mine) == reference, f"{path.name} year {index}"


# --------------------------------------------------------------------------
# Tie-outs, on the model's own output (docs/pipeline-schema.md §7)
# --------------------------------------------------------------------------


def within_tolerance(residual: float, reference: float) -> bool:
    """§7's tolerance: EUR 0.01m absolute or 0.1% relative, whichever is looser."""
    return abs(residual) <= max(TOLERANCE_ABS_M, TOLERANCE_REL * abs(reference))


def assert_ties(name: str, left: Any, right: Any, path: Path) -> None:
    residual = np.max(np.abs(np.asarray(left) - np.asarray(right)))
    scale = float(np.max(np.abs(np.asarray(right))))
    assert within_tolerance(float(residual), scale), f"{path.name} {name}: residual {residual:.3e}"


def test_the_income_statement_ties_out(
    modelled: list[tuple[Path, dict[str, Any], ProjectStatements]],
) -> None:
    for path, _, s in modelled:
        assert_ties("ebitda", s.ebitda, s.revenue - s.opex, path)
        assert_ties("ebit", s.ebit, s.ebitda - s.depreciation, path)
        assert_ties("pbt", s.pbt, s.ebit - s.interest_expense, path)
        assert_ties("netIncome", s.net_income, s.pbt - s.tax_expense, path)


def test_revenue_ties_to_the_physicals(
    modelled: list[tuple[Path, dict[str, Any], ProjectStatements]],
) -> None:
    """§7.2, with the division by 1e6 that A-1 made explicit."""
    for path, _, s in modelled:
        rebuilt = s.generation_gwh * MWH_PER_GWH * s.achieved_price / EUR_PER_EUR_MILLION
        assert_ties("revenue", s.revenue, rebuilt, path)


def test_the_cash_flow_identity_holds_in_its_published_form(
    modelled: list[tuple[Path, dict[str, Any], ProjectStatements]],
) -> None:
    """§7.3 as written, which is not the form the model accumulates (1C-18).

    The model uses the algebraically identical ``- equityDrawdown`` form because
    it is numerically lossless. This asserts the published form anyway, since
    that is what 2A's validator will apply to the emitted files.
    """
    for path, _, s in modelled:
        published = (
            s.ebitda
            - s.interest_expense
            - s.debt_repayment
            - s.tax_expense
            - s.capex
            + s.debt_drawdown
        )
        assert_ties("fcfe", s.fcfe, published, path)


def test_the_debt_schedule_rolls_forward(
    modelled: list[tuple[Path, dict[str, Any], ProjectStatements]],
) -> None:
    for path, _, s in modelled:
        assert_ties(
            "closing", s.debt_closing, s.debt_opening - s.debt_repayment + s.debt_drawdown, path
        )
        assert_ties("opening", s.debt_opening[1:], s.debt_closing[:-1], path)
        assert s.debt_opening[0] == 0.0
        assert abs(float(s.debt_closing[-1])) <= TOLERANCE_ABS_M


def test_funding_ties_out(
    modelled: list[tuple[Path, dict[str, Any], ProjectStatements]],
) -> None:
    """§7.5, including the per-year identity that makes the project fully funded."""
    for path, file, s in modelled:
        capital = file["capitalStructure"]
        equity = capital["totalCapex"] - capital["seniorDebt"]
        assert_ties("sum debtDrawdown", s.debt_drawdown.sum(), capital["seniorDebt"], path)
        assert_ties("sum equityDrawdown", s.equity_drawdown.sum(), equity, path)
        assert_ties("sum capex", s.capex.sum(), capital["totalCapex"], path)
        assert_ties("per-year funding", s.capex, s.debt_drawdown + s.equity_drawdown, path)


def test_depreciation_and_ppe_tie_out(
    modelled: list[tuple[Path, dict[str, Any], ProjectStatements]],
) -> None:
    """§7.6 in A-3's residual form, which holds even for a late-COD asset."""
    for path, file, s in modelled:
        total_capex = file["capitalStructure"]["totalCapex"]
        assert_ties("sum depreciation", s.depreciation.sum(), total_capex - s.ppe[-1], path)
        previous = np.concatenate(([0.0], s.ppe[:-1]))
        assert_ties("ppe roll-forward", s.ppe, previous - s.depreciation + s.capex, path)


def test_dscr_ties_to_the_service_it_covers(
    modelled: list[tuple[Path, dict[str, Any], ProjectStatements]],
) -> None:
    for path, _, s in modelled:
        live = np.isfinite(s.dscr)
        service = (s.interest_expense + s.debt_repayment)[live]
        assert_ties("dscr", s.dscr[live] * service, s.ebitda[live], path)


def test_every_series_has_thirty_years(
    modelled: list[tuple[Path, dict[str, Any], ProjectStatements]],
) -> None:
    for _, _, s in modelled:
        for series in s:
            assert series.shape == (YEARS,)


def test_magnitude_lines_never_go_negative(
    modelled: list[tuple[Path, dict[str, Any], ProjectStatements]],
) -> None:
    """§2's sign convention, which every tie-out identity depends on being true.

    Every identity is linear, so a negative ``opex`` satisfies all of them while
    inflating EBITDA. Balances are excluded: one that amortises exactly to zero
    lands either side of it, which is why 1A floors them at -1e-9 rather than 0.
    """
    magnitudes = (
        "generation_gwh",
        "achieved_price",
        "opex",
        "depreciation",
        "interest_expense",
        "tax_expense",
        "capex",
        "debt_drawdown",
        "equity_drawdown",
        "debt_repayment",
    )
    for path, _, s in modelled:
        for name in magnitudes:
            assert np.all(getattr(s, name) >= 0.0), f"{path.name} {name} went negative"
        for name in ("debt_opening", "debt_closing", "ppe"):
            assert np.all(getattr(s, name) >= -1e-9), f"{path.name} {name} went negative"


# --------------------------------------------------------------------------
# The model's own behaviour
# --------------------------------------------------------------------------


def test_the_ramp_year_is_partial_and_carries_a_full_year_of_service(
    modelled: list[tuple[Path, dict[str, Any], ProjectStatements]],
) -> None:
    """Why exactly one post-COD year has negative FCFE, for every project.

    Debt is drawn at COD and charged a full year's interest against 55% of a
    year's output. The ramp year's DSCR is therefore well below 1.0 -- reported
    in the file, and excluded from the *minimum* (§9.4).
    """
    for path, file, s in modelled:
        ramp = ramp_index(file["asset"]["codYear"], file["assumptions"]["baseYear"])
        assert ramp is not None
        operating = np.arange(YEARS) >= ramp
        assert int(np.sum(s.fcfe[operating] < 0)) == 1, path.name
        assert s.fcfe[ramp] < 0, path.name
        assert s.dscr[ramp] < 1.0, path.name


def test_min_dscr_excludes_the_ramp_year(
    modelled: list[tuple[Path, dict[str, Any], ProjectStatements]],
) -> None:
    for _, file, s in modelled:
        ramp = ramp_index(file["asset"]["codYear"], file["assumptions"]["baseYear"])
        assert min_dscr(s.dscr, ramp) > s.dscr[ramp]


def test_min_dscr_is_nan_for_an_unlevered_project() -> None:
    """A-21: there is no coverage ratio to fail, so it is undefined, not zero."""
    assert np.isnan(min_dscr(np.full(YEARS, np.nan), None))


def test_sizing_binds_on_gearing_or_on_cover_and_the_corpus_shows_both(
    modelled: list[tuple[Path, dict[str, Any], ProjectStatements]],
) -> None:
    """D-3's two-sidedness, re-derived rather than taken from the fixture.

    Sizing off *stabilised* EBITDA rather than the minimum over the debt life is
    what gives a spread instead of pinning min DSCR at the target. The reference
    corpus splits 27 gearing-capped to 21 sculpted.
    """
    bases: dict[str, int] = {}
    for _, file, _ in modelled:
        inputs = inputs_from(file)
        terms = DebtTerms(
            target_dscr=file["assumptions"]["targetDscr"],
            rate=inputs.debt_rate,
            tenor=inputs.debt_tenor_years,
            max_gearing=file["capitalStructure"]["maxGearing"],
        )
        sized, basis = size_senior_debt(stabilised_ebitda(inputs), terms, inputs.total_capex)
        assert sized == pytest.approx(inputs.senior_debt, rel=1e-12)
        bases[basis] = bases.get(basis, 0) + 1
    assert bases == {"max-gearing-cap": 27, "dscr-sculpt": 21}


def test_stabilised_ebitda_is_not_the_first_reported_year(
    modelled: list[tuple[Path, dict[str, Any], ProjectStatements]],
) -> None:
    """The distinction that decides every capex and every debt quantum.

    Stabilised EBITDA is synthetic: a full year with one step of degradation and
    escalation. The first *reported* operating year is the ramp, at 55% output.
    A port that sizes off ``ebitda[cod]`` gets a far smaller number.
    """
    for _, file, s in modelled:
        inputs = inputs_from(file)
        ramp = ramp_index(inputs.cod_year, inputs.base_year)
        assert ramp is not None
        assert stabilised_ebitda(inputs) > s.ebitda[ramp]


def test_the_entry_clamp_bounds_the_band_on_both_sides() -> None:
    """§9.1's clamp, which is what lets uneconomic assets exist to be rejected."""
    low, high = 560.0, 950.0
    assert entry_capex_per_kw(1.0, 100.0, 0.1, low, high) == (low, "floor")
    assert entry_capex_per_kw(1e6, 100.0, 0.1, low, high) == (high, "cap")
    value, clamp = entry_capex_per_kw(7.0, 100.0, 0.1, low, high)
    assert clamp == "none"
    assert low < value < high


def test_construction_equity_is_drawn_pro_rata_and_debt_at_cod(
    modelled: list[tuple[Path, dict[str, Any], ProjectStatements]],
) -> None:
    """§6's funding convention (A-4), which fixes FCFE timing and so every IRR."""
    for path, file, s in modelled:
        cod = file["asset"]["codYear"] - file["assumptions"]["baseYear"]
        equity = file["capitalStructure"]["totalCapex"] - file["capitalStructure"]["seniorDebt"]
        if cod <= 0:
            continue
        drawn = s.equity_drawdown[:cod]
        assert np.allclose(drawn, equity / cod, rtol=0, atol=1e-12), path.name
        assert s.equity_drawdown[cod] == 0.0
        assert s.debt_drawdown[cod] == file["capitalStructure"]["seniorDebt"]
        assert float(np.sum(s.debt_drawdown[:cod])) == 0.0
