"""Benchmark the search, and the budget repair operator inside it.

Every performance number in #12's pull request comes from here, so that the
before/after table, the wall-clock budgets and the claims in ``docs/decisions.md``
cannot quietly be measured three different ways.

Two things it exists to answer:

**Where does an Exhaustive run's time go?** ``--profile`` prints the hot spots. Epic §7
records a 30 s gap at 2,000 candidates and attributes it to the repair operator's
argsort running over the whole population every generation.

**Does masking the repair help, and which masking?** ``--compare-repair`` times four
variants of the same operator against each other:

``legacy``
    2A's shipped implementation — full argsort, gather, cumsum and scatter, every row,
    full candidate width. Imported from ``tests/regression/legacy_repair.py`` so the
    timing baseline and the correctness oracle are the same code.
``rows``
    #12's row mask alone: skip the rows already inside their budget.
``slice``
    #12's column slice alone: rank every row, but gather and accumulate only over the
    widest held count, because no row holds anything beyond it.
``both``
    What ships.

The ``identical`` column is the point of the table: a variant that is faster and
answers differently is not an optimisation.

Run from the repository root::

    uv run python tools/bench_ga.py --candidates 500,2000 --compare-repair

Timings are only meaningful next to the machine state they were taken on, which every
table here prints — ``docs/decisions.md`` is emphatic that a number taken under load is
worthless.
"""

from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
# tests/ holds the candidate-pool builder, the reference mandate and the pre-#12
# operator. Importing them rather than copying them is what keeps this tool measuring
# the same code the test suite checks.
sys.path[:0] = [
    str(_ROOT / "tests" / "perf"),
    str(_ROOT / "tests" / "regression"),
    str(_ROOT / "tests" / "golden"),
]

from legacy_repair import legacy_repair_to_budget  # noqa: E402
from pool import build_pool, describe_environment, load_shipped  # noqa: E402
from reference_mandates import REFERENCE_MANDATES  # noqa: E402

import terrafolio.optimiser.ga as ga_module  # noqa: E402
from terrafolio.config.assumptions import AssumptionSet  # noqa: E402
from terrafolio.domain.enums import Effort  # noqa: E402
from terrafolio.domain.scalars import MandateScalars  # noqa: E402
from terrafolio.optimiser.features import COLUMN, Features  # noqa: E402
from terrafolio.optimiser.ga import SearchControls, evolve, run_search  # noqa: E402
from terrafolio.optimiser.repair import repair_to_budget  # noqa: E402
from terrafolio.pipeline.arrays import BoolVector, Matrix, Vector  # noqa: E402

RepairFn = Callable[..., BoolVector]

EFFORTS = {effort.value: effort for effort in Effort}
ORDER = list(Effort)


# --- the four repair variants ------------------------------------------------


def _rows_only(  # noqa: PLR0913 - a drop-in for `repair_to_budget`, so the signature matches
    population: BoolVector,
    *,
    priority: Matrix,
    equity: Vector,
    budget: float,
    locked: BoolVector | None = None,
    tolerance: float = 1.0,
) -> BoolVector:
    """The row mask wrapped around the legacy body, to price the mask on its own."""
    rows = np.flatnonzero(np.where(population, equity, 0.0).sum(axis=-1) > budget - tolerance)
    repaired = population.copy()
    repaired[rows] = legacy_repair_to_budget(
        population[rows], priority=priority[rows], equity=equity, budget=budget, locked=locked
    )
    return repaired


def _legacy(population: BoolVector, **kwargs: object) -> BoolVector:
    """The pre-#12 operator, which never had a ``tolerance`` to be passed.

    ``ga.evolve`` passes one at both call sites now, so the keyword is dropped here
    rather than added to the oracle — which has to stay a frozen copy to be worth
    comparing against.
    """
    kwargs.pop("tolerance", None)
    return legacy_repair_to_budget(population, **kwargs)  # type: ignore[arg-type]


def _slice_only(population: BoolVector, **kwargs: object) -> BoolVector:
    """The shipped operator with the row mask switched off."""
    kwargs["tolerance"] = None
    return repair_to_budget(population, **kwargs)  # type: ignore[arg-type]


def _variants(assumptions: AssumptionSet) -> dict[str, RepairFn]:
    tolerance = assumptions.objective.equity_cap_tolerance_eur

    def both(population: BoolVector, **kwargs: object) -> BoolVector:
        """The shipped operator exactly as ``ga.py`` calls it."""
        kwargs["tolerance"] = tolerance
        return repair_to_budget(population, **kwargs)  # type: ignore[arg-type]

    return {
        "legacy": _legacy,
        "rows": _rows_only,
        "slice": _slice_only,
        "both": both,
    }


