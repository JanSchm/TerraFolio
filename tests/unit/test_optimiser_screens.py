"""The nine screens and the §5.4 preview, on a pipeline small enough to reason about.

``sample_arrays`` holds two projects: ``P01``, Spanish solar, ready-to-build, grid
secured, O&M contracted, EUR revenue, risk 2.7; and ``P02``, German onshore wind,
greenfield, **no** grid connection, EUR revenue, risk 3.4. Every test below turns one
control until exactly one of them falls out, which is the only way to tell a screen
that works from a screen that rejects everything.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from test_pipeline_arrays import sample_arrays

from terrafolio.config.loader import load_default
from terrafolio.domain.enums import RiskAppetite, Stage, WarningCode
from terrafolio.domain.scalars import MandateScalars
from terrafolio.optimiser.feasibility import preview_feasibility
from terrafolio.optimiser.screens import SCREEN_NAMES, apply_screens

ASSUMPTIONS = load_default()
ARRAYS = sample_arrays()


def mandate(**overrides: Any) -> MandateScalars:
    """A mandate both sample projects pass, with ``overrides`` applied."""
    defaults: dict[str, Any] = {
        "available_capital_eur": 1_200e6,
        "capacity_target_mw": 200.0,
        "solar_share": 0.45,
        "target_irr": 0.11,
        "hold_years": 10,
        "countries": ("ES", "DE"),
        "stages": (Stage.GREENFIELD, Stage.READY_TO_BUILD, Stage.CONSTRUCTION),
        "min_leverage": 0.60,
        "min_dscr": 1.25,
        "max_merchant_share": 0.35,
        "max_country_share": 0.35,
        "max_project_share": 0.15,
        "cod_from": 2027,
        "cod_to": 2032,
        "risk_appetite": RiskAppetite.HIGH,
        "grid_secured_only": False,
        "eur_revenue_only": False,
        "om_contracted_only": False,
    }
    return MandateScalars(**{**defaults, **overrides})


def _eligible(**overrides: Any) -> list[str]:
    result = apply_screens(ARRAYS, mandate(**overrides), ASSUMPTIONS)
    return [ARRAYS.ids[index] for index in np.flatnonzero(result.eligible).tolist()]


# ---------------------------------------------------------------------------
# Each screen, one at a time
# ---------------------------------------------------------------------------


def test_everything_passes_a_wide_open_mandate() -> None:
    assert _eligible() == ["P01", "P02"]


def test_the_country_screen_bites() -> None:
    assert _eligible(countries=("ES",)) == ["P01"]


def test_the_uk_alias_is_accepted_on_the_mandate_too() -> None:
    """An alias that holds in one direction only is a screen, not an alias."""
    british = sample_arrays()
    object.__setattr__(british.location, "country_codes", ("GB", "DE"))
    result = apply_screens(british, mandate(countries=("UK", "DE")), ASSUMPTIONS)
    assert result.eligible.tolist() == [True, True]


def test_the_stage_screen_bites() -> None:
    assert _eligible(stages=(Stage.READY_TO_BUILD,)) == ["P01"]


def test_the_cod_window_screen_bites_at_both_ends() -> None:
    assert _eligible(cod_from=2029) == ["P02"]
    assert _eligible(cod_to=2028) == ["P01"]


def test_the_cod_window_is_inclusive() -> None:
    assert _eligible(cod_from=2028, cod_to=2029) == ["P01", "P02"]


def test_the_grid_screen_only_applies_when_it_is_switched_on() -> None:
    assert _eligible(grid_secured_only=False) == ["P01", "P02"]
    assert _eligible(grid_secured_only=True) == ["P01"]


def test_the_om_screen_only_applies_when_it_is_switched_on() -> None:
    """Both sample projects have contracted O&M, so this one drops nobody."""
    assert _eligible(om_contracted_only=True) == ["P01", "P02"]


def test_the_risk_cap_comes_from_the_assumption_set() -> None:
    """2.6 / 3.6 / 5.0 by appetite — P02's 3.4 sits between the first two."""
    assert _eligible(risk_appetite=RiskAppetite.HIGH) == ["P01", "P02"]
    assert _eligible(risk_appetite=RiskAppetite.BALANCED) == ["P01", "P02"]
    assert _eligible(risk_appetite=RiskAppetite.LOW) == []


def test_the_exclusion_list_bites() -> None:
    result = apply_screens(ARRAYS, mandate(), ASSUMPTIONS, excluded_ids=["P01"])
    assert result.eligible.tolist() == [False, True]


