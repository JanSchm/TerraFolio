#!/usr/bin/env python3
"""Extract the v1.0 product specification PDF to ``docs/spec.md``.

No PDF tooling exists on a stock macOS box -- no poppler, no pypdf, no
``pdftotext`` -- so this reads the file itself, using nothing but the standard
library. The output is a pure function of the PDF: re-running reproduces
``docs/spec.md`` byte for byte, which ``--check`` asserts.

The PDF is Skia/PDF, printed from Chrome, and has two traps.

*Two font types.* Section headings are ``/Type0`` Identity-H, whose codes are
two bytes wide; every body paragraph is one of the 57 ``/Type3`` fonts Skia
emits, whose codes are one byte. Decode everything at two bytes and you get a
document of headings with no prose, which looks plausible enough to ship.

*Global coordinates.* Body text is laid out in a single document-wide space
whose y runs from 109 to 16229 across the 15 pages, while the running header
and footer are drawn in page-local space at y=18 and y=46. Ordering by
``(page, y)`` is therefore correct, and the furniture is dropped by its font
size rather than by position.

Structure is recovered from the drawing operators, not guessed from text:

* tables from the 1-unit-high filled rectangles that draw their row rules --
  the x-spans give exact column boundaries;
* bullets from the small filled bezier paths that draw the discs;
* the two-column scope box in section 3 from the blueprint corner ticks;
* heading level, table headers and pull-quotes from font size.

Each ``BT``/``ET`` block holds exactly one line of one table cell or one line
of prose, so a block is the atomic unit of placement.

Usage::

    python3 tools/extract_spec.py            # write docs/spec.md
    python3 tools/extract_spec.py --check    # verify it is unchanged
"""

from __future__ import annotations

import argparse
import re
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "# Solar Wind Portfolio Optimizer.pdf"
OUT = ROOT / "docs" / "spec.md"

# Font sizes, in the PDF's own units. Skia rounds these consistently, so they
# are an exact classifier rather than a threshold.
SIZE_FURNITURE = 10.5  # running header and footer, dropped
SIZE_TABLE_HEAD = 11.0  # small caps column headings -- used nowhere else
SIZE_SUPERSCRIPT = 13.0  # the two exponents in section 9.2
SIZE_PULLQUOTE = 14.0  # the emphasised notes in 9.4 and 10.2
SIZE_LIST = 14.5
SIZE_BODY = 15.0
SIZE_SUBTITLE = 16.0
SIZE_H3 = 17.0
SIZE_H2 = 21.0
SIZE_H1 = 34.0

PARAGRAPH_GAP = 30.0  # y-gap that ends a paragraph; line leading is ~24
TABLE_GAP = 200.0  # y-gap that ends a table
BULLET_BASELINE = 4.4  # a disc sits this far above its line's baseline
BULLET_OFFSET = 2.0  # tolerance on that offset
HEADER_REACH = 60.0  # how far above the first rule a column heading may sit

OBJ_RE = re.compile(rb"(\d+) 0 obj(.*?)endobj", re.DOTALL)
NUM = r"[-\d.]+"
TOKEN_RE = re.compile(
    rb"(BT)|(ET)"
    rb"|/(\w+) (" + NUM.encode() + rb") Tf"
    rb"|(" + NUM.encode() + rb") (" + NUM.encode() + rb") (" + NUM.encode() + rb")"
    rb" (" + NUM.encode() + rb") (" + NUM.encode() + rb") (" + NUM.encode() + rb") Tm"
    rb"|<([0-9A-Fa-f]*)> Tj"
)
RECT_RE = re.compile(
    rb"(" + NUM.encode() + rb") (" + NUM.encode() + rb") "
    rb"(" + NUM.encode() + rb") (" + NUM.encode() + rb") re\nf"
)
PATH_RE = re.compile(
    rb"(" + NUM.encode() + rb") (" + NUM.encode() + rb") m\n((?:[^\n]*\n){0,24}?)f\n"
)


# --------------------------------------------------------------------------
# PDF object layer
# --------------------------------------------------------------------------


def load_objects(data: bytes) -> dict[int, bytes]:
    """Map object number to body for every ``N 0 obj ... endobj`` in the file."""
    return {int(m.group(1)): m.group(2) for m in OBJ_RE.finditer(data)}


def inflate(objs: dict[int, bytes], num: int) -> bytes:
    """Return an object's decompressed stream, or empty bytes if it has none."""
    body = objs.get(num)
    if body is None:
        return b""
    start = body.find(b"stream")
    if start < 0:
        return b""
    raw = body[start + len(b"stream") :].lstrip(b"\r\n")
    end = raw.rfind(b"endstream")
    try:
        return zlib.decompress(raw[:end])
    except zlib.error:
        return b""


