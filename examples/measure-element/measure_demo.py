"""measure_element example — reactive element sizing (Phase 2 PR7).

Reference: ink's ``measureElement`` API + the related
``examples/measure-element`` demo. PyInk exposes the same surface as two
entry points:

* :func:`pyink.measure_element` — imperative snapshot.
* :func:`pyink.use_box_metrics` — reactive subscription returning a
  :class:`pyink.Computed` of :class:`pyink.BoxMetrics`.

This demo:

* mounts a Box with a ``ref`` prop,
* subscribes via ``use_box_metrics`` so the metrics refresh after every
  layout pass,
* prints the live ``width × height`` next to the box,
* switches the box's content based on the measured width: below 60
  columns we show a short label, otherwise a longer descriptive string.
  Resizing the terminal window therefore flips the content live — a
  minimal demonstration of dynamic layout driven by measurement.

Run::

    python examples/measure-element/measure_demo.py

Controls:

* Esc / Ctrl+C — quit
"""

from __future__ import annotations

import sys

from pyink import (
    Box,
    Text,
    create_element,
    render,
    use_app,
    use_box_metrics,
    use_input,
)
from pyink.core.element import Element
from pyink.core.signal import Ref, ref
from pyink.layout import LayoutNode
from pyink.render.keys import Key

#: Below this measured width the demo collapses to its short label.
NARROW_THRESHOLD: int = 60


def MeasureDemo() -> Element:
    """Build the demo tree.

    Hooks run inside the Impl closure so they execute during mount.
    """

    def Impl() -> Element:
        app = use_app()
        # ``ref`` is the PR1 non-reactive holder. The layout pass
        # back-fills it with the freshly-built LayoutNode after each
        # paint; ``use_box_metrics`` re-reads it via the layout epoch.
        measured_ref: Ref[LayoutNode | None] = ref(None)
        metrics = use_box_metrics(measured_ref)

        def on_key(key: Key) -> None:
            if key.escape:
                app.exit(None)

        use_input(on_key)

        def width_text() -> str:
            snap = metrics.value
            if not snap.has_measured or snap.width is None:
                return "Width: (not measured yet)"
            return f"Width: {snap.width}    Height: {snap.height}"

        def body_content() -> str:
            snap = metrics.value
            if not snap.has_measured or snap.width is None:
                return "(measuring…)"
            if snap.width < NARROW_THRESHOLD:
                return "narrow"
            return "wide-enough to show a longer description"

        # The measured Box must carry ``ref=measured_ref`` so the layout
        # pass can find it and write the post-layout LayoutNode back.
        measured_box: Element = Box(
            Text(body_content),
            ref=measured_ref,
            paddingX=1,
            borderStyle="round",
            borderColor="cyan",
        )

        return Box(
            Text("measure_element demo", bold=True),
            Text(
                "Resize the terminal — content flips below "
                f"{NARROW_THRESHOLD} cols.",
                dimColor=True,
            ),
            Text(width_text),
            Text(lambda: f"Threshold: {NARROW_THRESHOLD}"),
            measured_box,
            Text("Esc / Ctrl+C to quit", dimColor=True),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(MeasureDemo(), columns=70, rows=12)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
