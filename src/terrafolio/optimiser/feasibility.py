"""§5.4's live feasibility preview — the **normative** one.

``web/js/feasibility.js`` mirrors this for latency, because §12 wants the footer to
respond in under 100 ms and a round trip cannot promise that. 4B proves the two agree.
That makes this module the definition both sides are tested against, so where the
mockup and the specification differ, the specification wins and the difference is
recorded rather than copied.

Three such differences are already settled:

* **Order is §5.4's, not the mockup's** (A-5). Leverage precedes solar, which precedes
  capital absorption. ``WarningCode`` is an ``IntEnum`` in exactly that order, so
  ``sorted`` is display order and nothing here has to restate it.
* **The solar warning is one-sided** (A-22). It fires only when the pool holds *less*
  solar than the target. A pool with more can still reach the target by selecting
  fewer solar projects, so the target is not unreachable and the pinned string would
  be wrong.
* **``LOCKS_EXCEED_CAPITAL`` is the seventh code** (C-9), and blocking. §13 requires
  the run button disabled when locks alone exceed capital, and
  ``POST /optimisations`` answers 422 on exactly the two blocking conditions.

**No message strings.** A warning leaves here as a code and its numbers **in euros**.
Rendering "€1,200m" inside the numeric core would put a third unit boundary in a
system that permits two, and ``docs/ui-contract.md`` §3.5 already owns the copy.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

import numpy as np

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.domain.enums import WarningCode
from terrafolio.domain.scalars import MandateScalars
from terrafolio.optimiser.screens import ScreenResult, apply_screens
from terrafolio.pipeline.arrays import ProjectArrays

__all__ = ["FeasibilityPreview", "FeasibilitySignal", "preview_feasibility"]


@dataclass(frozen=True, slots=True, kw_only=True)
class FeasibilitySignal:
    """One warning: its pinned code, and the numbers a renderer needs.

    ``detail`` is in **euros** and fractions. The wire turns it into €m with an ``_m``
    suffix; the page turns that into "€1,200m". Neither conversion happens here.
    """

    code: WarningCode
    detail: Mapping[str, float] = field(default_factory=dict)

    @property
    def blocks_the_run(self) -> bool:
        return self.code.disables_run


@dataclass(frozen=True, slots=True, kw_only=True)
class FeasibilityPreview:
    """What the mandate footer shows, and whether the run button is live."""

    total_count: int
    eligible_capacity_mw: float
    eligible_equity: float
    """Euros. The equity required to buy the **whole** eligible set — §5.4's third
    footer figure, and deliberately not the equity of any portfolio."""

    eligible_solar_share: float
    eligible_gearing: float
    locked_equity: float
    signals: tuple[FeasibilitySignal, ...]
    screens: ScreenResult

    @property
    def eligible_count(self) -> int:
        return self.screens.eligible_count

    @property
    def runnable(self) -> bool:
        """False **iff** some warning blocks — the two 422 conditions, and no others."""
        return not any(signal.blocks_the_run for signal in self.signals)

    @property
    def screens_to_widen(self) -> tuple[str, ...]:
        """§13's "naming the screens to widen", worst offender first."""
        return self.screens.binding_screens


