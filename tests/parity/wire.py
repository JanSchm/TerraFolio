"""The one wire shape both implementations read, and the one view both produce.

#12's screen parity rests on the two sides being fed identical bytes. If the Python
test built ``ProjectArrays`` from files and the node test built plain objects from the
same files, the harness would be comparing two adapters as much as two implementations
— and an adapter bug would read as parity.

So a pair's payload is generated **once**, from ``api/scalars.py``, which is the real
``GET /pipeline`` serialiser, and committed. Both sides then read the same JSON.

One wart, raised rather than papered over: ``feasibility.js`` reads
``payload.assumptions.riskCaps``, and ``GET /pipeline`` does not serve an ``assumptions``
block at all — the caps live behind ``GET /assumptions``. Every existing JS test hand
builds the caps, so the page as shipped would throw on a real payload. The generated
payload carries the caps from the loaded assumption set so the harness can run, and the
gap is on the issue for #11.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import numpy as np

from terrafolio.api.scalars import Candidates, DerivedColumns, project_scalars
from terrafolio.config.assumptions import AssumptionSet
from terrafolio.domain.enums import RiskAppetite
from terrafolio.economics.returns import project_returns
from terrafolio.optimiser.feasibility import FeasibilityPreview
from terrafolio.optimiser.screens import SCREEN_NAMES
from terrafolio.pipeline.loader import LoadResult

__all__ = [
    "CONTINUOUS",
    "JS_SCREEN_NAMES",
    "parity_view",
    "pipeline_payload",
]

EUR_PER_M: Final = 1_000_000.0

JS_SCREEN_NAMES: Final[Mapping[str, str]] = {
    "country": "countries",
    "stage": "stages",
    "codWindow": "codWindow",
    "minDscr": "minDscr",
    "riskCap": "riskScore",
    "gridSecured": "gridSecured",
    "omContracted": "omContracted",
    "currency": "eurRevenue",
    "notExcluded": "exclusions",
}
"""``feasibility.js``'s screen names, mapped onto the ones on the wire.

Five of the nine differ in spelling — ``country``/``countries``, ``stage``/``stages``,
``riskCap``/``riskScore``, ``currency``/``eurRevenue``, ``notExcluded``/``exclusions``
— and the risk screen sits fifth in the JavaScript order and eighth in the Python one.
``docs/api.md`` §5's ``screensToWiden`` uses the Python names, and ``feasibility.js``
does not expose ``screensToWiden`` at all, so the JavaScript names are internal and the
map lives here rather than in a rename that would break 1D's own tests. Raised for #11
so the two vocabularies can converge in one place later (docs/decisions.md 4B-5).
"""

CONTINUOUS: Final = (
    "eligibleCapacityMw",
    "eligibleEquity_m",
    "eligibleSolarShare",
    "eligibleGearing",
    "lockedEquity_m",
)
"""The figures compared within a tolerance rather than exactly.

Python aggregates in euros and converts at the API boundary; ``feasibility.js``
aggregates in €m because that is what the wire carries. ``(x·10^6)/(y·10^6)`` is not
required to equal ``x/y`` to the last bit, so demanding exact equality here would be
demanding something floating point does not offer. Everything else — counts, codes,
flags, screen names — is compared exactly.
"""


def _risk_caps(assumptions: AssumptionSet) -> dict[str, float]:
    return {
        appetite.value: float(assumptions.risk_caps.project[appetite]) for appetite in RiskAppetite
    }


def pipeline_payload(loaded: LoadResult, assumptions: AssumptionSet, *, hold_years: int) -> dict:
    """A ``GET /pipeline`` body for ``loaded``, plus the risk caps the page needs."""
    candidates = Candidates(
        arrays=loaded.arrays,
        files=loaded.files,
        derived=DerivedColumns(loaded.arrays, assumptions),
    )
    returns = project_returns(loaded.arrays, assumptions, hold_years)
    projects = project_scalars(candidates, returns)
    return {
        "pipelineHash": loaded.pipeline_hash,
        "assumptionSetId": assumptions.assumption_set_id,
        "baseYear": loaded.arrays.base_year,
        "holdYears": hold_years,
        "projectCount": len(projects),
        "projects": [json.loads(project.model_dump_json()) for project in projects],
        "assumptions": {"riskCaps": _risk_caps(assumptions)},
    }


def parity_view(preview: FeasibilityPreview, *, ids: Sequence[str]) -> dict[str, Any]:
    """The Python side's answer, in the shape the node harness also emits.

    Only what both implementations claim to compute. ``detail`` payloads and rendered
    message strings are deliberately absent: the Python core emits no strings at all
    (they live in ``api/messages.py``) and the two ``detail`` vocabularies differ in
    both units and keys, so comparing them would be comparing two conventions rather
    than two answers.
    """
    screens = preview.screens
    failed = {
        project_id: [name for name in SCREEN_NAMES if not bool(screens.passes[name][index])]
        for index, project_id in enumerate(ids)
    }
    return {
        "eligibleCount": preview.eligible_count,
        "totalCount": preview.total_count,
        "eligibleCapacityMw": preview.eligible_capacity_mw,
        "eligibleEquity_m": preview.eligible_equity / EUR_PER_M,
        "eligibleSolarShare": preview.eligible_solar_share,
        "eligibleGearing": preview.eligible_gearing,
        "lockedEquity_m": preview.locked_equity / EUR_PER_M,
        "runnable": preview.runnable,
        "warnings": [signal.code.name for signal in preview.signals],
        "eligibleIds": [
            project_id
            for index, project_id in enumerate(ids)
            if bool(preview.screens.eligible[index])
        ],
        "failedScreens": {key: value for key, value in failed.items() if value},
    }


def load_payload(path: Path) -> dict:
    parsed: dict = json.loads(path.read_text(encoding="utf-8"))
    return parsed


def as_float(value: object) -> float:
    """``None`` and JSON ``null`` become ``NaN``, which is what an absent ratio is."""
    return float("nan") if value is None else float(np.asarray(value, dtype=np.float64))
