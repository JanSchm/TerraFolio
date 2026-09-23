"""The nine project-level pre-screens, and why the drop counts are independent.

§5.2 and §5.3 define nine tests a candidate must pass before the search ever sees it.
Running them is easy; **reporting** them is the part that has a wrong answer.

§13 requires that a mandate passing nothing raises a warning *naming the screens to
widen*. A cumulative count cannot produce one: evaluate the screens in order and stop
at the first failure, and every project is attributed to whichever screen happens to
run first, so "countries" absorbs the blame for a COD window that is really what is too
tight. So each screen is evaluated **independently, against the whole pipeline** — the
counts overlap, deliberately, and they answer the question a user actually asks, which
is "what would widening *this* control give me back".

**Locked projects re-admit regardless.** §13 already lets a lock breach a concentration
cap visibly rather than silently, and the same principle applies here: the user has
said they want this project, so the screens do not get to remove it — but the ids that
were re-admitted are surfaced, because a lock quietly overriding a DSCR floor is
exactly the kind of thing an investment committee should be told about.

numpy, the standard library and ``config`` only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

import numpy as np

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.domain.enums import Currency
from terrafolio.domain.scalars import MandateScalars
from terrafolio.pipeline.arrays import BoolVector, ProjectArrays, normalise_country_code
from terrafolio.pipeline.derive import min_dscr

__all__ = [
    "SCREEN_NAMES",
    "ScreenResult",
    "apply_screens",
]

COUNTRIES: Final = "countries"
STAGES: Final = "stages"
COD_WINDOW: Final = "codWindow"
MIN_DSCR: Final = "minDscr"
GRID_SECURED: Final = "gridSecured"
EUR_REVENUE: Final = "eurRevenue"
OM_CONTRACTED: Final = "omContracted"
RISK_SCORE: Final = "riskScore"
EXCLUSIONS: Final = "exclusions"

SCREEN_NAMES: Final[tuple[str, ...]] = (
    COUNTRIES,
    STAGES,
    COD_WINDOW,
    MIN_DSCR,
    GRID_SECURED,
    EUR_REVENUE,
    OM_CONTRACTED,
    RISK_SCORE,
    EXCLUSIONS,
)
"""In §5.2 then §5.3 order, which is the order the mandate screen presents them."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ScreenResult:
    """Who is eligible, and what each screen cost."""

    eligible: BoolVector
    """``(n,)`` — the candidate set the search runs over, locks already re-admitted."""

    passes: Mapping[str, BoolVector]
    """One ``(n,)`` mask per screen, each evaluated against the whole pipeline."""

    drops: Mapping[str, int]
    """How many projects **this screen alone** rejects. The counts overlap: that is
    the point, and summing them is meaningless."""

    readmitted: tuple[str, ...]
    """Locked ids that failed at least one screen and were admitted anyway."""

    @property
    def eligible_count(self) -> int:
        return int(self.eligible.sum())

    @property
    def binding_screens(self) -> tuple[str, ...]:
        """Screens that reject anything, worst first — §13's "which to widen"."""
        rejecting = [name for name in SCREEN_NAMES if self.drops[name] > 0]
        return tuple(sorted(rejecting, key=lambda name: (-self.drops[name], name)))


def _membership(values: Iterable[str], allowed: Iterable[str]) -> BoolVector:
    permitted = set(allowed)
    return np.array([value in permitted for value in values], dtype=np.bool_)


def apply_screens(
    arrays: ProjectArrays,
    mandate: MandateScalars,
    assumptions: AssumptionSet,
    *,
    locked_ids: Iterable[str] = (),
    excluded_ids: Iterable[str] = (),
) -> ScreenResult:
    """Run all nine screens over the whole pipeline.

    ``locked_ids`` and ``excluded_ids`` are run controls rather than mandate fields —
    they live on the run record, not on :class:`MandateScalars` — so they arrive
    separately. A project that is both locked and excluded stays **excluded**: the
    exclusion is the more specific instruction, and letting a stale lock resurrect a
    project the user has just struck out would be the surprising answer.
    """
    cod = arrays.asset.cod_year
    risk_cap = assumptions.risk_caps.project[mandate.risk_appetite]
    lowest_dscr = min_dscr(arrays)
    excluded = set(excluded_ids)

    passes: dict[str, BoolVector] = {
        COUNTRIES: _membership(
            arrays.location.country_codes,
            {normalise_country_code(code) for code in mandate.countries},
        ),
        STAGES: _membership(arrays.asset.stages, mandate.stages),
        COD_WINDOW: (cod >= mandate.cod_from) & (cod <= mandate.cod_to),
        # A null minimum passes (A-21). A project with no debt has no coverage ratio
        # to fail; screening it out would drop the safest assets in the pipeline for
        # having no risk to measure, and minimum leverage is the control that
        # actually expresses a preference against them.
        MIN_DSCR: np.isnan(lowest_dscr) | (lowest_dscr >= mandate.min_dscr),
        GRID_SECURED: arrays.execution.grid_secured
        if mandate.grid_secured_only
        else np.ones(arrays.count, dtype=np.bool_),
        EUR_REVENUE: _membership(arrays.execution.currencies, {Currency.EUR})
        if mandate.eur_revenue_only
        else np.ones(arrays.count, dtype=np.bool_),
        OM_CONTRACTED: arrays.execution.om_contracted
        if mandate.om_contracted_only
        else np.ones(arrays.count, dtype=np.bool_),
        RISK_SCORE: arrays.execution.development_risk_score <= risk_cap,
        EXCLUSIONS: np.array(
            [project_id not in excluded for project_id in arrays.ids], dtype=np.bool_
        ),
    }

    survives_every_screen = np.ones(arrays.count, dtype=np.bool_)
    for mask in passes.values():
        survives_every_screen &= mask

    # Built once, outside the comprehension. Rebuilding it per project was O(n x k)
    # for a value that never changes — and, worse, silently wrong for a generator:
    # `locked_ids` is typed `Iterable`, so the first project consumed it and every
    # project after that saw an empty set, dropping every lock without an error.
    held = set(locked_ids) - excluded
    locked = np.array([project_id in held for project_id in arrays.ids], dtype=np.bool_)
    eligible = survives_every_screen | locked
    readmitted = tuple(
        arrays.ids[index] for index in np.flatnonzero(locked & ~survives_every_screen).tolist()
    )

    return ScreenResult(
        eligible=eligible,
        passes=passes,
        drops={name: int((~mask).sum()) for name, mask in passes.items()},
        readmitted=readmitted,
    )
