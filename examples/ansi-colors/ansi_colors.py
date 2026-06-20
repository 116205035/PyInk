"""ANSI colors + styles example — showcase every colour and text style.

Reference: ink's ``test/fixtures/text-color.jpg`` and
``text-backgroundColor.jpg`` snapshots.

PyInk's ``Text`` accepts ``color`` / ``backgroundColor`` / ``bold`` /
``italic`` / ``underline`` / ``strikethrough`` / ``inverse`` /
``dimColor`` props. Colour specs may be:

* **named** — ``black`` / ``red`` / ``green`` / ``yellow`` / ``blue``
  / ``magenta`` / ``cyan`` / ``white`` plus the ``Bright`` family
  (``redBright`` … ``whiteBright``) and ``gray`` / ``grey``.
* **hex** — ``#rrggbb`` or ``#rgb`` (truecolor, ``38;2;r;g;b``).
* **rgb** — ``"rgb(255, 128, 0)"``.
* **ansi256** — ``"ansi256(9)"``.

This example stacks every variant in a single column so the user can
see them at a glance.

Run::

    python examples/ansi-colors/ansi_colors.py

Quit with ``Ctrl+C``.
"""

from __future__ import annotations

import sys

from pyink import Box, Spacer, Text, render
from pyink.core.element import Element

#: Foreground named colors, in the canonical chalk / CSS order.
FG_NAMED: tuple[str, ...] = (
    "black",
    "red",
    "green",
    "yellow",
    "blue",
    "magenta",
    "cyan",
    "white",
    "gray",
    "redBright",
    "greenBright",
    "yellowBright",
    "blueBright",
    "magentaBright",
    "cyanBright",
    "whiteBright",
)


def AnsiColors() -> Element:
    """Stack every named colour + custom colour + style in one column."""

    fg_block: list[Element] = [
        Box(
            Text(name, color=name),
            width=22,
        )
        for name in FG_NAMED
    ]

    bg_block: list[Element] = [
        # Light background needs dark text so the sample is legible.
        Box(
            Text(name, color="black", backgroundColor=name),
            width=22,
        )
        for name in FG_NAMED
    ]

    return Box(
        Text("ANSI Colors + Styles Demo", bold=True),
        Text("Ctrl+C to quit.", dimColor=True),
        Spacer(size=1),
        # Section 1 — foreground named colours, packed in rows of 4.
        Text("Foreground (16 named colours):", bold=True, underline=True),
        Box(
            *fg_block,
            flexDirection="row",
            flexWrap="wrap",
        ),
        Spacer(size=1),
        # Section 2 — background named colours.
        Text("Background (16 named colours):", bold=True, underline=True),
        Box(
            *bg_block,
            flexDirection="row",
            flexWrap="wrap",
        ),
        Spacer(size=1),
        # Section 3 — truecolor hex + rgb samples.
        Text("Truecolor (hex + rgb):", bold=True, underline=True),
        Box(
            Text("#ff0000", color="#ff0000"),
            Text("#00ff00", color="#00ff00"),
            Text("#0000ff", color="#0000ff"),
            Text("rgb(255,128,0)", color="rgb(255, 128, 0)"),
            Text("ansi256(9)", color="ansi256(9)"),
            flexDirection="column",
        ),
        Spacer(size=1),
        # Section 4 — every text style toggle.
        Text("Styles:", bold=True, underline=True),
        Box(
            Text("bold", bold=True),
            Text("italic", italic=True),
            Text("underline", underline=True),
            Text("strikethrough", strikethrough=True),
            Text("inverse", inverse=True),
            Text("dimColor", dimColor=True),
            flexDirection="column",
        ),
        flexDirection="column",
        padding=1,
    )


def main() -> int:
    inst = render(AnsiColors(), columns=70, rows=24)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
