"""The search: its operators, its budget repair, and its PRNG consumption contract.

The draw-order test is the load-bearing one. ``ga.py``'s module docstring states the
exact sequence of generator calls, and §10.1 makes "the same mandate, snapshot and
seed produce an identical portfolio" a requirement — which holds only if that sequence
never changes. Here it is asserted rather than described, call by call and shape by
shape, so the docstring cannot quietly stop being true.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from test_optimiser_screens import ASSUMPTIONS, mandate
from test_pipeline_arrays import sample_arrays

from terrafolio.domain.enums import Effort
from terrafolio.optimiser import ga as ga_module
from terrafolio.optimiser.features import COLUMN, build_features
from terrafolio.optimiser.ga import SearchControls, evolve, resolve_seed, run_search
from terrafolio.optimiser.operators import (
    elite_order,
    force_locks,
    inclusion_probability,
    mutate,
    tournament_select,
    uniform_crossover,
)
from terrafolio.optimiser.repair import repair_to_budget

ARRAYS = sample_arrays()
SOURCE = Path(ga_module.__file__).parent


def _features(count: int = 2) -> Any:
    arrays = ARRAYS
    return build_features(
        arrays,
        equity_irr=np.full(arrays.count, 0.13),
        irr_defined=np.ones(arrays.count, dtype=np.bool_),
        merchant_share=1.0 - arrays.revenue.ppa_share,
    ).take(np.arange(count, dtype=np.intp))


# ---------------------------------------------------------------------------
# The PRNG consumption contract
# ---------------------------------------------------------------------------


class CountingGenerator:
    """Records every draw, so the documented order can be asserted."""

    def __init__(self, seed: int) -> None:
        self._inner = np.random.default_rng(seed)
        self.calls: list[tuple[str, tuple[int, ...]]] = []

    def random(self, size: Any = None) -> Any:
        drawn = self._inner.random(size)
        self.calls.append(("random", tuple(np.shape(drawn))))
        return drawn

    def integers(self, low: int, high: int, **kwargs: Any) -> Any:
        drawn = self._inner.integers(low, high, **kwargs)
        self.calls.append(("integers", tuple(np.shape(drawn))))
        return drawn


@pytest.fixture
def counting(monkeypatch: pytest.MonkeyPatch) -> CountingGenerator:
    proxy = CountingGenerator(42)
    monkeypatch.setattr(ga_module.np.random, "default_rng", lambda _seed: proxy)
    return proxy


def test_initialisation_draws_exactly_twice(counting: CountingGenerator) -> None:
    features = _features()
    params = ASSUMPTIONS.ga.effort[Effort.FAST]
    search = evolve(features, mandate(), ASSUMPTIONS, SearchControls(effort=Effort.FAST, seed=42))
    next(search)

    assert counting.calls[:2] == [
        ("random", (params.population, features.project_count)),
        ("random", (params.population, features.project_count)),
    ]


def test_each_generation_draws_five_times_in_the_documented_order(
    counting: CountingGenerator,
) -> None:
    features = _features()
    params = ASSUMPTIONS.ga.effort[Effort.FAST]
    children = params.population - ASSUMPTIONS.ga.elite_count
    tournament = ASSUMPTIONS.ga.tournament_size
    width = features.project_count

    run_search(features, mandate(), ASSUMPTIONS, SearchControls(effort=Effort.FAST, seed=42))

    per_generation = counting.calls[2:]
    # The final generation is scored and yielded but breeds nothing.
    assert len(per_generation) == 5 * (params.generations - 1)

    expected = [
        ("integers", (tournament, children)),
        ("integers", (tournament, children)),
        ("integers", (children, width)),
        ("random", (children, width)),
        ("random", (children, width)),
    ]
    for start in range(0, len(per_generation), 5):
        assert per_generation[start : start + 5] == expected


def test_the_draw_count_does_not_depend_on_the_population(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A per-chromosome loop would make the call count scale with the population."""
    counts: dict[Effort, int] = {}
    for effort in (Effort.FAST, Effort.STANDARD):
        proxy = CountingGenerator(42)
        monkeypatch.setattr(ga_module.np.random, "default_rng", lambda _s, p=proxy: p)
        run_search(_features(), mandate(), ASSUMPTIONS, SearchControls(effort=effort, seed=42))
        params = ASSUMPTIONS.ga.effort[effort]
        counts[effort] = len(proxy.calls) - 5 * (params.generations - 1) - 2
    assert set(counts.values()) == {0}


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_the_same_seed_produces_an_identical_result() -> None:
    features = _features()
    controls = SearchControls(effort=Effort.FAST, seed=42)
    first = run_search(features, mandate(), ASSUMPTIONS, controls)
    second = run_search(features, mandate(), ASSUMPTIONS, controls)

    assert np.array_equal(first.selection, second.selection)
    assert first.fitness == second.fitness
    assert [e.best_fitness for e in first.convergence] == [
        e.best_fitness for e in second.convergence
    ]


