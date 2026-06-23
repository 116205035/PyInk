"""BigText example — ASCII art banner text (Phase 6 PR2).

Reference: ink-big-text's CLI demo. PyInk's ``BigText`` renders
multi-row ASCII art banners from a small built-in font registry (no
``cfonts`` / ``pyfiglet`` dependency). Two fonts ship out of the box:

* ``"block"`` — chunky Unicode block-element glyphs (``█`` / ``▄`` /
  ``▀``), 3 rows × 6 cells per glyph,
* ``"simple"`` — plain ASCII pipes / slashes / underscores, for
  terminals that mangle Unicode block elements.

Each glyph is rendered as three :func:`Text` leaves stacked in a
column; multi-character strings are drawn by concatenating glyphs
horizontally per row.

This demo mounts two banners side by side:

* ``"PyInk"`` rendered in the ``block`` font with a green hue,
* ``"HELLO"`` rendered in the ``simple`` font with a cyan hue.

Lowercase input is auto-uppercased before lookup (the shipped fonts
cover ``A-Z`` + ``0-9`` + space).

Run::

    python examples/big-text/big_text_demo.py

Controls:

* Esc / Ctrl+C — quit
"""

from __future__ import annotations

import sys

from pyink import Box, Text, create_element, render, use_app, use_input
from pyink.core.element import Element
from pyink.externals import BigText
from pyink.render.keys import Key


def BigTextDemo() -> Element:
    """Build the demo tree.

    Hooks run inside the Impl closure so they execute during mount.
    """

    def Impl() -> Element:
        app = use_app()

        def on_key(key: Key) -> None:
            if key.escape:
                app.exit(None)

        use_input(on_key)

        return Box(
            Text("BigText demo", bold=True),
            Text("Esc / Ctrl+C to quit", dimColor=True),
            Text("block font (green):", dimColor=True),
            BigText("PyInk", font="block", color="green"),
            Text("simple font (cyan):", dimColor=True),
            BigText("HELLO", font="simple", color="cyan"),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(BigTextDemo(), columns=44, rows=16)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
