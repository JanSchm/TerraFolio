"""Where the pipeline's projects are, and what they are.

A *site* is a project's identity and physical description: where it sits, what
technology it is, how big, what stage, and when it commissions. Everything else
about a project — its capacity factor, opex, contract terms, capex and debt — is
drawn or derived by :mod:`terrafolio.generate.pipeline`.

The split matters. Site identity is **data**: place names and coordinates are
geography, not calibration, so they live in ``countries.json`` rather than in the
assumption set, which epic §5 reserves for rates, weights, floors, clamps,
tolerances and bands. The *composition* of a pipeline — the technology and stage
mix, the capacity bands, how far past the first COD year each stage commissions —
is calibration and lives in ``[generator]``.

Two pools, for two different jobs:

:func:`reference_sites`
    The 48 sites the JavaScript reference hard-codes, extracted by running its
    own ``buildPipeline()``. Used only by the parity mode, which reproduces
    ``tests/golden/fixtures/`` field for field. It keeps the reference's ``UK``
    country code, which ``docs/pipeline-schema.md`` §4.1 accepts as an alias
    of ``GB``.

:func:`build_pool`
    A pipeline of any size, drawn to the configured mix across the 14 markets.
    This is what ships. It uses the canonical ``GB`` throughout: two spellings of
    one country in one pipeline would split it in the per-country concentration
    cap, which is a wrong answer rather than an untidy one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

import numpy as np

from terrafolio.config.assumptions import AssumptionSet, Range
from terrafolio.domain.enums import Currency, Stage, Technology

__all__ = ["Market", "Site", "build_pool", "markets", "reference_sites"]

_DATA: Final = Path(__file__).resolve().parent
_COUNTRIES: Final = _DATA / "countries.json"
_REFERENCE: Final = _DATA / "reference_sites.json"

# How precisely a drawn site is quoted: coordinates to a site centroid, which
# §8 says is sufficient for the map, and nameplate to a tenth of a megawatt.
# Neither is a tolerance or a band -- they are the precision the numbers are
# written at, and rounding them keeps a generated pipeline readable next to the
# reference corpus, whose capacities are whole megawatts.
_COORDINATE_PLACES, _CAPACITY_PLACES = 2, 1  # structural: quoted precision, not calibration


@dataclass(frozen=True, slots=True, kw_only=True)
class Market:
    """One country the pipeline can hold projects in."""

    code: str
    name: str
    iso3: str
    currency: Currency
    lat_range: Range
    lon_range: Range
    offshore_capable: bool
    regions: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class Site:
    """A project's identity and physical description, before any economics."""

    id: str
    name: str
    country_code: str
    country: str
    iso3: str
    lat: float
    lon: float
    technology: Technology
    stage: Stage
    capacity_mw: float
    cod_year: int
    currency: Currency


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _box(bounds: list[float]) -> Range:
    """A country's bounding box, as a range to place a site inside."""
    low, high = bounds
    return Range(low=low, span=high - low)


@lru_cache(maxsize=1)
def markets() -> tuple[Market, ...]:
    """The 14 markets, in the order ``countries.json`` declares them."""
    return tuple(
        Market(
            code=entry["code"],
            name=entry["name"],
            iso3=entry["iso3"],
            currency=Currency(entry["currency"]),
            lat_range=_box(entry["latRange"]),
            lon_range=_box(entry["lonRange"]),
            offshore_capable=entry["offshoreCapable"],
            regions=tuple(entry["regions"]),
        )
        for entry in _load(_COUNTRIES)["countries"]
    )


@lru_cache(maxsize=1)
def reference_sites() -> tuple[Site, ...]:
    """The reference's 48 sites, for the parity mode."""
    return tuple(
        Site(
            id=entry["id"],
            name=entry["name"],
            country_code=entry["countryCode"],
            country=entry["country"],
            iso3=entry["iso3"],
            lat=entry["lat"],
            lon=entry["lon"],
            technology=Technology(entry["technology"]),
            stage=Stage(entry["stage"]),
            capacity_mw=float(entry["capacityMw"]),
            cod_year=entry["codYear"],
            currency=Currency(entry["currency"]),
        )
        for entry in _load(_REFERENCE)["sites"]
    )


def _between(draw: float, span: Range) -> float:
    """Place a draw inside a range: ``low + u x span``, the span stated not derived."""
    return span.low + draw * span.span


def _weighted_choice[T](draw: float, weights: dict[T, float]) -> T:
    """Pick a key by cumulative share. Deterministic given ``draw``.

    Falls through to the last key, which is what absorbs a mix that does not
    sum to exactly one — a rounding artefact rather than an error worth
    refusing a pipeline over.
    """
    running = 0.0
    items = list(weights.items())
    for key, weight in items[:-1]:
        running += weight
        if draw < running:
            return key
    return items[-1][0]


