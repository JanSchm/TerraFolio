"""One real search, runnable with a substituted repair operator.

Comparing two repair operators on synthetic populations proves they agree on whatever
the test drew. It does not prove they agree on the populations the GA actually visits,
which after a few generations are nothing like uniform: almost every chromosome sits
on the equity boundary, which is precisely the case where a mask could go wrong.

So the equivalence tests run the whole search twice over the real 48-file golden
pipeline and compare the winner. The pool is built once at import — loading and
tying out 48 files costs about 40 ms, and every test here wants the same one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Final

import numpy as np
from pool import GOLDEN_PIPELINE, build_pool
from reference_mandates import REFERENCE_MANDATES

import terrafolio.optimiser.ga as ga_module
from terrafolio.config.loader import load_default
from terrafolio.domain.enums import Effort
from terrafolio.domain.scalars import MandateScalars
from terrafolio.optimiser.features import COLUMN, Features
from terrafolio.optimiser.ga import GaOutcome, SearchControls, run_search
from terrafolio.pipeline.arrays import BoolVector
from terrafolio.pipeline.loader import load_pipeline

__all__ = ["ASSUMPTIONS", "MANDATE", "POOL", "SEED", "patched_repair", "run_with_repair"]

SEED: Final = 2024
"""The seed ``tests/api/test_runner.py`` uses to compare two engines. Reused so a
divergence here and a divergence there describe the same search."""

ASSUMPTIONS: Final = load_default()
MANDATE: Final[MandateScalars] = REFERENCE_MANDATES["M0-default"]

_LOADED: Final = load_pipeline(GOLDEN_PIPELINE, ASSUMPTIONS)
POOL: Final[Features] = build_pool(_LOADED, MANDATE, ASSUMPTIONS, candidates=_LOADED.arrays.count)
"""All 48 golden projects — ``candidates`` equal to the pipeline, so nothing is tiled."""


def _without_tolerance(operator: Callable[..., BoolVector]) -> Callable[..., BoolVector]:
    """Let an operator that predates ``tolerance`` stand in for one that takes it.

    ``ga.evolve`` passes ``tolerance`` at both call sites, and the point of the oracle
    is that it never had the parameter. Dropping the keyword here rather than adding it
    there is what keeps the oracle a frozen copy.
    """

    def adapted(population: BoolVector, **kwargs: object) -> BoolVector:
        kwargs.pop("tolerance", None)
        return operator(population, **kwargs)

    return adapted


@contextmanager
def patched_repair(replacement: Callable[..., BoolVector]) -> Iterator[None]:
    """Swap the operator ``ga.evolve`` calls, and always put it back."""
    original = ga_module.repair_to_budget
    ga_module.repair_to_budget = replacement  # type: ignore[assignment]
    try:
        yield
    finally:
        ga_module.repair_to_budget = original  # type: ignore[assignment]


def run_with_repair(
    replacement: Callable[..., BoolVector] | None = None, *, effort: Effort
) -> GaOutcome:
    """Run the golden search, optionally with a different repair operator."""
    controls = SearchControls(effort=effort, seed=SEED)
    if replacement is None:
        return run_search(POOL, MANDATE, ASSUMPTIONS, controls)
    with patched_repair(_without_tolerance(replacement)):
        return run_search(POOL, MANDATE, ASSUMPTIONS, controls)


def held_equity(selection: BoolVector) -> float:
    """The equity a selection draws, in euros — for the never-over-budget assertions."""
    return float(np.asarray(POOL.fit[:, COLUMN["equity"]])[selection].sum())
