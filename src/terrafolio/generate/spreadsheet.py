"""The spreadsheet path: the same schema, laid out the way a model actually is.

Analysts work in Excel, so a project has to be able to leave as a workbook,
be revised, and come back. ``docs/pipeline-schema.md`` §13 fixes the layout --
identity and assumptions as path/value/unit pairs, line items as rows and the
thirty years as columns, and a ``TieOuts`` sheet of live formulas that proves
the workbook against itself.

**One schema, not two.** The layout below is declared once and drives both
directions, so a field cannot be written to a cell the reader does not look at.
An ingested workbook is then validated through
:class:`~terrafolio.domain.project_file.ProjectFile` -- the same model a JSON
file goes through, with no second schema and no second validator.

**The geometry is load-bearing.** Every ``TieOuts`` formula is a hard-coded
``Statements!<column><row>`` reference, so inserting a line item anywhere in the
statement block silently re-points twelve rows of thirty formulas. The row map
here is therefore derived from one ordered description, and the formulas are
built from that same map rather than written out, so the two cannot disagree.
Issue #8 owns this file's *content*; #3 owns its *layout*, which is preserved
exactly -- the five settled ``assumptions`` fields are appended after
``debtTenorYears``, keeping the ``Assumptions!$B$6`` the DSCR-coverage check
depends on.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

# openpyxl ships no type information and `types-openpyxl` would be a new
# dependency, which epic §10 asks be raised first. Ignored locally with its
# code rather than globally, so the next module to import it has to make the
# same decision deliberately.
from openpyxl import Workbook, load_workbook  # type: ignore[import-untyped]
from openpyxl.cell import _writer  # type: ignore[import-untyped]
from openpyxl.utils import get_column_letter  # type: ignore[import-untyped]
from openpyxl.worksheet.worksheet import (  # type: ignore[import-untyped]
    Worksheet,
)

from terrafolio.domain.conventions import YEARS

__all__ = ["read_workbook", "write_workbook"]

# --------------------------------------------------------------------------
# The layout (docs/pipeline-schema.md §13)
# --------------------------------------------------------------------------

IDENTITY_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("schemaVersion", ""),
    ("id", ""),
    ("name", ""),
    ("location.country", ""),
    ("location.countryCode", ""),
    ("location.iso3", ""),
    ("location.lat", "degrees"),
    ("location.lon", "degrees"),
    ("asset.technology", ""),
    ("asset.stage", ""),
    ("asset.capacityMw", "MW"),
    ("asset.codYear", "year"),
    ("asset.netCapacityFactor", "fraction"),
    ("asset.opexPerKwYear", "EUR/kW/yr"),
    ("revenue.ppaShare", "fraction"),
    ("revenue.ppaPrice", "EUR/MWh"),
    ("revenue.ppaTenorYears", "years"),
    ("revenue.countryBaseloadPrice", "EUR/MWh"),
    ("revenue.captureFactor", "fraction"),
    ("execution.developmentRiskScore", "1.0-5.0"),
    ("execution.gridSecured", ""),
    ("execution.omContracted", ""),
    ("execution.currency", ""),
    ("capitalStructure.totalCapex", "EURm"),
    ("capitalStructure.seniorDebt", "EURm"),
    ("capitalStructure.maxGearing", "fraction"),
)

ASSUMPTION_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("assumptions.baseYear", "year"),
    ("assumptions.taxRate", "fraction"),
    ("assumptions.depreciationYears", "years"),
    ("assumptions.debtRate", "fraction"),
    ("assumptions.debtTenorYears", "years"),
    ("assumptions.degradationRate", "fraction/yr"),
    ("assumptions.priceEscalation", "fraction/yr"),
    ("assumptions.merchantEscalation", "fraction/yr"),
    ("assumptions.opexEscalation", "fraction/yr"),
    ("assumptions.targetDscr", "x"),
)

PROVENANCE_SCALARS: Final[tuple[str, ...]] = (
    "provenance.preparedBy",
    "provenance.preparedOn",
    "provenance.modelVersion",
)

PROVENANCE_GROUPS: Final[tuple[str, ...]] = (
    "generation",
    "price",
    "capex",
    "opex",
    "debtTerms",
    "grid",
    "om",
)

STATEMENT_BLOCKS: Final[tuple[tuple[str, tuple[tuple[str, str], ...]], ...]] = (
    ("physicals", (("generationGwh", "GWh"), ("achievedPrice", "EUR/MWh"))),
    (
        "incomeStatement",
        tuple(
            (name, "EURm")
            for name in (
                "revenue",
                "opex",
                "ebitda",
                "depreciation",
                "ebit",
                "interestExpense",
                "pbt",
                "taxExpense",
                "netIncome",
            )
        ),
    ),
    (
        "cashFlow",
        tuple(
            (name, "EURm")
            for name in (
                "interestPaid",
                "debtRepayment",
                "taxPaid",
                "capex",
                "debtDrawdown",
                "equityDrawdown",
                "fcfe",
            )
        ),
    ),
    (
        "debtSchedule",
        tuple((name, "EURm") for name in ("opening", "drawdown", "repayment", "closing")),
    ),
    ("balanceSheet", (("ppe", "EURm"),)),
    ("ratios", (("dscr", "x"),)),
)

INTEGER_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "asset.codYear",
        "revenue.ppaTenorYears",
        "assumptions.baseYear",
        "assumptions.depreciationYears",
        "assumptions.debtTenorYears",
    }
)
"""Fields the schema declares as ``int``, which it will not accept as ``18.0``.

