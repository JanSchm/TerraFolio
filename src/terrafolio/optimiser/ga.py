"""The search itself, and the draw order that makes it reproducible.

THE PRNG CONSUMPTION CONTRACT
=============================

The same mandate, pipeline snapshot, assumption set and seed must produce a
bit-identical portfolio (§10.1). With a seeded generator that reduces to one rule:
**every run must draw from the generator in the same order, in the same shapes**. Add
a draw, reorder two, or split one block into two, and every subsequent number changes.

Initialisation makes **two** calls, in this order:

1. ``random((population, n))`` — the inclusion draw
2. ``random((population, n))`` — the repair priority

Each generation then makes **five**, in this order:

3. ``integers(0, population, size=(tournament_size, children))`` — one parent's contestants
4. ``integers(0, population, size=(tournament_size, children))`` — the other parent's
5. ``integers(0, 1, size=(children, n), endpoint=True)`` — the crossover coin
6. ``random((children, n))`` — the mutation draw
7. ``random((children, n))`` — the repair priority

The two tournaments are drawn separately rather than as one ``(2, ...)`` block because
the parents must be selected **independently**: reusing one block and reversing it
gives the same winner except on ties, so almost every child would cross a chromosome
with itself and the search would stop exploring.

where ``children = population - elite_count``. ``tests/unit/test_optimiser_ga.py``
wraps the generator in a counting proxy and asserts both the count and the shapes, so
this docstring cannot quietly stop being true.

WHAT THIS DELIBERATELY DOES NOT REPRODUCE
=========================================

``tests/golden/fixtures/ga_trace_fast_seed42.json`` traces the JavaScript reference's
GA and says so in its own ``semantics.note``: flat 35% initialisation, no repair, and
the mockup's reject score. The production search departs from all three per epic §6.1
and §6.2, so it **must not** be expected to reproduce that trace. What the trace is
still good for — the tournament and crossover mechanics, elitism, and sort stability —
is asserted against it directly.

PRECISION
=========

The per-generation fitness that drives *selection* is computed from a float32 matrix
product, which is measurably faster for these shapes and is the only place float32
appears. Every fitness that is **reported** — the streamed best, the final score — is
recomputed in float64 from the winning chromosome, so nothing downstream inherits the
hot path's precision. Both are quantised before comparison.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass

import numpy as np

from terrafolio.config.assumptions import AssumptionSet, EffortParams
from terrafolio.domain.enums import Effort
from terrafolio.domain.scalars import MandateScalars
from terrafolio.optimiser.aggregate import aggregate
from terrafolio.optimiser.features import COLUMN, Features
from terrafolio.optimiser.objective import quantise, score
from terrafolio.optimiser.operators import (
    elite_order,
    force_locks,
    inclusion_probability,
    initial_population,
    mutate,
    tournament_select,
    uniform_crossover,
)
from terrafolio.optimiser.repair import repair_to_budget
from terrafolio.pipeline.arrays import BoolVector, Vector

__all__ = [
    "GaOutcome",
    "GenerationEvent",
    "SearchControls",
    "evolve",
    "resolve_seed",
    "run_search",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchControls:
    """The run controls, which are not part of the mandate.

    Effort, locks and the seed live on the run record rather than on
    :class:`MandateScalars` — two runs of the same mandate may differ in all three —
    so they travel together rather than as three more parameters on every signature.
    """

    effort: Effort
    locked: BoolVector | None = None
    seed: int | None = None

    deterministic_reduction: bool = False
    """Reduce without BLAS, so the answer is reproducible across architectures.

    A run control rather than an assumption on purpose (#12, 4B-3).
    ``assumption_set_id`` is the digest of every non-metadata section of the
    calibration, and the seed pipeline's generator salts its PRNG with it — so a new
    key in the assumption set would change the id and oblige 2C to regenerate all 300
    committed files, which is a great deal of cascade for an execution mode. This sits
    beside ``effort`` and ``seed``, which are already run controls and not mandate.

    Costs 17-45x on the reduction, which is 2.13 ms per generation at (160, 2000) —
    about 0.23 s added to an Exhaustive run, so it is usable rather than theoretical.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerationEvent:
    """One generation's progress, for §6's search screen and ``docs/api.md`` §5's SSE.

    ``best_*`` describes the running best portfolio so the page can show headline
    figures while the search is still going. Everything is float64 and quantised.
    """

    generation: int
    total_generations: int
    best_fitness: float
    mean_fitness: float
    best_project_count: int
    best_capacity_mw: float
    best_equity: float
    best_blended_irr: float | None
    """``None`` where no held project has a defined IRR — never ``0.0`` (§13)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class GaOutcome:
    """The winner, and everything needed to reproduce the run that found it."""

    seed: int
    """Resolved. A run with no recorded seed is not reproducible and so not a run."""

    selection: BoolVector
    fitness: float
    convergence: tuple[GenerationEvent, ...]
    population_size: int
    generations: int
    inclusion_probability: float


def resolve_seed(seed: int | None) -> int:
    """Use the given seed, or draw one and hand it back to be recorded.

    Drawn from the operating system rather than from any generator in this module: a
    seed that came out of a seeded stream would make two "unseeded" runs identical,
    which is the opposite of what an unspecified seed means.
    """
    if seed is not None:
        return int(seed)
    entropy = np.random.SeedSequence().entropy
    # SeedSequence types `entropy` as int | Sequence[int] | None; the no-argument
    # constructor always produces a single integer, and a pool would not be a seed
    # anyone could write down in a run record.
    if not isinstance(entropy, int):  # pragma: no cover - defensive, numpy-version guard
        raise TypeError(f"expected a single integer of entropy, got {type(entropy).__name__}")
    return entropy


def _effort_params(effort: Effort, assumptions: AssumptionSet) -> EffortParams:
    params = assumptions.ga.effort[effort]
    if params.population & 1:
        raise ValueError(
            f"{effort} has an odd population of {params.population}; the elite and "
            f"child halves must divide evenly, and silently dropping a chromosome "
            f"would change the search without changing the configuration"
        )
    return params


def evolve(
    features: Features,
    mandate: MandateScalars,
    assumptions: AssumptionSet,
    controls: SearchControls,
) -> Generator[GenerationEvent, None, GaOutcome]:
    """Run the search, yielding one event per generation and returning the outcome.

    A generator rather than a callback so the optimiser never learns what HTTP is:
    3A drives it with ``next`` and streams, the CLI drains it, and both get the same
    :class:`GaOutcome` from ``StopIteration.value``.
    """
    effort, locked = controls.effort, controls.locked
    deterministic = controls.deterministic_reduction
    params = _effort_params(effort, assumptions)
    population_size, generations = params.population, params.generations
    candidates = features.project_count
    elites = assumptions.ga.elite_count
    children = population_size - elites
    tournament_size = assumptions.ga.tournament_size

    equity = features.fit[:, COLUMN["equity"]]
    budget = mandate.available_capital_eur
    # A row totalling within this of the budget is left alone rather than re-ranked.
    # The same constant epic §6.2 put on the objective's cap, for the same reason: a
    # chromosome sitting exactly on the boundary must not be moved by a rounding
    # artefact, and the utilisation reward actively pushes them onto it (#12, 4B-2).
    cap_tolerance = assumptions.objective.equity_cap_tolerance_eur
    resolved = resolve_seed(controls.seed)
    rng = np.random.default_rng(resolved)

    probability = inclusion_probability(
        capacity_target_mw=mandate.capacity_target_mw,
        available_capital_eur=budget,
        total_capacity_mw=float(features.fit[:, COLUMN["capacity_mw"]].sum()),
        total_equity_eur=float(equity.sum()),
        assumptions=assumptions,
    )

    # --- initialisation: two draws -----------------------------------------
    population = initial_population(rng.random((population_size, candidates)), probability, locked)
    population = repair_to_budget(
        population,
        priority=rng.random((population_size, candidates)),
        equity=equity,
        budget=budget,
        locked=locked,
        tolerance=cap_tolerance,
    )

    def exact_fitness(selection: BoolVector) -> tuple[float, object]:
        """Re-score one chromosome in float64, whatever the hot path used."""
        totals = aggregate(
            features, selection[None, :].astype(np.float64), deterministic=deterministic
        )
        return float(quantise(score(totals, mandate, assumptions), assumptions)[0]), totals

    def summarise(generation: int, winner: BoolVector, fitness: Vector) -> GenerationEvent:
        best, totals = exact_fitness(winner)
        blended = float(totals.blended_irr[0])  # type: ignore[attr-defined]
        return GenerationEvent(
            generation=generation,
            total_generations=generations,
            best_fitness=best,
            mean_fitness=float(quantise(np.array([fitness.mean()]), assumptions)[0]),
            best_project_count=int(winner.sum()),
            best_capacity_mw=float(totals.capacity_mw[0]),  # type: ignore[attr-defined]
            best_equity=float(totals.equity[0]),  # type: ignore[attr-defined]
            best_blended_irr=None if np.isnan(blended) else blended,
        )

    events: list[GenerationEvent] = []
    best_selection = population[0].copy()
    best_fitness = -np.inf

    for generation in range(1, generations + 1):
        fitness = quantise(
            score(
                aggregate(features, population.astype(np.float32), deterministic=deterministic),
                mandate,
                assumptions,
            ),
            assumptions,
        )
        ranked = elite_order(fitness)
        leader = int(ranked[0])
        if float(fitness[leader]) > best_fitness:
            best_fitness = float(fitness[leader])
            best_selection = population[leader].copy()

        event = summarise(generation, population[leader], fitness)
        events.append(event)
        yield event

        if generation == generations:
            break

        # --- one generation: five draws, in this order ----------------------
        first_contest = rng.integers(0, population_size, size=(tournament_size, children))
        second_contest = rng.integers(0, population_size, size=(tournament_size, children))
        coin = rng.integers(0, 1, size=(children, candidates), endpoint=True).astype(np.bool_)
        mutation = rng.random((children, candidates))
        priority = rng.random((children, candidates))

        mothers = tournament_select(first_contest, fitness)
        fathers = tournament_select(second_contest, fitness)
        offspring = uniform_crossover(coin, population[mothers], population[fathers])
        offspring = force_locks(mutate(mutation, offspring, assumptions), locked)
        offspring = repair_to_budget(
            offspring,
            priority=priority,
            equity=equity,
            budget=budget,
            locked=locked,
            tolerance=cap_tolerance,
        )

        population = np.vstack([population[ranked[:elites]], offspring])

    # The reported score is recomputed in float64 from the winning chromosome, so
    # nothing downstream inherits the hot path's precision.
    final_fitness, _ = exact_fitness(best_selection)
    return GaOutcome(
        seed=resolved,
        selection=best_selection,
        fitness=final_fitness,
        convergence=tuple(events),
        population_size=population_size,
        generations=generations,
        inclusion_probability=probability,
    )


def run_search(
    features: Features,
    mandate: MandateScalars,
    assumptions: AssumptionSet,
    controls: SearchControls,
) -> GaOutcome:
    """Drain :func:`evolve` and return its outcome, for callers that do not stream."""
    search = evolve(features, mandate, assumptions, controls)
    while True:
        try:
            next(search)
        except StopIteration as finished:
            outcome: GaOutcome = finished.value
            return outcome
