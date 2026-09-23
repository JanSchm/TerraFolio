"""The commands, and what they refuse to do.

The interesting behaviour of a generator is not the happy path -- it is what it
does when the data is wrong. These cover the four ways this one used to ship
something it should not have: a regeneration that left stale projects behind, an
ingest that accepted a file disagreeing with itself, an export that turned
project text into an Excel formula, and a resample that gave up and said it had
succeeded.

Each asserts the **exit status** and that **nothing was written**, because a
message on stderr that is followed by a written file is not a refusal.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any, Final

import pytest
from openpyxl import load_workbook

from terrafolio.cli import main
from terrafolio.generate.spreadsheet import write_workbook

REPO: Final = Path(__file__).resolve().parents[2]
TEMPLATE: Final = REPO / "templates" / "project-template.json"


@pytest.fixture
def template() -> dict[str, Any]:
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


def files_in(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.glob("*.json")) if directory.exists() else []


# --------------------------------------------------------------------------
# generate
# --------------------------------------------------------------------------


def _recalibrated(tmp_path: Path, *, low: float, high: float) -> Path:
    """A calibration directory whose min-DSCR band is one no project can reach.

    Edited through `tomllib`/round-trip rather than by string surgery, and the
    result is read back and asserted, so a replacement that silently missed
    would fail here instead of making the test below pass for the wrong reason.
    """
    directory = tmp_path / "assumptions"
    directory.mkdir()
    text = (REPO / "assumptions" / "default-2026.toml").read_text(encoding="utf-8")
    marker = "[validation.min_dscr_band]"
    head, _, tail = text.partition(marker)
    _band, _, rest = tail.partition("\n\n")
    replaced = f"{head}{marker}\nlow = {low}\nhigh = {high}\n\n{rest}"
    (directory / "default-2026.toml").write_text(replaced, encoding="utf-8")

    band = tomllib.loads(replaced)["validation"]["min_dscr_band"]
    assert (band["low"], band["high"]) == (low, high), band
    return directory


def test_generate_writes_a_pipeline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        main(["pipeline", "generate", "--count", "8", "--seed", "1", "--out", str(tmp_path)]) == 0
    )
    assert len(files_in(tmp_path)) == 8
    assert "wrote 8 project files" in capsys.readouterr().out


def test_generate_is_idempotent(tmp_path: Path) -> None:
    argv = ["pipeline", "generate", "--count", "8", "--seed", "1", "--out", str(tmp_path)]
    assert main(argv) == 0
    first = {path.name: path.read_text(encoding="utf-8") for path in tmp_path.glob("*.json")}
    assert main(argv) == 0
    assert {
        path.name: path.read_text(encoding="utf-8") for path in tmp_path.glob("*.json")
    } == first


def test_generate_refuses_a_non_positive_count(tmp_path: Path) -> None:
    assert main(["pipeline", "generate", "--count", "0", "--out", str(tmp_path)]) == 2
    assert files_in(tmp_path) == []


def test_generate_refuses_to_write_when_a_project_cannot_be_placed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exhausted resampling used to be reported as success.

    Every redraw failing and no redraw being needed both leave a defensible
    attempt count, and the CLI counted any multi-attempt project as "redrawn to
    bring min DSCR inside its plausibility band" -- which was simply false when
    the band could not be met. It then wrote the pipeline and exited zero.
    """
    calibration = _recalibrated(tmp_path, low=1.90, high=2.50)
    monkeypatch.setenv("TERRAFOLIO_ASSUMPTIONS_DIR", str(calibration))

    out = tmp_path / "pipeline"
    assert main(["pipeline", "generate", "--count", "12", "--seed", "1", "--out", str(out)]) == 1
    assert files_in(out) == [], "a refusal must not write anything"
    assert "refusing to write" in capsys.readouterr().err


# --------------------------------------------------------------------------
# ingest
# --------------------------------------------------------------------------


