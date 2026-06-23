"""Table example — column-aligned data tables (Phase 6 PR2).

Reference: ink-table's README examples. PyInk's ``Table`` is a thin
declarative factory that returns a ``box`` column element: each cell is
pinned to its column's resolved width so the columns line up visually
regardless of the surrounding container's main-axis sizing.

This demo mounts two ``Table`` variants side by side:

* the ``list[list[str]]`` positional mode — caller supplies the row
  arrays and ``Table`` synthesises ``Column 1`` / ``Column 2`` headers
  (or accepts an explicit ``columns`` list),
* the ``list[dict[str, str]]`` keyed mode — caller supplies row dicts
  and ``Table`` resolves the union of keys across rows as the column
  order, with missing keys rendering as empty cells.

Run::

    python examples/table/table_demo.py

Controls:

* Esc / Ctrl+C — quit
"""

from __future__ import annotations

import sys

from pyink import Box, Text, create_element, render, use_app, use_input
from pyink.core.element import Element
from pyink.externals import Table
from pyink.render.keys import Key

#: Positional-mode rows. Five rows × four columns of demo data — the
#: canonical "user roster" example. ``Table`` will derive the column
#: widths from the longest cell in each column (``"Samantha"`` for the
#: name column, ``"samantha@example.com"`` for the email column, …).
LIST_ROWS: list[list[str]] = [
    ["Alice", "30", "Engineer", "alice@example.com"],
    ["Bob", "25", "Designer", "bob@example.com"],
    ["Carol", "41", "PM", "carol@example.com"],
    ["Dave", "37", "QA", "dave@example.com"],
    ["Samantha", "29", "Eng Lead", "samantha@example.com"],
]

#: Dict-mode rows. The same data expressed as ``list[dict[str, str]]``
#: so callers can see how ``Table`` resolves the column order from the
#: union of keys (insertion-ordered, missing keys render as empty
#: cells).
DICT_ROWS: list[dict[str, str]] = [
    {"name": "Alice", "age": "30", "role": "Engineer"},
    {"name": "Bob", "age": "25", "role": "Designer", "team": "Web"},
    {"name": "Carol", "age": "41", "role": "PM", "team": "Platform"},
    {"name": "Dave", "role": "QA", "team": "Platform"},
    {"name": "Samantha", "age": "29", "role": "Eng Lead"},
]


def TableDemo() -> Element:
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
            Text("Table demo", bold=True),
            Text("Esc / Ctrl+C to quit", dimColor=True),
            Text("list[list[str]] mode:", dimColor=True),
            Table(
                data=LIST_ROWS,
                columns=["Name", "Age", "Role", "Email"],
            ),
            Text("list[dict[str, str]] mode (mixed keys):", dimColor=True),
            Table(data=DICT_ROWS),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(TableDemo(), columns=64, rows=18)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
