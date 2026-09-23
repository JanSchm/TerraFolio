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
"""

from __future__ import annotations

import numpy as np

from terrafolio.pipeline.arrays import BoolVector, Matrix, Vector

__all__ = ["repair_to_budget"]


def repair_to_budget(
    population: BoolVector,
    *,
    priority: Matrix,
    equity: Vector,
    budget: float,
    locked: BoolVector | None = None,
) -> BoolVector:
    """Drop holdings from each row until its equity fits ``budget``.

    ``population`` is ``(m, n)`` boolean, ``priority`` is ``(m, n)`` of the same shape
    — one fresh draw per chromosome per generation, which is what keeps the operator
    deterministic under a seeded generator and unbiased across the population.
    """
    keep_first = np.where(locked, -np.inf, priority) if locked is not None else priority
    ranked = np.argsort(np.where(population, keep_first, np.inf), axis=-1, kind="stable")

    held = np.take_along_axis(population, ranked, axis=-1)
    cumulative = np.cumsum(np.where(held, equity[ranked], 0.0), axis=-1)

    repaired = np.zeros_like(population)
    np.put_along_axis(repaired, ranked, held & (cumulative <= budget), axis=-1)
    return repaired
