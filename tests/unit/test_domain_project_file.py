"""The project file: what a well-formed one looks like, and what a rejected one says.

The fixture beside this module is 1B's own ``templates/project-template.json``
(Almonte Solar) with 1A's four schema additions applied, so these tests run
against a file whose tie-outs actually close rather than against invented
numbers.
"""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

import pytest
from pydantic import BaseModel, ValidationError

import terrafolio.domain.project_file as pf_module
from terrafolio.domain.conventions import YEARS
from terrafolio.domain.enums import ProvenanceGroup, Technology
from terrafolio.domain.errors import render_validation_error
from terrafolio.domain.project_file import ProjectFile

EXAMPLE_PATH: Final = Path(__file__).with_name("test_domain_example_file.json")


@pytest.fixture
def raw() -> dict[str, Any]:
    """A fresh, valid file as a mutable dict, for each test to break its own way."""
    return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


def _messages(raw: dict[str, Any]) -> list[str]:
    with pytest.raises(ValidationError) as caught:
        ProjectFile.model_validate(raw)
    return render_validation_error(caught.value)


# --------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------


def test_the_worked_example_validates(raw: dict[str, Any]) -> None:
    project = ProjectFile.model_validate(raw)
    assert project.id == "P01"
    assert project.asset.technology is Technology.SOLAR_PV
    assert project.asset.capacity_mw == 180.0


def test_round_trip_is_byte_identical(raw: dict[str, Any]) -> None:
    """Validate then dump must reproduce the file exactly.

    The cheapest possible regression net over every alias, every default and the
    serialisation of dates, enums and nulls at once.
    """
    project = ProjectFile.model_validate(raw)
    assert json.loads(project.model_dump_json()) == raw


def test_a_file_is_immutable_all_the_way_down(raw: dict[str, Any]) -> None:
    """``frozen=True`` freezes assignment; tuples are what freeze the contents.

    Both halves are needed. With ``list`` series, ``fcfe.append(0.0)`` would
    succeed on a "frozen" model and the snapshot hash would stop describing what
    is in memory.
    """
    project = ProjectFile.model_validate(raw)
    assert isinstance(project.statements.cash_flow.fcfe, tuple)
    with pytest.raises(ValidationError):
        project.id = "P02"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        project.statements.cash_flow.fcfe.append(0.0)  # type: ignore[attr-defined]


def test_provenance_group_named_capex_is_not_a_derived_field(raw: dict[str, Any]) -> None:
    """``provenance.fields.capex`` is vocabulary, not a stored capex scalar."""
    project = ProjectFile.model_validate(raw)
    assert ProvenanceGroup.CAPEX in project.provenance.fields


# --------------------------------------------------------------------------
# Shape: exactly thirty years, and the message says which field
# --------------------------------------------------------------------------


@pytest.mark.parametrize("length", [YEARS - 1, YEARS + 1])
def test_a_wrong_length_series_names_the_field(raw: dict[str, Any], length: int) -> None:
    series = raw["statements"]["incomeStatement"]["revenue"]
    raw["statements"]["incomeStatement"]["revenue"] = (series * 2)[:length]
    messages = _messages(raw)
    assert any(
        "statements.incomeStatement.revenue" in message
        and f"exactly {YEARS} annual values, got {length}" in message
        for message in messages
    ), messages


def test_the_field_named_is_the_alias_not_the_python_name(raw: dict[str, Any]) -> None:
    """The analyst is looking at their own JSON, which says ``generationGwh``."""
    raw["statements"]["physicals"]["generationGwh"] = []
    messages = _messages(raw)
    assert any("statements.physicals.generationGwh" in m for m in messages), messages
    assert not any("generation_gwh" in m for m in messages), messages


def test_dscr_may_be_null_and_other_series_may_not(raw: dict[str, Any]) -> None:
    assert None in raw["statements"]["ratios"]["dscr"]
    ProjectFile.model_validate(raw)

    raw["statements"]["incomeStatement"]["revenue"][3] = None
    assert _messages(raw) != []


def test_a_nan_is_rejected(raw: dict[str, Any]) -> None:
    """An undefined IRR is the only NaN in the system; a NaN input would end that."""
    raw["statements"]["incomeStatement"]["revenue"][3] = math.nan
    messages = _messages(raw)
    assert any("finite" in m for m in messages), messages


def test_years_must_anchor_to_the_base_year(raw: dict[str, Any]) -> None:
    raw["statements"]["years"] = [year + 1 for year in raw["statements"]["years"]]
    assert any("statements.years must run" in m for m in _messages(raw))


def test_years_must_be_contiguous(raw: dict[str, Any]) -> None:
    raw["statements"]["years"][17] += 1
    assert any("contiguously" in m for m in _messages(raw))


# --------------------------------------------------------------------------
# Nothing mandate-dependent is ever stored
# --------------------------------------------------------------------------

_MANDATE_DEPENDENT_KEYS: Final = [
    "irr",
    "IRR",
    "equityIrr",
    "moic",
    "terminalValue",
    "exitValue",
    "payback",
    "paybackYear",
    "minDscr",
]

