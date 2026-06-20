"""Transform example — rewrite rendered text with ``Transform``.

Reference: ink's ``examples/transform/``.

``Transform(*children, transform=fn)`` lays out the children, renders
them to a string, splits the result into lines, and pipes each line
through ``fn(line, index) -> str``. This demo shows three classic
uses stacked in one column:

* **uppercase** — every letter in ``"hello world"`` becomes uppercase.
* **hanging indent** — first line is flush, subsequent lines are
  indented by 4 spaces (useful for long paragraphs that wrap).
* **line numbering** — each line is prefixed with ``"  N: "`` so the
  output reads like a code listing.

Run::

    python examples/transform/transform_demo.py

Quit with ``Ctrl+C``.
"""

from __future__ import annotations

import sys

from pyink import Box, Spacer, Text, Transform, render
from pyink.core.element import Element


def _uppercase(line: str, _idx: int) -> str:
    return line.upper()


def _hanging_indent(line: str, idx: int) -> str:
    # First line flush, every subsequent line indented 4 spaces.
    return line if idx == 0 else "    " + line


def _line_number(line: str, idx: int) -> str:
    # Right-aligned 3-digit line number, colon, single space.
    return f"{idx + 1:3d}: {line}"


def TransformDemo() -> Element:
    """Compose the three Transform blocks with labels."""

    return Box(
        Text("Transform demo — three classic transforms", bold=True),
        Text("Press Ctrl+C to quit.", dimColor=True),
        Spacer(size=1),
        # Block 1 — uppercase.
        Box(
            Text("uppercase", dimColor=True, bold=True),
            Transform(
                Text("hello world"),
                transform=_uppercase,
            ),
            flexDirection="column",
        ),
        Spacer(size=1),
        # Block 2 — hanging indent. Long text so the wrap produces
        # multiple lines; the second line is indented.
        Box(
            Text("hanging indent", dimColor=True, bold=True),
            Transform(
                Text(
                    "This is a long paragraph that wraps across multiple "
                    "lines so the hanging-indent transform has something "
                    "to indent on every row past the first."
                ),
                transform=_hanging_indent,
            ),
            flexDirection="column",
        ),
        Spacer(size=1),
        # Block 3 — line numbering.
        Box(
            Text("line numbering", dimColor=True, bold=True),
            Transform(
                Text("alpha\nbeta\ngamma\ndelta\nepsilon"),
                transform=_line_number,
            ),
            flexDirection="column",
        ),
        flexDirection="column",
        padding=1,
    )


def main() -> int:
    inst = render(TransformDemo(), columns=60, rows=18)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
