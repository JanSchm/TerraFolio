"""The assumption set: what it holds, and what its identity does and does not move on.

``assumption_set_id`` is the whole audit trail in one field. It must move when a
number moves, stay put when only a label changes, and be the same in every
process — otherwise a stored run cannot say which numbers produced it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from terrafolio.config.hashing import ASSUMPTION_SET_ID_LENGTH, canonical_json, numbers_as_floats
from terrafolio.config.loader import (
    DEFAULT_ASSUMPTION_SET,
    ENV_ASSUMPTIONS_DIR,
    AssumptionError,
    assumptions_dir,
    load_assumption_set,
    load_default,
)
from terrafolio.domain.enums import Effort, RiskAppetite, Stage, Technology

SOURCE: Final = assumptions_dir() / f"{DEFAULT_ASSUMPTION_SET}.toml"


@pytest.fixture
def text() -> str:
    return SOURCE.read_text(encoding="utf-8")


def _load_variant(tmp_path: Path, text: str) -> str:
    path = tmp_path / "variant.toml"
    path.write_text(text, encoding="utf-8")
    return load_assumption_set(path).assumption_set_id


def _edited(text: str, old: str, new: str) -> str:
    """Apply an edit, failing if it matched nothing.

    A replacement that silently no-ops turns a test of the loader into a test
    that the shipped file loads, which it already is elsewhere.
    """
    assert old in text, f"the assumption file no longer contains {old!r}"
    return text.replace(old, new, 1)


# --------------------------------------------------------------------------
# What it holds
# --------------------------------------------------------------------------


def test_the_shipped_set_loads() -> None:
    assumptions = load_default()
    assert assumptions.name == DEFAULT_ASSUMPTION_SET
    assert len(assumptions.assumption_set_id) == ASSUMPTION_SET_ID_LENGTH
    assert assumptions.content_hash.startswith("sha256:")


def test_every_technology_and_stage_is_covered() -> None:
    """A missing member would otherwise fail as a KeyError deep in a reduction."""
    assumptions = load_default()
    assert set(assumptions.exit_multiples) == set(Technology)
    assert set(assumptions.validation.capex_per_kw) == set(Technology)
    assert set(assumptions.validation.stage_gearing_ceiling) == set(Stage)
    assert set(assumptions.generator.degradation) == set(Technology)
    assert set(assumptions.generator.contracted_share) == set(Stage)
    assert set(assumptions.ga.effort) == set(Effort)
    assert set(assumptions.risk_caps.project) == set(RiskAppetite)
    assert set(assumptions.risk_caps.portfolio) == set(RiskAppetite)


def test_the_two_risk_caps_are_different_things() -> None:
    """A hard per-project screen and a soft capex-weighted portfolio penalty (§5.3)."""
    caps = load_default().risk_caps
    assert caps.project[RiskAppetite.BALANCED] != caps.portfolio[RiskAppetite.BALANCED]


def test_the_graded_rejection_always_loses_to_a_feasible_portfolio() -> None:
    """Epic §6.2. At the mockup's -20 an infeasible portfolio could outrank a feasible one."""
    objective = load_default().objective
    assert objective.reject_base < objective.empty_portfolio_score
    assert objective.reject_slope < 0
    worst_feasible = objective.empty_portfolio_score
    for overshoot in (0.0, 0.5, 10.0):
        score = objective.reject_base + objective.reject_slope * overshoot
        assert score < worst_feasible


def test_the_inclusion_ceiling_is_the_specs_own_constant() -> None:
    """Epic §6.1 keeps §10.1's 0.35 as a ceiling rather than discarding it."""
    ga = load_default().ga
    assert ga.inclusion_floor < ga.inclusion_ceiling


def test_the_population_is_even() -> None:
    """The GA pairs parents; an odd population silently drops a chromosome."""
    for params in load_default().ga.effort.values():
        assert params.population % 2 == 0


def test_the_tolerance_keys_are_the_ones_the_schema_document_cites() -> None:
    validation = load_default().validation
    assert validation.tolerance_abs_m > 0
    assert validation.tolerance_rel > 0


def test_the_set_is_frozen() -> None:
    assumptions = load_default()
    with pytest.raises(AttributeError):
        assumptions.co2_t_per_mwh = 0.4  # type: ignore[misc]
    with pytest.raises(TypeError):
        assumptions.exit_multiples[Technology.SOLAR_PV] = 1.0  # type: ignore[index]


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


def test_changing_any_number_changes_the_id(tmp_path: Path, text: str) -> None:
    before = load_default().assumption_set_id
    after = _load_variant(tmp_path, _edited(text, "co2_t_per_mwh = 0.32", "co2_t_per_mwh = 0.33"))
    assert after != before


def test_changing_a_deeply_nested_number_changes_the_id(tmp_path: Path, text: str) -> None:
    before = load_default().assumption_set_id
    after = _load_variant(tmp_path, _edited(text, "ES = 58.0", "ES = 59.0"))
    assert after != before


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('label = "Default 2026"', 'label = "Default 2026 (typo fixed)"'),
        ('created_by = "terrafolio"', 'created_by = "someone else"'),
    ],
)
def test_changing_only_metadata_leaves_the_id_alone(
    tmp_path: Path, text: str, old: str, new: str
) -> None:
    """Fixing a typo in a label must not invalidate every run stored against it."""
    assert _load_variant(tmp_path, _edited(text, old, new)) == load_default().assumption_set_id


