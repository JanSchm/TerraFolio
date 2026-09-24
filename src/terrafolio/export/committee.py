"""The offline committee pack: one file that opens with no network at all.

§12 requires the result view to export to "a self-contained file that opens
without network access, for committee packs", and ``docs/api.md`` §9.2 is
explicit that a pack which fetches anything fails it. So: styles inlined, fonts
as base64, the map geometry projected and emitted as SVG paths, the data drawn
into the markup, and **no external reference of any kind**.

**No JavaScript either.** Three reasons, in order of weight. A pack opens from
``file://``, where a module script and ``fetch`` are both CORS-blocked (A-15), so
anything dynamic would have to be a classic inline script over inlined data.
§7.7's committee output is a *print*, and a document whose content appears only
after a script has run is a document that prints differently depending on when
the dialog opened. And #11's ``portfolio.js``, ``charts.js`` and ``map.js`` do
not exist yet — a pack that inlined them could not be built, let alone tested.
So the tiles, both charts, the map and the table are rendered here, server-side,
from the stored run alone.

**The run's own ``holdings`` array is the only data source.** It carries every
candidate the run saw, with the coordinates the map needs and the figures the
table shows, which is what makes a stored run self-contained (§8.2). A project
deleted from the pipeline after the run still appears in its pack.

Numbers go through ``csv.py``'s formatters, so a figure reads the same in the
pack, in the export and on the screen. Colours, bands and geometry come from
``pack-layout.json``, which quotes ``ui-contract.md``; a display constant written
into this file would be one nobody could audit against the document it came from.
"""

from __future__ import annotations

import base64
import datetime as dt
import html
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from terrafolio.domain.enums import Technology
from terrafolio.domain.results import Holding, RunRecord
from terrafolio.export.csv import (
    PERCENT_SCALE,
    SEPARATOR,
    money_m,
    multiple,
    percent,
    quantity,
)
from terrafolio.store.records import StoredRun

__all__ = [
    "CommitteePackUnavailableError",
    "committee_pack",
    "layout",
]

EM_DASH: Final = "—"
"""§13: an undefined figure is an em dash, never a zero and never a blank."""

LAYOUT_FILE: Final = Path(__file__).with_name("pack-layout.json")
FONT_PATTERN: Final = re.compile(r"url\(\s*(?:'|\")?([^)'\"]+\.woff2)(?:'|\")?\s*\)")


class CommitteePackUnavailableError(RuntimeError):
    """A file the pack must inline is not on disk.

    Named rather than generic so the endpoint can say *which* file and what to
    run. ``web/dist/app.css`` is a Tailwind build artefact and ``web/.gitignore``
    ignores ``dist/``, so a clean checkout has no stylesheet until
    ``npm --prefix web run build`` has produced one — and a pack served without
    it would be a worse answer than an error that names the command.
    """

    def __init__(self, path: Path, remedy: str) -> None:
        super().__init__(f"{path} is missing; {remedy}")
        self.path = path


def layout() -> Mapping[str, Any]:
    """``pack-layout.json``, the pack's quotation of ``ui-contract.md``."""
    parsed: Mapping[str, Any] = json.loads(LAYOUT_FILE.read_text(encoding="utf-8"))
    return parsed


# --------------------------------------------------------------------------
# Styles and fonts
# --------------------------------------------------------------------------


