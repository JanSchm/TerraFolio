"""Starting a run, refusing one, and reading the result — every status §11 lists."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from conftest import mandate, start_run

from terrafolio.domain.conventions import YEARS


async def test_a_run_is_accepted_with_202_and_a_location(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/optimisations", json={"mandate": mandate(), "effort": "fast", "seed": 42}
    )
    assert response.status_code == 202
    body = response.json()
    assert response.headers["Location"] == f"/optimisations/{body['runId']}"
    assert body["streamUrl"] == f"/optimisations/{body['runId']}/stream"
    assert body["resultUrl"] == f"/optimisations/{body['runId']}"
    assert body["runRef"].startswith("A-")
    assert body["seed"] == 42
    assert body["totalGenerations"] > 0


async def test_an_omitted_seed_is_drawn_and_returned(client: httpx.AsyncClient) -> None:
    """Epic §5: a run with no recorded seed is not a valid run.

    Returning it on the 202 means a client never has to wait for the result to
    learn what it can replay.
    """
    accepted = await start_run(client, seed=None)
    assert isinstance(accepted["seed"], int)
    stored = (await client.get(f"/optimisations/{accepted['runId']}")).json()
    assert stored["provenance"]["seed"] == accepted["seed"]


async def test_a_stale_pipeline_hash_is_409_carrying_the_current_one(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/optimisations",
        json={"mandate": mandate(), "pipelineHash": "sha256:" + "0" * 64},
    )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "PIPELINE_MOVED"
    current = (await client.get("/pipeline")).json()["pipelineHash"]
    assert error["detail"]["currentPipelineHash"] == current


async def test_the_current_pipeline_hash_is_accepted(client: httpx.AsyncClient) -> None:
    current = (await client.get("/pipeline")).json()["pipelineHash"]
    accepted = await start_run(client, pipelineHash=current)
    assert accepted["status"] == "queued"


async def test_locks_exceeding_capital_are_422_and_name_the_locks(
    client: httpx.AsyncClient,
) -> None:
    """§13 requires this to block rather than warn, and to say which to release."""
    listed = (await client.get("/pipeline")).json()["projects"]
    expensive = sorted(listed, key=lambda row: -row["equity_m"])[:4]
    locked = sorted(row["id"] for row in expensive)
    capital = sum(row["equity_m"] for row in expensive) / 2

    response = await client.post(
        "/optimisations",
        json={"mandate": mandate(availableCapital_m=round(capital)), "lockedIds": locked},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "LOCKS_EXCEED_CAPITAL"
    assert error["detail"]["lockedIds"] == locked
    assert error["detail"]["excess_m"] > 0
    assert "Release a lock to run" in error["message"]


async def test_no_candidates_is_422(client: httpx.AsyncClient) -> None:
    """A coherent mandate that nothing in the pipeline satisfies.

    A COD window at the far end of §5.2's range: the fixture corpus commissions
    between 2027 and 2032, so asking for 2033 alone is a perfectly sensible
    question with no answer in this pipeline — which is the shape
    ``NO_CANDIDATES`` is for. An *empty* country or stage list is a different
    thing and answers 400 — see the test below.
    """
    response = await client.post(
        "/optimisations", json={"mandate": mandate(codFrom=2033, codTo=2033)}
    )
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "NO_CANDIDATES"
    assert body["detail"]["screensToWiden"]


async def test_an_empty_country_list_is_400_not_422(client: httpx.AsyncClient) -> None:
    """1A's ``Mandate`` requires at least one country, and it owns the schema.

    ``docs/api.md`` §6.1 previously said this was accepted and answered 422;
    it now records the model's rule, because "you have selected no countries" is
    a fault in the mandate rather than a pipeline with nothing that fits.
    """
    response = await client.post("/optimisations", json={"mandate": mandate(countries=[])})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_MANDATE"
    assert response.json()["error"]["detail"]["field"] == "countries"


async def test_preview_and_run_agree_on_what_blocks(client: httpx.AsyncClient) -> None:
    """§5: ``runnable`` is false iff a warning blocks, and those are the two 422s.

    A preview reporting ``runnable: true`` for a mandate the run rejects would
    light up a button that cannot work.
    """
    unrunnable = mandate(codFrom=2033, codTo=2033)
    preview = (await client.post("/mandate/preview", json={"mandate": unrunnable})).json()
    assert preview["runnable"] is False
    started = await client.post("/optimisations", json={"mandate": unrunnable})
    assert started.status_code == 422

    preview = (await client.post("/mandate/preview", json={"mandate": mandate()})).json()
    assert preview["runnable"] is True
    assert (await client.post("/optimisations", json={"mandate": mandate()})).status_code == 202


async def test_locks_breaching_a_concentration_cap_do_not_block(
    client: httpx.AsyncClient,
) -> None:
    """§13: the run proceeds and the breach is surfaced on the result.

    Only two conditions block, and a concentration breach is not one of them.
    Locking several projects in one country under a 10% country cap must still
    run, and the cap must be visibly breached on the aggregates.
    """
    listed = (await client.get("/pipeline")).json()["projects"]
    by_country: dict[str, list[dict[str, Any]]] = {}
    for row in listed:
        by_country.setdefault(row["countryCode"], []).append(row)
    country, rows = max(by_country.items(), key=lambda item: len(item[1]))
    locked = sorted(row["id"] for row in rows[:3])

    accepted = await start_run(
        client, mandate=mandate(maxCountryShare=0.1, maxProjectShare=0.05), lockedIds=locked
    )
    stored = (await client.get(f"/optimisations/{accepted['runId']}")).json()
    assert stored["status"] == "succeeded"
    assert set(locked) <= set(stored["selectedIds"])
    assert stored["aggregates"]["largestCountryShare"] > 0.1
    assert stored["aggregates"]["largestCountryCode"] == country


@pytest.mark.parametrize(
    ("field", "value"),
    [("holdYears", 99), ("solarShare", 4), ("targetIrr", 0.9), ("availableCapital_m", -5)],
)
async def test_a_mandate_field_out_of_range_is_400(
    client: httpx.AsyncClient, field: str, value: float
) -> None:
    response = await client.post("/optimisations", json={"mandate": mandate(**{field: value})})
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "INVALID_MANDATE"
    assert error["detail"]["field"] == field


async def test_an_inverted_cod_window_is_400(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/optimisations", json={"mandate": mandate(codFrom=2032, codTo=2027)}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_MANDATE"


async def test_an_unknown_run_is_404(client: httpx.AsyncClient) -> None:
    for path in ("/optimisations/01JB2QNOTAREALRUNIDXXXXXXX", "/optimisations/nonsense"):
        response = await client.get(path)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "RUN_NOT_FOUND"


async def test_the_result_carries_everything_the_portfolio_screen_needs(
    client: httpx.AsyncClient,
) -> None:
    accepted = await start_run(client)
    body = (await client.get(f"/optimisations/{accepted['runId']}")).json()
    assert body["status"] == "succeeded"
    assert body["runRef"] == accepted["runRef"]
    assert len(body["cashflow30Y_m"]) == YEARS
    assert len(body["cashflowHold_m"]) == body["mandate"]["holdYears"]
    assert body["convergence"], "no convergence curve"
    assert body["holdings"], "no candidate snapshot"
    assert [holding["id"] for holding in body["holdings"] if holding["selected"]] == body[
        "selectedIds"
    ]
    # §8.2: the holdings carry the whole eligible set, not only the winners.
    assert len(body["holdings"]) > len(body["selectedIds"])
    # §7.3's map needs coordinates; nothing else on the result carries them.
    assert all("lat" in holding and "lon" in holding for holding in body["holdings"])


async def test_the_two_cash_flow_series_are_distinct(client: httpx.AsyncClient) -> None:
    """A-6, and the single most likely silent bug in the feature.

    ``cashflowHold_m`` carries the terminal value in its last element;
    ``cashflow30Y_m`` carries none. Neither is a slice of the other, so the
    overlapping years agree until the exit year and diverge there.
    """
    accepted = await start_run(client)
    body = (await client.get(f"/optimisations/{accepted['runId']}")).json()
    thirty = body["cashflow30Y_m"]
    hold = body["cashflowHold_m"]
    assert thirty[: len(hold) - 1] == pytest.approx(hold[:-1])
    assert hold[-1] != pytest.approx(thirty[len(hold) - 1])
    assert hold[-1] > thirty[len(hold) - 1], "the terminal value should lift the exit year"
    assert body["aggregates"]["thirtyYearFcfe_m"] == pytest.approx(sum(thirty))


async def test_a_run_id_reopens_byte_identical_bytes(client: httpx.AsyncClient) -> None:
    """§11: runs are immutable and addressable."""
    accepted = await start_run(client)
    first = await client.get(f"/optimisations/{accepted['runId']}")
    second = await client.get(f"/optimisations/{accepted['runId']}")
    assert first.content == second.content
    assert "immutable" in first.headers["Cache-Control"]


async def test_the_same_seed_reproduces_the_result_exactly(client: httpx.AsyncClient) -> None:
    """§12: mandate + pipeline hash + assumption set + seed determines the result."""
    first = await start_run(client, seed=1234)
    second = await start_run(client, seed=1234)
    one = (await client.get(f"/optimisations/{first['runId']}")).json()
    two = (await client.get(f"/optimisations/{second['runId']}")).json()
    assert one["selectedIds"] == two["selectedIds"]
    assert one["aggregates"] == two["aggregates"]
    assert one["cashflow30Y_m"] == two["cashflow30Y_m"]
    assert one["convergence"] == two["convergence"]


async def test_a_different_seed_is_allowed_to_differ(client: httpx.AsyncClient) -> None:
    """The counterpart to the reproducibility test: the seed is doing something."""
    one = (await client.get(f"/optimisations/{(await start_run(client, seed=1))['runId']}")).json()
    two = (await client.get(f"/optimisations/{(await start_run(client, seed=2))['runId']}")).json()
    assert one["provenance"]["seed"] != two["provenance"]["seed"]
    assert one["convergence"] != two["convergence"]


async def test_the_exports_match_the_stored_result(client: httpx.AsyncClient) -> None:
    accepted = await start_run(client)
    run_id = accepted["runId"]
    body = (await client.get(f"/optimisations/{run_id}")).json()

    holdings = await client.get(f"/optimisations/{run_id}/holdings.csv")
    assert holdings.status_code == 200
    assert holdings.headers["content-type"].startswith("text/csv")
    text = holdings.content.decode("utf-8-sig")
    rows = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert len(rows) - 1 == len(body["selectedIds"])

    cashflow = await client.get(f"/optimisations/{run_id}/cashflow.csv")
    lines = [
        line
        for line in cashflow.content.decode("utf-8-sig").splitlines()
        if line and not line.startswith("#")
    ]
    assert len(lines) - 1 == YEARS
