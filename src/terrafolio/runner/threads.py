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
import sys
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

_INHERITED: Final[dict[str, str | None]] = {name: os.environ.get(name) for name in THREAD_VARIABLES}
"""What this process was started with, captured before anything can pin it.

Needed because a pin applied *after* numpy is imported changes the environment
and nothing else: the libraries have already sized their pools. Without the
original values there is no way to say what the pools were actually sized to,
and the run record would claim a single thread over a run that had eight.
"""

_STATE: Final[dict[str, bool]] = {"pinned_in_time": False}
"""Whether :func:`pin_threads` ran before numpy was imported.

A one-key mapping rather than a module variable so the function that sets it does
not need ``global``, which reads as an afterthought where this is the whole point
of the module.
"""


def pin_threads() -> None:
    """Cap every numeric thread pool at one. **Call before importing numpy.**

    Records whether it was in time. Setting the variables afterwards is not an
    error and not a no-op — a library imported later still reads them — but it
    does not resize a pool that already exists, and a caller that believes
    otherwise would record a determinism the run does not have.
    """
    _STATE["pinned_in_time"] = "numpy" not in sys.modules
    for name in THREAD_VARIABLES:
        os.environ[name] = SINGLE_THREADED


def threads_are_pinned() -> bool:
    """Whether this process genuinely runs numeric libraries single-threaded.

    Both halves are required: the variables must read as single-threaded **and**
    the pin must have been applied before numpy loaded. An in-process run on a
    server that has been serving requests for an hour fails the second half, and
    saying so is the point.
    """
    environment = all(os.environ.get(name) == SINGLE_THREADED for name in THREAD_VARIABLES)
    return environment and _STATE["pinned_in_time"]


def observed_threads() -> int:
    """The thread count to record on the run, read back from the environment.

    Read rather than assumed. ``pin_threads`` may not have run, or may have run
    after numpy was already imported — an in-process run on a live server is
    exactly that case — and recording a 1 that never took effect would make the
    run record claim a determinism it does not have.

    Where the inherited variables disagree, the **largest** wins, and a variable
    nobody set counts as the whole machine: reduction order is non-deterministic
    if *any* library threads, so the honest figure is the worst case rather than
    the most flattering one.
    """
    if threads_are_pinned():
        return 1
    # The pin either did not happen or came too late, so what the libraries
    # actually sized their pools from is what this process inherited.
    #
    # **A variable that is unset counts as the whole machine**, not as absent.
    # Taking the maximum over only the variables that *were* set reported one
    # thread for a process where `OMP_NUM_THREADS=1` but OpenBLAS was left
    # uncapped — which is precisely the case where the reduction order is not
    # fixed, and precisely the claim the run record must not make.
    machine = os.cpu_count() or 1
    return max(_positive_int(_INHERITED[name]) or machine for name in THREAD_VARIABLES)


def _positive_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None
