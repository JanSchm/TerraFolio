"""The dependency direction, enforced rather than described.

The epic states one chain::

    api -> runner -> optimiser -> economics -> pipeline -> config -> domain

and one rule for the numeric core: ``economics``, ``model`` and ``optimiser``
import numpy, the standard library and ``config`` only — never pydantic, never
HTTP, never the store. That is what keeps them testable at 1e-12 and cheap to
start in a worker process.

Three checks, because one is not enough:

1. **Static, per module.** No module imports a package ranked above it, and no
   numeric-core module imports a forbidden package directly.
2. **Static, transitive.** The union of third-party packages reachable from the
   core is ``{numpy}``. A direct-import check alone would pass while the
   boundary was a fiction: ``config`` imports ``domain``, so a pydantic
   ``domain`` would put pydantic inside ``optimiser`` through a legal edge.
3. **At runtime.** A child process poisons ``pydantic`` on ``sys.meta_path`` and
   imports the core. This is the one that cannot be fooled by a dynamic import.

The checker is a pure function over **text**, so its own behaviour is tested
directly — which is what makes this meaningful today, while nine of the eleven
packages are still empty.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

PACKAGE_ROOT: Final = Path(__file__).resolve().parents[2] / "src" / "terrafolio"
PACKAGE: Final = "terrafolio"

LAYERS: Final[tuple[str, ...]] = (
    "domain",
    "config",
    "pipeline",
    "economics",
    "model",
    "optimiser",
    "generate",
    "store",
    "runner",
    "export",
    "api",
)
"""Rank order. A package may import anything ranked strictly below it.

The epic pins seven of these as a chain; the other four — ``model``,
``generate``, ``store``, ``export`` — it does not place, so they are ranked here
by what they consume. A later issue may re-rank one of those four in this tuple
if it needs to, provided the epic's chain still reads in order.
"""

NUMERIC_CORE: Final[frozenset[str]] = frozenset({"economics", "model", "optimiser"})
"""The packages that take arrays, an assumption set and plain scalars."""

CORE_THIRD_PARTY: Final[frozenset[str]] = frozenset({"numpy"})
"""The only third-party package the numeric core may reach, directly or not."""

PYDANTIC_FREE_DOMAIN: Final[frozenset[str]] = frozenset(
    {"enums", "conventions", "scalars", "mandate_bounds", "file_bounds"}
)
"""``domain`` submodules that import nothing beyond the standard library.

The core may use these — it genuinely needs ``Technology`` and ``YEARS``. The
pydantic half of ``domain`` is off limits, and so is ``domain`` itself as a
bare import, because a re-export added there later would quietly widen this.
"""


@dataclass(frozen=True, slots=True)
class ImportFacts:
    """Every absolute dotted import target a module names."""

    module: str
    targets: frozenset[str]


def _resolve_relative(module: str, level: int, target: str | None) -> str:
    """Turn ``from ..config import x`` inside ``a.b.c`` into ``a.config``.

    Relative imports are the common style inside a package, so getting this
    wrong would make the whole check vacuous rather than merely incomplete.
    """
    parts = module.split(".")[:-level]
    return ".".join([*parts, target]) if target else ".".join(parts)


def analyse_imports(source: str, *, module: str) -> ImportFacts:
    """Collect import targets from source text.

    Uses ``ast.walk`` rather than the top level: an import inside a function or
    a ``try: ... except ImportError`` still creates the dependency.

    ``if TYPE_CHECKING:`` is **not** exempt, deliberately. A function in
    ``economics`` annotated as taking a pydantic ``Mandate`` has already crossed
    the boundary, whether or not any bytecode imports it — the core takes plain
    scalars, and needing the annotation is the signal to add a reduction, not a
    guarded import.
    """
    targets: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _resolve_relative(module, node.level, node.module)
            else:
                base = node.module or ""
            if base == "__future__":
                continue
            targets.add(base)
            targets.update(f"{base}.{alias.name}" for alias in node.names)
    return ImportFacts(module=module, targets=frozenset(targets))


def module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT).with_suffix("")
    parts = [part for part in relative.parts if part != "__init__"]
    return ".".join([PACKAGE, *parts])


def build_graph(root: Path = PACKAGE_ROOT) -> list[ImportFacts]:
    return [
        analyse_imports(path.read_text(encoding="utf-8"), module=module_name(path))
        for path in sorted(root.rglob("*.py"))
    ]


def _package_of(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) < 2 or parts[0] != PACKAGE:
        return None
    return parts[1] if parts[1] in LAYERS else None


def _third_party_roots(targets: Iterable[str]) -> set[str]:
    roots: set[str] = set()
    for target in targets:
        root = target.split(".")[0]
        if root == PACKAGE or root in sys.stdlib_module_names:
            continue
        roots.add(root)
    return roots


def layer_violations(facts: Iterable[ImportFacts]) -> list[str]:
    """Imports that point at a package ranked at or above the importer's own."""
    rank = {name: index for index, name in enumerate(LAYERS)}
    problems: list[str] = []
    for fact in facts:
        owner = _package_of(fact.module)
        if owner is None:
            continue
        for target in sorted(fact.targets):
            imported = _package_of(target)
            if imported is None or imported == owner:
                continue
            if rank[imported] >= rank[owner]:
                problems.append(f"{fact.module} imports {target}: {imported} is not below {owner}")
    return problems