A spreadsheet has one numeric type, so a tenor that comes back as a float has
to be narrowed here rather than left to fail validation with a message about a
type the analyst never typed.
"""

# §13's geometry, derived from the header rows this module actually writes so
# the offsets and the sheets cannot drift apart. `Statements` carries a path and
# a unit before the first year; `TieOuts` carries a check, a breach and a status,
# which is why it is shifted one column right of `Statements` for the same year.
_TEXT_CELL: Final = "s"
"""openpyxl's data type for a string cell, as opposed to ``"f"`` for a formula."""

_HEADER_ROWS: Final = 1
_FIRST_FIELD_ROW: Final = _HEADER_ROWS + 1

_PAIR_HEADER: Final = ("field", "value", "unit")
_VALUE_COLUMN: Final = _PAIR_HEADER.index("value") + 1

_SERIES_HEADER: Final = ("field", "unit")
_LEAD_COLUMNS: Final = len(_SERIES_HEADER)
_FIRST_YEAR_COLUMN: Final = _LEAD_COLUMNS + 1

_TIEOUT_HEADER: Final = ("check", "max breach", "status")
_TIEOUT_YEAR_COLUMN: Final = len(_TIEOUT_HEADER) + 1

_PROVENANCE_HEADER: Final = ("group", "estimateBasis", "confidence", "note")
_BASIS_AT: Final = _PROVENANCE_HEADER.index("estimateBasis")
_CONFIDENCE_AT: Final = _PROVENANCE_HEADER.index("confidence")
_NOTE_AT: Final = _PROVENANCE_HEADER.index("note")

_ASSUMPTIONS_FOOTNOTE: Final = (
    "Declared inputs, not configuration. The loader's dispersion report compares "
    "these across the pipeline."
)
_STATEMENTS_FOOTNOTE: Final = (
    "An empty dscr cell means null: dscr is defined only inside the debt life. "
    "Every other cell is a number."
)
_PROVENANCE_FOOTNOTE: Final = (
    "estimateBasis: contracted | binding_offer | engineering_estimate | benchmark | "
    "internal_model | placeholder.  confidence: high | medium | low.  "
    "Reported, never blocking."
)
_TIEOUTS_FOOTNOTE: Final = (
    "Live formulas against Statements, Identity and Assumptions. Each cell holds the "
    "BREACH over tolerance -- max(0, |residual| - max(absolute, 0.1% of the line)) -- so "
    "zero means the year is inside tolerance and any non-zero cell names the year that "
    "is not. The absolute limb is EUR 0.01m for a money line and 0.001 for the DSCR "
    "ratio, from the assumption set. Every check must read PASS before the file is "
    "loaded; the last one is a plausibility warning, not a blocker."
)


