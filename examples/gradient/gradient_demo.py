"""Gradient example — multi-colour truecolor text (Phase 6 PR2).

Reference: ink-gradient's README examples. PyInk's ``Gradient`` paints
each character of a string with a colour interpolated linearly between
caller-supplied RGB endpoints. The component runs an inner layout pass
on its children at mount time, ANSI-strips the rendered output, and
re-paints every visible cell with a per-character SGR ``38;2;r;g;b``
sequence — truecolour only, so a modern terminal renders the ramp
cleanly.

This demo mounts three gradient variations side by side:

* the "PyInk" headline painted red → yellow → green,
* a single-colour-stop ramp (magenta → cyan) over a longer string,
* a hex-spec rainbow (``#ff0000`` / ``#00ff00`` / ``#0000ff``) over the
  ASCII fallback characters so the interpolation step is visible.

Run::

    python examples/gradient/gradient_demo.py

Controls:

* Esc / Ctrl+C — quit
"""

from __future__ import annotations

import sys

from pyink import Box, Text, create_element, render, use_app, use_input
from pyink.core.element import Element
from pyink.externals import Gradient
from pyink.render.keys import Key


def GradientDemo() -> Element:
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
            Text("Gradient demo", bold=True),
            Text("Esc / Ctrl+C to quit", dimColor=True),
            Box(
                Gradient("PyInk", colors=["red", "yellow", "green"], bold=True),
                paddingY=0,
            ),
            Text("Named endpoints:", dimColor=True),
            Gradient(
                "magenta to cyan gradient",
                colors=["magenta", "cyan"],
            ),
            Text("Hex endpoints (RGB rainbow):", dimColor=True),
            Gradient(
                "pyink gradient demo",
                colors=["#ff0000", "#00ff00", "#0000ff"],
            ),
            Text("Bright variants:", dimColor=True),
            Gradient(
                "redBright greenBright blueBright",
                colors=["redBright", "greenBright", "blueBright"],
            ),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(GradientDemo(), columns=60, rows=14)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
