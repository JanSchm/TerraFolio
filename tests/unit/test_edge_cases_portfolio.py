"""The six §13 rows a user meets on the portfolio screen (issue #13).

Rows 3 to 7 and row 9 of `docs/spec.md` §13. The user-visible half is asserted
against the **committee pack** — `src/terrafolio/export/committee.py`, served at
``GET /optimisations/{id}/pack`` — because it is the one surface that renders
§5.1's twelve tiles, the holdings table and the map today, server-side and with no
JavaScript, and because a committee reading a printed pack is exactly the audience
§13 is protecting.

Every tile carries its verdict as a mark and a screen-reader word as well as a
tone (§7.1, A-10), so "surfaced in the alert colour" is assertable without reading
a colour.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from test_edge_cases_support import (
    assumptions,
    completed_run,
    golden_load,
    mandate,
    opened_client,
    opened_service,
    settings_for,
    stored_run,
)

from terrafolio.economics.returns import project_returns, thirty_year_fcfe
from terrafolio.export.committee import EM_DASH, SEPARATOR, committee_pack
from terrafolio.optimiser.aggregate import aggregate
from terrafolio.optimiser.features import COLUMN, build_features

ASSUMPTIONS = assumptions()

SHORT_HOLD = 5
"""The shortest hold §5.1 allows. Exit year 2027 + 5 - 1 = 2031."""

LATE_COD = "P45"
"""A 2032 commercial operation date — after the exit year of a five-year hold."""

ALWAYS_DEFINED = "P01"
"""A 2028 COD, so its IRR is defined at every hold the mandate allows."""


def _pack(service: Any, settings: Any, run_id: str, *, atlas: Path | None = None) -> str:
    return committee_pack(
        stored_run(service, run_id),
        stylesheet=settings.stylesheet_path,
        fonts_dir=settings.fonts_dir,
        atlas=settings.atlas_path if atlas is None else atlas,
    ).decode("utf-8")


def _tile(document: str, label: str) -> str:
    """One §5.1 tile's markup, from its label to the end of its cell."""
    opened = document.index(f">{label}<")
    start = document.rindex('<div class="tile', 0, opened)
    return document[start : document.index("</div>", opened) + len("</div>")]


# ---------------------------------------------------------------------------
# locks-breach-a-concentration-cap
# ---------------------------------------------------------------------------


async def test_locked_projects_breaching_a_concentration_cap_still_run_and_show_the_breach(
    tmp_path: Path,
) -> None:
    """§13 row 3: the run proceeds; the breach is surfaced rather than hidden.

    A lock is a user instruction that outranks a portfolio-level cap (2A-20), so
    the answer to "this portfolio is 27% in one country against a 25% cap" is to
    show it, not to drop the lock and quietly return something else.

    The same mandate without the locks stays inside the cap, which is what makes
    this a test of the locks rather than of the corpus.
    """
    settings = settings_for(tmp_path)
    constrained = mandate(maxCountryShare=0.25, availableCapital_m=250)
    locked = ["P01", "P02"]

    with opened_service(settings) as service:
        async with opened_client(settings, service) as client:
            unlocked = await completed_run(client, mandate=constrained)
            assert unlocked["aggregates"]["largestCountryShare"] <= 0.25, (
                "without the locks the optimiser respects the cap"
            )

            result = await completed_run(client, mandate=constrained, lockedIds=locked)

    assert result["status"] == "succeeded", "§13 row 3: the run proceeds"
    for project_id in locked:
        assert project_id in result["selectedIds"]

    share = result["aggregates"]["largestCountryShare"]
    assert share > 0.25, "the locks alone put one country over the cap"
    assert result["aggregates"]["largestCountryCode"] == "ES"

    with opened_service(settings) as service:
        async with opened_client(settings, service) as client:
            replayed = await completed_run(client, mandate=constrained, lockedIds=locked)
            document = _pack(service, settings, replayed["runId"])

    tile = _tile(document, "Largest country")
    assert "tile--alert" in tile, "surfaced in the alert tone"
    assert "outside the mandate" in tile, "and in a word, never by colour alone (§7.1)"
    assert "cap 25%" in tile, "beside the cap it breaches"


# ---------------------------------------------------------------------------
# capacity-target-unreachable
# ---------------------------------------------------------------------------


async def test_an_unreachable_capacity_target_still_returns_a_portfolio_showing_the_shortfall(
    tmp_path: Path,
) -> None:
    """§13 row 4: the run returns the best feasible portfolio; the tile shows the shortfall.

    The shortfall is the point. An alert tone says the target was missed; a
    committee's next question is by how much, and before this test the pack's
    sub-label answered it with the target it had already missed.
    """
    settings = settings_for(tmp_path)
    unreachable = mandate(capacityTargetMw=4000, availableCapital_m=200)

    with opened_service(settings) as service:
        async with opened_client(settings, service) as client:
            result = await completed_run(client, mandate=unreachable)
            document = _pack(service, settings, result["runId"])

    assert result["status"] == "succeeded"
    assert result["selectedIds"], "a best feasible portfolio, not an empty one"
    capacity = result["aggregates"]["capacityMw"]
    assert capacity < 4000

    tile = _tile(document, "Installed capacity")
    assert "tile--alert" in tile
    assert "outside the mandate" in tile
    assert "target 4,000 MW" in tile
    assert f"{round(4000 - capacity):,} MW short" in tile, (
        "ui-contract.md §5.1: the sub-label carries the shortfall against target"
    )


