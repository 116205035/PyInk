"""use-window-size example — react to terminal resize.

Reference: ink's ``useWindowSize`` hook and ``examples/terminal-resize``.

``use_window_size()`` returns a :class:`pyink.WindowSize` snapshot
with the current ``columns`` and ``rows``. The Instance's existing
resize subscription triggers a re-render on resize; on re-render the
component body re-reads the live size, so reading the snapshot here
is enough — no extra subscription is needed at the hook level.

This demo:

* prints the current ``columns × rows`` at the top of the frame.
* below it, switches between **single-column** and **two-column**
  layouts depending on whether ``columns < 60``. Resize your terminal
  window to see the layout switch live.

Run::

    python examples/use-window-size/use_window_size.py

Quit with ``Ctrl+C``.
"""

from __future__ import annotations

import sys

from pyink import Box, Spacer, Text, create_element, render, use_window_size
from pyink.core.element import Element

#: Below this width the layout collapses to a single column.
SINGLE_COLUMN_THRESHOLD: int = 60


def UseWindowSize() -> Element:
    """Factory + Impl so the hook runs during mount."""

    def Impl() -> Element:
        size = use_window_size()
        columns = size.columns
        rows = size.rows
        is_two_column = columns >= SINGLE_COLUMN_THRESHOLD

        mode = "two-column" if is_two_column else "single-column"

        def pane(label: str, color: str) -> Element:
            return Box(
                Text(label, bold=True, color=color),
                Text("Resize the terminal", dimColor=True),
                Text("to see this switch.", dimColor=True),
                flexGrow=1,
                paddingX=1,
                borderStyle="round",
                borderColor=color,
            )

        if is_two_column:
            body: Element = Box(
                pane("Left pane", "cyan"),
                pane("Right pane", "magenta"),
                flexDirection="row",
                gap=1,
            )
        else:
            body = Box(
                pane("Only pane", "yellow"),
                flexDirection="column",
            )

        return Box(
            Text("use_window_size demo", bold=True),
            Text(lambda: f"Current terminal size: {columns} x {rows}"),
            Text(lambda: f"Layout mode: {mode} (threshold={SINGLE_COLUMN_THRESHOLD})"),
            Spacer(size=1),
            body,
            flexDirection="column",
            padding=1,
        )

    return create_element(Impl)


def main() -> int:
    inst = render(UseWindowSize(), columns=80, rows=12)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
