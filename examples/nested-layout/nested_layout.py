"""Nested-layout example — column-in-row-in-column with flexGrow.

Reference: ink's ``examples/`` flex tests and the
``measure-and-render`` layout discussion.

This demo builds the classic "sidebar + main content" two-pane layout
out of pure ``Box`` primitives and flexGrow:

::

    ┌──────────────────────────────────────────┐
    │ Header                                    │
    ├─────────┬────────────────────────────────┤
    │ Sidebar │ Title                           │
    │ Item A  │                                  │
    │ Item B  │ (main content area, flexGrow)   │
    │ Item C  │                                  │
    │         │ Status bar                       │
    └─────────┴────────────────────────────────┘
    Footer

Key techniques:

* **Outer column** holds header / main / footer.
* **Main content** is a row containing the sidebar (fixed ``width=15``)
  and the main area (``flexGrow=1`` so it absorbs the remaining width).
* The **sidebar** and **main area** are themselves columns.
* Every region carries a border to make the structure visible.

Run::

    python examples/nested-layout/nested_layout.py

Quit with ``Ctrl+C``.
"""

from __future__ import annotations

import sys

from pyink import Box, Spacer, Text, render
from pyink.core.element import Element


def NestedLayout() -> Element:
    """Build the sidebar + main content two-pane layout."""

    sidebar = Box(
        Text("Sidebar", bold=True),
        Text(""),
        Text("Item A"),
        Text("Item B"),
        Text("Item C"),
        flexDirection="column",
        width=15,
        paddingX=1,
        borderStyle="single",
        borderColor="cyan",
    )

    main_area = Box(
        Text("Main Title", bold=True, color="green"),
        Text(""),
        Box(
            Text(
                "This is the main content area. It flexGrow=1 so it "
                "absorbs whatever width remains after the fixed-width "
                "sidebar.",
                dimColor=True,
            ),
            flexGrow=1,
            padding=1,
            borderStyle="round",
        ),
        Text(""),
        Text("Status: ready", color="yellow"),
        flexDirection="column",
        flexGrow=1,
        paddingX=1,
        borderStyle="single",
        borderColor="green",
    )

    # The "main content" row: sidebar (fixed) + main area (grows).
    main_row = Box(
        sidebar,
        main_area,
        flexDirection="row",
    )

    return Box(
        Text("Nested Layout Demo", bold=True, color="magenta"),
        Text("Outer column → row → inner columns. Ctrl+C to quit.", dimColor=True),
        Spacer(size=1),
        main_row,
        Spacer(size=1),
        Text("Footer — fixed-height row at the bottom", dimColor=True),
        flexDirection="column",
        paddingX=1,
        borderStyle="round",
        borderColor="magenta",
    )


def main() -> int:
    inst = render(NestedLayout(), columns=70, rows=18)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
