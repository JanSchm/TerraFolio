"""The runtime half of the numeric-core boundary, for the modules 2A adds.

``tests/unit/test_import_boundaries.py`` already proves the static rules and poisons
``pydantic`` around ``import terrafolio.economics`` and friends. That check passes
trivially while those ``__init__.py`` files are empty, and it has a second blind spot
this module closes:

``from terrafolio.pipeline.arrays import ProjectArrays`` records only
``terrafolio.pipeline.arrays`` as an import target, so the transitive walk never visits
``pipeline/__init__.py`` — but Python executes it on every such import. A convenience
re-export of the loader there would put pydantic inside ``economics`` and ``optimiser``
at runtime while all three static checks stayed green.

So: import the **leaf modules by name**, in a child process, with every model and HTTP
package poisoned on ``sys.meta_path``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from test_import_boundaries import _import_under_poison, analyse_imports

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "terrafolio"

CORE_SAFE_MODULES = [
    "terrafolio.pipeline.arrays",
]
"""Modules the numeric core imports, which must not drag pydantic in behind them.

Grows as 2A lands ``economics`` and ``optimiser``; every module in those two packages
belongs here, along with anything under ``pipeline`` that they are allowed to reach.
"""


@pytest.mark.parametrize("module", CORE_SAFE_MODULES)
def test_leaf_module_imports_with_pydantic_poisoned(module: str) -> None:
    """Importing the leaf also executes its package ``__init__``, which is the point."""
    result: subprocess.CompletedProcess[str] = _import_under_poison([module])
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout


def test_pipeline_package_init_imports_nothing() -> None:
    """``pipeline/__init__.py`` stays empty of imports, as ``domain``'s already must.

    It is executed by every ``terrafolio.pipeline.arrays`` import the core makes, so a
    re-export added here is a back door into the numeric core.
    """
    source = (PACKAGE_ROOT / "pipeline" / "__init__.py").read_text(encoding="utf-8")
    assert analyse_imports(source, module="terrafolio.pipeline").targets == frozenset()


def test_the_poison_harness_still_has_teeth() -> None:
    """Guard the guard: a module that does import pydantic must fail under it."""
    result = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, ''); import pydantic"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, "pydantic must be installed for the poison test to mean anything"
    poisoned = _import_under_poison(["terrafolio.domain.project_file"])
    assert poisoned.returncode != 0