def test_a_different_seed_explores_differently() -> None:
    features = _features()
    first = run_search(features, mandate(), ASSUMPTIONS, SearchControls(effort=Effort.FAST, seed=1))
    second = run_search(
        features, mandate(), ASSUMPTIONS, SearchControls(effort=Effort.FAST, seed=2)
    )
    assert [e.mean_fitness for e in first.convergence] != [
        e.mean_fitness for e in second.convergence
    ]


def test_an_omitted_seed_is_resolved_and_returned() -> None:
    """A run with no recorded seed is not reproducible and therefore not a valid run."""
    features = _features()
    outcome = run_search(features, mandate(), ASSUMPTIONS, SearchControls(effort=Effort.FAST))
    assert isinstance(outcome.seed, int)

    repeated = run_search(
        features, mandate(), ASSUMPTIONS, SearchControls(effort=Effort.FAST, seed=outcome.seed)
    )
    assert np.array_equal(repeated.selection, outcome.selection)


def test_two_unseeded_runs_do_not_share_a_seed() -> None:
    assert resolve_seed(None) != resolve_seed(None)
    assert resolve_seed(7) == 7


# ---------------------------------------------------------------------------
# What the search must never return
# ---------------------------------------------------------------------------


def test_the_winner_always_fits_its_budget() -> None:
    features = _features()
    limit = mandate(available_capital_eur=60e6)
    outcome = run_search(features, limit, ASSUMPTIONS, SearchControls(effort=Effort.FAST, seed=42))
    equity = float(features.fit[outcome.selection, COLUMN["equity"]].sum())
    assert equity <= limit.available_capital_eur


def test_a_locked_project_is_held_in_every_generation() -> None:
    features = _features()
    locked = np.array([True, False])
    outcome = run_search(
        features,
        mandate(),
        ASSUMPTIONS,
        SearchControls(effort=Effort.FAST, locked=locked, seed=42),
    )
    assert outcome.selection[0]


def test_the_best_fitness_never_goes_backwards() -> None:
    """What elitism buys: the top two chromosomes carry forward unchanged."""
    outcome = run_search(
        _features(), mandate(), ASSUMPTIONS, SearchControls(effort=Effort.FAST, seed=42)
    )
    best = [event.best_fitness for event in outcome.convergence]
    assert best == sorted(best)


def test_an_odd_population_is_refused_rather_than_silently_trimmed() -> None:
    odd = dataclasses.replace(
        ASSUMPTIONS,
        ga=dataclasses.replace(
            ASSUMPTIONS.ga,
            effort={
                **ASSUMPTIONS.ga.effort,
                Effort.FAST: dataclasses.replace(ASSUMPTIONS.ga.effort[Effort.FAST], population=51),
            },
        ),
    )
    with pytest.raises(ValueError, match="odd population"):
        run_search(_features(), mandate(), odd, SearchControls(effort=Effort.FAST, seed=42))


