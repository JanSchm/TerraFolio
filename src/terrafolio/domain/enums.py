"""Closed vocabularies for files, mandates and results.

Standard library only, by design. ``config`` imports this module to key its
tables, and the numeric core imports ``config`` — so anything imported here is
transitively imported by ``optimiser``. Keeping it to the standard library is
what makes the dependency rule in the package docstring true rather than
nominal. ``tests/unit/test_import_boundaries.py`` enforces it.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Final

__all__ = [
    "Confidence",
    "Currency",
    "Effort",
    "EstimateBasis",
    "ProvenanceGroup",
    "RiskAppetite",
    "RunStatus",
    "Stage",
    "Technology",
    "WarningCode",
    "WarningSeverity",
]


class Technology(StrEnum):
    """Generation technology (``docs/pipeline-schema.md`` §4.2).

    ``solar_pv`` rather than ``solar``: the value names a technology, not a
    resource, and is symmetric with the two wind members. Spec §7.4 lists the
    three as "Solar PV, onshore wind, offshore wind".
    """

    SOLAR_PV = "solar_pv"
    ONSHORE_WIND = "onshore_wind"
    OFFSHORE_WIND = "offshore_wind"

    @property
    def is_wind(self) -> bool:
        """Offshore wind counts as wind in the technology-split objective (§5.1)."""
        return self in (Technology.ONSHORE_WIND, Technology.OFFSHORE_WIND)


class Stage(StrEnum):
    """Development stage. Brownfield is out of scope for v1 (epic §12 Q6)."""

    GREENFIELD = "greenfield"
    READY_TO_BUILD = "ready_to_build"
    CONSTRUCTION = "construction"


class Currency(StrEnum):
    """ISO 4217 currency of the file's statements.

    A closed vocabulary, not free text (issue 1A). Money is never pre-converted:
    a non-EUR file is screened out by the §5.3 EUR-only toggle rather than
    translated (``docs/decisions.md`` Q-8).
    """

    EUR = "EUR"
    GBP = "GBP"
    PLN = "PLN"
    RON = "RON"
    DKK = "DKK"
    SEK = "SEK"
    NOK = "NOK"
    CZK = "CZK"
    HUF = "HUF"
    BGN = "BGN"
    CHF = "CHF"


class EstimateBasis(StrEnum):
    """How firm a declared number is, hardest to softest.

    Ordered deliberately: ``docs/pipeline-schema.md`` §8.1 presents the ladder in
    this sequence, and the dispersion report weights by it.
    """

    CONTRACTED = "contracted"
    BINDING_OFFER = "binding_offer"
    ENGINEERING_ESTIMATE = "engineering_estimate"
    BENCHMARK = "benchmark"
    INTERNAL_MODEL = "internal_model"
    PLACEHOLDER = "placeholder"


class Confidence(StrEnum):
    """The analyst's own judgement of the range around a number.

    Independent of basis: a ``contracted`` price can still be ``medium``
    confidence if volumes are uncertain (§8.2).
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ProvenanceGroup(StrEnum):
    """The seven groups a ``provenance.fields`` block must cover (§8).

    Values are the JSON keys verbatim, so ``debtTerms`` is camelCase here while
    the others happen to be single words.
    """

    GENERATION = "generation"
    PRICE = "price"
    CAPEX = "capex"
    OPEX = "opex"
    DEBT_TERMS = "debtTerms"
    GRID = "grid"
    OM = "om"


class RiskAppetite(StrEnum):
    """Development-risk appetite (§5.3). Its two caps live in the assumption set."""

    LOW = "low"
    BALANCED = "balanced"
    HIGH = "high"


class Effort(StrEnum):
    """Search effort (§10.1). Population and generations live in the assumption set."""

    FAST = "fast"
    STANDARD = "standard"
    EXHAUSTIVE = "exhaustive"


class RunStatus(StrEnum):
    """Lifecycle of a stored run."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WarningSeverity(StrEnum):
    """Wire-level severity of a feasibility warning.

    Two values only, matching ``docs/api.md``. Whether a warning *blocks* is not
    a severity: it is :attr:`WarningCode.disables_run`, true for exactly two
    codes.
    """

    ALERT = "alert"
    INFO = "info"


class WarningCode(IntEnum):
    """Feasibility warnings, in spec §5.4's order of severity.

    **The enum ordering is the severity ordering**, ascending, so
    ``sorted(codes)`` is display order and the most severe comes first. Values
    are banded by severity with gaps between them, so inserting a code never
    renumbers its neighbours.

    The integer is internal only. Every wire and stored representation carries
    :attr:`name` — ``docs/api.md`` pins ``{"code": "CAPACITY_BELOW_TARGET"}`` —
    because runs are immutable and addressable indefinitely (§11), and an
    ordinal that shifts would silently rewrite the meaning of a stored run.
    """

    # Blocking — the run button is disabled.
    NO_CANDIDATES = 1010
    LOCKS_EXCEED_CAPITAL = 1020
    # Advisory, alert tone.
    CAPACITY_BELOW_TARGET = 2010
    LEVERAGE_UNREACHABLE = 2020
    # Advisory, neutral tone.
    SOLAR_MIX_UNREACHABLE = 3010
    CAPITAL_UNDERUSED = 3020
    LOCKS_PRESENT = 3030

    @property
    def severity(self) -> WarningSeverity:
        """The wire-level severity (``docs/api.md``)."""
        return _SEVERITY[self]

    @property
    def disables_run(self) -> bool:
        """True iff this warning stops the mandate being run at all.

        Only two codes do. Everything else is advisory: §5.4 allows the user to
        run an infeasible-looking mandate and see how close the optimiser gets.
        """
        return self in _BLOCKING


_SEVERITY: Final[dict[WarningCode, WarningSeverity]] = {
    WarningCode.NO_CANDIDATES: WarningSeverity.ALERT,
    WarningCode.LOCKS_EXCEED_CAPITAL: WarningSeverity.ALERT,
    WarningCode.CAPACITY_BELOW_TARGET: WarningSeverity.ALERT,
    WarningCode.LEVERAGE_UNREACHABLE: WarningSeverity.ALERT,
    WarningCode.SOLAR_MIX_UNREACHABLE: WarningSeverity.INFO,
    WarningCode.CAPITAL_UNDERUSED: WarningSeverity.INFO,
    WarningCode.LOCKS_PRESENT: WarningSeverity.INFO,
}

_BLOCKING: Final[frozenset[WarningCode]] = frozenset(
    {WarningCode.NO_CANDIDATES, WarningCode.LOCKS_EXCEED_CAPITAL}
)

SEVERITY_ORDER: Final[tuple[WarningCode, ...]] = (
    WarningCode.NO_CANDIDATES,
    WarningCode.LOCKS_EXCEED_CAPITAL,
    WarningCode.CAPACITY_BELOW_TARGET,
    WarningCode.LEVERAGE_UNREACHABLE,
    WarningCode.SOLAR_MIX_UNREACHABLE,
    WarningCode.CAPITAL_UNDERUSED,
    WarningCode.LOCKS_PRESENT,
)
"""Spec §5.4's order, written out.

Redundant against the enum values on purpose: the test that asserts
``sorted(WarningCode) == SEVERITY_ORDER`` is what catches a code added without a
considered rank, and a reorder is then a change to this tuple and the values
together rather than a silent renumbering.
"""