def core_violations(facts: Iterable[ImportFacts]) -> list[str]:
    """Numeric-core modules reaching outside numpy, stdlib and the allowed leaves."""
    problems: list[str] = []
    for fact in facts:
        owner = _package_of(fact.module)
        if owner not in NUMERIC_CORE:
            continue
        for root in sorted(_third_party_roots(fact.targets) - CORE_THIRD_PARTY):
            problems.append(f"{fact.module} imports {root}, which the numeric core may not")
        for target in sorted(fact.targets):
            if not target.startswith(f"{PACKAGE}.domain"):
                continue
            leaf = target.removeprefix(f"{PACKAGE}.domain").lstrip(".").split(".")[0]
            if leaf and leaf not in PYDANTIC_FREE_DOMAIN:
                problems.append(
                    f"{fact.module} imports {target}: only the pydantic-free domain "
                    f"leaves ({', '.join(sorted(PYDANTIC_FREE_DOMAIN))}) are available "
                    f"to the numeric core"
                )
    return problems


def transitive_third_party(facts: Iterable[ImportFacts], start: str) -> set[str]:
    """Every third-party root reachable from one first-party package.

    The walk is seeded with the package's own modules **and with
    ``terrafolio/__init__.py``**: importing ``terrafolio.optimiser`` executes
    the package root first, so an import added there is genuinely an import of
    the numeric core — but nothing names ``terrafolio`` as a target, so it would
    never be reached by following edges.
    """
    by_module = {fact.module: fact for fact in facts}
    seen: set[str] = set()
    roots: set[str] = set()
    queue = [name for name in by_module if _package_of(name) == start]
    if PACKAGE in by_module:
        queue.append(PACKAGE)
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        fact = by_module.get(current)
        if fact is None:
            continue
        roots |= _third_party_roots(fact.targets)
        queue.extend(target for target in fact.targets if target in by_module)
    return roots


# --------------------------------------------------------------------------
# The checker's own behaviour — provable while the tree is still mostly empty
# --------------------------------------------------------------------------


def test_checker_flags_pydantic_in_the_optimiser() -> None:
    facts = analyse_imports("from pydantic import BaseModel\n", module="terrafolio.optimiser.ga")
    assert core_violations([facts]) != []


def test_checker_flags_a_back_edge() -> None:
    facts = analyse_imports("from terrafolio.api import app\n", module="terrafolio.optimiser.ga")
    assert layer_violations([facts]) != []


def test_checker_flags_a_pydantic_bearing_domain_module() -> None:
    facts = analyse_imports(
        "from terrafolio.domain.mandate import Mandate\n",
        module="terrafolio.economics.irr",
    )
    assert core_violations([facts]) != []


def test_checker_allows_the_pydantic_free_leaves() -> None:
    facts = analyse_imports(
        "import numpy as np\nfrom terrafolio.domain.enums import Technology\n",
        module="terrafolio.economics.irr",
    )
    assert core_violations([facts]) == []


def test_checker_resolves_relative_imports() -> None:
    facts = analyse_imports("from ..api import app\n", module="terrafolio.optimiser.ga")
    assert "terrafolio.api" in facts.targets
    assert layer_violations([facts]) != []