def statement_rows() -> dict[str, int]:
    """Map ``block.line`` to its row on the ``Statements`` sheet.

    Built from :data:`STATEMENT_BLOCKS` so the writer, the reader and every
    ``TieOuts`` formula read one description of the geometry.
    """
    rows: dict[str, int] = {}
    row = _FIRST_FIELD_ROW
    for block, lines in STATEMENT_BLOCKS:
        rows[block] = row
        row += 1
        for line, _unit in lines:
            rows[f"{block}.{line}"] = row
            row += 1
    return rows


def _dig(file: dict[str, Any], path: str) -> Any:
    node: Any = file
    for part in path.split("."):
        node = node[part]
    return node


def _plant(file: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = file
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def _append(sheet: Worksheet, values: list[Any]) -> None:
    """Append a row, writing every text value as an **explicit string cell**.

    openpyxl infers a cell's type from its value, and a string beginning with
    ``=`` becomes a *formula*. Several fields a project file may legitimately
    carry are free text an analyst controls -- ``name``, ``provenance.preparedBy``,
    ``modelVersion`` and every ``note`` -- and the schema has no reason to forbid
    a leading ``=``. Left inferred, exporting such a project writes a live
    formula into the workbook, which Excel evaluates when an analyst opens it:
    ``=HYPERLINK("http://...")`` and the ``WEBSERVICE`` family reach the network,
    and the cell no longer round-trips as the text it was.

    So text is written as text. Formulas appear in exactly one place -- the
    ``TieOuts`` sheet, which builds its own and does not come through here.
    """
    sheet.append(values)
    row = sheet.max_row
    for column, value in enumerate(values, start=1):
        if isinstance(value, str):
            sheet.cell(row=row, column=column).data_type = _TEXT_CELL


def _write_pairs(
    sheet: Worksheet,
    fields: tuple[tuple[str, str], ...],
    file: dict[str, Any],
    footnote: str | None = None,
) -> None:
    """A path/value/unit sheet, optionally closed by a blank row and a footnote."""
    _append(sheet, list(_PAIR_HEADER))
    for path, unit in fields:
        _append(sheet, [path, _dig(file, path), unit or None])
    if footnote:
        sheet.append([])
        _append(sheet, [footnote])


def _write_provenance(sheet: Worksheet, file: dict[str, Any]) -> None:
    _append(sheet, list(_PROVENANCE_HEADER))
    for path in PROVENANCE_SCALARS:
        _append(sheet, [path, _dig(file, path)])
    _append(sheet, ["per field group"])
    groups = file["provenance"]["fields"]
    for name in PROVENANCE_GROUPS:
        entry = groups[name]
        _append(sheet, [name, entry["estimateBasis"], entry["confidence"], entry.get("note")])
    sheet.append([])
    _append(sheet, [_PROVENANCE_FOOTNOTE])


def _write_statements(sheet: Worksheet, file: dict[str, Any]) -> None:
    statements = file["statements"]
    _append(sheet, [*_SERIES_HEADER, *statements["years"]])
    for block, lines in STATEMENT_BLOCKS:
        _append(sheet, [block])
        for line, unit in lines:
            values = statements[block][line]
            _append(sheet, [f"{block}.{line}", unit, *values])
    sheet.append([])
    _append(sheet, [_STATEMENTS_FOOTNOTE])


def _cell(row: int, offset: int) -> str:
    return f"Statements!{get_column_letter(_FIRST_YEAR_COLUMN + offset)}{row}"


def _breach(actual: str, expected: str, absolute: str = "0.01") -> str:
    """One year's breach over tolerance: ``max(0, |residual| - max(abs, 0.1%))``.

    Zero means the year is inside tolerance, so any non-zero cell names the year
    that is not -- which is what makes the workbook prove itself rather than
    merely display a number next to a rule.
    """
    return f"=MAX(0,ABS({actual}-({expected}))-MAX({absolute},0.001*ABS({actual})))"


def _per_year_checks(rows: dict[str, int]) -> list[tuple[str, list[str]]]:
    checks: list[tuple[str, list[str]]] = []

    def line(name: str) -> int:
        return rows[name]

    def series(label: str, build: Any, absolute: str = "0.01") -> None:
        checks.append((label, [build(offset, absolute) for offset in range(YEARS)]))

    series(
        "ebitda = revenue - opex",
        lambda t, a: _breach(
            _cell(line("incomeStatement.ebitda"), t),
            f"{_cell(line('incomeStatement.revenue'), t)}-{_cell(line('incomeStatement.opex'), t)}",
            a,
        ),
    )
    series(
        "ebit = ebitda - depreciation",
        lambda t, a: _breach(
            _cell(line("incomeStatement.ebit"), t),
            f"{_cell(line('incomeStatement.ebitda'), t)}"
            f"-{_cell(line('incomeStatement.depreciation'), t)}",
            a,
        ),
    )
    series(
        "pbt = ebit - interestExpense",
        lambda t, a: _breach(
            _cell(line("incomeStatement.pbt"), t),
            f"{_cell(line('incomeStatement.ebit'), t)}"
            f"-{_cell(line('incomeStatement.interestExpense'), t)}",
            a,
        ),
    )
    series(
        "netIncome = pbt - taxExpense",
        lambda t, a: _breach(
            _cell(line("incomeStatement.netIncome"), t),
            f"{_cell(line('incomeStatement.pbt'), t)}"
            f"-{_cell(line('incomeStatement.taxExpense'), t)}",
            a,
        ),
    )
    series(
        "revenue = generationGwh * 1000 * achievedPrice / 1e6",
        lambda t, a: _breach(
            _cell(line("incomeStatement.revenue"), t),
            f"{_cell(line('physicals.generationGwh'), t)}*1000"
            f"*{_cell(line('physicals.achievedPrice'), t)}/1000000",
            a,
        ),
    )
    series(
        "fcfe = ebitda - interestPaid - debtRepayment - taxPaid - capex + debtDrawdown",
        lambda t, a: _breach(
            _cell(line("cashFlow.fcfe"), t),
            f"{_cell(line('incomeStatement.ebitda'), t)}"
            f"-{_cell(line('cashFlow.interestPaid'), t)}"
            f"-{_cell(line('cashFlow.debtRepayment'), t)}"
            f"-{_cell(line('cashFlow.taxPaid'), t)}"
            f"-{_cell(line('cashFlow.capex'), t)}"
            f"+{_cell(line('cashFlow.debtDrawdown'), t)}",
            a,
        ),
    )
    series(
        "closing = opening - repayment + drawdown",
        lambda t, a: _breach(
            _cell(line("debtSchedule.closing"), t),
            f"{_cell(line('debtSchedule.opening'), t)}"
            f"-{_cell(line('debtSchedule.repayment'), t)}"
            f"+{_cell(line('debtSchedule.drawdown'), t)}",
            a,
        ),
    )
    # opening[0] has no prior year, so its reference is the literal zero §7.4 states.
    opening = line("debtSchedule.opening")
    closing = line("debtSchedule.closing")
    checks.append(
        (
            "opening[t] = closing[t-1], and opening[0] = 0",
            [f"=MAX(0,ABS({_cell(opening, 0)})-MAX(0.01,0.001*ABS(0)))"]
            + [_breach(_cell(opening, t), _cell(closing, t - 1)) for t in range(1, YEARS)],
        )
    )
    ppe = line("balanceSheet.ppe")
    depreciation = line("incomeStatement.depreciation")
    capex = line("cashFlow.capex")
    checks.append(
        (
            "ppe[t] = ppe[t-1] - depreciation[t] + capex[t]",
            [_breach(_cell(ppe, 0), f"0-{_cell(depreciation, 0)}+{_cell(capex, 0)}")]
            + [
                _breach(
                    _cell(ppe, t), f"{_cell(ppe, t - 1)}-{_cell(depreciation, t)}+{_cell(capex, t)}"
                )
                for t in range(1, YEARS)
            ],
        )
    )
    series(
        "capex = debtDrawdown + equityDrawdown",
        lambda t, a: _breach(
            _cell(line("cashFlow.capex"), t),
            f"{_cell(line('cashFlow.debtDrawdown'), t)}"
            f"+{_cell(line('cashFlow.equityDrawdown'), t)}",
            a,
        ),
    )
    # DSCR coverage: derived from codYear and the tenor, never from whether the
    # cell happens to be empty. A blank inside the debt life is as much a
    # failure as a value outside it.
    dscr = line("ratios.dscr")
    cod = f"Identity!$B${IDENTITY_FIELDS.index(('asset.codYear', 'year')) + _FIRST_FIELD_ROW}"
    tenor = (
        f"Assumptions!$B$"
        f"{ASSUMPTION_FIELDS.index(('assumptions.debtTenorYears', 'years')) + _FIRST_FIELD_ROW}"
    )
    checks.append(
        (
            "dscr is present exactly inside the debt life",
            [
                f"=IF(AND(Statements!${get_column_letter(_FIRST_YEAR_COLUMN + t)}$1>={cod},"
                f"Statements!${get_column_letter(_FIRST_YEAR_COLUMN + t)}$1<{cod}+{tenor})"
                f'=({_cell(dscr, t)}<>""),0,1)'
                for t in range(YEARS)
            ],
        )
    )

    # The DSCR ratio's absolute limb is 0.001, not EUR 0.01m: it is a multiple,
    # not an amount, so the money tolerance would pass anything.
    def dscr_check(offset: int) -> str:
        ratio = _cell(dscr, offset)
        interest = _cell(line("cashFlow.interestPaid"), offset)
        principal = _cell(line("cashFlow.debtRepayment"), offset)
        ebitda = _cell(line("incomeStatement.ebitda"), offset)
        service = f"{interest}+{principal}"
        return (
            f'=IF(OR({ratio}="",{service}=0),0,'
            f"MAX(0,ABS({ratio}-{ebitda}/({service}))"
            f"-MAX(0.001,0.001*ABS({ratio}))))"
        )

    checks.append(
        (
            "dscr = ebitda / (interestPaid + debtRepayment)",
            [dscr_check(t) for t in range(YEARS)],
        )
    )
    return checks


def _scalar_checks(rows: dict[str, int]) -> list[tuple[str, str]]:
    """Checks that span the whole series, so they live in column B alone."""
    last = get_column_letter(_FIRST_YEAR_COLUMN + YEARS - 1)
    first = get_column_letter(_FIRST_YEAR_COLUMN)
    total_capex = (
        f"Identity!$B$"
        f"{IDENTITY_FIELDS.index(('capitalStructure.totalCapex', 'EURm')) + _FIRST_FIELD_ROW}"
    )
    senior_debt = (
        f"Identity!$B$"
        f"{IDENTITY_FIELDS.index(('capitalStructure.seniorDebt', 'EURm')) + _FIRST_FIELD_ROW}"
    )
    tax_rate = (
        f"Assumptions!$B$"
        f"{ASSUMPTION_FIELDS.index(('assumptions.taxRate', 'fraction')) + _FIRST_FIELD_ROW}"
    )

    def total(name: str) -> str:
        row = rows[name]
        return f"SUM(Statements!${first}${row}:${last}${row})"

    return [
        (
            "closing[last] = 0",
            f"=MAX(0,ABS(Statements!{last}{rows['debtSchedule.closing']})-MAX(0.01,0.001*ABS(0)))",
        ),
        (
            "sum debtDrawdown = seniorDebt",
            f"=MAX(0,ABS({total('cashFlow.debtDrawdown')}-{senior_debt})"
            f"-MAX(0.01,0.001*ABS({senior_debt})))",
        ),
        (
            "sum equityDrawdown = totalCapex - seniorDebt",
            f"=MAX(0,ABS({total('cashFlow.equityDrawdown')}-({total_capex}-{senior_debt}))"
            f"-MAX(0.01,0.001*ABS({total_capex}-{senior_debt})))",
        ),
        (
            "sum capex = totalCapex",
            f"=MAX(0,ABS({total('cashFlow.capex')}-{total_capex})"
            f"-MAX(0.01,0.001*ABS({total_capex})))",
        ),
        (
            "sum depreciation = totalCapex - ppe[last]",
            f"=MAX(0,ABS({total('incomeStatement.depreciation')}"
            f"-({total_capex}-Statements!{last}{rows['balanceSheet.ppe']}))"
            f"-MAX(0.01,0.001*ABS({total_capex})))",
        ),
        (
            "taxExpense = taxRate * max(0, pbt)   [warn]",
            f"=MAX(0,ABS({total('incomeStatement.taxExpense')}-{tax_rate}"
            f"*SUMPRODUCT((Statements!${first}${rows['incomeStatement.pbt']}"
            f":${last}${rows['incomeStatement.pbt']}>0)"
            f"*Statements!${first}${rows['incomeStatement.pbt']}"
            f":${last}${rows['incomeStatement.pbt']}))"
            f"-MAX(0.01,0.001*ABS({total('incomeStatement.taxExpense')})))",
        ),
    ]


def _write_tieouts(sheet: Worksheet, file: dict[str, Any]) -> None:
    rows = statement_rows()
    last_year_column = get_column_letter(_TIEOUT_YEAR_COLUMN + YEARS - 1)
    first_year_column = get_column_letter(_TIEOUT_YEAR_COLUMN)
    _append(sheet, [*_TIEOUT_HEADER, *file["statements"]["years"]])

    row = _FIRST_FIELD_ROW
    for label, formulas in _per_year_checks(rows):
        sheet.append(
            [
                label,
                f"=MAX({first_year_column}{row}:{last_year_column}{row})",
                f'=IF(B{row}=0,"PASS","FAIL")',
                *formulas,
            ]
        )
        row += 1
    for label, formula in _scalar_checks(rows):
        sheet.append([label, formula, f'=IF(B{row}=0,"PASS","FAIL")'])
        row += 1
    sheet.append([])
    _append(sheet, [_TIEOUTS_FOOTNOTE])


@contextmanager
def _full_precision() -> Iterator[None]:
    """Write doubles at the precision that round-trips, not openpyxl's 16 digits.

    ``openpyxl.compat.safe_string`` formats every number with ``%.16g``, and a
    binary64 double needs up to **17** significant digits to survive a
    round trip. Measured over 40 generated files, 8,578 of 32,400 values came
    back changed in their last digit -- about 1e-16 relative, far inside the
    EUR 0.01m tie-out tolerance, but not lossless, and issue #8 asks for
    lossless.

    ``repr`` gives the shortest string that reads back as the identical double,
    so it is both exact and no longer than it has to be.

    Contained: the override is installed for one workbook write and removed
    again, and it asserts the attribute it replaces still exists, so an
    openpyxl restructure fails here rather than silently going back to losing a
    digit.

    **This does not make Excel itself exact.** Excel carries 15 significant
    digits, so an analyst who opens one of these workbooks and saves it will
    lose the last two -- by about 1e-15 relative, still three orders inside the
    file's own tie-out tolerance. Recorded in ``docs/decisions.md``: the
    spreadsheet path is exact as a machine round trip and approximate once a
    human has been through it, which is a property of the format rather than of
    this code.
    """
    if not hasattr(_writer, "safe_string"):  # pragma: no cover - guards a restructure
        raise RuntimeError(
            "openpyxl.cell._writer no longer exposes safe_string; the full-precision "
            "workbook writer needs updating"
        )
    original = _writer.safe_string

    def exact(value: Any) -> Any:
        if isinstance(value, float) and math.isfinite(value):
            return repr(value)
        return original(value)

    _writer.safe_string = exact
    try:
        yield
    finally:
        _writer.safe_string = original


def write_workbook(file: dict[str, Any], path: Path) -> Path:
    """Write one project file as the §13 workbook. Returns the path written."""
    workbook = Workbook()
    workbook.remove(workbook.active)
    _write_pairs(workbook.create_sheet("Identity"), IDENTITY_FIELDS, file)
    _write_pairs(
        workbook.create_sheet("Assumptions"), ASSUMPTION_FIELDS, file, _ASSUMPTIONS_FOOTNOTE
    )
    _write_provenance(workbook.create_sheet("Provenance"), file)
    _write_statements(workbook.create_sheet("Statements"), file)
    _write_tieouts(workbook.create_sheet("TieOuts"), file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _full_precision():
        workbook.save(path)
    return path


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def _narrow(path: str, value: Any) -> Any:
    """Coerce a cell back to the type the schema declares for its field.

    A spreadsheet has one numeric type, so an integer field can come back as
    ``18.0`` and a date as a :class:`datetime.datetime`. Both are the format's
    doing rather than the analyst's, and both would otherwise fail validation
    with a message about something they never typed. A float that is *not* whole
    is left alone, so a genuine ``18.5`` tenor still fails, and says so.
    """
    if path in INTEGER_FIELDS and isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


def _read_pairs(sheet: Worksheet, fields: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for row in sheet.iter_rows(min_row=_FIRST_FIELD_ROW, max_col=_VALUE_COLUMN, values_only=True):
        key, value = row[0], row[1]
        if isinstance(key, str) and key in {name for name, _ in fields}:
            found[key] = _narrow(key, value)
    missing = [name for name, _ in fields if name not in found]
    if missing:
        raise ValueError(f"{sheet.title}: missing rows for {', '.join(missing)}")
    return found


def read_workbook(path: Path) -> dict[str, Any]:
    """Read a §13 workbook back into the canonical JSON shape.

    Returns a plain mapping rather than a model: the caller validates it through
    :class:`~terrafolio.domain.project_file.ProjectFile`, which is the same
    gate a JSON file passes and the reason there is only one schema.
    """
    workbook = load_workbook(path, data_only=True)
    expected = {"Identity", "Assumptions", "Provenance", "Statements"}
    missing = expected - set(workbook.sheetnames)
    if missing:
        raise ValueError(f"{path.name}: missing sheet(s) {', '.join(sorted(missing))}")

    file: dict[str, Any] = {}
    for key, value in _read_pairs(workbook["Identity"], IDENTITY_FIELDS).items():
        _plant(file, key, value)
    for key, value in _read_pairs(workbook["Assumptions"], ASSUMPTION_FIELDS).items():
        _plant(file, key, value)

    provenance: dict[str, Any] = {"fields": {}}
    for row in workbook["Provenance"].iter_rows(
        min_row=_FIRST_FIELD_ROW, max_col=len(_PROVENANCE_HEADER), values_only=True
    ):
        label = row[0]
        if not isinstance(label, str):
            continue
        if label in PROVENANCE_SCALARS:
            provenance[label.split(".")[1]] = _narrow(label, row[_BASIS_AT])
        elif label in PROVENANCE_GROUPS:
            provenance["fields"][label] = {
                "estimateBasis": row[_BASIS_AT],
                "confidence": row[_CONFIDENCE_AT],
                "note": row[_NOTE_AT],
            }
    file["provenance"] = provenance

    statements: dict[str, Any] = {}
    sheet = workbook["Statements"]
    header = next(sheet.iter_rows(min_row=_HEADER_ROWS, max_row=_HEADER_ROWS, values_only=True))
    statements["years"] = [int(year) for year in header[_LEAD_COLUMNS : _LEAD_COLUMNS + YEARS]]
    known = {f"{block}.{line}" for block, lines in STATEMENT_BLOCKS for line, _ in lines}
    for row in sheet.iter_rows(min_row=_FIRST_FIELD_ROW, values_only=True):
        label = row[0]
        if not isinstance(label, str) or label not in known:
            continue
        block, line = label.split(".")
        values = list(row[_LEAD_COLUMNS : _LEAD_COLUMNS + YEARS])
        statements.setdefault(block, {})[line] = [
            None if value is None else float(value) for value in values
        ]
    file["statements"] = statements
    return file
