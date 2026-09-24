"""Write the committed (pipeline, mandate) pairs the parity harness reads.

Run from the repository root when a pair is added or the wire shape changes::

    uv run python tests/parity/generate_pairs.py

The pairs are generated rather than hand-written for one reason: three of them have to
sit *exactly* on a threshold — one candidate passing, a solar target exactly 20 points
from the pool's mix, capital absorption exactly 90% — and those values are properties
of the pool, not numbers anybody can type. The generator solves for them and records
what it solved for, so a change to the golden fixtures moves the pairs instead of
silently moving them off their boundaries.

Two pipelines, so that "pipeline" is a real variable and not a constant: 1C's 48 golden
files, and a three-project subset small enough to reason about and to pin a boundary on.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Final

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "perf")]

from pool import GOLDEN_PIPELINE  # noqa: E402
from wire import DEFAULT_MANDATE, mandate, pipeline_payload  # noqa: E402

from terrafolio.config.assumptions import AssumptionSet  # noqa: E402
from terrafolio.config.loader import load_default  # noqa: E402
from terrafolio.domain.mandate import Mandate  # noqa: E402
from terrafolio.domain.reduce import mandate_to_scalars  # noqa: E402
from terrafolio.optimiser.feasibility import preview_feasibility  # noqa: E402
from terrafolio.pipeline.loader import LoadResult, load_pipeline  # noqa: E402

HOLD_YEARS: Final = 10
PAIRS_DIR: Final = _HERE / "pairs"

BASE: Final = DEFAULT_MANDATE


def _eligible(
    payload: dict, mandate_json: dict, loaded: LoadResult, assumptions: AssumptionSet
) -> list[dict]:
    """The projects ``mandate_json`` admits, screened by the engine itself.

    This used to apply five of the nine screens inline, which made it a third
    implementation of the very logic this suite exists to prove there are only two of —
    and a dangerous one, because the three boundary pairs are *solved from its output*.
    A divergence between it and ``apply_screens`` would move those pairs off the
    boundaries they are named for while every assertion still passed: the suite would
    report green having stopped testing any boundary.

    So it calls ``preview_feasibility`` and maps the eligible ids back onto the payload
    records, which are what the boundaries have to be expressed in (€m, not euros).
    """
    scalars = mandate_to_scalars(Mandate.model_validate(mandate_json))
    preview = preview_feasibility(loaded.arrays, scalars, assumptions)
    eligible = {
        project_id
        for index, project_id in enumerate(loaded.arrays.ids)
        if bool(preview.screens.eligible[index])
    }
    return [row for row in payload["projects"] if row["id"] in eligible]


def _pool_figures(
    payload: dict, mandate_json: dict, loaded: LoadResult, assumptions: AssumptionSet
) -> dict[str, float]:
    """The footer figures for whatever ``mandate_json`` admits, in the mandate's units."""
    pool = _eligible(payload, mandate_json, loaded, assumptions)
    capacity = sum(project["capacityMw"] for project in pool)
    solar = sum(row["capacityMw"] for row in pool if row["technology"] == "solar")
    return {
        "count": float(len(pool)),
        "capacityMw": capacity,
        "equity_m": sum(project["equity_m"] for project in pool),
        "solarShare": solar / capacity if capacity > 0 else float("nan"),
    }


