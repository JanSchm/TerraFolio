"""Candidate pools at a chosen width, and the environment a timing came from.

Epic §7 budgets the search at 500 candidates and requires it to scale to 2,000
without an architectural change, but the shipped pipeline holds 300 files. Generating
1,700 more would take minutes and produce data nobody reads, so a pool of ``n``
candidates is the loaded pipeline's feature columns **tiled** to width ``n``.

That is legitimate for a *cost* measurement and not for a *correctness* one. The GA's
cost depends on the shape of the feature matrix and on how many chromosomes land over
budget; neither cares that two columns describe the same park. Nothing in
``tests/regression/`` or ``tests/parity/`` uses a tiled pool — those run against the
real 48-file golden fixtures or the real 300-file pipeline, where ids are unique and
the answer means something.

``describe_environment`` exists because ``docs/decisions.md`` insists on it: a timing
taken under load is worthless, so every number this package prints carries the load
average, the thread count and the numpy version it was taken under.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.config.loader import load_default
from terrafolio.domain.scalars import MandateScalars
from terrafolio.economics.returns import project_returns
from terrafolio.optimiser.features import Features, build_features
from terrafolio.pipeline.loader import LoadResult, load_pipeline
from terrafolio.runner.threads import observed_threads

__all__ = [
    "GOLDEN_PIPELINE",
    "SHIPPED_PIPELINE",
    "Environment",
    "build_pool",
    "describe_environment",
    "load_shipped",
    "repo_root",
]


def repo_root() -> Path:
    """The repository root, found from this file rather than from the cwd."""
    return Path(__file__).resolve().parent.parent.parent


SHIPPED_PIPELINE: Final = repo_root() / "pipeline"
"""2C's 300 generated files — the pipeline the application actually ships."""

GOLDEN_PIPELINE: Final = repo_root() / "tests" / "golden" / "fixtures" / "pipeline"
"""1C's 48 files extracted from the JavaScript reference."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Environment:
    """What a measurement was taken on, printed beside every number."""

    numpy_version: str
    blas_threads: int
    load_average: float
    cpu_count: int
    platform: str

    def __str__(self) -> str:
        return (
            f"numpy {self.numpy_version} · BLAS threads {self.blas_threads}"
            f" · {self.cpu_count} CPUs · load {self.load_average:.2f}"
            f" · {self.platform}"
        )


def describe_environment() -> Environment:
    """Capture the machine state a timing is only meaningful relative to."""
    return Environment(
        numpy_version=np.__version__,
        blas_threads=observed_threads(),
        load_average=os.getloadavg()[0],
        cpu_count=os.cpu_count() or 1,
        platform=f"{platform.system()} {platform.machine()}",
    )


def load_shipped(assumptions: AssumptionSet | None = None) -> tuple[LoadResult, AssumptionSet]:
    """Load the shipped 300-file pipeline, with the default calibration."""
    resolved = assumptions if assumptions is not None else load_default()
    return load_pipeline(SHIPPED_PIPELINE, resolved), resolved


def build_pool(
    loaded: LoadResult,
    mandate: MandateScalars,
    assumptions: AssumptionSet,
    *,
    candidates: int,
) -> Features:
    """Feature columns for exactly ``candidates`` projects, tiled from ``loaded``.

    The merchant share is the file's ``ppaShare`` complement, which is the one
    :func:`terrafolio.optimiser.features.build_features` documents as the objective's
    (the revenue-weighted figure belongs to the detail sheet, not here).
    """
    returns = project_returns(loaded.arrays, assumptions, mandate.hold_years)
    features = build_features(
        loaded.arrays,
        equity_irr=np.nan_to_num(returns.equity_irr),
        irr_defined=returns.defined,
        merchant_share=1.0 - loaded.arrays.revenue.ppa_share,
    )
    width = features.project_count
    if width == 0:
        raise ValueError("cannot build a candidate pool from an empty pipeline")
    repeats = -(-candidates // width)
    rows = np.tile(np.arange(width), repeats)[:candidates].astype(np.intp)
    return features.take(rows)