def test_every_generation_is_reported_once() -> None:
    params = ASSUMPTIONS.ga.effort[Effort.FAST]
    outcome = run_search(
        _features(), mandate(), ASSUMPTIONS, SearchControls(effort=Effort.FAST, seed=42)
    )
    assert len(outcome.convergence) == params.generations
    assert [e.generation for e in outcome.convergence] == list(range(1, params.generations + 1))
    assert all(e.total_generations == params.generations for e in outcome.convergence)


def test_a_streamed_run_and_a_drained_one_agree() -> None:
    """3A drives the generator; the CLI drains it. They must not diverge."""
    features = _features()
    controls = SearchControls(effort=Effort.FAST, seed=42)
    search = evolve(features, mandate(), ASSUMPTIONS, controls)
    streamed = []
    while True:
        try:
            streamed.append(next(search))
        except StopIteration as finished:
            outcome = finished.value
            break
    drained = run_search(features, mandate(), ASSUMPTIONS, controls)
    assert np.array_equal(outcome.selection, drained.selection)
    assert [e.best_fitness for e in streamed] == [e.best_fitness for e in drained.convergence]


# ---------------------------------------------------------------------------
# Capital-aware initialisation (epic §6.1)
# ---------------------------------------------------------------------------


def test_inclusion_scales_down_when_the_budget_is_the_binding_constraint() -> None:
    probability = inclusion_probability(
        capacity_target_mw=10_000.0,
        available_capital_eur=100e6,
        total_capacity_mw=20_000.0,
        total_equity_eur=4_000e6,
        assumptions=ASSUMPTIONS,
    )
    assert probability == pytest.approx(100e6 / 4_000e6)


def test_inclusion_scales_down_when_capacity_is_the_binding_constraint() -> None:
    probability = inclusion_probability(
        capacity_target_mw=1_000.0,
        available_capital_eur=4_000e6,
        total_capacity_mw=20_000.0,
        total_equity_eur=4_000e6,
        assumptions=ASSUMPTIONS,
    )
    assert probability == pytest.approx(1_000.0 / 20_000.0)


def test_the_spec_probability_survives_as_a_ceiling() -> None:
    """§10.1's flat 0.35 is not deleted — it is the upper bound."""
    probability = inclusion_probability(
        capacity_target_mw=1e9,
        available_capital_eur=1e15,
        total_capacity_mw=1.0,
        total_equity_eur=1.0,
        assumptions=ASSUMPTIONS,
    )
    assert probability == ASSUMPTIONS.ga.inclusion_ceiling


def test_the_floor_keeps_the_first_generation_from_being_empty() -> None:
    probability = inclusion_probability(
        capacity_target_mw=1.0,
        available_capital_eur=1.0,
        total_capacity_mw=1e9,
        total_equity_eur=1e15,
        assumptions=ASSUMPTIONS,
    )
    assert probability == ASSUMPTIONS.ga.inclusion_floor


# ---------------------------------------------------------------------------
# Budget repair
# ---------------------------------------------------------------------------


def _naive_repair(
    population: np.ndarray, priority: np.ndarray, equity: np.ndarray, budget: float
) -> np.ndarray:
    """The row-by-row implementation the vectorised one has to match.

    Note the **prefix** rule: holdings are taken in priority order until the running
    total no longer fits, and everything after that is dropped — including anything
    cheap enough to have squeezed in. Skipping the expensive one and carrying on would
    be a knapsack heuristic, and a better one; it is also not what epic §6.1 specifies,
    and a repair that quietly optimises would bias the search towards cheap projects
    in a way no weight in the assumption set asked for.

    About 100x slower than the vectorised form, which is why it is only ever an oracle.
    """
    out = np.zeros_like(population)
    for row in range(population.shape[0]):
        held = [index for index in np.argsort(priority[row]) if population[row, index]]
        spent = 0.0
        for index in held:
            spent += equity[index]
            if spent > budget:
                break
            out[row, index] = True
    return out


