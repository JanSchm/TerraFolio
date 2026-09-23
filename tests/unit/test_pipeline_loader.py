"""Loading a directory: what is excluded, what fails the whole load, what warns.

The three outcomes are deliberately different, and most of these tests exist to keep
them apart. A bad file is excluded by name and the rest of the pipeline still loads; a
broken *index* — duplicate ids, disagreeing base years, mixed-width ids — fails the
load entirely, because no subset of it is usable; a file outside a plausibility band
loads with a warning.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from terrafolio.config.loader import load_default
from terrafolio.domain.conventions import EUR_PER_EUR_MILLION, YEARS, canonical_order
from terrafolio.pipeline.loader import PipelineLoadError, load_pipeline

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "fixtures" / "pipeline"
ASSUMPTIONS = load_default()


def _copy_pipeline(destination: Path, *, names: dict[str, str] | None = None) -> Path:
    """The 48 golden files in a writable directory, optionally renamed."""
    destination.mkdir(parents=True, exist_ok=True)
    for path in sorted(GOLDEN.glob("*.json")):
        target = destination / (names or {}).get(path.name, path.name)
        target.write_bytes(path.read_bytes())
    return destination


def _edit(directory: Path, name: str, mutate: Any) -> None:
    path = directory / name
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def golden_load() -> Any:
    """One load of the untouched corpus, shared by the tests that only read it."""
    return load_pipeline(GOLDEN, ASSUMPTIONS)


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_all_forty_eight_golden_files_load_unmodified(golden_load: Any) -> None:
    """Acceptance criterion 1."""
    assert golden_load.file_count == 48
    assert golden_load.loaded_count == 48
    assert golden_load.rejected == ()


def test_arrays_and_files_share_one_ordering(golden_load: Any) -> None:
    """The join 3A depends on: the core has no names, so it joins by position."""
    assert golden_load.arrays.ids == tuple(file.id for file in golden_load.files)
    assert golden_load.arrays.ids == canonical_order(golden_load.arrays.ids)


def test_money_crosses_to_euros_exactly_once(golden_load: Any) -> None:
    arrays, files = golden_load.arrays, golden_load.files
    assert arrays.capital.total_capex[0] == pytest.approx(
        files[0].capital_structure.total_capex * EUR_PER_EUR_MILLION
    )
    assert arrays.statements.income.ebitda[0][1] == pytest.approx(
        files[0].statements.income_statement.ebitda[1] * EUR_PER_EUR_MILLION
    )


def test_generation_stays_in_gwh(golden_load: Any) -> None:
    """Only money converts. GWh, €/MWh and ratios pass through untouched."""
    arrays, files = golden_load.arrays, golden_load.files
    assert arrays.statements.physicals.generation_gwh[0][1] == pytest.approx(
        files[0].statements.physicals.generation_gwh[1]
    )
    assert arrays.asset.net_capacity_factor[0] == pytest.approx(files[0].asset.net_capacity_factor)


def test_a_null_dscr_becomes_nan_not_zero(golden_load: Any) -> None:
    """Zero would read as total failure to cover, not as "no debt service"."""
    dscr = golden_load.arrays.statements.ratios.dscr
    assert np.isnan(dscr).any()
    assert not (dscr == 0.0).any()


def test_uk_is_normalised_to_gb(golden_load: Any) -> None:
    """The assumption set's market tables and the mandate chips both key on GB."""
    codes = set(golden_load.arrays.location.country_codes)
    assert "GB" in codes
    assert "UK" not in codes


def test_the_pipeline_hash_covers_every_loaded_file(golden_load: Any) -> None:
    assert set(golden_load.file_hashes) == set(golden_load.arrays.ids)
    assert golden_load.pipeline_hash.startswith("sha256:")


def test_statement_matrices_are_the_full_grid(golden_load: Any) -> None:
    statements = golden_load.arrays.statements
    assert statements.years.shape == (48, YEARS)
    assert statements.cash_flow.fcfe.shape == (48, YEARS)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_shuffling_file_names_leaves_the_hash_and_the_arrays_identical(tmp_path: Path) -> None:
    """Acceptance criterion 8. Canonical order is by ``id``, not by file name."""
    straight = _copy_pipeline(tmp_path / "straight")
    renamed = _copy_pipeline(
        tmp_path / "renamed",
        names={
            path.name: f"z{47 - index:03d}.json"
            for index, path in enumerate(sorted(GOLDEN.glob("*.json")))
        },
    )
    first, second = load_pipeline(straight, ASSUMPTIONS), load_pipeline(renamed, ASSUMPTIONS)

    assert first.pipeline_hash == second.pipeline_hash
    assert first.arrays.ids == second.arrays.ids
    assert np.array_equal(first.arrays.capital.total_capex, second.arrays.capital.total_capex)
    assert np.array_equal(
        first.arrays.statements.cash_flow.fcfe, second.arrays.statements.cash_flow.fcfe
    )


