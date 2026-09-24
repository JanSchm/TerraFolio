"""§5.4's warning sentences, pinned to the document that owns the copy.

``optimiser/feasibility.py`` deliberately emits a code and its numbers **in
euros** and never a string: rendering "€1,200m" inside the numeric core would put
a third unit boundary in a system that permits two, and ``ui-contract.md`` §3.5
already owns the copy. But ``docs/api.md`` §5 puts ``message`` on the wire, for
non-browser clients and for anything that cannot run ``js/feasibility.js``. So
the sentences have to exist in Python too.

They are therefore stored here **verbatim as §3.5 and §3.6 write them**, with
their own placeholders, and ``tests/api/test_messages.py`` parses those two
sections out of the markdown and asserts each template still matches. Copy that
drifts from the document is then a failing test rather than a discrepancy nobody
notices until an investor reads it.

Numbers go through ``export/csv.py``'s formatters — the same ones the CSV header
and the committee pack use — so one rounding rule (half away from zero, §2)
serves every server-rendered figure. The unit is stripped where the sentence
supplies it, rather than rounding a second time here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final

from terrafolio.domain.conventions import EUR_PER_EUR_MILLION
from terrafolio.domain.enums import RunStatus, WarningCode
from terrafolio.domain.results import FeasibilityWarning
from terrafolio.export.csv import money_m, percent, quantity
from terrafolio.optimiser.feasibility import FeasibilityPreview

__all__ = [
    "CANCELLED_CODE",
    "CANCELLED_MESSAGE",
    "ENGINE_ERROR_CODE",
    "FAILED_MESSAGE",
    "TEMPLATES",
    "render",
    "terminal_error",
    "warnings_for",
]

TEMPLATES: Final[Mapping[WarningCode, str]] = {
    WarningCode.NO_CANDIDATES: (
        "No candidates pass the current screens. Widen countries, stages or the COD window."
    ),
    WarningCode.LOCKS_EXCEED_CAPITAL: (
        "Locked projects need €{locked}m of equity against €{capital}m available. "
        "Release a lock to run."
    ),
    WarningCode.CAPACITY_BELOW_TARGET: (
        "Eligible pipeline is {mw} MW — below the {target} MW target."
    ),
    WarningCode.LEVERAGE_UNREACHABLE: (
        "Minimum leverage of {minLev}% exceeds what the eligible pool supports ({poolLev}%)."
    ),
    WarningCode.SOLAR_MIX_UNREACHABLE: (
        "Solar target of {solar}% may be unreachable: eligible pool is {poolSolar}% solar."
    ),
    WarningCode.CAPITAL_UNDERUSED: (
        "Full pipeline absorbs only €{equity}m of the €{capital}m available."
    ),
    WarningCode.LOCKS_PRESENT: "{n} project(s) locked in; {m} excluded.",
}
"""One sentence per code, exactly as ``ui-contract.md`` §3.5 and §3.6 write them.

