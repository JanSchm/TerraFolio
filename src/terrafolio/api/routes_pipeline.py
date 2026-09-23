"""The pipeline endpoints: what can be bought, and whether the files are sound.

``GET /pipeline`` is hit on every load of the mandate screen and is budgeted at
under 200 ms warm at 300 projects, so it answers from bytes that were serialised
once. It carries **scalars only**: at 300 projects the 30-year arrays are 1.8 MB
against 400 KB for everything else, and §7.5's detail sheet needs only the
scalars. The arrays live at ``GET /projects/{id}/statements`` and are fetched on
demand.
"""

from __future__ import annotations

import datetime as dt
from http import HTTPStatus
from typing import Annotated, Final

from fastapi import APIRouter, Header, Query, Request, Response

from terrafolio.api.errors import ApiError, ErrorCode
from terrafolio.api.service import Service
from terrafolio.api.wire import (
    AssumptionsResponse,
    PipelineStatusResponse,
    assumptions_response,
    pipeline_status,
    statements_payload,
)
from terrafolio.domain.mandate_bounds import ALL_BOUNDS

__all__ = ["router", "service_of"]

HOLD_YEARS: Final = ALL_BOUNDS["hold_years"]
"""§5.2's own control range, from the one module that states it.

Writing ``ge=5, le=30`` here would be a second source for a range the
specification already fixes — and the endpoint and the slider disagreeing about
it is the kind of difference nobody finds until a client sends 31.
"""

router = APIRouter()


def service_of(request: Request) -> Service:
    """The application's state, injected by the factory rather than imported."""
    service: Service = request.app.state.service
    return service


def _require_active_assumption_set(service: Service, requested: str | None) -> None:
    """One assumption set is loaded per server, so a request for another is an error.

    §2 offers ``assumptionSetId`` as a query parameter. v1 loads exactly one set
    at startup and every run is recorded against it, so silently serving the
    active set under someone else's id would attach the wrong calibration to
    whatever the client then did with the answer.
    """
    active = service.assumptions.assumption_set_id
    if requested is not None and requested != active:
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            ErrorCode.INVALID_REQUEST,
            f"This server is running assumption set {active}; {requested} is not loaded.",
            {"assumptionSetId": active, "requested": requested},
        )


@router.get("/pipeline")
def get_pipeline(
    request: Request,
    hold_years: Annotated[
        int, Query(alias="holdYears", ge=int(HOLD_YEARS.lo), le=int(HOLD_YEARS.hi))
    ] = int(HOLD_YEARS.default),
    assumption_set_id: Annotated[str | None, Query(alias="assumptionSetId")] = None,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Response:
    """§2. The candidate set with its derived economics, and a snapshot hash.

    The ETag covers the pipeline hash, the assumption set, the engine version
    **and** the hold period — not the pipeline hash alone, which would leave a
    client holding IRRs computed at a different hold period while the hash said
    nothing had moved.
    """
    service = service_of(request)
    _require_active_assumption_set(service, assumption_set_id)
    rendered = service.source.rendered(hold_years)
    if if_none_match is not None and _matches(if_none_match, rendered.etag):
        return Response(status_code=HTTPStatus.NOT_MODIFIED, headers={"ETag": rendered.etag})
    return Response(
        content=rendered.body,
        media_type="application/json",
        headers={"ETag": rendered.etag, "Cache-Control": "private, max-age=0, must-revalidate"},
    )


def _matches(header: str, etag: str) -> bool:
    """``If-None-Match`` semantics: ``*``, or any of a comma-separated list.

    A weak validator prefix is accepted on the way in — a proxy may add one —
    because the comparison this endpoint needs is "is your copy the current
    content", which is exactly what weak comparison answers.
    """
    candidates = [part.strip() for part in header.split(",")]
    return "*" in candidates or any(
        candidate.removeprefix("W/") == etag.removeprefix("W/") for candidate in candidates
    )


@router.get("/pipeline/status")
def get_pipeline_status(request: Request) -> PipelineStatusResponse:
    """§3. Load and validation state, including the dispersion report.

    ``rejected`` files did not load — a tie-out failed. ``warnings`` loaded
    normally: plausibility checks and the dispersion report warn and never block,
    because one stale file must not stop all work (A-7).
    """
    service = service_of(request)
    return pipeline_status(service.source.result, loaded_at=service.source.loaded_at)


@router.post("/pipeline/reload")
def reload_pipeline(request: Request) -> PipelineStatusResponse:
    """Revalidate the directory and recompute the snapshot hash.

    Users add and remove files while the server runs (epic §2). Stored runs keep
    the snapshot they were produced against — it is content-addressed and never
    deleted — so a reload can only affect a *new* run (§13).
    """
    service = service_of(request)
    service.reload_pipeline()
    return pipeline_status(service.source.result, loaded_at=service.source.loaded_at)


@router.get("/projects/{project_id}/statements")
def get_statements(request: Request, project_id: str) -> dict[str, object]:
    """§4. One project's thirty years, in €m and GWh, for §7.5's detail sheet.

    Mandate-independent, so no ``holdYears``: nothing here moves with the hold
    period, and taking one would imply otherwise.
    """
    service = service_of(request)
    for file in service.source.candidates.files:
        if file.id == project_id:
            return statements_payload(file)
    raise ApiError(
        HTTPStatus.NOT_FOUND,
        ErrorCode.PROJECT_NOT_FOUND,
        f"No project {project_id} in the current pipeline.",
        {"id": project_id},
    )


@router.get("/assumptions")
def get_assumptions(request: Request) -> AssumptionsResponse:
    """§10. The active calibration, so a client can show what a run used.

    Every rate, weight, floor, clamp, tolerance and band the engine reads
    appears here and nowhere else in code (§9.4, §10.2).
    """
    service = service_of(request)
    return assumptions_response(service.assumptions, recorded_at=_recorded_at(service))


def _recorded_at(service: Service) -> dt.datetime:
    return service.assumptions_recorded_at
