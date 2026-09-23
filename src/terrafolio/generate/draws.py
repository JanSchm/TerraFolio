"""The draw plan: which random numbers a project consumes, and in what order.

This module is the reason the generator can reproduce the reference corpus at
all. The *values* a project gets are the reference's formulas; the *order* it
draws them in is just as load-bearing, because two of the nine draws are
conditional:

======  ==================  =============================================
order   draw                taken when
======  ==================  =============================================
1       capacity factor     always
2       opex per kW         always
3       development risk    always
4       grid secured        only for a greenfield project
5       O&M contracted      only when not already under construction
6       contracted share    always
7       contracted price    always
8       contracted tenor    only when the share clears the floor
9       entry yield         always
======  ==================  =============================================

So a project takes seven, eight or nine draws depending on what it is, and a
port that draws unconditionally — reading the value but taking it anyway —
diverges from the very first project. ``js_prng.json`` records the plan and the
consumed count for all 48 reference projects, and
``tests/unit/test_generate_draws.py`` replays it.

**Two stream sources, one plan.**

*Reference parity* seeds a fresh ``xorshift32`` per project from its **index**,
``i * 97 + 13``, which is what the reference does and what reproduces the corpus.

*The shipped generator* keys on ``(project id, assumption set id)`` instead.
Index-keyed jitter means inserting one candidate at the head of a pipeline
reprices every project behind it, and every stored run then stops explaining its
own numbers. Keying on the id removes that: a project's economics depend on what
it is called and which calibration was used, and on nothing else in the
directory around it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

import numpy as np

from terrafolio.generate.sites import reference_seeding
from terrafolio.model.prng import Xorshift32

__all__ = ["DrawSource", "project_stream", "reference_stream"]

DrawSource = Callable[[], float]
"""Anything that yields the next draw in ``[0, 1)``. Both stream sources are one."""


def reference_stream(index: int) -> DrawSource:
    """A fresh xorshift32 seeded as the reference seeds it: ``index * 97 + 13``.

    Index-keyed, and therefore only for reproducing the 48-project corpus. The
    shipped generator uses :func:`project_stream`.
    """
    stride, offset = reference_seeding()
    return Xorshift32(index * stride + offset)


def project_stream(project_id: str, assumption_set_id: str, attempt: int = 0) -> DrawSource:
    """A stream keyed on ``(project id, assumption set id)``.

    ``SeedSequence`` mixes the two properly, so neighbouring ids do not give
    correlated streams — which is exactly the failure that makes naive
    ``seed + i`` schemes unusable and why the reference needs its ``* 97``
    stride. The project id is hashed with SHA-256 rather than passed to Python's
    :func:`hash`, which is salted per process and would make a pipeline
    irreproducible between runs of the same build.

    ``attempt`` lets a project be redrawn — the bounded resample that keeps a
    generated min DSCR inside its plausibility band — without disturbing any
    other project's stream, and while staying a pure function of the project's
    own identity.
    """
    digest = hashlib.sha256(project_id.encode("utf-8")).digest()
    salt = int.from_bytes(bytes.fromhex(assumption_set_id), "big")
    entropy = [salt, int.from_bytes(digest, "big"), attempt]
    generator = np.random.default_rng(np.random.SeedSequence(entropy))

    def draw() -> float:
        return float(generator.random())

    return draw
