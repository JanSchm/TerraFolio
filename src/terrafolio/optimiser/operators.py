"""Initialisation, selection, crossover, mutation and elitism — all population-wide.

Every operator here takes the whole population and returns the whole population. There
is no loop over chromosomes anywhere in this module, and that is a correctness property
as much as a speed one: a row-at-a-time operator draws from the generator in a
different order than a vectorised one, and the draw order *is* the reproducibility
contract (see :mod:`terrafolio.optimiser.ga`).

**Capital-aware initialisation** is epic §6.1. The inclusion probability is scaled from
the mandate — ``min(ceiling, capacity_target / Σ MW, capital / Σ equity)``, clipped to
the configured floor — rather than fixed at §10.1's 0.35, which survives as the
ceiling. Both the numerator and the denominator of each ratio are properties of the
mandate and the eligible pool, so this is not a tuned constant in disguise.
"""

from __future__ import annotations

import numpy as np

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.pipeline.arrays import BoolVector, IntVector, Matrix, Vector

__all__ = [
    "elite_order",
    "force_locks",
    "inclusion_probability",
    "initial_population",
    "mutate",
    "tournament_select",
    "uniform_crossover",
]


def inclusion_probability(
    *,
    capacity_target_mw: float,
    available_capital_eur: float,
    total_capacity_mw: float,
    total_equity_eur: float,
    assumptions: AssumptionSet,
) -> float:
    """How likely each gene is to start set, scaled from the mandate (epic §6.1).

    Both ratios answer "what fraction of this pool would I have to hold to reach the
    target" — one in capacity, one in capital — and the binding one is the smaller.
    The floor keeps a tiny mandate against a huge pool from initialising every
    chromosome empty, which would make the first generation carry no information.
    """
    ga = assumptions.ga
    candidates = [ga.inclusion_ceiling]
    if total_capacity_mw > 0.0:
        candidates.append(capacity_target_mw / total_capacity_mw)
    if total_equity_eur > 0.0:
        candidates.append(available_capital_eur / total_equity_eur)
    return float(np.clip(min(candidates), ga.inclusion_floor, ga.inclusion_ceiling))


def initial_population(
    draws: Matrix, probability: float, locked: BoolVector | None = None
) -> BoolVector:
    """``(pop, n)`` starting chromosomes from one block of uniform draws."""
    population = draws < probability
    return force_locks(population, locked)


def force_locks(population: BoolVector, locked: BoolVector | None) -> BoolVector:
    """Set every locked gene, which §10.1 calls for after each operator.

    Returns the input untouched when nothing is locked, so the common case allocates
    nothing and the lock path is not a special case anyone has to remember.
    """
    if locked is None or not locked.any():
        return population
    return population | locked


def elite_order(fitness: Vector) -> IntVector:
    """Population indices, best first, with ties keeping their existing order.

    ``kind="stable"`` is the requirement: an unstable sort would reorder equally-fit
    chromosomes differently on another machine or another numpy, and the elites carried
    into the next generation would differ from there on.
    """
    order: IntVector = np.argsort(-fitness, kind="stable")
    return order


def tournament_select(draws: IntVector, fitness: Vector) -> IntVector:
    """Binary tournament, generalised to ``draws.shape[0]`` contestants.

    ``draws`` is ``(tournament_size, count)`` of population indices. The loop runs over
    the tournament size — two — not over the population, and **ties go to the later
    draw**, which is what the reference's ``a.f > b.f ? a : b`` does.
    """
    winners = draws[0]
    for challenger in draws[1:]:
        winners = np.where(fitness[challenger] >= fitness[winners], challenger, winners)
    selected: IntVector = np.asarray(winners, dtype=np.int64)
    return selected


def uniform_crossover(coin: BoolVector, first: BoolVector, second: BoolVector) -> BoolVector:
    """Per-gene choice between two parents.

    ``coin`` is an even Bernoulli draw — that is what makes the crossover *uniform*,
    so it is drawn as an integer in ``{0, 1}`` rather than compared against a
    probability the assumption set does not carry and should not have to.
    """
    return np.where(coin, first, second)


def mutate(draws: Matrix, children: BoolVector, assumptions: AssumptionSet) -> BoolVector:
    """Flip each gene with the configured probability, after crossover."""
    return children ^ (draws < assumptions.ga.mutation_rate)
