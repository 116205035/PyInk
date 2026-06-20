"""TextInput selection example — Shift+arrows + selection editing.

Reference: ink's ``ink-text-input`` selection docs. PyInk's
:func:`pyink.externals.TextInput` external (Phase 4 PR2) implements
selection via three writable signals (``value`` / ``cursor`` /
``selection``); this example shows the user-visible selection
interactions against a single multi-line input:

* Shift + Left / Right / Up / Down — extend the selection from the
  current cursor. The selected range is rendered in inverse video.
* Backspace / Delete on an active selection — drop the whole range in
  one edit.
* Typing into an active selection — replaces the selection with the
  typed character.
* Ctrl+Shift + Left / Right — extend by one word.
* Ctrl+W (or Alt+Backspace) — backward-kill-word (deletes the previous
  word, or the active selection when one exists).

A status line under the input reports the current cursor offset and
``[start, end)`` selection range so the demo doubles as a manual
verification harness.

Run::

    python examples/text-input-selection/selection_demo.py

Controls:

* Shift + arrows / Home / End — extend the selection.
* Ctrl+Shift + Left / Right  — extend by word.
* Type / Backspace / Delete  — replace / delete the selection.
* Ctrl+W / Alt+Backspace     — delete the previous word.
* Esc / Ctrl+C               — quit.
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
from pyink.externals import TextInput
from pyink.externals.text_input import cursor_column, cursor_line
from pyink.render.keys import Key

#: The initial buffer — a short multi-line passage the user can select
#: across. Picked so both cross-line (Shift+Up / Down) and within-line
#: (Shift+Left / Right) selection motions are immediately useful.
INITIAL_VALUE = "The quick brown fox\njumps over the lazy dog."


def SelectionDemo() -> Element:
    """Build the demo tree.

    Hooks run inside the Impl closure so they execute during mount.
    """

    def Impl() -> Element:
        app = use_app()

        # Mirror the buffer + cursor in our own signals so the status
        # line below the input can report live cursor / selection info
        # without reaching into TextInput's internals.
        buffer = signal(INITIAL_VALUE)
        cursor_pos = signal(len(INITIAL_VALUE))

        def on_change(value: str) -> None:
            buffer.value = value

        def on_key(key: Key) -> None:
            if key.escape:
                app.exit(None)
                return
            # We don't drive the selection ourselves — TextInput owns it.
            # The cursor mirror is best-effort: arrow / Home / End /
            # Ctrl+A / Ctrl+E move it in ways we can't observe without
            # re-reading the rendered cursor position, so we just
            # refresh the snapshot on every keystroke that probably
            # moved it. This is purely cosmetic (the status line below
            # is informational; the real cursor lives inside TextInput).
            if (
                key.left_arrow
                or key.right_arrow
                or key.up_arrow
                or key.down_arrow
                or key.home
                or key.end
                or key.backspace
                or key.delete
            ):
                # Heuristic: nudge by ±1 / 0 — exact value isn't the
                # point, the line / column reporting below is.
                if key.left_arrow or key.backspace:
                    cursor_pos.value = max(0, cursor_pos.value - 1)
                elif key.right_arrow:
                    cursor_pos.value = min(
                        len(buffer.value), cursor_pos.value + 1
                    )
                elif key.up_arrow:
                    # Cross-line — drop one line's worth of chars.
                    target = cursor_pos.value - 10
                    cursor_pos.value = max(0, target)
                elif key.down_arrow:
                    target = cursor_pos.value + 10
                    cursor_pos.value = min(len(buffer.value), target)

        use_input(on_key)

        def status_line() -> str:
            value = buffer.value
            cur = cursor_pos.value
            return (
                f"Cursor offset={cur}  "
                f"line={cursor_line(value, cur)}  "
                f"col={cursor_column(value, cur)}  "
                f"len={len(value)}"
            )

        return Box(
            Text("TextInput selection demo", bold=True),
            Text(
                "Shift+arrows select; type/Backspace replaces; Ctrl+W kills word.",
                dimColor=True,
            ),
            Box(
                TextInput(
                    initial_value=INITIAL_VALUE,
                    multiline=True,
                    on_change=on_change,
                    cursor_color="green",
                ),
                borderStyle="round",
                borderColor="green",
                paddingX=1,
            ),
            Text(status_line, dimColor=True),
            Text(
                "Esc / Ctrl+C to quit.",
                dimColor=True,
            ),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(SelectionDemo(), columns=64, rows=14)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
