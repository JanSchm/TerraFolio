"""Turning a finished search into the immutable record a committee reads.

The worker returns euros in plain dataclasses, because the numeric core may not
import pydantic and a process boundary should not carry a validated model across
it. This is where that becomes ``docs/api.md`` §8's ``RunRecord`` — the €m
conversion, the join onto names and coordinates, and the provenance that makes
the run reproducible.

**The two cash-flow series are converted separately and never sliced from each
other.** ``cashflow30Y_m`` is thirty years with no terminal value and is what the
chart, the tile and ``cashflow.csv`` show; ``cashflowHold_m`` runs the mandate's
hold period and carries the terminal value in its last element, and drives
``equityIrr`` and ``moic`` and nothing else. Conflating them is the single most
likely silent bug in the feature, and it produces numbers that look right.
"""

from __future__ import annotations

import datetime as dt
import platform
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

import terrafolio
from terrafolio.api.scalars import Candidates, holding_records
from terrafolio.config.assumptions import AssumptionSet
from terrafolio.domain.conventions import EUR_PER_EUR_MILLION
from terrafolio.domain.enums import Effort, RunStatus
from terrafolio.domain.mandate import Mandate
from terrafolio.domain.results import (
    ConvergencePoint,
    FeasibilityWarning,
    PortfolioAggregates,
    RunProvenance,
    RunRecord,
)
from terrafolio.economics.returns import ProjectReturns
from terrafolio.optimiser.ga import GenerationEvent
from terrafolio.optimiser.result import PortfolioTotals, RunResult
from terrafolio.pipeline.loader import LoadResult
from terrafolio.runner.worker import WorkerOutcome
from terrafolio.store.records import RunEvent

