"""#12 made the budget repair cheaper. This is the proof it did not make it different.

Two masks were added to :func:`terrafolio.optimiser.repair.repair_to_budget`: a column
slice that stops the gather, the accumulation and the scatter at the widest held count,
and an optional row mask that skips rows already inside their budget. Neither is allowed
to move a single bit of the answer, because the GA's whole trajectory turns on which
holdings survive a repair — so every test here compares against
:func:`legacy_repair.legacy_repair_to_budget`, the operator as 2A shipped it.

The oracle is deliberately a frozen copy rather than a re-derivation. 2A's
``tests/unit/test_optimiser_ga.py`` already checks the vectorised form against a
row-by-row loop, which pins the *semantics*; this pins the *refactor*, and the two
failures look completely different when they happen.
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from legacy_repair import legacy_repair_to_budget
from searches import ASSUMPTIONS, run_with_repair

from terrafolio.domain.enums import Effort
from terrafolio.optimiser.repair import repair_to_budget
from terrafolio.pipeline.arrays import BoolVector, Matrix, Vector

TOLERANCE = ASSUMPTIONS.objective.equity_cap_tolerance_eur
"""What ``ga.py`` passes: €1, on sums of order €10^9."""

# No deadline, for the reason tests/property/test_objective_monotonicity.py gives: these
# assert an equality, and hypothesis's 200 ms default measures the machine instead.
EXAMPLES = settings(max_examples=250, deadline=None)


def _draw(seed: int, rows: int, columns: int, density: float) -> tuple[BoolVector, Matrix, Vector]:
    """A population, its priorities and an equity column, from one seeded stream.

    Equity is rounded to the cent and priorities to two places on purpose: exact ties
    in the priority draw are what would expose a non-stable sort, and round decimals are
    what put a row's total *exactly* on a budget.
    """
    rng = np.random.default_rng(seed)
    population = rng.random((rows, columns)) < density
    priority = np.round(rng.random((rows, columns)), 2)
    equity = np.round(rng.uniform(0.0, 50.0, size=columns), 2)
    return population, priority, equity


@given(
    seed=st.integers(min_value=0, max_value=2**32 - 1),
    rows=st.integers(min_value=1, max_value=40),
    columns=st.integers(min_value=1, max_value=60),
    density=st.floats(min_value=0.0, max_value=1.0),
    share=st.floats(min_value=0.0, max_value=1.2),
    with_locks=st.booleans(),
)
@EXAMPLES
def test_both_masks_answer_exactly_what_the_pre_masking_operator_answered(  # noqa: PLR0913, PLR0917 - one hypothesis strategy per axis of the operator
    seed: int, rows: int, columns: int, density: float, share: float, with_locks: bool
) -> None:
    population, priority, equity = _draw(seed, rows, columns, density)
    locked = None
    if with_locks:
        locked = np.random.default_rng(seed + 1).random(columns) < 0.15
    budget = float(equity.sum() * share)

    expected = legacy_repair_to_budget(
        population, priority=priority, equity=equity, budget=budget, locked=locked
    )
    for tolerance in (None, TOLERANCE):
        actual = repair_to_budget(
            population,
            priority=priority,
            equity=equity,
            budget=budget,
            locked=locked,
            tolerance=tolerance,
        )
        assert np.array_equal(actual, expected), f"diverged at tolerance={tolerance}"


@pytest.mark.parametrize("tolerance", [None, TOLERANCE])
@pytest.mark.parametrize(("rows", "columns"), [(0, 5), (3, 0), (0, 0), (1, 1)])
def test_an_empty_axis_is_shaped_and_not_special_cased(
    tolerance: float | None, rows: int, columns: int
) -> None:
    """A pipeline with no eligible candidate, and a population of none."""
    population = np.zeros((rows, columns), dtype=np.bool_)
    priority = np.zeros((rows, columns))
    equity = np.ones(columns)

    repaired = repair_to_budget(
        population, priority=priority, equity=equity, budget=10.0, tolerance=tolerance
    )
    assert repaired.shape == (rows, columns)
    assert repaired.dtype == np.bool_
    assert not repaired.any()


@pytest.mark.parametrize("tolerance", [None, TOLERANCE])
def test_a_row_of_nothing_but_locks_is_still_trimmed_to_the_budget(
    tolerance: float | None,
) -> None:
    """Locks sort first, but three €50 locks do not fit €100.

    ``LOCKS_EXCEED_CAPITAL`` stops this reaching the search in production (§5.4), so
    what matters here is only that the operator does not loop forever or keep more
    than the budget allows when it does.
    """
    equity = np.array([50.0, 50.0, 50.0])
    population = np.ones((1, 3), dtype=np.bool_)
    priority = np.array([[0.9, 0.1, 0.5]])
    locked = np.ones(3, dtype=np.bool_)

    repaired = repair_to_budget(
        population,
        priority=priority,
        equity=equity,
        budget=100.0,
        locked=locked,
        tolerance=tolerance,
    )
    assert float(equity[repaired[0]].sum()) <= 100.0
    assert np.array_equal(
        repaired,
        legacy_repair_to_budget(
            population, priority=priority, equity=equity, budget=100.0, locked=locked
        ),
    )


def test_the_row_mask_is_a_margin_below_the_budget_not_a_test_against_it() -> None:
    """Why ``tolerance`` is subtracted, and why a zero margin would be wrong.

    A row whose holdings total exactly the budget has a pairwise row sum of exactly the
    budget — so a bare ``total > budget`` test skips it — while the ranked ``cumsum``
    overshoots by a last-bit fraction and drops its final holding. Widening the mask by
    any positive margin puts the row back on the full path, which is why ``ga.py``
    passes €1 rather than nothing.
    """
    # Whether the ranked cumsum lands above or below the pairwise sum depends on the
    # numbers, so this example was searched for rather than picked: seed 0 of this
    # construction overshoots by 1.14e-13 on numpy 2.4.6. The assertions below fail
    # loudly if a numpy release ever makes it undershoot, because then the example has
    # stopped testing what it was built to test.
    rng = np.random.default_rng(0)
    equity = np.round(rng.uniform(0.5, 40.0, 40), 2)
    priority = np.round(rng.random((1, equity.size)), 2)
    population = np.ones((1, equity.size), dtype=np.bool_)
    budget = float(np.where(population[0], equity, 0.0).sum())

    expected = legacy_repair_to_budget(population, priority=priority, equity=equity, budget=budget)
    ranked = np.argsort(np.where(population[0], priority[0], np.inf), kind="stable")
    overshoot = float(np.cumsum(np.where(population[0][ranked], equity[ranked], 0.0))[-1]) - budget
    assert overshoot > 0.0, "this example no longer exercises the artefact it was built for"
    assert int(expected.sum()) < equity.size, "the oracle should drop the last holding here"

    skipped = repair_to_budget(
        population, priority=priority, equity=equity, budget=budget, tolerance=0.0
    )
    assert int(skipped.sum()) == equity.size, "a zero margin skips the row, as documented"

    for tolerance in (None, 1e-9, TOLERANCE):
        repaired = repair_to_budget(
            population, priority=priority, equity=equity, budget=budget, tolerance=tolerance
        )
        assert np.array_equal(repaired, expected), f"diverged at tolerance={tolerance}"


@pytest.mark.parametrize("effort", list(Effort))
def test_a_whole_search_finds_the_same_portfolio_either_way(effort: Effort) -> None:
    """The end the masks are a means to: same seed, same winner, same score.

    Comparing operators in isolation proves they agree on the populations the test
    happens to draw. This runs the real search twice over a real pipeline, so the
    populations are the ones the GA actually visits — including the late generations
    where almost every chromosome sits on the budget boundary.
    """
    expected = run_with_repair(legacy_repair_to_budget, effort=effort)
    actual = run_with_repair(effort=effort)

    assert np.array_equal(actual.selection, expected.selection)
    assert actual.fitness == expected.fitness
    assert [event.best_fitness for event in actual.convergence] == [
        event.best_fitness for event in expected.convergence
    ]