def _write(name: str, pair: dict[str, Any]) -> None:
    path = PAIRS_DIR / f"{name}.json"
    path.write_text(json.dumps(pair, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    assumptions = load_default()
    PAIRS_DIR.mkdir(parents=True, exist_ok=True)

    golden = load_pipeline(GOLDEN_PIPELINE, assumptions)
    payload = pipeline_payload(golden, assumptions, hold_years=HOLD_YEARS)
    (PAIRS_DIR / "payload-golden48.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    ids = sorted(project["id"] for project in payload["projects"])
    by_id = {project["id"]: project for project in payload["projects"]}
    figures = _pool_figures(payload, BASE, golden, assumptions)

    # A three-project subset, written as its own pipeline so that the pairs below vary
    # the pipeline and not only the mandate.
    # The three largest projects that the base mandate actually admits, so the trio's
    # own capacity target lands inside §5.2's 200-4,000 MW range.
    admitted = _eligible(payload, BASE, golden, assumptions)
    biggest = sorted(admitted, key=lambda row: -row["capacityMw"])[:3]
    trio_ids = sorted(row["id"] for row in biggest)
    trio = dict(payload)
    trio["projects"] = [by_id[project_id] for project_id in trio_ids]
    trio["projectCount"] = len(trio_ids)
    (PAIRS_DIR / "payload-trio.json").write_text(
        json.dumps(trio, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    trio_figures = _pool_figures(trio, BASE, golden, assumptions)

    # "Exactly one candidate passes" has to be solved for, not typed. No COD year and
    # no country in the golden 48 is held by a single project, but the highest minimum
    # DSCR is unique — so a floor set to exactly that value admits exactly one. (A null
    # DSCR passes the screen unconditionally, A-21; there are none here, and the
    # assertion below says so rather than assuming it.)
    # "Exactly one candidate passes" has to be solved for, not typed, and it has to be
    # solved for inside the mandate's own bounds — `minDscr` stops at 2.00, and five of
    # the 48 clear that. A (country, stage) pair held by exactly one project does it
    # with two controls a user can actually set.
    by_country_stage: dict[tuple[str, str], list[str]] = {}
    for row in payload["projects"]:
        by_country_stage.setdefault((row["countryCode"], row["stage"]), []).append(row["id"])
    lonely_key, lonely = next(
        (key, held) for key, held in sorted(by_country_stage.items()) if len(held) == 1
    )

    # A COD window inside the slider's range that no project sits in.
    cod_years = {row["codYear"] for row in payload["projects"]}
    empty_year = next(year for year in range(2033, 2026, -1) if year not in cod_years)
    expensive = sorted(payload["projects"], key=lambda row: -row["equity_m"])[:3]
    dearest = sorted(row["id"] for row in expensive)
    non_eur = sorted(row["id"] for row in payload["projects"] if row["currency"] != "EUR")
    no_grid = sorted(row["id"] for row in payload["projects"] if not row["gridSecured"])
    screened_out = no_grid[0] if no_grid else ids[0]

    pairs: dict[str, dict[str, Any]] = {
        # --- the nine screens, one at a time -------------------------------------
        "01-baseline": {
            "why": "Everything passes. The control: if this diverges, nothing else means much.",
            "payload": "payload-golden48.json",
            "mandate": mandate(),
        },
        "02-screen-countries": {
            "why": "One country only, so the country screen is the sole binding one.",
            "payload": "payload-golden48.json",
            "mandate": mandate(countries=["ES"]),
        },
        "03-screen-countries-uk-alias": {
            "why": (
                "The mandate says UK; the pipeline says GB. Python normalises both ends"
                " and feasibility.js compared raw strings, so this pair is the "
                "regression test for that fix."
            ),
            "payload": "payload-golden48.json",
            "mandate": mandate(countries=["UK", "IE"]),
        },
        "04-screen-stages": {
            "why": "Ready-to-build only.",
            "payload": "payload-golden48.json",
            "mandate": mandate(stages=["ready_to_build"]),
        },
        "05-screen-cod-window": {
            "why": "A two-year COD window inside the pool's range.",
            "payload": "payload-golden48.json",
            "mandate": mandate(codFrom=2029, codTo=2030),
        },
        "06-screen-min-dscr": {
            "why": "A DSCR floor above most of the pool. Null DSCR passes (A-21).",
            "payload": "payload-golden48.json",
            "mandate": mandate(minDscr=1.45),
        },
        "07-screen-grid-secured": {
            "why": "Grid-secured only, which drops the unconnected projects.",
            "payload": "payload-golden48.json",
            "mandate": mandate(gridSecuredOnly=True),
        },
        "08-screen-eur-revenue": {
            "why": f"EUR revenue only. {len(non_eur)} of 48 are denominated otherwise.",
            "payload": "payload-golden48.json",
            "mandate": mandate(eurRevenueOnly=True),
        },
        "09-screen-om-contracted": {
            "why": "O&M contracted only.",
            "payload": "payload-golden48.json",
            "mandate": mandate(omContractedOnly=True),
        },
        "10-screen-risk-low": {
            "why": "The low risk appetite, which is the tightest project risk cap.",
            "payload": "payload-golden48.json",
            "mandate": mandate(riskAppetite="low"),
        },
        "11-screen-exclusions": {
            "why": "Three projects excluded by hand.",
            "payload": "payload-golden48.json",
            "mandate": mandate(),
            "locks": {"lockedIds": [], "excludedIds": ids[:3]},
        },
        "12-screens-all-on": {
            "why": "Grid, O&M and EUR-only together, with the low risk cap.",
            "payload": "payload-golden48.json",
            "mandate": mandate(
                gridSecuredOnly=True,
                omContractedOnly=True,
                eurRevenueOnly=True,
                riskAppetite="low",
            ),
        },
        # --- the seven warnings ---------------------------------------------------
        "13-warn-no-candidates": {
            "why": (
                f"NO_CANDIDATES, blocking: COD {empty_year} only, which no project meets."
                " The year is inside §5.2's slider range, so this is a mandate a user"
                " can actually state."
            ),
            "payload": "payload-golden48.json",
            "mandate": mandate(codFrom=empty_year, codTo=empty_year),
        },
        "14-warn-no-candidates-by-exclusion": {
            "why": "NO_CANDIDATES reached by excluding every project, not by a screen.",
            "payload": "payload-trio.json",
            "mandate": mandate(),
            "locks": {"lockedIds": [], "excludedIds": trio_ids},
        },
        "15-warn-locks-exceed-capital": {
            "why": (
                "LOCKS_EXCEED_CAPITAL, blocking: the three dearest against the smallest"
                " capital §5.2 allows."
            ),
            "payload": "payload-golden48.json",
            "mandate": mandate(availableCapital_m=200.0),
            "locks": {"lockedIds": dearest, "excludedIds": []},
        },
        "16-warn-capacity-below-target": {
            "why": (
                f"CAPACITY_BELOW_TARGET: the slider's ceiling, 4,000 MW, against a"
                f" {figures['capacityMw']:,.0f} MW pool."
            ),
            "payload": "payload-golden48.json",
            "mandate": mandate(capacityTargetMw=4000.0),
        },
        "17-warn-leverage-unreachable": {
            "why": (
                "LEVERAGE_UNREACHABLE: the slider's ceiling, 85%, against a pool"
                " the files gear at about 65%."
            ),
            "payload": "payload-golden48.json",
            "mandate": mandate(minLeverage=0.85),
        },
        "18-warn-solar-mix-unreachable": {
            "why": "SOLAR_MIX_UNREACHABLE: a solar target far above the pool's mix.",
            "payload": "payload-golden48.json",
            "mandate": mandate(solarShare=1.0),
        },
        "19-warn-capital-underused": {
            "why": (
                f"CAPITAL_UNDERUSED: the slider's ceiling, €4,000m, against a pool that"
                f" absorbs €{figures['equity_m']:,.0f}m."
            ),
            "payload": "payload-golden48.json",
            "mandate": mandate(availableCapital_m=4000.0),
        },
        "20-warn-locks-present": {
            "why": "LOCKS_PRESENT: two locked, one excluded, all of them eligible.",
            "payload": "payload-golden48.json",
            "mandate": mandate(),
            "locks": {"lockedIds": ids[:2], "excludedIds": [ids[5]]},
        },
        "21-warn-every-advisory-at-once": {
            "why": "All four advisory warnings together, on a pool of three.",
            "payload": "payload-trio.json",
            "mandate": mandate(
                capacityTargetMw=4000.0,
                minLeverage=0.85,
                solarShare=1.0,
                availableCapital_m=4000.0,
            ),
        },
        # --- locks, which is where the two implementations disagreed --------------
        "22-lock-readmits-a-screened-out-project": {
            "why": (
                "A lock on a project the grid screen drops. Python re-admits it;"
                " feasibility.js dropped it, so the footer promised a count the run"
                " would not deliver. The fix's regression test."
            ),
            "payload": "payload-golden48.json",
            "mandate": mandate(gridSecuredOnly=True),
            "locks": {"lockedIds": [screened_out], "excludedIds": []},
        },
        "23-lock-and-exclusion-on-one-id": {
            "why": "Exclusion wins over a lock, and lockedEquity must not count it.",
            "payload": "payload-golden48.json",
            "mandate": mandate(),
            "locks": {"lockedIds": ids[:2], "excludedIds": [ids[0]]},
        },
        "24-lock-every-project": {
            "why": "Everything locked, so nothing can be screened out.",
            "payload": "payload-trio.json",
            "mandate": mandate(countries=["ES"]),
            "locks": {"lockedIds": trio_ids, "excludedIds": []},
        },
        # --- the three boundaries -------------------------------------------------
        "25-boundary-exactly-one-candidate": {
            "why": (
                f"{lonely_key[0]} plus {lonely_key[1]} is held by exactly one project,"
                f" {lonely[0]}. A screen that admits exactly one is where an off-by-one"
                " hides, and both sides have to agree which one it is."
            ),
            "payload": "payload-golden48.json",
            "mandate": mandate(countries=[lonely_key[0]], stages=[lonely_key[1]]),
        },
        "26-boundary-solar-exactly-twenty-points": {
            "why": (
                "The solar target sits exactly 20 points above the pool's mix, which is"
                " the tolerance itself, so SOLAR_MIX_UNREACHABLE must *not* fire."
                f" Pool mix {figures['solarShare']:.17g}."
            ),
            "payload": "payload-golden48.json",
            "mandate": mandate(solarShare=float(figures["solarShare"]) + 0.20),
        },
        "27-boundary-capital-absorption-exactly-ninety": {
            "why": (
                "Capital chosen so the eligible pool absorbs exactly 90% of it, which"
                " is the floor itself, so CAPITAL_UNDERUSED must *not* fire."
                f" Pool equity €{figures['equity_m']:.17g}m."
            ),
            "payload": "payload-golden48.json",
            "mandate": mandate(availableCapital_m=float(figures["equity_m"]) / 0.90),
        },
        "28-boundary-locks-exactly-at-capital": {
            "why": (
                "Locked equity exactly equals available capital, so"
                " LOCKS_EXCEED_CAPITAL must not fire: the test is strictly greater."
            ),
            "payload": "payload-golden48.json",
            "mandate": mandate(availableCapital_m=float(sum(row["equity_m"] for row in expensive))),
            "locks": {"lockedIds": dearest, "excludedIds": []},
        },
        "29-boundary-capacity-exactly-on-target": {
            "why": "A capacity target exactly equal to the pool's, so the test is strict.",
            "payload": "payload-golden48.json",
            "mandate": mandate(capacityTargetMw=float(figures["capacityMw"])),
        },
        "30-boundary-trio-capacity-exactly-on-target": {
            "why": "The same strictness on a pool of three, where one project dominates.",
            "payload": "payload-trio.json",
            "mandate": mandate(capacityTargetMw=float(trio_figures["capacityMw"])),
        },
    }

    for name, pair in pairs.items():
        pair.setdefault("locks", {"lockedIds": [], "excludedIds": []})
        _write(name, pair)

    print(f"wrote {len(pairs)} pairs and 2 payloads to {PAIRS_DIR}")
    print(f"pool: {int(figures['count'])} of 48 eligible under the base mandate")
    print(f"  capacity {figures['capacityMw']:,.1f} MW, equity €{figures['equity_m']:,.2f}m")
    print(f"  solar share {figures['solarShare']:.17g}")
    print(f"  one candidate: {lonely_key} -> {lonely[0]}; empty COD year {empty_year}")
    print(f"  trio {trio_ids} at {trio_figures['capacityMw']:,.0f} MW")
    print(f"  non-EUR {len(non_eur)}, ungridded {len(no_grid)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