def preview_feasibility(
    arrays: ProjectArrays,
    mandate: MandateScalars,
    assumptions: AssumptionSet,
    *,
    locked_ids: Iterable[str] = (),
    excluded_ids: Iterable[str] = (),
) -> FeasibilityPreview:
    """Run the screens and derive §5.4's footer figures and warnings."""
    locked = tuple(locked_ids)
    excluded = tuple(excluded_ids)
    screens = apply_screens(arrays, mandate, assumptions, locked_ids=locked, excluded_ids=excluded)
    eligible = screens.eligible
    thresholds = assumptions.feasibility

    capacity = float(arrays.asset.capacity_mw[eligible].sum())
    equity = float(arrays.capital.equity[eligible].sum())
    capex = float(arrays.capital.total_capex[eligible].sum())
    debt = float(arrays.capital.senior_debt[eligible].sum())
    solar_capacity = float(arrays.asset.capacity_mw[eligible & arrays.is_solar].sum())

    solar_share = solar_capacity / capacity if capacity > 0.0 else 0.0
    gearing = debt / capex if capex > 0.0 else 0.0

    # Hoisted: this ran two set constructions per project, and §12 budgets the whole
    # preview at under 100 ms.
    held = set(locked) - set(excluded)
    locked_rows = np.array([project_id in held for project_id in arrays.ids], dtype=np.bool_)
    locked_equity = float(arrays.capital.equity[locked_rows].sum())

    signals: list[FeasibilitySignal] = []
    # An empty pool has zero capacity, zero equity and a zero solar share, so every
    # advisory test below would fire on figures that describe nothing. `NO_CANDIDATES`
    # is the whole answer, and `feasibility.js` guards the same four the same way —
    # a preview that disagrees with its mirror is worse than one that says less.
    has_candidates = screens.eligible_count > 0

    if not has_candidates:
        signals.append(
            FeasibilitySignal(
                code=WarningCode.NO_CANDIDATES,
                detail={"totalCount": float(arrays.count)},
            )
        )

    if locked_equity > mandate.available_capital_eur:
        signals.append(
            FeasibilitySignal(
                code=WarningCode.LOCKS_EXCEED_CAPITAL,
                detail={
                    "lockedEquity": locked_equity,
                    "availableCapital": mandate.available_capital_eur,
                    "excess": locked_equity - mandate.available_capital_eur,
                    "lockedCount": float(len(locked)),
                },
            )
        )

    if has_candidates and capacity < mandate.capacity_target_mw:
        signals.append(
            FeasibilitySignal(
                code=WarningCode.CAPACITY_BELOW_TARGET,
                detail={
                    "eligibleCapacityMw": capacity,
                    "capacityTargetMw": mandate.capacity_target_mw,
                    "shortfallMw": mandate.capacity_target_mw - capacity,
                },
            )
        )

    # The whole eligible pool is the most levered portfolio available: any subset
    # mixes in nothing more geared than what is already here.
    if has_candidates and mandate.min_leverage > gearing:
        signals.append(
            FeasibilitySignal(
                code=WarningCode.LEVERAGE_UNREACHABLE,
                detail={"eligibleGearing": gearing, "minLeverage": mandate.min_leverage},
            )
        )

    # One-sided (A-22): only a pool with too *little* solar cannot reach the target.
    if has_candidates and solar_share < mandate.solar_share - (
        thresholds.solar_divergence_tolerance
    ):
        signals.append(
            FeasibilitySignal(
                code=WarningCode.SOLAR_MIX_UNREACHABLE,
                detail={
                    "eligibleSolarShare": solar_share,
                    "targetSolarShare": mandate.solar_share,
                },
            )
        )

    absorbed = equity / mandate.available_capital_eur
    if has_candidates and absorbed < thresholds.capital_absorption_floor:
        signals.append(
            FeasibilitySignal(
                code=WarningCode.CAPITAL_UNDERUSED,
                detail={
                    "eligibleEquity": equity,
                    "availableCapital": mandate.available_capital_eur,
                    "absorbedShare": absorbed,
                },
            )
        )

    if locked or excluded or screens.readmitted:
        signals.append(
            FeasibilitySignal(
                code=WarningCode.LOCKS_PRESENT,
                detail={
                    "lockedCount": float(len(locked)),
                    "excludedCount": float(len(excluded)),
                    "readmittedCount": float(len(screens.readmitted)),
                },
            )
        )

    return FeasibilityPreview(
        total_count=arrays.count,
        eligible_capacity_mw=capacity,
        eligible_equity=equity,
        eligible_solar_share=solar_share,
        eligible_gearing=gearing,
        locked_equity=locked_equity,
        # WarningCode's ordinals are §5.4's severity order, so sorting is display order.
        signals=tuple(sorted(signals, key=lambda signal: signal.code)),
        screens=screens,
    )