async def test_a_capacity_target_that_is_met_keeps_the_sub_label_it_always_had(
    tmp_path: Path,
) -> None:
    """The other side of the same change: a portfolio on target gains no clause."""
    settings = settings_for(tmp_path)
    with opened_service(settings) as service:
        async with opened_client(settings, service) as client:
            result = await completed_run(client, mandate=mandate(capacityTargetMw=200))
            document = _pack(service, settings, result["runId"])

    assert result["aggregates"]["capacityMw"] > 200
    tile = _tile(document, "Installed capacity")
    assert "target 200 MW" in tile
    assert "short" not in tile


# ---------------------------------------------------------------------------
# irr-undefined
# ---------------------------------------------------------------------------


async def test_an_undefined_irr_renders_an_em_dash_and_is_left_out_of_the_weighted_average(
    tmp_path: Path,
) -> None:
    """§13 row 5: an em dash, never zero, and excluded from every weighted average.

    Displaying a dash is the easy half. The half that silently goes wrong is the
    blend: a project with no IRR must leave **both** sides of the weighted average
    — numerator and denominator — so that adding it to a portfolio cannot move the
    reported return at all. A blend that treated it as zero would drag the number
    down and look plausible doing it.
    """
    arrays = golden_load().arrays
    index = {project_id: position for position, project_id in enumerate(arrays.ids)}
    returns = project_returns(arrays, ASSUMPTIONS, SHORT_HOLD)

    late = index[LATE_COD]
    defined = index[ALWAYS_DEFINED]
    assert not bool(returns.defined[late]), f"{LATE_COD} has no sign change at a {SHORT_HOLD}y hold"
    assert np.isnan(returns.equity_irr[late]), "undefined is NaN, never 0.0"
    assert bool(returns.defined[defined])

    features = build_features(
        arrays,
        equity_irr=np.nan_to_num(returns.equity_irr),
        irr_defined=returns.defined,
        merchant_share=1.0 - arrays.revenue.ppa_share,
    )
    assert features.fit[late, COLUMN["equity_weighted_irr"]] == 0.0
    assert features.fit[late, COLUMN["equity_with_defined_irr"]] == 0.0, (
        "cleared from the denominator too, which is what 'excluded' has to mean"
    )

    alone = np.zeros((1, len(arrays.ids)))
    alone[0, defined] = 1.0
    both = alone.copy()
    both[0, late] = 1.0
    only_undefined = np.zeros((1, len(arrays.ids)))
    only_undefined[0, late] = 1.0

    assert aggregate(features, both).blended_irr[0] == aggregate(features, alone).blended_irr[0], (
        "adding a project with no IRR does not move the blend by a single bit"
    )
    assert np.isnan(aggregate(features, only_undefined).blended_irr[0]), (
        "a portfolio where nothing has an IRR has none either — never 0.0"
    )

    # And the user-visible half, on the row a committee reads.
    settings = settings_for(tmp_path)
    with opened_service(settings) as service:
        async with opened_client(settings, service) as client:
            result = await completed_run(
                client, mandate=mandate(holdYears=SHORT_HOLD), lockedIds=[LATE_COD]
            )
            document = _pack(service, settings, result["runId"])

    holding = next(row for row in result["holdings"] if row["id"] == LATE_COD)
    assert holding["selected"] is True
    assert holding["equityIrr"] is None, "NaN became null at the pydantic boundary (C-12)"

    row = _holdings_row(document, LATE_COD)
    assert EM_DASH in row
    assert "0.0%" not in row, "never coerced to zero"


def _holdings_row(document: str, project_id: str) -> str:
    """The pack's holdings row for one project.

    Anchored on the row's own meta line — ``Spain · P01`` — rather than on the id
    alone, which also occurs in the provenance block's file hashes.
    """
    at = document.index(f"{SEPARATOR}{project_id}</span>")
    start = document.rindex("<tr>", 0, at)
    return document[start : document.index("</tr>", at) + len("</tr>")]


# ---------------------------------------------------------------------------
# already-operating-in-the-base-year
# ---------------------------------------------------------------------------


OPERATING = "P03"
"""A 2027 commercial operation date against a 2027 base year."""


