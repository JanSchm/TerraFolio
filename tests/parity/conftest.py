"""Make #12's sibling test directories importable from here."""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_TESTS / "perf")]