def test_ingest_accepts_a_coherent_workbook(
    template: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = write_workbook(template, tmp_path / "p.xlsx")
    out = tmp_path / "ingested"
    assert main(["pipeline", "ingest", str(book), "--out", str(out)]) == 0
    assert files_in(out) == ["P01.json"]
    assert "reproduces this file on every line" in capsys.readouterr().out


def test_ingest_refuses_a_workbook_that_disagrees_with_itself(
    template: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """§7's tie-outs are blocking: "a file that fails any of these does not load".

    `ProjectFile` checks shape, domain and the reject-derived rule. It does not
    check that the statements agree with each other, so a workbook of entirely
    plausible positive numbers used to be written as canonical pipeline data.
    """
    broken = json.loads(json.dumps(template))
    broken["statements"]["incomeStatement"]["ebitda"][5] += 5.0
    book = write_workbook(broken, tmp_path / "broken.xlsx")
    out = tmp_path / "ingested"

    assert main(["pipeline", "ingest", str(book), "--out", str(out)]) == 1
    assert files_in(out) == [], "a file that fails a tie-out must not be written"
    captured = capsys.readouterr().err
    assert "tie-out failure" in captured
    assert "ebitda = revenue - opex" in captured
    assert "year index 5" in captured


def test_ingest_keeps_a_house_model_variance_advisory(
    template: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The distinction that matters (A-7, Q-4).

    A changed capacity factor makes the house model disagree while the
    statements stay coherent. That is the analyst's call to make, so the file is
    written and the variance is reported -- not refused.
    """
    varied = json.loads(json.dumps(template))
    varied["asset"]["netCapacityFactor"] *= 1.2
    book = write_workbook(varied, tmp_path / "varied.xlsx")
    out = tmp_path / "ingested"

    assert main(["pipeline", "ingest", str(book), "--out", str(out)]) == 0
    assert files_in(out) == ["P01.json"]
    assert "the house model differs on" in capsys.readouterr().out


def test_ingest_refuses_a_missing_file(tmp_path: Path) -> None:
    assert main(["pipeline", "ingest", str(tmp_path / "nope.xlsx"), "--out", str(tmp_path)]) == 2


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------


def test_export_round_trips_through_ingest(tmp_path: Path) -> None:
    source = tmp_path / "pipeline"
    assert main(["pipeline", "generate", "--count", "6", "--seed", "1", "--out", str(source)]) == 0
    identifier = json.loads(sorted(source.glob("*.json"))[0].read_text(encoding="utf-8"))["id"]

    book = tmp_path / "exported.xlsx"
    assert (
        main(
            [
                "pipeline",
                "export",
                "--id",
                identifier,
                "--pipeline",
                str(source),
                "--out",
                str(book),
                "--xlsx",
            ]
        )
        == 0
    )
    out = tmp_path / "back"
    assert main(["pipeline", "ingest", str(book), "--out", str(out)]) == 0

    original = next(
        json.loads(path.read_text(encoding="utf-8"))
        for path in source.glob("*.json")
        if json.loads(path.read_text(encoding="utf-8"))["id"] == identifier
    )
    assert json.loads((out / f"{identifier}.json").read_text(encoding="utf-8")) == original


def test_export_refuses_an_unknown_id(tmp_path: Path) -> None:
    source = tmp_path / "pipeline"
    assert main(["pipeline", "generate", "--count", "3", "--out", str(source)]) == 0
    assert main(["pipeline", "export", "--id", "NOPE", "--pipeline", str(source), "--xlsx"]) == 1


def test_export_requires_a_named_format(tmp_path: Path) -> None:
    """There is one format, and it is still stated rather than assumed."""
    source = tmp_path / "pipeline"
    assert main(["pipeline", "generate", "--count", "3", "--out", str(source)]) == 0
    identifier = json.loads(sorted(source.glob("*.json"))[0].read_text(encoding="utf-8"))["id"]
    assert main(["pipeline", "export", "--id", identifier, "--pipeline", str(source)]) == 2


def test_export_writes_no_formula_from_project_text(tmp_path: Path) -> None:
    """The injection path, end to end through the command an analyst runs."""
    source = tmp_path / "pipeline"
    source.mkdir()
    poisoned = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    poisoned["name"] = '=HYPERLINK("http://attacker.example","x")'
    (source / "P01-poisoned.json").write_text(json.dumps(poisoned), encoding="utf-8")

    book = tmp_path / "out.xlsx"
    assert (
        main(
            [
                "pipeline",
                "export",
                "--id",
                "P01",
                "--pipeline",
                str(source),
                "--out",
                str(book),
                "--xlsx",
            ]
        )
        == 0
    )
    sheet = load_workbook(book)["Identity"]
    assert sheet.cell(4, 2).data_type == "s"
    assert sheet.cell(4, 2).value == poisoned["name"]