def _font_data_uri(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:font/woff2;base64,{encoded}"


def inline_fonts(css: str, fonts_dir: Path) -> str:
    """Rewrite every ``url(../fonts/*.woff2)`` to a base64 ``data:`` URI.

    The ten Barlow subsets are about 122 KB on disk and 166 KB encoded, which is
    the price of the pack looking like the application rather than like whatever
    the reader's machine substitutes.
    """

    def replace(match: re.Match[str]) -> str:
        name = Path(match.group(1)).name
        source = fonts_dir / name
        if not source.is_file():
            raise CommitteePackUnavailableError(
                source, "run `npm --prefix web run vendor` to fetch the Barlow subsets"
            )
        return f"url({_font_data_uri(source)})"

    return FONT_PATTERN.sub(replace, css)


def _stylesheet(stylesheet: Path, fonts_dir: Path) -> str:
    if not stylesheet.is_file():
        raise CommitteePackUnavailableError(
            stylesheet, "run `npm --prefix web run build` to compile the stylesheet"
        )
    return inline_fonts(stylesheet.read_text(encoding="utf-8"), fonts_dir)


def _tokens(colour: Mapping[str, Any]) -> str:
    """``ui-contract.md`` §1.1's palette as custom properties.

    The compiled Tailwind stylesheet resolves its tokens into utility classes and
    emits no custom properties, so the pack — which uses its own semantic
    class names rather than 1D's utilities — declares them itself, from the
    document that defines their values.
    """
    divider = f"color-mix(in srgb, {colour['text']} {_pct(colour['dividerAlpha'])}, transparent)"
    named = "\n".join(
        f"  --pack-{name}: {value};" for name, value in colour.items() if isinstance(value, str)
    )
    # Muted is `neutral-700`, not ink at 55%. §7.3 was corrected by #10: 55%
    # measures 3.64:1 against the page ground and so misses §12's 4.5:1 floor,
    # where `neutral-700` clears it at 5.87:1. The pack uses muted for every
    # eyebrow, sub-label and caption — including the words that carry a tile's
    # compliance verdict — so it is the one token here that is a legibility
    # obligation rather than a preference.
    return (
        f":root {{\n{named}\n"
        f"  --pack-divider: {divider};\n"
        f"  --pack-muted: {colour['neutral700']};\n}}"
    )


def _pct(fraction: float) -> str:
    return f"{fraction:.0%}"


# --------------------------------------------------------------------------
# The Mercator projection ui-contract §5.3 specifies
# --------------------------------------------------------------------------


MERCATOR_LIMIT: Final = math.degrees(math.atan(math.sinh(math.pi)))
"""The latitude Mercator can represent, in degrees — about 85.05°.

``atan(sinh(pi))`` is where the square projection's edge falls. Derived rather
than written down: it is a property of the projection, not a value anyone chose.
"""


def _raw(lon: float, lat: float) -> tuple[float, float]:
    """The unscaled Mercator of one coordinate, in radians.

    Spelled as d3 spells it — ``log(tan((halfPi + phi) / 2))`` rather than the
    algebraically identical ``log(tan(pi/4 + phi/2))`` — so the two agree in the
    last bits and not merely to within a rounding.
    """
    clamped = max(-MERCATOR_LIMIT, min(MERCATOR_LIMIT, lat))
    phi = math.radians(clamped)
    return math.radians(lon), math.log(math.tan((math.pi / 2 + phi) / 2))


@dataclass(frozen=True, slots=True)
class Mercator:
    """``d3.geoMercator``, to the arithmetic d3 actually performs.

    d3 projects to ``[k.x + dx, dy - k.y]`` over the raw Mercator
    ``[lambda, ln tan(pi/4 + phi/2)]``, with ``dx`` and ``dy`` chosen so the
    configured centre lands on the configured translate. Reimplemented here
    rather than inlining 36 KB of ``d3-geo`` and a 108 KB atlas into every pack,
    and pinned against the real library by a parity test so "reimplemented"
    cannot quietly become "approximated".

    The two offsets depend only on the configuration, so they are computed once
    at construction. Deriving them inside ``__call__`` meant reprojecting the
    centre for every one of the ~8,000 points in a world atlas — three times the
    trigonometry per point, for two numbers that never change.
    """

    scale: float
    centre_lon: float
    centre_lat: float
    translate_x: float
    translate_y: float

    offset_x: float = field(init=False)
    offset_y: float = field(init=False)

    def __post_init__(self) -> None:
        centre_x, centre_y = _raw(self.centre_lon, self.centre_lat)
        # `object.__setattr__` because the dataclass is frozen: these are
        # derived at construction, not assignable afterwards.
        object.__setattr__(self, "offset_x", self.translate_x - self.scale * centre_x)
        object.__setattr__(self, "offset_y", self.translate_y + self.scale * centre_y)

    def __call__(self, lon: float, lat: float) -> tuple[float, float]:
        x, y = _raw(lon, lat)
        return self.scale * x + self.offset_x, self.offset_y - self.scale * y


def _decode_arcs(topology: Mapping[str, Any]) -> list[list[tuple[float, float]]]:
    """Undo TopoJSON's quantisation and delta encoding, once for the whole file.

    Arcs are stored as integer deltas against a shared transform, which is what
    makes a 110m world atlas 108 KB instead of a megabyte. Every geometry indexes
    into this list, so decoding once and reusing is the difference between
    linear and quadratic work.
    """
    transform = topology["transform"]
    scale_x, scale_y = transform["scale"]
    shift_x, shift_y = transform["translate"]
    decoded: list[list[tuple[float, float]]] = []
    for arc in topology["arcs"]:
        x = y = 0
        points: list[tuple[float, float]] = []
        for delta_x, delta_y in arc:
            x += delta_x
            y += delta_y
            points.append((x * scale_x + shift_x, y * scale_y + shift_y))
        decoded.append(points)
    return decoded


def _ring(
    arcs: Sequence[int], decoded: Sequence[Sequence[tuple[float, float]]]
) -> list[tuple[float, float]]:
    """One closed ring, stitched from its arc indices.

    A negative index means the arc traversed backwards, encoded as its one's
    complement — that is how TopoJSON shares a coastline between two countries
    without storing it twice. Each subsequent arc repeats the previous one's last
    point, so it is dropped.
    """
    ring: list[tuple[float, float]] = []
    for index in arcs:
        reversed_arc = index < 0
        points = decoded[~index if reversed_arc else index]
        ordered = list(reversed(points)) if reversed_arc else list(points)
        ring.extend(ordered[1:] if ring else ordered)
    return ring


def _path(
    geometry: Mapping[str, Any], decoded: Sequence[Sequence[tuple[float, float]]], project: Mercator
) -> str:
    """One country as an SVG path, at one decimal — enough for a 960px panel."""
    rings: list[Sequence[int]] = []
    if geometry["type"] == "Polygon":
        rings = list(geometry["arcs"])
    elif geometry["type"] == "MultiPolygon":
        rings = [ring for polygon in geometry["arcs"] for ring in polygon]
    parts: list[str] = []
    for arcs in rings:
        points = _ring(arcs, decoded)
        if not points:
            continue
        projected = [project(lon, lat) for lon, lat in points]
        head = projected[0]
        body = "L".join(f"{x:.1f},{y:.1f}" for x, y in projected[1:])
        parts.append(f"M{head[0]:.1f},{head[1]:.1f}" + (f"L{body}" if body else "") + "Z")
    return "".join(parts)


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------


def _figure(value: float) -> str:
    """A grouped whole number with no unit — ``1,234``."""
    return quantity(value, "").strip()


def _one_decimal(value: float) -> str:
    """A number to one decimal — ``3.2``.

    Routed through ``percent`` so the half-away-from-zero rule lives in one
    place in the system (``ui-contract.md`` §2) rather than being re-implemented
    here with a different one: an f-string's ``.1f`` rounds half to **even**, so
    a figure landing on a half would read one way in the pack and another in the
    CSV for the same run. The scale cancels — ``percent`` multiplies by 100
    inside the decimal domain and this divides by the same constant — so the
    only thing borrowed is the rounding.
    """
    return percent(value / PERCENT_SCALE, places=1).removesuffix("%")


def _text(value: object) -> str:
    return html.escape(str(value), quote=True)


def _optional(value: float | None, render: Any) -> str:
    return EM_DASH if value is None else str(render(value))


def _join(*parts: str) -> str:
    return SEPARATOR.join(part for part in parts if part)


# --------------------------------------------------------------------------
# §5.1 — the twelve tiles
# --------------------------------------------------------------------------

COMPLIANT: Final = "compliant"
ALERT: Final = "alert"
NEUTRAL: Final = "neutral"

MARKS: Final[Mapping[str, str]] = {COMPLIANT: "✓", ALERT: "!", NEUTRAL: ""}
STATE_LABELS: Final[Mapping[str, str]] = {
    COMPLIANT: "on target",
    ALERT: "outside the mandate",
    NEUTRAL: "",
}
"""§12 and ``ui-contract.md`` §7.1: colour is never the only channel.

Every tile that carries a verdict carries a mark **and** a word, the word in the
screen-reader layer. The live page computes these in ``controls.js``; the pack
bakes them in, because it has no script to compute them with.
"""


def _verdict(holds: bool) -> str:
    return COMPLIANT if holds else ALERT


@dataclass(frozen=True, slots=True, kw_only=True)
class Tile:
    label: str
    value: str
    sub: str
    state: str
    highlight: bool = False


def _tiles(record: RunRecord, bands: Mapping[str, float]) -> list[Tile]:
    """§5.1's twelve, in order, with the compliance rule each one carries.

    The verdicts are recomputed here from the aggregates and the mandate rather
    than read off the result: ``PortfolioAggregates`` carries the figures and
    ``docs/api.md`` §8.1 does not put a verdict on the wire, because the rule —
    and the 8% and 8-point bands it turns on — belongs to ``ui-contract.md``
    §5.1 rather than to the engine. Putting a display band in the assumption set
    would make it a calibration value, which it is not.
    """
    totals = record.aggregates
    mandate = record.mandate
    if totals is None:  # pragma: no cover - the endpoint refuses an unfinished run
        return []
    shortfall = mandate.capacity_target_mw - totals.capacity_mw
    capacity_gap = abs(totals.capacity_mw - mandate.capacity_target_mw) / mandate.capacity_target_mw
    split_gap = abs(totals.solar_share - mandate.solar_share)
    moic = "" if totals.equity_irr is None else f"{multiple(totals.moic or 0.0)} MOIC"
    return [
        Tile(
            label="Installed capacity",
            value=quantity(totals.capacity_mw, "MW"),
            # §5.1: the sub-label carries the **shortfall** when the target is
            # unreachable (§13). The run still returns the best feasible
            # portfolio, so an alert tone alone would say the target was missed
            # without saying by how much — which is the one number a committee
            # asks for next. A portfolio at or above target renders as before,
            # because `_join` drops the empty clause.
            sub=_join(
                f"target {quantity(mandate.capacity_target_mw, 'MW')}",
                f"{quantity(shortfall, 'MW')} short" if shortfall > 0.0 else "",
            ),
            state=_verdict(capacity_gap <= bands["capacityBand"]),
        ),
        Tile(
            label="Projects",
            value=str(totals.project_count),
            sub=_join(f"{totals.solar_count} solar", f"{totals.wind_count} wind"),
            state=NEUTRAL,
        ),
        Tile(
            label="Technology split",
            value=f"{percent(totals.solar_share)} solar",
            sub=f"target {percent(mandate.solar_share)} solar",
            state=_verdict(split_gap <= bands["techSplitBand"]),
        ),
        Tile(
            label="Equity required",
            value=money_m(totals.equity_m),
            sub=_join(
                f"of {money_m(mandate.available_capital_m)}",
                f"{percent(totals.capital_deployed)} deployed",
            ),
            state=NEUTRAL,
        ),
        Tile(
            label="Total project cost",
            value=money_m(totals.total_capex_m),
            sub=f"{money_m(totals.senior_debt_m)} senior debt",
            state=NEUTRAL,
        ),
        Tile(
            label=f"Equity IRR ({mandate.hold_years}y)",
            value=_optional(totals.equity_irr, lambda value: percent(value, places=1)),
            # §5.1: when the IRR is undefined the MOIC clause is dropped rather
            # than shown as a zero multiple.
            sub=_join(f"hurdle {percent(mandate.target_irr, places=1)}", moic),
            state=NEUTRAL
            if totals.equity_irr is None
            else _verdict(totals.equity_irr >= mandate.target_irr),
            highlight=True,
        ),
        Tile(
            label="Leverage",
            value=percent(totals.gearing),
            sub=_join(
                f"min {percent(mandate.min_leverage)}",
                f"DSCR floor {_optional(totals.worst_min_dscr, multiple)}",
            ),
            state=_verdict(totals.gearing >= mandate.min_leverage),
        ),
        Tile(
            label="Weighted LCOE",
            value=f"€{_figure(totals.weighted_lcoe)}",
            sub="per MWh, real",
            state=NEUTRAL,
        ),
        Tile(
            label="Annual generation",
            value=quantity(totals.annual_generation_gwh, "GWh"),
            sub=f"{quantity(totals.co2_avoided_kt, 'kt')} CO₂ avoided p.a.",
            state=NEUTRAL,
        ),
        Tile(
            label="30-year FCFE",
            value=money_m(totals.thirty_year_fcfe_m),
            sub="undiscounted, post debt",
            state=NEUTRAL,
        ),
        Tile(
            label="Merchant exposure",
            value=percent(totals.merchant_share),
            sub=f"cap {percent(mandate.max_merchant_share)}",
            state=_verdict(totals.merchant_share <= mandate.max_merchant_share),
        ),
        Tile(
            label="Largest country",
            value=_join(
                totals.largest_country_code or EM_DASH, percent(totals.largest_country_share)
            ),
            sub=_join(
                f"cap {percent(mandate.max_country_share)}",
                f"risk score {_one_decimal(totals.weighted_risk_score)}",
            ),
            state=_verdict(totals.largest_country_share <= mandate.max_country_share),
        ),
    ]


def _tiles_html(tiles: Iterable[Tile]) -> str:
    cells = []
    for tile in tiles:
        mark = MARKS[tile.state]
        word = STATE_LABELS[tile.state]
        classes = f"tile tile--{tile.state}" + (" tile--highlight" if tile.highlight else "")
        cells.append(
            f'<div class="{classes}">'
            f'<p class="tile__label">{_text(tile.label)}</p>'
            f'<p class="tile__value">{_text(tile.value)}</p>'
            f'<p class="tile__sub">'
            f'<span aria-hidden="true" class="tile__mark">{_text(mark)}</span>'
            f'<span class="sr-only">{_text(word)}</span> {_text(tile.sub)}</p>'
            f"</div>"
        )
    return f'<section class="tiles" aria-label="Headline metrics">{"".join(cells)}</section>'


# --------------------------------------------------------------------------
# §5.2 and §6 — the two charts
# --------------------------------------------------------------------------


def _cashflow_svg(series: Sequence[float], base_year: int, spec: Mapping[str, Any]) -> str:
    """§5.2's thirty bars, from ``cashflow30Y_m``.

    **Never ``cashflowHold_m``.** That series carries the terminal value in its
    last element and runs the mandate's hold period; drawing it here is the
    single most likely silent bug in the feature, and it produces a chart that
    looks entirely plausible.

    The zero line sits at ``max ÷ (max - min)`` of the plot height, as §5.2
    specifies, so a portfolio with no negative year still draws its baseline at
    the foot of the plot rather than in the middle of it.
    """
    if not series:
        return '<p class="notice">No cash-flow series on this run.</p>'
    width = spec["width"]
    height = spec["height"]
    gap = spec["barGap"]
    tick_baseline = spec["tickBaseline"]
    count = len(series)
    highest = max(*series, 0.0)
    lowest = min(*series, 0.0)
    span = highest - lowest or 1.0
    zero_y = height * (highest / span)
    bar_width = (width - gap * (count - 1)) / count

    bars: list[str] = []
    ticks: list[str] = []
    for index, value in enumerate(series):
        x = index * (bar_width + gap)
        magnitude = abs(value) / span * height
        y = zero_y - magnitude if value >= 0 else zero_y
        fill = "var(--pack-accent)" if value >= 0 else "var(--pack-accent300)"
        year = base_year + index
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" '
            f'height="{magnitude:.1f}" fill="{fill}">'
            f"<title>{year}: {_text(money_m(value))}</title></rect>"
        )
        if index % spec["tickEvery"] == 0:
            ticks.append(
                f'<text x="{x + bar_width / 2:.1f}" y="{height + tick_baseline:.0f}" '
                f'text-anchor="middle" class="axis">{year}</text>'
            )
    baseline = (
        f'<line x1="0" y1="{zero_y:.1f}" x2="{width}" y2="{zero_y:.1f}" '
        f'stroke="var(--pack-divider)"/>'
    )
    top = spec["axisLabelSpace"]
    labels = (
        f'<text x="0" y="{spec["axisBaseline"]}" class="axis">'
        f"{_text(money_m(highest))}</text>"
        f'<text x="{width}" y="{spec["axisBaseline"]}" text-anchor="end" class="axis">'
        f"{_text(money_m(lowest))}</text>"
    )
    return (
        f'<svg class="chart" viewBox="0 {-top} {width} '
        f'{height + top + spec["tickLabelSpace"]}" role="img" '
        f'aria-label="Free cash flow to equity over thirty years">'
        f"{labels}{''.join(bars)}{baseline}{''.join(ticks)}</svg>"
    )


