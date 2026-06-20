"""Spinner example — animated loading indicators (Phase 2 PR2).

Reference: ink-spinner's CLI demo. PyInk ships the canonical ``cli-spinners``
frame data via :data:`pyink.externals.SPINNERS`; this example mounts a small
gallery of them side by side, each labelled with its name and painted in a
different colour so the frames are easy to tell apart at a glance.

Each spinner runs on its own ``use_interval`` worker thread (started by the
``Spinner`` external). The frame index lives in a signal the component
writes to from the worker; the render loop subscribes through the callable
``Text`` child and re-paints on every tick.

Run::

    python examples/spinner/spinner_demo.py

Controls:

* Esc / Ctrl+C — quit
"""

from __future__ import annotations

import sys

from pyink import Box, Text, create_element, render, use_app, use_input
from pyink.core.element import Element
from pyink.externals import Spinner
from pyink.render.keys import Key

#: The (name, colour) pairs to show. Picked to span a few visual
#: families: Braille dots, lines, ASCII, lunar phases, weather. Each
#: Spinner renders its first frame on the initial paint, so the gallery
#: is non-empty even before the first tick fires.
SPINNER_SHOWCASE: tuple[tuple[str, str], ...] = (
    ("dots", "green"),
    ("dots2", "cyan"),
    ("line", "yellow"),
    ("arc", "magenta"),
    ("moon", "blue"),
    ("star", "red"),
)


def SpinnerDemo() -> Element:
    """Build the demo tree.

    Hooks run inside the Impl closure so they execute during mount.
    """

    def Impl() -> Element:
        app = use_app()

        def on_key(key: Key) -> None:
            if key.escape:
                app.exit(None)

        use_input(on_key)

        def row(name: str, color: str) -> Element:
            # Children-before-props positional style (PRD Decision 8): the
            # Spinner element and its label are positional, everything
            # else flows through kwargs.
            return Box(
                Spinner(type=name, color=color),
                Text(f" {name}", color=color),
                Text(f" ({color})", dimColor=True),
                flexDirection="row",
                alignItems="center",
                gap=1,
            )

        return Box(
            Text("Spinner demo", bold=True),
            Text("Esc / Ctrl+C to quit", dimColor=True),
            Box(
                *(row(name, color) for name, color in SPINNER_SHOWCASE),
                flexDirection="column",
                gap=0,
            ),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(SpinnerDemo(), columns=40, rows=12)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