# --- measurement -------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Case:
    """One (pool width, mandate, calibration) configuration to measure."""

    pool: Features
    mandate: MandateScalars
    assumptions: AssumptionSet

    @property
    def candidates(self) -> int:
        return self.pool.project_count


@dataclass(frozen=True, slots=True, kw_only=True)
class Timing:
    seconds: float
    fitness: float
    held: int
    selection: tuple[bool, ...]
    max_gap_ms: float


def _time_run(case: Case, effort: Effort, *, seed: int, repeats: int) -> Timing:
    """Run the search ``repeats`` times and keep the fastest, with its gap profile."""
    best: Timing | None = None
    for _ in range(repeats):
        gaps: list[float] = []
        started = last = time.perf_counter()
        search = evolve(
            case.pool, case.mandate, case.assumptions, SearchControls(effort=effort, seed=seed)
        )
        while True:
            try:
                next(search)
            except StopIteration as finished:
                outcome = finished.value
                break
            now = time.perf_counter()
            gaps.append((now - last) * 1000)
            last = now
        timing = Timing(
            seconds=time.perf_counter() - started,
            fitness=outcome.fitness,
            held=int(outcome.selection.sum()),
            selection=tuple(bool(flag) for flag in outcome.selection),
            max_gap_ms=max(gaps),
        )
        if best is None or timing.seconds < best.seconds:
            best = timing
    if best is None:
        raise ValueError("--repeats must be at least 1")
    return best


def _over_budget_census(case: Case, effort: Effort, *, seed: int) -> tuple[int, int]:
    """How many rows each repair call actually had to change. Epic §7's premise.

    If this is close to 100%, masking by row cannot save anything, whatever the
    profile says about where the time goes.
    """
    equity = case.pool.fit[:, COLUMN["equity"]]
    budget = case.mandate.available_capital_eur
    seen = [0, 0]

    def counting(population: BoolVector, **kwargs: object) -> BoolVector:
        totals = np.where(population, equity, 0.0).sum(axis=-1)
        seen[0] += int((totals > budget - 1.0).sum())
        seen[1] += int(population.shape[0])
        return repair_to_budget(population, **kwargs)  # type: ignore[arg-type]

    controls = SearchControls(effort=effort, seed=seed)
    with _patched_repair(counting):
        run_search(case.pool, case.mandate, case.assumptions, controls)
    return seen[0], seen[1]


class _patched_repair:  # noqa: N801 - a context manager, used as a statement
    """Swap the operator ``ga.evolve`` calls, and always put it back."""

    def __init__(self, replacement: RepairFn) -> None:
        self._replacement = replacement
        self._original = ga_module.repair_to_budget

    def __enter__(self) -> None:
        ga_module.repair_to_budget = self._replacement  # type: ignore[assignment]

    def __exit__(self, *_: object) -> None:
        ga_module.repair_to_budget = self._original  # type: ignore[assignment]


def _cases(widths: Sequence[int]) -> list[Case]:
    loaded, assumptions = load_shipped()
    mandate: MandateScalars = REFERENCE_MANDATES["M0-default"]
    print(f"\npipeline: {loaded.loaded_count} files loaded in {loaded.duration_ms} ms")
    print(f"environment: {describe_environment()}\n")
    return [
        Case(
            pool=build_pool(loaded, mandate, assumptions, candidates=width),
            mandate=mandate,
            assumptions=assumptions,
        )
        for width in widths
    ]


# --- reports -----------------------------------------------------------------


def _report_runs(widths: Sequence[int], efforts: Sequence[Effort], seed: int, repeats: int) -> None:
    cases = _cases(widths)
    print("| candidates | effort | wall clock | max gap | best fitness | held | over budget |")
    print("|---:|---|---:|---:|---:|---:|---:|")
    for case in cases:
        for effort in efforts:
            timing = _time_run(case, effort, seed=seed, repeats=repeats)
            over, total = _over_budget_census(case, effort, seed=seed)
            print(
                f"| {case.candidates:,} | {effort.value} | {timing.seconds:.2f} s |"
                f" {timing.max_gap_ms:.1f} ms | {timing.fitness:.3f} | {timing.held} |"
                f" {over:,}/{total:,} ({over / total:.1%}) |"
            )