def _cashflow_table(series: Sequence[float], base_year: int) -> str:
    """The same thirty numbers as text, for a reader who cannot see the chart.

    ``ui-contract.md`` §5.2 makes every bar a control carrying its year and
    amount so the series is reachable without a pointer. A printed page has no
    hover, so the pack carries the table instead — visually hidden on screen and
    printed on the last page, where a committee can actually read the figures.
    """
    rows = "".join(
        f"<tr><td>{base_year + index}</td><td>{_text(money_m(value))}</td></tr>"
        for index, value in enumerate(series)
    )
    return (
        '<table class="cashflow-table"><caption>Free cash flow to equity by year</caption>'
        "<thead><tr><th>Year</th><th>FCFE</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _convergence_svg(record: RunRecord, spec: Mapping[str, Any]) -> str:
    """§6's two polylines: the running best and the population mean.

    On the search screen these animate as the run proceeds. In a pack they are
    the evidence that the search *converged* rather than stopped, which is what
    §6 says the screen exists to show — so the pack keeps them.
    """
    points = record.convergence
    if not points:
        return '<p class="notice">No convergence curve was recorded for this run.</p>'
    width = spec["width"]
    height = spec["height"]
    best = [point.best_fitness for point in points]
    mean = [point.mean_fitness for point in points]
    highest = max(*best, *mean)
    lowest = min(*best, *mean)
    span = highest - lowest or 1.0
    last = max(len(points) - 1, 1)

    def polyline(values: Sequence[float], colour: str) -> str:
        coordinates = " ".join(
            f"{index / last * width:.1f},{(highest - value) / span * height:.1f}"
            for index, value in enumerate(values)
        )
        return (
            f'<polyline points="{coordinates}" fill="none" stroke="{colour}" '
            f'stroke-width="1" vector-effect="non-scaling-stroke"/>'
        )

    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Best and mean portfolio score by generation">'
        f"{polyline(best, 'var(--pack-accent700)')}"
        f"{polyline(mean, 'var(--pack-accent2)')}"
        f"</svg>"
    )


