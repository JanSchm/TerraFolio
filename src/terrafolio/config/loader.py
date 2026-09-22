"""Reading an assumption set from TOML.

Hand-written rather than delegated to a validation library, for the reason in
:mod:`terrafolio.config.assumptions`: nothing on the ``config`` import path may
touch pydantic. The parsing is paid for once and repaid in error quality — this
file is edited by hand by people who are not reading a stack trace.
"""

from __future__ import annotations

import math
import os
import re
import tomllib
from collections.abc import Callable, Mapping
from enum import StrEnum
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from terrafolio.config.assumptions import (
    AssumptionSet,
    Band,
    EffortParams,
    FeasibilityThresholds,
    GaParams,
    GeneratorParams,
    Interpretation,
    IrrBracket,
    Metadata,
    ObjectiveWeights,
    Range,
    RiskCaps,
    ValidationParams,
)
from terrafolio.config.hashing import (
    ASSUMPTION_SET_ID_LENGTH,
    canonical_json,
    content_hash,
    numbers_as_floats,
    sha256_hex,
)
from terrafolio.domain.enums import Effort, RiskAppetite, Stage, Technology

__all__ = [
    "DEFAULT_ASSUMPTION_SET",
    "ENV_ASSUMPTIONS_DIR",
    "MARKET_CAPACITY_FACTOR_TECHNOLOGIES",
    "AssumptionError",
    "assumptions_dir",
    "load_assumption_set",
    "load_default",
]

ENV_ASSUMPTIONS_DIR: Final = "TERRAFOLIO_ASSUMPTIONS_DIR"
DEFAULT_ASSUMPTION_SET: Final = "default-2026"
_METADATA_SECTION: Final = "meta"
_PACKAGED_DIR: Final = "_assumptions"
_MARKET_CODE: Final = re.compile(r"[A-Z]{2}")

MARKET_CAPACITY_FACTOR_TECHNOLOGIES: Final[frozenset[Technology]] = frozenset(
    {Technology.SOLAR, Technology.ONSHORE_WIND}
)
"""Technologies whose capacity factor varies by market.

Offshore wind is drawn from a single band rather than a market table (§9.2),
so it is permitted here but not required — while a missing solar or onshore
table is named at load rather than surfacing as a KeyError mid-generation.
"""


class AssumptionError(ValueError):
    """A calibration file is missing a key, or a key holds the wrong kind of value."""


# --------------------------------------------------------------------------
# Typed accessors
# --------------------------------------------------------------------------
#
# tomllib hands back dict[str, Any], and `strict` mypy rightly objects to every
# bare `return table[key]`. These absorb that once, and produce a keypath the
# person editing the file can act on.


def _at(where: str, key: str) -> str:
    return f"{where}.{key}" if where else key


def _table(parent: Mapping[str, Any], key: str, where: str = "") -> Mapping[str, Any]:
    path = _at(where, key)
    value = parent.get(key)
    if value is None:
        raise AssumptionError(f"{path}: missing section")
    if not isinstance(value, Mapping):
        raise AssumptionError(f"{path}: expected a section, got {type(value).__name__}")
    return value


def _float(table: Mapping[str, Any], key: str, where: str = "") -> float:
    path = _at(where, key)
    if key not in table:
        raise AssumptionError(f"{path}: missing key")
    value = table[key]
    # bool subclasses int, and a flag read as 1.0 is a silent wrong number.
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise AssumptionError(f"{path}: expected a number, got {type(value).__name__}")
    # TOML has nan and inf literals. Neither is a calibration value: a
    # non-finite weight propagates silently through every fitness comparison,
    # and it also makes the assumption-set digest fail far from here, inside
    # json.dumps, with a message naming neither the key nor the file.
    if not math.isfinite(value):
        raise AssumptionError(f"{path}: expected a finite number, got {value}")
    return float(value)


