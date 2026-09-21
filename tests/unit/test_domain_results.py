"""The stored run: the candidate snapshot, the two cash-flow series, and the audit trail.

``docs/api.md`` §8 is the contract these models implement, and A-14 is the
decision behind the shape of ``holdings``: a stored run carries **every** project
in its eligible candidate set, so reopening it does not depend on the live
pipeline still agreeing.
"""

from __future__ import annotations

import json
import math
from typing import Any, Final

import pytest
from pydantic import ValidationError

# Imported by bare module name: tests/ is not a package, and pytest puts each
# test directory on sys.path. One mandate payload, used by both modules.
from test_domain_mandate import VALID as VALID_MANDATE

from terrafolio.domain.conventions import YEARS
from terrafolio.domain.enums import ProvenanceGroup, WarningCode, WarningSeverity
from terrafolio.domain.results import (
    FeasibilityWarning,
    Holding,
    PortfolioAggregates,
    ProjectScalars,
    RunProvenance,
    RunRecord,
)

HOLD_YEARS: Final = int(VALID_MANDATE["holdYears"])


def scalars(project_id: str = "P001", **overrides: Any) -> dict[str, Any]:
    """One candidate's full scalar record, as `GET /pipeline` puts it on the wire."""
    return {
        "id": project_id,
        "name": "Almonte Solar",
        "country": "Spain",
        "countryCode": "ES",
        "iso3": "ESP",
        "lat": 39.25,
        "lon": -6.52,
        "technology": "solar",
        "stage": "ready_to_build",
        "capacityMw": 180.0,
        "codYear": 2028,
        "netCapacityFactor": 0.230324,
        "annualGenerationGwh": 363.17,
        "opexPerKwYear": 12.813182,
        "ppaShare": 0.55,
        "ppaTenorYears": 10,
        "ppaPrice": 34.0,
        "countryBaseloadPrice": 58.0,
        "captureFactor": 0.68,
        "capturePrice": 39.44,
        "developmentRiskScore": 2.7,
        "gridSecured": True,
        "omContracted": True,
        "currency": "EUR",
        "totalCapex_m": 103.322563,
        "seniorDebt_m": 74.392246,
        "equity_m": 28.930317,
        "gearing": 0.72,
        "maxGearing": 0.72,
        "capexPerKw": 574.01,
        "debtRate": 0.055,
        "debtTenorYears": 18,
        "lcoe": 31.0,
        "minDscr": 1.65,
        "thirtyYearFcfe_m": 142.8,
        "equityIrr": 0.1417,
        "moic": 1.94,
        "paybackYear": 2038,
        "provenance": {
            group: {"estimateBasis": "benchmark", "confidence": "medium"}
            for group in ("generation", "price", "capex", "opex", "debtTerms", "grid", "om")
        },
        **overrides,
    }


def holding(project_id: str = "P001", *, selected: bool = True, **overrides: Any) -> dict[str, Any]:
    return scalars(project_id, selected=selected, locked=False, **overrides)


AGGREGATES: Final[dict[str, Any]] = {
    "projectCount": 1,
    "solarCount": 1,
    "windCount": 0,
    "capacityMw": 180.0,
    "solarShare": 1.0,
    "totalCapex_m": 103.32,
    "seniorDebt_m": 74.39,
    "equity_m": 28.93,
    "gearing": 0.72,
    "capitalDeployed": 0.024,
    "equityIrr": 0.1417,
    "moic": 1.94,
    "weightedLcoe": 31.0,
    "annualGenerationGwh": 363.17,
    "co2AvoidedKt": 116.2,
    "merchantShare": 0.45,
    "weightedRiskScore": 2.7,
    "worstMinDscr": 1.65,
    "countryShares": {"PT": 0.4, "ES": 0.6},
    "largestCountryCode": "ES",
    "largestCountryShare": 0.6,
    "thirtyYearFcfe_m": 142.8,
    "fitness": 7.881234,
}

