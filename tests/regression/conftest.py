"""Make #12's sibling test directories importable from here.

pytest puts each test file's own directory on ``sys.path`` and nothing else, which is
how ``tests/golden/test_objective_cases.py`` imports ``reference_mandates`` by bare
name. The regression suite needs 1C's reference mandates and #12's own pool builder,
both of which live one directory over, so this adds them the same way.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_TESTS / "golden"), str(_TESTS / "perf")]
