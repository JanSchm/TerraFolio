"""§12's guarantee, and the honest limit of it.

The search's trajectory turns on ``f[a] >= f[b]``. A GEMM's reduction order depends on
BLAS blocking, which depends on the CPU and on the thread count, so one last-bit
difference flips a tournament and diverges every generation after it. 2A quantises
fitness to 6 dp and the runner pins BLAS to one thread, which makes that vanishingly
unlikely — and "vanishingly unlikely" is not the word §12 uses.

``SearchControls.deterministic_reduction`` closes it by not using BLAS at all:
``einsum(optimize=False)`` reduces in an order fixed by the shape and dtype alone. The
golden below was produced with the flag on, which is what makes it meaningful to check
on a machine that is not the one it was recorded on.

What this does **not** prove is that the GEMM path agrees with the einsum path on every
machine — only that the einsum path agrees with itself everywhere. That distinction is
the whole of ``docs/decisions.md`` 4B-4, and overclaiming it in a §12 sign-off is the
kind of lie the first CI migration would expose.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import numpy as np
import pytest
from engine import traced_run
from searches import ASSUMPTIONS, MANDATE, SEED, pool

from terrafolio.domain.enums import Effort
from terrafolio.optimiser import aggregate as aggregate_module
from terrafolio.optimiser.aggregate import aggregate
from terrafolio.optimiser.ga import SearchControls, run_search
from terrafolio.runner.threads import observed_threads

GOLDEN = Path(__file__).parent / "deterministic_reduction_golden.json"


def _controls(effort: Effort, *, deterministic: bool) -> SearchControls:
    return SearchControls(effort=effort, seed=SEED, deterministic_reduction=deterministic)


def _diagnosis() -> str:
    """What a reader needs to tell an intentional dependency bump from a real bug.

    A bare "arrays differ" on this test sends someone hunting through the optimiser.
    Naming the three things that legitimately change the answer turns it into "refresh
    the golden" — which is what §12's audit trail is for.
    """
    return (
        f"numpy {np.__version__}, BLAS threads {observed_threads()},"
        f" deterministic_reduction=True."
        f" If a dependency was bumped on purpose, regenerate {GOLDEN.name}"
        f" and say so in the commit; otherwise this is a real divergence."
    )


def test_the_deterministic_branch_never_reaches_a_gemm() -> None:
    """``optimize=False`` is the whole mechanism, so it is asserted in the source.

    With optimisation on, einsum is entitled to hand a two-operand contraction to
    ``tensordot``, which calls the GEMM this flag exists to avoid. The flag would still
    be *set*, the tests would still pass on one machine, and the guarantee would be
    silently gone — so the keyword is checked rather than trusted.
    """
    tree = ast.parse(Path(aggregate_module.__file__).read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "einsum"
    ]
    assert len(calls) == 1, "expected exactly one einsum in aggregate.py"
    keywords = {keyword.arg: keyword.value for keyword in calls[0].keywords}
    assert "optimize" in keywords, "einsum must pin optimize explicitly"
    assert ast.literal_eval(keywords["optimize"]) is False


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_both_reductions_agree_on_this_machine(dtype: type[np.floating]) -> None:
    """Not a guarantee, a sanity check: the two paths should not differ *here*.

    They are free to differ in the last bits — that is why fitness is quantised — but a
    large difference would mean the einsum contraction is not the same arithmetic.
    """
    rng = np.random.default_rng(7)
    selection = (rng.random((16, pool().project_count)) < 0.1).astype(dtype)

    gemm = aggregate(pool(), selection, deterministic=False)
    free = aggregate(pool(), selection, deterministic=True)

    np.testing.assert_allclose(free.equity, gemm.equity, rtol=1e-6)
    np.testing.assert_allclose(free.capacity_mw, gemm.capacity_mw, rtol=1e-6)
    assert np.array_equal(free.project_count, gemm.project_count)


def test_the_flag_does_not_change_the_draw_sequence() -> None:
    """Switching reduction must not switch the search into a different PRNG stream.

    The flag is meant to remove a source of divergence, so if turning it on moved the
    draws it would be introducing one.
    """
    with_flag = run_search(pool(), MANDATE, ASSUMPTIONS, _controls(Effort.FAST, deterministic=True))
    without = run_search(pool(), MANDATE, ASSUMPTIONS, _controls(Effort.FAST, deterministic=False))

    assert with_flag.seed == without.seed == SEED
    assert with_flag.population_size == without.population_size
    assert with_flag.inclusion_probability == without.inclusion_probability


def test_the_cross_platform_golden_still_holds() -> None:
    """The recorded answer, reproduced without BLAS. §12's regression across releases."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    effort = Effort(golden["effort"])
    outcome = run_search(pool(), MANDATE, ASSUMPTIONS, _controls(effort, deterministic=True))

    assert golden["seed"] == SEED
    assert [int(index) for index in np.flatnonzero(outcome.selection)] == golden["selectedRows"], (
        f"the winning portfolio moved. {_diagnosis()}"
    )
    assert outcome.fitness == golden["fitness"], f"the winning score moved. {_diagnosis()}"
    assert [event.best_fitness for event in outcome.convergence] == golden["bestFitness"], (
        f"the convergence trajectory moved. {_diagnosis()}"
    )


def test_every_reduction_in_a_run_honours_the_flag() -> None:
    """The flag has to reach *all* of them, not just the two inside the search.

    `aggregate` is called from three places: the population-wide hot path, `ga.py`'s
    float64 re-score of the leader, and `build_result`'s aggregation of the winner into
    the twelve §8.1 tiles. The third was missed when the flag landed, so a run asking
    for a BLAS-free reduction got one for its *trajectory* and a GEMM for the numbers it
    reported — reproducible in which projects it picked, not in what it said about them.

    Asserting on every recorded call rather than on the one that was wrong is the point:
    a fourth call site added later is caught by the same test.
    """
    timeline = traced_run(MANDATE, deterministic=True)
    reductions = timeline.of("score")

    assert len(reductions) > 3, "expected the hot path, the re-scores and build_result"
    missed = [event for event in reductions if event.deterministic is not True]
    assert not missed, (
        "these reductions did not receive the run's reduction mode, so their results"
        f" came back through BLAS: {missed}"
    )


def test_the_flag_being_off_is_also_threaded_everywhere() -> None:
    """The counterweight: the assertion above must be reading the argument, not a default."""
    timeline = traced_run(MANDATE, deterministic=False)
    assert all(event.deterministic is False for event in timeline.of("score"))