_REDUNDANT_KEYS: Final = ["lcoe", "leverage", "gearing", "equity", "capexPerKw"]


@pytest.mark.parametrize("key", _MANDATE_DEPENDENT_KEYS)
def test_a_mandate_dependent_field_is_rejected_and_explained(raw: dict[str, Any], key: str) -> None:
    raw[key] = 0.12
    messages = _messages(raw)
    assert any(key in m and "hold period" in m for m in messages), messages


@pytest.mark.parametrize("key", _REDUNDANT_KEYS)
def test_a_redundant_field_is_rejected_and_explained(raw: dict[str, Any], key: str) -> None:
    raw[key] = 0.5
    messages = _messages(raw)
    assert any(key in m and "one arithmetic step" in m for m in messages), messages


def test_a_derived_field_is_caught_at_any_depth(raw: dict[str, Any]) -> None:
    raw["capitalStructure"]["irr"] = 0.11
    messages = _messages(raw)
    assert any("capitalStructure/irr" in m for m in messages), messages


def test_every_offender_is_reported_at_once(raw: dict[str, Any]) -> None:
    """Three derived blocks should take one edit to fix, not three round trips."""
    raw["irr"] = 0.11
    raw["moic"] = 1.9
    raw["capitalStructure"]["gearing"] = 0.72
    message = "\n".join(_messages(raw))
    assert "irr" in message
    assert "moic" in message
    assert "capitalStructure/gearing" in message


def test_a_top_level_capex_scalar_is_rejected_by_name(raw: dict[str, Any]) -> None:
    """A-2: ``capex`` is the annual line, ``totalCapex`` is the project scalar."""
    raw["capex"] = 103.32
    messages = _messages(raw)
    assert any("totalCapex" in m for m in messages), messages


def test_capex_stays_legitimate_inside_the_cash_flow(raw: dict[str, Any]) -> None:
    assert "capex" in raw["statements"]["cashFlow"]
    ProjectFile.model_validate(raw)


# --------------------------------------------------------------------------
# The rest of the contract
# --------------------------------------------------------------------------


def test_unknown_keys_are_an_error(raw: dict[str, Any]) -> None:
    """A misspelled field silently ignored is how a wrong number reaches a committee."""
    raw["asset"]["capacityMW"] = 180.0
    assert any("Extra inputs" in m for m in _messages(raw))


def test_snake_case_is_not_the_file_format(raw: dict[str, Any]) -> None:
    raw["asset"]["capacity_mw"] = raw["asset"].pop("capacityMw")
    assert _messages(raw) != []


def test_a_different_schema_version_is_named(raw: dict[str, Any]) -> None:
    raw["schemaVersion"] = "2.0"
    assert any("'2.0'" in m for m in _messages(raw))


@pytest.mark.parametrize(
    ("block", "key", "value"),
    [
        ("asset", "technology", "solar"),
        ("asset", "stage", "Ready-to-build"),
        ("execution", "currency", "XYZ"),
        ("provenance", "modelVersion", ""),
    ],
)
def test_closed_vocabularies_reject_near_misses(
    raw: dict[str, Any], block: str, key: str, value: str
) -> None:
    """``solar`` and ``Ready-to-build`` are the reference's spellings, not ours."""
    raw[block][key] = value
    assert _messages(raw) != []


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda r: r["capitalStructure"].__setitem__("seniorDebt", 1e6), "exceeds totalCapex"),
        (lambda r: r["revenue"].__setitem__("ppaTenorYears", 0), "fully merchant"),
        (lambda r: r["execution"].__setitem__("developmentRiskScore", 2.75), "one decimal"),
        (lambda r: r["asset"].__setitem__("codYear", 2060), "outside"),
        (lambda r: r["provenance"]["fields"].pop("grid"), "missing grid"),
    ],
)
def test_cross_field_rules(
    raw: dict[str, Any], mutate: Callable[[dict[str, Any]], object], fragment: str
) -> None:
    mutate(raw)
    messages = _messages(raw)
    assert any(fragment in m for m in messages), messages


def test_an_id_must_match_the_pattern(raw: dict[str, Any]) -> None:
    raw["id"] = "p01"
    assert _messages(raw) != []


# --------------------------------------------------------------------------
# The alias inventory
# --------------------------------------------------------------------------

