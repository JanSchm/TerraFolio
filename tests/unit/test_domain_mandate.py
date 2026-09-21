"""The mandate: every §5.1-5.3 range, and the two rules that are not ranges."""

from __future__ import annotations

from typing import Any, Final

import pytest
from pydantic import ValidationError

from terrafolio.domain import mandate_bounds as mb
from terrafolio.domain.enums import RiskAppetite, Stage
from terrafolio.domain.mandate import Mandate
from terrafolio.domain.reduce import mandate_to_scalars

VALID: Final[dict[str, Any]] = {
    "availableCapital_m": 1200.0,
    "capacityTargetMw": 1500.0,
    "solarShare": 0.45,
    "targetIrr": 0.11,
    "holdYears": 10,
    "countries": ["ES", "PT", "DE"],
    "stages": ["greenfield", "ready_to_build", "construction"],
    "minLeverage": 0.60,
    "minDscr": 1.25,
    "maxMerchantShare": 0.35,
    "maxCountryShare": 0.35,
    "maxProjectShare": 0.15,
    "codFrom": 2027,
    "codTo": 2032,
    "riskAppetite": "balanced",
}

ALIASES: Final[dict[str, str]] = {
    name: field.alias or name for name, field in Mandate.model_fields.items()
}


def _with(**overrides: Any) -> dict[str, Any]:
    return {**VALID, **overrides}


def test_the_defaults_in_the_bounds_table_make_a_valid_mandate() -> None:
    """Every §5.1-5.3 default, taken straight from the bounds module."""
    payload = {ALIASES[name]: bound.default for name, bound in mb.ALL_BOUNDS.items()}
    payload["holdYears"] = int(mb.HOLD_YEARS.default)
    payload["codFrom"] = int(mb.COD_FROM.default)
    payload["codTo"] = int(mb.COD_TO.default)
    payload |= {
        "countries": VALID["countries"],
        "stages": VALID["stages"],
        "riskAppetite": "balanced",
    }
    mandate = Mandate.model_validate(payload)
    assert mandate.available_capital_m == mb.AVAILABLE_CAPITAL_M.default


def test_bounds_cover_the_model_exactly() -> None:
    """A field added without a bound, or a bound left behind, fails here."""
    bounded = set(mb.ALL_BOUNDS)
    unbounded = {
        "countries",
        "stages",
        "risk_appetite",
        "grid_secured_only",
        "eur_revenue_only",
        "om_contracted_only",
    }
    assert bounded | unbounded == set(Mandate.model_fields)
    assert bounded & unbounded == set()


@pytest.mark.parametrize("name", sorted(mb.ALL_BOUNDS))
def test_each_range_accepts_its_endpoints(name: str) -> None:
    bound = mb.ALL_BOUNDS[name]
    alias = ALIASES[name]
    integral = name in {"hold_years", "cod_from", "cod_to"}
    for edge in (bound.lo, bound.hi):
        value: Any = int(edge) if integral else edge
        # The COD window has two handles on one range; widening from either end
        # must keep from <= to or the cross-field rule fires instead.
        extra: dict[str, Any] = {}
        if name == "cod_from":
            extra["codTo"] = int(mb.COD_TO.hi)
        elif name == "cod_to":
            extra["codFrom"] = int(mb.COD_FROM.lo)
        Mandate.model_validate(_with(**{alias: value}, **extra))


@pytest.mark.parametrize("name", sorted(mb.ALL_BOUNDS))
def test_each_range_rejects_just_outside(name: str) -> None:
    bound = mb.ALL_BOUNDS[name]
    alias = ALIASES[name]
    integral = name in {"hold_years", "cod_from", "cod_to"}
    for edge, delta in ((bound.lo, -bound.step), (bound.hi, bound.step)):
        value: Any = int(edge + delta) if integral else edge + delta
        with pytest.raises(ValidationError):
            Mandate.model_validate(_with(**{alias: value}))


def test_steps_are_published_but_not_enforced() -> None:
    """§5's steps describe sliders; the API is also a programmatic surface."""
    schema = Mandate.model_json_schema()
    assert schema["properties"]["minDscr"]["multipleOf"] == mb.MIN_DSCR.step
    assert Mandate.model_validate(_with(minDscr=1.27)).min_dscr == 1.27


