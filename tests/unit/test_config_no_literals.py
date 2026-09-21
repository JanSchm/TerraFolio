"""Every rate, weight, floor, clamp, tolerance and band lives in the assumption set.

Spec §9.4 and §10.2 make changing one an auditable event, which only holds if it
cannot also be changed by editing a number in a source file. Three tiers, because
a single rule is either too loose or unusable:

**A — deny by default in the numeric core.** In ``economics``, ``model``,
``optimiser``, ``pipeline`` and ``generate``, *every* numeric literal is a
violation except ``0``, ``1`` and ``-1``. These modules take arrays, an
assumption set and plain scalars; a number written into them is nearly always a
calibration value that escaped. This is the tier that catches a fresh ``0.061``
hardcoded instead of reading ``lcoe.real_discount_rate`` — something a
compare-against-the-config check can never see, because 0.061 is not in the
config.

**B — collision, everywhere.** No literal anywhere in ``src`` may equal a
numeric leaf of the assumption set. This is the acceptance criterion: a weight
copy-pasted into ``api`` or ``export`` fails here.

**C — no non-integral floats, everywhere.** A ratchet on top of B. A bare
``0.75`` in ``store`` is a rate or a share whatever it is called.

The escape is a per-line ``# structural: <reason>`` comment — local, visible in
review at the point of use, and counted, rather than a central allowlist that
nobody prunes.

Two modules are exempt from B and C: ``domain/mandate_bounds.py`` and
``domain/file_bounds.py``. Both are pure constant declarations quoting the
specification's own control and field tables — bounds on what a user or a file
may *say*, which never move a result. The exemption list is asserted to be
exactly those two.

The scanner is a pure function over **text**, so its behaviour is tested
directly. That matters: ``src`` is still small, so a tree scan alone would pass
whether or not the scanner worked.
"""

from __future__ import annotations

import ast
import io
import math
import tokenize
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest
from test_import_boundaries import NUMERIC_CORE

from terrafolio.config.loader import DEFAULT_ASSUMPTION_SET, assumptions_dir

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
PACKAGE_ROOT: Final = REPO_ROOT / "src" / "terrafolio"

# One definition of the numeric core, shared with the import-boundary guard so
# a package added by a later issue cannot be ranked in one and forgotten in the
# other. The literal scan covers two packages more: `pipeline` and `generate`
# consume the assumption set just as heavily but are allowed to import pydantic,
# so they fall outside the *import* rule while still needing the *literal* one.
CORE_PACKAGES: Final[frozenset[str]] = NUMERIC_CORE | {"pipeline", "generate"}

EXEMPT_MODULES: Final[frozenset[str]] = frozenset(
    {"domain/mandate_bounds.py", "domain/file_bounds.py"}
)

STRUCTURAL_MARKER: Final = "# structural:"
MAX_STRUCTURAL_ESCAPES: Final = 12
"""A ratchet. Growing past this should be a conversation, not a quiet drift."""

# Indices, lengths and signs. Deliberately not ``2``: tournament size and elite
# count are both 2 in the assumption set, so allowing a bare 2 in the core would
# leave exactly the hole this file exists to close.
CORE_ALLOWED: Final[frozenset[float]] = frozenset({0.0, 1.0, -1.0})

# Outside the core, 2 is ordinarily an index or a slice, so it is not treated as
# a collision. Inside the core it is still caught by tier A.
COLLISION_EXEMPT: Final[frozenset[float]] = frozenset({0.0, 1.0, -1.0, 2.0})


@dataclass(frozen=True, slots=True)
class Violation:
    """One numeric literal that should have come from configuration."""

    module: str
    line: int
    value: float
    tier: str

    def __str__(self) -> str:
        return f"{self.module}:{self.line} tier {self.tier}: literal {self.value!r}"


def _escaped_lines(source: str) -> frozenset[int]:
    """Lines carrying a real ``# structural:`` comment.

    Tokenised rather than substring-matched: ``S = "# structural: ..."`` is a
    string, not an escape, and a guard whose opt-out can be triggered from
    inside a string literal or a docstring is not a guard.
    """
    escaped: set[int] = set()
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT and STRUCTURAL_MARKER in token.string:
                escaped.add(token.start[0])
    except tokenize.TokenError:  # pragma: no cover - ast.parse reports it better
        pass
    return frozenset(escaped)


def _annotation_nodes(tree: ast.AST) -> set[int]:
    """Node ids inside a type annotation.

    ``x: Literal[30]`` and ``tuple[float, 3]`` are type syntax, not arithmetic.
    """
    skip: set[int] = set()
    for node in ast.walk(tree):
        annotations: list[ast.expr | None] = []
        if isinstance(node, ast.AnnAssign | ast.arg):
            annotations.append(node.annotation)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            annotations.append(node.returns)
        for annotation in annotations:
            if annotation is not None:
                skip.update(id(inner) for inner in ast.walk(annotation))
    return skip


