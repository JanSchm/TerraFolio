"""The offline pack: self-contained, complete, and faithful to the document.

§12 wants "a self-contained file that opens without network access", and
``docs/api.md`` §9.2 is explicit that a pack which fetches anything fails it. So
the first test here is the one that matters: after stripping comments, nothing in
the bytes points anywhere.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import GOLDEN_PIPELINE, REPO_ROOT, mandate, settings_for, start_run

from terrafolio.api.app import create_app
from terrafolio.api.service import build_service
from terrafolio.api.wire import OptimisationRequest
from terrafolio.domain.reduce import mandate_to_scalars
from terrafolio.export.committee import (
    CommitteePackUnavailableError,
    Mercator,
    committee_pack,
    layout,
)
from terrafolio.optimiser.feasibility import preview_feasibility
from terrafolio.store.runs import load_run

UI_CONTRACT = (REPO_ROOT / "docs" / "ui-contract.md").read_text(encoding="utf-8")
CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
DATA_URI = re.compile(r"data:[^)'\"\s]*")
VENDOR = REPO_ROOT / "web" / "vendor"


@pytest.fixture
async def packed(client: httpx.AsyncClient) -> bytes:
    """A pack for a real run over the golden pipeline."""
    accepted = await start_run(client)
    response = await client.get(f"/optimisations/{accepted['runId']}/pack")
    if response.status_code != 200:
        pytest.skip(f"pack unavailable: {response.json()['error']['message']}")
    assert response.headers["content-type"].startswith("text/html")
    return response.content


def _stripped(document: str) -> str:
    """The document with CSS and HTML comments removed.

    Tailwind's preflight quotes its own documentation URL inside a comment. A
    URL in a comment is not a reference and nothing fetches it, but a naive
    search for ``http`` would flag it — so the assertion is made against the
    text a browser would actually act on.
    """
    return HTML_COMMENT.sub("", CSS_COMMENT.sub("", document))


def _addressable(document: str) -> str:
    """The stripped document with ``data:`` payloads removed as well.

    Base64 is base64: 166 KB of encoded font contains every two-character
    sequence there is, including ``//``, so scanning it for URL shapes finds
    them and means nothing. What is left is the text that could address
    something.
    """
    return DATA_URI.sub("", _stripped(document))


def test_the_pack_has_no_external_reference_of_any_kind(packed: bytes) -> None:
    """The §12 requirement, asserted three ways.

    Attributes that fetch, ``url()`` in the styles, and — after comments are
    stripped — any absolute or protocol-relative URL at all.
    """
    document = packed.decode("utf-8")
    body = _stripped(document)

    for attribute in ("src", "href", "action", "data", "poster", "srcset"):
        for value in re.findall(rf'\b{attribute}="([^"]*)"', body):
            assert value.startswith("data:"), f"{attribute}={value!r} leaves the file"

    for url in re.findall(r"url\(\s*['\"]?([^)'\"]+)", body):
        assert url.startswith("data:"), f"url({url!r}) leaves the file"

    addressable = _addressable(document)
    assert "http://" not in addressable
    assert "https://" not in addressable
    assert not re.search(r"(?<![a-zA-Z:])//[a-zA-Z]", addressable), "a protocol-relative URL"
    assert "<script" not in body, "the pack runs no JavaScript"
    assert "@import" not in body


def test_the_pack_inlines_every_font(packed: bytes) -> None:
    document = packed.decode("utf-8")
    subsets = list((REPO_ROOT / "web" / "fonts").glob("*.woff2"))
    assert subsets, "no Barlow subsets vendored"
    assert document.count("data:font/woff2;base64,") == len(subsets)
    assert ".woff2" not in _addressable(document)


def test_the_pack_renders_every_tile_both_charts_and_the_map(packed: bytes) -> None:
    """The acceptance criterion, element by element."""
    document = packed.decode("utf-8")
    assert document.count('class="tile tile--') == 12, "§5.1's twelve tiles"
    assert document.count("<rect") == 30, "§5.2's thirty bars, one per year"
    assert document.count("<polyline") == 2, "§6's best and mean curves"
    assert document.count("<path") > 100, "the Natural Earth base map"
    assert "<circle" in document, "a marker per selected site"
    assert "@media print" in document, "the print stylesheet"
    assert "Investment committee sheet" in document
    for heading in ("Holdings", "Mandate", "Provenance", "Selected sites"):
        assert f">{heading}<" in document


def test_the_pack_carries_the_run_reference_and_the_mandate(packed: bytes) -> None:
    """§7.7: all three exports carry the run reference and the mandate."""
    document = packed.decode("utf-8")
    assert "run A-" in document
    assert "Equity available" in document
    assert "Capacity target" in document
    assert "Hold period" in document


def test_the_pack_never_encodes_status_by_colour_alone(packed: bytes) -> None:
    """§12 and ui-contract §7.1: every verdict carries a mark and a word."""
    document = packed.decode("utf-8")
    assert 'class="sr-only"' in document
    marked = re.findall(r'class="tile tile--(compliant|alert)"', document)
    assert marked, "no tile carried a verdict"
    assert "on target" in document or "outside the mandate" in document


def test_an_undefined_figure_is_an_em_dash_and_never_a_zero(packed: bytes) -> None:
    document = packed.decode("utf-8")
    assert "0.00\u00d7 MOIC" not in document
    # The dash is available for the cases that need it.
    assert "—" in document


async def test_the_pack_survives_a_project_deleted_from_the_pipeline(
    tmp_path: Path,
) -> None:
    """§8.2: the run's own ``holdings`` are the pack's only data source.

    A project removed from the directory after the run still appears, with the
    figures the run saw — which is what makes a stored run self-contained.
    """
    directory = tmp_path / "pipeline"
    shutil.copytree(GOLDEN_PIPELINE, directory)
    settings = settings_for(tmp_path, pipeline=directory)
    service = build_service(settings)
    try:
        app = create_app(settings, service=service)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                accepted = await start_run(client)
                selected = (await client.get(f"/optimisations/{accepted['runId']}")).json()[
                    "selectedIds"
                ]
                assert selected
                for path in directory.glob("*.json"):
                    if json.loads(path.read_text())["id"] == selected[0]:
                        path.unlink()
                assert (await client.post("/pipeline/reload")).status_code == 200
                assert selected[0] not in {
                    row["id"] for row in (await client.get("/pipeline")).json()["projects"]
                }
                pack = await client.get(f"/optimisations/{accepted['runId']}/pack")
        assert pack.status_code == 200
        assert selected[0] in pack.text
    finally:
        service.shutdown()


def test_a_missing_stylesheet_is_named_rather_than_silently_skipped(tmp_path: Path) -> None:
    """``web/dist/app.css`` is a build artefact and ``web/.gitignore`` ignores it.

    A clean checkout has none until ``npm --prefix web run build`` has run, and
    a pack served unstyled would be a worse answer than an error naming the
    command.
    """
    settings = settings_for(tmp_path)
    service = build_service(settings)
    try:
        stored = _any_stored_run(service, settings)
        with pytest.raises(CommitteePackUnavailableError, match="npm --prefix web run build"):
            committee_pack(
                stored,
                stylesheet=tmp_path / "nowhere" / "app.css",
                fonts_dir=settings.fonts_dir,
                atlas=settings.atlas_path,
            )
    finally:
        service.shutdown()


def test_a_missing_atlas_degrades_to_a_notice(tmp_path: Path) -> None:
    """§13: the map panel degrades; the rest of the page is unaffected."""
    settings = settings_for(tmp_path)
    service = build_service(settings)
    try:
        stored = _any_stored_run(service, settings)
        document = committee_pack(
            stored,
            stylesheet=settings.stylesheet_path,
            fonts_dir=settings.fonts_dir,
            atlas=tmp_path / "no-atlas.json",
        ).decode("utf-8")
    except CommitteePackUnavailableError as missing:
        pytest.skip(f"pack unavailable: {missing}")
    finally:
        service.shutdown()
    assert "Map data unavailable." in document
    assert document.count('class="tile tile--') == 12, "the rest of the page is unaffected"


def _any_stored_run(service: Any, settings: Any) -> Any:
    """One succeeded run, produced through the real engine, for a pure-function test."""

    request = OptimisationRequest.model_validate(
        {"mandate": mandate(), "effort": "fast", "seed": 5}
    )
    preview = _preview_for(service, request)
    pending = service.submit(request, preview)
    with closing(service.connect()) as connection:
        return load_run(connection, run_id=pending.run_id)


def _preview_for(service: Any, request: Any) -> Any:
    return preview_feasibility(
        service.source.candidates.arrays,
        mandate_to_scalars(request.mandate),
        service.assumptions,
        locked_ids=request.locked_ids,
        excluded_ids=request.excluded_ids,
    )


# --------------------------------------------------------------------------
# pack-layout.json is a quotation, so it is checked against what it quotes
# --------------------------------------------------------------------------


def _document_colour(token: str) -> str:
    match = re.search(rf"\|\s*`--color-{token}`\s*\|\s*`(#[0-9a-f]{{6}})`", UI_CONTRACT)
    assert match, f"--color-{token} is not in ui-contract §1.1"
    return match.group(1)


def _ramp(step: int) -> tuple[str, str]:
    match = re.search(
        rf"^\|\s*{step}\s*\|\s*`(#[0-9a-f]{{6}})`\s*\|\s*`(#[0-9a-f]{{6}})`", UI_CONTRACT, re.M
    )
    assert match, f"no ramp row for step {step}"
    return match.group(1), match.group(2)


def test_the_palette_matches_ui_contract() -> None:
    """The pack declares §1.1's tokens itself, so they are checked against §1.1.

    The compiled Tailwind stylesheet resolves its tokens into utility classes
    and emits no custom properties, so the pack cannot read them from it.
    """
    colour = layout()["colour"]
    for name, token in (
        ("bg", "bg"),
        ("surface", "surface"),
        ("text", "text"),
        ("accent", "accent"),
    ):
        assert colour[name] == _document_colour(token), name
    for step in (200, 300, 400, 700, 800):
        neutral, accent = _ramp(step)
        if f"neutral{step}" in colour:
            assert colour[f"neutral{step}"] == neutral, f"neutral {step}"
        if f"accent{step}" in colour:
            assert colour[f"accent{step}"] == accent, f"accent {step}"


def test_the_map_geometry_matches_ui_contract() -> None:
    spec = layout()["map"]
    assert re.search(r"scale `width \u00d7 1\.15`", UI_CONTRACT)
    assert spec["scaleFactor"] == 1.15
    assert re.search(r"centre `\[12, 55\]`", UI_CONTRACT)
    assert (spec["centreLon"], spec["centreLat"]) == (12, 55)
    assert re.search(r"`r = max\(3, \u221aMW \u00d7 0\.42\)`", UI_CONTRACT)
    assert (spec["markerRadiusFloor"], spec["markerRadiusFactor"]) == (3, 0.42)
    assert "fill-opacity 0.82" in UI_CONTRACT
    assert spec["markerFillOpacity"] == 0.82
    assert "stroke at 0.6px" in UI_CONTRACT
    assert spec["countryStroke"] == 0.6


def test_the_compliance_bands_match_ui_contract() -> None:
    bands = layout()["compliance"]
    assert "within 8% of target" in UI_CONTRACT
    assert "within 8 points of target" in UI_CONTRACT
    assert bands["capacityBand"] == 0.08
    assert bands["techSplitBand"] == 0.08


# --------------------------------------------------------------------------
# The projection, against the library it reimplements
# --------------------------------------------------------------------------

_D3_HARNESS = """
import fs from 'node:fs';
function load(path, req) {
  const mod = { exports: {} };
  const body = fs.readFileSync(path, 'utf8');
  new Function('module', 'exports', 'require', body)(mod, mod.exports, req);
  return mod.exports;
}
const d3array = load(process.argv[2], () => { throw new Error('no deps'); });
const d3 = load(process.argv[3], (name) => {
  if (name === 'd3-array') return d3array;
  throw new Error('unexpected dependency ' + name);
});
const [W, H, lon, lat, scaleFactor] = process.argv.slice(4).map(Number);
const p = d3.geoMercator().center([lon, lat]).scale(W * scaleFactor).translate([W / 2, H / 2]);
console.log(JSON.stringify(JSON.parse(process.argv[9]).map((q) => p(q))));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.skipif(not VENDOR.is_dir(), reason="d3-geo is not vendored")
def test_the_projection_agrees_with_d3_geo(tmp_path: Path) -> None:
    """The pack reimplements ``d3.geoMercator``; this is what stops it drifting.

    Inlining 36 KB of d3-geo, 17 KB of d3-array and a 108 KB atlas into every
    pack — and then needing a script to run before the map appeared — costs more
    than sixty lines of projection. But "reimplemented" is only defensible if it
    is pinned to the real thing.
    """
    spec = layout()["map"]
    width, height = spec["width"], spec["height"]
    points = [[12, 55], [-8.9, 36.2], [30, 60], [-10, 84], [0, 0], [25, -20], [179, 70]]
    script = tmp_path / "d3check.mjs"
    script.write_text(_D3_HARNESS, encoding="utf-8")
    result = subprocess.run(
        [
            "node",
            str(script),
            str(VENDOR / "d3-array.min.js"),
            str(VENDOR / "d3-geo.min.js"),
            str(width),
            str(height),
            str(spec["centreLon"]),
            str(spec["centreLat"]),
            str(spec["scaleFactor"]),
            json.dumps(points),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    expected = json.loads(result.stdout)
    project = Mercator(
        scale=width * spec["scaleFactor"],
        centre_lon=spec["centreLon"],
        centre_lat=spec["centreLat"],
        translate_x=width / 2,
        translate_y=height / 2,
    )
    for (lon, lat), (x, y) in zip(points, expected, strict=True):
        got_x, got_y = project(lon, lat)
        assert got_x == pytest.approx(x, abs=1e-9)
        assert got_y == pytest.approx(y, abs=1e-9)


def test_every_colour_token_the_pack_uses_is_declared(packed: bytes) -> None:
    """A ``var()`` naming a property nothing declares falls back silently.

    That is not a cosmetic failure: an undeclared ``fill`` resolves to its
    initial value, which is **black**, so the whole map renders as a solid
    block and every assertion about paths and markers still passes. This test
    exists because that happened.
    """
    document = packed.decode("utf-8")
    used = set(re.findall(r"var\(\s*(--pack-[a-zA-Z0-9-]+)", document))
    declared = set(re.findall(r"(--pack-[a-zA-Z0-9-]+)\s*:", document))
    assert used, "the pack stopped using its own tokens"
    assert used <= declared, f"undeclared: {sorted(used - declared)}"


def test_the_map_is_not_one_flat_colour(packed: bytes) -> None:
    """Held countries and the rest are different fills (ui-contract §5.3)."""
    document = packed.decode("utf-8")
    fills = set(re.findall(r'<path d="[^"]+" fill="([^"]+)"', document))
    assert fills == {"var(--pack-accent200)", "var(--pack-neutral200)"}, fills
    markers = set(re.findall(r'<circle [^>]*fill="(var\([^)]+\))"', document))
    assert markers <= {"var(--pack-accent700)", "var(--pack-accent400)"}
    assert markers, "no site markers"
