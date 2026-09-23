"""The shipped pipeline: it loads clean, it is stable, and it exercises the screens.

Issue #8's first acceptance criterion is that ``pipeline generate --count 300``
produces 300 files that pass every tie-out with **zero** failures and **zero**
plausibility warnings. 2A owns the validator and has not landed, so the checks
here are written against ``docs/pipeline-schema.md`` §7 and §10 directly -- the
same normative text 2A implements -- and applied to the emitted JSON rather than
to the model's arrays, so the serialiser is in scope too.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Final

import numpy as np
import pytest

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.config.loader import load_default
from terrafolio.domain.conventions import EUR_PER_EUR_MILLION, MWH_PER_GWH, YEARS, canonical_order
from terrafolio.domain.project_file import ProjectFile
from terrafolio.generate.pipeline import (
    BuiltProject,
    PipelineCollisionError,
    as_file,
    build_project,
    generate_pipeline,
    write_pipeline,
)
from terrafolio.generate.sites import build_pool

COUNT: Final = 300
SEED: Final = 1
MANDATE_DSCR_FLOOR: Final = 1.25
"""``domain/mandate_bounds.py``'s default for the §5.2 DSCR slider."""


@pytest.fixture(scope="module")
def assumptions() -> AssumptionSet:
    return load_default()


@pytest.fixture(scope="module")
def built(assumptions: AssumptionSet) -> list[BuiltProject]:
    return generate_pipeline(COUNT, SEED, assumptions)


@pytest.fixture(scope="module")
def emitted(built: list[BuiltProject], assumptions: AssumptionSet) -> list[dict[str, Any]]:
    return [as_file(project, assumptions) for project in built]


def ties(residual: float, reference: float, assumptions: AssumptionSet) -> bool:
    """§7's tolerance: EUR 0.01m absolute or 0.1% relative, whichever is looser."""
    validation = assumptions.validation
    return abs(residual) <= max(
        validation.tolerance_abs_m, validation.tolerance_rel * abs(reference)
    )


# --------------------------------------------------------------------------
# It loads
# --------------------------------------------------------------------------


def test_the_requested_number_of_projects_is_produced(built: list[BuiltProject]) -> None:
    assert len(built) == COUNT


def test_every_file_validates_against_the_schema(emitted: list[dict[str, Any]]) -> None:
    for file in emitted:
        ProjectFile.model_validate(file)


def test_ids_are_unique_and_uniformly_padded(emitted: list[dict[str, Any]]) -> None:
    """C-5: canonical order is a plain lexicographic sort, so widths must match.

    ``P9`` sorting after ``P10`` is a determinism bug, not an untidy listing:
    the GA's draws are indexed by position.
    """
    ids = [file["id"] for file in emitted]
    assert len(set(ids)) == len(ids)
    assert len({len(value) for value in ids}) == 1
    assert canonical_order(ids) == tuple(ids)


def test_the_pipeline_shares_one_base_year(emitted: list[dict[str, Any]]) -> None:
    """§4.6.1 -- a disagreement fails the load as a whole, not file by file."""
    assert len({file["assumptions"]["baseYear"] for file in emitted}) == 1


def test_every_series_is_thirty_years_anchored_to_the_base_year(
    emitted: list[dict[str, Any]],
) -> None:
    for file in emitted:
        base = file["assumptions"]["baseYear"]
        statements = file["statements"]
        assert statements["years"] == list(range(base, base + YEARS))
        for block in ("physicals", "incomeStatement", "cashFlow", "debtSchedule", "balanceSheet"):
            for series in statements[block].values():
                assert len(series) == YEARS


def test_dscr_is_non_null_exactly_inside_the_debt_life(emitted: list[dict[str, Any]]) -> None:
    """§7.8's placement check, which is blocking in its own right."""
    for file in emitted:
        cod = file["asset"]["codYear"]
        tenor = file["assumptions"]["debtTenorYears"]
        years = file["statements"]["years"]
        for year, value in zip(years, file["statements"]["ratios"]["dscr"], strict=True):
            inside = 0 <= year - cod < tenor
            assert (value is not None) is inside, f"{file['id']} {year}"