def test_two_loads_of_the_same_directory_agree(golden_load: Any) -> None:
    again = load_pipeline(GOLDEN, ASSUMPTIONS)
    assert again.pipeline_hash == golden_load.pipeline_hash
    assert again.arrays.ids == golden_load.arrays.ids


# ---------------------------------------------------------------------------
# One bad file, excluded by name
# ---------------------------------------------------------------------------


def test_a_broken_tie_out_excludes_only_that_file(tmp_path: Path) -> None:
    directory = _copy_pipeline(tmp_path / "pipeline")

    def break_debt(payload: dict[str, Any]) -> None:
        payload["statements"]["debtSchedule"]["closing"][5] += 1.0

    _edit(directory, "P01-almonte-solar.json", break_debt)
    result = load_pipeline(directory, ASSUMPTIONS)

    assert result.loaded_count == 47
    assert "P01" not in result.arrays.ids
    assert "P01" not in result.file_hashes
    rejected = [row for row in result.rejected if row.file == "P01-almonte-solar.json"]
    assert any(row.check == "7.4 closing = opening - repayment + drawdown" for row in rejected)


def test_a_rejected_file_is_named_with_its_check_year_and_residual(tmp_path: Path) -> None:
    directory = _copy_pipeline(tmp_path / "pipeline")

    def break_debt(payload: dict[str, Any]) -> None:
        payload["statements"]["debtSchedule"]["closing"][5] += 1.0

    _edit(directory, "P01-almonte-solar.json", break_debt)
    row = next(
        row
        for row in load_pipeline(directory, ASSUMPTIONS).rejected
        if row.check == "7.4 closing = opening - repayment + drawdown"
    )
    assert row.file == "P01-almonte-solar.json"
    assert row.year == 2032
    assert row.residual_m == pytest.approx(1.0)


def test_a_file_carrying_a_derived_field_is_rejected_by_that_rule(tmp_path: Path) -> None:
    directory = _copy_pipeline(tmp_path / "pipeline")
    _edit(directory, "P02-tierra-llana-pv.json", lambda payload: payload.update({"irr": 0.12}))
    result = load_pipeline(directory, ASSUMPTIONS)
    assert result.loaded_count == 47
    assert any(row.check == "9 reject-derived fields" for row in result.rejected)


def test_unparseable_json_is_reported_not_raised(tmp_path: Path) -> None:
    directory = _copy_pipeline(tmp_path / "pipeline")
    (directory / "P03-ebro-valley-pv.json").write_text("{ not json", encoding="utf-8")
    result = load_pipeline(directory, ASSUMPTIONS)
    assert result.loaded_count == 47
    assert any("unreadable" in row.message for row in result.rejected)


def test_a_file_that_is_not_utf_8_is_reported_not_fatal(tmp_path: Path) -> None:
    """``json.loads`` on bytes decodes first, and ``UnicodeDecodeError`` is neither
    ``OSError`` nor ``JSONDecodeError`` — so one stray byte used to abort the whole
    load instead of excluding one file."""
    directory = _copy_pipeline(tmp_path / "pipeline")
    (directory / "P03-ebro-valley-pv.json").write_bytes(b"\xff\xfe{}")
    result = load_pipeline(directory, ASSUMPTIONS)
    assert result.loaded_count == 47
    assert any("unreadable" in row.message for row in result.rejected)


def test_a_schema_violation_is_reported_against_the_file(tmp_path: Path) -> None:
    directory = _copy_pipeline(tmp_path / "pipeline")
    _edit(
        directory,
        "P04-sierra-norte-pv.json",
        lambda payload: payload["asset"].update({"technology": "tidal"}),
    )
    result = load_pipeline(directory, ASSUMPTIONS)
    assert result.loaded_count == 47
    assert any(row.file == "P04-sierra-norte-pv.json" for row in result.rejected)


# ---------------------------------------------------------------------------
# A broken index, which fails the load
# ---------------------------------------------------------------------------


def test_two_files_sharing_an_id_are_both_rejected_by_name(tmp_path: Path) -> None:
    """Acceptance criterion 3, and §7.9: preferring one would make the hash a lie."""
    directory = _copy_pipeline(tmp_path / "pipeline")
    _edit(directory, "P02-tierra-llana-pv.json", lambda payload: payload.update({"id": "P01"}))

    with pytest.raises(PipelineLoadError) as raised:
        load_pipeline(directory, ASSUMPTIONS)

    message = str(raised.value)
    assert "P01-almonte-solar.json" in message
    assert "P02-tierra-llana-pv.json" in message


