"""The three §13 rows a user meets on the mandate screen (issue #13).

Rows 1, 2 and 8 of `docs/spec.md` §13: nothing passes the screens, the locks alone
cannot be funded, and the pipeline moves under a session. Each asserts the backend
behaviour and the sentence or the figures the user is shown.

The copy is not transcribed here. `ui-contract.md` §3.5 and §3.6 own it and
`tests/api/test_messages.py` already holds `api/messages.py` to the document, so
these tests read the templates from the module the server renders and assert the
**rendering** — which is the half a pinned template cannot cover.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import pytest
from test_edge_cases_support import (
    GOLDEN_PIPELINE,
    completed_run,
    edge_corpus,
    mandate,
    opened_client,
    opened_service,
    settings_for,
)

from terrafolio.api.messages import TEMPLATES
from terrafolio.cli import main
from terrafolio.domain.enums import WarningCode
from terrafolio.optimiser.screens import SCREEN_NAMES

EMPTIES_THE_POOL: Final[dict[str, object]] = {"minDscr": 2.0, "riskAppetite": "low"}
"""A coherent mandate that no file in the corpus satisfies.

Deliberately blocked on **coverage and development risk**, neither of which
`ui-contract.md` §3.5's sentence names: the static copy says "widen countries,
stages or the COD window", so this is exactly the mandate where following the
sentence alone sends a user to three controls that are not what is wrong. The
actionable names have to travel in ``screensToWiden`` (4C-6), which is what this
test is really about.

