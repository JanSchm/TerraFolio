"""The seven edge cases the file-based input model adds (issue #13, epic §2).

One named test per case. Each asserts the backend behaviour **and** the
user-visible result: what `GET /pipeline/status` puts on the wire for the mandate
screen's banner (`docs/api.md` §3), and what `terrafolio pipeline validate`
prints, which is the surface an analyst who has just dropped a file into
``pipeline/`` actually reads.

The fixtures are described in ``tests/fixtures/edge/README.md``; the guard at the
foot of this module holds them to that description.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from test_edge_cases_support import (
    EDGE_FIXTURES,
    EDGE_PROVENANCE,
    GOLDEN_PIPELINE,
    assumptions,
    base_payload,
    edge_corpus,
    edge_payload,
    golden_load,
    mandate,
    opened_client,
    opened_service,
    repair,
    settings_for,
)

from terrafolio.api.wire import pipeline_status
from terrafolio.cli import main
from terrafolio.domain.conventions import EUR_PER_EUR_MILLION
from terrafolio.domain.enums import WarningCode
from terrafolio.domain.mandate import Mandate
from terrafolio.domain.reduce import MandateScalars, mandate_to_scalars
from terrafolio.optimiser.feasibility import preview_feasibility
from terrafolio.optimiser.screens import apply_screens
from terrafolio.pipeline.loader import LoadResult, PipelineLoadError, load_pipeline

ASSUMPTIONS = assumptions()

GOLDEN_COUNT = 48
"""1C's corpus. Spelled once so a test that says 48 says why."""

EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
"""A fixed load time. §3's body carries one and none of these tests is about it."""


def _scalars(body: dict[str, Any]) -> MandateScalars:
    """A wire mandate through the pydantic model the API validates it with."""
    return mandate_to_scalars(Mandate.model_validate(body))


def _eligible_count(printed: str) -> int:
    """The first figure of ``preview``'s opening line."""
    return int(printed.split(" of ", 1)[0])


def _status(loaded: LoadResult) -> dict[str, Any]:
    """The `GET /pipeline/status` body for a load, as the mandate screen reads it.

    Takes the result rather than the directory: every caller has just loaded it,
    and re-loading forty-nine files to render a report about them is the kind of
    waste a test suite accumulates quietly.
    """
    return json.loads(pipeline_status(loaded, loaded_at=EPOCH).model_dump_json(by_alias=True))


# ---------------------------------------------------------------------------
# file-fails-a-tie-out
# ---------------------------------------------------------------------------


def test_a_file_that_fails_a_tie_out_is_excluded_by_name_and_never_partially_loaded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """§13 ingestion: excluded with a named reason, counted in the mandate footer.

    The sharp assertion is the last one. ``tests/unit/test_pipeline_loader.py``
    already shows that a broken file is excluded and the others survive; what it
    does not show is that the surviving 48 hash to **exactly** what they hash to
    on their own. A loader that let one byte of a rejected file reach the digest
    would pass every count-based test and quietly make every snapshot a lie.
    """
    directory = edge_corpus(tmp_path, add=["P49-fails-a-tie-out.json"])
    loaded = load_pipeline(directory, ASSUMPTIONS)

    assert loaded.file_count == GOLDEN_COUNT + 1
    assert loaded.loaded_count == GOLDEN_COUNT
    assert "P49" not in loaded.arrays.ids
    assert "P49-fails-a-tie-out.json" not in loaded.file_hashes

    rejected = [row for row in loaded.rejected if row.file == "P49-fails-a-tie-out.json"]
    assert rejected, "the rejection is keyed on the file name, which is all a bad parse leaves"
    checks = {row.check for row in rejected}
    assert "7.4 closing = opening - repayment + drawdown" in checks
    primary = next(row for row in rejected if row.check in checks)
    assert primary.year is not None, "a per-year tie-out names the year it broke"
    assert primary.message

    assert loaded.pipeline_hash == golden_load().pipeline_hash, "never partially loaded"

    # The mandate footer's two numbers, and the reason beside them.
    status = _status(loaded)
    assert (status["fileCount"], status["loadedCount"]) == (GOLDEN_COUNT + 1, GOLDEN_COUNT)
    entry = next(row for row in status["rejected"] if row["file"] == "P49-fails-a-tie-out.json")
    assert entry["check"] in checks
    assert entry["message"]
    assert entry["residual_m"] != 0.0, "§3: a reader can see a typo from a broken model"

    # And on the surface an analyst who has just edited a file actually reads.
    assert main(["--pipeline", str(directory), "pipeline", "validate"]) == 1
    printed = capsys.readouterr().out
    assert f"Loaded {GOLDEN_COUNT} of {GOLDEN_COUNT + 1} files" in printed
    assert "P49-fails-a-tie-out.json" in printed
    assert "7.4 closing = opening - repayment + drawdown" in printed


