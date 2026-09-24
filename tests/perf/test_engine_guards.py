"""The guards that catch the regressions that matter, without measuring the machine.

Wall-clock assertions are noise in CI: they fail on a loaded runner, get marked flaky,
get skipped, and then measure nothing. These do not measure time at all. They count
calls and check array shapes, which is where the expensive mistakes actually show up —
and each of them fails loudly the moment someone reintroduces the thing it forbids.
"""

from __future__ import annotations

import numpy as np
from engine import NPV_CALLS_PER_SOLVE, Timeline

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.domain.scalars import MandateScalars


def test_the_irr_solver_runs_twice_and_never_inside_the_search(timeline: Timeline) -> None:
    """§10.3: "full IRR solving inside the fitness loop is ... not acceptable".

    #12 asks for ``n_eligible + 1`` calls — one per eligible project plus the portfolio.
    2A vectorised the per-project half instead, so the real count is **two**: one
    bisection over an ``(n, hold)`` matrix covering every loaded project at once, and
    one over the portfolio's own ``(1, hold)`` cash flow. That is strictly better than
    what the criterion describes and satisfies what §10.3 forbids, which is IRR solving
    *inside the loop* — so that is what is asserted, and the arithmetic is raised on
    the issue rather than faked here (docs/decisions.md 4B-7).

    The ordering assertion is the one that would catch the regression. A per-project
    solve moved inside the fitness loop would still be "a few" calls by some countings;
    it would not be outside the scoring window.
    """
    solves = timeline.irr_solves
    assert len(timeline.of("npv")) == 2 * NPV_CALLS_PER_SOLVE, (
        f"expected exactly two bisections, saw {len(timeline.of('npv'))} NPV evaluations"
    )
    assert len(solves) == 2, f"expected one per-project solve and one portfolio solve, got {solves}"

    per_project, portfolio = solves
    assert per_project.shape[0] > 1, "the per-project solve must be vectorised over the pipeline"
    assert portfolio.shape[0] == 1, "the portfolio solve is one aggregated cash flow"
    assert per_project.shape[1] == portfolio.shape[1], "both solve over the same hold window"

    first_score = timeline.index_of_first("score")
    last_score = timeline.index_of_last("score")
    inside = [
        index
        for index, event in enumerate(timeline.events)
        if event.kind == "npv" and first_score < index < last_score
    ]
    assert not inside, f"an IRR solve happened inside the scoring window, at {inside}"


def test_the_population_is_scored_once_per_generation(timeline: Timeline) -> None:
    """#12 asks for one fitness call per generation. There are two kinds of call.

    ``ga.py`` scores the whole population once per generation in float32 — that is the
    hot path, and it is the one whose count must not grow. It then re-scores the
    *leader alone* in float64 so that nothing reported inherits the hot path's
    precision, once per generation plus once for the winner, and ``build_result``
    aggregates the winner once more.

    Both counts are pinned. Collapsing them into a single number would let a
    population-scale call hide behind a one-row one.
    """
    scores = timeline.of("score")
    population_scale = [event for event in scores if event.shape[0] == timeline.population_size]
    single_row = [event for event in scores if event.shape[0] == 1]

    assert len(population_scale) == timeline.generations, (
        f"expected one population scoring per generation, got {len(population_scale)}"
        f" for {timeline.generations} generations"
    )
    assert len(single_row) == timeline.generations + 2, (
        "expected one float64 re-score of the leader per generation, one for the winner"
        f" and one in build_result, got {len(single_row)}"
    )
    assert len(scores) == len(population_scale) + len(single_row), "an unexplained scoring shape"


def test_the_hot_path_is_float32_and_everything_reported_is_float64(
    timeline: Timeline,
) -> None:
    """Epic §5: float32 only inside the GA fitness hot path.

    The invariant is cheap to state and easy to lose — one ``astype`` moved by a line
    and either the hot path loses its 80x or a reported metric inherits its precision.
    """
    for event in timeline.of("score"):
        expected = "float32" if event.shape[0] == timeline.population_size else "float64"
        assert event.dtype == expected, (
            f"a {event.shape} scoring ran in {event.dtype}, expected {expected}"
        )


def test_no_operator_ever_sees_one_chromosome_at_a_time(
    timeline: Timeline, assumptions: AssumptionSet
) -> None:
    """What "no Python loop over the population" reduces to, as a shape assertion.

    A loop over the population would show up here as a stream of calls with a leading
    dimension of one, or as many more calls than there are generations. Both are
    checked: every operator sees the whole population, or the whole child half, exactly
    once per generation.
    """
    population = timeline.population_size
    # Read from the calibration, not restated. A hardcoded 2 here made the check below
    # (`children == population_size - 2`) a tautology that could never fire, and an
    # `elite_count` change would have surfaced as a confusing shape mismatch instead.
    children = population - assumptions.ga.elite_count

    boundaries = {
        "initial_population": (population,),
        "elite_order": (population,),
        "crossover": (children,),
        "mutate": (children,),
        "force_locks": (children,),
        "repair": (population, children),
    }
    for kind, allowed in boundaries.items():
        events = timeline.of(kind)
        assert events, f"nothing recorded at the {kind} boundary"
        for event in events:
            assert event.shape[0] in allowed, (
                f"{kind} saw {event.shape[0]} rows, expected one of {allowed}"
            )

    assert len(timeline.of("elite_order")) == timeline.generations
    assert len(timeline.of("crossover")) == timeline.generations - 1
    assert len(timeline.of("repair")) == timeline.generations, (
        "one repair at initialisation and one per breeding generation"
    )
    assert 0 < children < population, (
        f"an elite count of {assumptions.ga.elite_count} leaves {children} children,"
        " which is not a breeding population"
    )


def test_every_operator_call_spans_the_whole_candidate_width(timeline: Timeline) -> None:
    """The other axis: an operator narrowed to a subset of candidates would break the
    PRNG contract, because the draws are shaped ``(rows, candidates)`` by position."""
    for kind in ("initial_population", "crossover", "mutate", "repair"):
        for event in timeline.of(kind):
            assert event.shape[1] == timeline.candidates, (
                f"{kind} saw {event.shape[1]} candidates, expected {timeline.candidates}"
            )


def test_the_run_actually_did_something(timeline: Timeline, mandate: MandateScalars) -> None:
    """A guard on the guards: every assertion above is vacuous over an empty run."""
    assert timeline.eligible > 1, "the traced mandate must admit a real pool"
    assert timeline.generations > 1
    assert timeline.result is not None
    assert timeline.result.selected_ids, "the search must hold something"
    equity = timeline.result.totals.equity
    assert 0.0 < equity <= mandate.available_capital_eur + 1.0, (
        f"the winner draws {equity:,.0f} against a {mandate.available_capital_eur:,.0f} budget"
    )
    assert np.isfinite(timeline.result.cashflow_30y).all()