def parse_to_unicode(objs: dict[int, bytes], num: int) -> dict[int, str]:
    """Parse a ``/ToUnicode`` CMap into a code -> text mapping."""
    stream = inflate(objs, num)
    cmap: dict[int, str] = {}
    if not stream:
        return cmap
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", stream, re.DOTALL):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            cmap[int(src, 16)] = bytes.fromhex(dst.decode()).decode(
                "utf-16-be", "replace"
            )
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", stream, re.DOTALL):
        matches = re.findall(
            rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block
        )
        for lo_h, hi_h, dst_h in matches:
            lo, hi, dst = int(lo_h, 16), int(hi_h, 16), int(dst_h, 16)
            for code in range(lo, hi + 1):
                cmap[code] = chr(dst + code - lo)
    return cmap


def page_order(objs: dict[int, bytes], catalog: int) -> list[int]:
    """Walk ``/Pages`` depth-first so pages come back in document order."""
    pages: list[int] = []

    def walk(num: int) -> None:
        body = objs[num]
        if b"/Type /Pages" in body:
            kids = re.search(rb"/Kids \[(.*?)\]", body, re.DOTALL)
            if kids:
                for ref in re.findall(rb"(\d+) 0 R", kids.group(1)):
                    walk(int(ref))
        else:
            pages.append(num)

    walk(catalog)
    return pages


def find_catalog(objs: dict[int, bytes], data: bytes) -> int:
    """Resolve ``/Root -> /Pages``, the root of the page tree."""
    root = re.search(rb"/Root (\d+) 0 R", data)
    if root is None:
        raise SystemExit("no /Root in trailer")
    pages = re.search(rb"/Pages (\d+) 0 R", objs[int(root.group(1))])
    if pages is None:
        raise SystemExit("catalog has no /Pages")
    return int(pages.group(1))


def font_map(
    objs: dict[int, bytes], page: int
) -> dict[str, tuple[bool, dict[int, str]]]:
    """Per-page resource name -> (is two-byte, code -> text).

    ``/Subtype /Type0`` is Identity-H and uses two-byte CIDs; the ``/Type3``
    fonts Skia emits for body text use one-byte codes. Getting this wrong is
    what silently yields a headings-only document.
    """
    body = objs[page]
    indirect = re.search(rb"/Resources (\d+) 0 R", body)
    resources = objs[int(indirect.group(1))] if indirect else body
    block = re.search(rb"/Font <<(.*?)>>", resources, re.DOTALL)
    fonts: dict[str, tuple[bool, dict[int, str]]] = {}
    if block is None:
        return fonts
    for name, num in re.findall(rb"/(\w+) (\d+) 0 R", block.group(1)):
        font = objs[int(num)]
        wide = b"/Subtype /Type0" in font
        to_unicode = re.search(rb"/ToUnicode (\d+) 0 R", font)
        cmap = parse_to_unicode(objs, int(to_unicode.group(1))) if to_unicode else {}
        fonts[name.decode()] = (wide, cmap)
    return fonts


def contents(objs: dict[int, bytes], page: int) -> bytes:
    """Concatenate a page's content streams."""
    body = objs[page]
    single = re.search(rb"/Contents (\d+) 0 R", body)
    if single:
        return inflate(objs, int(single.group(1)))
    array = re.search(rb"/Contents \[(.*?)\]", body, re.DOTALL)
    if array:
        return b"".join(
            inflate(objs, int(ref)) for ref in re.findall(rb"(\d+) 0 R", array.group(1))
        )
    return b""


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Chunk:
    """One ``BT``/``ET`` block: a single line of one cell, or one line of prose."""

    page: int
    y: float
    x: float
    size: float
    text: str


@dataclass(frozen=True)
class RuleLine:
    """A row rule: the filled 1-unit rectangles drawn across one table row."""

    page: int
    y: float
    columns: tuple[float, ...]


@dataclass(frozen=True)
class Table:
    page: int
    rules: tuple[RuleLine, ...]

    @property
    def columns(self) -> tuple[float, ...]:
        return self.rules[0].columns

    @property
    def top(self) -> float:
        return self.rules[0].y

    @property
    def bottom(self) -> float:
        return self.rules[-1].y


@dataclass(frozen=True)
class Card:
    """One pane of the section 3 scope box, bounded by its blueprint corners."""

    page: int
    x0: float
    x1: float
    y0: float
    y1: float


