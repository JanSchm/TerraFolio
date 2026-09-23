"""The CLI's two jobs beyond printing: validating a mandate, and refusing a bad run.

`terrafolio run` is the only thing in 2A that takes user input, so it is the only
thing that can be handed a mandate §5 does not permit or a lock set §13 says blocks
the run. Both used to get through — one as a traceback from deep in the economics
layer, the other as a portfolio silently missing the locks that were asked for, which
is the worse of the two.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from terrafolio.cli import (
    MandateError,
    _mandate_from,
    build_parser,
    main,
    multiple,
    percentage,
)

FIXTURES = Path(__file__).resolve().parents[1] / "golden" / "fixtures" / "pipeline"


def _args(*argv: str) -> object:
    return build_parser().parse_args(["--pipeline", str(FIXTURES), *argv])


def _run(*argv: str) -> int:
    return main(["--pipeline", str(FIXTURES), "run", "--effort", "fast", "--seed", "42", *argv])


# ---------------------------------------------------------------------------
# A mandate the specification does not permit
# ---------------------------------------------------------------------------


def test_the_defaults_make_a_valid_mandate() -> None:
    """If this fails, every negative test below is passing for the wrong reason."""
    mandate = _mandate_from(_args("run"))  # type: ignore[arg-type]
    assert mandate.hold_years == 10
    assert mandate.available_capital_eur == pytest.approx(1_200e6)


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--hold", "31"),
        ("--hold", "4"),
        ("--capital", "10"),
        ("--capital", "9000"),
        ("--hurdle", "0.9"),
        ("--min-leverage", "1.5"),
        ("--min-dscr", "0.5"),
        ("--max-project", "0.01"),
        ("--target", "10"),
    ],
)
def test_a_control_outside_its_published_range_is_refused(flag: str, value: str) -> None:
    """§5's ranges belong to the pydantic ``Mandate``, which is now what validates.

    ``MandateScalars`` is a reduction, not a contract — it validates nothing — so
    building it straight from ``argparse`` let these through to the numeric core.
    """
    with pytest.raises(MandateError):
        _mandate_from(_args("run", flag, value))  # type: ignore[arg-type]


def test_an_inverted_cod_window_is_refused() -> None:
    """A cross-field rule, which no per-argument type could have caught."""
    with pytest.raises(MandateError, match="codFrom"):
        _mandate_from(_args("run", "--cod-from", "2032", "--cod-to", "2027"))  # type: ignore[arg-type]


def test_a_malformed_country_code_is_refused() -> None:
    """``argparse`` sees a string; only the model knows it must be alpha-2 upper.

    An empty ``--countries`` is not tested here because ``nargs="+"`` makes argparse
    reject it first — the parser and the model each catch what they can see.
    """
    with pytest.raises(MandateError):
        _mandate_from(_args("run", "--countries", "esp"))  # type: ignore[arg-type]


def test_the_error_names_the_field_rather_than_the_stack() -> None:
    """These messages are read by someone editing a command line, not a stack."""
    with pytest.raises(MandateError) as raised:
        _mandate_from(_args("run", "--hold", "31"))  # type: ignore[arg-type]
    assert "hold_years" in str(raised.value) or "holdYears" in str(raised.value)


def test_an_out_of_range_mandate_exits_cleanly_rather_than_raising(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _run("--hold", "31") == 2
    assert "error:" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# §13's two blocking conditions
# ---------------------------------------------------------------------------


def test_locks_whose_equity_exceeds_the_budget_refuse_the_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The failure this replaces was silent, which is why it matters.

    The repair operator drops holdings once their cumulative equity exceeds the
    budget. With locks alone over the cap it dropped the locked ones, so the command
    printed a perfectly plausible portfolio that simply did not contain the projects
    the user had required. §13 says block the run and say which locks to release.
    """
    assert _run("--capital", "200", "--lock", "P45", "P46", "P47", "P48") == 2
    message = capsys.readouterr().err
    assert "release a lock" in message
    assert "€200m" in message


def test_a_mandate_no_candidate_passes_refuses_the_run_and_names_the_screens(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _run("--countries", "FI", "--min-dscr", "1.95") == 2
    message = capsys.readouterr().err
    assert "no candidate passes" in message
    assert "minDscr" in message


def test_an_advisory_warning_does_not_refuse_the_run(capsys: pytest.CaptureFixture[str]) -> None:
    """§5.4: the user may run an infeasible-looking mandate and see how close it gets."""
    assert _run("--target", "4000") == 0
    assert "Selected" in capsys.readouterr().out


def test_a_run_that_keeps_its_locks_still_succeeds(capsys: pytest.CaptureFixture[str]) -> None:
    """The blocking check must not fire on locks the budget can actually carry."""
    assert _run("--lock", "P01") == 0
    printed = capsys.readouterr().out
    assert "P01" in printed


# ---------------------------------------------------------------------------
# The other subcommands still work
# ---------------------------------------------------------------------------


def test_pipeline_validate_reports_a_clean_corpus(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--pipeline", str(FIXTURES), "pipeline", "validate"]) == 0
    printed = capsys.readouterr().out
    assert "Loaded 48 of 48 files" in printed
    assert "Every file passed every tie-out." in printed


def test_preview_reports_the_footer_figures(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--pipeline", str(FIXTURES), "preview"]) == 0
    printed = capsys.readouterr().out
    assert "33 of 48 candidates pass the screens" in printed
    assert "Runnable: yes" in printed


def test_preview_returns_non_zero_when_the_mandate_cannot_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--pipeline", str(FIXTURES), "preview", "--countries", "FI", "--min-dscr", "1.95"])
    assert "Runnable: no" in capsys.readouterr().out


def test_run_prints_all_twelve_tiles(capsys: pytest.CaptureFixture[str]) -> None:
    """Acceptance criterion 10, as a test rather than as a transcript."""
    assert _run() == 0
    printed = capsys.readouterr().out
    for label in (
        "Installed capacity",
        "Projects",
        "Technology split",
        "Equity required",
        "Total project cost",
        "Equity IRR",
        "Leverage",
        "Weighted LCOE",
        "Annual generation",
        "30-year FCFE",
        "Merchant exposure",
        "Largest country",
    ):
        assert label in printed


def test_an_undefined_figure_renders_as_an_em_dash_never_as_zero() -> None:
    """§14, and the last link in the NaN to null to em dash chain."""
    assert percentage(None) == "\u2014"
    assert multiple(None) == "\u2014"
    assert multiple(1.375) == "1.38\u00d7"
    assert percentage(0.1238) == "12.4%"
