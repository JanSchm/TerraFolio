"""The budget repair operator as 2A shipped it, kept verbatim as the oracle.

#12 optimises :func:`terrafolio.optimiser.repair.repair_to_budget` two ways — a row
mask and a column slice — and the whole claim behind both is that they change the
operator's *cost* and not its *answer*. That claim needs something to be checked
against, so the pre-#12 implementation lives here unchanged.

This is a fixture, not a second implementation: nothing in ``src/`` imports it, and it
must never be "kept up to date" with the optimised version. The day it is edited to
agree with the code it exists to check, it stops checking anything.

``tools/bench_ga.py`` imports it too, for the "before" column of the before/after
table, so that both the timing baseline and the correctness baseline are the same code.
"""

from __future__ import annotations

import numpy as np

from terrafolio.pipeline.arrays import BoolVector, Matrix, Vector

__all__ = ["legacy_repair_to_budget"]


def legacy_repair_to_budget(
    population: BoolVector,
    *,
    priority: Matrix,
    equity: Vector,
    budget: float,
    locked: BoolVector | None = None,
) -> BoolVector:
    """Drop holdings from each row until its equity fits ``budget``.

    Every row goes through the full argsort, gather, cumsum and scatter, over the full
    candidate width — which is exactly what #12 changes.
    """
    keep_first = np.where(locked, -np.inf, priority) if locked is not None else priority
    ranked = np.argsort(np.where(population, keep_first, np.inf), axis=-1, kind="stable")

    held = np.take_along_axis(population, ranked, axis=-1)
    cumulative = np.cumsum(np.where(held, equity[ranked], 0.0), axis=-1)

    repaired = np.zeros_like(population)
    np.put_along_axis(repaired, ranked, held & (cumulative <= budget), axis=-1)
    return repaired