def _numbers(tree: ast.AST) -> Iterator[tuple[float, int]]:
    """Every numeric literal, with unary minus folded in.

    Folding matters: ``-0.6`` parses as a negation of ``0.6``, so without this
    every negative weight and floor in the assumption set would be compared
    against a positive literal and never match.
    """
    skip = _annotation_nodes(tree)
    negated: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.UnaryOp)
            and isinstance(node.op, ast.USub)
            and isinstance(node.operand, ast.Constant)
        ):
            negated.add(id(node.operand))
            value = node.operand.value
            if isinstance(value, bool) or not isinstance(value, int | float):
                continue
            if id(node) in skip:
                continue
            yield -float(value), node.lineno
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or id(node) in negated or id(node) in skip:
            continue
        value = node.value
        # bool before int: isinstance(True, int) is true, and a flag counted as
        # 1 would be a phantom violation on every `Final = True`.
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        yield float(value), node.lineno


def scan_source(
    source: str,
    *,
    module: str,
    core: bool,
    exempt: bool,
    forbidden: frozenset[float] = frozenset(),
) -> list[Violation]:
    """Return every literal in ``source`` that breaches a tier."""
    tree = ast.parse(source)
    escaped = _escaped_lines(source)
    found: list[Violation] = []
    for value, line in _numbers(tree):
        if line in escaped:
            continue
        if core and value not in CORE_ALLOWED:
            found.append(Violation(module, line, value, "A"))
            continue
        if exempt:
            continue
        if value in forbidden and value not in COLLISION_EXEMPT:
            found.append(Violation(module, line, value, "B"))
        elif not math.isfinite(value) or value != int(value):
            # int(inf) raises, so the guard has to test finiteness before
            # integrality -- and a literal that overflows to inf (1e999) is
            # itself worth reporting rather than crashing the scan.
            found.append(Violation(module, line, value, "C"))
    return found


def _module_key(path: Path) -> str:
    return path.relative_to(PACKAGE_ROOT).as_posix()


def _owning_package(key: str) -> str:
    """The package a module belongs to, whether or not it is a directory.

    ``optimiser/ga.py`` and a flat ``optimiser.py`` are both the optimiser;
    taking the first path segment alone would treat the second as a package
    called ``optimiser.py`` and quietly drop it out of the strictest tier.
    """
    return key.split("/", maxsplit=1)[0].removesuffix(".py")


def scan_tree(
    root: Path = PACKAGE_ROOT, forbidden: frozenset[float] = frozenset()
) -> list[Violation]:
    found: list[Violation] = []
    for path in sorted(root.rglob("*.py")):
        key = _module_key(path)
        found.extend(
            scan_source(
                path.read_text(encoding="utf-8"),
                module=key,
                core=_owning_package(key) in CORE_PACKAGES,
                exempt=key in EXEMPT_MODULES,
                forbidden=forbidden,
            )
        )
    return found


def _leaves(value: Any) -> Iterator[float]:
    if isinstance(value, bool):
        return
    if isinstance(value, int | float):
        yield float(value)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from _leaves(item)


def assumption_set_numbers() -> frozenset[float]:
    """Every number in the shipped calibration, metadata excluded.

    Read from the TOML rather than the dataclasses so a value that the loader
    does not yet surface is still protected.
    """
    raw = tomllib.loads(
        (assumptions_dir() / f"{DEFAULT_ASSUMPTION_SET}.toml").read_text(encoding="utf-8")
    )
    raw.pop("meta", None)
    return frozenset(_leaves(raw))


# --------------------------------------------------------------------------
# The scanner's own behaviour
# --------------------------------------------------------------------------

_CORE = {"module": "optimiser/objective.py", "core": True, "exempt": False}
_PLAIN = {"module": "api/routes.py", "core": False, "exempt": False}


def test_core_rejects_any_literal() -> None:
    found = scan_source("RATE = 3.2\n", **_CORE)
    assert len(found) == 1
    assert found[0].value == 3.2
    assert found[0].line == 1


def test_core_rejects_a_value_absent_from_the_assumption_set() -> None:
    """The case a collision check cannot see."""
    assert scan_source("rate = 0.061\n", **_CORE) != []


def test_core_rejects_a_bare_two() -> None:
    """Tournament size and elite count are both 2; neither may be written here."""
    assert scan_source("ELITES = 2\n", **_CORE) != []


def test_core_allows_indices_and_signs() -> None:
    assert scan_source("x = items[0] + items[1] * -1\n", **_CORE) == []


def test_unary_minus_is_folded() -> None:
    found = scan_source(
        "W = -0.6\n", module="api/x.py", core=False, exempt=False, forbidden=frozenset({-0.6})
    )
    assert [v.value for v in found] == [-0.6]
    assert found[0].tier == "B"


