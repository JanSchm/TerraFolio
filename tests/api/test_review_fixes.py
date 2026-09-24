"""Guards for the defects an extra-high-effort review of this branch turned up.

Each test here names one of them. They live together rather than scattered
through the suite because what they have in common is the interesting part:
every one was invisible to the tests that already existed, and most were
invisible to reading the code as well.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import shutil
import tempfile
from concurrent.futures import CancelledError, Future
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import GOLDEN_PIPELINE, mandate, settings_for
from test_sse import queued_run

from terrafolio.api.app import ASSET_DIRECTORIES, PAGES, create_app
from terrafolio.api.pipeline_source import PipelineSource
from terrafolio.api.routes_runs import FAILED_MESSAGE
from terrafolio.api.service import build_service
from terrafolio.api.sse import RunPulse, _generation_frame, _number, _terminal_frame
from terrafolio.config.loader import load_default
from terrafolio.domain.enums import RunStatus
from terrafolio.export.committee import Mercator, _figure, _one_decimal, layout
from terrafolio.pipeline.loader import load_pipeline
from terrafolio.runner.pool import Callbacks, _report
from terrafolio.runner.worker import _PIPELINES, _pipeline_for
from terrafolio.store.records import RunEvent

# --------------------------------------------------------------------------
# The pack endpoint served a broken 200 for a run with no portfolio
# --------------------------------------------------------------------------


async def test_the_pack_refuses_a_run_that_has_no_portfolio(tmp_path: Path) -> None:
    """It used to answer 200 with a 336 KB document containing zero tiles.

    Both CSV endpoints already refused; the pack was the odd one out, and
    ``committee.py`` even carried ``# pragma: no cover - the endpoint refuses an
    unfinished run`` describing a guard that did not exist. A committee would
    have received a blank sheet that looked like a real export.
    """
    settings = settings_for(tmp_path)
    service = build_service(settings)
    try:
        app = create_app(settings, service=service)
        run_id = queued_run(service)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            for endpoint in ("pack", "holdings.csv", "cashflow.csv"):
                response = await client.get(f"/optimisations/{run_id}/{endpoint}")
                assert response.status_code == 409, endpoint
                assert response.json()["error"]["code"] == "RUN_NOT_FINISHED", endpoint
    finally:
        service.shutdown()


# --------------------------------------------------------------------------
# A cancelled future left its run queued for ever
# --------------------------------------------------------------------------


def test_a_cancelled_future_still_ends_its_run() -> None:
    """``Future.exception()`` *raises* on a cancelled future rather than returning.

    The obvious spelling therefore raised inside a done-callback, where
    ``concurrent.futures`` swallows it, and neither callback fired. Since
    ``Runner.shutdown`` cancels outstanding futures, every shutdown left queued
    runs stuck in ``queued`` — a status the store's forward-only transitions can
    never correct.
    """
    seen: list[str] = []
    callbacks = Callbacks(
        succeeded=lambda outcome: seen.append("succeeded"),
        failed=lambda error: seen.append(f"failed:{type(error).__name__}"),
    )
    future: Future[Any] = Future()
    future.cancel()
    future.set_running_or_notify_cancel()

    _report(future, callbacks)

    assert seen == ["failed:CancelledError"]


def test_a_failed_future_still_reports_its_error() -> None:
    """The counterpart: the ordinary failure path is unchanged."""
    seen: list[BaseException] = []
    callbacks = Callbacks(
        succeeded=lambda outcome: seen.append(RuntimeError("unexpected")),
        failed=seen.append,
    )
    future: Future[Any] = Future()
    future.set_exception(ValueError("boom"))
    _report(future, callbacks)
    assert [type(error).__name__ for error in seen] == ["ValueError"]
    assert not isinstance(seen[0], CancelledError)


# --------------------------------------------------------------------------
# Tracebacks reached the client
# --------------------------------------------------------------------------


def test_the_failure_message_on_the_wire_is_one_sentence() -> None:
    """§1.7: ``message`` is one sentence fit to show a user.

    It used to be ``run.error_message``, which holds the worker's whole
    traceback — 1,015 characters of absolute server paths, to a caller that
    nothing in this backlog authenticates (epic §12 Q7).
    """
    assert FAILED_MESSAGE.count(".") <= 2
    assert "Traceback" not in FAILED_MESSAGE
    assert "/" not in FAILED_MESSAGE
    assert len(FAILED_MESSAGE) < 120


def test_a_cancelled_run_is_not_reported_as_an_engine_error() -> None:
    """``error_code`` is optional for a cancelled run, mandatory for a failed one.

    Defaulting to ENGINE_ERROR reported a run someone deliberately stopped as a
    crash, and any retry keyed on that code would have retried it.
    """

    def frame(status: RunStatus) -> dict[str, Any]:
        pulse = RunPulse(
            status=status,
            created_at=dt.datetime.now(tz=dt.UTC),
            duration_ms=1,
            total_generations=10,
            error_code=None,
            error_message=None,
        )
        raw = _terminal_frame("01JB2Q", pulse).decode("utf-8")
        return json.loads(raw.split("data: ")[1])

    cancelled = frame(RunStatus.CANCELLED)
    assert cancelled["error"]["code"] == "RUN_CANCELLED"
    assert "cancelled" in cancelled["error"]["message"]

    failed = frame(RunStatus.FAILED)
    assert failed["error"]["code"] == "ENGINE_ERROR"
    assert "Traceback" not in failed["error"]["message"]


# --------------------------------------------------------------------------
# A NaN fitness would have emitted invalid JSON
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_a_non_finite_fitness_crosses_the_wire_as_null(value: float) -> None:
    """``json.dumps(nan)`` emits the bare token ``NaN``, which is not JSON.

    One such frame and every subscriber's ``JSON.parse`` throws mid-stream,
    taking the search screen down with no diagnosable error. §1.4 requires
    ``null`` for an undefined number anywhere on this wire.
    """
    assert _number(value) == "null"
    frame = _generation_frame(
        RunEvent(generation=1, best_fitness=value, mean_fitness=value, summary_json="{}"),
        total=60,
    ).decode("utf-8")
    payload = json.loads(frame.split("data: ")[1])
    assert payload["bestFitness"] is None
    assert payload["meanFitness"] is None


def test_an_ordinary_fitness_is_unchanged() -> None:
    assert _number(6.831402) == "6.831402"
    assert _number(0.0) == "0.0"


# --------------------------------------------------------------------------
# The static mount published the whole web working tree
# --------------------------------------------------------------------------


async def test_only_the_pages_and_their_assets_are_served(
    client: httpx.AsyncClient,
) -> None:
    """Mounting ``web/`` wholesale also published the build inputs.

    ``package.json``, ``tailwind.config.js``, the un-compiled ``src/``, the node
    test suite and ``node_modules/`` were all reachable anonymously.
    """
    for path in (
        "/package.json",
        "/package-lock.json",
        "/tailwind.config.js",
        "/src/input.css",
        "/tests/offline.test.js",
        "/tools/vendor.mjs",
        "/node_modules/jsdom/package.json",
    ):
        assert (await client.get(path)).status_code == 404, path

    for page in PAGES:
        assert (await client.get(f"/{page}.html")).status_code == 200, page
    assert (await client.get("/")).status_code == 200
    assert (await client.get("/js/format.js")).status_code == 200
    assert set(ASSET_DIRECTORIES) == {"dist", "fonts", "js", "public", "vendor"}


# --------------------------------------------------------------------------
# One status for one condition
# --------------------------------------------------------------------------


async def test_a_foreign_assumption_set_is_400_on_both_endpoints(
    client: httpx.AsyncClient,
) -> None:
    """It answered 400 on GET /pipeline and 409 on POST /optimisations.

    §11 maps 409 to PIPELINE_MOVED alone, so a client keying off the status read
    the conflict as "reload the pipeline" and got nowhere.
    """
    listed = await client.get("/pipeline", params={"assumptionSetId": "not-loaded"})
    started = await client.post(
        "/optimisations", json={"mandate": mandate(), "assumptionSetId": "not-loaded"}
    )
    assert listed.status_code == started.status_code == 400
    assert listed.json()["error"]["code"] == started.json()["error"]["code"] == "INVALID_REQUEST"


# --------------------------------------------------------------------------
# Snapshot isolation, caching and rounding
# --------------------------------------------------------------------------


def test_a_held_snapshot_survives_a_reload(tmp_path: Path) -> None:
    """The class-level guarantee the endpoints rely on."""
    directory = tmp_path / "pipeline"
    shutil.copytree(GOLDEN_PIPELINE, directory)
    source = PipelineSource(directory, load_default(), engine_version="test")
    held = source.current()
    original = held.pipeline_hash
    rendered = held.rendered(10)

    copied = json.loads((directory / "P01-almonte-solar.json").read_text())
    copied["id"] = "P96"
    (directory / "P96.json").write_text(json.dumps(copied))
    source.reload()

    assert source.current().pipeline_hash != original
    # The snapshot a request is working from is untouched, caches included.
    assert held.pipeline_hash == original
    assert held.rendered(10) is rendered
    assert held.candidates.arrays.count < source.current().candidates.arrays.count


def test_the_worker_caches_one_pipeline_not_one_per_reload() -> None:
    """15 MB per reload per worker, for the life of the process, was the cost."""
    _PIPELINES.clear()
    assumptions = load_default()
    directory = Path(__file__).resolve().parents[1] / "golden" / "fixtures" / "pipeline"
    with tempfile.TemporaryDirectory() as other:
        second = Path(other) / "pipeline"
        shutil.copytree(directory, second)
        copied = json.loads((second / "P01-almonte-solar.json").read_text())
        copied["id"] = "P95"
        (second / "P95.json").write_text(json.dumps(copied))

        _pipeline_for(directory, _hash_of(directory, assumptions), assumptions)
        assert len(_PIPELINES) == 1
        _pipeline_for(second, _hash_of(second, assumptions), assumptions)
        assert len(_PIPELINES) == 1, "the cache kept a pipeline nothing can ask for again"
    _PIPELINES.clear()


def _hash_of(directory: Path, assumptions: Any) -> str:
    return load_pipeline(directory, assumptions).pipeline_hash


def test_the_projection_computes_its_constants_once() -> None:
    """The centre was reprojected for every one of ~8,000 points per pack."""
    spec = layout()["map"]
    project = Mercator(
        scale=spec["width"] * spec["scaleFactor"],
        centre_lon=spec["centreLon"],
        centre_lat=spec["centreLat"],
        translate_x=spec["width"] / 2,
        translate_y=spec["height"] / 2,
    )
    # The centre still lands exactly on the translate, which is the property
    # the offsets exist to satisfy.
    assert project(spec["centreLon"], spec["centreLat"]) == pytest.approx(
        (spec["width"] / 2, spec["height"] / 2)
    )
    # And the offsets are real attributes rather than recomputed per call.
    assert isinstance(project.offset_x, float)
    assert isinstance(project.offset_y, float)


def test_every_pack_figure_uses_the_one_rounding_rule() -> None:
    """An f-string rounds half to even; ``csv.py`` rounds half away from zero.

    An LCOE landing on €42.5/MWh therefore read 43 in holdings.csv and 42 in the
    pack, for the same run — against the invariant ``committee.py``'s own module
    docstring states.
    """
    assert _figure(42.5) == "43"
    assert _figure(41.5) == "42"
    assert f"{42.5:,.0f}" == "42", "the old spelling, kept here to show the difference"
    assert _figure(1234.0) == "1,234"
    assert _one_decimal(3.45) == "3.5"
    assert _one_decimal(3.2) == "3.2"
