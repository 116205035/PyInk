"""Divider example — section separators (Phase 2 PR4).

Reference: ink-divider's README examples. PyInk's ``Divider`` renders a
single horizontal or vertical line, optionally carrying a centred label.
The line character comes from the resolved ``border_style``; ``color``
paints line + label uniformly.

This demo mounts four divider variations in a single column:

* a plain horizontal line,
* a labelled divider (with colour),
* four different border styles stacked,
* a vertical divider inside a row container.

Run::

    python examples/divider/divider_demo.py

Controls:

* Esc / Ctrl+C — quit
"""

from __future__ import annotations

import sys

from pyink import Box, Text, create_element, render, use_app, use_input
from pyink.core.element import Element
from pyink.externals import Divider
from pyink.render.keys import Key

#: Border styles worth eyeballing side by side. Mirrors the borders
#: example's selection so the two demos read consistently.
BORDER_STYLES: tuple[str, ...] = ("single", "double", "round", "bold")


def DividerDemo() -> Element:
    """Build the demo tree.

    Hooks run inside the Impl closure so they execute during mount.
    """

    def Impl() -> Element:
        app = use_app()

        def on_key(key: Key) -> None:
            if key.escape:
                app.exit(None)

        use_input(on_key)

        # Vertical divider between two text siblings. The Divider fills
        # the row's main axis (height) via flexGrow=1.
        vertical_demo: Element = Box(
            Text("Left", dimColor=True),
            Divider(direction="vertical"),
            Text("Right", dimColor=True),
            flexDirection="row",
            alignItems="center",
            gap=1,
        )

        return Box(
            Text("Divider demo", bold=True),
            Text("Esc / Ctrl+C to quit", dimColor=True),
            Divider(),
            Text("Below: labelled divider", dimColor=True),
            Divider(label="Section A", color="green"),
            Text("Below: border styles", dimColor=True),
            *(Divider(border_style=style) for style in BORDER_STYLES),
            Text("Below: vertical divider", dimColor=True),
            vertical_demo,
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(DividerDemo(), columns=60, rows=18)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
