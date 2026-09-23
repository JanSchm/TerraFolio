"""``terrafolio`` — the whole compute spine, verifiable before any HTTP exists.

Subcommands that between them exercise the compute spine (issue 2A) and the
seed pipeline and spreadsheet path (issue 2C):

``terrafolio pipeline validate``
    Load the pipeline, report per-file status, tie-out failures, plausibility
    warnings and the cross-file dispersion report.
``terrafolio pipeline generate``
    Write a seed pipeline of project files, replacing the set it last generated.
``terrafolio pipeline ingest``
    Read an analyst's workbook into canonical JSON, through the same schema and
    the same blocking tie-outs a JSON file passes.
``terrafolio pipeline export``
    Write a project back out as a workbook, so it can be revised and re-ingested.
``terrafolio preview``
    §5.4's feasibility footer for a mandate, including the screens to widen.
``terrafolio run``
    A full search, printing the selected ids and all twelve §7.1 tiles.
``terrafolio bench``
    Timings for the load and for the search at each effort level.

**This module is the display boundary.** The core works in euros; §14's formats are
€m with thousands separators, IRR to one decimal, DSCR and MOIC to two with a
multiplication sign, and an em dash for undefined. Those conversions happen here and
nowhere else in 2A — ``js/format.js`` does the same job for the browser, and 3A's API
does it for the wire.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import numpy as np
from pydantic import ValidationError

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.config.loader import load_default
from terrafolio.domain.conventions import EUR_PER_EUR_MILLION
from terrafolio.domain.enums import Effort, RiskAppetite, Stage, WarningCode
from terrafolio.domain.errors import format_validation_error, render_validation_error
from terrafolio.domain.mandate import Mandate
from terrafolio.domain.mandate_bounds import ALL_BOUNDS
from terrafolio.domain.project_file import ProjectFile
from terrafolio.domain.reduce import mandate_to_scalars
from terrafolio.domain.scalars import MandateScalars
from terrafolio.economics.returns import contracted_revenue_share, project_returns
from terrafolio.generate.from_file import inputs_from_file, statements_from_file
from terrafolio.generate.pipeline import (
    BuiltProject,
    PipelineCollisionError,
    PipelineExhaustedError,
    as_file,
    generate_pipeline,
    write_pipeline,
)
from terrafolio.generate.spreadsheet import read_workbook, write_workbook
from terrafolio.model.variance import compare_to_house_model
from terrafolio.optimiser.feasibility import (
    FeasibilityPreview,
    FeasibilitySignal,
    preview_feasibility,
)
from terrafolio.optimiser.features import build_features
from terrafolio.optimiser.ga import SearchControls, run_search
from terrafolio.optimiser.result import RunResult, SelectionOutcome, build_result
from terrafolio.pipeline.loader import LoadResult, PipelineLoadError, load_pipeline
from terrafolio.pipeline.validator import tie_out_failures

EM_DASH = "\u2014"
"""§14: undefined renders as an em dash, never as zero and never as a blank."""

TIMES = "\u00d7"
"""§14: DSCR and MOIC carry a true multiplication sign, not the letter x.

Written as an escape rather than as the glyph so it survives a source scan that
cannot tell a deliberate typographic character from a mistyped one.
"""
MILLISECONDS_PER_SECOND = 1000

BENCH_REPEATS: Final = 3  # structural: a benchmark repeat count, not a calibration
"""Enough runs for a best-of to mean something without making `bench` slow."""

BENCH_SEED: Final = 42  # structural: an arbitrary fixed seed, so two benchmarks compare

GENERATE_COUNT: Final = 300
"""The shipped pipeline's size. Epic §7 budgets the load at 300 files."""

GENERATE_SEED: Final = 1
"""The seed the committed pipeline was generated with."""


# ---------------------------------------------------------------------------
# §14's number formats, in one place
# ---------------------------------------------------------------------------


def millions(euros: float) -> str:
    """Euros in millions with thousands separators and no decimals (§14)."""
    return f"€{euros / EUR_PER_EUR_MILLION:,.0f}m"