# --------------------------------------------------------------------------
# Zero tie-out failures (§7)
# --------------------------------------------------------------------------


def test_every_tie_out_holds_on_every_file(
    emitted: list[dict[str, Any]], assumptions: AssumptionSet
) -> None:
    failures: list[str] = []

    def check(name: str, file: dict[str, Any], left: Any, right: Any) -> None:
        left_array, right_array = np.asarray(left, dtype=float), np.asarray(right, dtype=float)
        residual = float(np.max(np.abs(left_array - right_array)))
        scale = float(np.max(np.abs(right_array))) if right_array.size else 0.0
        if not ties(residual, scale, assumptions):
            failures.append(f"{file['id']} {name}: residual {residual:.3e}")

    for file in emitted:
        s = file["statements"]
        income, cash = s["incomeStatement"], s["cashFlow"]
        debt, capital = s["debtSchedule"], file["capitalStructure"]
        physicals = s["physicals"]
        revenue = np.array(income["revenue"])
        opex = np.array(income["opex"])
        ebitda = np.array(income["ebitda"])
        capex = np.array(cash["capex"])
        depreciation = np.array(income["depreciation"])
        ppe = np.array(s["balanceSheet"]["ppe"])

        # 7.1 income statement
        check("ebitda", file, ebitda, revenue - opex)
        check("ebit", file, income["ebit"], ebitda - depreciation)
        check(
            "pbt",
            file,
            income["pbt"],
            np.array(income["ebit"]) - np.array(income["interestExpense"]),
        )
        check(
            "netIncome",
            file,
            income["netIncome"],
            np.array(income["pbt"]) - np.array(income["taxExpense"]),
        )
        # 7.2 revenue ties to physicals
        check(
            "revenue vs physicals",
            file,
            revenue,
            np.array(physicals["generationGwh"])
            * MWH_PER_GWH
            * np.array(physicals["achievedPrice"])
            / EUR_PER_EUR_MILLION,
        )
        # 7.3 cash flow, in its published form
        check(
            "fcfe",
            file,
            cash["fcfe"],
            ebitda
            - np.array(cash["interestPaid"])
            - np.array(cash["debtRepayment"])
            - np.array(cash["taxPaid"])
            - capex
            + np.array(cash["debtDrawdown"]),
        )
        # 7.4 debt schedule
        opening, closing = np.array(debt["opening"]), np.array(debt["closing"])
        check(
            "closing",
            file,
            closing,
            opening - np.array(debt["repayment"]) + np.array(debt["drawdown"]),
        )
        check("continuity", file, opening[1:], closing[:-1])
        assert debt["opening"][0] == 0.0, file["id"]
        assert ties(closing[-1], 0.0, assumptions), file["id"]
        # 7.5 funding
        equity = capital["totalCapex"] - capital["seniorDebt"]
        check("sum debtDrawdown", file, [sum(cash["debtDrawdown"])], [capital["seniorDebt"]])
        check("sum equityDrawdown", file, [sum(cash["equityDrawdown"])], [equity])
        check("sum capex", file, [sum(cash["capex"])], [capital["totalCapex"]])
        check(
            "per-year funding",
            file,
            capex,
            np.array(cash["debtDrawdown"]) + np.array(cash["equityDrawdown"]),
        )
        # 7.6 depreciation and PP&E, in A-3's residual form
        check("sum depreciation", file, [depreciation.sum()], [capital["totalCapex"] - ppe[-1]])
        check(
            "ppe roll-forward", file, ppe, np.concatenate(([0.0], ppe[:-1])) - depreciation + capex
        )
        # 7.7 dscr
        live = [i for i, v in enumerate(s["ratios"]["dscr"]) if v is not None]
        service = np.array([cash["interestPaid"][i] + cash["debtRepayment"][i] for i in live])
        check(
            "dscr", file, np.array([s["ratios"]["dscr"][i] for i in live]) * service, ebitda[live]
        )

    assert not failures, "\n".join(failures[:10])


