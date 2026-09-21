"""``holdings.csv`` and ``cashflow.csv``, as pure functions of a stored run.

§11 requires the exports to be "generated server-side so they match the stored
result exactly". That holds here by construction rather than by care: both
functions read one :class:`~terrafolio.store.records.StoredRun` and touch
nothing else, so there is no path by which the file and the result the user is
looking at can come from different data.

**The cells carry raw numbers.** ``api.md`` §9 is explicit — "a CSV is for a
spreadsheet, not for reading" — and it is the only arrangement in which the
cash-flow column sums to ``cashflow30Y_m`` exactly and every column is
summable in Excel at all. A euro sign in a cell makes that cell text.

**The §14 formatting goes in the comment header**, which is where a human
reads and a spreadsheet ignores: the run reference and the mandate, rendered
with the euro signs, thousands separators and multiplication signs §14 asks
for. A UTF-8 BOM is what keeps those glyphs intact when Excel opens the file,
and it is also why the rounding here is half-up: Python's ``round`` is
half-even, so a DSCR of 1.125 would print 1.12 in the export against 1.13 on
the screen, from the same stored run.

Both files use ``\\r\\n``. ``docs/ui-contract.md`` §2 says ``holdings.csv`` is
formatted server-side and ``api.md`` §9 says it carries raw numbers; those
cannot both be true of the cells, and ``api.md`` is the contract the endpoint
implements, so the formatter serves the header and the committee pack instead.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from terrafolio.domain.enums import RunStatus
from terrafolio.domain.mandate import Mandate
from terrafolio.domain.results import Holding
from terrafolio.store.records import StoredRun

__all__ = [
    "CASHFLOW_COLUMNS",
    "HOLDINGS_COLUMNS",
    "cashflow_csv",
    "holdings_csv",
    "money_m",
    "multiple",
    "percent",
    "quantity",
]

HOLDINGS_COLUMNS: Final = (
    "id",
    "name",
    "country",
    "technology",
    "stage",
    "capacityMw",
    "codYear",
    "totalCapex_m",
    "equity_m",
    "gearing",
    "netCapacityFactor",
    "annualGenerationGwh",
    "lcoe",
    "ppaShare",
    "equityIrr",
    "minDscr",
    "developmentRiskScore",
)
"""``api.md`` §9.1, in that order.