def percentage(fraction: float | None) -> str:
    """``12.4%``, one decimal, or an em dash where the value is undefined."""
    return EM_DASH if fraction is None else f"{fraction * 100:.1f}%"


def multiple(value: float | None) -> str:
    """Two decimals with a multiplication sign, as §14 writes DSCR and MOIC."""
    return EM_DASH if value is None else f"{value:.2f}{TIMES}"


class MandateError(ValueError):
    """A mandate the specification's own control ranges do not permit."""


def _mandate_from(args: argparse.Namespace) -> MandateScalars:
    """Build a mandate from the command line, validated the way the wire validates it.

    Deliberately routed through the **pydantic** :class:`Mandate` rather than straight
    into :class:`MandateScalars`. The scalars record is the numeric core's input and
    validates nothing — it is a reduction, not a contract — so constructing it directly
    let ``--hold 31`` reach the economics layer and die in a traceback, and let an
    inverted COD window or an out-of-range share through entirely. §5's ranges belong
    to one model, and ``mandate_to_scalars`` is the only sanctioned way across.
    """
    # ``model_validate`` rather than the constructor: the model generates camelCase
    # aliases, so its ``__init__`` takes those, and spelling twenty wire names here
    # would be a second copy of the contract. ``validate_by_name`` accepts the field
    # names, which are what this file already speaks.
    try:
        mandate = Mandate.model_validate(
            {
                "available_capital_m": args.capital,
                "capacity_target_mw": args.target,
                "solar_share": args.solar_share,
                "target_irr": args.hurdle,
                "hold_years": args.hold,
                "countries": tuple(args.countries),
                "stages": tuple(Stage(stage) for stage in args.stages),
                "min_leverage": args.min_leverage,
                "min_dscr": args.min_dscr,
                "max_merchant_share": args.max_merchant,
                "max_country_share": args.max_country,
                "max_project_share": args.max_project,
                "cod_from": args.cod_from,
                "cod_to": args.cod_to,
                "risk_appetite": RiskAppetite(args.risk),
                "grid_secured_only": args.grid_only,
                "eur_revenue_only": args.eur_only,
                "om_contracted_only": args.om_only,
            }
        )
    except ValidationError as error:
        problems = "; ".join(render_validation_error(error))
        raise MandateError(problems) from error
    return mandate_to_scalars(mandate)


def _load(args: argparse.Namespace, assumptions: AssumptionSet) -> LoadResult:
    return load_pipeline(Path(args.pipeline), assumptions)


# ---------------------------------------------------------------------------
# pipeline validate
# ---------------------------------------------------------------------------


def _validate(args: argparse.Namespace, assumptions: AssumptionSet) -> int:
    loaded = _load(args, assumptions)
    print(
        f"Loaded {loaded.loaded_count} of {loaded.file_count} files "
        f"in {loaded.duration_ms} ms  ({loaded.pipeline_hash[:23]}…)"
    )
    print(f"Base year {loaded.arrays.base_year}, assumption set {assumptions.assumption_set_id}")

    if loaded.rejected:
        print(f"\nRejected — {len(loaded.rejected)} failure(s):")
        for row in loaded.rejected:
            where = f" {row.year}" if row.year is not None else ""
            print(f"  {row.file}: {row.check}{where}")
            print(f"    {row.message}")
    else:
        print("\nEvery file passed every tie-out.")

    if loaded.warnings:
        print(f"\nPlausibility warnings — {len(loaded.warnings)} (never blocking):")
        for warning in loaded.warnings:
            print(f"  {warning.check}: {warning.message}")
    else:
        print("No plausibility warnings.")

    print("\nDeclared assumptions across the pipeline:")
    for name, spread in loaded.dispersion.pipeline_wide.items():
        if spread.agrees:
            print(f"  {name:22s} all {spread.count} files agree at {spread.median:g}")
        else:
            tails = ", ".join(spread.outliers[:OUTLIER_SAMPLE])
            print(
                f"  {name:22s} median {spread.median:g}  "
                f"range {spread.minimum:g} to {spread.maximum:g}  tails: {tails}"
            )
    disagreeing = loaded.dispersion.disagreements
    if disagreeing:
        print(f"\n{len(disagreeing)} field(s) disagree: {', '.join(disagreeing[:8])}")

    return 1 if loaded.rejected else 0


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