def test_a_float_written_without_a_point_is_the_same_calibration(tmp_path: Path, text: str) -> None:
    """``9`` and ``9.0`` are one exit multiple, and json.dumps writes them differently."""
    assert (
        _load_variant(tmp_path, _edited(text, "solar_pv = 9.0", "solar_pv = 9"))
        == load_default().assumption_set_id
    )


def test_an_integer_field_must_be_written_as_an_integer(tmp_path: Path, text: str) -> None:
    """A tenor of 18.0 years is a typo worth naming, not a value worth guessing at."""
    with pytest.raises(AssumptionError, match="expected an integer, got float"):
        _load_variant(tmp_path, _edited(text, "debt_tenor_years = 18", "debt_tenor_years = 18.5"))


def test_reformatting_does_not_invent_a_new_calibration(tmp_path: Path, text: str) -> None:
    """Hashed from the normalised structure, never the file bytes."""
    reformatted = text.replace("\n\n", "\n\n\n") + "\n# a trailing comment\n"
    assert _load_variant(tmp_path, reformatted) == load_default().assumption_set_id


def test_the_id_is_identical_across_processes() -> None:
    """Asserting it twice in one process would prove nothing about hash randomisation."""
    code = (
        "from terrafolio.config.loader import load_default; print(load_default().assumption_set_id)"
    )
    seen = set()
    for seed in ("0", "1", "12345"):
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        seen.add(result.stdout.strip())
    assert len(seen) == 1
    assert seen == {load_default().assumption_set_id}


def test_canonical_json_is_insensitive_to_key_order() -> None:
    one = {"b": {"y": 2, "x": 1}, "a": 3}
    two = {"a": 3, "b": {"x": 1, "y": 2}}
    assert canonical_json(numbers_as_floats(one)) == canonical_json(numbers_as_floats(two))


def test_a_flag_is_not_a_number() -> None:
    """``isinstance(True, int)`` is true, and a flag hashed as 1.0 would collide."""
    assert canonical_json(numbers_as_floats({"f": True, "n": 1})) == '{"f":true,"n":1.0}'


# --------------------------------------------------------------------------
# Errors an analyst can act on
# --------------------------------------------------------------------------


def test_a_missing_key_names_its_path(tmp_path: Path, text: str) -> None:
    with pytest.raises(AssumptionError, match=r"objective\.risk_weight: missing key"):
        _load_variant(tmp_path, _edited(text, "risk_weight = -1.6", ""))


def test_a_wrong_type_names_its_path_and_what_it_found(tmp_path: Path, text: str) -> None:
    with pytest.raises(
        AssumptionError, match=r"lcoe\.real_discount_rate: expected a number, got str"
    ):
        _load_variant(
            tmp_path, _edited(text, "real_discount_rate = 0.06", 'real_discount_rate = "six"')
        )


def test_a_missing_section_is_named(tmp_path: Path, text: str) -> None:
    with pytest.raises(AssumptionError, match=r"emissions: missing section"):
        _load_variant(tmp_path, _edited(text, "[emissions]\nco2_t_per_mwh = 0.32", ""))


def test_a_typo_in_an_enum_keyed_table_is_not_ignored(tmp_path: Path, text: str) -> None:
    """Silently ignoring ``solar`` would leave solar_pv missing and fail far away."""
    with pytest.raises(AssumptionError, match="unknown key"):
        _load_variant(tmp_path, _edited(text, "solar_pv = 9.0", "solar_pv = 9.0\nsolar = 9.0"))


def test_a_missing_enum_member_is_named(tmp_path: Path, text: str) -> None:
    with pytest.raises(AssumptionError, match="offshore_wind"):
        _load_variant(tmp_path, _edited(text, "offshore_wind = 9.5", ""))


def test_an_inverted_band_is_rejected(tmp_path: Path, text: str) -> None:
    with pytest.raises(AssumptionError, match="is above high"):
        _load_variant(
            tmp_path,
            _edited(
                text,
                "[validation.capacity_factor]\nlow = 0.05",
                "[validation.capacity_factor]\nlow = 0.95",
            ),
        )


def test_a_flag_must_be_a_boolean(tmp_path: Path, text: str) -> None:
    with pytest.raises(AssumptionError, match="expected true or false"):
        _load_variant(
            tmp_path, _edited(text, "exit_year_fcfe_included = true", "exit_year_fcfe_included = 1")
        )


def test_broken_toml_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.toml"
    path.write_text("[meta\n", encoding="utf-8")
    with pytest.raises(AssumptionError, match="not valid TOML"):
        load_assumption_set(path)


def test_a_missing_file_is_named(tmp_path: Path) -> None:
    with pytest.raises(AssumptionError, match="no assumption set at"):
        load_assumption_set(tmp_path / "absent.toml")


# --------------------------------------------------------------------------
# Finding the directory
# --------------------------------------------------------------------------


def test_the_environment_override_wins(
    tmp_path: Path, text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / f"{DEFAULT_ASSUMPTION_SET}.toml").write_text(text, encoding="utf-8")
    monkeypatch.setenv(ENV_ASSUMPTIONS_DIR, str(tmp_path))
    assert assumptions_dir() == tmp_path
    assert load_default().assumption_set_id == _load_variant(tmp_path, text)


def test_an_override_pointing_nowhere_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_ASSUMPTIONS_DIR, "/nonexistent/assumptions")
    with pytest.raises(AssumptionError, match="not a directory"):
        assumptions_dir()
