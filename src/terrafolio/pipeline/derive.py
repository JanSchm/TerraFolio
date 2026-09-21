"""Per-project derivations that do **not** depend on the mandate.

``docs/api.md`` §1.4 draws the line this module sits on: ``minDscr``, ``lcoe``,
``gearing`` and ``equity_m`` are mandate-independent, while ``equityIrr``, ``moic``,
``terminalValue_m`` and ``paybackYear`` are not. The mandate-dependent half lives in
``economics`` and may never be cached without ``hold_years`` in the key; everything
here is a property of the file and the assumption set alone.

The split is also structural. ``pipeline`` ranks below ``economics``, so ``economics``
may import this module and the tie-out validator may use it too — which matters,
because §10's DSCR-sizing plausibility band is stated in terms of the same minimum
this module computes, and deriving it twice is how the two would come to disagree.

numpy, the standard library and ``config`` only: ``economics`` imports this, and the
numeric core's transitive third-party closure must stay ``{numpy}``.
"""

from __future__ import annotations

import numpy as np

from terrafolio.domain.conventions import YEARS
from terrafolio.pipeline.arrays import ProjectArrays, Vector

__all__ = ["min_dscr", "ramp_offsets"]


def ramp_offsets(arrays: ProjectArrays) -> Vector:
    """Column index of each project's ramp year, or ``NaN`` where it has none.

    The ramp year is the first operating year, which runs at a partial-year fraction
    of full output (§9.2) and therefore carries a DSCR far below the rest of the debt
    life. ``NaN`` means the ramp falls outside the 30-year window: the asset was
    already operating at the base year, so the whole equity outflow is booked in year
    one and there is no partial year (§13).
    """
    offsets = (arrays.asset.cod_year - arrays.base_year).astype(np.float64)
    inside = (offsets >= 0) & (offsets < YEARS)
    return np.where(inside, offsets, np.nan)


def min_dscr(arrays: ProjectArrays) -> Vector:
    """Minimum annual DSCR over the debt life, **excluding the ramp year** (§9.4).

    ``NaN`` where the project carries no debt, which is the honest answer rather than
    a sentinel: there is no debt service to fail to cover. It becomes ``null`` on the
    wire and an em dash in the table, and A-21 makes it **pass** the mandate's DSCR
    screen — rejecting an unlevered project for having no coverage ratio would drop
    the safest assets in the pipeline, and minimum leverage is the control that
    actually expresses a preference against them.

    Read from the file's own ``ratios.dscr``, which the §7.7 tie-out has already
    proved equal to ``ebitda ÷ (interestPaid + debtRepayment)``. The file is the
    source; this is the reduction over it, with the one documented exclusion.

    The reference additionally rounds to 2 dp and caps at 3.2 before screening
    (1C-4). Neither is reproduced here: ``docs/api.md`` §2 asks for the minimum over
    the debt life and nothing else, and a 2 dp rounding decides whether a project at
    1.2449 clears a 1.25 floor. ``derived_expectations.json`` carries ``minDSCRRaw``
    beside ``minDSCRReference`` precisely so a port can choose.
    """
    dscr = arrays.statements.ratios.dscr
    columns = np.arange(YEARS, dtype=np.float64)
    is_ramp = columns[None, :] == ramp_offsets(arrays)[:, None]

    # +inf rather than NaN so the reduction never sees an all-NaN slice, which numpy
    # warns on and `filterwarnings = ["error"]` would turn into a test failure.
    considered = np.where(np.isnan(dscr) | is_ramp, np.inf, dscr)
    lowest = considered.min(axis=-1)
    return np.where(np.isfinite(lowest), lowest, np.nan)