The em dash and the euro signs are escapes rather than glyphs so that a source
scan cannot mistake a deliberate typographic character for a mistyped one, which
is the convention ``cli.py`` already follows for the multiplication sign.
"""


def _figure(value: float) -> str:
    """A grouped whole number, no unit — ``1,180``."""
    return quantity(value, "").rstrip()


def _percent(fraction: float) -> str:
    """A whole percent, no sign — ``60``. The sentence supplies the ``%``."""
    return percent(fraction).removesuffix("%")


def _millions(euros: float) -> str:
    """€m as a grouped whole number, no sign or suffix — ``1,200``.

    The euro sign and the ``m`` are literal text in §3.5's sentences, so they are
    taken off rather than rounded a second time: one rounding implementation for
    every figure the server renders is the point of routing through ``money_m``.
    """
    return money_m(euros / EUR_PER_EUR_MILLION).removeprefix("€").removesuffix("m")


_VALUES: Final[Mapping[WarningCode, Callable[[Mapping[str, float]], Mapping[str, str]]]] = {
    WarningCode.NO_CANDIDATES: lambda detail: {},
    WarningCode.LOCKS_EXCEED_CAPITAL: lambda detail: {
        "locked": _millions(detail["lockedEquity"]),
        "capital": _millions(detail["availableCapital"]),
    },
    WarningCode.CAPACITY_BELOW_TARGET: lambda detail: {
        "mw": _figure(detail["eligibleCapacityMw"]),
        "target": _figure(detail["capacityTargetMw"]),
    },
    WarningCode.LEVERAGE_UNREACHABLE: lambda detail: {
        "minLev": _percent(detail["minLeverage"]),
        "poolLev": _percent(detail["eligibleGearing"]),
    },
    WarningCode.SOLAR_MIX_UNREACHABLE: lambda detail: {
        "solar": _percent(detail["targetSolarShare"]),
        "poolSolar": _percent(detail["eligibleSolarShare"]),
    },
    WarningCode.CAPITAL_UNDERUSED: lambda detail: {
        "equity": _millions(detail["eligibleEquity"]),
        "capital": _millions(detail["availableCapital"]),
    },
    WarningCode.LOCKS_PRESENT: lambda detail: {
        "n": _figure(detail["lockedCount"]),
        "m": _figure(detail["excludedCount"]),
    },
}
"""Which of ``FeasibilitySignal.detail``'s euro figures each sentence needs.

Keyed by code and looked up rather than branched, so a code added to the enum
without a sentence is a ``KeyError`` naming it at the point of use, not a
sentence with a stray ``{placeholder}`` in it reaching a client.
"""


def render(code: WarningCode, detail: Mapping[str, float]) -> str:
    """The sentence for one warning, from its code and its **euro** numbers.

    ``detail`` is the mapping ``FeasibilitySignal`` carries: euros and fractions,
    never percentages and never €m. This function is where that becomes copy, and
    it is the only place in the API that formats a number into a sentence.
    """
    return TEMPLATES[code].format(**_VALUES[code](detail))


# --------------------------------------------------------------------------
# What a run that did not succeed says, and what a preview's warnings look like
# --------------------------------------------------------------------------

ENGINE_ERROR_CODE: Final = "ENGINE_ERROR"
CANCELLED_CODE: Final = "RUN_CANCELLED"

FAILED_MESSAGE: Final = "The search did not complete. The failure is recorded against this run."
CANCELLED_MESSAGE: Final = "The search was cancelled before it finished."
"""One sentence each, as §1.7 requires.

**Never ``run.error_message``**, which holds the worker's whole traceback for
the audit trail. A traceback is neither one sentence nor fit to show: it carries
the server's absolute paths and its internal structure to a caller that, in this
backlog, is not authenticated at all (epic §12 Q7). It stays in the store and in
the server log, where an operator reads it.
"""


def terminal_error(status: RunStatus, error_code: str | None) -> Mapping[str, str]:
    """The ``error`` block for a run that ended without a portfolio.

    One definition, used by both the HTTP body and the SSE terminal frame, so a
    client watching a run and a client reading it afterwards cannot be told two
    different stories about why it stopped.

    A cancellation is **not** an engine failure. ``error_code`` is mandatory for
    ``failed`` and optional for ``cancelled``, so defaulting the missing case to
    ``ENGINE_ERROR`` reported a run someone deliberately stopped as a crash —
    and any retry keyed on that code would have retried it.
    """
    cancelled = status is RunStatus.CANCELLED
    fallback = CANCELLED_CODE if cancelled else ENGINE_ERROR_CODE
    return {
        "code": error_code or fallback,
        "message": CANCELLED_MESSAGE if cancelled else FAILED_MESSAGE,
    }


def warnings_for(preview: FeasibilityPreview) -> tuple[FeasibilityWarning, ...]:
    """§5's warnings, ordered by severity, each with its pinned sentence.

    ``severity`` is ``alert`` or ``info``, as 1A's enum defines it. Whether a
    warning *blocks* is not a severity — it is a property of the code, and
    ``runnable`` is the signal a client acts on.
    """
    return tuple(
        FeasibilityWarning(
            code=signal.code,
            severity=signal.code.severity,
            message=render(signal.code, signal.detail),
        )
        for signal in preview.signals
    )