PROVENANCE: Final[dict[str, Any]] = {
    "seed": 42,
    "pipelineHash": "sha256:9f2c",
    "fileHashes": {"P002": "sha256:bb", "P001": "sha256:aa"},
    "assumptionSetId": "111c20146e3e30fc",
    "assumptionSetHash": "sha256:cc",
    "engineVersion": "1.0.0",
    "numpyVersion": "2.4.6",
    "blasThreads": 8,
    "pythonVersion": "3.13.1",
    "platform": "darwin-arm64",
}


def run(**overrides: Any) -> dict[str, Any]:
    return {
        "runId": "01JB2Q",
        "runRef": "A-4",
        "status": "succeeded",
        "createdAt": "2026-09-21T09:22:11Z",
        "durationMs": 2483,
        "mandate": VALID_MANDATE,
        "lockedIds": [],
        "excludedIds": [],
        "effort": "standard",
        "selectedIds": ["P001"],
        "aggregates": AGGREGATES,
        "holdings": [holding("P001"), holding("P002", selected=False)],
        "cashflow30Y_m": [0.0] * YEARS,
        "cashflowHold_m": [0.0] * HOLD_YEARS,
        "convergence": [{"generation": 1, "bestFitness": -12.4, "meanFitness": -31.2}],
        "provenance": PROVENANCE,
        **overrides,
    }


def _messages(payload: dict[str, Any]) -> str:
    with pytest.raises(ValidationError) as caught:
        RunRecord.model_validate(payload)
    return str(caught.value)


# --------------------------------------------------------------------------
# The candidate snapshot
# --------------------------------------------------------------------------


def test_a_run_validates() -> None:
    record = RunRecord.model_validate(run())
    assert record.run_id == "01JB2Q"
    assert [item.id for item in record.holdings] == ["P001", "P002"]


def test_a_holding_is_the_full_scalar_record_not_the_table_projection() -> None:
    """A-14: §7.3's map needs coordinates and §7.5's drawer needs the debt terms.

    A stored run reopened after the pipeline has moved cannot go and fetch them,
    so they have to be in the run.
    """
    item = Holding.model_validate(holding())
    assert (item.lat, item.lon) == (39.25, -6.52)
    assert (item.debt_rate, item.debt_tenor_years) == (0.055, 18)
    assert item.capture_price == 39.44
    assert item.payback_year == 2038
    assert item.provenance["capex"].estimate_basis == "benchmark"


def test_a_holding_is_flat_on_the_wire() -> None:
    """The two flags sit beside the scalars, not under a nested key."""
    payload = json.loads(Holding.model_validate(holding()).model_dump_json())
    assert payload["selected"] is True
    assert payload["capacityMw"] == 180.0
    assert "scalars" not in payload


def test_a_holding_round_trips() -> None:
    source = holding()
    assert json.loads(Holding.model_validate(source).model_dump_json()) == source


def test_holdings_carry_the_rejected_candidates_too() -> None:
    """§7.4's *show all candidates* toggle needs what the optimiser turned down."""
    record = RunRecord.model_validate(run())
    assert [item.id for item in record.holdings if not item.selected] == ["P002"]


def test_a_holding_is_a_project_scalars() -> None:
    """`GET /pipeline` and a stored holding are one shape plus two flags."""
    assert issubclass(Holding, ProjectScalars)
    assert set(ProjectScalars.model_fields) < set(Holding.model_fields)


def test_holdings_must_be_in_canonical_order() -> None:
    assert "ordered by id" in _messages(
        run(holdings=[holding("P002", selected=False), holding("P001")])
    )


def test_selected_flags_must_agree_with_selected_ids() -> None:
    assert "selectedIds" in _messages(run(selectedIds=["P002"]))


# --------------------------------------------------------------------------
# The two cash-flow series stay apart, and keep their lengths
# --------------------------------------------------------------------------


def test_the_thirty_year_series_must_carry_thirty_years() -> None:
    message = _messages(run(cashflow30Y_m=[0.0] * (YEARS - 1)))
    assert "cashflow30Y_m" in message
    assert "29" in message


def test_the_hold_series_must_run_the_mandates_hold_period() -> None:
    message = _messages(run(cashflowHold_m=[0.0] * (HOLD_YEARS + 1)))
    assert "cashflowHold_m" in message
    assert f"hold period of {HOLD_YEARS}" in message


