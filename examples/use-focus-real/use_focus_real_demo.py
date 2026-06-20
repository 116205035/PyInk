"""use_focus example — the real focus hooks (Phase 2 PR6).

Reference: ink's ``examples/use-focus/``. PyInk ships
:func:`pyink.use_focus` + :func:`pyink.use_focus_manager` in Phase 2 —
this example uses them directly, replacing the hand-rolled ``signal``-based
``examples/use-focus/use_focus_demo.py`` from the MVP.

Three focusable boxes are mounted inside a ``use_focus_manager`` subtree.
``Tab`` advances focus, ``Shift+Tab`` reverses it; the focused box paints a
rounded green border, the others paint a plain single border. The active
handle's id is printed at the bottom so callers can verify ``focus(id)``
jumps from a key handler (here: digit keys 1/2/3 jump straight to a box).

Run::

    python examples/use-focus-real/use_focus_demo.py

Controls:

* Tab          — focus next
* Shift+Tab    — focus previous
* 1 / 2 / 3    — jump straight to box N
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
    use_focus,
    use_focus_manager,
    use_input,
)
from pyink.core.element import Element
from pyink.render.keys import Key

#: Each focusable box carries a stable id so the key handler can jump to
#: it via ``focus(id=...)``. The label is shown inside the box.
BOXES: tuple[tuple[str, str], ...] = (
    ("input-1", "Input 1"),
    ("input-2", "Input 2"),
    ("input-3", "Input 3"),
)


def FocusableBox(box_id: str, label: str, *, auto_focus: bool) -> Element:
    """One focusable box.

    Wraps the Impl in a factory so each call site gets its own
    ``use_focus`` subscription (hooks must run inside a function-component
    body mounted by the reconciler, not at call time).
    """

    def Impl() -> Element:
        # ``auto_focus=True`` on the first box sets initial focus on mount.
        handle = use_focus({"id": box_id, "auto_focus": auto_focus})

        return Box(
            Text(
                label,
                bold=lambda: handle.is_focused.value,
                color=lambda: "green" if handle.is_focused.value else None,
            ),
            borderStyle=lambda: "round" if handle.is_focused.value else "single",
            borderColor=lambda: "green" if handle.is_focused.value else None,
            paddingX=1,
        )

    return create_element(Impl)


def UseFocusDemo() -> Element:
    """Build the demo tree.

    Hooks run inside the Impl closure so they execute during mount.
    """

    def Impl() -> Element:
        app = use_app()
        focus = use_focus_manager()

        def on_key(key: Key) -> None:
            if key.escape:
                app.exit(None)
                return
            if key.tab:
                if key.shift:
                    focus.focus_previous()
                else:
                    focus.focus_next()
                return
            # Digit-key jumps. ``key.input`` carries the raw keystroke —
            # only single-character digits 1..N are honoured.
            raw = key.input
            if raw and raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(BOXES):
                    focus.focus(BOXES[idx][0])

        use_input(on_key)

        # First box grabs initial focus; the rest leave auto_focus=False
        # so the manager's ``active_index`` is unambiguous.
        children = tuple(
            FocusableBox(box_id, label, auto_focus=(i == 0))
            for i, (box_id, label) in enumerate(BOXES)
        )

        # ``focus.wrap`` injects the manager into the subtree via a
        # Provider so descendant ``use_focus`` calls read it via context.
        body: Element = focus.wrap(
            *children,
            Text(
                lambda: f"Active: {focus.active_id or '(none)'}",
                dimColor=True,
            ),
        )

        return Box(
            Text("use_focus demo (real)", bold=True),
            Text(
                "Tab / Shift+Tab cycles focus, 1..3 jumps, Esc quits.",
                dimColor=True,
            ),
            body,
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(UseFocusDemo(), columns=50, rows=14)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
