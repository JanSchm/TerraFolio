"""Pre-multiplied per-project columns, so a generation collapses into one GEMM.

Every aggregate §10.2 needs is **linear in the selection vector**. Weighted averages
are not, but each is a ratio of two linear quantities, so carrying both numerator and
denominator as columns makes the whole objective reachable from one matrix product:

``A = selection @ hstack([fit, country])``

At Standard effort over 500 candidates that is 90 x 500 x 23, about a million
multiply-accumulates per generation, which BLAS does in well under a millisecond.

**The undefined-IRR columns are a pair on purpose.** ``equity x irr x defined`` over
``equity x defined`` is the equity-weighted blend with undefined projects *excluded*
from both halves — not coalesced to zero and then averaged in, which is what the
JavaScript reference does and what the epic's invariant forbids (1C-6). Splitting the
average into two linear columns is what makes the exclusion expressible at all inside
a single matrix product.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

from terrafolio.pipeline.arrays import BoolVector, Matrix, ProjectArrays, Vector

__all__ = ["FEATURE_COLUMNS", "Features", "build_features"]

FEATURE_COLUMNS: Final[tuple[str, ...]] = (
    "capex",
    "capacity_mw",
    "equity",
    "senior_debt",
    "solar_mw",
    "capex_weighted_risk",
    "capex_weighted_merchant",
    "equity_weighted_irr",
    "equity_with_defined_irr",
)
"""The nine ``fit`` columns, in order.

Positions come from :data:`COLUMN` rather than being written out, so the index of a
column is never a literal — the numeric core forbids those, and an index that drifts
from its name is a bug nothing else would catch.
"""

COLUMN: Final[dict[str, int]] = {name: index for index, name in enumerate(FEATURE_COLUMNS)}


@dataclass(frozen=True, slots=True, kw_only=True)
class Features:
    """Per-project columns, plus the two dtypes the reduction runs in.

    Both precisions are materialised once at build time: the genetic algorithm's hot
    path multiplies in **float32**, which is measurably faster for these shapes, while
    everything reported — and every comparison against the golden oracle — goes
    through **float64**. Choosing between them at the call site rather than converting
    per generation is what keeps the float32 confined to the hot path.
    """

    fit: Matrix
    """``(n, 9)`` float64, in :data:`FEATURE_COLUMNS` order."""

    country: Matrix
    """``(n, k)`` float64 — one-hot country membership scaled by capex, so a column
    sum is that country's capex in the selection."""

    country_codes: tuple[str, ...]
    """The ``k`` country codes, in ascending code order, matching ``country``."""

    capex: Vector
    """``(n,)`` euros. Kept out of the GEMM because single-project concentration is
    genuinely ``O(m x n)`` and needs the per-project share, not a total."""

    combined: Matrix
    """``hstack([fit, country])`` in float64 — what the reduction multiplies."""

    combined_f32: Matrix
    """The same matrix in float32, for the genetic algorithm's hot path."""

    @property
    def project_count(self) -> int:
        return int(self.fit.shape[0])

    @property
    def country_count(self) -> int:
        return len(self.country_codes)

    def matrix_for(self, dtype: np.dtype[np.floating]) -> Matrix:
        """The reduction matrix at the precision ``selection`` is carried in."""
        return self.combined_f32 if dtype == np.float32 else self.combined

    def take(self, rows: NDArray[np.intp]) -> Features:
        """The same columns for a subset of projects, in the order ``rows`` gives.

        The search runs over the *eligible* candidates, not the whole pipeline, so
        the features are built once for everything loaded and narrowed here. The
        country columns are kept whole rather than re-derived: a country with nothing
        selected contributes a zero share, which is the right answer and one fewer
        thing that can differ between two calls.
        """
        return Features(
            fit=self.fit[rows],
            country=self.country[rows],
            country_codes=self.country_codes,
            capex=self.capex[rows],
            combined=self.combined[rows],
            combined_f32=self.combined_f32[rows],
        )


def build_features(
    arrays: ProjectArrays,
    *,
    equity_irr: Vector,
    irr_defined: BoolVector,
    merchant_share: Vector,
) -> Features:
    """Pre-multiply every column the objective and the tiles both need.

    ``merchant_share`` is passed in rather than derived here because *which* merchant
    share feeds the objective is a decision, not a detail: §7.1 and §10.2 are both
    capex-weighted on the file's ``ppaShare``, while
    :func:`terrafolio.economics.returns.contracted_revenue_share` computes a different,
    revenue-weighted figure for the detail sheet. Making the caller name it keeps the
    two from being swapped by accident.
    """
    capex = arrays.capital.total_capex
    equity = arrays.capital.equity
    defined = irr_defined.astype(np.float64)
    # NaN times zero is still NaN, so the undefined rates are cleared before scaling
    # rather than masked afterwards — one NaN in this column poisons the whole GEMM.
    contribution = np.where(irr_defined, equity_irr, 0.0)

    fit = np.column_stack(
        [
            capex,
            arrays.asset.capacity_mw,
            equity,
            arrays.capital.senior_debt,
            arrays.asset.capacity_mw * arrays.is_solar,
            capex * arrays.execution.development_risk_score,
            capex * merchant_share,
            equity * contribution * defined,
            equity * defined,
        ]
    ).astype(np.float64)

    codes = tuple(sorted(set(arrays.location.country_codes)))
    position = {code: index for index, code in enumerate(codes)}
    country = np.zeros((arrays.count, len(codes)), dtype=np.float64)
    country[np.arange(arrays.count), [position[c] for c in arrays.location.country_codes]] = capex

    combined = np.hstack([fit, country])
    return Features(
        fit=fit,
        country=country,
        country_codes=codes,
        capex=capex,
        combined=combined,
        combined_f32=combined.astype(np.float32),
    )
