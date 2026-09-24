"""Starting a run, watching it, and reading what it produced.

Three things here carry product meaning rather than plumbing, and all three are
§13 cases:

* **409** when the pipeline moved under the hash the client was looking at. The
  run is refused rather than silently executed against different data; stored
  runs keep their own snapshot and only a *new* run is blocked.
* **422** on exactly the two conditions that make a mandate unrunnable — nothing
  passes the screens, or the locked projects alone need more equity than is
  available — and the second says which locks to release.
* **Locked projects that alone breach a concentration cap do not block.** That is
  not one of the two conditions: the run proceeds and the breach surfaces on the
  result tile rather than being hidden.

``GET /optimisations/{id}`` for a finished run returns the **stored bytes
verbatim**. Re-serialising a parsed record would make §11's "a run id reopens the
exact result" true only up to whatever pydantic happens to emit today.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from http import HTTPStatus
from typing import Annotated, Any, Final

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import StreamingResponse

from terrafolio.api.errors import ApiError, ErrorCode
from terrafolio.api.messages import render
from terrafolio.api.pipeline_source import Snapshot
from terrafolio.api.routes_pipeline import require_active_assumption_set, service_of
from terrafolio.api.service import ENGINE_ERROR, Service
from terrafolio.api.sse import RunPulse, event_stream, read_pulse, resume_from
from terrafolio.api.wire import (
    OptimisationAccepted,
    OptimisationRequest,
    PreviewRequest,
    PreviewResponse,
    RunInFlight,
)
from terrafolio.domain.conventions import EUR_PER_EUR_MILLION
from terrafolio.domain.enums import RunStatus, WarningCode
from terrafolio.domain.reduce import mandate_to_scalars
from terrafolio.domain.results import FeasibilityWarning
from terrafolio.export.committee import CommitteePackUnavailableError, committee_pack
from terrafolio.export.csv import cashflow_csv, holdings_csv
from terrafolio.optimiser.feasibility import FeasibilityPreview, preview_feasibility
from terrafolio.store.events import latest_generation
from terrafolio.store.ids import is_ulid
from terrafolio.store.records import StoredRun
from terrafolio.store.runs import TERMINAL_STATUSES, load_result_json, load_run

__all__ = ["router"]

router = APIRouter()

CSV_MEDIA_TYPE: Final = "text/csv; charset=utf-8"
IMMUTABLE: Final = "private, immutable, max-age=31536000"
"""§8: a finished run never changes, so a client may keep it for a year."""


# --------------------------------------------------------------------------
# Feasibility
# --------------------------------------------------------------------------


def _preview(
    snapshot: Snapshot, service: Service, request: PreviewRequest | OptimisationRequest
) -> FeasibilityPreview:
    """Screen one mandate against **one** load of the pipeline.

    The snapshot is passed rather than fetched so that a caller which then acts
    on the result — starting a run against the eligible set this computed — is
    guaranteed to be acting on the same pipeline it screened.
    """
    return preview_feasibility(
        snapshot.candidates.arrays,
        mandate_to_scalars(request.mandate),
        service.assumptions,
        locked_ids=request.locked_ids,
        excluded_ids=request.excluded_ids,
    )


def _warnings(preview: FeasibilityPreview) -> tuple[FeasibilityWarning, ...]:
    """§5's warnings, ordered by severity, each with its pinned sentence.

    ``severity`` is ``alert`` or ``info``, as 1A's enum defines it. Whether a
    warning *blocks* is not a severity — it is a property of the code, and
    ``runnable`` is the signal a client acts on.
    """
    return tuple(
        FeasibilityWarning(
            code=signal.code,
            severity=signal.code.severity,
            message=render(signal.code, signal.detail),
        )
        for signal in preview.signals
    )


@router.post("/mandate/preview")
def post_preview(request: Request, body: PreviewRequest) -> PreviewResponse:
    """§5. The §5.4 figures for a mandate, without starting a run.

    The screen computes these client-side, because §12 budgets the feedback at
    under 100 ms and a round trip cannot promise that. This exists so the two
    implementations can be held to the same answer — if they diverge, the client
    is wrong.
    """
    service = service_of(request)
    preview = _preview(service.source.current(), service, body)
    return PreviewResponse.model_validate(
        {
            "eligible_count": preview.eligible_count,
            "total_count": preview.total_count,
            "eligible_capacity_mw": preview.eligible_capacity_mw,
            "eligible_equity_m": preview.eligible_equity / EUR_PER_EUR_MILLION,
            "eligible_solar_share": preview.eligible_solar_share,
            "eligible_gearing": preview.eligible_gearing,
            "locked_equity_m": preview.locked_equity / EUR_PER_EUR_MILLION,
            "warnings": _warnings(preview),
            "screens_to_widen": preview.screens_to_widen,
            "runnable": preview.runnable,
        }
    )


# --------------------------------------------------------------------------
# Starting a run
# --------------------------------------------------------------------------


def _refuse_stale_pipeline(snapshot: Snapshot, claimed: str | None) -> None:
    current = snapshot.pipeline_hash
    if claimed is not None and claimed != current:
        raise ApiError(
            HTTPStatus.CONFLICT,
            ErrorCode.PIPELINE_MOVED,
            "The pipeline changed since you loaded it; reload and try again.",
            {"currentPipelineHash": current, "pipelineHash": claimed},
        )


def _refuse_unrunnable(preview: FeasibilityPreview, locked_ids: Iterable[str]) -> None:
    """422 on exactly the two blocking conditions, and no others.

    Deliberately driven by ``WarningCode.disables_run`` rather than by re-testing
    the conditions here: preview and run must agree, and a second implementation
    of "what blocks" is how they stop agreeing.
    """
    blocking = [signal for signal in preview.signals if signal.blocks_the_run]
    if not blocking:
        return
    signal = blocking[0]
    detail: dict[str, Any] = {}
    if signal.code is WarningCode.LOCKS_EXCEED_CAPITAL:
        detail = {
            "lockedEquity_m": signal.detail["lockedEquity"] / EUR_PER_EUR_MILLION,
            "availableCapital_m": signal.detail["availableCapital"] / EUR_PER_EUR_MILLION,
            "excess_m": signal.detail["excess"] / EUR_PER_EUR_MILLION,
            "lockedIds": sorted(locked_ids),
        }
    else:
        detail = {"screensToWiden": list(preview.screens_to_widen)}
    raise ApiError(
        HTTPStatus.UNPROCESSABLE_ENTITY,
        ErrorCode(signal.code.name),
        render(signal.code, signal.detail),
        detail,
    )


@router.post("/optimisations", status_code=HTTPStatus.ACCEPTED)
def post_optimisation(request: Request, body: OptimisationRequest, response: Response) -> Any:
    """§6. Starts a run. **202, never a result** — the client watches the stream."""
    service = service_of(request)
    snapshot = service.source.current()
    # One assumption set per server, and one status for saying so — `GET
    # /pipeline` answers 400 for the identical condition, and §11 maps 409 to
    # PIPELINE_MOVED alone, so a client keying off the status would read a
    # conflict here as "reload the pipeline" and get nowhere.
    require_active_assumption_set(service, body.assumption_set_id)
    _refuse_stale_pipeline(snapshot, body.pipeline_hash)
    preview = _preview(snapshot, service, body)
    _refuse_unrunnable(preview, body.locked_ids)

    pending = service.submit(body, preview, snapshot)
    location = f"/optimisations/{pending.run_id}"
    response.headers["Location"] = location
    return OptimisationAccepted(
        run_id=pending.run_id,
        run_ref=pending.run_ref,
        status=RunStatus.QUEUED,
        stream_url=f"{location}/stream",
        result_url=location,
        total_generations=pending.total_generations,
        seed=pending.provenance.seed,
    )


# --------------------------------------------------------------------------
# Reading one
# --------------------------------------------------------------------------


FAILED_MESSAGE: Final = "The search did not complete. The failure is recorded against this run."
"""What a failed run says on the wire.

