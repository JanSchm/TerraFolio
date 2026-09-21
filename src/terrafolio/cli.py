"""``terrafolio`` -- the command line.

Deliberately thin. The epic gives this module to issue 2A, which owns the shape
and will add ``serve``, ``pipeline validate``, ``run``, ``show``, ``export`` and
``bench``. It lands here first only because 2C's first acceptance criterion is
``terrafolio pipeline generate --count 300 --seed 1``, and 2A has not started.

So the structure is additive rather than clever: one function per command group,
each handed the subparser action to register against, and a dispatch table keyed
by the parser's own ``dest``. Adding a group is a function and a line; nothing
here needs rearranging to make room for one.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.config.loader import load_default
from terrafolio.domain.errors import format_validation_error
from terrafolio.domain.project_file import ProjectFile
from terrafolio.generate.from_file import inputs_from_file, statements_from_file
from terrafolio.generate.pipeline import generate_pipeline, write_pipeline
from terrafolio.generate.spreadsheet import read_workbook, write_workbook
from terrafolio.model.variance import compare_to_house_model

DEFAULT_PIPELINE = Path("pipeline")
DEFAULT_COUNT = 300
DEFAULT_SEED = 1


def _add_pipeline(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """``pipeline`` -- generate the seed pipeline and move files to and from Excel."""
    pipeline = subparsers.add_parser("pipeline", help="generate, ingest and export project files")
    actions = pipeline.add_subparsers(dest="action", required=True)

    generate = actions.add_parser("generate", help="write a seed pipeline of project files")
    generate.add_argument("--count", type=int, default=DEFAULT_COUNT, help="how many projects")
    generate.add_argument("--seed", type=int, default=DEFAULT_SEED, help="site-pool seed")
    generate.add_argument("--out", type=Path, default=DEFAULT_PIPELINE, help="output directory")

    ingest = actions.add_parser("ingest", help="read an analyst workbook into canonical JSON")
    ingest.add_argument("workbook", type=Path, help="an .xlsx laid out as the template is")
    ingest.add_argument("--out", type=Path, default=DEFAULT_PIPELINE, help="output directory")

    export = actions.add_parser("export", help="write a project out as an analyst workbook")
    export.add_argument("--id", dest="project_id", required=True, help="the project to export")
    export.add_argument(
        "--pipeline", type=Path, default=DEFAULT_PIPELINE, help="pipeline directory"
    )
    export.add_argument("--out", type=Path, default=None, help="output path or directory")
    export.add_argument(
        "--xlsx", action="store_true", help="write a workbook (the only format so far)"
    )


def _generate(arguments: argparse.Namespace, assumptions: AssumptionSet) -> int:
    if arguments.count <= 0:
        print(f"--count must be positive, got {arguments.count}", file=sys.stderr)
        return 2
    built = generate_pipeline(arguments.count, arguments.seed, assumptions)
    written = list(write_pipeline(built, arguments.out, assumptions))
    resampled = sum(1 for project in built if project.attempts > 1)
    print(
        f"wrote {len(written)} project files to {arguments.out}/ "
        f"(seed {arguments.seed}, assumption set {assumptions.assumption_set_id})"
    )
    if resampled:
        print(f"  {resampled} redrawn to bring min DSCR inside its plausibility band")
    return 0


def _ingest(arguments: argparse.Namespace, assumptions: AssumptionSet) -> int:
    if not arguments.workbook.is_file():
        print(f"{arguments.workbook}: not a file", file=sys.stderr)
        return 2
    file = read_workbook(arguments.workbook)
    try:
        validated = ProjectFile.model_validate(file)
    except ValidationError as error:
        print(format_validation_error(str(arguments.workbook), error), file=sys.stderr)
        return 1

    arguments.out.mkdir(parents=True, exist_ok=True)
    target = arguments.out / f"{validated.id}.json"
    target.write_text(json.dumps(file, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{arguments.workbook} -> {target}")

    # The house model's second job (epic §2): re-derive the statements from the
    # file's own declared assumptions and say where the two disagree. It reports
    # and never gates -- the file has already been written above, and a
    # divergence is a modelling difference worth seeing, not a rejection (A-7).
    report = compare_to_house_model(
        inputs_from_file(file, assumptions),
        statements_from_file(file),
        tolerance_abs=assumptions.validation.tolerance_abs_m,
        tolerance_rel=assumptions.validation.tolerance_rel,
    )
    if report.agrees:
        print("  the house model reproduces this file on every line")
    else:
        print(f"  the house model differs on {len(report.diverging)} line(s):")
        for line in report.diverging:
            print(f"    {line}")
    return 0


def _export(arguments: argparse.Namespace, assumptions: AssumptionSet) -> int:
    if not arguments.xlsx:
        print("--xlsx is the only export format; pass it explicitly", file=sys.stderr)
        return 2
    matches = [
        path
        for path in sorted(arguments.pipeline.glob("*.json"))
        if json.loads(path.read_text(encoding="utf-8")).get("id") == arguments.project_id
    ]
    if not matches:
        print(
            f"{arguments.project_id}: no project with that id in {arguments.pipeline}/",
            file=sys.stderr,
        )
        return 1
    source = json.loads(matches[0].read_text(encoding="utf-8"))
    destination = arguments.out or Path(f"{arguments.project_id}.xlsx")
    if destination.is_dir():
        destination = destination / f"{arguments.project_id}.xlsx"
    written = write_workbook(source, destination)
    print(f"{matches[0]} -> {written}")
    return 0


_PIPELINE_ACTIONS = {"generate": _generate, "ingest": _ingest, "export": _export}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="terrafolio",
        description="Renewables portfolio optimiser: mandate in, committee-ready portfolio out.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_pipeline(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit status rather than raising."""
    arguments = build_parser().parse_args(argv)
    assumptions = load_default()
    if arguments.command == "pipeline":
        return _PIPELINE_ACTIONS[arguments.action](arguments, assumptions)
    raise AssertionError(f"unreachable: argparse accepted {arguments.command!r}")


if __name__ == "__main__":  # pragma: no cover - exercised through the console script
    raise SystemExit(main())