def _preview(args: argparse.Namespace, assumptions: AssumptionSet) -> int:
    loaded = _load(args, assumptions)
    mandate = _mandate_from(args)
    preview = preview_feasibility(
        loaded.arrays, mandate, assumptions, locked_ids=args.lock, excluded_ids=args.exclude
    )

    print(
        f"{preview.eligible_count} of {preview.total_count} candidates pass the screens\n"
        f"  eligible capacity   {preview.eligible_capacity_mw:,.0f} MW "
        f"(target {mandate.capacity_target_mw:,.0f} MW)\n"
        f"  equity to buy all   {millions(preview.eligible_equity)} "
        f"(available {millions(mandate.available_capital_eur)})\n"
        f"  pool solar share    {percentage(preview.eligible_solar_share)} "
        f"(target {percentage(mandate.solar_share)})\n"
        f"  pool gearing        {percentage(preview.eligible_gearing)} "
        f"(minimum {percentage(mandate.min_leverage)})"
    )

    if preview.signals:
        print("\nWarnings, most severe first:")
        for signal in preview.signals:
            blocking = " [blocks the run]" if signal.blocks_the_run else ""
            print(f"  {signal.code.severity:5s} {signal.code.name}{blocking}")
    else:
        print("\nNo warnings.")

    if preview.screens_to_widen:
        print("\nScreens rejecting candidates, worst first:")
        for name in preview.screens_to_widen:
            print(f"  {name:14s} rejects {preview.screens.drops[name]} on its own")

    print(f"\nRunnable: {'yes' if preview.runnable else 'no'}")
    return 0 if preview.runnable else 1


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def _blocked_run_message(blocking: list[FeasibilitySignal], preview: FeasibilityPreview) -> str:
    """Say what blocks the run, and what would unblock it (§13)."""
    lines = []
    for signal in blocking:
        if signal.code is WarningCode.NO_CANDIDATES:
            widen = ", ".join(preview.screens_to_widen) or "the mandate"
            lines.append(f"no candidate passes the screens; widen {widen}")
        elif signal.code is WarningCode.LOCKS_EXCEED_CAPITAL:
            lines.append(
                f"locked projects alone need {millions(signal.detail['lockedEquity'])} of "
                f"equity against {millions(signal.detail['availableCapital'])} available "
                f"({millions(signal.detail['excess'])} over); release a lock to run"
            )
    return "; ".join(lines)


