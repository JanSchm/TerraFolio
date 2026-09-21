"""Exit value: what the equity is worth when the hold period ends.

``terminal value = exitMultiple[technology] x ebitda[exit] - debtBalanceClosing[exit]``

Every part of that is mandate-dependent or assumption-set-dependent — the exit index
moves with the hold-period slider and the multiple comes from the assumption set — so
nothing here may be cached without ``hold_years`` in the key, and none of it is ever
stored in a file (``docs/pipeline-schema.md`` §9).

The outstanding debt is read from the file's own ``debtSchedule.closing``, which §7.4
has already proved rolls forward correctly. The JavaScript reference instead re-simulates
an 18-year annuity from the facility size, because its model had no schedule to read;
with statements ingested there is one, and re-deriving a balance that is sitting in the
file would be a second source for the same number.
"""

from __future__ import annotations

import numpy as np

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.domain.conventions import exit_index
from terrafolio.pipeline.arrays import ProjectArrays, Vector

__all__ = ["exit_multiples", "terminal_value"]


def exit_multiples(arrays: ProjectArrays, assumptions: AssumptionSet) -> Vector:
    """``(n,)`` EV/EBITDA multiple per project, by technology."""
    return np.array(
        [assumptions.exit_multiples[technology] for technology in arrays.asset.technologies],
        dtype=np.float64,
    )


def terminal_value(arrays: ProjectArrays, assumptions: AssumptionSet, hold_years: int) -> Vector:
    """``(n,)`` exit proceeds to equity, in euros, floored at zero.

    The floor is limited liability, not a fudge. If the outstanding debt exceeds what
    the asset fetches, the equity is wiped out — it does not owe the difference, and a
    negative exit value would flow straight into an IRR as though it did. The
    JavaScript reference floors it for the same reason.
    """
    index = exit_index(hold_years)
    ebitda = arrays.statements.income.ebitda[:, index]
    outstanding = arrays.statements.debt.closing[:, index]
    gross = exit_multiples(arrays, assumptions) * ebitda - outstanding
    return np.maximum(gross, 0.0)
