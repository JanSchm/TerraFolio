"""The tie-out suite, against the 48 golden files and against deliberate breakages.

The golden corpus is the positive case: every file must pass every identity
**unmodified**. The negative cases each break exactly one number in an otherwise
valid file, and assert that the named check — not merely *a* check — is the one that
fires. A validator that rejects everything would pass a test that only counted
failures.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from terrafolio.config.loader import load_default
from terrafolio.domain.project_file import ProjectFile
from terrafolio.pipeline.validator import TIE_OUT_NAMES, tie_out_failures

FIXTURES = Path(__file__).resolve().parents[1] / "golden" / "fixtures" / "pipeline"
GOLDEN_FILES = sorted(FIXTURES.glob("*.json"))
ASSUMPTIONS = load_default()


def _payload(name: str = "P01-almonte-solar.json") -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload


def test_the_corpus_is_all_of_it() -> None:
    """A glob that silently matched nothing would make every test below vacuous."""
    assert len(GOLDEN_FILES) == 48


@pytest.mark.parametrize("path", GOLDEN_FILES, ids=lambda path: path.stem)
def test_every_golden_file_passes_every_tie_out(path: Path) -> None:
    file = ProjectFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assert tie_out_failures(file, ASSUMPTIONS) == ()


def _failing_checks(payload: dict[str, Any]) -> set[str]:
    file = ProjectFile.model_validate(payload)
    return {failure.check for failure in tie_out_failures(file, ASSUMPTIONS)}


def test_a_broken_debt_roll_forward_names_that_check() -> None:
    """The acceptance criterion: rejected, naming the specific check."""
    payload = _payload()
    payload["statements"]["debtSchedule"]["closing"][5] += 1.0
    assert "7.4 closing = opening - repayment + drawdown" in _failing_checks(payload)


def test_a_broken_roll_forward_reports_the_year_and_a_signed_residual() -> None:
    payload = _payload()
    payload["statements"]["debtSchedule"]["closing"][5] += 1.0
    file = ProjectFile.model_validate(payload)
    roll_forward = next(
        failure
        for failure in tie_out_failures(file, ASSUMPTIONS)
        if failure.check == "7.4 closing = opening - repayment + drawdown"
    )
    assert roll_forward.year == payload["assumptions"]["baseYear"] + 5
    assert roll_forward.residual_m == pytest.approx(1.0)
    assert "2032" in roll_forward.message


def test_a_debt_balance_that_does_not_amortise_is_caught() -> None:
    payload = _payload()
    payload["statements"]["debtSchedule"]["closing"][-1] = 5.0
    assert "7.4 closing[last] = 0 (fully amortised)" in _failing_checks(payload)


def test_ebitda_that_does_not_tie_to_revenue_less_opex_is_caught() -> None:
    payload = _payload()
    payload["statements"]["incomeStatement"]["ebitda"][3] += 0.5
    assert "7.1 ebitda = revenue - opex" in _failing_checks(payload)


def test_revenue_that_does_not_tie_to_the_physicals_is_caught() -> None:
    payload = _payload()
    payload["statements"]["physicals"]["achievedPrice"][4] *= 1.5
    assert "7.2 revenue = generationGwh x 1000 x achievedPrice / 1e6" in _failing_checks(payload)


def test_funding_that_does_not_sum_to_the_facility_is_caught() -> None:
    payload = _payload()
    payload["capitalStructure"]["seniorDebt"] += 2.0
    assert "7.5 sum debtDrawdown = seniorDebt" in _failing_checks(payload)


def test_an_fcfe_that_does_not_follow_the_cash_flow_is_caught() -> None:
    payload = _payload()
    payload["statements"]["cashFlow"]["fcfe"][7] -= 0.25
    expected = "7.3 fcfe = ebitda - interestPaid - debtRepayment - taxPaid - capex + debtDrawdown"
    assert expected in _failing_checks(payload)


def test_a_broken_ppe_roll_forward_is_caught() -> None:
    payload = _payload()
    payload["statements"]["balanceSheet"]["ppe"][10] += 3.0
    assert "7.6 ppe[t] = ppe[t-1] - depreciation[t] + capex[t]" in _failing_checks(payload)


def test_a_dscr_that_disagrees_with_its_own_debt_service_is_caught() -> None:
    payload = _payload()
    payload["statements"]["ratios"]["dscr"][2] *= 2.0
    assert "7.7 dscr x (interestPaid + debtRepayment) = ebitda" in _failing_checks(payload)


def test_a_dscr_outside_the_debt_life_is_caught() -> None:
    """§7.8's coverage rule — the shape check 1A's model does not enforce."""
    payload = _payload()
    payload["statements"]["ratios"]["dscr"][-1] = 1.5
    failures = _failing_checks(payload)
    assert "7.8 dscr non-null exactly where 0 <= year - codYear < debtTenorYears" in failures


def test_a_dscr_missing_inside_the_debt_life_is_caught() -> None:
    payload = _payload()
    payload["statements"]["ratios"]["dscr"][3] = None
    failures = _failing_checks(payload)
    assert "7.8 dscr non-null exactly where 0 <= year - codYear < debtTenorYears" in failures


def test_opex_that_ignores_the_declared_escalator_is_caught() -> None:
    payload = _payload()
    payload["assumptions"]["opexEscalation"] = 0.10
    assert "7.2 opex = capacityMw x 1000 x opexPerKwYear / 1e6, escalated" in _failing_checks(
        payload
    )


def test_every_identity_reports_all_of_its_breaks_not_only_the_first() -> None:
    """Two broken identities should surface together, not one fix at a time."""
    payload = _payload()
    payload["statements"]["incomeStatement"]["ebitda"][3] += 0.5
    payload["statements"]["balanceSheet"]["ppe"][10] += 3.0
    assert len(_failing_checks(payload)) >= 2


# ---------------------------------------------------------------------------
# The tolerance itself
# ---------------------------------------------------------------------------


def test_a_miss_inside_the_absolute_tolerance_passes() -> None:
    """€0.01m absolute is the looser limb on a small figure."""
    payload = _payload()
    payload["statements"]["incomeStatement"]["ebitda"][3] += ASSUMPTIONS.validation.tolerance_abs_m
    assert "7.1 ebitda = revenue - opex" not in _failing_checks(payload)


def test_a_miss_just_outside_the_absolute_tolerance_fails() -> None:
    payload = _payload()
    payload["statements"]["incomeStatement"]["ebitda"][3] += (
        ASSUMPTIONS.validation.tolerance_abs_m * 10
    )
    assert "7.1 ebitda = revenue - opex" in _failing_checks(payload)


def test_a_miss_inside_the_relative_tolerance_passes_on_a_large_figure() -> None:
    """0.1% relative is the looser limb once the number is big enough.

    Total capex is hundreds of €m, so a miss far above €0.01m is still inside 0.1%.
    """
    payload = _payload()
    capex = payload["capitalStructure"]["totalCapex"]
    payload["capitalStructure"]["totalCapex"] = capex * (
        1 + ASSUMPTIONS.validation.tolerance_rel / 2
    )
    assert "7.5 sum capex = totalCapex" not in _failing_checks(payload)


def test_the_balance_residue_in_the_corpus_is_inside_tolerance() -> None:
    """Committed closing balances land at about -1.3e-13 rather than exactly zero.

    Comparing a balance to exact zero would reject all 48 correct files — the defect
    1A hit on the way in, and the one worth not repeating here.
    """
    payload = _payload()
    assert payload["statements"]["debtSchedule"]["closing"][-1] != 0
    assert "7.4 closing[last] = 0 (fully amortised)" not in _failing_checks(payload)


def test_tie_out_names_match_what_the_checks_can_report() -> None:
    """The published list is what ``pipeline validate`` prints; keep it honest."""
    assert len(TIE_OUT_NAMES) == len(set(TIE_OUT_NAMES))
    payload = _payload()
    for series in ("ebitda", "ebit", "pbt", "netIncome", "opex", "revenue"):
        payload["statements"]["incomeStatement"][series] = [
            value + 100.0 for value in payload["statements"]["incomeStatement"][series]
        ]
    reported = {failure.check for failure in tie_out_failures(ProjectFile(**payload), ASSUMPTIONS)}
    assert reported <= set(TIE_OUT_NAMES)
