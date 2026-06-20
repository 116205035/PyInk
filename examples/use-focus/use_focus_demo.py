"""use-focus example — Tab-driven focus between two boxes.

Reference: ink's ``examples/use-focus/``.

The MVP does not ship a ``use_focus`` hook — this example demonstrates
the equivalent pattern using ``signal`` directly to track which input
has focus. ``Tab`` cycles focus between two boxes; the focused box
paints a rounded green border while the other gets a plain single border.

Run::

    python examples/use-focus/use_focus_demo.py

Controls:

* Tab     — toggle focus between Input A and Input B
* Escape  — quit
"""

from __future__ import annotations

import sys

from pyink import Box, Text, create_element, render, signal, use_app, use_input
from pyink.core.element import Element
from pyink.render.keys import Key


def FocusDemo() -> Element:
    """Build the focus demo tree."""

    def FocusDemoImpl() -> Element:
        focused = signal(0)  # 0 → Input A, 1 → Input B
        app = use_app()

        def on_key(key: Key) -> None:
            if key.tab:
                focused.value = 1 - focused.value
            elif key.escape:
                app.exit(None)

        use_input(on_key)

        def make_box(label: str, idx: int) -> Element:
            # ``idx`` is a function parameter — bound at call time, so each
            # callable reads the right value (PRD Decision 13).
            return Box(
                Text(
                    label,
                    color=lambda: "green" if focused.value == idx else None,
                    bold=lambda: focused.value == idx,
                ),
                borderStyle=lambda: "round" if focused.value == idx else "single",
                borderColor=lambda: "green" if focused.value == idx else None,
                paddingX=1,
            )

        return Box(
            Text("Tab switches focus, Escape quits.", dimColor=True),
            make_box("Input A", 0),
            make_box("Input B", 1),
            flexDirection="column",
            gap=1,
        )

    return create_element(FocusDemoImpl)


def main() -> int:
    inst = render(FocusDemo(), columns=40, rows=12)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