# ---------------------------------------------------------------------------
# two-files-share-an-id
# ---------------------------------------------------------------------------


def test_two_files_sharing_an_id_abort_the_load_and_the_server_keeps_the_pipeline_it_had(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """§13 ingestion: **both** rejected, named.

    §7.9 fails the load rather than the file, because no subset of a pipeline is
    usable once two files claim one id — silently preferring one would make the
    snapshot hash a lie (2A-11).

    The half nothing asserted before is the session's: a user who drops a
    colliding file in and reloads must not lose the pipeline they were working
    against. ``PipelineSource.reload`` builds the new snapshot before it rebinds,
    so the old one survives the failure.
    """
    directory = edge_corpus(tmp_path)
    settings = settings_for(tmp_path, pipeline=directory)

    with opened_service(settings) as service:
        before = service.source.current()
        assert before.result.loaded_count == GOLDEN_COUNT

        (directory / "P01-duplicates-an-existing-id.json").write_bytes(
            (EDGE_FIXTURES / "P01-duplicates-an-existing-id.json").read_bytes()
        )

        with pytest.raises(PipelineLoadError) as raised:
            service.source.reload()

        message = str(raised.value)
        assert "P01-almonte-solar.json" in message
        assert "P01-duplicates-an-existing-id.json" in message

        after = service.source.current()
        assert after.pipeline_hash == before.pipeline_hash
        assert after.result.loaded_count == GOLDEN_COUNT

    # The user-visible half: the command line names both files and says which id.
    assert main(["--pipeline", str(directory), "pipeline", "validate"]) == 2
    printed = capsys.readouterr().err
    assert printed.startswith("error: duplicate project ids abort the load")
    assert "P01-almonte-solar.json" in printed
    assert "P01-duplicates-an-existing-id.json" in printed


# ---------------------------------------------------------------------------
# pipeline-is-empty
# ---------------------------------------------------------------------------


async def test_an_empty_pipeline_says_so_plainly_rather_than_running_zero_candidates(
    tmp_path: Path,
) -> None:
    """§13 ingestion: the mandate page says so, rather than rendering a zero-candidate run.

    Before this test the application could not start at all against an empty
    directory: ``dispersion_report`` built a ``Distribution`` per declared
    assumption whose median, min and max were all ``NaN``, and ``DispersionEntry``
    forbids a non-finite number, so ``build_service`` raised at startup. A
    pipeline with no files has no distributions — see 4C-2.
    """
    empty = tmp_path / "empty"
    empty.mkdir()
    loaded = load_pipeline(empty, ASSUMPTIONS)

    assert (loaded.file_count, loaded.loaded_count) == (0, 0)
    assert loaded.rejected == ()
    assert loaded.dispersion.pipeline_wide == {}
    assert loaded.dispersion.disagreements == (), "no files is not nine disagreements"

    preview = preview_feasibility(
        loaded.arrays, _scalars(mandate()), ASSUMPTIONS, locked_ids=(), excluded_ids=()
    )
    assert preview.total_count == 0
    assert preview.eligible_count == 0
    assert preview.runnable is False
    assert [signal.code for signal in preview.signals] == [WarningCode.NO_CANDIDATES], (
        "2A-19: an empty pool raises NO_CANDIDATES and nothing else"
    )
    assert preview.screens_to_widen == ()

    settings = settings_for(tmp_path, pipeline=empty)
    with opened_service(settings) as service:
        async with opened_client(settings, service) as client:
            status = await client.get("/pipeline/status")
            assert status.status_code == 200, status.text
            assert status.json()["fileCount"] == 0
            assert status.json()["loadedCount"] == 0
            assert status.json()["dispersion"] == {}

            body = {"mandate": mandate(), "lockedIds": [], "excludedIds": []}
            previewed = await client.post("/mandate/preview", json=body)
            assert previewed.status_code == 200, previewed.text
            assert previewed.json()["totalCount"] == 0
            assert previewed.json()["runnable"] is False
            assert previewed.json()["screensToWiden"] == [], (
                "nothing to widen is what tells a client the pipeline is empty "
                "rather than the mandate too narrow (4C-1)"
            )

            attempted = await client.post(
                "/optimisations", json={"mandate": mandate(), "effort": "fast", "seed": 42}
            )
            assert attempted.status_code == 422, "not an empty run, and not a stack trace"
            error = attempted.json()["error"]
            assert error["code"] == "NO_CANDIDATES"
            assert error["message"].endswith("."), "a readable sentence"


# ---------------------------------------------------------------------------
# outlier-declared-assumptions
# ---------------------------------------------------------------------------


def test_a_file_declaring_an_outlier_assumption_loads_and_is_flagged_never_blocked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """§13 ingestion, A-7 and epic §12 Q3: warn, never block.

    P53 declares a 35% tax rate into a corpus that is unanimously at 20%. Two
    independent reports notice — the dispersion report gains a field it did not
    have, and a plausibility check fires because the stored tax expense no longer
    follows the declared rate — and neither stops the file loading. Blocking here
    would let one stale file stop all work.
    """
    directory = edge_corpus(tmp_path, add=["P53-declares-outlier-assumptions.json"])
    loaded = load_pipeline(directory, ASSUMPTIONS)

    assert loaded.loaded_count == GOLDEN_COUNT + 1
    assert loaded.rejected == (), "an outlier is not a rejection"
    assert "P53" in loaded.arrays.ids

    spread = loaded.dispersion.pipeline_wide["taxRate"]
    assert "taxRate" not in golden_load().dispersion.disagreements, "the corpus agreed before"
    assert "taxRate" in loaded.dispersion.disagreements, "and disagrees because of this file"
    assert spread.maximum == pytest.approx(0.35)
    assert spread.minimum == pytest.approx(0.20)
    assert "P53" in spread.outliers

    checks = {warning.check for warning in loaded.warnings}
    assert checks == {"plausibility.tax"}
    assert any("P53" in warning.message for warning in loaded.warnings)

    assert main(["--pipeline", str(directory), "pipeline", "validate"]) == 0, "never blocking"
    printed = capsys.readouterr().out
    assert "plausibility.tax" in printed
    assert "field(s) disagree" in printed
    assert "taxRate" in printed


# ---------------------------------------------------------------------------
# file-supplies-a-derived-result
# ---------------------------------------------------------------------------


def test_a_file_supplying_a_mandate_dependent_result_is_rejected_with_the_reason_explained(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """§13 ingestion and `pipeline-schema.md` §9.

    IRR, MOIC and a terminal value are computed under somebody's hold period.
    Shipping one means the hold-period slider silently stops working, so the file
    is refused and the message says why rather than naming a schema violation.
    """
    directory = edge_corpus(tmp_path, add=["P50-supplies-derived-results.json"])
    loaded = load_pipeline(directory, ASSUMPTIONS)

    assert loaded.loaded_count == GOLDEN_COUNT
    rejected = [row for row in loaded.rejected if row.file == "P50-supplies-derived-results.json"]
    assert len(rejected) == 1
    assert rejected[0].check == "9 reject-derived fields"

    message = rejected[0].message
    for key in ("irr", "moic", "terminalValue"):
        assert key in message, "every offending key is collected, not just the first"
    assert "depends on the mandate's hold period" in message

    status = _status(loaded)
    entry = next(
        row for row in status["rejected"] if row["file"] == "P50-supplies-derived-results.json"
    )
    assert "depends on the mandate's hold period" in entry["message"]

    assert main(["--pipeline", str(directory), "pipeline", "validate"]) == 1
    printed = capsys.readouterr().out
    assert "9 reject-derived fields" in printed
    assert "depends on the mandate's hold period" in printed


# ---------------------------------------------------------------------------
# wrong-length-statement-series
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "length"),
    [
        ("P51-twenty-nine-statement-years.json", 29),
        ("P52-thirty-one-statement-years.json", 31),
    ],
)
def test_a_statement_series_of_the_wrong_length_names_the_field_and_the_expected_length(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], fixture: str, length: int
) -> None:
    """§13 ingestion: rejected, naming the field and the expected length.

    Thirty is not a preference. Every reduction downstream is a 2-D numpy one over
    a fixed grid, so a 29-year file would not be short by a year — it would be a
    different shape, and nothing after the loader is in a position to say so.
    """
    directory = edge_corpus(tmp_path, add=[fixture])
    loaded = load_pipeline(directory, ASSUMPTIONS)

    assert loaded.loaded_count == GOLDEN_COUNT
    rejected = [row for row in loaded.rejected if row.file == fixture]
    assert len(rejected) == 1
    assert rejected[0].check == "schema"
    assert rejected[0].year is None, "a shape failure belongs to no year"

    message = rejected[0].message
    assert "statements.years" in message, "the field, by its wire name"
    assert f"must have exactly 30 annual values, got {length}" in message

    status = _status(loaded)
    entry = next(row for row in status["rejected"] if row["file"] == fixture)
    assert "statements.years" in entry["message"]

    assert main(["--pipeline", str(directory), "pipeline", "validate"]) == 1
    printed = capsys.readouterr().out
    assert fixture in printed
    assert f"must have exactly 30 annual values, got {length}" in printed


