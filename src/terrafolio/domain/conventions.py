"""Indexing and ordering conventions shared by every layer.

Standard library only — the numeric core needs these too, and it may not import
pydantic. See the package docstring in ``terrafolio/__init__.py``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

__all__ = [
    "EUR_PER_EUR_MILLION",
    "KW_PER_MW",
    "MWH_PER_GWH",
    "YEARS",
    "canonical_order",
    "exit_index",
    "ramp_index",
]

YEARS: Final = 30
"""The fixed length of every statement series.

Structural, not calibration: ``docs/pipeline-schema.md`` §5 defines the file
format as exactly 30 contiguous years from ``assumptions.baseYear``. Changing it
is a new schema version, not a configuration change.
"""


EUR_PER_EUR_MILLION: Final = 1_000_000
"""Definitional, not configuration: a million euros is not a tunable.

Files are in €m and the numeric core is in euros (epic §5). This constant exists
so the two conversion boundaries — the loader on the way in, the API on the way
out — name the same number instead of each spelling ``1e6``.
"""

MWH_PER_GWH: Final = 1_000
"""Definitional. Used where €/MWh prices meet GWh volumes."""

KW_PER_MW: Final = 1_000
"""Definitional, and the same number as :data:`MWH_PER_GWH` for a different reason.

Used where a €/kW opex or capex meets a capacity in MW. Named apart because reading
``MWH_PER_GWH`` in a cost line is how a unit error survives review.
"""


def canonical_order(ids: Iterable[str]) -> tuple[str, ...]:
    """Return project ids in canonical order: plain ascending string sort.

    Canonical ordering is a **determinism requirement**, not tidiness — the GA's
    PRNG draws are indexed by position (epic §5), so two loads of the same files
    in a different order must produce the same sequence.

    The sort is lexicographic, which means **ids must be zero-padded to a uniform
    width within a pipeline**: ``"P10" < "P9"``, so mixing ``P9`` with ``P10``
    orders them in a way no one intends. The schema's id pattern permits both, so
    this is a generator obligation and a loader check (issue 2A), not something
    this function can repair — sorting numerically instead would only move the
    problem to ids that are not numeric at all.
    """
    return tuple(sorted(ids))


def ramp_index(cod_year: int, base_year: int) -> int | None:
    """Index of the project's ramp year, or ``None`` if it has none.

    The ramp year is the first operating year, which runs at a partial-year
    fraction of full output (§9.2). It carries a DSCR far below the rest of the
    debt life, so the portfolio's *minimum* DSCR excludes it (§9.4) — that
    exclusion is the consumer's job, and this is the index it excludes.

    ``None`` means there is no ramp year inside the 30-year window: either the
    asset was already operating at ``base_year`` (§13 — the whole equity outflow
    is booked in year one and there is no partial year), or its COD falls beyond
    the window, which a valid file cannot do.
    """
    offset = cod_year - base_year
    if offset < 0 or offset >= YEARS:
        return None
    return offset


def exit_index(hold_years: int) -> int:
    """Index of the exit year for a hold of ``hold_years`` from the base year.

    Holding for ``h`` years means holding years ``0 … h-1``, so the exit lands on
    index ``h - 1``. The terminal value is added to that year's FCFE, alongside
    the year's own cash flow — matching the JavaScript reference, and giving 2C's
    "does the exit year's own FCFE count" interpretation flag a defined meaning.

    Clamped to the window: a 30-year hold from the base year exits at index 29.
    """
    if hold_years < 1:
        raise ValueError(f"hold_years must be at least 1, got {hold_years}")
    return min(hold_years, YEARS) - 1
