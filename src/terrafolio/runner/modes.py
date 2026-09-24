"""Where a search runs. Its own module so that naming it costs nothing.

``api/settings.py`` needs this enum to parse configuration, and ``runner/pool.py``
needs it to build an executor. Putting it in either would make the other import a
module with side effects — ``pool`` pulls in ``worker``, whose import *pins the
process's BLAS threads*, which is exactly right in a worker and a surprise in
anything that only wanted to read a setting.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["RunnerMode"]


class RunnerMode(StrEnum):
    """All three run the **same** engine through the same entry point.

    ``INLINE`` is not a stand-in implementation: it executes the real search
    synchronously on the calling thread, which is what makes an API test
    deterministic without waiting on a pool. What it cannot do is pin BLAS to one
    thread, because numpy is already imported by then — so a run that needs
    §12's bit-exact guarantee needs ``PROCESS``, and ``blasThreads`` on the run
    record says which one it got.
    """

    PROCESS = "process"
    THREAD = "thread"
    INLINE = "inline"
