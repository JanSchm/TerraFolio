"""One real search, runnable with a substituted repair operator.

Comparing two repair operators on synthetic populations proves they agree on whatever
the test drew. It does not prove they agree on the populations the GA actually visits,
which after a few generations are nothing like uniform: almost every chromosome sits
on the equity boundary, which is precisely the case where a mask could go wrong.

So the equivalence tests run the whole search twice over the real 48-file golden
pipeline and compare the winner. The pool is built on first use and cached: loading and
tying out 48 files is not work a test that never touches the pool should pay, and doing
it at import turns a moved fixture or a tie-out regression into a collection error
rather than a test failure.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import cache
from typing import Final

from pool import GOLDEN_PIPELINE, build_pool
from reference_mandates import REFERENCE_MANDATES

import terrafolio.optimiser.ga as ga_module
from terrafolio.config.loader import load_default
from terrafolio.domain.enums import Effort
from terrafolio.domain.scalars import MandateScalars
from terrafolio.optimiser.features import Features
from terrafolio.optimiser.ga import GaOutcome, SearchControls, run_search
from terrafolio.pipeline.arrays import BoolVector
from terrafolio.pipeline.loader import load_pipeline

__all__ = ["ASSUMPTIONS", "MANDATE", "SEED", "patched_repair", "pool", "run_with_repair"]

SEED: Final = 2024
"""The seed ``tests/api/test_runner.py`` uses to compare two engines. Reused so a
divergence here and a divergence there describe the same search."""

ASSUMPTIONS: Final = load_default()
MANDATE: Final[MandateScalars] = REFERENCE_MANDATES["M0-default"]


@cache
def pool() -> Features:
    """All 48 golden projects, loaded once per session on first use.

    ``candidates`` equals the pipeline's own count, so nothing is tiled and every id is
    real — a tiled pool is for cost measurements only (see ``pool.build_pool``).
    """
    loaded = load_pipeline(GOLDEN_PIPELINE, ASSUMPTIONS)
    return build_pool(loaded, MANDATE, ASSUMPTIONS, candidates=loaded.arrays.count)


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
        return run_search(pool(), MANDATE, ASSUMPTIONS, controls)
    with patched_repair(_without_tolerance(replacement)):
        return run_search(pool(), MANDATE, ASSUMPTIONS, controls)