**Not the stored ``error_message``**, which holds the worker's whole traceback
for the audit trail. §1.7 says ``message`` is "one sentence fit to show a user",
and a traceback is neither one sentence nor fit to show: it carries the server's
absolute filesystem paths and its internal structure to a caller that, in this
backlog, is not authenticated at all (epic §12 Q7). The traceback stays in the
store and in the server log, where an operator reads it.
"""


def _require_ulid(run_id: str) -> None:
    """A malformed id never reaches the store."""
    if not is_ulid(run_id):
        raise ApiError(
            HTTPStatus.NOT_FOUND,
            ErrorCode.RUN_NOT_FOUND,
            "No run with that identifier.",
            {"runId": run_id},
        )


def _pulse(connection: sqlite3.Connection, run_id: str) -> RunPulse:
    """One run's state, cheaply, or 404.

    ``load_run`` parses ``result_json`` into a validated ``RunRecord`` — every
    holding, every field — which is a great deal of work to answer "has it
    finished". This reads four columns by primary key instead, and the full
    record is loaded only where one is actually needed.
    """
    pulse = read_pulse(connection, run_id)
    if pulse is None:
        raise ApiError(
            HTTPStatus.NOT_FOUND,
            ErrorCode.RUN_NOT_FOUND,
            "No run with that identifier.",
            {"runId": run_id},
        )
    return pulse


def _finished(service: Service, run_id: str) -> StoredRun:
    """One run that has produced a portfolio, or 409.

    The exports and the pack all need a result; asking for one before the search
    has finished is a 409 rather than a 404, because the run exists and the same
    request will succeed later.
    """
    _require_ulid(run_id)
    with closing(service.connect()) as connection:
        pulse = _pulse(connection, run_id)
        if pulse.status is not RunStatus.SUCCEEDED:
            raise ApiError(
                HTTPStatus.CONFLICT,
                ErrorCode.RUN_NOT_FINISHED,
                "That run has not produced a portfolio yet.",
                {"runId": run_id, "status": pulse.status.value},
            )
        return load_run(connection, run_id=run_id)


@router.get("/optimisations/{run_id}")
def get_optimisation(request: Request, run_id: str) -> Response:
    """§8. The stored result, or the run's progress if it has not finished.

    A run in flight answers **200** with ``status`` and no ``aggregates``. Not
    202: the run is the resource, and 202 would say the request to read it had
    been accepted rather than that the run has not finished.
    """
    service = service_of(request)
    _require_ulid(run_id)
    with closing(service.connect()) as connection:
        pulse = _pulse(connection, run_id)
        if pulse.status is RunStatus.SUCCEEDED:
            return Response(
                content=load_result_json(connection, run_id=run_id),
                media_type="application/json",
                headers={"Cache-Control": IMMUTABLE},
            )
        if pulse.status in TERMINAL_STATUSES:
            body: dict[str, Any] = json.loads(load_result_json(connection, run_id=run_id))
            body["error"] = {"code": pulse.error_code or ENGINE_ERROR, "message": FAILED_MESSAGE}
            return Response(
                content=json.dumps(body),
                media_type="application/json",
                headers={"Cache-Control": IMMUTABLE},
            )
        stored = load_run(connection, run_id=run_id)
        generation = latest_generation(connection, run_id=run_id)
    return Response(
        content=_in_flight(stored, generation).model_dump_json(by_alias=True),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


def _in_flight(stored: StoredRun, generation: int) -> RunInFlight:
    record = stored.record
    return RunInFlight(
        run_id=record.run_id,
        run_ref=record.run_ref,
        status=record.status,
        created_at=record.created_at,
        effort=record.effort,
        mandate=record.mandate,
        locked_ids=record.locked_ids,
        excluded_ids=record.excluded_ids,
        generation=generation,
        total_generations=stored.generations_planned,
    )


# --------------------------------------------------------------------------
# The stream
# --------------------------------------------------------------------------


@router.get("/optimisations/{run_id}/stream")
def get_stream(
    request: Request,
    run_id: str,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """§7. Server-sent events, tailed from the durable log.

    A subscriber that arrives after the run finished still receives every
    generation and then exactly one terminal frame — which is the whole reason
    the log rather than a fan-out is the source of truth.
    """
    service = service_of(request)
    if not is_ulid(run_id):
        raise ApiError(
            HTTPStatus.NOT_FOUND, ErrorCode.RUN_NOT_FOUND, "No run with that identifier."
        )
    with closing(service.connect()) as connection:
        if read_pulse(connection, run_id) is None:
            raise ApiError(
                HTTPStatus.NOT_FOUND, ErrorCode.RUN_NOT_FOUND, "No run with that identifier."
            )
    return StreamingResponse(
        event_stream(
            service.connect,
            run_id,
            after_generation=resume_from(last_event_id),
            poll_ms=service.settings.sse_poll_ms,
            keepalive_ms=service.settings.sse_keepalive_ms,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            # Proxies that buffer a response defeat the point of streaming it.
            "X-Accel-Buffering": "no",
        },
    )


# --------------------------------------------------------------------------
# Exports
# --------------------------------------------------------------------------


def _attachment(name: str) -> str:
    return f'attachment; filename="{name}"'


@router.get("/optimisations/{run_id}/holdings.csv")
def get_holdings_csv(request: Request, run_id: str) -> Response:
    """§9. The selection, seventeen columns, generated from the stored result."""
    stored = _finished(service_of(request), run_id)
    return Response(
        content=holdings_csv(stored),
        media_type=CSV_MEDIA_TYPE,
        headers={"Content-Disposition": _attachment(f"holdings-{stored.record.run_ref}.csv")},
    )


@router.get("/optimisations/{run_id}/cashflow.csv")
def get_cashflow_csv(request: Request, run_id: str) -> Response:
    """§9. Thirty rows from ``cashflow30Y_m`` — **no terminal value** (A-6)."""
    stored = _finished(service_of(request), run_id)
    return Response(
        content=cashflow_csv(stored),
        media_type=CSV_MEDIA_TYPE,
        headers={"Content-Disposition": _attachment(f"cashflow-{stored.record.run_ref}.csv")},
    )


@router.get("/optimisations/{run_id}/pack")
def get_pack(request: Request, run_id: str) -> Response:
    """§9.2. The offline committee pack: one file that opens with no network."""
    service = service_of(request)
    stored = _finished(service, run_id)
    try:
        document = committee_pack(
            stored,
            stylesheet=service.settings.stylesheet_path,
            fonts_dir=service.settings.fonts_dir,
            atlas=service.settings.atlas_path,
        )
    except CommitteePackUnavailableError as missing:
        raise ApiError(
            HTTPStatus.SERVICE_UNAVAILABLE,
            ErrorCode.PACK_UNAVAILABLE,
            str(missing),
            {"path": str(missing.path)},
        ) from missing
    return Response(
        content=document,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": _attachment(f"terrafolio-{stored.record.run_ref}.html")},
    )