def test_checker_sees_imports_inside_functions_and_try_blocks() -> None:
    source = (
        "def build():\n"
        "    import pydantic\n"
        "    return pydantic\n"
        "try:\n"
        "    import fastapi\n"
        "except ImportError:\n"
        "    fastapi = None\n"
    )
    facts = analyse_imports(source, module="terrafolio.optimiser.ga")
    assert {"pydantic", "fastapi"} <= facts.targets


def test_checker_does_not_exempt_type_checking_imports() -> None:
    source = (
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pydantic import BaseModel\n"
    )
    facts = analyse_imports(source, module="terrafolio.optimiser.ga")
    assert core_violations([facts]) != []


def test_the_package_root_is_part_of_the_core_s_reachable_set() -> None:
    """A third-party import in ``terrafolio/__init__.py`` is one the core makes.

    Nothing imports the package root by name, so following edges alone never
    arrives there — yet every ``import terrafolio.optimiser`` executes it.
    """
    facts = [
        analyse_imports("import pydantic\n", module=PACKAGE),
        analyse_imports("", module=f"{PACKAGE}.optimiser"),
    ]
    assert "pydantic" in transitive_third_party(facts, "optimiser")


def test_checker_ignores_future_imports() -> None:
    facts = analyse_imports(
        "from __future__ import annotations\n", module="terrafolio.optimiser.ga"
    )
    assert facts.targets == frozenset()


# --------------------------------------------------------------------------
# The tree as it actually is
# --------------------------------------------------------------------------


def test_every_package_has_a_layer() -> None:
    """A package added by a later issue must be ranked, not silently unchecked."""
    on_disk = {
        path.name
        for path in PACKAGE_ROOT.iterdir()
        if path.is_dir() and not path.name.startswith(("_", "."))
    }
    assert on_disk == set(LAYERS)


def test_no_back_edges() -> None:
    assert layer_violations(build_graph()) == []


def test_numeric_core_imports_nothing_it_should_not() -> None:
    assert core_violations(build_graph()) == []


@pytest.mark.parametrize("package", sorted(NUMERIC_CORE))
def test_numeric_core_reaches_only_numpy_transitively(package: str) -> None:
    reachable = transitive_third_party(build_graph(), package)
    assert reachable <= CORE_THIRD_PARTY, (
        f"{package} transitively reaches {sorted(reachable - CORE_THIRD_PARTY)}"
    )


def test_domain_init_imports_nothing() -> None:
    """``domain/__init__.py`` must stay empty of imports.

    ``config`` imports ``domain.enums``, and the numeric core imports ``config``.
    A convenience re-export here would execute on every one of those imports and
    drag pydantic across the boundary — while every static check still passed.
    """
    source = (PACKAGE_ROOT / "domain" / "__init__.py").read_text(encoding="utf-8")
    facts = analyse_imports(source, module="terrafolio.domain")
    assert facts.targets == frozenset()


# --------------------------------------------------------------------------
# The runtime proof
# --------------------------------------------------------------------------

_POISON = """
import importlib, sys

BLOCKED = {"pydantic", "pydantic_core", "pydantic_settings", "fastapi", "starlette",
           "httpx", "uvicorn", "openpyxl"}

class Blocked:
    def find_spec(self, fullname, path, target=None):
        if fullname.split(".")[0] in BLOCKED:
            raise AssertionError(f"imported {fullname}, which the numeric core may not")
        return None

sys.meta_path.insert(0, Blocked())
MODULES = __MODULES__
for name in MODULES:
    importlib.import_module(name)
print("clean")
"""


def _import_under_poison(modules: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _POISON.replace("__MODULES__", repr(modules))],
        capture_output=True,
        text=True,
        check=False,
    )


def test_numeric_core_imports_without_pydantic_present() -> None:
    """A fresh process, with pydantic poisoned, can still import the core."""
    result = _import_under_poison([f"{PACKAGE}.{name}" for name in sorted(NUMERIC_CORE)])
    assert result.returncode == 0, result.stderr


def test_config_loads_an_assumption_set_without_pydantic() -> None:
    """The check that has teeth today.

    ``config`` imports ``domain.enums`` and is imported by the numeric core. If
    the assumption set were a pydantic model — or if ``domain.enums`` grew a
    pydantic import — this fails, and it is the only check here that would
    notice before any optimiser code exists.
    """
    result = _import_under_poison(
        [f"{PACKAGE}.config.loader", f"{PACKAGE}.domain.enums", f"{PACKAGE}.domain.scalars"]
    )
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout
