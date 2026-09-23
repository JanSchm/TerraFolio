"""``holdings.csv`` and ``cashflow.csv``, down to the byte.

§11 requires the exports to be "generated server-side so they match the stored
result exactly", so the interesting tests here are not that the files parse but
that they say the same thing as the run they came from — and that the two
cash-flow series never get confused for one another (A-6).

The golden files next to this module are the contract. They are committed, and
a change to either of them should be a deliberate edit with a reason, not a
diff someone regenerates to make a test pass.
"""

from __future__ import annotations

import csv
import dataclasses
import io
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
from test_domain_mandate import VALID as VALID_MANDATE
from test_domain_results import AGGREGATES, PROVENANCE, holding
from test_store_runs import (
    BASE_YEAR,
    CREATED_AT,
    RUN_ID,
    cashflow_30y,
    completed,
    opened_store,
    submission,
)

from terrafolio.domain.conventions import YEARS
from terrafolio.domain.enums import Effort, RunStatus
from terrafolio.domain.mandate import Mandate
from terrafolio.domain.results import (
    ConvergencePoint,
    Holding,
    PortfolioAggregates,
    RunProvenance,
    RunRecord,
)
from terrafolio.export.csv import (
    CASHFLOW_COLUMNS,
    HOLDINGS_COLUMNS,
    cashflow_csv,
    holdings_csv,
    money_m,
    multiple,
    percent,
    quantity,
)
from terrafolio.store import RunNotFinishedError, StoredRun, finish_run, open_run

HOLD_YEARS: Final = int(VALID_MANDATE["holdYears"])
GOLDEN_HOLDINGS: Final = Path(__file__).with_name("test_export_csv_holdings.csv")
GOLDEN_CASHFLOW: Final = Path(__file__).with_name("test_export_csv_cashflow.csv")


def golden_run(**overrides: Any) -> StoredRun:
    """A hand-authored run, fixed in every digit.

    Built without a database on purpose: the exports are pure functions of a
    stored run, and a golden that depended on the shipped calibration's digest
    would break whenever somebody edited a weight.
    """
    record = RunRecord(
        run_id=RUN_ID,
        run_ref="A-4",
        status=RunStatus.SUCCEEDED,
        created_at=datetime(2026, 9, 21, 9, 22, 11, tzinfo=UTC),
        duration_ms=2483,
        mandate=Mandate.model_validate(VALID_MANDATE),
        locked_ids=(),
        excluded_ids=(),
        effort=Effort.STANDARD,
        selected_ids=("P001", "P003"),
        aggregates=PortfolioAggregates.model_validate(AGGREGATES),
        holdings=(
            Holding.model_validate(holding("P001")),
            Holding.model_validate(holding("P002", selected=False)),
            # §13's undefined IRR, which must leave the system as an empty cell.
            Holding.model_validate(
                holding("P003", equityIrr=None, minDscr=None, moic=None, name="Puglia Sole")
            ),
        ),
        cashflow30Y_m=cashflow_30y(),
        cashflowHold_m=tuple(float(year) for year in range(HOLD_YEARS)),
        convergence=(ConvergencePoint(generation=1, best_fitness=-12.4, mean_fitness=-31.2),),
        provenance=RunProvenance.model_validate(PROVENANCE),
    )
    fields: dict[str, Any] = {
        "record": record,
        "result_json": record.model_dump_json(),
        "run_reference": 4,
        "created_by": "jan.schmitz",
        "finished_at": CREATED_AT,
        "population_size": 90,
        "generations_planned": 60,
        "generations_used": 1,
        "eligible_ids": ("P001", "P002", "P003"),
        "warnings_raised": (),
        "base_year": BASE_YEAR,
        "assumption_snapshot_hash": "sha256:dd",
        "deterministic_reduction": True,
        "error_code": None,
        "error_message": None,
        "schema_version": 1,
    }
    return StoredRun(**(fields | overrides))


def _with_status(run: StoredRun, status: RunStatus) -> StoredRun:
    """The same run, unfinished. A queued record carries no aggregates."""
    record = run.record.model_copy(
        update={"status": status, "aggregates": None, "duration_ms": None}
    )
    return dataclasses.replace(run, record=record, result_json=record.model_dump_json())


def rows(payload: bytes) -> list[list[str]]:
    """The data rows, with the comment header stripped."""
    text = payload.decode("utf-8-sig")
    body = [line for line in text.split("\r\n") if line and not line.startswith("#")]
    return list(csv.reader(io.StringIO("\n".join(body))))


def comments(payload: bytes) -> list[str]:
    text = payload.decode("utf-8-sig")
    return [line for line in text.split("\r\n") if line.startswith("#")]


# --------------------------------------------------------------------------
# The golden bytes
# --------------------------------------------------------------------------


def test_holdings_csv_matches_its_golden_bytes() -> None:
    assert holdings_csv(golden_run()) == GOLDEN_HOLDINGS.read_bytes()


def test_cashflow_csv_matches_its_golden_bytes() -> None:
    assert cashflow_csv(golden_run()) == GOLDEN_CASHFLOW.read_bytes()


# --------------------------------------------------------------------------
# What Excel needs
# --------------------------------------------------------------------------


@pytest.mark.parametrize("export", [holdings_csv, cashflow_csv])
def test_an_export_opens_in_excel_with_its_euro_signs_intact(
    export: Any,
) -> None:
    """A UTF-8 BOM and CRLF line endings, which is what makes Excel read the
    header as UTF-8 rather than as the host's code page. Without it the mandate
    line arrives as mojibake."""
    payload = export(golden_run())
    assert payload.startswith(b"\xef\xbb\xbf")
    assert payload.count(b"\n") == payload.count(b"\r\n")
    text = payload.decode("utf-8-sig")
    assert "€1,200m available" in text
    assert "1,500 MW target" in text


@pytest.mark.parametrize("export", [holdings_csv, cashflow_csv])
def test_an_export_carries_the_run_reference_and_the_mandate(export: Any) -> None:
    """§7.7: all three exports carry both."""
    header = comments(export(golden_run()))
    assert header[0].startswith("# TerraFolio run A-4")
    assert header[1].startswith("# Mandate:")
    assert "10-year hold" in header[1]


@pytest.mark.parametrize("export", [holdings_csv, cashflow_csv])
def test_an_export_names_the_pipeline_the_calibration_and_the_seed(
    export: Any,
) -> None:
    """§12: a file that leaves the building without these cannot be traced back
    to a run anybody could reproduce."""
    provenance = comments(export(golden_run()))[2]
    assert PROVENANCE["pipelineHash"] in provenance
    assert PROVENANCE["assumptionSetId"] in provenance
    assert f"seed {PROVENANCE['seed']}" in provenance


# --------------------------------------------------------------------------
# holdings.csv
# --------------------------------------------------------------------------


def test_holdings_csv_has_the_seventeen_columns_api_md_names() -> None:
    """§7.4's sixteen are a screen layout: the first is a button and the second
    packs three fields into one cell. ``api.md`` §9.1 specifies the export in
    its own right."""
    assert rows(holdings_csv(golden_run()))[0] == list(HOLDINGS_COLUMNS)
    assert len(HOLDINGS_COLUMNS) == 17


def test_holdings_csv_carries_the_selection_only_ordered_by_id() -> None:
    """``holdings`` holds every eligible candidate (A-14); the export is that
    array filtered, so the table and the file cannot disagree."""
    body = rows(holdings_csv(golden_run()))[1:]
    assert [row[0] for row in body] == ["P001", "P003"]


def test_the_lock_state_is_not_a_column() -> None:
    """It steers the next run; it is not a property of this portfolio."""
    assert "locked" not in HOLDINGS_COLUMNS
    assert "selected" not in HOLDINGS_COLUMNS


def test_an_undefined_irr_is_an_empty_cell_never_a_zero() -> None:
    """§13. A zero return and an unsolvable one are different facts, and a
    spreadsheet averaging the column must not silently treat one as the other."""
    body = rows(holdings_csv(golden_run()))[1:]
    undefined = next(row for row in body if row[0] == "P003")
    assert undefined[HOLDINGS_COLUMNS.index("equityIrr")] == ""
    assert undefined[HOLDINGS_COLUMNS.index("minDscr")] == ""


def test_the_cells_carry_raw_numbers_not_formatted_strings() -> None:
    """``api.md`` §9 — "a CSV is for a spreadsheet, not for reading". A euro
    sign in a cell makes that cell text, and the column stops summing."""
    body = rows(holdings_csv(golden_run()))[1:]
    row = body[0]
    assert row[HOLDINGS_COLUMNS.index("equity_m")] == "28.930317"
    assert row[HOLDINGS_COLUMNS.index("equityIrr")] == "0.1417"
    assert float(row[HOLDINGS_COLUMNS.index("gearing")]) == 0.72


def test_a_project_name_cannot_become_a_formula_in_excel() -> None:
    """Names come from files analysts drop into the pipeline directory, and this
    file exists to be opened in Excel. A name beginning `=` would otherwise be
    imported as a live formula rather than as the name of a wind farm.

    The CSV quoting does not help: Excel strips it before evaluating what is
    inside. A leading apostrophe is what marks the cell as literal text.
    """
    hostile = Holding.model_validate(holding("P001", name='=HYPERLINK("http://evil","click")'))
    run = golden_run()
    record = run.record.model_copy(update={"holdings": (hostile,), "selected_ids": ("P001",)})
    exported = rows(
        holdings_csv(dataclasses.replace(run, record=record, result_json=record.model_dump_json()))
    )[1]
    assert exported[HOLDINGS_COLUMNS.index("name")].startswith("'=")


def test_a_negative_cash_flow_is_still_a_number() -> None:
    """The formula guard applies to text only. A leading `-` on a number is a
    sign, and quoting it would stop the column summing — which is the whole
    reason the cells carry raw numbers."""
    body = rows(cashflow_csv(golden_run()))[1:]
    assert body[0][1] == "-28.93"
    assert float(body[0][1]) == -28.93