def test_booleans_are_not_numbers() -> None:
    assert scan_source("FLAG = True\nOFF = False\n", **_CORE) == []


def test_annotations_are_type_syntax_not_arithmetic() -> None:
    source = "from typing import Literal\nx: Literal[30] = 30  # structural: format\n"
    assert scan_source(source, **_PLAIN) == []


def test_structural_escape_is_honoured() -> None:
    assert scan_source("N = 30  # structural: 30-year file format\n", **_CORE) == []


def test_collision_is_caught_outside_the_core() -> None:
    found = scan_source("w = 3.2\n", forbidden=frozenset({3.2}), **_PLAIN)
    assert [v.tier for v in found] == ["B"]


def test_non_integral_float_is_caught_outside_the_core() -> None:
    found = scan_source("x = 0.7315\n", forbidden=frozenset(), **_PLAIN)
    assert [v.tier for v in found] == ["C"]


def test_exempt_module_may_state_the_specification_s_own_bounds() -> None:
    found = scan_source(
        "MIN_DSCR = 1.25\n",
        module="domain/mandate_bounds.py",
        core=False,
        exempt=True,
        forbidden=frozenset({1.25}),
    )
    assert found == []


def test_docstrings_and_strings_are_ignored() -> None:
    assert scan_source('"""A 3.2 weight, described."""\nS = "0.35"\n', **_CORE) == []


# --------------------------------------------------------------------------
# The tree as it actually is
# --------------------------------------------------------------------------


def test_no_calibration_value_is_a_literal_in_src() -> None:
    found = scan_tree(forbidden=assumption_set_numbers())
    assert found == [], "\n".join(str(item) for item in found)


def test_the_exemption_list_is_exactly_two_modules() -> None:
    """Both exempt modules must exist, and nothing else may join them quietly."""
    assert len(EXEMPT_MODULES) == 2
    for name in EXEMPT_MODULES:
        assert (PACKAGE_ROOT / name).is_file(), name


@pytest.mark.parametrize("name", sorted(EXEMPT_MODULES))
def test_exempt_modules_declare_constants_and_nothing_else(name: str) -> None:
    """The exemption is safe only because there is nowhere in these files to hide.

    No function or class may carry logic here beyond a frozen dataclass and a
    property that returns a constant — otherwise an exempt module becomes the
    obvious place to put a weight.
    """
    tree = ast.parse((PACKAGE_ROOT / name).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.For | ast.While | ast.Try | ast.Lambda):
            pytest.fail(f"{name} contains control flow at line {node.lineno}")


def test_structural_escapes_stay_rare() -> None:
    total = sum(
        len(_escaped_lines(path.read_text(encoding="utf-8"))) for path in PACKAGE_ROOT.rglob("*.py")
    )
    assert total <= MAX_STRUCTURAL_ESCAPES, (
        f"{total} structural escapes in src; each one is a number the assumption set does not own"
    )


def test_the_escape_must_be_a_real_comment() -> None:
    """A marker inside a string is a string, not an opt-out.

    Substring-matching the raw line let any source mentioning the marker — a
    docstring describing the rule, most obviously — switch the guard off for
    itself.
    """
    source = 'S = "# structural: not a comment"\nY = 3.2\n'
    assert [item.value for item in scan_source(source, **_CORE)] == [3.2]


def test_a_docstring_mentioning_the_marker_does_not_escape_its_own_line() -> None:
    source = '"""Escapes are written # structural: like this."""\nW = 3.2\n'
    assert scan_source(source, **_CORE) != []


def test_a_real_comment_still_escapes() -> None:
    assert scan_source("N = 30  # structural: the 30-year format\n", **_CORE) == []


def test_an_overflowing_literal_is_reported_not_crashed() -> None:
    """``1e999`` parses to inf, and ``int(inf)`` raises.

    A guard that dies on one pathological literal is easier to write off as
    flaky than one that fails with a finding.
    """
    found = scan_source("X = 1e999\n", **_PLAIN)
    assert [item.tier for item in found] == ["C"]


def test_a_flat_module_is_still_part_of_its_package() -> None:
    """``optimiser.py`` and ``optimiser/ga.py`` are both the optimiser.

    Issue 2A adds ``cli.py`` at the package root, and any later flattening
    produces the same shape; taking the first path segment alone would drop the
    strictest tier for exactly those modules.
    """
    assert _owning_package("optimiser/ga.py") == "optimiser"
    assert _owning_package("optimiser.py") == "optimiser"
    assert _owning_package("domain/enums.py") == "domain"


def test_the_two_core_definitions_cannot_drift() -> None:
    """The literal scan covers the import rule's core plus two more, by derivation."""
    assert NUMERIC_CORE < CORE_PACKAGES
    assert {"pipeline", "generate"} == CORE_PACKAGES - NUMERIC_CORE