# --------------------------------------------------------------------------
# Zero plausibility warnings (§10)
# --------------------------------------------------------------------------


def test_no_file_raises_a_plausibility_warning(
    built: list[BuiltProject], emitted: list[dict[str, Any]], assumptions: AssumptionSet
) -> None:
    """All six §10 checks. Ceiling checks carry the relative tolerance."""
    validation = assumptions.validation
    relative = validation.tolerance_rel
    warnings: list[str] = []

    for project, file in zip(built, emitted, strict=True):
        asset, capital = file["asset"], file["capitalStructure"]
        factor = asset["netCapacityFactor"]
        if not validation.capacity_factor.low <= factor <= validation.capacity_factor.high:
            warnings.append(f"{file['id']} capacity factor {factor}")

        per_kw = capital["totalCapex"] * EUR_PER_EUR_MILLION / (asset["capacityMw"] * MWH_PER_GWH)
        band = validation.capex_per_kw[project.site.technology]
        if not band.low * (1 - relative) <= per_kw <= band.high * (1 + relative):
            warnings.append(f"{file['id']} capex/kW {per_kw:.1f}")

        gearing = capital["seniorDebt"] / capital["totalCapex"]
        ceiling = validation.stage_gearing_ceiling[project.site.stage]
        if gearing > ceiling * (1 + relative):
            warnings.append(f"{file['id']} gearing {gearing:.6f} over stage ceiling {ceiling}")
        if gearing > capital["maxGearing"] * (1 + relative):
            warnings.append(f"{file['id']} gearing over its own declared ceiling")

        rate = file["assumptions"]["taxRate"]
        for index, (charged, pbt) in enumerate(
            zip(
                file["statements"]["incomeStatement"]["taxExpense"],
                file["statements"]["incomeStatement"]["pbt"],
                strict=True,
            )
        ):
            expected = rate * max(0.0, pbt)
            if not ties(charged - expected, expected, assumptions):
                warnings.append(f"{file['id']} tax year {index}")

        if not validation.min_dscr_band.low <= project.min_dscr <= validation.min_dscr_band.high:
            warnings.append(f"{file['id']} min DSCR {project.min_dscr:.4f}")

    assert not warnings, "\n".join(warnings[:10])


# --------------------------------------------------------------------------
# It exercises the screens (D-3)
# --------------------------------------------------------------------------


def test_min_dscr_is_two_sided_around_the_sizing_target(
    built: list[BuiltProject], assumptions: AssumptionSet
) -> None:
    """D-3's whole point: sculpting off stabilised EBITDA gives a distribution.

    Sizing off the minimum over the debt life would pin min DSCR at the target
    and leave §5.2's slider a dead control over most of its range.
    """
    target = assumptions.generator.target_dscr
    covers = np.array([project.min_dscr for project in built])
    assert np.sum(covers < target) > 0, "nothing below the sizing target"
    assert np.sum(covers > target) > 0, "nothing above the sizing target"
    assert covers.min() < target < covers.max()


def test_a_realistic_share_falls_under_the_default_mandate_floor(
    built: list[BuiltProject],
) -> None:
    """Otherwise §7.4's "red below the mandate floor" never fires and the
    worst-DSCR tile is a constant."""
    covers = np.array([project.min_dscr for project in built])
    share = float(np.mean(covers < MANDATE_DSCR_FLOOR))
    assert 0.01 <= share <= 0.25, f"{share:.1%} below {MANDATE_DSCR_FLOOR}"


def test_both_sizing_constraints_bind_somewhere(built: list[BuiltProject]) -> None:
    bases = {project.drawn.debt_basis for project in built}
    assert bases == {"max-gearing-cap", "dscr-sculpt"}


def test_the_entry_clamp_binds_on_both_sides(built: list[BuiltProject]) -> None:
    """§9.1's clamp is what lets uneconomic assets exist for the screens to reject."""
    clamps = {project.drawn.capex_clamp for project in built}
    assert clamps == {"floor", "cap", "none"}