def test_the_vectorised_repair_matches_the_row_by_row_one() -> None:
    rng = np.random.default_rng(7)
    population = rng.random((40, 12)) < 0.6
    priority = rng.random((40, 12))
    equity = rng.random(12) * 100.0
    budget = 250.0

    assert np.array_equal(
        repair_to_budget(population, priority=priority, equity=equity, budget=budget),
        _naive_repair(population, priority, equity, budget),
    )


def test_repair_never_leaves_a_row_over_budget() -> None:
    rng = np.random.default_rng(11)
    population = np.ones((5, 8), dtype=np.bool_)
    priority = rng.random((5, 8))
    equity = np.full(8, 50.0)
    repaired = repair_to_budget(population, priority=priority, equity=equity, budget=120.0)
    assert ((repaired * equity).sum(axis=-1) <= 120.0).all()


def test_repair_never_adds_a_holding() -> None:
    rng = np.random.default_rng(13)
    population = rng.random((6, 10)) < 0.5
    repaired = repair_to_budget(
        population, priority=rng.random((6, 10)), equity=np.full(10, 1.0), budget=1e9
    )
    assert np.array_equal(repaired, population)


def test_repair_spends_the_budget_on_locked_holdings_first() -> None:
    population = np.ones((1, 3), dtype=np.bool_)
    equity = np.array([100.0, 100.0, 100.0])
    locked = np.array([False, False, True])
    # A priority order that would otherwise drop the locked project last.
    priority = np.array([[0.1, 0.2, 0.9]])
    repaired = repair_to_budget(
        population, priority=priority, equity=equity, budget=150.0, locked=locked
    )
    assert repaired[0, 2]
    assert repaired.sum() == 1


def test_repair_holds_no_python_loop() -> None:
    """The row-by-row form timed out a 120 s benchmark this one finishes in 2.5 s."""
    source = (SOURCE / "repair.py").read_text(encoding="utf-8")
    loops = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.For | ast.While | ast.comprehension)
    ]
    assert loops == []


def test_the_only_loop_in_the_operators_runs_over_the_tournament() -> None:
    """Two contestants, not ninety chromosomes."""
    source = (SOURCE / "operators.py").read_text(encoding="utf-8")
    loops = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.For)]
    assert len(loops) == 1
    assert ast.unparse(loops[0].iter) == "draws[1:]"


# ---------------------------------------------------------------------------
# The operators, one at a time
# ---------------------------------------------------------------------------


def test_a_tie_goes_to_the_later_draw() -> None:
    """The reference's ``a.f > b.f ? a : b``, reproduced."""
    fitness = np.array([1.0, 1.0, 0.0])
    draws = np.array([[0], [1]])
    assert tournament_select(draws, fitness).tolist() == [1]


def test_the_tournament_prefers_the_fitter_contestant() -> None:
    fitness = np.array([0.0, 5.0])
    assert tournament_select(np.array([[0], [1]]), fitness).tolist() == [1]
    assert tournament_select(np.array([[1], [0]]), fitness).tolist() == [1]


def test_elite_order_is_stable_on_ties() -> None:
    order = elite_order(np.array([1.0, 1.0, 1.0, 2.0]))
    assert order.tolist() == [3, 0, 1, 2]


def test_crossover_takes_each_gene_from_the_chosen_parent() -> None:
    first = np.array([[True, True, False]])
    second = np.array([[False, False, True]])
    coin = np.array([[True, False, True]])
    assert uniform_crossover(coin, first, second).tolist() == [[True, False, False]]


def test_mutation_flips_rather_than_sets() -> None:
    """A set-to-one mutation would drift every chromosome towards holding everything."""
    children = np.array([[True, False]])
    always = np.zeros((1, 2))
    assert mutate(always, children, ASSUMPTIONS).tolist() == [[False, True]]


def test_forcing_locks_leaves_an_unlocked_population_alone() -> None:
    population = np.array([[True, False]])
    assert force_locks(population, None) is population
    assert force_locks(population, np.array([False, False])) is population
