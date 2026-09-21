"""Assemble project files, and write a pipeline directory.

The last step: take a site, draw its economics, run the house model over it, and
serialise the result as a file that ``docs/pipeline-schema.md`` describes and
1A's :class:`~terrafolio.domain.project_file.ProjectFile` accepts.

Two entry points, for the two things a generator is for:

:func:`reference_pipeline`
    The 48 projects the JavaScript reference builds, seeded as it seeds them.
    This is the parity target: field for field against
    ``tests/golden/fixtures/pipeline/``.

:func:`generate_pipeline`
    A pipeline of any size. Each project's economics are keyed on its own id and
    the assumption set, so adding a file to a pipeline directory reprices
    nothing else in it, and a project whose min DSCR falls outside the §10
    plausibility band is redrawn from its own stream rather than shipped with a
    warning attached.

Nothing mandate-dependent is emitted, and nothing derived that a file can do
without: §9's reject list is enforced by the schema, and the emitted shape is
checked against it on every file this module writes.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.domain.conventions import YEARS, ramp_index
from terrafolio.domain.enums import Confidence, EstimateBasis, Stage
from terrafolio.generate.draws import project_stream, reference_stream
from terrafolio.generate.project import DrawnProject, draw_project
from terrafolio.generate.sites import Site, build_pool, reference_sites
from terrafolio.model.project import ProjectInputs, ProjectStatements, min_dscr, project_statements

__all__ = [
    "BuiltProject",
    "build_project",
    "generate_pipeline",
    "reference_pipeline",
    "write_pipeline",
]

SCHEMA_VERSION: Final = "1.0"
PREPARED_BY: Final = "terrafolio pipeline generate"
MODEL_VERSION: Final = "house-model@1"
_JSON_INDENT: Final = 2  # structural: file formatting, matching the golden corpus

PREPARED_ON: Final = "2026-09-21"
"""Fixed, never ``date.today()``: a generated pipeline must be byte-identical
on every machine and in every CI image, and a date that moves would make the
snapshot hash move with it."""


@dataclass(frozen=True, slots=True, kw_only=True)
class BuiltProject:
    """A drawn project, its statements, and the figures a caller wants to check."""

    drawn: DrawnProject
    statements: ProjectStatements
    min_dscr: float
    attempts: int

    @property
    def site(self) -> Site:
        return self.drawn.site


def _inputs(drawn: DrawnProject, assumptions: AssumptionSet) -> ProjectInputs:
    generator = assumptions.generator
    site = drawn.site
    return ProjectInputs(
        capacity_mw=site.capacity_mw,
        cod_year=site.cod_year,
        base_year=generator.base_year,
        net_capacity_factor=drawn.net_capacity_factor,
        opex_per_kw_year=drawn.opex_per_kw_year,
        ppa_share=drawn.ppa_share,
        ppa_price=drawn.ppa_price,
        ppa_tenor_years=drawn.ppa_tenor_years,
        capture_price=drawn.capture_price,
        total_capex=drawn.total_capex,
        senior_debt=drawn.senior_debt,
        tax_rate=generator.tax_rate,
        depreciation_years=generator.depreciation_years,
        debt_rate=generator.debt_rate,
        debt_tenor_years=generator.debt_tenor_years,
        degradation_rate=generator.degradation[site.technology],
        price_escalation=generator.price_escalation,
        merchant_escalation=generator.merchant_escalation,
        opex_escalation=generator.opex_escalation,
        ramp_factor=generator.ramp_factor,
        hours_per_year_gwh=generator.hours_per_year_gwh,
    )


def build_project(
    site: Site, assumptions: AssumptionSet, *, index: int | None = None
) -> BuiltProject:
    """Draw, price and model one project.

    ``index`` selects the reference's index-keyed stream and is used only by the
    parity path. Without it the project is keyed on ``(id, assumption set id)``
    and, if its min DSCR lands outside the plausibility band, redrawn — up to
    ``generator.dscr_resample_attempts`` times, each attempt a different stream
    that is still a pure function of the project's own identity.

    A project that cannot be brought inside the band is returned anyway, with
    its attempt count, rather than raising: the caller decides whether an
    unplaceable site is a warning or a failure, and silently dropping one would
    make ``--count`` a suggestion.
    """
    band = assumptions.validation.min_dscr_band
    attempts = assumptions.generator.dscr_resample_attempts if index is None else 1

    built: BuiltProject | None = None
    for attempt in range(attempts):
        stream = (
            reference_stream(index)
            if index is not None
            else project_stream(site.id, assumptions.assumption_set_id, attempt)
        )
        drawn = draw_project(site, stream, assumptions)
        statements = project_statements(_inputs(drawn, assumptions))
        cover = min_dscr(
            statements.dscr, ramp_index(site.cod_year, assumptions.generator.base_year)
        )
        built = BuiltProject(
            drawn=drawn, statements=statements, min_dscr=cover, attempts=attempt + 1
        )
        if not np.isfinite(cover) or band.low <= cover <= band.high:
            return built
    assert built is not None
    return built


def _provenance(drawn: DrawnProject) -> dict[str, Any]:
    """How firm each group of numbers is, by stage (§8).

    Issue #8 asks for greenfield ``analyst_estimate``/low, ready-to-build
    ``budget_quote``/medium and construction ``signed_contract``/high. Those
    names are not in §8.1's closed vocabulary, so they are mapped onto the
    values that mean the same thing: a house-model estimate is
    ``internal_model``, a budget quote is a ``binding_offer``, and a signed
    contract is ``contracted``. Recorded in ``docs/decisions.md``.

    Grid and O&M carry what the project actually has rather than what its stage
    suggests — a greenfield project with a firm connection agreement has a
    contract, and saying otherwise would make the provenance block disagree with
    the field it describes.
    """
    stage = drawn.site.stage
    basis, confidence = {
        Stage.GREENFIELD: (EstimateBasis.INTERNAL_MODEL, Confidence.LOW),
        Stage.READY_TO_BUILD: (EstimateBasis.BINDING_OFFER, Confidence.MEDIUM),
        Stage.CONSTRUCTION: (EstimateBasis.CONTRACTED, Confidence.HIGH),
    }[stage]

    def entry(value: EstimateBasis, level: Confidence, note: str) -> dict[str, str]:
        return {"estimateBasis": value.value, "confidence": level.value, "note": note}

    contracted_note = (
        f"{drawn.ppa_share:.0%} of revenue under a "
        f"{drawn.ppa_tenor_years}-year PPA; the merchant tail is the house model's capture "
        f"price against the market baseload curve."
        if drawn.ppa_tenor_years
        else "Fully merchant; priced against the market baseload curve."
    )
    return {
        "preparedBy": PREPARED_BY,
        "preparedOn": PREPARED_ON,
        "modelVersion": MODEL_VERSION,
        "fields": {
            "generation": entry(
                EstimateBasis.ENGINEERING_ESTIMATE
                if stage is not Stage.GREENFIELD
                else EstimateBasis.BENCHMARK,
                confidence,
                "P50 net capacity factor from the market resource curve, adjusted per project.",
            ),
            "price": entry(
                EstimateBasis.CONTRACTED if drawn.ppa_tenor_years else EstimateBasis.INTERNAL_MODEL,
                Confidence.HIGH if drawn.ppa_tenor_years else Confidence.MEDIUM,
                contracted_note,
            ),
            "capex": entry(
                basis,
                confidence,
                "Entry pricing at the stage EBITDA yield, clamped to the technology cost band.",
            ),
            "opex": entry(
                EstimateBasis.BENCHMARK,
                Confidence.MEDIUM,
                "Technology opex benchmark in base-year euros, escalated annually.",
            ),
            "debtTerms": entry(
                basis,
                confidence,
                "Sized to the target base-case DSCR on stabilised first-full-year EBITDA, "
                "then capped by the stage gearing ceiling.",
            ),
            "grid": entry(
                EstimateBasis.CONTRACTED if drawn.grid_secured else EstimateBasis.PLACEHOLDER,
                Confidence.HIGH if drawn.grid_secured else Confidence.LOW,
                "Firm connection agreement in place."
                if drawn.grid_secured
                else "No firm connection agreement yet; grid works at an indicative cost.",
            ),
            "om": entry(
                EstimateBasis.CONTRACTED if drawn.om_contracted else EstimateBasis.BENCHMARK,
                Confidence.HIGH if drawn.om_contracted else Confidence.LOW,
                "Long-term full-scope service agreement signed."
                if drawn.om_contracted
                else "No service agreement contracted; opex carried at benchmark.",
            ),
        },
    }


def _series(values: Any) -> list[float]:
    return [float(value) for value in values]


def as_file(built: BuiltProject, assumptions: AssumptionSet) -> dict[str, Any]:
    """Serialise one project into the shape ``docs/pipeline-schema.md`` §3 fixes."""
    drawn, s = built.drawn, built.statements
    site = drawn.site
    generator = assumptions.generator
    base_year = generator.base_year
    return {
        "schemaVersion": SCHEMA_VERSION,
        "id": site.id,
        "name": site.name,
        "location": {
            "country": site.country,
            "countryCode": site.country_code,
            "iso3": site.iso3,
            "lat": site.lat,
            "lon": site.lon,
        },
        "asset": {
            "technology": site.technology.value,
            "stage": site.stage.value,
            "capacityMw": site.capacity_mw,
            "codYear": site.cod_year,
            "netCapacityFactor": drawn.net_capacity_factor,
            "opexPerKwYear": drawn.opex_per_kw_year,
        },
        "revenue": {
            "ppaShare": drawn.ppa_share,
            "ppaPrice": drawn.ppa_price,
            "ppaTenorYears": drawn.ppa_tenor_years,
            "countryBaseloadPrice": drawn.baseload_price,
            "captureFactor": drawn.capture_factor_effective,
        },
        "execution": {
            "developmentRiskScore": drawn.development_risk_score,
            "gridSecured": drawn.grid_secured,
            "omContracted": drawn.om_contracted,
            "currency": site.currency.value,
        },
        "capitalStructure": {
            "totalCapex": drawn.total_capex,
            "seniorDebt": drawn.senior_debt,
            "maxGearing": drawn.max_gearing,
        },
        "assumptions": {
            "baseYear": base_year,
            "taxRate": generator.tax_rate,
            "depreciationYears": generator.depreciation_years,
            "debtRate": generator.debt_rate,
            "debtTenorYears": generator.debt_tenor_years,
            "degradationRate": generator.degradation[site.technology],
            "priceEscalation": generator.price_escalation,
            "merchantEscalation": generator.merchant_escalation,
            "opexEscalation": generator.opex_escalation,
            "targetDscr": generator.target_dscr,
        },
        "statements": {
            "years": list(range(base_year, base_year + YEARS)),
            "physicals": {
                "generationGwh": _series(s.generation_gwh),
                "achievedPrice": _series(s.achieved_price),
            },
            "incomeStatement": {
                "revenue": _series(s.revenue),
                "opex": _series(s.opex),
                "ebitda": _series(s.ebitda),
                "depreciation": _series(s.depreciation),
                "ebit": _series(s.ebit),
                "interestExpense": _series(s.interest_expense),
                "pbt": _series(s.pbt),
                "taxExpense": _series(s.tax_expense),
                "netIncome": _series(s.net_income),
            },
            "cashFlow": {
                "interestPaid": _series(s.interest_expense),
                "debtRepayment": _series(s.debt_repayment),
                "taxPaid": _series(s.tax_expense),
                "capex": _series(s.capex),
                "debtDrawdown": _series(s.debt_drawdown),
                "equityDrawdown": _series(s.equity_drawdown),
                "fcfe": _series(s.fcfe),
            },
            "debtSchedule": {
                "opening": _series(s.debt_opening),
                "drawdown": _series(s.debt_drawdown),
                "repayment": _series(s.debt_repayment),
                "closing": _series(s.debt_closing),
            },
            "balanceSheet": {"ppe": _series(s.ppe)},
            "ratios": {"dscr": [None if np.isnan(value) else float(value) for value in s.dscr]},
        },
        "provenance": _provenance(drawn),
    }


def reference_pipeline(assumptions: AssumptionSet) -> list[BuiltProject]:
    """The reference's 48 projects, seeded as it seeds them. The parity target."""
    return [
        build_project(site, assumptions, index=index)
        for index, site in enumerate(reference_sites())
    ]


def generate_pipeline(count: int, seed: int, assumptions: AssumptionSet) -> list[BuiltProject]:
    """A pipeline of ``count`` projects, drawn deterministically from ``seed``."""
    return [build_project(site, assumptions) for site in build_pool(count, seed, assumptions)]


def _slug(name: str) -> str:
    keep = [character.lower() if character.isalnum() else "-" for character in name]
    return "".join(keep).strip("-").replace("--", "-")


def write_pipeline(
    projects: list[BuiltProject], directory: Path, assumptions: AssumptionSet
) -> Iterator[Path]:
    """Write one file per project, named ``<id>-<slug>.json``.

    The name is for a human reading a directory listing; ``id`` is what
    identifies a project, and the loader does not look at file names (§1).
    """
    directory.mkdir(parents=True, exist_ok=True)
    for built in projects:
        path = directory / f"{built.site.id}-{_slug(built.site.name)}.json"
        payload = json.dumps(as_file(built, assumptions), indent=_JSON_INDENT, ensure_ascii=False)
        path.write_text(payload + "\n", encoding="utf-8")
        yield path