A DSCR floor alone does not do it: an unlevered project has no coverage to fail
and passes (A-21), and the corpus has two. Capping development risk finishes the
job — and the two screens overlap, which is why summing drop counts is meaningless
(2A-8).
"""


async def test_a_mandate_no_candidate_passes_disables_the_run_and_names_the_screens_to_widen(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """§13 row 1: run disabled, with an explicit warning naming the screens to widen."""
    settings = settings_for(tmp_path)
    with opened_service(settings) as service:
        async with opened_client(settings, service) as client:
            body = {
                "mandate": mandate(**EMPTIES_THE_POOL),
                "lockedIds": [],
                "excludedIds": [],
            }
            previewed = await client.post("/mandate/preview", json=body)
            assert previewed.status_code == 200, previewed.text
            preview = previewed.json()

            assert preview["eligibleCount"] == 0
            assert preview["totalCount"] == 48
            assert preview["runnable"] is False, "the run button cannot work"

            codes = [warning["code"] for warning in preview["warnings"]]
            assert codes == [WarningCode.NO_CANDIDATES.name], "2A-19: this warning, and no other"
            assert preview["warnings"][0]["severity"] == "alert", (
                "3A-5: severity is alert or info; `runnable` carries the block"
            )
            assert preview["warnings"][0]["message"] == TEMPLATES[WarningCode.NO_CANDIDATES]

            # The actionable half: the screens that are actually rejecting, worst
            # first, evaluated independently against the whole pipeline (2A-8).
            widen = preview["screensToWiden"]
            assert widen == ["minDscr", "riskScore"], (
                "worst offender first, and neither is a screen the sentence names"
            )
            assert set(widen) <= set(SCREEN_NAMES)
            assert not set(widen) & {"countries", "stages", "codWindow"}, (
                "the three the static sentence names are not what is wrong here"
            )

            attempted = await client.post(
                "/optimisations",
                json={"mandate": mandate(**EMPTIES_THE_POOL), "effort": "fast", "seed": 42},
            )
            assert attempted.status_code == 422, attempted.text
            error = attempted.json()["error"]
            assert error["code"] == "NO_CANDIDATES"
            assert error["detail"]["screensToWiden"] == widen, (
                "preview and run must name the same screens, or the button lies about the body"
            )

    # The counts the ordering is sorted by are not on the wire, so the command
    # line is where "worst offender first" is actually checkable.
    argv = ["--pipeline", str(GOLDEN_PIPELINE), "preview", "--min-dscr", "2.0", "--risk", "low"]
    assert main(argv) == 1
    printed = capsys.readouterr().out
    assert "0 of 48 candidates pass the screens" in printed
    assert "NO_CANDIDATES [blocks the run]" in printed
    assert "Screens rejecting candidates, worst first:" in printed
    assert "minDscr        rejects 43 on its own" in printed
    assert "riskScore      rejects 32 on its own" in printed
    assert "Runnable: no" in printed


async def test_locks_that_alone_exceed_capital_block_the_run_and_name_the_locks_to_release(
    tmp_path: Path,
) -> None:
    """§13 row 2: block the run, and say which locks to release.

    The assertion nothing made before is the last one. A user reads the sentence
    and a client acts on ``detail``; if the two carried different figures the
    screen would be telling somebody to release €220m of locks while the body said
    something else. Both go through ``export/csv.py``'s formatters, so agreeing is
    a property of the code rather than of the copy — but only if something checks.
    """
    settings = settings_for(tmp_path)
    locked = ["P02", "P05", "P30", "P45", "P46", "P48"]

    with opened_service(settings) as service:
        async with opened_client(settings, service) as client:
            attempted = await client.post(
                "/optimisations",
                json={
                    "mandate": mandate(availableCapital_m=200),
                    "effort": "fast",
                    "seed": 42,
                    "lockedIds": locked,
                },
            )
            assert attempted.status_code == 422, attempted.text
            error = attempted.json()["error"]
            assert error["code"] == "LOCKS_EXCEED_CAPITAL"

            detail = error["detail"]
            assert detail["lockedIds"] == sorted(locked), (
                "§13 requires the body to say which locks to release, by id"
            )
            assert detail["availableCapital_m"] == pytest.approx(200.0)
            assert detail["lockedEquity_m"] > detail["availableCapital_m"]
            assert detail["excess_m"] == pytest.approx(
                detail["lockedEquity_m"] - detail["availableCapital_m"]
            )

            previewed = await client.post(
                "/mandate/preview",
                json={
                    "mandate": mandate(availableCapital_m=200),
                    "lockedIds": locked,
                    "excludedIds": [],
                },
            )
            assert previewed.status_code == 200, previewed.text
            preview = previewed.json()
            assert preview["runnable"] is False, (
                "preview and run agree, or the button lights up for a run that 422s"
            )
            assert preview["lockedEquity_m"] == pytest.approx(detail["lockedEquity_m"])

            blocked = _warning(preview, WarningCode.LOCKS_EXCEED_CAPITAL)
            assert blocked["message"] == error["message"]
            assert _figures_in(blocked["message"]) == _figures_in(
                TEMPLATES[WarningCode.LOCKS_EXCEED_CAPITAL].format(
                    locked=f"{detail['lockedEquity_m']:,.0f}",
                    capital=f"{detail['availableCapital_m']:,.0f}",
                )
            ), "the number the user reads is the number the client acts on"


def _warning(preview: dict[str, Any], code: WarningCode) -> dict[str, Any]:
    matching = [row for row in preview["warnings"] if row["code"] == code.name]
    assert len(matching) == 1, f"expected exactly one {code.name}, got {preview['warnings']}"
    found: dict[str, Any] = matching[0]
    return found


def _figures_in(sentence: str) -> list[str]:
    """Every euro figure in a rendered sentence, in order."""
    return [word for word in sentence.replace(",", "").split() if word.startswith("€")]


async def test_a_pipeline_that_moves_mid_session_keeps_stored_runs_and_warns_on_the_next_run(
    tmp_path: Path,
) -> None:
    """§13 row 8: stored runs keep their snapshot; re-running warns that it moved.

    Users add and remove files while the server runs. The requirement has two
    halves and they pull against each other: the run already on screen must keep
    reading exactly as it did — including a project that is no longer on disk —
    while a *new* run against the hash the client is holding must be refused with
    something a page can render.
    """
    directory = edge_corpus(tmp_path)
    settings = settings_for(tmp_path, pipeline=directory)

    with opened_service(settings) as service:
        async with opened_client(settings, service) as client:
            before = (await client.get("/pipeline/status")).json()["pipelineHash"]
            result = await completed_run(client, mandate=mandate())
            run_id = result["runId"]
            held = result["selectedIds"][0]
            aggregates = result["aggregates"]

            removed = next(
                path
                for path in sorted(directory.glob("*.json"))
                if path.stem.split("-")[0] not in result["selectedIds"]
            )
            removed.unlink()

            reloaded = await client.post("/pipeline/reload")
            assert reloaded.status_code == 200, reloaded.text
            after = reloaded.json()["pipelineHash"]
            assert after != before, "removing a file moves the hash"
            assert reloaded.json()["loadedCount"] == 47

            # The stored run is untouched, down to the figures on the tiles.
            stored = await client.get(f"/optimisations/{run_id}")
            assert stored.status_code == 200, stored.text
            assert stored.json()["aggregates"] == aggregates
            assert held in stored.json()["selectedIds"]
            assert stored.json()["provenance"]["pipelineHash"] == before, (
                "a stored run carries the pipeline it was run against, not today's"
            )

            # And the offline pack still renders it, which is what "keeps its
            # snapshot" means to somebody who exported it yesterday.
            pack = await client.get(f"/optimisations/{run_id}/pack")
            assert pack.status_code == 200, pack.text
            assert held in pack.text

            # A *new* run against the hash the client is still holding is refused.
            stale = await client.post(
                "/optimisations",
                json={
                    "mandate": mandate(),
                    "effort": "fast",
                    "seed": 42,
                    "pipelineHash": before,
                },
            )
            assert stale.status_code == 409, stale.text
            error = stale.json()["error"]
            assert error["code"] == "PIPELINE_MOVED"
            assert error["detail"]["currentPipelineHash"] == after
            assert error["message"] == (
                "The pipeline changed since you loaded it; reload and try again."
            ), "§13 wants a clear message, not an error page"

            # And re-running against the pipeline as it now stands works.
            retried = await client.post(
                "/optimisations",
                json={"mandate": mandate(), "effort": "fast", "seed": 42, "pipelineHash": after},
            )
            assert retried.status_code == 202, retried.text