def _int(table: Mapping[str, Any], key: str, where: str = "") -> int:
    path = _at(where, key)
    if key not in table:
        raise AssumptionError(f"{path}: missing key")
    value = table[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise AssumptionError(f"{path}: expected an integer, got {type(value).__name__}")
    return value


def _str(table: Mapping[str, Any], key: str, where: str = "") -> str:
    path = _at(where, key)
    if key not in table:
        raise AssumptionError(f"{path}: missing key")
    value = table[key]
    if not isinstance(value, str):
        raise AssumptionError(f"{path}: expected a string, got {type(value).__name__}")
    return value


def _bool(table: Mapping[str, Any], key: str, where: str = "") -> bool:
    path = _at(where, key)
    if key not in table:
        raise AssumptionError(f"{path}: missing key")
    value = table[key]
    if not isinstance(value, bool):
        raise AssumptionError(f"{path}: expected true or false, got {type(value).__name__}")
    return value


def _int_tuple(table: Mapping[str, Any], key: str, where: str = "") -> tuple[int, ...]:
    path = _at(where, key)
    if key not in table:
        raise AssumptionError(f"{path}: missing key")
    value = table[key]
    if not isinstance(value, list) or not all(
        isinstance(item, int) and not isinstance(item, bool) for item in value
    ):
        raise AssumptionError(f"{path}: expected a list of integers")
    return tuple(int(item) for item in value)


def _band(parent: Mapping[str, Any], key: str, where: str = "") -> Band:
    table = _table(parent, key, where)
    path = _at(where, key)
    band = Band(low=_float(table, "low", path), high=_float(table, "high", path))
    if band.low > band.high:
        raise AssumptionError(f"{path}: low ({band.low}) is above high ({band.high})")
    return band


def _range(parent: Mapping[str, Any], key: str, where: str = "") -> Range:
    """A draw range, stated as ``low`` and ``span``.

    A negative span would invert the range and silently draw below ``low``,
    which is the same class of mistake ``_band`` refuses for ``low > high``.
    """
    table = _table(parent, key, where)
    location = f"{where}.{key}" if where else key
    span = _float(table, "span", location)
    if span < 0:
        raise AssumptionError(f"{location}: span is negative ({span})")
    return Range(low=_float(table, "low", location), span=span)


def _market_keyed(table: Mapping[str, Any], where: str) -> Mapping[str, float]:
    """Read a table keyed by ISO 3166-1 alpha-2 market code.

    The key shape is checked because a market table is looked up by a project's
    own ``countryCode``: a typo such as ``Es`` or ``SPAIN`` would sit here
    unused and surface much later as a missing market for a project that looks
    perfectly valid.
    """
    if not table:
        raise AssumptionError(
            f"{where}: no markets; every project is looked up by its own "
            f"countryCode, so an empty table fails only once a run reaches one"
        )
    for code in table:
        if not _MARKET_CODE.fullmatch(code):
            raise AssumptionError(f"{where}.{code}: expected an ISO 3166-1 alpha-2 market code")
    return MappingProxyType({code: _float(table, code, where) for code in sorted(table)})


def _keyed_by[E: StrEnum, V](
    table: Mapping[str, Any],
    members: type[E],
    read: Callable[[Mapping[str, Any], str, str], V],
    where: str,
    *,
    ignore: frozenset[str] = frozenset(),
) -> Mapping[E, V]:
    """Read a table keyed by an enumeration, requiring every member exactly once.

    Both halves matter: a missing member would fail later as a ``KeyError`` deep
    in a reduction, and an unknown key is almost always a typo that would
    otherwise be silently ignored.
    """
    unknown = set(table) - {member.value for member in members} - ignore
    if unknown:
        raise AssumptionError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
    return MappingProxyType({member: read(table, member.value, where) for member in members})


def _keyed_by_some[E: StrEnum, V](
    table: Mapping[str, Any],
    members: type[E],
    read: Callable[[Mapping[str, Any], str, str], V],
    where: str,
    *,
    required: frozenset[E],
) -> Mapping[E, V]:
    """Read a table keyed by an enumeration where only some members apply.

    Still rejects unknown keys, and still names a missing required member at
    load time. Skipping whatever is absent instead would return a seemingly
    valid assumption set and fail as a ``KeyError`` deep in a generation run, at
    which point nothing points back at the file that caused it.
    """
    known = {member.value: member for member in members}
    unknown = set(table) - set(known)
    if unknown:
        raise AssumptionError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
    missing = sorted(member.value for member in required if member.value not in table)
    if missing:
        raise AssumptionError(f"{where}: missing key(s) {', '.join(missing)}")
    return MappingProxyType({known[key]: read(table, key, where) for key in sorted(table)})


# --------------------------------------------------------------------------
# Locating a file
# --------------------------------------------------------------------------


def _search_upward(start: Path) -> Path | None:
    for directory in (start, *start.parents):
        candidate = directory / "assumptions"
        if candidate.is_dir() and any(candidate.glob("*.toml")):
            return candidate
    return None


def assumptions_dir() -> Path:
    """Where calibration files live.

    The directory sits at the repository root rather than inside the package
    because it is meant to be edited and reviewed in git — changing a weight is
    an auditable event (§10.2). It is copied into the wheel at build time so an
    installed application still finds it, which keeps one source of truth
    instead of two that drift.

    Resolution order: the environment override, then upward from the working
    directory, then upward from the installed package, then the packaged copy.
    """
    override = os.environ.get(ENV_ASSUMPTIONS_DIR)
    if override:
        path = Path(override)
        if not path.is_dir():
            raise AssumptionError(
                f"{ENV_ASSUMPTIONS_DIR} points at {path}, which is not a directory"
            )
        return path

    for start in (Path.cwd(), Path(__file__).resolve().parent):
        found = _search_upward(start)
        if found is not None:
            return found

    packaged = Path(str(resources.files("terrafolio"))) / _PACKAGED_DIR
    if packaged.is_dir():
        return packaged

    raise AssumptionError(
        "no assumptions directory found; set "
        f"{ENV_ASSUMPTIONS_DIR} or run from inside the repository"
    )


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def _reject_non_finite(node: Any, path: tuple[str, ...] = ()) -> None:
    """Fail on a nan or inf anywhere in the file, naming where it is.

    Runs before the digest, because that is what sees a non-finite value first:
    ``json.dumps`` refuses it with "Out of range float values are not JSON
    compliant", which names neither the key nor the file and arrives before any
    typed accessor has had a chance to produce a better message.
    """
    if isinstance(node, bool):
        return
    if isinstance(node, float) and not math.isfinite(node):
        where = ".".join(path) or "<root>"
        raise AssumptionError(f"{where}: expected a finite number, got {node}")
    if isinstance(node, Mapping):
        for key, value in node.items():
            _reject_non_finite(value, (*path, str(key)))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _reject_non_finite(item, (*path, str(index)))


def _identity(raw: Mapping[str, Any]) -> tuple[str, str]:
    """Digest the calibration values, excluding metadata.

    Hashed from the normalised structure rather than the file bytes, so
    reformatting or adding a comment does not invent a new calibration. Every
    number is normalised to a float first, so editing ``18`` to ``18.0`` does
    not either.
    """
    values = {key: value for key, value in raw.items() if key != _METADATA_SECTION}
    _reject_non_finite(values)
    payload = canonical_json(numbers_as_floats(values))
    return sha256_hex(payload)[:ASSUMPTION_SET_ID_LENGTH], content_hash(payload)


def _objective(raw: Mapping[str, Any]) -> ObjectiveWeights:
    table = _table(raw, "objective")
    return ObjectiveWeights(
        capacity_weight=_float(table, "capacity_weight", "objective"),
        capacity_score_floor=_float(table, "capacity_score_floor", "objective"),
        tech_split_weight=_float(table, "tech_split_weight", "objective"),
        tech_split_score_floor=_float(table, "tech_split_score_floor", "objective"),
        tech_split_tolerance=_float(table, "tech_split_tolerance", "objective"),
        return_weight=_float(table, "return_weight", "objective"),
        return_clamp=_float(table, "return_clamp", "objective"),
        return_scale=_float(table, "return_scale", "objective"),
        utilisation_weight=_float(table, "utilisation_weight", "objective"),
        leverage_weight=_float(table, "leverage_weight", "objective"),
        merchant_weight=_float(table, "merchant_weight", "objective"),
        country_concentration_weight=_float(table, "country_concentration_weight", "objective"),
        project_concentration_weight=_float(table, "project_concentration_weight", "objective"),
        risk_weight=_float(table, "risk_weight", "objective"),
        empty_portfolio_score=_float(table, "empty_portfolio_score", "objective"),
        reject_base=_float(table, "reject_base", "objective"),
        reject_slope=_float(table, "reject_slope", "objective"),
        equity_cap_tolerance_eur=_float(table, "equity_cap_tolerance_eur", "objective"),
        quantisation_dp=_int(table, "quantisation_dp", "objective"),
    )


def _ga(raw: Mapping[str, Any]) -> GaParams:
    table = _table(raw, "ga")
    efforts = _table(table, "effort", "ga")
    return GaParams(
        mutation_rate=_float(table, "mutation_rate", "ga"),
        tournament_size=_int(table, "tournament_size", "ga"),
        elite_count=_int(table, "elite_count", "ga"),
        inclusion_ceiling=_float(table, "inclusion_ceiling", "ga"),
        inclusion_floor=_float(table, "inclusion_floor", "ga"),
        effort=_keyed_by(
            efforts,
            Effort,
            lambda parent, key, where: EffortParams(
                population=_int(_table(parent, key, where), "population", f"{where}.{key}"),
                generations=_int(_table(parent, key, where), "generations", f"{where}.{key}"),
            ),
            "ga.effort",
        ),
    )


def _validation(raw: Mapping[str, Any]) -> ValidationParams:
    table = _table(raw, "validation")
    return ValidationParams(
        tolerance_abs_m=_float(table, "tolerance_abs_m", "validation"),
        tolerance_rel=_float(table, "tolerance_rel", "validation"),
        capacity_factor=_band(table, "capacity_factor", "validation"),
        min_dscr_band=_band(table, "min_dscr_band", "validation"),
        capex_per_kw=_keyed_by(
            _table(table, "capex_per_kw", "validation"),
            Technology,
            _band,
            "validation.capex_per_kw",
        ),
        stage_gearing_ceiling=_keyed_by(
            _table(table, "stage_gearing_ceiling", "validation"),
            Stage,
            _float,
            "validation.stage_gearing_ceiling",
        ),
    )


def _generator(raw: Mapping[str, Any]) -> GeneratorParams:
    table = _table(raw, "generator")
    risk = _table(table, "development_risk", "generator")
    probability = _table(table, "probability", "generator")
    capacity_factor = _table(table, "capacity_factor", "generator")
    baseload = _market_keyed(
        _table(table, "baseload_price", "generator"), "generator.baseload_price"
    )
    factors = _keyed_by_some(
        capacity_factor,
        Technology,
        lambda parent, key, where: _market_keyed(_table(parent, key, where), f"{where}.{key}"),
        "generator.capacity_factor",
        required=MARKET_CAPACITY_FACTOR_TECHNOLOGIES,
    )
    # A market needs both a capacity factor and a price for a project to be
    # generated in it. One without the other is a table that was edited and its
    # partner forgotten, and it surfaces as a KeyError mid-run rather than here.
    for technology, markets in factors.items():
        orphans = sorted(set(markets) - set(baseload))
        if orphans:
            raise AssumptionError(
                f"generator.capacity_factor.{technology.value}: "
                f"{', '.join(orphans)} have no generator.baseload_price"
            )
    return GeneratorParams(
        base_year=_int(table, "base_year", "generator"),
        cod_first_year=_int(table, "cod_first_year", "generator"),
        cod_last_year=_int(table, "cod_last_year", "generator"),
        hours_per_year_gwh=_float(table, "hours_per_year_gwh", "generator"),
        ramp_factor=_float(table, "ramp_factor", "generator"),
        tax_rate=_float(table, "tax_rate", "generator"),
        depreciation_years=_int(table, "depreciation_years", "generator"),
        debt_rate=_float(table, "debt_rate", "generator"),
        debt_tenor_years=_int(table, "debt_tenor_years", "generator"),
        target_dscr=_float(table, "target_dscr", "generator"),
        price_escalation=_float(table, "price_escalation", "generator"),
        merchant_escalation=_float(table, "merchant_escalation", "generator"),
        opex_escalation=_float(table, "opex_escalation", "generator"),
        entry_yield_jitter=_range(table, "entry_yield_jitter", "generator"),
        contracted_share_floor=_float(table, "contracted_share_floor", "generator"),
        capacity_factor_jitter=_range(table, "capacity_factor_jitter", "generator"),
        contract_price_factor=_range(table, "contract_price_factor", "generator"),
        contract_tenor_choices=_int_tuple(table, "contract_tenor_choices", "generator"),
        dscr_resample_attempts=_int(table, "dscr_resample_attempts", "generator"),
        technology_mix=_keyed_by(
            _table(table, "technology_mix", "generator"),
            Technology,
            _float,
            "generator.technology_mix",
        ),
        stage_mix=_keyed_by(
            _table(table, "stage_mix", "generator"),
            Stage,
            _float,
            "generator.stage_mix",
        ),
        capacity_mw=_keyed_by(
            _table(table, "capacity_mw", "generator"),
            Technology,
            _range,
            "generator.capacity_mw",
        ),
        cod_offset=_keyed_by(
            _table(table, "cod_offset", "generator"),
            Stage,
            _range,
            "generator.cod_offset",
        ),
        degradation=_keyed_by(
            _table(table, "degradation", "generator"),
            Technology,
            _float,
            "generator.degradation",
        ),
        capture_factor=_keyed_by(
            _table(table, "capture_factor", "generator"),
            Technology,
            _float,
            "generator.capture_factor",
        ),
        entry_yield=_keyed_by(
            _table(table, "entry_yield", "generator"),
            Stage,
            _float,
            "generator.entry_yield",
            ignore=frozenset({"offshore_override"}),
        ),
        entry_yield_offshore_override=_float(
            _table(table, "entry_yield", "generator"),
            "offshore_override",
            "generator.entry_yield",
        ),
        opex_per_kw_year=_keyed_by(
            _table(table, "opex_per_kw_year", "generator"),
            Technology,
            _range,
            "generator.opex_per_kw_year",
        ),
        offshore_capacity_factor=_range(table, "offshore_capacity_factor", "generator"),
        contracted_share=_keyed_by(
            _table(table, "contracted_share", "generator"),
            Stage,
            _range,
            "generator.contracted_share",
        ),
        development_risk_base=_keyed_by(
            _table(table, "development_risk_base", "generator"),
            Stage,
            _float,
            "generator.development_risk_base",
        ),
        development_risk_offshore_premium=_float(
            risk, "offshore_premium", "generator.development_risk"
        ),
        development_risk_jitter=_float(risk, "jitter", "generator.development_risk"),
        development_risk=_band(table, "development_risk", "generator"),
        grid_secured_greenfield_probability=_float(
            probability, "grid_secured_greenfield", "generator.probability"
        ),
        om_contracted_probability=_float(probability, "om_contracted", "generator.probability"),
        baseload_price=baseload,
        capacity_factor=factors,
    )


def load_assumption_set(path: Path) -> AssumptionSet:
    """Read and resolve one calibration file."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AssumptionError(f"no assumption set at {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise AssumptionError(f"{path}: not valid TOML — {exc}") from exc

    set_id, digest = _identity(raw)
    meta = _table(raw, _METADATA_SECTION)
    risk_caps = _table(raw, "risk_caps")
    feasibility = _table(raw, "feasibility")
    interpretation = _table(raw, "interpretation")

    return AssumptionSet(
        name=path.stem,
        assumption_set_id=set_id,
        content_hash=digest,
        meta=Metadata(
            label=_str(meta, "label", _METADATA_SECTION),
            version=_int(meta, "version", _METADATA_SECTION),
            created_by=_str(meta, "created_by", _METADATA_SECTION),
            supersedes=_str(meta, "supersedes", _METADATA_SECTION),
        ),
        exit_multiples=_keyed_by(
            _table(raw, "exit_multiples"), Technology, _float, "exit_multiples"
        ),
        lcoe_real_discount_rate=_float(_table(raw, "lcoe"), "real_discount_rate", "lcoe"),
        irr=IrrBracket(
            low=_float(_table(raw, "irr"), "low", "irr"),
            high=_float(_table(raw, "irr"), "high", "irr"),
            iterations=_int(_table(raw, "irr"), "iterations", "irr"),
        ),
        co2_t_per_mwh=_float(_table(raw, "emissions"), "co2_t_per_mwh", "emissions"),
        objective=_objective(raw),
        ga=_ga(raw),
        risk_caps=RiskCaps(
            project=_keyed_by(
                _table(risk_caps, "project", "risk_caps"),
                RiskAppetite,
                _float,
                "risk_caps.project",
            ),
            portfolio=_keyed_by(
                _table(risk_caps, "portfolio", "risk_caps"),
                RiskAppetite,
                _float,
                "risk_caps.portfolio",
            ),
        ),
        feasibility=FeasibilityThresholds(
            solar_divergence_tolerance=_float(
                feasibility, "solar_divergence_tolerance", "feasibility"
            ),
            capital_absorption_floor=_float(feasibility, "capital_absorption_floor", "feasibility"),
        ),
        validation=_validation(raw),
        interpretation=Interpretation(
            exit_year_fcfe_included=_bool(
                interpretation, "exit_year_fcfe_included", "interpretation"
            ),
            lcoe_opex_basis=_str(interpretation, "lcoe_opex_basis", "interpretation"),
        ),
        generator=_generator(raw),
    )


def load_default(name: str = DEFAULT_ASSUMPTION_SET) -> AssumptionSet:
    """Load a named calibration from :func:`assumptions_dir`."""
    return load_assumption_set(assumptions_dir() / f"{name}.toml")