def _report_repair(
    widths: Sequence[int], efforts: Sequence[Effort], seed: int, repeats: int
) -> None:
    cases = _cases(widths)
    for case in cases:
        variants = _variants(case.assumptions)
        if case is cases[0]:
            print("| candidates | effort | " + " | ".join(variants) + " | speed-up | identical |")
            print("|---:|---|" + "---:|" * (len(variants) + 1) + "---|")
        for effort in efforts:
            results: dict[str, Timing] = {}
            for name, variant in variants.items():
                with _patched_repair(variant):
                    results[name] = _time_run(case, effort, seed=seed, repeats=repeats)
            answers = {timing.selection for timing in results.values()}
            cells = " | ".join(f"{results[name].seconds:.3f} s" for name in variants)
            speedup = results["legacy"].seconds / results["both"].seconds
            print(
                f"| {case.candidates:,} | {effort.value} | {cells} | {speedup:.2f}x |"
                f" {'yes' if len(answers) == 1 else 'NO'} |"
            )


def _report_operator(widths: Sequence[int], efforts: Sequence[Effort], repeats: int) -> None:
    """Time the four variants on the operator alone, away from the rest of the search.

    The whole-run table is the number that matters, and on a shared machine it is also
    the number that moves: a search is a fifth of a second and the load average is not
    a constant. This times only ``repair_to_budget``, on populations recorded from a
    real run — so it is stable to a few percent under load and it isolates the change.

    The populations are drawn to be over budget at the rate a real late generation is,
    which the census in ``--compare-repair`` measures at 95-99%. Drawing them uniformly
    instead would flatter the row mask by inventing rows it could skip.
    """
    loaded, assumptions = load_shipped()
    mandate: MandateScalars = REFERENCE_MANDATES["M0-default"]
    variants = _variants(assumptions)
    budget = mandate.available_capital_eur
    print(f"\nenvironment: {describe_environment()}\n")
    print("| candidates | rows | over budget | " + " | ".join(variants) + " | speed-up |")
    print("|---:|---:|---:|" + "---:|" * (len(variants) + 1))
    for width in widths:
        pool = build_pool(loaded, mandate, assumptions, candidates=width)
        equity = pool.fit[:, COLUMN["equity"]]
        for effort in efforts:
            rows = assumptions.ga.effort[effort].population - assumptions.ga.elite_count
            rng = np.random.default_rng(17)
            # Chromosomes just over their budget, which is where the search spends
            # almost all of its generations.
            population = rng.random((rows, width)) < (budget / float(equity.sum())) * 1.15
            priority = rng.random((rows, width))
            over = int((np.where(population, equity, 0.0).sum(axis=-1) > budget).sum())
            timings: dict[str, float] = {}
            for name, variant in variants.items():
                best = float("inf")
                for _ in range(repeats):
                    started = time.perf_counter()
                    variant(population, priority=priority, equity=equity, budget=budget)
                    best = min(best, time.perf_counter() - started)
                timings[name] = best * 1000
            cells = " | ".join(f"{timings[name]:.2f} ms" for name in variants)
            print(
                f"| {width:,} | {rows} | {over}/{rows} | {cells} |"
                f" {timings['legacy'] / timings['both']:.2f}x |"
            )


def _report_profile(width: int, effort: Effort, seed: int, rows: int) -> None:
    case = _cases([width])[0]
    profiler = cProfile.Profile()
    profiler.enable()
    run_search(case.pool, case.mandate, case.assumptions, SearchControls(effort=effort, seed=seed))
    profiler.disable()
    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).sort_stats("tottime").print_stats(rows)
    print(f"profile: {effort.value} at {width:,} candidates")
    print(stream.getvalue())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark the search and its repair operator.")
    parser.add_argument("--candidates", default="500,2000", help="comma-separated pool widths")
    parser.add_argument(
        "--efforts", default="fast,standard,exhaustive", help="comma-separated effort names"
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--repeats", type=int, default=3, help="runs per cell; the fastest is reported"
    )
    parser.add_argument("--compare-repair", action="store_true", help="the before/after table")
    parser.add_argument(
        "--operator", action="store_true", help="time the repair operator on its own"
    )
    parser.add_argument("--profile", action="store_true", help="hot spots for one configuration")
    parser.add_argument("--profile-rows", type=int, default=14)
    args = parser.parse_args(argv)

    widths = sorted(int(value) for value in args.candidates.split(","))
    efforts = sorted((EFFORTS[name] for name in args.efforts.split(",")), key=ORDER.index)

    if args.profile:
        _report_profile(widths[-1], efforts[-1], args.seed, args.profile_rows)
    if args.operator:
        _report_operator(widths, efforts, max(args.repeats, 20))
    if args.compare_repair:
        _report_repair(widths, efforts, args.seed, args.repeats)
    if not args.compare_repair and not args.profile and not args.operator:
        _report_runs(widths, efforts, args.seed, args.repeats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