def test_the_hold_series_follows_the_mandate_not_a_constant() -> None:
    mandate = {**VALID_MANDATE, "holdYears": 25}
    RunRecord.model_validate(run(mandate=mandate, cashflowHold_m=[0.0] * 25))
    assert "hold period of 25" in _messages(run(mandate=mandate, cashflowHold_m=[0.0] * HOLD_YEARS))


def test_a_run_still_in_flight_has_no_cash_flows_to_check() -> None:
    """§8 returns a running run with no aggregates; it has nothing to be wrong yet."""
    record = RunRecord.model_validate(
        run(
            status="running",
            aggregates=None,
            durationMs=None,
            holdings=[],
            selectedIds=[],
            cashflow30Y_m=[],
            cashflowHold_m=[],
            convergence=[],
        )
    )
    assert record.is_complete is False


# --------------------------------------------------------------------------
# Immutability reaches the contents
# --------------------------------------------------------------------------


def test_result_mappings_cannot_be_mutated_after_validation() -> None:
    """``frozen=True`` stops assignment; a dict field would still accept ``pop``."""
    record = RunRecord.model_validate(run())
    assert record.aggregates is not None
    for mapping in (record.aggregates.country_shares, record.provenance.file_hashes):
        with pytest.raises(AttributeError):
            mapping.pop("ES")  # type: ignore[attr-defined]
        with pytest.raises(TypeError):
            mapping["XX"] = "x"  # type: ignore[index]


def test_result_mappings_are_in_canonical_key_order() -> None:
    """Two identical results must not differ by insertion order."""
    record = RunRecord.model_validate(run())
    assert record.aggregates is not None
    assert list(record.aggregates.country_shares) == ["ES", "PT"]
    assert list(record.provenance.file_hashes) == ["P001", "P002"]


def test_a_nan_never_reaches_a_result() -> None:
    """A NaN here means the NaN-to-None conversion was missed upstream."""
    assert _messages(run(aggregates={**AGGREGATES, "weightedLcoe": math.nan})) != ""


def test_undefined_metrics_are_null_not_zero() -> None:
    record = RunRecord.model_validate(
        run(aggregates={**AGGREGATES, "equityIrr": None, "moic": None, "worstMinDscr": None})
    )
    assert record.aggregates is not None
    assert record.aggregates.equity_irr is None
    payload = json.loads(record.model_dump_json())
    assert payload["aggregates"]["equityIrr"] is None


def test_a_run_without_a_seed_is_not_a_valid_run() -> None:
    with pytest.raises(ValidationError):
        RunProvenance.model_validate(
            {key: value for key, value in PROVENANCE.items() if key != "seed"}
        )


def test_aggregates_reject_coerced_numbers() -> None:
    with pytest.raises(ValidationError):
        PortfolioAggregates.model_validate({**AGGREGATES, "capacityMw": "180"})


# --------------------------------------------------------------------------
# Warnings
# --------------------------------------------------------------------------


def test_a_warning_serialises_by_name() -> None:
    warning = FeasibilityWarning.model_validate(
        {"code": "CAPACITY_BELOW_TARGET", "severity": "alert", "message": "x"}
    )
    assert json.loads(warning.model_dump_json())["code"] == "CAPACITY_BELOW_TARGET"


@pytest.mark.parametrize("ordinal", [1010, "1010", WarningCode.NO_CANDIDATES.value])
def test_an_ordinal_is_refused_on_the_way_in(ordinal: object) -> None:
    """Serialising by name is only a guarantee if the ordinal is refused both ways.

    Otherwise a record written before a code was inserted can be reinterpreted
    as a different warning after one was.
    """
    with pytest.raises(ValidationError, match="by name, not by ordinal"):
        FeasibilityWarning.model_validate({"code": ordinal, "severity": "alert", "message": "x"})


def test_severity_is_derived_from_the_code_when_omitted() -> None:
    warning = FeasibilityWarning.model_validate(
        {"code": "LOCKS_PRESENT", "message": "2 project(s) locked in; 0 excluded."}
    )
    assert warning.severity is WarningSeverity.INFO


