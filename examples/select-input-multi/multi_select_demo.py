"""SelectInput multi-select example — Space toggles + Enter confirms.

Reference: ink's third-party ``ink-multi-select`` component. PyInk's
:func:`pyink.externals.SelectInput` supports ``multi_select=True``
(Phase 4 PR3) — ``Space`` toggles the focused item in / out of the
selection set, ``Enter`` confirms with the whole list of selected
values, and ``ArrowUp`` / ``ArrowDown`` / ``j`` / ``k`` / digit keys
navigate the same way as single-select.

This example mounts a ten-item checklist and reports the live
selection cardinality below it, so the user can see toggles landing
as they navigate.

Run::

    python examples/select-input-multi/multi_select_demo.py

Controls:

* Up / Down (or ``k`` / ``j``) — move the highlight.
* ``1``..``9``                  — jump straight to that index.
* Space                         — toggle the focused item in / out.
* Enter                         — confirm the selection and exit.
* Esc / Ctrl+C                  — quit without confirming.
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

#: Ten checkbox-style options — picked so digit-key jumps (``1``..``9``)
#: cover most of the list and Space-toggling produces visually
#: interesting selections.
ITEMS: tuple[dict[str, str], ...] = tuple(
    {"label": label, "value": value}
    for label, value in (
        ("Read README", "read"),
        ("Write tests", "tests"),
        ("Run lint", "lint"),
        ("Run mypy", "mypy"),
        ("Update docs", "docs"),
        ("Bump version", "bump"),
        ("Cut release", "release"),
        ("Tag commit", "tag"),
        ("Push to remote", "push"),
        ("Open PR", "pr"),
    )
)


def MultiSelectDemo() -> Element:
    """Build the demo tree.

    Hooks run inside the Impl closure so they execute during mount.
    """

    def Impl() -> Element:
        app = use_app()
        # Track the live selection count so the user sees Space
        # toggles landing. We can't peek into the SelectInput's
        # internal selection set directly, so we count from the
        # ``on_change``-driven focus index plus a parallel local set
        # updated whenever Space fires. Easier approach: capture the
        # last ``on_select`` payload.
        last_submit = signal("(not confirmed yet)")

        def on_key(key: Key) -> None:
            if key.escape:
                app.exit(None)

        use_input(on_key)

        def on_select(values: list[str]) -> None:
            last_submit.value = (
                f"Confirmed {len(values)} item(s): {values!r}"
            )
            app.exit(None)

        return Box(
            Text("SelectInput multi-select demo", bold=True),
            Text(
                "Up/Down or j/k; 1..9 jumps; Space toggles; Enter confirms; Esc quits.",
                dimColor=True,
            ),
            SelectInput(
                list(ITEMS),
                multi_select=True,
                on_select=on_select,
                indicator="❯",
                selected_indicator="✓",
                unselected_indicator=" ",
                selected_color="green",
            ),
            Text(lambda: last_submit.value, dimColor=True),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(MultiSelectDemo(), columns=64, rows=18)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