def test_the_pipeline_spans_every_market_technology_and_stage(
    emitted: list[dict[str, Any]],
) -> None:
    assert len({file["location"]["countryCode"] for file in emitted}) == 14
    assert len({file["asset"]["technology"] for file in emitted}) == 3
    assert len({file["asset"]["stage"] for file in emitted}) == 3
    years = {file["asset"]["codYear"] for file in emitted}
    assert min(years) == 2027
    assert max(years) == 2033


# --------------------------------------------------------------------------
# It is stable
# --------------------------------------------------------------------------


def test_generation_is_deterministic(
    emitted: list[dict[str, Any]], assumptions: AssumptionSet
) -> None:
    again = [
        as_file(project, assumptions) for project in generate_pipeline(COUNT, SEED, assumptions)
    ]
    assert json.dumps(again, sort_keys=True) == json.dumps(emitted, sort_keys=True)


def test_inserting_a_project_at_the_head_reprices_nothing(assumptions: AssumptionSet) -> None:
    """The reason jitter is keyed on the id rather than on the index.

    A pipeline is a directory a user drops files into. Index-keyed jitter would
    mean adding one candidate silently changed every other project's capex --
    and every stored run would stop explaining its own numbers.
    """
    pool = build_pool(24, SEED, assumptions)
    before = {site.id: as_file(build_project(site, assumptions), assumptions) for site in pool}

    inserted = replace(pool[0], id="P000", name="Inserted Solar")
    after = {
        site.id: as_file(build_project(site, assumptions), assumptions)
        for site in (inserted, *pool)
    }

    assert "P000" in after
    for project_id, file in before.items():
        assert after[project_id] == file, f"{project_id} moved when P000 was inserted"
    # The inserted project draws its own economics. Asserted on the capacity
    # factor rather than on capex, because capex is clamped to a EUR/kW band --
    # two same-sized projects that both clamp to the floor share a capex without
    # sharing a stream, which is the clamp working, not the keying failing.
    assert (
        after["P000"]["asset"]["netCapacityFactor"]
        != before[pool[0].id]["asset"]["netCapacityFactor"]
    ), "the inserted project should draw from its own stream"


def test_the_pipeline_scales_to_the_two_thousand_candidate_target(
    assumptions: AssumptionSet,
) -> None:
    """Epic §12 wants 2,000 candidates without an architectural change."""
    large = generate_pipeline(2000, SEED, assumptions)
    assert len(large) == 2000
    ids = [project.site.id for project in large]
    assert len(set(ids)) == 2000
    assert len({len(value) for value in ids}) == 1
    assert len({project.site.name for project in large}) == 2000


def test_a_count_of_zero_is_refused(assumptions: AssumptionSet) -> None:
    with pytest.raises(ValueError, match="count"):
        generate_pipeline(0, SEED, assumptions)


# --------------------------------------------------------------------------
# Writing a pipeline replaces the previous one, and only the previous one
# --------------------------------------------------------------------------


def generated_ids(directory: Path) -> list[str]:
    return sorted(
        json.loads(path.read_text(encoding="utf-8"))["id"] for path in directory.glob("*.json")
    )


def test_a_smaller_regeneration_removes_the_projects_it_no_longer_produces(
    assumptions: AssumptionSet, tmp_path: Path
) -> None:
    """Writing only the new filenames left the old ones behind.

    Dropping --count from 30 to 10 used to leave 20 stale projects in the
    directory, which a consumer then loads as though they were part of the run.
    """
    write_pipeline(generate_pipeline(30, SEED, assumptions), tmp_path, assumptions)
    assert len(generated_ids(tmp_path)) == 30

    outcome = write_pipeline(generate_pipeline(10, SEED, assumptions), tmp_path, assumptions)
    assert len(generated_ids(tmp_path)) == 10
    assert len(outcome.written) == 10
    assert len(outcome.replaced) == 20


