"""The cross-file dispersion report: what 300 independent authors disagree about.

With statements ingested, each file declares its own tax rate, debt terms, escalators
and market prices. Nothing forces two analysts to agree, and nothing should — but a
committee cannot trust an aggregate built from 300 private opinions it cannot see. So
the loader reports the distribution of every **declared** assumption, with the files at
each tail.

This is a first-class feature, not plumbing (epic §2). It is the substitute for the
consistency that central derivation used to guarantee, and it **warns, never blocks**
(A-7, epic §12 Q3): blocking on an outlier would let one stale file stop all work.

``docs/pipeline-schema.md`` §11 names six fields; issue #6 adds the three escalators,
which C-2 put in the file for exactly this report and which nothing else lets a
consumer recover. Two of the nine are market-dependent — a Finnish baseload price and a
Greek one *should* differ — so those are grouped by country and compared within it,
which is what "by market" in §11 means.

The one declared assumption absent here is ``baseYear``. It is not a disagreement about
modelling that a report can surface; it is a broken index, because portfolio series are
summed by position. The loader fails on it instead (A-13).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import nan
from typing import Final

import numpy as np

from terrafolio.pipeline.arrays import ProjectArrays, Vector

__all__ = [
    "MARKET_KEYED_FIELDS",
    "PIPELINE_WIDE_FIELDS",
    "DispersionReport",
    "Distribution",
    "dispersion_report",
]

PIPELINE_WIDE_FIELDS: Final[tuple[str, ...]] = (
    "taxRate",
    "debtRate",
    "debtTenorYears",
    "depreciationYears",
    "degradationRate",
    "priceEscalation",
    "merchantEscalation",
    "opexEscalation",
    "targetDscr",
)
"""Declared assumptions that should not vary with geography.

Two files disagreeing about the opex escalator are disagreeing about the world, not
about where their turbines are.
"""

MARKET_KEYED_FIELDS: Final[tuple[str, ...]] = (
    "countryBaseloadPrice",
    "captureFactor",
)
"""Declared assumptions that legitimately vary between markets, so they are compared
within one. A Finnish baseload of €45 beside a Greek €84 is not a disagreement; two
Finnish files disagreeing is."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Distribution:
    """One field's spread across the files that declare it.

    Mirrors ``docs/api.md`` §3's dispersion row. ``outliers`` is §11's "the files at
    each tail" — the ids holding the extremes, and empty when every file agrees, since
    a pipeline with no dispersion has no tails to name.
    """

    count: int
    median: float
    minimum: float
    maximum: float
    outliers: tuple[str, ...]

    @property
    def agrees(self) -> bool:
        """True when every file declares the same value."""
        return self.minimum == self.maximum


def _distribution(ids: Sequence[str], values: Vector) -> Distribution:
    if values.size == 0:
        return Distribution(count=0, median=nan, minimum=nan, maximum=nan, outliers=())
    lowest, highest = float(values.min()), float(values.max())
    if lowest == highest:
        tails: tuple[str, ...] = ()
    else:
        at_tail = np.flatnonzero((values == lowest) | (values == highest))
        tails = tuple(sorted(ids[index] for index in at_tail.tolist()))
    return Distribution(
        count=len(ids),
        median=float(np.median(values)),
        minimum=lowest,
        maximum=highest,
        outliers=tails,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class DispersionReport:
    """Every declared assumption's spread, ready to serve or to print."""

    pipeline_wide: Mapping[str, Distribution]
    """Keyed by the field's wire name, as ``PIPELINE_WIDE_FIELDS`` lists it."""

    by_market: Mapping[str, Mapping[str, Distribution]]
    """Field name, then country code. Markets are in code order."""

    @property
    def disagreements(self) -> tuple[str, ...]:
        """Fields where the files do not all say the same thing.

        Not a verdict — legitimate variation lives here too. It is what a CLI or a UI
        shows first, so a reader starts where the pipeline is least settled.
        """
        wide = [name for name, spread in self.pipeline_wide.items() if not spread.agrees]
        market = [
            f"{name}[{market}]"
            for name, markets in self.by_market.items()
            for market, spread in markets.items()
            if not spread.agrees
        ]
        return (*wide, *market)


def dispersion_report(arrays: ProjectArrays) -> DispersionReport:
    """Build the report over a loaded pipeline."""
    declared = arrays.assumptions
    columns: dict[str, Vector] = {
        "taxRate": declared.tax_rate,
        "debtRate": declared.debt_rate,
        "debtTenorYears": declared.debt_tenor_years.astype(np.float64),
        "depreciationYears": declared.depreciation_years.astype(np.float64),
        "degradationRate": declared.degradation_rate,
        "priceEscalation": declared.price_escalation,
        "merchantEscalation": declared.merchant_escalation,
        "opexEscalation": declared.opex_escalation,
        "targetDscr": declared.target_dscr,
    }
    market_columns: dict[str, Vector] = {
        "countryBaseloadPrice": arrays.revenue.country_baseload_price,
        "captureFactor": arrays.revenue.capture_factor,
    }

    ids = arrays.ids
    pipeline_wide = {name: _distribution(ids, values) for name, values in columns.items()}

    rows_by_market: dict[str, list[int]] = {}
    for position, code in enumerate(arrays.location.country_codes):
        rows_by_market.setdefault(code, []).append(position)

    by_market = {
        name: {
            market: _distribution(
                [ids[row] for row in rows], values[np.array(rows, dtype=np.int64)]
            )
            for market, rows in sorted(rows_by_market.items())
        }
        for name, values in market_columns.items()
    }
    return DispersionReport(pipeline_wide=pipeline_wide, by_market=by_market)
