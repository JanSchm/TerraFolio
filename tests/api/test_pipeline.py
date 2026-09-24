"""``GET /pipeline``, its ETag, the load report, and reloading under a live server."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import GOLDEN_PIPELINE, mandate, settings_for

from terrafolio.api.app import create_app
from terrafolio.api.service import build_service
from terrafolio.domain.conventions import YEARS

_GOLDEN_COUNT = len(list(GOLDEN_PIPELINE.glob("*.json")))
"""The fixture corpus, counted rather than written down."""


def _by_id(body: dict[str, Any]) -> dict[str, Any]:
    return {row["id"]: row for row in body["projects"]}


async def test_pipeline_lists_every_loaded_project_in_canonical_order(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/pipeline")
    assert response.status_code == 200
    body = response.json()
    ids = [project["id"] for project in body["projects"]]
    assert ids == sorted(ids)
    assert body["projectCount"] == len(ids)
    assert body["pipelineHash"].startswith("sha256:")


async def test_pipeline_carries_no_thirty_year_array(client: httpx.AsyncClient) -> None:
    """§2: scalars only.

    At 300 projects the statement arrays are 1.8 MB against 400 KB for everything
    else — four and a half times the whole rest of the response, on an endpoint
    the mandate screen hits on every load. Checked structurally rather than by
    naming the blocks, so a block added later cannot slip a series in.
    """
    body = (await client.get("/pipeline")).json()

    def is_series(node: Any) -> bool:
        """A statement series: thirty-odd numbers in a row, nulls included."""
        return (
            isinstance(node, list)
            and len(node) >= YEARS
            and all(value is None or isinstance(value, int | float) for value in node)
        )

    def arrays(node: Any, path: str = "") -> list[str]:
        found: list[str] = []
        if is_series(node):
            found.append(path)
        elif isinstance(node, dict):
            for key, value in node.items():
                found.extend(arrays(value, f"{path}.{key}"))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                found.extend(arrays(value, f"{path}[{index}]"))
        return found

    assert arrays(body["projects"]) == []


async def test_etag_round_trips_to_304(client: httpx.AsyncClient) -> None:
    first = await client.get("/pipeline")
    etag = first.headers["ETag"]
    again = await client.get("/pipeline", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.content == b""


async def test_the_etag_moves_with_the_hold_period(client: httpx.AsyncClient) -> None:
    """Not ``pipelineHash`` alone (§2).

    Conflating them leaves a client holding IRRs computed at a different hold
    period while the hash says nothing moved — which is the failure that makes
    the hold-period slider look broken.
    """
    ten = await client.get("/pipeline", params={"holdYears": 10})
    fifteen = await client.get("/pipeline", params={"holdYears": 15})
    assert ten.json()["pipelineHash"] == fifteen.json()["pipelineHash"]
    assert ten.headers["ETag"] != fifteen.headers["ETag"]
    stale = await client.get(
        "/pipeline", params={"holdYears": 15}, headers={"If-None-Match": ten.headers["ETag"]}
    )
    assert stale.status_code == 200


async def test_the_hold_period_moves_the_returns_and_not_the_rest(
    client: httpx.AsyncClient,
) -> None:
    """§1.6: ``equityIrr``, ``moic`` and ``paybackYear`` move; ``lcoe`` does not."""
    ten = {
        row["id"]: row for row in (await client.get("/pipeline?holdYears=10")).json()["projects"]
    }
    twenty = {
        row["id"]: row for row in (await client.get("/pipeline?holdYears=20")).json()["projects"]
    }
    moved = [key for key in ten if ten[key]["equityIrr"] != twenty[key]["equityIrr"]]
    assert moved, "no project's IRR responded to a doubled hold period"
    assert all(ten[key]["lcoe"] == twenty[key]["lcoe"] for key in ten)
    assert all(ten[key]["minDscr"] == twenty[key]["minDscr"] for key in ten)


async def test_an_undefined_irr_is_null_and_never_zero(client: httpx.AsyncClient) -> None:
    """§1.4. The chain is engine ``NaN`` -> API ``null`` -> UI em dash."""
    body = (await client.get("/pipeline")).json()
    for project in body["projects"]:
        for field in ("equityIrr", "moic", "minDscr", "paybackYear"):
            assert project[field] is None or isinstance(project[field], int | float)


async def test_hold_years_outside_the_control_range_is_refused(
    client: httpx.AsyncClient,
) -> None:
    assert (await client.get("/pipeline", params={"holdYears": 31})).status_code == 400
    assert (await client.get("/pipeline", params={"holdYears": 4})).status_code == 400


async def test_an_assumption_set_the_server_is_not_running_is_refused(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/pipeline", params={"assumptionSetId": "not-loaded"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


async def test_status_reports_the_load(client: httpx.AsyncClient) -> None:
    body = (await client.get("/pipeline/status")).json()
    assert body["fileCount"] == body["loadedCount"] > 0
    assert body["rejected"] == []
    assert set(body["dispersion"]) >= {"taxRate", "debtRate", "debtTenorYears"}
    for spread in body["dispersion"].values():
        assert spread["min"] <= spread["median"] <= spread["max"]


async def test_statements_carry_the_thirty_years_and_the_full_provenance(
    client: httpx.AsyncClient,
) -> None:
    listed = (await client.get("/pipeline")).json()["projects"][0]
    body = (await client.get(f"/projects/{listed['id']}/statements")).json()
    assert len(body["years"]) == YEARS
    assert len(body["cashFlow"]["fcfe"]) == YEARS
    assert body["assumptions"]["baseYear"] == (await client.get("/pipeline")).json()["baseYear"]
    # The half GET /pipeline abbreviates away: the free-text notes.
    assert any("note" in entry for entry in body["provenance"]["fields"].values())


async def test_statements_for_an_unknown_project_is_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/projects/P999/statements")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PROJECT_NOT_FOUND"


async def test_assumptions_expose_every_group_the_engine_reads(
    client: httpx.AsyncClient,
) -> None:
    body = (await client.get("/assumptions")).json()
    assert set(body) >= {
        "id",
        "hash",
        "exitMultiples",
        "lcoeDiscountRate",
        "objectiveWeights",
        "riskCaps",
        "co2FactorTPerMwh",
        "validation",
        "generator",
        "ga",
        "feasibility",
    }


async def test_adding_a_file_then_reloading_moves_the_hash(tmp_path: Path) -> None:
    """Users add and remove files while the server runs (epic §2).

    The prior run keeps the snapshot it was produced against: snapshots are
    content-addressed and a trigger forbids deleting one, so a reload can only
    ever affect a *new* run (§13).
    """
    directory = tmp_path / "pipeline"
    shutil.copytree(GOLDEN_PIPELINE, directory)
    settings = settings_for(tmp_path, pipeline=directory)
    service = build_service(settings)
    try:
        app = create_app(settings, service=service)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                before = (await client.get("/pipeline")).json()["pipelineHash"]
                started = await client.post(
                    "/optimisations",
                    json={"mandate": mandate(), "effort": "fast", "seed": 7},
                )
                assert started.status_code == 202
                run_id = started.json()["runId"]

                copied = json.loads((directory / "P01-almonte-solar.json").read_text())
                copied["id"] = "P99"
                copied["name"] = "Copy of Almonte"
                (directory / "P99-copy.json").write_text(json.dumps(copied))

                reloaded = await client.post("/pipeline/reload")
                assert reloaded.status_code == 200
                after = reloaded.json()["pipelineHash"]
                assert after != before
                assert reloaded.json()["loadedCount"] == _GOLDEN_COUNT + 1

                stored = (await client.get(f"/optimisations/{run_id}")).json()
                assert stored["provenance"]["pipelineHash"] == before
                assert "P99" not in stored["provenance"]["fileHashes"]
    finally:
        service.shutdown()


@pytest.mark.parametrize("path", ["/pipeline", "/pipeline/status", "/assumptions"])
async def test_reading_the_pipeline_never_mutates_the_hash(
    client: httpx.AsyncClient, path: str
) -> None:
    before = (await client.get("/pipeline")).json()["pipelineHash"]
    await client.get(path)
    assert (await client.get("/pipeline")).json()["pipelineHash"] == before