def test_changing_the_seed_does_not_leave_two_files_per_id(
    assumptions: AssumptionSet, tmp_path: Path
) -> None:
    """The sharper version of the same bug, and the one that breaks a load.

    A new seed renames every site, so every *filename* changes while every *id*
    stays the same. Overwriting by filename therefore produced two files for
    each id -- and §7.9 aborts the entire load on a duplicate id, so the whole
    pipeline became unusable rather than merely stale.
    """
    write_pipeline(generate_pipeline(12, 1, assumptions), tmp_path, assumptions)
    before = generated_ids(tmp_path)
    write_pipeline(generate_pipeline(12, 2, assumptions), tmp_path, assumptions)
    after = generated_ids(tmp_path)

    assert after == before
    assert len(after) == len(set(after)) == 12


def test_a_file_this_generator_did_not_write_is_left_alone(
    assumptions: AssumptionSet, tmp_path: Path
) -> None:
    """§1: a user adds a project by dropping a file in. It is not ours to delete."""
    write_pipeline(generate_pipeline(8, SEED, assumptions), tmp_path, assumptions)
    hand_written = tmp_path / "ANALYST1-hand-written.json"
    theirs = json.loads((tmp_path / sorted(p.name for p in tmp_path.glob("*.json"))[0]).read_text())
    theirs["id"] = "ANALYST1"
    theirs["provenance"]["preparedBy"] = "someone@example.com"
    hand_written.write_text(json.dumps(theirs), encoding="utf-8")

    outcome = write_pipeline(generate_pipeline(8, 2, assumptions), tmp_path, assumptions)
    assert hand_written.exists()
    assert outcome.kept == (hand_written,)
    assert json.loads(hand_written.read_text(encoding="utf-8"))["id"] == "ANALYST1"


def test_an_id_collision_with_somebody_else_s_file_refuses_to_write(
    assumptions: AssumptionSet, tmp_path: Path
) -> None:
    """Better to refuse than to silently overwrite an analyst's project."""
    built = generate_pipeline(8, SEED, assumptions)
    write_pipeline(built, tmp_path, assumptions)
    theirs = as_file(built[0], assumptions)
    theirs["provenance"]["preparedBy"] = "someone@example.com"
    (tmp_path / "P1-someone-elses.json").write_text(json.dumps(theirs), encoding="utf-8")

    with pytest.raises(PipelineCollisionError, match="not written by this generator"):
        write_pipeline(built, tmp_path, assumptions)


def test_nothing_is_written_when_the_collision_check_fails(
    assumptions: AssumptionSet, tmp_path: Path
) -> None:
    """A refusal must leave the previous pipeline exactly as it was."""
    built = generate_pipeline(6, SEED, assumptions)
    write_pipeline(built, tmp_path, assumptions)
    before = {path.name: path.read_text(encoding="utf-8") for path in tmp_path.glob("*.json")}

    theirs = as_file(built[0], assumptions)
    theirs["provenance"]["preparedBy"] = "someone@example.com"
    intruder = tmp_path / "zz-someone-elses.json"
    intruder.write_text(json.dumps(theirs), encoding="utf-8")

    with pytest.raises(PipelineCollisionError):
        write_pipeline(generate_pipeline(6, 2, assumptions), tmp_path, assumptions)

    after = {
        path.name: path.read_text(encoding="utf-8")
        for path in tmp_path.glob("*.json")
        if path != intruder
    }
    assert after == before


def test_writing_leaves_no_staging_directory_behind(
    assumptions: AssumptionSet, tmp_path: Path
) -> None:
    target = tmp_path / "pipeline"
    write_pipeline(generate_pipeline(4, SEED, assumptions), target, assumptions)
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["pipeline"]


def test_the_committed_pipeline_is_what_the_generator_produces(
    assumptions: AssumptionSet, tmp_path: Path
) -> None:
    """The 300 files in `pipeline/` are reproducible from the command that made them."""
    repo_pipeline = Path(__file__).resolve().parents[2] / "pipeline"
    committed = {
        path.name: path.read_text(encoding="utf-8") for path in sorted(repo_pipeline.glob("*.json"))
    }
    write_pipeline(generate_pipeline(COUNT, SEED, assumptions), tmp_path, assumptions)
    regenerated = {
        path.name: path.read_text(encoding="utf-8") for path in sorted(tmp_path.glob("*.json"))
    }
    assert regenerated == committed
