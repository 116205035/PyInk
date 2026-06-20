"""SelectInput example — the real external (Phase 4 PR3).

Reference: ink's third-party ``ink-select-input`` component. PyInk
ships :func:`pyink.externals.SelectInput` in Phase 4 — this example
mounts it directly to contrast with the hand-rolled
:mod:`examples.select-input.select_input` demo from the MVP (which
built the same UX out of ``use_input`` + ``signal`` primitives).

The real external handles keyboard navigation internally — arrow keys
/ ``j`` / ``k`` / digit jumps are all wired up, so the demo only needs
to forward the selected value to a status line and exit on confirm.

Run::

    python examples/select-input-real/select_input_demo.py

Controls:

* Up / Down (or ``k`` / ``j``) — move the highlight.
* ``1``..``9``                  — jump straight to that index.
* Enter                         — confirm and exit.
* Esc / Ctrl+C                  — quit without selecting.
"""

from __future__ import annotations

import sys

from pyink import (
    Box,
    Text,
    create_element,
    render,
    signal,
    use_app,
    use_input,
)
from pyink.core.element import Element
from pyink.externals import SelectInput
from pyink.render.keys import Key

#: The showcase list — same fruit set as the MVP-era
#: ``examples/select-input`` demo so the two can be compared directly.
ITEMS: tuple[str, ...] = (
    "Apple",
    "Banana",
    "Cherry",
    "Date",
    "Elderberry",
)


def SelectInputDemo() -> Element:
    """Build the demo tree.

    Hooks run inside the Impl closure so they execute during mount.
    """

    def Impl() -> Element:
        app = use_app()
        selected = signal("(nothing selected yet)")

        def on_key(key: Key) -> None:
            if key.escape:
                app.exit(None)

        use_input(on_key)

        def on_select(value: str) -> None:
            selected.value = f"Selected: {value!r}"
            # Exit after confirmation — matches the MVP-era demo's UX.
            app.exit(None)

        def on_change(idx: int) -> None:
            # Side-effect only — we don't render this, but it
            # demonstrates the callback surface.
            del idx

        return Box(
            Text("SelectInput demo (real external)", bold=True),
            Text("Up/Down or j/k; 1..5 jumps; Enter confirms; Esc quits.", dimColor=True),
            SelectInput(
                list(ITEMS),
                on_select=on_select,
                on_change=on_change,
                indicator="❯",
                selected_color="green",
            ),
            Text(lambda: selected.value, dimColor=True),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(SelectInputDemo(), columns=48, rows=14)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