def _search(
    loaded: LoadResult,
    mandate: MandateScalars,
    assumptions: AssumptionSet,
    args: argparse.Namespace,
    effort: Effort,
) -> tuple[RunResult, int, float]:
    """Screen, derive, search and aggregate. Returns the result, seed and elapsed ms."""
    arrays = loaded.arrays
    started = time.perf_counter()

    # §13 has two conditions that block a run, and `POST /optimisations` answers 422
    # on exactly these two. The preview decides both, and it carries the screening it
    # did — so this takes `preview.screens` rather than screening the pipeline a
    # second time, which recomputed every project's minimum DSCR for nothing.
    preview = preview_feasibility(
        arrays, mandate, assumptions, locked_ids=args.lock, excluded_ids=args.exclude
    )
    if not preview.runnable:
        blocking = [signal for signal in preview.signals if signal.blocks_the_run]
        raise MandateError(_blocked_run_message(blocking, preview))

    screens = preview.screens
    rows = np.flatnonzero(screens.eligible)

    returns = project_returns(arrays, assumptions, mandate.hold_years)
    features = build_features(
        arrays,
        equity_irr=np.nan_to_num(returns.equity_irr),
        irr_defined=returns.defined,
        merchant_share=1.0 - arrays.revenue.ppa_share,
    ).take(rows)

    # One definition of "locked", and the same one the screens used: an exclusion is
    # the more specific instruction, so a project that is both stays excluded. Two
    # definitions in one function is how the holdings end up flagged `locked` for a
    # project the user struck out.
    held = set(args.lock) - set(args.exclude)
    locked_all = np.array([project_id in held for project_id in arrays.ids], dtype=np.bool_)
    outcome = run_search(
        features,
        mandate,
        assumptions,
        SearchControls(effort=effort, locked=locked_all[rows], seed=args.seed),
    )
    result = build_result(
        arrays,
        features,
        mandate,
        assumptions,
        SelectionOutcome(
            eligible=screens.eligible,
            winner=outcome.selection,
            locked=locked_all,
            returns=returns,
            contracted_share=contracted_revenue_share(arrays),
        ),
    )
    elapsed = (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
    return result, outcome.seed, elapsed


def _print_tiles(result: RunResult, mandate: MandateScalars) -> None:
    """§7.1's twelve, in order, with each tile's sub-label."""
    totals = result.totals
    rows = [
        (
            "Installed capacity",
            f"{totals.capacity_mw:,.0f} MW",
            f"target {mandate.capacity_target_mw:,.0f} MW "
            f"({totals.capacity_deviation * 100:+.1f}%)",
        ),
        (
            "Projects",
            f"{totals.project_count}",
            f"{totals.solar_count} solar · {totals.wind_count} wind",
        ),
        (
            "Technology split",
            f"{percentage(totals.solar_share)} solar",
            f"target {percentage(mandate.solar_share)} "
            f"({totals.tech_split_deviation * 100:+.1f} points)",
        ),
        (
            "Equity required",
            millions(totals.equity),
            f"of {millions(mandate.available_capital_eur)} · "
            f"{percentage(totals.capital_deployed)} deployed",
        ),
        (
            "Total project cost",
            millions(totals.total_capex),
            f"{millions(totals.senior_debt)} senior debt",
        ),
        (
            f"Equity IRR ({result.hold_years}y)",
            percentage(totals.equity_irr),
            f"hurdle {percentage(mandate.target_irr)} · "
            + (f"{multiple(totals.moic)} MOIC" if totals.equity_irr is not None else "")
            + f" [{totals.return_compliance}]",
        ),
        (
            "Leverage",
            percentage(totals.gearing),
            f"min {percentage(mandate.min_leverage)} · DSCR floor "
            f"{multiple(totals.worst_min_dscr)} [{totals.leverage_compliance}]",
        ),
        (
            "Weighted LCOE",
            f"€{totals.weighted_lcoe:,.0f}",
            "per MWh, real",
        ),
        (
            "Annual generation",
            f"{totals.annual_generation_gwh:,.0f} GWh",
            f"{totals.co2_avoided_kt:,.0f} kt CO2 avoided p.a.",
        ),
        (
            "30-year FCFE",
            millions(totals.thirty_year_fcfe),
            "undiscounted, post debt",
        ),
        (
            "Merchant exposure",
            percentage(totals.merchant_share),
            f"cap {percentage(mandate.max_merchant_share)} [{totals.merchant_compliance}]",
        ),
        (
            "Largest country",
            f"{totals.largest_country_code or EM_DASH} {percentage(totals.largest_country_share)}",
            f"cap {percentage(mandate.max_country_share)} · risk score "
            f"{totals.weighted_risk_score:.2f} [{totals.country_compliance}]",
        ),
    ]
    width = max(len(label) for label, _, _ in rows)
    for label, value, sub in rows:
        print(f"  {label:<{width}}  {value:>16}   {sub}")


def _run(args: argparse.Namespace, assumptions: AssumptionSet) -> int:
    loaded = _load(args, assumptions)
    mandate = _mandate_from(args)
    result, seed, elapsed = _search(loaded, mandate, assumptions, args, Effort(args.effort))

    print(
        f"Run complete in {elapsed:,.0f} ms · seed {seed} · effort {args.effort} "
        f"· fitness {result.totals.fitness:.6f}"
    )
    print(f"Pipeline {loaded.pipeline_hash[:23]}… · assumptions {assumptions.assumption_set_id}")

    print(f"\nSelected {len(result.selected_ids)} projects:")
    print("  " + ", ".join(result.selected_ids))

    print("\nHeadline metrics:")
    _print_tiles(result, mandate)

    if result.terms is None:
        # An override decided the score, so the nine terms are not its explanation.
        print("\nObjective terms: not applicable — the score came from an override.")
    else:
        print("\nObjective terms:")
        for name, value in result.terms.items():
            print(f"  {name:30s} {value:+.6f}")

    print(
        f"\n30-year FCFE series sums to {millions(float(result.cashflow_30y.sum()))} "
        f"over {result.cashflow_30y.size} years (no terminal value)"
    )
    print(
        f"Hold series sums to {millions(float(result.cashflow_hold.sum()))} "
        f"over {result.cashflow_hold.size} years (terminal value included)"
    )
    return 0


# ---------------------------------------------------------------------------
# bench
# ---------------------------------------------------------------------------


def _bench(args: argparse.Namespace, assumptions: AssumptionSet) -> int:
    started = time.perf_counter()
    loaded = _load(args, assumptions)
    load_ms = (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
    print(
        f"Load: {loaded.loaded_count} files in {load_ms:,.0f} ms "
        f"({load_ms / max(loaded.loaded_count, 1):.1f} ms per file)"
    )

    mandate = _mandate_from(args)
    for effort in Effort:
        timings = []
        for _ in range(args.repeats):
            _, _, elapsed = _search(loaded, mandate, assumptions, args, effort)
            timings.append(elapsed)
        params = assumptions.ga.effort[effort]
        print(
            f"Search {effort.value:11s} {params.population:3d}x{params.generations:<4d} "
            f"best {min(timings):8,.0f} ms   median {sorted(timings)[len(timings) // 2]:8,.0f} ms"
        )
    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _default(field: str) -> float:
    """§5's own default for a control, from the one place that states them.

    ``domain/mandate_bounds.py`` already carries every control's range, step and
    default, quoted from the specification's tables. Writing them out again here would
    be a second source for twelve numbers — and would put a dozen calibration values
    in a source file, which is exactly what the literal guard exists to prevent.
    """
    return ALL_BOUNDS[field].default


def _add_mandate_arguments(parser: argparse.ArgumentParser) -> None:
    """§5's controls, at §5's defaults. Rates and shares are fractions (A-17)."""
    parser.add_argument(
        "--capital",
        type=float,
        default=_default("available_capital_m"),
        help="equity available, EURm",
    )
    parser.add_argument(
        "--target", type=float, default=_default("capacity_target_mw"), help="capacity target, MW"
    )
    parser.add_argument("--solar-share", type=float, default=_default("solar_share"))
    parser.add_argument(
        "--hurdle", type=float, default=_default("target_irr"), help="target equity IRR, a fraction"
    )
    parser.add_argument(
        "--hold", type=int, default=int(_default("hold_years")), help="hold period, years"
    )
    parser.add_argument("--min-leverage", type=float, default=_default("min_leverage"))
    parser.add_argument("--min-dscr", type=float, default=_default("min_dscr"))
    parser.add_argument("--max-merchant", type=float, default=_default("max_merchant_share"))
    parser.add_argument("--max-country", type=float, default=_default("max_country_share"))
    parser.add_argument("--max-project", type=float, default=_default("max_project_share"))
    parser.add_argument("--cod-from", type=int, default=int(_default("cod_from")))
    parser.add_argument("--cod-to", type=int, default=int(_default("cod_to")))
    parser.add_argument(
        "--countries",
        nargs="+",
        default=[
            "ES",
            "PT",
            "IT",
            "GR",
            "FR",
            "DE",
            "PL",
            "RO",
            "NL",
            "DK",
            "IE",
            "SE",
            "FI",
            "GB",
        ],
    )
    parser.add_argument(
        "--stages",
        nargs="+",
        default=[stage.value for stage in Stage],
        choices=[s.value for s in Stage],
    )
    parser.add_argument(
        "--risk", default=RiskAppetite.BALANCED.value, choices=[r.value for r in RiskAppetite]
    )
    parser.add_argument("--grid-only", action="store_true")
    parser.add_argument("--eur-only", action="store_true")
    parser.add_argument("--om-only", action="store_true")
    parser.add_argument("--lock", nargs="*", default=[], help="project ids to hold regardless")
    parser.add_argument("--exclude", nargs="*", default=[], help="project ids to strike out")


# ---------------------------------------------------------------------------
# The seed pipeline and the spreadsheet path (issue 2C)
# ---------------------------------------------------------------------------

MAX_REPORTED: Final = 30
"""How many problems to print before summarising. A wall of text is not a report."""

OUTLIER_SAMPLE: Final = 4  # structural: how many dispersion outliers to name
"""How many problems to print before summarising. A wall of text is not a report."""


def _unshippable(built: list[BuiltProject], assumptions: AssumptionSet) -> list[str]:
    """Every reason a generated pipeline should not be written, as readable lines.

    Checked **before** writing. Issue #8 asks for a pipeline that loads with zero
    tie-out failures and zero plausibility warnings, and that is only a property
    of what ships if the generator refuses to ship anything else. A-29 leaves the
    decision about an unplaceable site to the caller; this is the caller.
    """
    problems: list[str] = []
    band = assumptions.validation.min_dscr_band
    for project in built:
        if not project.within_band:
            problems.append(
                f"{project.site.id} {project.site.name}: min DSCR {project.min_dscr:.4f} is "
                f"outside the {band.low}-{band.high} plausibility band after "
                f"{project.attempts} attempt(s)"
            )
        for failure in tie_out_failures(
            ProjectFile.model_validate(as_file(project, assumptions)), assumptions
        ):
            problems.append(f"{project.site.id} {project.site.name}: {failure.message}")
    return problems


def _report(lines: list[str], headline: str) -> None:
    print(headline, file=sys.stderr)
    for line in lines[:MAX_REPORTED]:
        print(f"  {line}", file=sys.stderr)
    if len(lines) > MAX_REPORTED:
        print(f"  ... and {len(lines) - MAX_REPORTED} more", file=sys.stderr)


def _generate(args: argparse.Namespace, assumptions: AssumptionSet) -> int:
    if args.count <= 0:
        print(f"--count must be positive, got {args.count}", file=sys.stderr)
        return 2

    target = Path(args.pipeline)
    try:
        built = generate_pipeline(args.count, args.seed, assumptions)
    except PipelineExhaustedError as exhausted:
        # Not a bug: a calibration whose min-DSCR band no site can reach has no
        # pipeline to generate, and saying so beats writing a short one.
        _report(exhausted.skipped, f"refusing to write: {exhausted}")
        return 1

    problems = _unshippable(built, assumptions)
    if problems:
        _report(problems, f"refusing to write: {len(problems)} project(s) would not load cleanly")
        return 1

    try:
        outcome = write_pipeline(built, target, assumptions)
    except PipelineCollisionError as collision:
        print(str(collision), file=sys.stderr)
        return 1

    resampled = sum(1 for project in built if project.attempts > 1)
    print(
        f"wrote {len(outcome.written)} project files to {target}/ "
        f"(seed {args.seed}, assumption set {assumptions.assumption_set_id})"
    )
    if resampled:
        print(f"  {resampled} redrawn to bring min DSCR inside its plausibility band")
    if outcome.replaced:
        print(f"  {len(outcome.replaced)} previously generated file(s) removed")
    if outcome.kept:
        print(f"  {len(outcome.kept)} file(s) this generator did not write, left alone")
    return 0


def _ingest(args: argparse.Namespace, assumptions: AssumptionSet) -> int:
    workbook = Path(args.workbook)
    if not workbook.is_file():
        print(f"{workbook}: not a file", file=sys.stderr)
        return 2
    file = read_workbook(workbook)
    try:
        validated = ProjectFile.model_validate(file)
    except ValidationError as error:
        print(format_validation_error(str(workbook), error), file=sys.stderr)
        return 1

    # §7's tie-outs are **blocking**: "a file that fails any of these does not
    # load". `ProjectFile` checks shape, domain and the reject-derived rule; it
    # does not check that the statements agree with each other, and a workbook
    # can hold entirely plausible positive numbers that do not. This is 2A's
    # validator, the same one `pipeline validate` runs, so a workbook and a JSON
    # file are held to one set of identities rather than two.
    failures = tie_out_failures(validated, assumptions)
    if failures:
        _report(
            [failure.message for failure in failures],
            f"{workbook}: {len(failures)} tie-out failure(s); not ingested",
        )
        return 1

    target = Path(args.pipeline)
    target.mkdir(parents=True, exist_ok=True)
    written = target / f"{validated.id}.json"
    written.write_text(json.dumps(file, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{workbook} -> {written}")

    # The house model's second job (epic §2), and the one advisory check here. A
    # tie-out failure means the file disagrees with itself; a variance means this
    # model would have computed something else, which the analyst may overrule
    # (A-7, Q-4).
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


def _export(args: argparse.Namespace, assumptions: AssumptionSet) -> int:
    if not args.xlsx:
        print("--xlsx is the only export format; pass it explicitly", file=sys.stderr)
        return 2
    source = Path(args.pipeline)
    matches = [
        path
        for path in sorted(source.glob("*.json"))
        if json.loads(path.read_text(encoding="utf-8")).get("id") == args.id
    ]
    if not matches:
        print(f"{args.id}: no project with that id in {source}/", file=sys.stderr)
        return 1
    destination = Path(args.out) if args.out else Path(f"{args.id}.xlsx")
    if destination.is_dir():
        destination = destination / f"{args.id}.xlsx"
    written = write_workbook(json.loads(matches[0].read_text(encoding="utf-8")), destination)
    print(f"{matches[0]} -> {written}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="terrafolio", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--pipeline",
        default="pipeline",
        help="directory of project files (default: ./pipeline)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    pipeline = commands.add_parser("pipeline", help="inspect the candidate pipeline")
    pipeline_commands = pipeline.add_subparsers(dest="pipeline_command", required=True)
    pipeline_commands.add_parser("validate", help="tie-outs, plausibility and dispersion")

    generate = pipeline_commands.add_parser("generate", help="write a seed pipeline")
    generate.add_argument("--count", type=int, default=GENERATE_COUNT, help="how many projects")
    generate.add_argument("--seed", type=int, default=GENERATE_SEED, help="site-pool seed")

    ingest = pipeline_commands.add_parser("ingest", help="read an analyst workbook into JSON")
    ingest.add_argument("workbook", help="an .xlsx laid out as the template is")

    export = pipeline_commands.add_parser("export", help="write a project out as a workbook")
    export.add_argument("--id", required=True, help="the project to export")
    export.add_argument("--out", default=None, help="output path or directory")
    export.add_argument("--xlsx", action="store_true", help="write a workbook")

    preview = commands.add_parser("preview", help="the §5.4 feasibility footer")
    _add_mandate_arguments(preview)

    run = commands.add_parser("run", help="search for a portfolio")
    _add_mandate_arguments(run)
    run.add_argument("--effort", default=Effort.STANDARD.value, choices=[e.value for e in Effort])
    run.add_argument("--seed", type=int, default=None, help="omit to have one drawn and reported")

    bench = commands.add_parser("bench", help="time the load and the search")
    _add_mandate_arguments(bench)
    bench.add_argument("--repeats", type=int, default=BENCH_REPEATS)
    bench.add_argument("--seed", type=int, default=BENCH_SEED)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``terrafolio`` console script."""
    args = build_parser().parse_args(argv)
    assumptions = load_default()
    pipeline_handlers = {
        "validate": _validate,
        "generate": _generate,
        "ingest": _ingest,
        "export": _export,
    }
    handlers = {
        "pipeline": lambda a, s: pipeline_handlers[a.pipeline_command](a, s),
        "preview": _preview,
        "run": _run,
        "bench": _bench,
    }
    try:
        return handlers[args.command](args, assumptions)
    except (PipelineLoadError, MandateError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