async def test_a_project_operating_in_the_base_year_books_its_equity_in_year_one(
    tmp_path: Path,
) -> None:
    """§13 row 6: the whole equity outflow in year one, no construction draw-down.

    `pipeline-schema.md` §6's third row. The loader accepts such a file — there is
    nothing wrong with it — and the §7.5 funding tie-out holds by construction,
    which is what stops "already operating" from being a special case anywhere
    downstream.
    """
    loaded = golden_load()
    arrays = loaded.arrays
    position = arrays.ids.index(OPERATING)

    assert arrays.asset.cod_year[position] == arrays.base_year
    assert loaded.rejected == (), "the loader accepts it"

    cash_flow = arrays.statements.cash_flow
    equity = arrays.capital.equity[position]
    assert cash_flow.equity_drawdown[position, 0] == pytest.approx(equity)
    assert cash_flow.equity_drawdown[position, 1:].sum() == 0.0, "no construction draw-down"
    assert cash_flow.capex[position, 0] == pytest.approx(arrays.capital.total_capex[position])
    assert cash_flow.capex[position, 1:].sum() == 0.0

    # The user-visible half: the statements the detail sheet reads, and the year
    # the pack prints beside the holding.
    settings = settings_for(tmp_path)
    with opened_service(settings) as service:
        async with opened_client(settings, service) as client:
            statements = await client.get(f"/projects/{OPERATING}/statements")
            assert statements.status_code == 200, statements.text
            body = statements.json()
            assert len(body["years"]) == 30
            assert body["cashFlow"]["equityDrawdown"][0] > 0.0
            assert sum(body["cashFlow"]["equityDrawdown"][1:]) == pytest.approx(0.0)

            result = await completed_run(client, lockedIds=[OPERATING])
            document = _pack(service, settings, result["runId"])

    assert f">{arrays.base_year}<" in _holdings_row(document, OPERATING), (
        "the COD cell prints the base year"
    )


# ---------------------------------------------------------------------------
# hold-shorter-than-the-last-cod
# ---------------------------------------------------------------------------


async def test_a_project_whose_cod_falls_after_the_hold_contributes_outflows_and_an_exit_only(
    tmp_path: Path,
) -> None:
    """§13 row 7: only construction outflows and an exit value.

    This is the case most likely to conflate the two cash-flow series, so the
    assertion that matters is that they still disagree: the 30-year series carries
    no terminal value and runs past the exit, while the hold-truncated one stops
    at the exit and carries it (A-6).

    The exit value here is exactly zero. At the exit year the asset has no EBITDA
    and outstanding debt, so the terminal value floors at zero under limited
    liability — §13's "an exit value" is satisfied by a zero one, and the model is
    not bent to avoid it (4C-4).
    """
    arrays = golden_load().arrays
    position = arrays.ids.index(LATE_COD)
    returns = project_returns(arrays, ASSUMPTIONS, SHORT_HOLD)
    exit_year = arrays.base_year + SHORT_HOLD - 1

    assert arrays.asset.cod_year[position] > exit_year
    series = returns.series[position]
    assert len(series) == SHORT_HOLD
    assert (series <= 0.0).all(), "construction outflows, and nothing else"
    assert returns.terminal[position] == 0.0
    assert np.isnan(returns.payback[position]), "nothing is ever paid back"

    thirty_year = thirty_year_fcfe(arrays)[position]
    assert thirty_year != pytest.approx(series.sum()), (
        "the 30-year series and the hold-truncated one are never the same number"
    )
    assert thirty_year > series.sum(), "the years after the exit are the profitable ones"

    # The user-visible half: what a page needs to say so. No wire flag is added —
    # the drawer computes it from three figures it already has (4C-5).
    settings = settings_for(tmp_path)
    with opened_service(settings) as service:
        async with opened_client(settings, service) as client:
            listed = await client.get(f"/pipeline?holdYears={SHORT_HOLD}")
            assert listed.status_code == 200, listed.text
            body = listed.json()
            assert body["baseYear"] == arrays.base_year
            assert body["holdYears"] == SHORT_HOLD
            project = next(row for row in body["projects"] if row["id"] == LATE_COD)
            assert project["codYear"] > body["baseYear"] + SHORT_HOLD - 1
            assert project["equityIrr"] is None


# ---------------------------------------------------------------------------
# map-geometry-unavailable
# ---------------------------------------------------------------------------


async def test_missing_map_geometry_degrades_to_a_notice_and_leaves_the_rest_of_the_page(
    tmp_path: Path,
) -> None:
    """§13 row 9: the map panel degrades to a notice; the rest of the page is unaffected.

    ``tests/api/test_committee_pack.py`` already asserts the notice and the twelve
    tiles. The clause nothing checked is the wider one — that the two charts and
    every holdings row survive a missing atlas, which is what "the rest of the
    page" means to somebody holding the printout.
    """
    settings = settings_for(tmp_path)
    with opened_service(settings) as service:
        async with opened_client(settings, service) as client:
            result = await completed_run(client)
            served = await client.get(f"/optimisations/{result['runId']}/pack")
            assert served.status_code == 200, "the atlas is present in a checkout"

            document = _pack(service, settings, result["runId"], atlas=tmp_path / "absent.json")

    assert "Map data unavailable." in document
    assert "<circle" not in document, "no markers without geometry"

    assert document.count('class="tile tile--') == 12
    assert document.count("<rect") == 30, "the thirty cash-flow bars"
    assert document.count("<polyline") == 2, "both convergence curves"
    for heading in ("Holdings", "Mandate", "Provenance"):
        assert f">{heading}<" in document
    for project_id in result["selectedIds"]:
        assert _holdings_row(document, project_id), "every selected project still has its row"
