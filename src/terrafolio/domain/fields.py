"""Annotated field types shared by the file, mandate and result models.

Two themes run through this module.

**Strictness over coercion.** Pydantic's default lax mode accepts ``"180"`` and
``True`` where a ``float`` is declared, converting the latter to ``1.0``. For an
analyst-authored JSON file that turns a type error into a plausible financial
input, which is precisely the failure the schema exists to prevent. The strict
variants accept ``int`` for ``float`` — a lossless widening, and ``34`` is a
legitimate way to write ``34.0`` in JSON — and reject everything else.

**Immutability that reaches the contents.** ``frozen=True`` prevents attribute
assignment and nothing more: a ``dict`` field on a frozen model can still be
mutated in place, so a validated file could drift from the hash taken over it.
:data:`FrozenMap` closes that, as tuples already do for the series types.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Final

from pydantic import AfterValidator, Field

from terrafolio.domain.conventions import YEARS

__all__ = [
    "FREEZE_MAPPING",
    "Count",
    "Flag",
    "MagnitudeSeries30",
    "Number",
    "Series30",
    "Series30Opt",
    "Years30",
    "exact_years",
]

Number = Annotated[float, Field(strict=True)]
"""A float that will not be conjured from a string or a boolean.

Accepts ``int``, because JSON has one number type and ``34`` is how a
spreadsheet export writes ``34.0``.
"""

Count = Annotated[int, Field(strict=True)]
"""An integer that rejects ``"2028"``, ``True`` and ``2028.0`` alike.

A year or a tenor written with a decimal point is a mistake worth naming rather
than a value worth rounding.
"""

Flag = Annotated[bool, Field(strict=True)]
"""A boolean that rejects ``1`` and ``"true"``."""

Magnitude = Annotated[float, Field(strict=True, ge=0)]
"""A non-negative float, for the statement lines stated as positive magnitudes."""


def exact_years[T](values: tuple[T, ...]) -> tuple[T, ...]:
    """Require exactly 30 annual values.

    The error carries the count but not the field name — pydantic prepends the
    alias path (``statements.incomeStatement.revenue``), which is what the
    analyst sees in their own file.
    """
    if len(values) != YEARS:
        raise ValueError(f"must have exactly {YEARS} annual values, got {len(values)}")
    return values


Series30 = Annotated[tuple[Number, ...], AfterValidator(exact_years)]
"""A signed 30-element annual series in €m.

A tuple, not a list. ``frozen=True`` freezes attribute *assignment*, not a
list's contents, so a list field would leave every "immutable" file mutable —
``file.statements.cashFlow.fcfe.append(0.0)`` would work, and the pipeline
snapshot hash would stop describing what is in memory.
"""

MagnitudeSeries30 = Annotated[tuple[Magnitude, ...], AfterValidator(exact_years)]
"""A 30-element series whose every value is stated as a positive magnitude.

``docs/pipeline-schema.md`` §2 fixes the sign convention: ``opex``,
``depreciation``, ``interestExpense``, ``taxExpense``, ``capex`` and
``debtRepayment`` are magnitudes that carry their sign through the identity
consuming them. A negative one still satisfies every tie-out — the identities
are linear — while making earnings look better than they are.
"""

Series30Opt = Annotated[tuple[Number | None, ...], AfterValidator(exact_years)]
"""A 30-element series permitting nulls. Only ``ratios.dscr`` uses it (§5.7)."""

Years30 = Annotated[tuple[Count, ...], AfterValidator(exact_years)]
"""The 30 calendar years the arrays are indexed by."""


def _freeze[K, V](value: Mapping[K, V]) -> Mapping[K, V]:
    return MappingProxyType(dict(value))


FREEZE_MAPPING: Final = AfterValidator(_freeze)
"""Makes a validated mapping field read-only at runtime.

Pair it with a ``Mapping[...]`` annotation — so type checkers withhold ``pop``
and ``__setitem__`` as well — and with a ``PlainSerializer(dict, ...)``, because
``mappingproxy`` is not a type pydantic knows how to write. Each use site
supplies its own serializer return type, which keeps the JSON schema exact::

    FrozenShares = Annotated[
        Mapping[str, float],
        FREEZE_MAPPING,
        PlainSerializer(dict, return_type=dict[str, float]),
    ]
"""
