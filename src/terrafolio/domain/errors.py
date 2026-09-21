"""Turning a pydantic ``ValidationError`` into something an analyst can act on.

A project file is edited by hand, often in a spreadsheet exported to JSON. The
person fixing it needs the path as it appears **in their file**, which is the
camelCase alias — so these renderers use ``loc``, which pydantic populates with
aliases, rather than the Python field names.
"""

from __future__ import annotations

from pydantic import ValidationError

__all__ = ["format_validation_error", "render_validation_error"]


def render_validation_error(error: ValidationError) -> list[str]:
    """One ``path: message`` line per problem, in the order pydantic found them.

    A whole-file rule — the reject-derived check is the one that matters here —
    has an empty ``loc``, so it renders as the message alone rather than with a
    bare ``:`` in front of it.
    """
    lines: list[str] = []
    for item in error.errors():
        path = ".".join(str(part) for part in item["loc"])
        lines.append(f"{path}: {item['msg']}" if path else str(item["msg"]))
    return lines


def format_validation_error(source: str, error: ValidationError) -> str:
    """A single message naming the source and every problem in it.

    ``source`` is whatever identifies the input to its author — a file path for
    the loader, a sheet name for the spreadsheet path.
    """
    lines = render_validation_error(error)
    plural = "problem" if len(lines) == 1 else "problems"
    return f"{source}: {len(lines)} validation {plural}\n" + "\n".join(
        f"  {line}" for line in lines
    )