def test_an_unlevered_project_passes_the_dscr_screen() -> None:
    """A-21. There is no coverage ratio to fail, so a null must not reject."""
    unlevered = sample_arrays()
    object.__setattr__(
        unlevered.statements.ratios, "dscr", np.full_like(ARRAYS.statements.ratios.dscr, np.nan)
    )
    result = apply_screens(unlevered, mandate(min_dscr=2.0), ASSUMPTIONS)
    assert result.eligible.tolist() == [True, True]


def test_a_levered_project_below_the_floor_is_screened_out() -> None:
    result = apply_screens(ARRAYS, mandate(min_dscr=2.0), ASSUMPTIONS)
    assert result.eligible.tolist() == [False, False]


# ---------------------------------------------------------------------------
# Drop counts, and why they overlap
# ---------------------------------------------------------------------------


def test_every_screen_is_reported_even_when_it_drops_nothing() -> None:
    result = apply_screens(ARRAYS, mandate(), ASSUMPTIONS)
    assert set(result.drops) == set(SCREEN_NAMES)
    assert sum(result.drops.values()) == 0


def test_drop_counts_are_independent_not_cumulative() -> None:
    """§13 needs to name *which* screens to widen.

    Both controls reject ``P02`` here. Counted cumulatively, whichever ran first
    would take the credit and the other would report zero — and a user widening the
    named control would get nothing back.
    """
    result = apply_screens(
        ARRAYS, mandate(countries=("ES",), stages=(Stage.READY_TO_BUILD,)), ASSUMPTIONS
    )
    assert result.drops["countries"] == 1
    assert result.drops["stages"] == 1
    assert result.eligible_count == 1


def test_binding_screens_are_ordered_worst_first() -> None:
    result = apply_screens(
        ARRAYS, mandate(countries=("ES",), risk_appetite=RiskAppetite.LOW), ASSUMPTIONS
    )
    assert result.binding_screens[0] == "riskScore"
    assert set(result.binding_screens) == {"riskScore", "countries"}


# ---------------------------------------------------------------------------
# Locks
# ---------------------------------------------------------------------------


def test_a_lock_re_admits_a_project_the_screens_rejected() -> None:
    result = apply_screens(ARRAYS, mandate(countries=("ES",)), ASSUMPTIONS, locked_ids=["P02"])
    assert result.eligible.tolist() == [True, True]
    assert result.readmitted == ("P02",)


def test_locks_and_exclusions_accept_any_iterable_not_only_a_sequence() -> None:
    """The signature says ``Iterable``, so a generator has to work.

    Building the lock set inside the per-project comprehension consumed a generator
    on the first project and left every project after it looking unlocked — no error,
    just a silently different portfolio. 3A receives these ids over the wire and may
    well hand over a comprehension.
    """
    wanted = ["P02"]
    from_list = apply_screens(ARRAYS, mandate(countries=("ES",)), ASSUMPTIONS, locked_ids=wanted)
    from_generator = apply_screens(
        ARRAYS, mandate(countries=("ES",)), ASSUMPTIONS, locked_ids=(p for p in wanted)
    )
    assert from_generator.readmitted == from_list.readmitted == ("P02",)
    assert from_generator.eligible.tolist() == from_list.eligible.tolist()

    excluded_from_generator = apply_screens(
        ARRAYS, mandate(), ASSUMPTIONS, excluded_ids=(p for p in ["P01"])
    )
    assert excluded_from_generator.eligible.tolist() == [False, True]


def test_a_lock_that_changes_nothing_is_not_reported_as_a_re_admission() -> None:
    result = apply_screens(ARRAYS, mandate(), ASSUMPTIONS, locked_ids=["P01"])
    assert result.readmitted == ()


def test_an_exclusion_beats_a_lock() -> None:
    """The more specific instruction wins; a stale lock does not resurrect a project."""
    result = apply_screens(ARRAYS, mandate(), ASSUMPTIONS, locked_ids=["P01"], excluded_ids=["P01"])
    assert result.eligible.tolist() == [False, True]
    assert result.readmitted == ()


# ---------------------------------------------------------------------------
# §5.4's preview
# ---------------------------------------------------------------------------


def test_the_footer_figures_describe_the_whole_eligible_set() -> None:
    preview = preview_feasibility(ARRAYS, mandate(), ASSUMPTIONS)
    assert preview.eligible_count == 2
    assert preview.total_count == 2
    assert preview.eligible_capacity_mw == pytest.approx(270.0)
    assert preview.eligible_equity == pytest.approx(float(ARRAYS.capital.equity.sum()))
    assert preview.eligible_solar_share == pytest.approx(180.0 / 270.0)