def read_page(
    objs: dict[int, bytes], page: int, index: int
) -> tuple[list[Chunk], list[RuleLine], list[tuple[float, float]], list[Card]]:
    fonts = font_map(objs, page)
    stream = contents(objs, page)

    chunks: list[Chunk] = []
    glyphs: list[str] = []
    wide: bool = False
    cmap: dict[int, str] = {}
    x = y = size = 0.0
    open_block = False
    for token in TOKEN_RE.finditer(stream):
        if token.group(1):  # BT
            glyphs, size, open_block = [], 0.0, True
        elif token.group(2):  # ET
            text = "".join(glyphs)
            if open_block and text.strip() and size > SIZE_FURNITURE:
                chunks.append(Chunk(index, round(y, 1), round(x, 1), size, text))
            open_block = False
        elif token.group(3) is not None:  # Tf
            wide, cmap = fonts.get(token.group(3).decode(), (False, {}))
            size = max(size, float(token.group(4)))
        elif token.group(5) is not None:  # Tm
            x, y = float(token.group(9)), float(token.group(10))
        elif token.group(11) is not None:  # Tj
            digits = token.group(11).decode()
            step = 4 if wide else 2
            codes = (int(digits[i : i + step], 16) for i in range(0, len(digits), step))
            glyphs.append("".join(cmap.get(code, "") for code in codes))

    spans: dict[float, list[tuple[float, float]]] = {}
    ticks: list[tuple[float, float]] = []
    for rect in RECT_RE.finditer(stream):
        rx, ry, rw, rh = (float(v) for v in rect.groups())
        if rh == 1.0 and rw > 20.0 and rx > 10.0:
            spans.setdefault(ry, []).append((rx, rx + rw))
        elif rw == 1.0 and rh == 11.0:
            ticks.append((rx, ry))

    rules: list[RuleLine] = []
    for ry in sorted(spans):
        ordered = sorted(spans[ry])
        columns = (ordered[0][0], *(end for _, end in ordered))
        rules.append(RuleLine(index, ry, columns))

    bullets: list[tuple[float, float]] = []
    for path in PATH_RE.finditer(stream):
        px, py = float(path.group(1)), float(path.group(2))
        numbers = [
            float(v) for v in re.findall(rb"(" + NUM.encode() + rb")", path.group(3))
        ]
        xs, ys = numbers[0::2] + [px], numbers[1::2] + [py]
        if max(xs) - min(xs) < 10.0 and max(ys) - min(ys) < 10.0:
            bullets.append((round(min(xs), 1), round((min(ys) + max(ys)) / 2, 1)))

    cards: list[Card] = []
    if ticks:
        xs = sorted({tx for tx, _ in ticks})
        ys = sorted({ty for _, ty in ticks})
        for left, right in zip(xs[0::2], xs[1::2]):
            cards.append(Card(index, left, right, ys[0], ys[-1] + 11.0))

    return chunks, rules, sorted(bullets), cards


def group_tables(rules: list[RuleLine]) -> list[Table]:
    """Runs of adjacent rules that share a page and a column tuple form a table."""
    tables: list[Table] = []
    run: list[RuleLine] = []
    for rule in rules:
        if run and (
            rule.page != run[-1].page
            or rule.columns != run[-1].columns
            or rule.y - run[-1].y > TABLE_GAP
        ):
            tables.append(Table(run[0].page, tuple(run)))
            run = []
        run.append(rule)
    if run:
        tables.append(Table(run[0].page, tuple(run)))
    return tables


# --------------------------------------------------------------------------
# Text assembly
# --------------------------------------------------------------------------


def join(parts: list[str]) -> str:
    """Join wrapped fragments, honouring a hyphen broken across lines."""
    out = ""
    for part in parts:
        piece = part.strip()
        if not piece:
            continue
        if not out:
            out = piece
        elif out.endswith(("-", "\u2013")) and (
            piece[:1].islower() or piece[:1].isdigit()
        ):
            out += piece
        else:
            out += " " + piece
    return out


def cell_text(
    chunks: list[Chunk], lo: float, hi: float, top: float, bottom: float
) -> str:
    inside = [c for c in chunks if top < c.y < bottom and lo - 1.0 <= c.x < hi - 1.0]
    lines: dict[float, list[Chunk]] = {}
    for chunk in inside:
        lines.setdefault(chunk.y, []).append(chunk)
    parts = [
        "".join(c.text for c in sorted(lines[y], key=lambda c: c.x))
        for y in sorted(lines)
    ]
    return join(parts).replace("|", r"\|")


