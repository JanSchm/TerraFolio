"""§7's blocking tie-outs: they hold on real data, and they catch a file that lies.

The distinction this module exists to keep is between a file that **disagrees
with itself** -- which nothing can make loadable, so §7 blocks it -- and a file
the house model would have computed differently, which an analyst is entitled to
overrule (A-7, Q-4). A perturbation that breaks an identity must be caught here;
one that only changes an input must not.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Final

import pytest

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.config.loader import load_default
from terrafolio.generate.from_file import inputs_from_file, statements_from_file
from terrafolio.model.tieouts import check_tie_outs

REPO: Final = Path(__file__).resolve().parents[2]
TEMPLATE: Final = REPO / "templates" / "project-template.json"
GOLDEN: Final = REPO / "tests" / "golden" / "fixtures" / "pipeline"
SHIPPED: Final = REPO / "pipeline"


@pytest.fixture(scope="module")
def assumptions() -> AssumptionSet:
    return load_default()


@pytest.fixture
def template() -> dict[str, Any]:
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


def failures(file: dict[str, Any], assumptions: AssumptionSet) -> tuple[Any, ...]:
    return check_tie_outs(
        inputs_from_file(file, assumptions),
        statements_from_file(file),
        tolerance_abs=assumptions.validation.tolerance_abs_m,
        tolerance_rel=assumptions.validation.tolerance_rel,
    )


# --------------------------------------------------------------------------
# Real data ties out
# --------------------------------------------------------------------------


def test_the_worked_example_ties_out(template: dict[str, Any], assumptions: AssumptionSet) -> None:
    assert failures(template, assumptions) == ()


def test_every_golden_file_ties_out(assumptions: AssumptionSet) -> None:
    paths = sorted(GOLDEN.glob("*.json"))
    assert len(paths) == 48
    for path in paths:
        found = failures(json.loads(path.read_text(encoding="utf-8")), assumptions)
        assert found == (), f"{path.name}: {[str(item) for item in found]}"


def test_every_shipped_pipeline_file_ties_out(assumptions: AssumptionSet) -> None:
    """Issue #8's first acceptance criterion, asserted against what is committed."""
    paths = sorted(SHIPPED.glob("*.json"))
    assert len(paths) == 300
    for path in paths:
        found = failures(json.loads(path.read_text(encoding="utf-8")), assumptions)
        assert found == (), f"{path.name}: {[str(item) for item in found]}"


# --------------------------------------------------------------------------
# A file that lies is caught, and named
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "perturbation",
    [
        ("incomeStatement", "ebitda", 5, "ebitda = revenue - opex"),
        ("incomeStatement", "netIncome", 7, "netIncome = pbt - taxExpense"),
        ("incomeStatement", "revenue", 6, "revenue = generationGwh"),
        ("cashFlow", "fcfe", 9, "fcfe = ebitda"),
        ("cashFlow", "equityDrawdown", 0, "capex = debtDrawdown + equityDrawdown"),
        ("balanceSheet", "ppe", 3, "ppe[t] = ppe[t-1]"),
    ],
    ids=["ebitda", "netIncome", "revenue", "fcfe", "equityDrawdown", "ppe"],
)
def test_a_broken_identity_is_caught_and_named(
    template: dict[str, Any],
    assumptions: AssumptionSet,
    perturbation: tuple[str, str, int, str],
) -> None:
    """Every value stays a plausible positive magnitude, so only the identity betrays it."""
    block, line, index, expected = perturbation
    broken = copy.deepcopy(template)
    broken["statements"][block][line][index] += 5.0
    found = failures(broken, assumptions)
    assert found, f"{block}.{line} perturbation went unnoticed"
    assert any(expected in item.check for item in found), [item.check for item in found]
    # The perturbed year, or the one after it: a balance feeds the next year's
    # roll-forward, so moving `ppe[3]` breaks both year 3 and year 4, and the
    # checker reports the worst breach rather than the first.
    assert any(item.year in (index, index + 1) for item in found if item.year is not None)


def test_a_residual_inside_the_tolerance_is_not_a_failure(
    template: dict[str, Any], assumptions: AssumptionSet
) -> None:
    """§7 allows EUR 0.01m; a file is not required to be exact, only coherent."""
    edged = copy.deepcopy(template)
    edged["statements"]["cashFlow"]["fcfe"][9] += assumptions.validation.tolerance_abs_m / 2
    assert failures(edged, assumptions) == ()


def test_a_debt_schedule_that_does_not_close_is_caught(
    template: dict[str, Any], assumptions: AssumptionSet
) -> None:
    broken = copy.deepcopy(template)
    broken["statements"]["debtSchedule"]["closing"][-1] = 3.0
    checks = {item.check for item in failures(broken, assumptions)}
    assert "closing[last] = 0 (fully amortised)" in checks


def test_funding_that_does_not_sum_is_caught(
    template: dict[str, Any], assumptions: AssumptionSet
) -> None:
    broken = copy.deepcopy(template)
    broken["capitalStructure"]["seniorDebt"] += 1.0
    checks = {item.check for item in failures(broken, assumptions)}
    assert "sum debtDrawdown = seniorDebt" in checks
    assert "sum equityDrawdown = totalCapex - seniorDebt" in checks


def test_a_misplaced_dscr_is_caught(template: dict[str, Any], assumptions: AssumptionSet) -> None:
    """§7.8's coverage rule is blocking in its own right.

    A value outside the debt life is as much a failure as a blank inside it, and
    neither is visible to a check that only looks at the numbers present.
    """
    outside = copy.deepcopy(template)
    outside["statements"]["ratios"]["dscr"][25] = 1.5
    assert any(
        item.check == "dscr is present exactly inside the debt life"
        for item in failures(outside, assumptions)
    )

    blank = copy.deepcopy(template)
    blank["statements"]["ratios"]["dscr"][5] = None
    assert any(
        item.check == "dscr is present exactly inside the debt life"
        for item in failures(blank, assumptions)
    )


def test_changing_only_an_input_is_not_a_tie_out_failure(
    template: dict[str, Any], assumptions: AssumptionSet
) -> None:
    """The distinction the whole module exists for.

    A different capacity factor makes the house model disagree -- that is the
    variance report's business, and advisory. The statements still agree with
    each other, so nothing here should fire.
    """
    altered = copy.deepcopy(template)
    altered["asset"]["netCapacityFactor"] *= 1.2
    assert failures(altered, assumptions) == ()


def test_all_failures_are_reported_not_just_the_first(
    template: dict[str, Any], assumptions: AssumptionSet
) -> None:
    """A file with three broken blocks should take one edit to fix, not three rounds."""
    broken = copy.deepcopy(template)
    broken["statements"]["incomeStatement"]["opex"][4] += 3.0
    broken["statements"]["debtSchedule"]["closing"][-1] = 2.0
    broken["statements"]["balanceSheet"]["ppe"][2] += 1.0
    checks = {item.check for item in failures(broken, assumptions)}
    assert len(checks) >= 3


def test_a_failure_reads_as_a_sentence(
    template: dict[str, Any], assumptions: AssumptionSet
) -> None:
    """It is shown to an analyst editing a workbook, so it names check, year and residual."""
    broken = copy.deepcopy(template)
    broken["statements"]["incomeStatement"]["ebitda"][5] += 5.0
    text = str(failures(broken, assumptions)[0])
    assert "year index 5" in text
    assert "residual" in text
    assert "tolerance" in text
