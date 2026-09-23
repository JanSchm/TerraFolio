"""The §5.4 warning sentences, pinned to the document that owns the copy.

``ui-contract.md`` §3.5 and §3.6 are where these strings are specified, and
``api/messages.py`` holds them verbatim so the wire can carry ``message`` (§5)
while the numeric core keeps emitting codes and euros and never prose. Copy that
drifts from the document should be a failing test, not a discrepancy found by an
investor reading a warning that no longer matches the screen.
"""

from __future__ import annotations

import re

import httpx
import pytest
from conftest import REPO_ROOT, mandate

from terrafolio.api.messages import TEMPLATES, render
from terrafolio.domain.enums import SEVERITY_ORDER, WarningCode, WarningSeverity

UI_CONTRACT = (REPO_ROOT / "docs" / "ui-contract.md").read_text(encoding="utf-8")

DOCUMENTED_ORDER = (
    WarningCode.NO_CANDIDATES,
    WarningCode.CAPACITY_BELOW_TARGET,
    WarningCode.LEVERAGE_UNREACHABLE,
    WarningCode.SOLAR_MIX_UNREACHABLE,
    WarningCode.CAPITAL_UNDERUSED,
    WarningCode.LOCKS_PRESENT,
)
"""§3.5's six rows, in the order the table lists them.

The table has no code column — it is copy, not a contract — so the mapping is
stated here and the *strings* are what the test compares.
"""


def _table_strings() -> list[str]:
    """§3.5's six sentences, read out of the markdown table."""
    section = UI_CONTRACT.split("### 3.5 Warning strings")[1].split("### 3.6")[0]
    rows = re.findall(r"^\|\s*\d+\s*\|[^|]*\|[^|]*\|\s*`(.+?)`\s*\|$", section, re.M)
    assert len(rows) == len(DOCUMENTED_ORDER), rows
    return rows


def _blocking_string() -> str:
    """§3.6's one blocking sentence, quoted as a block quote rather than a row."""
    section = UI_CONTRACT.split("### 3.6 Blocking error")[1]
    match = re.search(r"^>\s*`(.+?)`\s*$", section, re.M)
    assert match, "§3.6 no longer quotes the blocking message"
    return match.group(1)


def test_every_template_is_the_document_s_sentence_verbatim() -> None:
    for code, documented in zip(DOCUMENTED_ORDER, _table_strings(), strict=True):
        assert TEMPLATES[code] == documented, code.name
    assert TEMPLATES[WarningCode.LOCKS_EXCEED_CAPITAL] == _blocking_string()


def test_every_code_has_a_sentence() -> None:
    """A code added without copy should fail here, not reach a client bare."""
    assert set(TEMPLATES) == set(WarningCode)


@pytest.mark.parametrize(
    ("code", "detail", "expected"),
    [
        (WarningCode.NO_CANDIDATES, {}, "No candidates pass the current screens."),
        (
            WarningCode.LOCKS_EXCEED_CAPITAL,
            {"lockedEquity": 1_420e6, "availableCapital": 1_200e6},
            "Locked projects need €1,420m of equity against €1,200m available.",
        ),
        (
            WarningCode.CAPACITY_BELOW_TARGET,
            {"eligibleCapacityMw": 1180.0, "capacityTargetMw": 1500.0},
            "Eligible pipeline is 1,180 MW — below the 1,500 MW target.",
        ),
        (
            WarningCode.LEVERAGE_UNREACHABLE,
            {"minLeverage": 0.6, "eligibleGearing": 0.548},
            "Minimum leverage of 60% exceeds what the eligible pool supports (55%).",
        ),
        (
            WarningCode.CAPITAL_UNDERUSED,
            {"eligibleEquity": 640e6, "availableCapital": 1_200e6},
            "Full pipeline absorbs only €640m of the €1,200m available.",
        ),
        (
            WarningCode.LOCKS_PRESENT,
            {"lockedCount": 3.0, "excludedCount": 1.0},
            "3 project(s) locked in; 1 excluded.",
        ),
    ],
)
def test_the_numbers_are_formatted_as_section_2_specifies(
    code: WarningCode, detail: dict[str, float], expected: str
) -> None:
    """Euros in, §14's formats out: thousands separators, whole percents, €m."""
    assert render(code, detail).startswith(expected)


async def test_the_preview_orders_its_warnings_by_severity(
    client: httpx.AsyncClient,
) -> None:
    """§5.4's order, not the mockup's (A-5)."""
    body = (await client.post("/mandate/preview", json={"mandate": mandate()})).json()
    codes = [WarningCode[row["code"]] for row in body["warnings"]]
    assert codes == sorted(codes)
    assert [code.name for code in SEVERITY_ORDER if code in codes] == [code.name for code in codes]


async def test_severity_is_alert_or_info_and_runnable_carries_the_blocking_signal(
    client: httpx.AsyncClient,
) -> None:
    """1A's ``WarningSeverity`` has two values; whether a warning blocks is not one.

    ``api.md`` §5's example wrote ``"severity": "blocking"`` for the two blocking
    codes. The executable definition of the schema — and what 2B stores — admits
    only ``alert`` and ``info``, with blocking a property of the *code*. So the
    wire carries the severity and ``runnable`` carries the block, and the two
    cannot contradict each other.
    """
    body = (
        await client.post("/mandate/preview", json={"mandate": mandate(codFrom=2033, codTo=2033)})
    ).json()
    assert body["runnable"] is False
    severities = {row["severity"] for row in body["warnings"]}
    assert severities <= {value.value for value in WarningSeverity}
    blocking = [row for row in body["warnings"] if WarningCode[row["code"]].disables_run]
    assert blocking, "nothing blocked a mandate reported as not runnable"
    assert body["screensToWiden"], "§13 asks which screens to widen"


async def test_the_preview_reports_the_pool_in_millions(client: httpx.AsyncClient) -> None:
    """§1.2: an amount in €m carries the suffix; a count and a share do not."""
    body = (await client.post("/mandate/preview", json={"mandate": mandate()})).json()
    assert set(body) == {
        "eligibleCount",
        "totalCount",
        "eligibleCapacityMw",
        "eligibleEquity_m",
        "eligibleSolarShare",
        "eligibleGearing",
        "lockedEquity_m",
        "warnings",
        "screensToWiden",
        "runnable",
    }
    assert 0.0 <= body["eligibleSolarShare"] <= 1.0
    assert 0.0 <= body["eligibleGearing"] <= 1.0
    # €m, not euros: an eligible pool of 48 projects is hundreds, not hundreds
    # of millions.
    assert body["eligibleEquity_m"] < 100_000
