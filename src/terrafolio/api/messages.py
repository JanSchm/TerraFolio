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
from terrafolio.domain.enums import WarningCode
from terrafolio.export.csv import money_m, percent, quantity

__all__ = ["TEMPLATES", "render"]

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
