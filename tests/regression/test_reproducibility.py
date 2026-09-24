"""§12's four reproducibility claims, each asserted on the bytes the API serves.

§12 is unusually strong — "mandate + pipeline hash + assumption set + seed determines
the result **exactly**. Regression tests assert this across releases" — and each claim
fails in a different way, so each gets its own test:

**Same seed, same bytes.** The headline claim.

**Order independence.** The pipeline is a directory. A user's filenames are not part of
their mandate, so loading the same 48 files under names that reverse their id order has
to produce the same portfolio. This is the test most likely to catch a real bug, because
the canonical ordering it depends on is invisible until something indexes by position —
and the GA indexes its PRNG draws by position.

**Id-keyed jitter.** Adding a candidate must not reprice the ones already there, or every
stored run stops explaining its own numbers.

**Round trip.** A stored run has to carry enough to re-execute itself. If it does not,
the audit trail is decoration.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import numpy as np
import pytest
from pool import GOLDEN_PIPELINE
from runs import VOLATILE, served_run, settings_for, stable

from terrafolio.api.app import create_app
from terrafolio.api.service import build_service
from terrafolio.config.loader import load_default
from terrafolio.pipeline.loader import load_pipeline
from terrafolio.runner.threads import observed_threads

SEED = 4242


def _diagnosis(what: str) -> str:
    """Name the three things that legitimately move a result (§12's audit trail)."""
    return (
        f"{what} moved. numpy {np.__version__}, BLAS threads {observed_threads()},"
        f" deterministic_reduction off (the default GEMM path)."
        " A deliberate dependency bump means refreshing the expectation and saying so;"
        " anything else is a real divergence."
    )


def _copy_pipeline(destination: Path, *, rename: bool = False) -> Path:
    """The 48 golden files, optionally under names that reverse their id order.

    ``load_pipeline`` globs in filename order and then re-sorts by ``id``, so renaming
    is the only way to make the two orders disagree. ``z000`` holds what ``P48`` held.
    """
    destination.mkdir(parents=True, exist_ok=True)
    sources = sorted(GOLDEN_PIPELINE.glob("*.json"))
    for index, source in enumerate(sources):
        name = f"z{len(sources) - 1 - index:03d}.json" if rename else source.name
        shutil.copyfile(source, destination / name)
    return destination


async def test_the_same_seed_serves_byte_identical_results(tmp_path: Path) -> None:
    """The §12 headline, on two runs through two databases in one process."""
    first = await served_run(settings_for(tmp_path / "a"), seed=SEED)
    second = await served_run(settings_for(tmp_path / "b"), seed=SEED)

    assert first.comparable == second.comparable, _diagnosis("the served result")
    assert first.body["provenance"] == second.body["provenance"]
    assert first.run_id != second.run_id, "two submissions are two runs"


async def test_the_stored_bytes_are_the_served_bytes(tmp_path: Path) -> None:
    """``load_result_json`` exists so the endpoint never re-serialises a result.

    If the endpoint built a model instead, §11's "the same id always reopens the same
    result" would hold only as long as no dependency changed how a float renders.
    """
    run = await served_run(settings_for(tmp_path), seed=SEED)
    assert stable(json.loads(run.stored_json)) == run.comparable


async def test_a_different_seed_reaches_a_different_answer(tmp_path: Path) -> None:
    """The counterweight: if every seed agreed, the tests above would prove nothing."""
    first = await served_run(settings_for(tmp_path / "a"), seed=SEED, effort="standard")
    second = await served_run(settings_for(tmp_path / "b"), seed=SEED + 1, effort="standard")
    assert first.comparable != second.comparable


async def test_shuffling_the_file_names_changes_nothing(tmp_path: Path) -> None:
    """Order independence, end to end rather than at the loader.

    ``tests/unit/test_pipeline_loader.py`` already proves the hash and the arrays
    survive a rename. This proves the *portfolio* does, which is the claim a user
    would notice being false.
    """
    plain = await served_run(
        settings_for(tmp_path / "a", pipeline=_copy_pipeline(tmp_path / "plain")), seed=SEED
    )
    renamed = await served_run(
        settings_for(tmp_path / "b", pipeline=_copy_pipeline(tmp_path / "renamed", rename=True)),
        seed=SEED,
    )

    assert plain.comparable == renamed.comparable, _diagnosis("the result under a rename")
    assert plain.body["provenance"]["pipelineHash"] == renamed.body["provenance"]["pipelineHash"]
    assert plain.selected_ids == renamed.selected_ids


def test_inserting_a_project_at_the_head_reprices_no_other_capex(tmp_path: Path) -> None:
    """Id-keyed jitter, asserted on the loaded arrays rather than on the files.

    ``tests/unit/test_generate_pipeline.py`` asserts the *files* are byte-equal, which
    is where the keying lives. This asserts the consequence one level up: after a load,
    every pre-existing project's capex, senior debt and capacity are bit-identical, and
    the inserted project has taken a row of its own.

    The inserted id keeps the width of the others on purpose. Mixed-width ids abort a
    load outright (``"P10" < "P9"``), so a test that inserted ``P000`` into ``P01..P48``
    would be exercising that guard instead of this one.
    """
    assumptions = load_default()
    before_dir = _copy_pipeline(tmp_path / "before")
    after_dir = _copy_pipeline(tmp_path / "after")

    original = load_pipeline(before_dir, assumptions)
    head = sorted(original.arrays.ids)[0]
    inserted_id = "P00"
    assert len(inserted_id) == len(head), "the inserted id must keep the pipeline's id width"
    assert inserted_id < head, "the point is to insert at the head, not in the middle"

    source = json.loads(
        (GOLDEN_PIPELINE / sorted(GOLDEN_PIPELINE.glob("*.json"))[0].name).read_text()
    )
    source["id"] = inserted_id
    source["name"] = "Inserted Solar"
    (after_dir / f"{inserted_id}-inserted.json").write_text(json.dumps(source), encoding="utf-8")

    grown = load_pipeline(after_dir, assumptions)
    assert grown.arrays.count == original.arrays.count + 1
    assert grown.arrays.ids[0] == inserted_id

    positions = grown.arrays.index_map()
    for index, project_id in enumerate(original.arrays.ids):
        moved = positions[project_id]
        for name, before, after in (
            ("capex", original.arrays.capital.total_capex, grown.arrays.capital.total_capex),
            ("seniorDebt", original.arrays.capital.senior_debt, grown.arrays.capital.senior_debt),
            ("capacity", original.arrays.asset.capacity_mw, grown.arrays.asset.capacity_mw),
        ):
            assert float(before[index]) == float(after[moved]), (
                f"{project_id}'s {name} moved when {inserted_id} was inserted"
            )


@pytest.mark.parametrize("field", VOLATILE)
def test_the_volatile_list_is_the_only_exemption(field: str) -> None:
    """A guard on the guard: every exempted field must be one §12 does not promise.

    The comparison above is only as strong as this list is short, and the cheapest way
    for a future change to weaken it is to add a field here to make a test pass.
    """
    assert field in {"runId", "runRef", "createdAt", "durationMs"}
    assert len(VOLATILE) == 4, "adding an exemption narrows §12's guarantee; say so in a decision"


async def test_a_stored_run_carries_enough_to_re_execute_itself(tmp_path: Path) -> None:
    """§12's audit trail, tested as a capability rather than as a set of columns.

    The re-submission is built from the stored record and nothing else: its mandate,
    its effort, its locks, its exclusions and its seed. If any of those were missing
    from what is stored — or stored in a form that does not round trip — the second run
    would answer differently, which is the only failure mode worth testing here.

    Locks and exclusions are set deliberately. A round trip over a run with neither
    would pass while both fields were being dropped.
    """
    settings = settings_for(tmp_path / "original")
    assumptions = load_default()
    ids = sorted(load_pipeline(GOLDEN_PIPELINE, assumptions).arrays.ids)
    locked, excluded = (ids[0], ids[1]), (ids[-1],)

    original = await served_run(
        settings, seed=SEED, effort="standard", locked_ids=locked, excluded_ids=excluded
    )
    stored = original.record
    assert stored["lockedIds"] == list(locked)
    assert stored["excludedIds"] == list(excluded)

    replayed = await served_run(
        settings_for(tmp_path / "replay"),
        seed=stored["provenance"]["seed"],
        effort=stored["effort"],
        locked_ids=stored["lockedIds"],
        excluded_ids=stored["excludedIds"],
        **dict(stored["mandate"]),
    )

    assert replayed.comparable == original.comparable, _diagnosis("a re-executed run")
    assert set(locked) <= set(replayed.selected_ids), "a locked project must be held"
    assert not set(excluded) & set(replayed.selected_ids), "an excluded project must not be"


async def test_a_replay_against_a_changed_pipeline_is_refused_rather_than_answered(
    tmp_path: Path,
) -> None:
    """The other half of the round trip: the recorded hash has to be load-bearing.

    A record that replays happily against a pipeline it was not produced from is worse
    than one that fails, because it invents a reproduction that never happened. So the
    pipeline is genuinely changed — one file removed — and the replay is submitted with
    the hash the original recorded.
    """
    directory = _copy_pipeline(tmp_path / "pipeline")
    original = await served_run(settings_for(tmp_path / "a", pipeline=directory), seed=SEED)
    recorded = original.body["provenance"]["pipelineHash"]

    sorted(directory.glob("*.json"))[0].unlink()
    assert load_pipeline(directory, load_default()).pipeline_hash != recorded

    settings = settings_for(tmp_path / "b", pipeline=directory)
    service = build_service(settings)
    try:
        app = create_app(settings, service=service)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                refused = await client.post(
                    "/optimisations",
                    json={
                        "mandate": dict(original.body["mandate"]),
                        "effort": original.body["effort"],
                        "seed": original.body["provenance"]["seed"],
                        "pipelineHash": recorded,
                    },
                )
    finally:
        service.shutdown()

    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "PIPELINE_MOVED"
    assert error["detail"]["currentPipelineHash"] != recorded