def test_an_inverted_cod_window_raises() -> None:
    """Not a silently empty result — that sends the user hunting the other screens."""
    with pytest.raises(ValidationError) as caught:
        Mandate.model_validate(_with(codFrom=2031, codTo=2029))
    assert "COD window" in str(caught.value)


@pytest.mark.parametrize("field", ["countries", "stages"])
def test_an_empty_set_raises(field: str) -> None:
    with pytest.raises(ValidationError):
        Mandate.model_validate(_with(**{field: []}))


def test_set_like_fields_are_normalised() -> None:
    """Two mandates differing only in click order are the same mandate."""
    one = Mandate.model_validate(_with(countries=["PT", "ES", "ES", "DE"]))
    two = Mandate.model_validate(_with(countries=["DE", "ES", "PT"]))
    assert one.countries == two.countries == ("DE", "ES", "PT")
    assert hash(one) == hash(two)
    assert one == two


def test_a_mandate_is_hashable_and_frozen() -> None:
    """The sub-100ms feasibility path wants a mandate it can use as a cache key."""
    mandate = Mandate.model_validate(VALID)
    assert isinstance(hash(mandate), int)
    with pytest.raises(ValidationError):
        mandate.min_dscr = 1.5  # type: ignore[misc]


def test_run_controls_are_not_part_of_the_mandate() -> None:
    """Locks, exclusions, effort and seed belong to a run (docs/api.md §6.2)."""
    for key in ("lockedIds", "excludedIds", "effort", "seed"):
        with pytest.raises(ValidationError):
            Mandate.model_validate(_with(**{key: []}))


def test_the_wire_round_trips() -> None:
    mandate = Mandate.model_validate(VALID)
    again = Mandate.model_validate(mandate.model_dump(mode="json"))
    assert again == mandate


# --------------------------------------------------------------------------
# The reduction to plain scalars
# --------------------------------------------------------------------------


def test_money_is_the_only_unit_that_changes() -> None:
    """€m on the wire, euros in the core — and nothing else converts."""
    mandate = Mandate.model_validate(VALID)
    scalars = mandate_to_scalars(mandate)
    assert scalars.available_capital_eur == mandate.available_capital_m * 1_000_000
    assert scalars.solar_share == mandate.solar_share
    assert scalars.target_irr == mandate.target_irr
    assert scalars.min_leverage == mandate.min_leverage
    assert scalars.max_merchant_share == mandate.max_merchant_share


def test_the_reduction_carries_every_screen() -> None:
    """The core must be able to apply all nine screens from what it is handed."""
    scalars = mandate_to_scalars(Mandate.model_validate(VALID))
    assert scalars.countries == ("DE", "ES", "PT")
    assert Stage.GREENFIELD in scalars.stages
    assert scalars.risk_appetite is RiskAppetite.BALANCED
    assert (scalars.cod_from, scalars.cod_to) == (2027, 2032)
    assert scalars.grid_secured_only is False


def test_the_reduction_is_frozen() -> None:
    scalars = mandate_to_scalars(Mandate.model_validate(VALID))
    with pytest.raises(AttributeError):
        scalars.min_dscr = 2.0  # type: ignore[misc]


# --------------------------------------------------------------------------
# Strictness
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["Germany", "de", "ESP", "E", "D3"])
def test_a_country_must_be_an_alpha_two_code(value: str) -> None:
    """Otherwise it is hashed into the mandate and then matches nothing.

    The screen would report no candidates pass, which sends the user hunting
    through the other eight rather than fixing the country list.
    """
    with pytest.raises(ValidationError):
        Mandate.model_validate(_with(countries=[value]))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("minDscr", "1.25"),
        ("availableCapital_m", "1200"),
        ("solarShare", True),
        ("holdYears", 10.0),
        ("holdYears", "10"),
        ("codFrom", 2027.0),
        ("gridSecuredOnly", 1),
        ("eurRevenueOnly", "true"),
    ],
)
def test_mandate_numbers_are_not_coerced(key: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Mandate.model_validate(_with(**{key: value}))


def test_an_integer_is_still_a_valid_float() -> None:
    """JSON has one number type; ``1200`` is how a client writes ``1200.0``."""
    assert Mandate.model_validate(_with(availableCapital_m=1200)).available_capital_m == 1200.0