def test_disagreeing_base_years_fail_the_load_and_report_the_distribution(
    tmp_path: Path,
) -> None:
    """A-13. Series are summed by position, so this is a broken index, not a view."""
    directory = _copy_pipeline(tmp_path / "pipeline")

    def rebase(payload: dict[str, Any]) -> None:
        """A *correctly* re-based file: every age is unchanged, so it ties out.

        That is the point. A file that merely broke would be rejected on its own and
        never reach the pipeline-level check; this one is individually valid and still
        unusable next to the other 47.
        """
        payload["assumptions"]["baseYear"] += 1
        payload["asset"]["codYear"] += 1
        payload["statements"]["years"] = [year + 1 for year in payload["statements"]["years"]]

    _edit(directory, "P05-castilla-fields.json", rebase)

    with pytest.raises(PipelineLoadError) as raised:
        load_pipeline(directory, ASSUMPTIONS)

    message = str(raised.value)
    assert "2027" in message
    assert "2028" in message
    assert "P05-castilla-fields.json" in message


def test_mixed_width_ids_fail_the_load(tmp_path: Path) -> None:
    """C-5: canonical order is a string sort, so ``"P10" < "P9"``."""
    directory = _copy_pipeline(tmp_path / "pipeline")
    _edit(directory, "P09-puglia-sole.json", lambda payload: payload.update({"id": "P9"}))

    with pytest.raises(PipelineLoadError) as raised:
        load_pipeline(directory, ASSUMPTIONS)
    assert "uniform width" in str(raised.value)


def test_an_empty_directory_loads_to_nothing_rather_than_failing(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    result = load_pipeline(empty, ASSUMPTIONS)
    assert result.loaded_count == 0
    assert result.arrays.statements.cash_flow.fcfe.shape == (0, YEARS)


# ---------------------------------------------------------------------------
# Warnings, which never block
# ---------------------------------------------------------------------------


def test_the_golden_corpus_raises_no_plausibility_warnings(golden_load: Any) -> None:
    """A corpus the house model produced should sit inside the house bands."""
    assert golden_load.warnings == ()


def test_an_implausible_capacity_factor_warns_without_blocking(tmp_path: Path) -> None:
    directory = _copy_pipeline(tmp_path / "pipeline")
    _edit(
        directory,
        "P01-almonte-solar.json",
        lambda payload: payload["asset"].update({"netCapacityFactor": 0.95}),
    )
    result = load_pipeline(directory, ASSUMPTIONS)

    assert result.loaded_count == 48
    assert result.rejected == ()
    assert any(warning.check == "plausibility.capacityFactor" for warning in result.warnings)
    assert any("P01" in warning.message for warning in result.warnings)


def test_gearing_above_the_declared_ceiling_warns(tmp_path: Path) -> None:
    directory = _copy_pipeline(tmp_path / "pipeline")
    _edit(
        directory,
        "P01-almonte-solar.json",
        lambda payload: payload["capitalStructure"].update({"maxGearing": 0.30}),
    )
    result = load_pipeline(directory, ASSUMPTIONS)
    assert result.loaded_count == 48
    assert any(warning.check == "plausibility.declaredGearing" for warning in result.warnings)


# ---------------------------------------------------------------------------
# Dispersion
# ---------------------------------------------------------------------------


def test_dispersion_reports_every_declared_assumption(golden_load: Any) -> None:
    report = golden_load.dispersion
    assert set(report.pipeline_wide) >= {
        "taxRate",
        "debtRate",
        "debtTenorYears",
        "depreciationYears",
        "priceEscalation",
        "merchantEscalation",
        "opexEscalation",
    }
    assert report.pipeline_wide["taxRate"].count == 48


def test_a_pipeline_that_agrees_names_no_tails(golden_load: Any) -> None:
    """1C's corpus declares one tax rate throughout, so there is nothing to flag."""
    tax = golden_load.dispersion.pipeline_wide["taxRate"]
    assert tax.agrees
    assert tax.outliers == ()


def test_market_keyed_fields_are_compared_within_a_market(golden_load: Any) -> None:
    """A Finnish baseload beside a Greek one is not a disagreement."""
    baseload = golden_load.dispersion.by_market["countryBaseloadPrice"]
    assert set(baseload) == set(golden_load.arrays.location.country_codes)
    assert all(spread.agrees for spread in baseload.values())


def test_one_disagreeing_file_appears_at_a_tail(tmp_path: Path) -> None:
    directory = _copy_pipeline(tmp_path / "pipeline")
    _edit(
        directory,
        "P01-almonte-solar.json",
        lambda payload: payload["assumptions"].update({"taxRate": 0.35}),
    )
    report = load_pipeline(directory, ASSUMPTIONS).dispersion
    tax = report.pipeline_wide["taxRate"]
    assert not tax.agrees
    assert "P01" in tax.outliers
    assert "taxRate" in report.disagreements


def test_dispersion_never_blocks_a_load(tmp_path: Path) -> None:
    """A-7: blocking on an outlier would let one stale file stop all work."""
    directory = _copy_pipeline(tmp_path / "pipeline")
    _edit(
        directory,
        "P01-almonte-solar.json",
        lambda payload: payload["assumptions"].update({"debtRate": 0.19}),
    )
    result = load_pipeline(directory, ASSUMPTIONS)
    assert result.loaded_count == 48
    assert result.rejected == ()
    assert "debtRate" in result.dispersion.disagreements