Seventeen, not §7.4's sixteen: those are a *screen* layout whose first column
is the lock control — a button, not a value — and whose second packs the name,
the country and the id into one cell. The lock state is left out because it
steers the next run rather than describing this portfolio.
"""

CASHFLOW_COLUMNS: Final = ("year", "fcfe_m")

BOM: Final = "﻿"
"""A UTF-8 byte-order mark. It is what makes Excel read the header's euro signs
as euro signs rather than as mojibake (``api.md`` §9)."""

LINE_ENDING: Final = "\r\n"
SEPARATOR: Final = " · "
TIMES_SIGN: Final = "×"  # noqa: RUF001 - §14's multiplication sign, not the letter x
PERCENT_SCALE: Final = 100


def _half_up(value: float, places: str) -> Decimal:
    """Round for display, half away from zero at the stated precision.

    Neither language gives this by default — Python's ``round`` is half-even,
    JavaScript's ``toFixed`` rounds the binary value — so a figure sitting
    exactly on a half renders differently on the screen and in its own export
    unless both sides do it on purpose (``ui-contract.md`` §2).
    """
    return Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def money_m(value: float) -> str:
    """§14 money: euros in millions, thousands separators, no decimals."""
    return f"€{_half_up(value, '1'):,}m"


def quantity(value: float, unit: str) -> str:
    """§14 capacity and generation: an integer with separators, then a unit."""
    return f"{_half_up(value, '1'):,} {unit}"


def percent(value: float, *, places: int = 0) -> str:
    """§14 shares and returns. A fraction on the way in, never a percentage."""
    quantum = "1" if places == 0 else "0." + "0" * places
    return f"{_half_up(value * PERCENT_SCALE, quantum)}%"


def multiple(value: float) -> str:
    """§14 DSCR, MOIC and exit multiples: two decimals and a times sign."""
    return f"{_half_up(value, '0.01')}{TIMES_SIGN}"


def _cell(value: object) -> str:
    """One data cell: the stored number, unrounded, or empty when undefined.

    Empty rather than ``0`` for a null ``equityIrr`` or ``minDscr`` — a zero
    return and an unsolvable one are different facts, and a spreadsheet that
    averages the column must not silently include the second as the first.
    """
    if value is None:
        return ""
    if isinstance(value, bool):  # pragma: no cover - no boolean column exists today
        return str(value).lower()
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _timestamp(moment: datetime) -> str:
    """UTC in the ``...Z`` form ``api.md`` uses, not ``isoformat``'s ``+00:00``.

    The same instant either way, but a committee pack and the endpoint that
    produced it should not spell it two ways.
    """
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _mandate_line(mandate: Mandate) -> str:
    """The mandate, as §7.7 requires every export to carry it."""
    parts = (
        f"{money_m(mandate.available_capital_m)} available",
        quantity(mandate.capacity_target_mw, "MW") + " target",
        f"{percent(mandate.solar_share)} solar",
        f"{percent(mandate.target_irr, places=1)} target IRR",
        f"{mandate.hold_years}-year hold",
        f"leverage at least {percent(mandate.min_leverage)}",
        f"DSCR at least {multiple(mandate.min_dscr)}",
        f"merchant at most {percent(mandate.max_merchant_share)}",
        f"country at most {percent(mandate.max_country_share)}",
        f"project at most {percent(mandate.max_project_share)}",
        f"COD {mandate.cod_from}-{mandate.cod_to}",
        f"{mandate.risk_appetite.value} risk",
        f"{len(mandate.countries)} countries",
        f"{len(mandate.stages)} stages",
    )
    return "# Mandate: " + SEPARATOR.join(parts)


def _header(run: StoredRun) -> tuple[str, ...]:
    """The three comment lines both exports carry.

    The run reference and the mandate are §7.7's requirement. The third line is
    §12's: a CSV that leaves the building without naming the pipeline, the
    calibration and the seed cannot be traced back to a reproducible run.
    """
    record = run.record
    provenance = record.provenance
    return (
        f"# TerraFolio run {record.run_ref}{SEPARATOR}{_timestamp(record.created_at)}",
        _mandate_line(record.mandate),
        f"# Provenance: pipeline {provenance.pipeline_hash}{SEPARATOR}"
        f"assumptions {provenance.assumption_set_id}{SEPARATOR}seed {provenance.seed}",
    )


def _require_result(run: StoredRun) -> None:
    if run.record.status is not RunStatus.SUCCEEDED:
        raise ValueError(
            f"run {run.record.run_ref} is {run.record.status.value}; "
            "only a finished run has a portfolio to export"
        )


def _render(header: Iterable[str], columns: Sequence[str], rows: Iterable[Sequence[str]]) -> bytes:
    """Assemble one file.

    Bytes rather than text, because the BOM and the line ending are part of the
    contract with Excel, and a caller that re-encoded would be free to lose
    them.
    """
    buffer = io.StringIO()
    buffer.write(BOM)
    for line in header:
        buffer.write(line + LINE_ENDING)
    writer = csv.writer(buffer, lineterminator=LINE_ENDING)
    writer.writerow(columns)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _holding_row(holding: Holding) -> tuple[str, ...]:
    values: tuple[object, ...] = (
        holding.id,
        holding.name,
        holding.country,
        holding.technology.value,
        holding.stage.value,
        holding.capacity_mw,
        holding.cod_year,
        holding.total_capex_m,
        holding.equity_m,
        holding.gearing,
        holding.net_capacity_factor,
        holding.annual_generation_gwh,
        holding.lcoe,
        holding.ppa_share,
        holding.equity_irr,
        holding.min_dscr,
        holding.development_risk_score,
    )
    return tuple(_cell(value) for value in values)


def holdings_csv(run: StoredRun) -> bytes:
    """The selected portfolio, seventeen columns, ordered by ``id``.

    ``holdings`` carries every eligible candidate (A-14); this is that array
    filtered to the ones the optimiser chose, which is what §7.7 means by
    "selection only". Because both the table on screen and this file read the
    same array, they cannot disagree.
    """
    _require_result(run)
    selected = [holding for holding in run.record.holdings if holding.selected]
    return _render(_header(run), HOLDINGS_COLUMNS, [_holding_row(item) for item in selected])


def cashflow_csv(run: StoredRun) -> bytes:
    """The 30-year portfolio cash-flow schedule, one row per year.

    Over ``cashflow30Y_m``, which carries **no terminal value**, and never over
    ``cashflowHold_m``, which does. A-6 calls conflating the two the single
    most likely silent bug in the feature, and it produces a file whose numbers
    look entirely reasonable.

    The years come from the pipeline snapshot's base year: the run result does
    not carry one, and A-13 makes it a property of the whole loaded set.

    The series is exactly ``YEARS`` long because ``RunRecord`` pins that for a
    finished run; there is no second check here, which would only state the
    same invariant further from where it is enforced.
    """
    _require_result(run)
    series = run.record.cashflow_30y_m
    rows = [(_cell(run.base_year + offset), _cell(amount)) for offset, amount in enumerate(series)]
    return _render(_header(run), CASHFLOW_COLUMNS, rows)