# ---------------------------------------------------------------------------
# non-eur-under-the-eur-only-screen
# ---------------------------------------------------------------------------


NON_EUR_IDS = (
    "P19", "P20", "P21", "P23", "P24", "P27", "P28",
    "P33", "P34", "P43", "P44", "P45", "P46", "P48",
)  # fmt: skip
"""The 14 golden files whose revenue is PLN, RON, DKK, SEK or GBP."""


def test_a_non_eur_file_is_screened_out_rather_than_converted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """§13 ingestion and §3: v1 is single-currency, so a market is in or out.

    The assertion that matters is the second one. Screening a project out is
    cheap to get right; what a currency screen must never do is quietly apply a
    rate, and the only way to show it has not is to read the same project's
    capital back under both settings and find it unmoved. `currency` is the
    **revenue** currency; the statements are always euros (A-12).
    """
    loaded = golden_load()
    arrays = loaded.arrays
    index = {project_id: position for position, project_id in enumerate(arrays.ids)}

    screened = apply_screens(arrays, _scalars(mandate(eurRevenueOnly=True)), ASSUMPTIONS)
    open_to_all = apply_screens(arrays, _scalars(mandate(eurRevenueOnly=False)), ASSUMPTIONS)

    assert screened.drops["eurRevenue"] == len(NON_EUR_IDS)
    assert open_to_all.drops["eurRevenue"] == 0, "the screen is off unless the mandate asks"
    for project_id in NON_EUR_IDS:
        position = index[project_id]
        assert not bool(screened.passes["eurRevenue"][position])
        assert bool(open_to_all.passes["eurRevenue"][position])
        assert not bool(screened.eligible[position]), "a non-EUR project never enters the search"

    # Turning the toggle changes the currency screen and nothing else. Some of the
    # fourteen fail other screens too, so their eligibility is not the thing to
    # compare; the masks are.
    moved = {
        name
        for name in screened.passes
        if not np.array_equal(screened.passes[name], open_to_all.passes[name])
    }
    assert moved == {"eurRevenue"}
    admitted = [
        arrays.ids[position]
        for position in range(len(arrays.ids))
        if bool(open_to_all.eligible[position]) and not bool(screened.eligible[position])
    ]
    assert set(admitted) <= set(NON_EUR_IDS)
    assert admitted, "the screen has to cost the pool something, or it proves nothing"

    # Screened, not converted: the capital is the file's own, under either setting.
    for project_id in NON_EUR_IDS:
        position = index[project_id]
        declared = loaded.files[position].capital_structure.total_capex
        assert arrays.capital.total_capex[position] == pytest.approx(declared * EUR_PER_EUR_MILLION)

    # The user-visible half: the screen names its own cost, independently of the
    # others (2A-8). The default mandate's COD window and risk cap also bite, so
    # the eligible count is not the currency screen's alone — the drop count is.
    assert main(["--pipeline", str(GOLDEN_PIPELINE), "preview"]) == 0
    without = capsys.readouterr().out
    assert main(["--pipeline", str(GOLDEN_PIPELINE), "preview", "--eur-only"]) == 0
    with_screen = capsys.readouterr().out

    assert f"eurRevenue     rejects {len(NON_EUR_IDS)} on its own" in with_screen
    assert "eurRevenue" not in without, "an unasked screen rejects nothing and is not listed"
    assert _eligible_count(with_screen) < _eligible_count(without)


# ---------------------------------------------------------------------------
# The fixtures themselves
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", sorted(EDGE_PROVENANCE))
def test_every_edge_fixture_differs_from_its_base_only_where_its_name_says(fixture: str) -> None:
    """The fixtures are derived, so they can drift; this is what stops them.

    Repair every pointer ``tests/fixtures/edge/README.md`` documents, and the file
    must be its base again. One assertion, three guarantees: the fixture is
    current with 1A's schema, current with 1C's corpus, and broken in **exactly
    one** way — which is what stops a test named for one reason from passing on
    another.
    """
    base = base_payload()
    repaired = repair(edge_payload(fixture), base, EDGE_PROVENANCE[fixture])
    assert repaired == base