def test_the_comment_header_stays_on_three_lines() -> None:
    """The header bypasses `csv.writer` — it is not a row — so nothing else
    escapes it, and a newline in an interpolated value would add physical lines
    ahead of the column header."""
    run = golden_run()
    provenance = run.record.provenance.model_copy(
        update={"pipeline_hash": "sha256:9f2c\r\n# injected"}
    )
    record = run.record.model_copy(update={"provenance": provenance})
    exported = holdings_csv(
        dataclasses.replace(run, record=record, result_json=record.model_dump_json())
    )
    assert len(comments(exported)) == 3
    assert "# injected" not in exported.decode("utf-8-sig").split("\r\n")[3]


def test_a_run_with_no_result_has_nothing_to_export() -> None:
    """A queued run has a valid record and an empty portfolio. Exporting it
    would produce a file that looks like a portfolio of nothing."""
    with pytest.raises(RunNotFinishedError, match="queued"):
        holdings_csv(_with_status(golden_run(), RunStatus.QUEUED))


# --------------------------------------------------------------------------
# cashflow.csv
# --------------------------------------------------------------------------


def test_cashflow_csv_is_thirty_years_from_the_base_year() -> None:
    body = rows(cashflow_csv(golden_run()))
    assert body[0] == list(CASHFLOW_COLUMNS)
    assert len(body) - 1 == YEARS
    assert body[1][0] == str(BASE_YEAR)
    assert body[-1][0] == str(BASE_YEAR + YEARS - 1)


def test_cashflow_csv_totals_match_the_stored_series() -> None:
    """The acceptance criterion. Raw cells are what make this exact rather than
    approximate: a rounded column would not add up to the stored number."""
    run = golden_run()
    body = rows(cashflow_csv(run))[1:]
    assert sum(float(row[1]) for row in body) == sum(run.record.cashflow_30y_m)


def test_cashflow_csv_excludes_the_terminal_value() -> None:
    """A-6 calls conflating the two series the single most likely silent bug in
    the feature, and it produces a file whose numbers look entirely plausible.
    The export reads ``cashflow30Y_m`` and never ``cashflowHold_m``."""
    run = golden_run()
    exported = [float(row[1]) for row in rows(cashflow_csv(run))[1:]]
    assert exported == list(run.record.cashflow_30y_m)
    assert exported[:HOLD_YEARS] != list(run.record.cashflow_hold_m)
    assert len(exported) != len(run.record.cashflow_hold_m)


def test_a_run_still_searching_has_no_schedule_to_export() -> None:
    with pytest.raises(RunNotFinishedError, match="running"):
        cashflow_csv(_with_status(golden_run(), RunStatus.RUNNING))


# --------------------------------------------------------------------------
# The §14 formatter, which serves the header and #12's parity tests
# --------------------------------------------------------------------------


def test_money_is_euros_in_millions_with_separators_and_no_decimals() -> None:
    assert money_m(1200) == "€1,200m"
    assert money_m(1154.6) == "€1,155m"


def test_a_quantity_is_an_integer_with_separators_and_a_unit() -> None:
    assert quantity(1661.4, "MW") == "1,661 MW"


def test_a_return_carries_one_decimal_and_a_share_carries_none() -> None:
    assert percent(0.124, places=1) == "12.4%"
    assert percent(0.45) == "45%"


def test_a_multiple_carries_two_decimals_and_a_times_sign() -> None:
    assert multiple(1.38) == "1.38×"  # noqa: RUF001 - the multiplication sign


def test_a_share_on_an_exact_half_rounds_up() -> None:
    """The scaling to a percentage has to happen inside the decimal domain.

    In binary float `0.145 * 100` is `14.499999999999998`, so scaling before
    converting puts back exactly the representation error `Decimal` was chosen
    to remove — and a 14.5% solar target would export as `14% solar`. These are
    the values `ui-contract.md` §2 says #12's parity tests compare.
    """
    assert percent(0.145) == "15%"
    assert percent(0.565) == "57%"
    assert percent(0.8845, places=1) == "88.5%"


def test_rounding_is_half_up_not_python_s_half_even() -> None:
    """Left to the defaults the same stored run shows 1.13 on screen and 1.12
    in its own export (``ui-contract.md`` §2). ``round(1.125, 2)`` is 1.12."""
    assert multiple(1.125) == "1.13×"  # noqa: RUF001 - the multiplication sign
    assert percent(0.1245, places=1) == "12.5%"
    assert money_m(2.5) == "€3m"


# --------------------------------------------------------------------------
# Against a real stored run
# --------------------------------------------------------------------------


def test_an_export_of_a_stored_run_reads_what_the_store_serves(tmp_path: Path) -> None:
    """The exports take a stored run and nothing else, so there is no path by
    which the file and the result on screen can come from different data."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = open_run(connection, submission())
        done = finish_run(connection, record=completed(stored.record), finished_at=CREATED_AT)
        body = rows(cashflow_csv(done))[1:]
        assert sum(float(row[1]) for row in body) == sum(done.record.cashflow_30y_m)
        assert [row[0] for row in rows(holdings_csv(done))[1:]] == list(done.record.selected_ids)