def _identifier(index: int, total: int) -> str:
    """``P001`` — zero-padded to a uniform width across the pipeline (C-5).

    Canonical order is a plain lexicographic sort of ids, so a pipeline mixing
    ``P9`` with ``P10`` orders them in a way no one intends. The width comes
    from the pipeline's own size, so a 300-project run pads to three and a
    2,000-project run to four.
    """
    return f"P{index + 1:0{len(str(total))}d}"


_PHASE_NUMERALS: Final = ("", " II", " III", " IV", " V", " VI", " VII", " VIII", " IX", " X")


def _site_name(region: str, technology: Technology, phase: int) -> str:
    """``Aragon Solar``, then ``Aragon Solar II`` when the region comes round again.

    Phases are how the industry actually names repeat build-out on one site, and
    they are what lets a market hold more projects than it has named regions —
    which epic §12's 2,000-candidate target requires, since 14 markets x 24
    regions is 336.
    """
    suffix = {
        Technology.SOLAR: "Solar",
        Technology.ONSHORE_WIND: "Wind",
        Technology.OFFSHORE_WIND: "Offshore",
    }[technology]
    if phase < len(_PHASE_NUMERALS):
        return f"{region} {suffix}{_PHASE_NUMERALS[phase]}"
    return f"{region} {suffix} Phase {phase + 1}"


def build_pool(count: int, seed: int, assumptions: AssumptionSet) -> tuple[Site, ...]:
    """Draw ``count`` sites across the 14 markets, deterministically from ``seed``.

    The stream here is **separate from each project's financial stream**, and
    deliberately so. Site identity is a property of the pipeline as a whole and
    is drawn once, in order; a project's economics are keyed on its own id and
    the assumption set, so adding a project to a pipeline directory reprices
    nothing. Mixing the two would reintroduce exactly the index dependence that
    keying on the id exists to remove.

    **This draws from numpy, not from the ported xorshift32.** The port exists to
    reproduce the reference corpus and is used only where parity demands it. Here
    it was measurably the wrong tool: seven draws per site means every technology
    choice reads a stride-7 subsequence of one xorshift32 stream, and that
    subsequence is not uniform — the measured mix came out 49.7 / 38.7 / 11.7
    against a target of 45 / 44 / 11, a five-point bias that more samples did not
    wash out. numpy's PCG64 has no such structure, and the pipeline's composition
    is not something the reference has an opinion about.

    Markets are visited round-robin rather than drawn, so every one of the 14 is
    represented at any size worth generating and the pipeline spans the mandate's
    country chips. Everything else — technology, stage, capacity, COD, the site
    itself and where in the country it sits — is drawn.
    """
    if count <= 0:
        raise ValueError(f"count must be positive, got {count}")
    generator = assumptions.generator
    pool = markets()
    technology_mix = dict(generator.technology_mix)
    stage_mix = dict(generator.stage_mix)

    rng = np.random.default_rng(np.random.SeedSequence(seed))
    phases: dict[tuple[str, str, Technology], int] = {}
    sites: list[Site] = []

    for index in range(count):
        market = pool[index % len(pool)]
        technology = _weighted_choice(float(rng.random()), technology_mix)
        if technology is Technology.OFFSHORE_WIND and not market.offshore_capable:
            technology = Technology.ONSHORE_WIND
        stage = _weighted_choice(float(rng.random()), stage_mix)

        region = market.regions[int(rng.integers(len(market.regions)))]
        key = (market.code, region, technology)
        phase = phases.get(key, 0)
        phases[key] = phase + 1

        capacity = _between(float(rng.random()), generator.capacity_mw[technology])
        offset = int(_between(float(rng.random()), generator.cod_offset[stage]))
        cod = min(generator.cod_first_year + offset, generator.cod_last_year)
        lat = _between(float(rng.random()), market.lat_range)
        lon = _between(float(rng.random()), market.lon_range)

        sites.append(
            Site(
                id=_identifier(index, count),
                name=_site_name(region, technology, phase),
                country_code=market.code,
                country=market.name,
                iso3=market.iso3,
                lat=round(lat, _COORDINATE_PLACES),
                lon=round(lon, _COORDINATE_PLACES),
                technology=technology,
                stage=stage,
                capacity_mw=round(capacity, _CAPACITY_PLACES),
                cod_year=cod,
                currency=market.currency,
            )
        )
    return tuple(sites)