def test_no_candidates_blocks_the_run_and_names_the_screens() -> None:
    preview = preview_feasibility(ARRAYS, mandate(risk_appetite=RiskAppetite.LOW), ASSUMPTIONS)
    assert preview.eligible_count == 0
    assert not preview.runnable
    assert preview.signals[0].code == WarningCode.NO_CANDIDATES
    assert "riskScore" in preview.screens_to_widen


def test_an_empty_pool_raises_no_advisory_warnings() -> None:
    """An empty pool has zero capacity, zero equity and a zero solar share.

    Every advisory test would fire on figures that describe nothing, so the preview
    would read as five problems when there is one. ``web/js/feasibility.js`` guards
    the same four checks on a non-empty pool, and 4B proves the two agree — a preview
    that disagrees with its mirror is worse than one that says less.
    """
    preview = preview_feasibility(ARRAYS, mandate(risk_appetite=RiskAppetite.LOW), ASSUMPTIONS)
    assert preview.eligible_count == 0
    assert [signal.code for signal in preview.signals] == [WarningCode.NO_CANDIDATES]


def test_an_empty_pool_still_reports_locks_and_exclusions() -> None:
    """``LOCKS_PRESENT`` is about the user's own edits, not about the pool."""
    preview = preview_feasibility(
        ARRAYS, mandate(risk_appetite=RiskAppetite.LOW), ASSUMPTIONS, excluded_ids=["P01"]
    )
    codes = [signal.code for signal in preview.signals]
    assert codes == [WarningCode.NO_CANDIDATES, WarningCode.LOCKS_PRESENT]


def test_locks_exceeding_capital_block_the_run() -> None:
    """§13 and C-9: the second of exactly two blocking conditions."""
    preview = preview_feasibility(
        ARRAYS, mandate(available_capital_eur=1.0e6), ASSUMPTIONS, locked_ids=["P01"]
    )
    codes = [signal.code for signal in preview.signals]
    assert WarningCode.LOCKS_EXCEED_CAPITAL in codes
    assert not preview.runnable


def test_only_the_two_blocking_codes_disable_a_run() -> None:
    preview = preview_feasibility(ARRAYS, mandate(capacity_target_mw=4000.0), ASSUMPTIONS)
    assert WarningCode.CAPACITY_BELOW_TARGET in [s.code for s in preview.signals]
    assert preview.runnable, "an advisory warning must not disable the run (§5.4)"


def test_warnings_come_out_in_the_spec_severity_order() -> None:
    """A-5: the specification's order, not the mockup's."""
    preview = preview_feasibility(
        ARRAYS,
        mandate(capacity_target_mw=4000.0, min_leverage=0.85, solar_share=1.0),
        ASSUMPTIONS,
        locked_ids=["P01"],
    )
    codes = [signal.code for signal in preview.signals]
    assert codes == sorted(codes)
    assert codes.index(WarningCode.LEVERAGE_UNREACHABLE) < codes.index(
        WarningCode.SOLAR_MIX_UNREACHABLE
    )
    assert codes.index(WarningCode.SOLAR_MIX_UNREACHABLE) < codes.index(
        WarningCode.CAPITAL_UNDERUSED
    )


def test_the_solar_warning_is_one_sided() -> None:
    """A-22. A pool with *more* solar than the target can still reach it."""
    codes_when_short = [
        signal.code
        for signal in preview_feasibility(ARRAYS, mandate(solar_share=1.0), ASSUMPTIONS).signals
    ]
    codes_when_over = [
        signal.code
        for signal in preview_feasibility(ARRAYS, mandate(solar_share=0.0), ASSUMPTIONS).signals
    ]
    assert WarningCode.SOLAR_MIX_UNREACHABLE in codes_when_short
    assert WarningCode.SOLAR_MIX_UNREACHABLE not in codes_when_over


def test_capital_underuse_fires_on_its_own() -> None:
    """The mockup gated this on capacity also being missed; §5.4 does not."""
    preview = preview_feasibility(
        ARRAYS, mandate(available_capital_eur=4_000e6, capacity_target_mw=200.0), ASSUMPTIONS
    )
    codes = [signal.code for signal in preview.signals]
    assert WarningCode.CAPITAL_UNDERUSED in codes
    assert WarningCode.CAPACITY_BELOW_TARGET not in codes


def test_a_signal_carries_numbers_in_euros_and_no_rendered_message() -> None:
    """Formatting money inside the numeric core would be a third unit boundary."""
    preview = preview_feasibility(ARRAYS, mandate(available_capital_eur=4_000e6), ASSUMPTIONS)
    signal = next(s for s in preview.signals if s.code == WarningCode.CAPITAL_UNDERUSED)
    assert not hasattr(signal, "message")
    assert signal.detail["availableCapital"] == pytest.approx(4_000e6)
    assert signal.detail["absorbedShare"] < 1.0