def test_a_severity_that_contradicts_its_code_is_rejected() -> None:
    """Otherwise a blocking condition renders in an informational tone."""
    with pytest.raises(ValidationError, match="severity is a property of the code"):
        FeasibilityWarning.model_validate(
            {"code": "NO_CANDIDATES", "severity": "info", "message": "x"}
        )


@pytest.mark.parametrize("code", sorted(WarningCode))
def test_every_code_accepts_its_own_severity(code: WarningCode) -> None:
    warning = FeasibilityWarning.model_validate(
        {"code": code.name, "severity": code.severity.value, "message": "x"}
    )
    assert warning.code is code


# --------------------------------------------------------------------------
# The candidate snapshot carries the domains the file schema states
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("lat", 999.0),
        ("lon", -999.0),
        ("ppaShare", 7.0),
        ("developmentRiskScore", 99.0),
        ("gearing", -3.0),
        ("maxGearing", 1.5),
        ("netCapacityFactor", 4.0),
        ("capacityMw", 0.0),
        ("id", "!!"),
        ("countryCode", "Germany"),
        ("iso3", "X"),
        ("lcoe", -12.0),
        ("moic", -1.0),
        ("codYear", 1800),
    ],
)
def test_a_holding_is_bound_by_the_same_domains_as_a_file(key: str, value: object) -> None:
    """These are what §7.3's map plots and §7.4's table renders.

    ``ProjectFile`` constrains every one of them; the result-side record used to
    constrain none, so a row read back from the store could put a site at
    latitude 999.
    """
    with pytest.raises(ValidationError):
        Holding.model_validate(holding(**{key: value}))


def test_a_holding_cannot_be_more_than_fully_debt_funded() -> None:
    with pytest.raises(ValidationError, match="exceeds totalCapex_m"):
        Holding.model_validate(holding(seniorDebt_m=999.0))


def test_a_holding_must_carry_every_provenance_group() -> None:
    """§7.5's drawer reads a basis per group; a partial block KeyErrors at render."""
    partial = holding()
    partial["provenance"] = {"grid": {"estimateBasis": "benchmark", "confidence": "low"}}
    with pytest.raises(ValidationError, match="provenance must cover every group"):
        Holding.model_validate(partial)


def test_provenance_is_frozen_and_ordered_like_the_other_mappings() -> None:
    item = Holding.model_validate(holding())
    assert list(item.provenance) == sorted(item.provenance)
    with pytest.raises(AttributeError):
        item.provenance.pop(ProvenanceGroup.CAPEX)  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# Consistency rules that used to have gaps
# --------------------------------------------------------------------------


def test_duplicate_holding_ids_are_rejected() -> None:
    """``sorted`` leaves duplicates in order, so ordering alone never caught this."""
    message = _messages(run(holdings=[holding("P001"), holding("P001")], selectedIds=["P001"]))
    assert "duplicate ids: P001" in message


def test_the_selected_check_runs_even_with_no_holdings() -> None:
    """The empty case is exactly the drift the check exists to catch."""
    assert "selectedIds" in _messages(run(holdings=[], selectedIds=["P001"]))


@pytest.mark.parametrize(
    ("status", "aggregates", "fragment"),
    [
        ("succeeded", None, "aggregates are absent"),
        ("running", AGGREGATES, "aggregates are present"),
    ],
)
def test_status_and_aggregates_must_agree(
    status: str, aggregates: dict[str, Any] | None, fragment: str
) -> None:
    """Only §8's two shapes are representable.

    Deriving completeness from ``aggregates`` instead of ``status`` let a
    partial write — succeeded with a null result — skip every check below it.
    """
    payload = run(status=status, aggregates=aggregates)
    if aggregates is None:
        payload |= {"cashflow30Y_m": [], "cashflowHold_m": [], "holdings": [], "selectedIds": []}
    assert fragment in _messages(payload)


def test_a_naive_created_at_is_rejected() -> None:
    """Mixing naive and aware timestamps makes any comparison between runs raise."""
    assert "timezone" in _messages(run(createdAt="2026-09-21T09:22:11"))


def test_run_id_lists_are_normalised() -> None:
    """Two runs differing only in the order ids were collected are the same run."""
    record = RunRecord.model_validate(run(lockedIds=["P002", "P001", "P002"]))
    assert record.locked_ids == ("P001", "P002")