EXPECTED_ALIASES: Final[tuple[str, ...]] = (
    "Asset.capacity_mw=capacityMw",
    "Asset.cod_year=codYear",
    "Asset.net_capacity_factor=netCapacityFactor",
    "Asset.opex_per_kw_year=opexPerKwYear",
    "Asset.stage=stage",
    "Asset.technology=technology",
    "BalanceSheet.ppe=ppe",
    "CapitalStructure.max_gearing=maxGearing",
    "CapitalStructure.senior_debt=seniorDebt",
    "CapitalStructure.total_capex=totalCapex",
    "CashFlow.capex=capex",
    "CashFlow.debt_drawdown=debtDrawdown",
    "CashFlow.debt_repayment=debtRepayment",
    "CashFlow.equity_drawdown=equityDrawdown",
    "CashFlow.fcfe=fcfe",
    "CashFlow.interest_paid=interestPaid",
    "CashFlow.tax_paid=taxPaid",
    "DebtSchedule.closing=closing",
    "DebtSchedule.drawdown=drawdown",
    "DebtSchedule.opening=opening",
    "DebtSchedule.repayment=repayment",
    "DeclaredAssumptions.base_year=baseYear",
    "DeclaredAssumptions.debt_rate=debtRate",
    "DeclaredAssumptions.debt_tenor_years=debtTenorYears",
    "DeclaredAssumptions.degradation_rate=degradationRate",
    "DeclaredAssumptions.depreciation_years=depreciationYears",
    "DeclaredAssumptions.merchant_escalation=merchantEscalation",
    "DeclaredAssumptions.opex_escalation=opexEscalation",
    "DeclaredAssumptions.price_escalation=priceEscalation",
    "DeclaredAssumptions.target_dscr=targetDscr",
    "DeclaredAssumptions.tax_rate=taxRate",
    "Execution.currency=currency",
    "Execution.development_risk_score=developmentRiskScore",
    "Execution.grid_secured=gridSecured",
    "Execution.om_contracted=omContracted",
    "IncomeStatement.depreciation=depreciation",
    "IncomeStatement.ebit=ebit",
    "IncomeStatement.ebitda=ebitda",
    "IncomeStatement.interest_expense=interestExpense",
    "IncomeStatement.net_income=netIncome",
    "IncomeStatement.opex=opex",
    "IncomeStatement.pbt=pbt",
    "IncomeStatement.revenue=revenue",
    "IncomeStatement.tax_expense=taxExpense",
    "Location.country=country",
    "Location.country_code=countryCode",
    "Location.iso3=iso3",
    "Location.lat=lat",
    "Location.lon=lon",
    "Physicals.achieved_price=achievedPrice",
    "Physicals.generation_gwh=generationGwh",
    "ProjectFile.asset=asset",
    "ProjectFile.assumptions=assumptions",
    "ProjectFile.capital_structure=capitalStructure",
    "ProjectFile.execution=execution",
    "ProjectFile.id=id",
    "ProjectFile.location=location",
    "ProjectFile.name=name",
    "ProjectFile.provenance=provenance",
    "ProjectFile.revenue=revenue",
    "ProjectFile.schema_version=schemaVersion",
    "ProjectFile.statements=statements",
    "Provenance.fields=fields",
    "Provenance.model_version=modelVersion",
    "Provenance.prepared_by=preparedBy",
    "Provenance.prepared_on=preparedOn",
    "ProvenanceEntry.confidence=confidence",
    "ProvenanceEntry.estimate_basis=estimateBasis",
    "ProvenanceEntry.note=note",
    "Ratios.dscr=dscr",
    "RevenueTerms.capture_factor=captureFactor",
    "RevenueTerms.country_baseload_price=countryBaseloadPrice",
    "RevenueTerms.ppa_price=ppaPrice",
    "RevenueTerms.ppa_share=ppaShare",
    "RevenueTerms.ppa_tenor_years=ppaTenorYears",
    "Statements.balance_sheet=balanceSheet",
    "Statements.cash_flow=cashFlow",
    "Statements.debt_schedule=debtSchedule",
    "Statements.income_statement=incomeStatement",
    "Statements.physicals=physicals",
    "Statements.ratios=ratios",
    "Statements.years=years",
)


def _file_models() -> list[type[BaseModel]]:
    return [
        value
        for name in pf_module.__all__
        if isinstance(value := getattr(pf_module, name), type) and issubclass(value, BaseModel)
    ]


def test_the_alias_inventory_is_pinned() -> None:
    """Every camelCase name in the file format, written down.

    ``to_camel`` has an early-return branch whose behaviour could shift across a
    pydantic release, and that would silently rename the entire file format.
    This is also the list 1B documents and 2C's spreadsheet path mirrors, so a
    deliberate change should show up as a diff here.
    """
    actual = tuple(
        sorted(
            f"{model.__name__}.{name}={field.alias}"
            for model in _file_models()
            for name, field in model.model_fields.items()
        )
    )
    assert actual == EXPECTED_ALIASES


def test_the_example_file_uses_every_alias() -> None:
    """The fixture must exercise the whole schema, not a convenient subset."""
    raw = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    aliases = {row.split("=", 1)[1] for row in EXPECTED_ALIASES}
    seen: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                seen.add(str(key))
                walk(value)

    walk(raw)
    assert aliases - seen - {group.value for group in ProvenanceGroup} == set()


def test_mutating_the_fixture_does_not_leak_between_tests(raw: dict[str, Any]) -> None:
    before = copy.deepcopy(raw)
    raw["id"] = "MUTATED"
    assert json.loads(EXAMPLE_PATH.read_text(encoding="utf-8")) == before
