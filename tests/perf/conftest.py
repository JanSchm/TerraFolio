"""Make #12's sibling test directories importable, and share one traced run.

pytest puts each test file's own directory on ``sys.path`` and nothing else, which is
how ``tests/golden/test_objective_cases.py`` imports ``reference_mandates`` by bare
name. The perf suite needs 1C's reference mandates, which live one directory over.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_TESTS / "golden"), str(_TESTS / "parity")]

import pytest  # noqa: E402
from engine import Timeline, traced_run  # noqa: E402
from reference_mandates import REFERENCE_MANDATES  # noqa: E402

from terrafolio.domain.scalars import MandateScalars  # noqa: E402


@pytest.fixture(scope="session")
def mandate() -> MandateScalars:
    """1C's default reference mandate — €1,200m against 1,500 MW."""
    reference: MandateScalars = REFERENCE_MANDATES["M0-default"]
    return reference


@pytest.fixture(scope="session")
def timeline(mandate: MandateScalars) -> Timeline:
    """One traced run, shared by every guard that asserts a property of it.

    Session-scoped because the run is the expensive part and every guard here asks a
    different question of the same run. Nothing mutates it.
    """
    return traced_run(mandate)