__all__ = [
    "PendingRun",
    "aggregates_of",
    "build_provenance",
    "convergence_from_log",
    "failed_record",
    "succeeded_record",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class PendingRun:
    """Everything fixed at submission, held until the run comes back.

    The candidate view is captured **here**, not looked up when the run
    finishes. ``PipelineSource.reload()`` replaces its objects rather than
    mutating them, so a reference taken at submission still describes the
    snapshot this run was accepted against even if the pipeline moved under it
    mid-search — which is exactly what §13 requires of a stored run.
    """

    run_id: str
    run_ref: str
    created_at: dt.datetime
    mandate: Mandate
    effort: Effort
    locked_ids: tuple[str, ...]
    excluded_ids: tuple[str, ...]
    provenance: RunProvenance
    candidates: Candidates
    returns: ProjectReturns
    total_generations: int

    warnings: tuple[FeasibilityWarning, ...] = ()
    """The §5.4 warnings this mandate was accepted under.

    2B stores these on the run so a reader can see the advisory conditions that
    applied at submission — a capacity target the eligible pool could not reach,
    a budget it could not absorb — without re-deriving them against a pipeline
    that has since moved.
    """


def _m(euros: float) -> float:
    return euros / EUR_PER_EUR_MILLION


def _series_m(series: object) -> tuple[float, ...]:
    values: Sequence[float] = np.asarray(series, dtype=np.float64).tolist()
    return tuple(_m(float(value)) for value in values)


def build_provenance(
    *,
    seed: int,
    loaded: LoadResult,
    assumptions: AssumptionSet,
    blas_threads: int,
) -> RunProvenance:
    """§8.3's record. Every field here is required to reproduce the run (§12).

    ``blas_threads`` is a **claim made before the run**, because ``open_run``
    writes it and the run's inputs are frozen at submission. The worker reports
    what it actually saw and :func:`succeeded_record` refuses a mismatch: a
    provenance block that says "one thread" over a run that had four is worse
    than no provenance at all, because it invites a reproduction attempt that
    cannot succeed and gives no clue why.
    """
    return RunProvenance(
        seed=seed,
        pipeline_hash=loaded.pipeline_hash,
        file_hashes=dict(loaded.file_hashes),
        assumption_set_id=assumptions.assumption_set_id,
        assumption_set_hash=assumptions.content_hash,
        engine_version=terrafolio.__version__,
        numpy_version=np.__version__,
        blas_threads=blas_threads,
        python_version=platform.python_version(),
        platform=f"{platform.system()} {platform.machine()}",
    )


def aggregates_of(totals: PortfolioTotals) -> PortfolioAggregates:
    """§8.1's twelve tiles, converted to €m at this boundary and nowhere else.

    ``blendedIrr`` is deliberately absent. It is the cheap equity-weighted
    approximation the *search* optimised (§10.3); the tile shows the pooled rate
    solved once on the portfolio's own aggregated cash flow, and putting both on
    the result would invite a reader to compare two numbers that answer
    different questions.
    """
    return PortfolioAggregates.model_validate(
        {
            "project_count": totals.project_count,
            "solar_count": totals.solar_count,
            "wind_count": totals.wind_count,
            "capacity_mw": totals.capacity_mw,
            "solar_share": totals.solar_share,
            "total_capex_m": _m(totals.total_capex),
            "senior_debt_m": _m(totals.senior_debt),
            "equity_m": _m(totals.equity),
            "gearing": totals.gearing,
            "capital_deployed": totals.capital_deployed,
            "equity_irr": totals.equity_irr,
            "moic": totals.moic,
            "weighted_lcoe": totals.weighted_lcoe,
            "annual_generation_gwh": totals.annual_generation_gwh,
            "co2_avoided_kt": totals.co2_avoided_kt,
            "merchant_share": totals.merchant_share,
            "weighted_risk_score": totals.weighted_risk_score,
            "worst_min_dscr": totals.worst_min_dscr,
            "country_shares": dict(totals.country_shares),
            "largest_country_code": totals.largest_country_code,
            "largest_country_share": totals.largest_country_share,
            "thirty_year_fcfe_m": _m(totals.thirty_year_fcfe),
            "fitness": totals.fitness,
        }
    )


def convergence_of(events: Sequence[GenerationEvent]) -> tuple[ConvergencePoint, ...]:
    """The §6 curve, as the result carries it.

    Must match the event log point for point: ``finish_run`` reconciles the two
    and refuses a record whose curve disagrees with what was streamed, so a
    subscriber and a reader of the stored run cannot be shown different searches.
    """
    return tuple(
        ConvergencePoint(
            generation=event.generation,
            best_fitness=event.best_fitness,
            mean_fitness=event.mean_fitness,
        )
        for event in events
    )


def convergence_from_log(events: Sequence[RunEvent]) -> tuple[ConvergencePoint, ...]:
    """The same curve, recovered from the log when the run did not finish.

    A failed run still has to reconcile against whatever generations reached the
    table before it failed, and the in-memory list did not survive the failure.
    """
    return tuple(
        ConvergencePoint(
            generation=event.generation,
            best_fitness=event.best_fitness,
            mean_fitness=event.mean_fitness,
        )
        for event in events
    )


class BlasThreadMismatchError(RuntimeError):
    """The run ran under a thread count its provenance does not describe."""


def succeeded_record(pending: PendingRun, outcome: WorkerOutcome) -> RunRecord:
    """§8's stored result for a search that completed."""
    provenance = pending.provenance
    if outcome.blas_threads != provenance.blas_threads:
        raise BlasThreadMismatchError(
            f"run {pending.run_id} recorded blasThreads={provenance.blas_threads} "
            f"before it started but ran with {outcome.blas_threads}; the run is "
            f"reproducible only under the count its provenance names"
        )
    result: RunResult = outcome.result
    return RunRecord.model_validate(
        {
            "run_id": pending.run_id,
            "run_ref": pending.run_ref,
            "status": RunStatus.SUCCEEDED,
            "created_at": pending.created_at,
            "duration_ms": outcome.duration_ms,
            "mandate": pending.mandate,
            "locked_ids": pending.locked_ids,
            "excluded_ids": pending.excluded_ids,
            "effort": pending.effort,
            "selected_ids": result.selected_ids,
            "aggregates": aggregates_of(result.totals),
            "holdings": holding_records(
                pending.candidates,
                pending.returns,
                eligible=outcome.eligible,
                selected=outcome.selected,
                locked=outcome.locked,
            ),
            "cashflow_30y_m": _series_m(result.cashflow_30y),
            "cashflow_hold_m": _series_m(result.cashflow_hold),
            "convergence": convergence_of(outcome.convergence),
            "provenance": provenance,
        }
    )


def failed_record(
    pending: PendingRun,
    *,
    duration_ms: int,
    convergence: tuple[ConvergencePoint, ...],
    status: RunStatus = RunStatus.FAILED,
) -> RunRecord:
    """The record a run that did not finish still leaves behind.

    A failed run is audit trail (§12), so it keeps its mandate, its seed and
    whatever curve it managed before it stopped — which is exactly what someone
    asking "why did this not work" needs. ``aggregates`` is absent, and the
    empty cash-flow series are the shape ``RunRecord`` requires of a run with no
    portfolio, not a portfolio of zeroes.
    """
    return RunRecord.model_validate(
        {
            "run_id": pending.run_id,
            "run_ref": pending.run_ref,
            "status": status,
            "created_at": pending.created_at,
            "duration_ms": duration_ms,
            "mandate": pending.mandate,
            "locked_ids": pending.locked_ids,
            "excluded_ids": pending.excluded_ids,
            "effort": pending.effort,
            "selected_ids": (),
            "aggregates": None,
            "holdings": (),
            "cashflow_30y_m": (),
            "cashflow_hold_m": (),
            "convergence": convergence,
            "provenance": pending.provenance,
        }
    )