# --------------------------------------------------------------------------
# §5.3 — the map
# --------------------------------------------------------------------------


def _map_svg(holdings: Sequence[Holding], atlas: Path, spec: Mapping[str, Any]) -> str:
    """Selected sites on a Natural Earth 110m base, projected here in Python.

    §13: if the geometry is unavailable the panel degrades to a notice and the
    rest of the page is unaffected — so a missing atlas is a missing map, not a
    missing pack.

    Countries are matched by **name**, which works because the pipeline's own
    geography file and the atlas agree on all fourteen markets. A country whose
    name does not match simply is not highlighted, which is the right failure:
    the markers, which carry the actual portfolio, are plotted from the run's own
    coordinates regardless.
    """
    selected = [holding for holding in holdings if holding.selected]
    if not atlas.is_file():
        return '<p class="notice">Map data unavailable.</p>'
    topology = json.loads(atlas.read_text(encoding="utf-8"))
    width, height = spec["width"], spec["height"]
    project = Mercator(
        scale=width * spec["scaleFactor"],
        centre_lon=spec["centreLon"],
        centre_lat=spec["centreLat"],
        translate_x=width / 2,
        translate_y=height / 2,
    )
    decoded = _decode_arcs(topology)
    held = {holding.country for holding in selected}
    shapes: list[str] = []
    for geometry in topology["objects"]["countries"]["geometries"]:
        name = geometry.get("properties", {}).get("name", "")
        fill = "var(--pack-accent200)" if name in held else "var(--pack-neutral200)"
        drawn = _path(geometry, decoded, project)
        if drawn:
            shapes.append(
                f'<path d="{drawn}" fill="{fill}" stroke="var(--pack-divider)" '
                f'stroke-width="{spec["countryStroke"]}"/>'
            )

    markers: list[str] = []
    for holding in sorted(selected, key=lambda row: -row.capacity_mw):
        x, y = project(holding.lon, holding.lat)
        radius = max(
            spec["markerRadiusFloor"],
            math.sqrt(holding.capacity_mw) * spec["markerRadiusFactor"],
        )
        colour = (
            "var(--pack-accent700)"
            if holding.technology is Technology.SOLAR
            else "var(--pack-accent400)"
        )
        markers.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="{colour}" '
            f'fill-opacity="{spec["markerFillOpacity"]}" stroke="var(--pack-bg)" '
            f'stroke-width="{spec["markerStroke"]}">'
            f"<title>{_text(holding.name)} {_text(quantity(holding.capacity_mw, 'MW'))}"
            f"</title></circle>"
        )
    return (
        f'<svg class="map" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Map of the selected sites">{"".join(shapes)}{"".join(markers)}</svg>'
    )


