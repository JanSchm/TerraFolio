"""The house model against an analyst's file: reported, never enforced.

The variance report exists because the file is the source of truth and the house
model is not (epic §2). Two things therefore have to be true of it: it has to
find a real disagreement and say *which line* it is on, and it has to be
incapable of stopping a file loading. Q-4 and A-7 settle the second -- gating on
the house model would make it authoritative again, which is the thing the input
design set out to change.
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
from terrafolio.generate.pipeline import as_file, generate_pipeline
from terrafolio.model.variance import COMPARED_LINES, compare_to_house_model

TEMPLATE: Final = Path(__file__).resolve().parents[2] / "templates" / "project-template.json"


@pytest.fixture(scope="module")
def assumptions() -> AssumptionSet:
    return load_default()


@pytest.fixture(scope="module")
def generated(assumptions: AssumptionSet) -> list[dict[str, Any]]:
    return [as_file(built, assumptions) for built in generate_pipeline(6, 3, assumptions)]


def report_for(file: dict[str, Any], assumptions: AssumptionSet) -> Any:
    return compare_to_house_model(
        inputs_from_file(file, assumptions),
        statements_from_file(file),
        tolerance_abs=assumptions.validation.tolerance_abs_m,
        tolerance_rel=assumptions.validation.tolerance_rel,
    )


def test_a_file_the_house_model_produced_agrees_with_it(
    generated: list[dict[str, Any]], assumptions: AssumptionSet
) -> None:
    """The control. Without this, "diverges" would mean nothing."""
    for file in generated:
        report = report_for(file, assumptions)
        assert report.agrees, f"{file['id']}: {report}"
        assert len(report.lines) == len(COMPARED_LINES)


def test_the_committed_template_agrees_too(assumptions: AssumptionSet) -> None:
    """1B's worked example was produced by a different implementation entirely."""
    assert report_for(json.loads(TEMPLATE.read_text(encoding="utf-8")), assumptions).agrees


@pytest.mark.parametrize(
    ("block", "line", "attribute"),
    [
        ("incomeStatement", "opex", "opex"),
        ("incomeStatement", "taxExpense", "tax_expense"),
        ("cashFlow", "fcfe", "fcfe"),
        ("debtSchedule", "repayment", "debt_repayment"),
        ("balanceSheet", "ppe", "ppe"),
    ],
)
def test_a_perturbed_line_is_named_and_its_neighbours_are_not(
    generated: list[dict[str, Any]],
    assumptions: AssumptionSet,
    block: str,
    line: str,
    attribute: str,
) -> None:
    """The report has to point at the line, not merely say the file is wrong."""
    file = copy.deepcopy(generated[0])
    series = file["statements"][block][line]
    year = next(index for index, value in enumerate(series) if abs(value) > 1)
    series[year] = series[year] * 2

    report = report_for(file, assumptions)
    named = {item.line for item in report.diverging}
    assert named == {attribute}, f"expected only {attribute}, got {sorted(named)}"

    found = next(item for item in report.diverging if item.line == attribute)
    assert found.worst_year == year
    assert found.declared == pytest.approx(series[year])
    assert found.relative > 0


def test_perturbing_an_input_diverges_everything_that_depends_on_it(
    generated: list[dict[str, Any]], assumptions: AssumptionSet
) -> None:
    """A changed capacity factor is not a one-line disagreement.

    Generation, revenue, EBITDA, tax and FCFE all move with it, and the report
    should say so rather than blaming the first line it happens to check.
    """
    file = copy.deepcopy(generated[0])
    file["asset"]["netCapacityFactor"] *= 1.5
    named = {item.line for item in report_for(file, assumptions).diverging}
    assert {"generation_gwh", "revenue", "ebitda", "fcfe"} <= named
    assert "capex" not in named, "capex is declared, not re-derived from the resource"


def test_the_worst_year_is_the_one_outside_tolerance_not_the_biggest_number(
    generated: list[dict[str, Any]], assumptions: AssumptionSet
) -> None:
    """Ranking by raw residual would always point at the largest line.

    A EUR 0.02m miss on a EUR 1m line is a real breach; the same miss on a EUR
    2,000m line is inside the relative limb. The report ranks by breach over
    tolerance so the small line wins, which is the one worth reading.
    """
    file = copy.deepcopy(generated[0])
    revenue = file["statements"]["incomeStatement"]["revenue"]
    small = min(
        (index for index, value in enumerate(revenue) if value > 0), key=lambda i: revenue[i]
    )
    large = max(range(len(revenue)), key=lambda i: revenue[i])
    assert revenue[large] > revenue[small] * 2
    revenue[small] += assumptions.validation.tolerance_abs_m * 10
    revenue[large] += assumptions.validation.tolerance_abs_m * 10

    found = next(item for item in report_for(file, assumptions).diverging if item.line == "revenue")
    assert found.worst_year == small


def test_a_variance_is_reported_and_never_raised(
    generated: list[dict[str, Any]], assumptions: AssumptionSet
) -> None:
    """A-7 and Q-4. A wildly wrong file still returns a report."""
    file = copy.deepcopy(generated[0])
    for block, lines in file["statements"].items():
        if block == "years":
            continue
        for name, series in lines.items():
            if name == "dscr":
                continue
            lines[name] = [value * -3 for value in series]
    report = report_for(file, assumptions)
    assert not report.agrees
    assert len(report.diverging) > 5
    assert "diverges" in str(report)


def test_diverging_lines_come_first_and_worst_first(
    generated: list[dict[str, Any]], assumptions: AssumptionSet
) -> None:
    file = copy.deepcopy(generated[0])
    file["statements"]["incomeStatement"]["opex"] = [
        value * 4 for value in file["statements"]["incomeStatement"]["opex"]
    ]
    file["statements"]["balanceSheet"]["ppe"] = [
        value * 1.001 for value in file["statements"]["balanceSheet"]["ppe"]
    ]
    report = report_for(file, assumptions)
    diverging = [item.line for item in report.lines if item.diverges]
    assert [item.line for item in report.lines][: len(diverging)] == diverging
    assert report.lines[0].relative >= report.lines[1].relative


def test_the_report_reads_as_a_sentence(
    generated: list[dict[str, Any]], assumptions: AssumptionSet
) -> None:
    """It is shown to an analyst, so it has to say something in plain words."""
    assert "reproduces this file" in str(report_for(generated[0], assumptions))
    file = copy.deepcopy(generated[0])
    file["statements"]["incomeStatement"]["opex"][5] += 5.0
    text = str(report_for(file, assumptions))
    assert "opex" in text
    assert "file" in text
    assert "model" in text