def render_table(table: Table, chunks: list[Chunk]) -> tuple[float, list[str]]:
    """Render one table, returning the y it should be placed at and its rows."""
    columns = table.columns
    page_chunks = [c for c in chunks if c.page == table.page]
    heads = [
        c
        for c in page_chunks
        if c.size == SIZE_TABLE_HEAD and table.top - HEADER_REACH < c.y < table.top
    ]
    header_top = min((c.y for c in heads), default=table.top) - 1.0

    rows: list[list[str]] = []
    bands = [(header_top, table.top)]
    bands += [(a.y, b.y) for a, b in zip(table.rules, table.rules[1:])]
    for top, bottom in bands:
        rows.append(
            [
                cell_text(page_chunks, columns[i], columns[i + 1], top, bottom)
                for i in range(len(columns) - 1)
            ]
        )

    header, *body = rows
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return header_top, lines


def render_cards(
    cards: list[Card], chunks: list[Chunk], bullets: list[tuple[float, float]]
) -> list[str]:
    """Render the section 3 scope box as a two-column table."""
    columns: list[tuple[str, list[str]]] = []
    for card in cards:
        inside = sorted(
            (
                c
                for c in chunks
                if c.page == card.page
                and card.y0 < c.y < card.y1
                and card.x0 <= c.x < card.x1
            ),
            key=lambda c: (c.y, c.x),
        )
        if not inside:
            continue
        title, *rest = inside
        items: list[list[str]] = []
        for chunk in rest:
            starts = discs(chunk, bullets, card.x0, card.x1)
            if starts or not items:
                items.append([chunk.text])
            else:
                items[-1].append(chunk.text)
        columns.append((title.text.strip(), [join(part) for part in items]))

    if len(columns) != 2:
        return []
    depth = max(len(items) for _, items in columns)
    lines = ["| " + " | ".join(title for title, _ in columns) + " |", "|---|---|"]
    for row in range(depth):
        cells = [items[row] if row < len(items) else "" for _, items in columns]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def discs(
    chunk: Chunk, bullets: list[tuple[float, float]], lo: float, hi: float
) -> bool:
    """True if a bullet disc is drawn just above this line, within [lo, hi)."""
    return any(
        lo <= bx < hi and abs(chunk.y - by - BULLET_BASELINE) < BULLET_OFFSET
        for bx, by in bullets
    )


def merge_superscripts(chunks: list[Chunk]) -> list[Chunk]:
    """Fold the two raised exponents in section 9.2 back into their line.

    An exponent is a small run sitting 3--8 above a full-size line and to the
    right of where that line starts. Column headings are also small, but
    nothing full-size sits just beneath them, so they are left alone.
    """

    def host(small: Chunk) -> Chunk | None:
        candidates = [
            other
            for other in chunks
            if other.page == small.page
            and other.size >= SIZE_LIST
            and other.x < small.x
            and 3.0 <= other.y - small.y <= 8.0
        ]
        # The line is split at the exponent, so the block immediately to its
        # left is the base it belongs to.
        return max(candidates, key=lambda c: c.x, default=None)

    raised = {
        id(c): h
        for c in chunks
        if c.size < SIZE_SUPERSCRIPT and (h := host(c)) is not None
    }
    merged: list[Chunk] = []
    for chunk in chunks:
        if id(chunk) in raised:
            continue
        text = chunk.text + "".join(
            "^" + small.text.strip()
            for small in chunks
            if raised.get(id(small)) is chunk
        )
        merged.append(Chunk(chunk.page, chunk.y, chunk.x, chunk.size, text))
    return merged


def merge_lines(chunks: list[Chunk]) -> list[Chunk]:
    """Outside a table, one visual line is several blocks sharing a y.

    A numbered item's marker, its bold lead-in and its running text are three
    separate blocks at three x positions. Concatenating by x rebuilds the line
    and leaves its left margin in ``x``, which is what distinguishes a list
    item from its continuation.
    """
    lines: dict[tuple[int, float], list[Chunk]] = {}
    for chunk in chunks:
        lines.setdefault((chunk.page, chunk.y), []).append(chunk)
    merged: list[Chunk] = []
    for (page, y), group in lines.items():
        group.sort(key=lambda c: c.x)
        merged.append(
            Chunk(
                page,
                y,
                group[0].x,
                max(c.size for c in group),
                "".join(c.text for c in group),
            )
        )
    merged.sort(key=lambda c: (c.page, c.y))
    return merged


