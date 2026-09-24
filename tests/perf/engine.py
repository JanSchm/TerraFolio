"""One whole run, in process, with every interesting call recorded as it happens.

The guards in this package are about *how* the engine computes, not how fast: how many
times it solves an IRR, how many times it scores a population, what shape the arrays
crossing each operator boundary have. All of those are properties of one run, so the
run happens once per test and the timeline it produced is what gets asserted.

Recording a timeline rather than a set of counters is deliberate. "``irr`` was called
twice" and "``irr`` was never called between the first and last scoring" are different
claims, and only the second one is what §10.3 actually forbids.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from typing import Any, Final

import numpy as np
from pool import GOLDEN_PIPELINE

import terrafolio.economics.irr as irr_module
import terrafolio.optimiser.ga as ga_module
import terrafolio.optimiser.result as result_module
from terrafolio.config.loader import load_default
from terrafolio.domain.enums import Effort
from terrafolio.domain.scalars import MandateScalars
from terrafolio.economics.irr import IRR_BISECTION_ITERATIONS
from terrafolio.economics.returns import contracted_revenue_share, project_returns
from terrafolio.optimiser.feasibility import preview_feasibility
from terrafolio.optimiser.features import build_features
from terrafolio.optimiser.ga import SearchControls, run_search
from terrafolio.optimiser.result import RunResult, SelectionOutcome, build_result
from terrafolio.pipeline.loader import load_pipeline

__all__ = ["NPV_CALLS_PER_SOLVE", "Event", "Timeline", "traced_run"]

SEED: Final = 2024


@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    """One recorded call: what it was, what shape it saw, at what precision."""

    kind: str
    shape: tuple[int, ...]
    dtype: str = ""

    deterministic: bool | None = None
    """The reduction mode the call asked for, where the call takes one.

    ``None`` means the argument was absent, which is how a call site that forgot to
    thread it shows up — see ``tests/regression/test_deterministic_reduction.py``.
    """


@dataclass(slots=True)
class Timeline:
    """Every recorded call from one run, in the order it happened."""

    events: list[Event] = field(default_factory=list)
    population_size: int = 0
    generations: int = 0
    candidates: int = 0
    eligible: int = 0
    result: RunResult | None = None

    def of(self, kind: str) -> list[Event]:
        return [event for event in self.events if event.kind == kind]

    @property
    def irr_solves(self) -> list[Event]:
        """One event per IRR solve, recovered from the NPV calls it made.

        Every solve makes exactly ``NPV_CALLS_PER_SOLVE`` calls at one shape, so the
        first of each block identifies the solve.
        """
        calls = self.of("npv")
        return calls[::NPV_CALLS_PER_SOLVE]

    def index_of_first(self, kind: str) -> int:
        return next(index for index, event in enumerate(self.events) if event.kind == kind)

    def index_of_last(self, kind: str) -> int:
        return max(index for index, event in enumerate(self.events) if event.kind == kind)


@contextmanager
def _recorded(
    module: Any, name: str, kind: str, timeline: Timeline, *, probe: int = 0
) -> Iterator[None]:
    """Wrap ``module.name`` so every call appends an :class:`Event`.

    ``probe`` selects which array argument to record the shape and dtype of — the
    population for a scoring, the cash-flow matrix for an NPV, the incoming chromosomes
    for an operator.
    """
    original = getattr(module, name)

    def recording(*args: Any, **kwargs: Any) -> Any:
        arrays = [value for value in (*args, *kwargs.values()) if isinstance(value, np.ndarray)]
        seen = arrays[probe] if len(arrays) > probe else None
        timeline.events.append(
            Event(
                kind=kind,
                shape=tuple(int(size) for size in seen.shape) if seen is not None else (),
                dtype=str(seen.dtype) if seen is not None else "",
                deterministic=kwargs.get("deterministic"),
            )
        )
        return original(*args, **kwargs)

    setattr(module, name, recording)
    try:
        yield
    finally:
        setattr(module, name, original)


# Every call worth counting, and where the name it is called by actually lives. These
# are the *bound* references — `ga.py` imports `aggregate` by name, so patching
# `aggregate.aggregate` would record nothing.
#
# The IRR solve is hooked at `npv` rather than at `irr`, and that is the difference
# between a guard and a decoration. `irr` is imported by name into two modules, so
# patching those two bindings catches only the two call sites that exist today: an IRR
# solve added inside the fitness loop through a fresh `from ... import irr` in `ga.py`
# would be invisible, which is exactly the regression §10.3 forbids. `npv` is reached
# only from inside `irr`, which resolves it from its own module globals at call time,
# so one hook catches every solve however `irr` was imported. Verified by adding such a
# call to the loop on purpose and watching the guard fail.
TRACED: Final = (
    (ga_module, "aggregate", "score", 0),
    (result_module, "aggregate", "score", 0),
    (irr_module, "npv", "npv", 1),
    (ga_module, "initial_population", "initial_population", 0),
    (ga_module, "tournament_select", "tournament_select", 0),
    (ga_module, "uniform_crossover", "crossover", 0),
    (ga_module, "mutate", "mutate", 0),
    (ga_module, "force_locks", "force_locks", 0),
    (ga_module, "repair_to_budget", "repair", 0),
    (ga_module, "elite_order", "elite_order", 0),
)

NPV_CALLS_PER_SOLVE: Final = IRR_BISECTION_ITERATIONS + 2
"""Two to bracket, then one per bisection step. Read from the engine, not written out,
so a change to the iteration count reads as a change and not as a failure."""


def traced_run(
    mandate: MandateScalars,
    *,
    effort: Effort = Effort.FAST,
    pipeline: Any = None,
    deterministic: bool = False,
) -> Timeline:
    """Run the whole in-process path once, recording every traced call.

    The order is ``cli.py``'s: load, screen, solve per-project returns, build features,
    search, build the result. Going through the CLI's own path rather than a shortcut
    is what makes the IRR count mean something — the per-project solve is outside the
    search, and a guard that never invoked it could not notice it moving inside.
    """
    assumptions = load_default()
    loaded = load_pipeline(pipeline or GOLDEN_PIPELINE, assumptions)
    arrays = loaded.arrays

    timeline = Timeline()
    with ExitStack() as stack:
        for module, name, kind, probe in TRACED:
            stack.enter_context(_recorded(module, name, kind, timeline, probe=probe))

        preview = preview_feasibility(arrays, mandate, assumptions)
        rows = np.flatnonzero(preview.screens.eligible)
        returns = project_returns(arrays, assumptions, mandate.hold_years)
        features = build_features(
            arrays,
            equity_irr=np.nan_to_num(returns.equity_irr),
            irr_defined=returns.defined,
            merchant_share=1.0 - arrays.revenue.ppa_share,
        ).take(rows)
        controls = SearchControls(effort=effort, seed=SEED, deterministic_reduction=deterministic)
        outcome = run_search(features, mandate, assumptions, controls)
        timeline.result = build_result(
            arrays,
            features,
            mandate,
            assumptions,
            SelectionOutcome(
                eligible=preview.screens.eligible,
                winner=outcome.selection,
                locked=np.zeros(arrays.count, dtype=np.bool_),
                returns=returns,
                contracted_share=contracted_revenue_share(arrays),
            ),
            deterministic=controls.deterministic_reduction,
        )

    timeline.population_size = outcome.population_size
    timeline.generations = outcome.generations
    timeline.candidates = features.project_count
    timeline.eligible = int(rows.size)
    return timeline
