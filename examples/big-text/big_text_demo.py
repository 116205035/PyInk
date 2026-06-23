"""BigText example — ASCII art banner text (Phase 6 PR2).

Reference: ink-big-text's CLI demo. PyInk's ``BigText`` delegates to
:mod:`pyfiglet` for the glyph data — 300+ FIGlet fonts, multi-row
output, and full Unicode / ASCII coverage out of the box. The
hand-rolled font registry that shipped in earlier PRs is gone;
``pyfiglet`` covers strictly more glyphs and fonts than we could
ship inline.

This demo mounts several banners in a column:

* ``"PyInk"`` rendered in the ``block`` font with a two-colour
  ``colors=["red", "yellow"]`` cycle (matches the ink-big-text demo's
  red/yellow gradient feel).
* ``"HELLO"`` rendered in the ``standard`` font, no colour.
* ``"shadow"`` / ``"digital"`` / ``"banner"`` font showcase, each
  with a single hue.
* ``"centered"`` rendered with ``align="center"`` so pyfiglet
  pre-pads the rows for centring inside ``width=60``.

Requires ``pip install pyink[big-text]`` (or ``pip install pyfiglet``).

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
            Text("BigText demo (pyfiglet)", bold=True),
            Text("Esc / Ctrl+C to quit", dimColor=True),
            Text("block font, colors=['red', 'yellow']:", dimColor=True),
            BigText("PyInk", font="block", colors=["red", "yellow"]),
            Text("standard font:", dimColor=True),
            BigText("HELLO", font="standard"),
            Text("shadow font (green):", dimColor=True),
            BigText("shadow", font="shadow", color="green"),
            Text("digital font (cyan):", dimColor=True),
            BigText("digital", font="digital", color="cyan"),
            Text("banner font (magenta):", dimColor=True),
            BigText("banner", font="banner", color="magenta"),
            Text("centered, width=60:", dimColor=True),
            BigText("centered", font="small", align="center", width=60),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(BigTextDemo(), columns=100, rows=40)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
