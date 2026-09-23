"""Pinning BLAS to one thread, before anything can import numpy.

**This module must never import numpy, directly or transitively.** Every
threading library reads its environment variable once, when its shared object is
loaded, which happens the first time numpy is imported in a process. Setting
``OMP_NUM_THREADS`` afterwards changes nothing. So this module imports ``os`` and
nothing else, and is imported at the top of :mod:`terrafolio.runner.worker` and
passed as the process pool's ``initializer`` — belt and braces, because the two
paths into a worker differ between spawn and fork.

Two reasons, and the second is the one that matters:

1. Several concurrent runs on a pool sized to the machine would oversubscribe it.
2. **A multi-threaded BLAS reduction has no fixed order.** ``a + b + c`` summed
   by four threads and by one thread differ in the last bits, the GA's
   trajectory turns on ``f[a] >= f[b]``, and one flipped tournament diverges the
   entire run. §12 requires mandate + pipeline hash + assumption set + seed to
   determine the result exactly, and that is false on a multi-threaded BLAS.

``blas_threads`` on every run record says which regime produced it, because a run
whose figures cannot be explained is not audit trail.
"""

from __future__ import annotations

import os
from typing import Final

__all__ = ["THREAD_VARIABLES", "observed_threads", "pin_threads", "threads_are_pinned"]

THREAD_VARIABLES: Final[tuple[str, ...]] = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    # Not in the issue's list, and here because the first three do not cover
    # every build: a wheel linked against MKL or a BSD-flavoured pthread pool
    # ignores all three and threads anyway. Setting a variable no library reads
    # costs nothing; missing one costs reproducibility.
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)
"""Every environment variable that caps a numeric library's thread pool."""

SINGLE_THREADED: Final = "1"


def pin_threads() -> None:
    """Cap every numeric thread pool at one. Call before importing numpy."""
    for name in THREAD_VARIABLES:
        os.environ[name] = SINGLE_THREADED


def threads_are_pinned() -> bool:
    """Whether every variable this process can control reads as single-threaded."""
    return all(os.environ.get(name) == SINGLE_THREADED for name in THREAD_VARIABLES)


def observed_threads() -> int:
    """The thread count to record on the run, read back from the environment.

    Read rather than assumed. ``pin_threads`` may not have run — an in-process
    run inherits whatever the server was started with, and numpy is long since
    imported by then — and recording a 1 that was never set would make the run
    record claim a determinism it does not have.

    Where the variables disagree, the **largest** wins: the reduction order is
    non-deterministic if *any* library threads, so the honest figure is the worst
    case rather than the first one read.
    """
    counts = [_positive_int(os.environ.get(name)) for name in THREAD_VARIABLES]
    present = [count for count in counts if count is not None]
    if not present:
        # Nothing is capped, so the libraries size their own pools from the
        # machine. `os.cpu_count()` is what they will have seen.
        return os.cpu_count() or 1
    return max(present)


def _positive_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None
