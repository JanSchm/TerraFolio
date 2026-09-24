"""The budget repair operator — epic §6.1's other half.

§10.1 as written initialises at a flat 35% inclusion. That is calibrated for the
mockup's 48-project pipeline, where 35% is about 17 projects and about the right size
for €1,200m of equity. At the 300-500 candidates this application targets it selects
105-175 projects needing many times the budget, **every** chromosome lands in the
rejection band, the landscape goes flat, tournament selection becomes a coin flip, and
the run returns garbage while looking converged. Measured at 500 candidates: -28.77
against +7.88 with capital-aware initialisation and this operator.

So chromosomes are repaired into their budget rather than merely punished for missing
it. Holdings are kept in a **random priority order** until the cumulative equity no
longer fits; the rest are dropped. Random rather than greedy-by-anything on purpose —
a greedy rule would bias every chromosome towards the same subset and collapse the
diversity the search depends on.

**Vectorised, necessarily.** The row-by-row loop is about 100x slower and defeats the
whole design: it timed out a 120 s benchmark where this form takes 2.5 s.

**Locked holdings survive.** They sort first, so the budget is spent on them before
anything else. If the locks alone exceed the budget there is nothing to repair, and
that case never reaches the search — §5.4's ``LOCKS_EXCEED_CAPITAL`` is blocking and
``POST /optimisations`` answers 422.

TWO MASKS, ONE OF WHICH PAYS (#12)
==================================

``cProfile`` puts this operator at 55% of an Exhaustive run at 2,000 candidates, spread
across the argsort, the gather, the cumsum and the scatter — all of which run at the
full ``(m, n)``. #12 narrows both axes.

**By column, unconditionally.** No row holds more than ``width`` projects, so ranked
positions beyond ``width`` are unheld in *every* row. ``held`` is ``False`` there and
the scatter would write back the zeros it started from, which means the gather, the
accumulation and the scatter can stop at ``width`` — about 22 of 2,000 in practice. The
ranking itself still runs over the full row, so sort stability is untouched and the
answer is bit-identical; ``tests/regression/test_repair_equivalence.py`` holds that
against the pre-#12 implementation.

**By row, only when asked.** ``tolerance`` skips rows already inside their budget,
which is what epic §7 predicted would be the win. It is not: measured over real runs,
95-99% of rows are over budget at every call, because the utilisation reward parks
chromosomes on the budget boundary and mutation and crossover push nearly every child
back over it. The mask ships because it costs almost nothing and protects the one case
where it would matter — a mandate whose capital dwarfs its pipeline — but the numbers
are in ``docs/decisions.md`` 4B-1 so nobody expects it to do more.

``tolerance`` is a **margin below the budget**, not a margin above it. A row whose total
sits exactly on the budget has a pairwise row sum of exactly ``budget`` and a ranked
``cumsum`` that overshoots it by a last-bit fraction, so skipping such a row would keep
a holding the unmasked operator drops. Widening the mask by the tolerance puts every
such row back on the full path. ``None`` means no row mask at all, which is the pre-#12
behaviour exactly; ``ga.py`` passes ``objective.equity_cap_tolerance_eur``, the constant
epic §6.2 introduced for this same rounding artefact on this same boundary.
"""

from __future__ import annotations

import numpy as np

from terrafolio.pipeline.arrays import BoolVector, IntVector, Matrix, Vector

__all__ = ["repair_to_budget"]


def _rows_to_repair(
    population: BoolVector, equity: Vector, budget: float, tolerance: float | None
) -> slice | IntVector:
    """Which rows need the full treatment, or every row when ``tolerance`` is ``None``.

    The row totals are a pairwise ``sum``, deliberately not ``population @ equity``: a
    GEMM's reduction order depends on BLAS blocking and thread count, and the one place
    that may vary across machines is the fitness the GA quantises, not an operator that
    decides which holdings survive.
    """
    if tolerance is None:
        return slice(None)
    held = np.where(population, equity, 0.0).sum(axis=-1)
    return np.flatnonzero(held > budget - tolerance)


def repair_to_budget(  # noqa: PLR0913 - one keyword per axis of the operator, by design
    population: BoolVector,
    *,
    priority: Matrix,
    equity: Vector,
    budget: float,
    locked: BoolVector | None = None,
    tolerance: float | None = None,
) -> BoolVector:
    """Drop holdings from each row until its equity fits ``budget``.

    ``population`` is ``(m, n)`` boolean, ``priority`` is ``(m, n)`` of the same shape
    — one fresh draw per chromosome per generation, which is what keeps the operator
    deterministic under a seeded generator and unbiased across the population.

    ``tolerance`` is how far below ``budget`` a row may total and still be left alone.
    The answer does not depend on it; only the cost does.
    """
    rows = _rows_to_repair(population, equity, budget, tolerance)
    over = population[rows]
    priorities = priority[rows]
    keep_first = np.where(locked, -np.inf, priorities) if locked is not None else priorities

    width = int(over.sum(axis=-1).max(initial=0))
    ranked = np.argsort(np.where(over, keep_first, np.inf), axis=-1, kind="stable")[:, :width]

    held = np.take_along_axis(over, ranked, axis=-1)
    cumulative = np.cumsum(np.where(held, equity[ranked], 0.0), axis=-1)

    trimmed = np.zeros_like(over)
    np.put_along_axis(trimmed, ranked, held & (cumulative <= budget), axis=-1)

    repaired = population.copy()
    repaired[rows] = trimmed
    return repaired