# --------------------------------------------------------------------------
# §5.4 — the holdings table
# --------------------------------------------------------------------------

HOLDINGS_HEADINGS: Final[tuple[str, ...]] = (
    "Project",
    "Technology",
    "Stage",
    "MW",
    "COD",
    "Capex €m",
    "Equity €m",
    "Lev",
    "Cap. factor",
    "P50 GWh/y",
    "LCOE €/MWh",
    "Contracted",
    "Equity IRR",
    "Min DSCR",
    "Risk",
)
"""``ui-contract.md`` §5.4's sixteen columns without the first.

The lock control is a button rather than a value — a steering instruction for
the *next* run, not a property of the portfolio being exported — so it is
omitted here for the same reason ``holdings.csv`` omits it (§9.1).
"""


def _label(value: object) -> str:
    """``ready_to_build`` becomes ``Ready to build``, for a human reader."""
    return str(value).replace("_", " ").capitalize()


def _holdings_table(record: RunRecord) -> str:
    """The selection, ordered by id, with the DSCR floor marked where it breaches.

    §5.4 asks for "red below the mandate floor"; the palette has no red, so the
    alert tone carries a mark as well and never signals by colour alone (§7.1).
    """
    floor = record.mandate.min_dscr
    rows: list[str] = []
    for holding in record.holdings:
        if not holding.selected:
            continue
        breached = holding.min_dscr is not None and holding.min_dscr < floor
        dscr = _optional(holding.min_dscr, multiple)
        dscr_cell = (
            f'<td class="num cell--alert"><span aria-hidden="true">!</span> {_text(dscr)}'
            f'<span class="sr-only"> below the {_text(multiple(floor))} mandate floor</span></td>'
            if breached
            else f'<td class="num">{_text(dscr)}</td>'
        )
        rows.append(
            "<tr>"
            f'<td><span class="row__name">{_text(holding.name)}</span>'
            f'<span class="row__meta">{_text(_join(holding.country, holding.id))}</span></td>'
            f"<td>{_text(_label(holding.technology.value))}</td>"
            f"<td>{_text(_label(holding.stage.value))}</td>"
            f'<td class="num">{_text(quantity(holding.capacity_mw, ""))}</td>'
            f'<td class="num">{holding.cod_year}</td>'
            f'<td class="num">{_text(money_m(holding.total_capex_m))}</td>'
            f'<td class="num">{_text(money_m(holding.equity_m))}</td>'
            f'<td class="num">{_text(percent(holding.gearing))}</td>'
            f'<td class="num">{_text(percent(holding.net_capacity_factor, places=1))}</td>'
            f'<td class="num">{_text(quantity(holding.annual_generation_gwh, ""))}</td>'
            f'<td class="num">{_text(_figure(holding.lcoe))}</td>'
            f'<td class="num">{_text(percent(holding.ppa_share))}</td>'
            f'<td class="num">'
            f"{_text(_optional(holding.equity_irr, lambda value: percent(value, places=1)))}</td>"
            f"{dscr_cell}"
            f'<td class="num">{_text(_one_decimal(holding.development_risk_score))}</td>'
            "</tr>"
        )
    headings = "".join(f"<th>{_text(name)}</th>" for name in HOLDINGS_HEADINGS)
    considered = len(record.holdings)
    return (
        f'<table class="holdings"><caption>{len(rows)} selected of {considered} '
        f"candidates the run considered</caption>"
        f"<thead><tr>{headings}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


# --------------------------------------------------------------------------
# The mandate and the audit trail
# --------------------------------------------------------------------------


def _definition_list(rows: Sequence[tuple[str, str]], css_class: str) -> str:
    items = "".join(
        f"<div><dt>{_text(label)}</dt><dd>{_text(value)}</dd></div>" for label, value in rows
    )
    return f'<dl class="{css_class}">{items}</dl>'


def _mandate_html(record: RunRecord) -> str:
    """§7.7: every export carries the run reference **and the mandate**.

    A portfolio without the question it answers is not something a committee can
    act on — it cannot tell an under-target result from a deliberately small one.
    """
    mandate = record.mandate
    rows = [
        ("Equity available", money_m(mandate.available_capital_m)),
        ("Capacity target", quantity(mandate.capacity_target_mw, "MW")),
        ("Technology split", f"{percent(mandate.solar_share)} solar"),
        ("Return hurdle", percent(mandate.target_irr, places=1)),
        ("Hold period", f"{mandate.hold_years} years"),
        ("Countries", ", ".join(mandate.countries)),
        ("Stages", ", ".join(_label(stage.value) for stage in mandate.stages)),
        ("Minimum leverage", percent(mandate.min_leverage)),
        ("Minimum DSCR", multiple(mandate.min_dscr)),
        ("Merchant cap", percent(mandate.max_merchant_share)),
        ("Country cap", percent(mandate.max_country_share)),
        ("Single-project cap", percent(mandate.max_project_share)),
        ("COD window", f"{mandate.cod_from}\u2013{mandate.cod_to}"),
        ("Risk appetite", _label(mandate.risk_appetite.value)),
    ]
    screens = [
        name
        for name, on in (
            ("grid connection secured", mandate.grid_secured_only),
            ("euro revenue only", mandate.eur_revenue_only),
            ("O&M contracted", mandate.om_contracted_only),
        )
        if on
    ]
    rows.append(("Execution screens", ", ".join(screens) if screens else "none"))
    if record.locked_ids:
        rows.append(("Locked in", ", ".join(record.locked_ids)))
    if record.excluded_ids:
        rows.append(("Excluded", ", ".join(record.excluded_ids)))
    return _definition_list(rows, "mandate")


def _provenance_html(run: StoredRun) -> str:
    """§12's audit trail: what produced these numbers, and under what.

    ``blasThreads`` is here because it changes reduction order and so changes
    the result — a reproduction attempt that ignores it can fail for a reason
    nothing else on the page would explain.
    """
    record = run.record
    trail = record.provenance
    rows = [
        ("Run reference", record.run_ref),
        ("Run id", record.run_id),
        ("Started", record.created_at.isoformat()),
        ("Duration", f"{record.duration_ms:,} ms" if record.duration_ms else EM_DASH),
        ("Effort", f"{_label(record.effort.value)} — {run.generations_planned} generations"),
        ("Seed", str(trail.seed)),
        ("Pipeline hash", trail.pipeline_hash),
        ("Files in snapshot", f"{len(trail.file_hashes):,}"),
        ("Assumption set", f"{trail.assumption_set_id} ({trail.assumption_set_hash})"),
        ("Engine", trail.engine_version),
        ("numpy", trail.numpy_version),
        ("BLAS threads", str(trail.blas_threads)),
        ("Python", trail.python_version),
        ("Platform", trail.platform),
    ]
    return _definition_list(rows, "provenance")


# --------------------------------------------------------------------------
# The document
# --------------------------------------------------------------------------

PACK_CSS: Final = """
.pack { max-width: var(--pack-page-width); margin: 0 auto; padding: 32px 24px 64px; }
.pack h1 { font-size: 34px; margin: 0; }
.pack h2 { font-size: 15px; text-transform: uppercase; letter-spacing: .08em;
           margin: 40px 0 12px; color: var(--pack-muted); }
.sub { color: var(--pack-muted); margin: 6px 0 0; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
           overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }
.tiles { display: grid; grid-template-columns: repeat(4, 1fr);
         border-top: 1px solid var(--pack-divider); border-left: 1px solid var(--pack-divider); }
.tile { padding: 14px 16px; border-right: 1px solid var(--pack-divider);
        border-bottom: 1px solid var(--pack-divider); }
.tile--highlight { background: var(--pack-accent100); }
.tile__label { font-size: 11.5px; text-transform: uppercase; letter-spacing: .07em;
               color: var(--pack-muted); margin: 0; }
.tile__value { font-size: 33px; line-height: 1.1; margin: 4px 0 2px; }
.tile__sub { font-size: 13px; color: var(--pack-muted); margin: 0; }
.tile--compliant .tile__mark { color: var(--pack-accent700); }
.tile--alert .tile__mark, .cell--alert { color: var(--pack-accent800); font-weight: 600; }
.panel { border: 1px solid var(--pack-divider); padding: 16px; }
.chart, .map { width: 100%; height: auto; display: block; }
.axis { font-size: 11.5px; fill: var(--pack-muted); }
.legend { font-size: 12px; color: var(--pack-muted); margin: 8px 0 0; }
.notice { color: var(--pack-muted); font-style: italic; margin: 0; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
caption { text-align: left; font-size: 12px; color: var(--pack-muted); padding-bottom: 6px; }
th, td { border-bottom: 1px solid var(--pack-divider); padding: 5px 8px; text-align: left;
         vertical-align: top; }
th { font-size: 11px; text-transform: uppercase; letter-spacing: .05em;
     color: var(--pack-muted); font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.row__name { display: block; }
.row__meta { display: block; font-size: 11px; color: var(--pack-muted); }
dl { display: grid; grid-template-columns: repeat(2, 1fr); gap: 0; margin: 0;
     border-top: 1px solid var(--pack-divider); }
dl > div { display: flex; justify-content: space-between; gap: 16px; padding: 6px 0;
           border-bottom: 1px solid var(--pack-divider); }
dl > div:nth-child(odd) { padding-right: 24px; }
dt { color: var(--pack-muted); font-size: 12.5px; }
dd { margin: 0; font-size: 12.5px; text-align: right; word-break: break-all; }
.cashflow-table { margin-top: 24px; }

@media print {
  @page { size: A4 landscape; margin: 12mm; }
  body { background: #fff; }
  .pack { max-width: none; padding: 0; }
  .page-break { break-before: page; }
  .panel, .tiles, table { break-inside: avoid; }
  thead { display: table-header-group; }
  tr { break-inside: avoid; }
  .pack h2 { margin-top: 18px; }
}

@media screen {
  .cashflow-table { position: absolute; width: 1px; height: 1px; overflow: hidden;
                    clip: rect(0 0 0 0); white-space: nowrap; }
}
"""
"""The pack's own layout, and the print rules the application does not have.

``web/dist/app.css`` carries the ten ``@font-face`` rules and Tailwind's base
layer, which is what makes the pack look like the product. It carries no
``@media print`` block — nothing in ``web/`` does — and no custom properties,
because Tailwind resolves its tokens into utility classes at build time. So the
pack declares §1.1's palette itself and styles its own semantic class names,
rather than guessing at utility names it does not own.
"""


def _subtitle(record: RunRecord) -> str:
    """§5's sub-line: what this portfolio is, in one sentence."""
    totals = record.aggregates
    if totals is None:  # pragma: no cover - the endpoint refuses an unfinished run
        return ""
    countries = len({holding.country_code for holding in record.holdings if holding.selected})
    return _join(
        f"{quantity(totals.capacity_mw, 'MW')} across {totals.project_count} projects "
        f"in {countries} countries",
        f"{record.mandate.hold_years}-year hold",
        f"run {record.run_ref}",
    )


def _document(run: StoredRun, css: str, atlas: Path, spec: Mapping[str, Any]) -> str:
    record = run.record
    generated = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
    cashflow = [float(value) for value in record.cashflow_30y_m]
    caption = (
        "Negative years are equity draw-down during construction. Cumulative "
        f"undiscounted FCFE over {len(cashflow)} years: {money_m(sum(cashflow))}."
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Investment committee sheet {_text(record.run_ref)} — TerraFolio</title>
<link rel="icon" href="data:,">
<style>{css}</style>
</head>
<body>
<main class="pack">
  <header>
    <h1>Suggested portfolio</h1>
    <p class="sub">{_text(_subtitle(record))}</p>
    <p class="sub">Investment committee sheet, generated {_text(generated)}</p>
  </header>

  {_tiles_html(_tiles(record, spec["compliance"]))}

  <h2>Free cash flow to equity — 30 years</h2>
  <div class="panel">
    {_cashflow_svg(cashflow, run.base_year, spec["cashflow"])}
    <p class="legend">{_text(caption)}</p>
  </div>

  <h2>Selected sites</h2>
  <div class="panel">
    {_map_svg(record.holdings, atlas, spec["map"])}
    <p class="legend">Solar {_text(SEPARATOR)} Wind {_text(SEPARATOR)} Marker area ∝ MW</p>
  </div>

  <h2>Search convergence</h2>
  <div class="panel">
    {_convergence_svg(record, spec["convergence"])}
    <p class="legend">Best portfolio score and population mean, by generation.</p>
  </div>

  <h2 class="page-break">Holdings</h2>
  {_holdings_table(record)}

  <h2 class="page-break">Mandate</h2>
  {_mandate_html(record)}

  <h2>Provenance</h2>
  {_provenance_html(run)}

  {_cashflow_table(cashflow, run.base_year)}
</main>
</body>
</html>
"""


def committee_pack(run: StoredRun, *, stylesheet: Path, fonts_dir: Path, atlas: Path) -> bytes:
    """One self-contained HTML file for a run, ready to print (§12, §7.7).

    Takes explicit paths rather than the application's settings: ``export`` sits
    below ``api`` in the dependency order and may not import it, and a pure
    function of four paths is also what makes the pack testable without a server.
    """
    spec = layout()
    css = "\n".join(
        [
            _stylesheet(stylesheet, fonts_dir),
            _tokens(spec["colour"]),
            f":root {{ --pack-page-width: {spec['page']['width']}px; }}",
            PACK_CSS,
        ]
    )
    return _document(run, css, atlas, spec).encode("utf-8")