def bullet_list(items: list[list[str]]) -> list[str]:
    """Render an ordered list verbatim, an unordered one with discs."""
    text = [join(part) for part in items]
    if all(re.match(r"\d+\. ", line) for line in text):
        return text
    return ["- " + line for line in text]


def heading(chunk: Chunk) -> str | None:
    if chunk.size == SIZE_H1:
        return "# " + chunk.text.strip()
    if chunk.size == SIZE_H2:
        return "## " + chunk.text.strip()
    if chunk.size == SIZE_H3:
        return "### " + chunk.text.strip()
    return None


def render(
    chunks: list[Chunk],
    tables: list[Table],
    bullets: list[tuple[float, float]],
    cards: list[Card],
) -> str:
    """Interleave prose, tables and the scope box in reading order."""
    blocks: list[tuple[tuple[int, float], list[str]]] = []
    consumed: list[tuple[int, float, float]] = []

    for table in tables:
        top, lines = render_table(table, chunks)
        blocks.append(((table.page, top), lines))
        consumed.append((table.page, top, table.bottom))
    if cards:
        lines = render_cards(cards, chunks, bullets)
        if lines:
            blocks.append(((cards[0].page, cards[0].y0), lines))
        for card in cards:
            consumed.append((card.page, card.y0, card.y1))

    loose = [
        c
        for c in chunks
        if not any(p == c.page and lo <= c.y <= hi for p, lo, hi in consumed)
    ]
    prose = merge_lines(loose)

    para: list[str] = []
    items: list[list[str]] = []
    quote: list[str] = []
    previous: Chunk | None = None
    anchor: tuple[int, float] = (0, 0.0)

    def flush() -> None:
        nonlocal para, items, quote
        if para:
            blocks.append((anchor, [join(para)]))
            para = []
        if items:
            blocks.append((anchor, bullet_list(items)))
            items = []
        if quote:
            blocks.append((anchor, ["> " + join(quote)]))
            quote = []

    for chunk in prose:
        head = heading(chunk)
        # y is document-global, so a page turn shows up as a large gap that
        # is furniture rather than spacing. Only compare within a page; every
        # real boundary across one is marked by a heading or a size change.
        broke = previous is None or (
            chunk.page == previous.page and chunk.y - previous.y > PARAGRAPH_GAP
        )
        changed = previous is not None and chunk.size != previous.size
        if head or broke or changed:
            flush()
            anchor = (chunk.page, chunk.y)
        if head:
            blocks.append(((chunk.page, chunk.y), [head]))
        elif chunk.size == SIZE_PULLQUOTE:
            quote.append(chunk.text)
        elif chunk.size == SIZE_LIST:
            numbered = re.match(r"\d+\. ", chunk.text.strip())
            disc = discs(chunk, bullets, 0.0, 800.0)
            if numbered or disc or not items:
                items.append([chunk.text])
            else:
                items[-1].append(chunk.text)
        else:
            para.append(chunk.text)
        previous = chunk
    flush()

    blocks.sort(key=lambda item: item[0])
    return "\n\n".join("\n".join(lines) for _, lines in blocks) + "\n"


def extract() -> str:
    data = PDF.read_bytes()
    objs = load_objects(data)
    pages = page_order(objs, find_catalog(objs, data))

    chunks: list[Chunk] = []
    rules: list[RuleLine] = []
    bullets: list[tuple[float, float]] = []
    cards: list[Card] = []
    for index, page in enumerate(pages, start=1):
        page_chunks, page_rules, page_bullets, page_cards = read_page(objs, page, index)
        chunks += page_chunks
        rules += page_rules
        bullets += page_bullets
        cards += page_cards

    chunks = merge_superscripts(chunks)
    chunks.sort(key=lambda c: (c.page, c.y, c.x))
    return render(chunks, group_tables(rules), bullets, cards)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the committed docs/spec.md differs from a fresh extraction",
    )
    parser.add_argument("--out", type=Path, default=OUT, help="output path")
    args = parser.parse_args()

    markdown = extract()
    if args.check:
        if not args.out.exists():
            print(f"{args.out} does not exist", file=sys.stderr)
            return 1
        current = args.out.read_text(encoding="utf-8")
        if current != markdown:
            print(f"{args.out} differs from a fresh extraction", file=sys.stderr)
            return 1
        print(f"{args.out} reproduces byte-identically ({len(markdown)} bytes)")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(markdown, encoding="utf-8")
    print(f"wrote {args.out} ({len(markdown)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
