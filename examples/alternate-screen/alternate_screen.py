"""Alternate-screen example — full-screen UI with scrollback preservation.

Reference: ink's ``render(tree, { stdout, stdin })`` alternate-screen
mode and the ``alternate-screen`` npm package.

PyInk ships the same capability as a keyword argument to
:func:`pyink.render.render`: pass ``alternate_screen=True`` and the
pipeline emits ``\\x1b[?1049h`` on mount and ``\\x1b[?1049l`` on
unmount. The atexit + SIGINT hooks registered by the Instance guarantee
the main screen buffer is restored even when the process crashes —
the user's previous scrollback is preserved.

Run::

    python examples/alternate-screen/alternate_screen.py

Controls:

* Esc / Ctrl+C — quit (the alternate screen is exited and the previous
  terminal contents, including scrollback, are restored).

What to verify manually:

1. ``echo "scrollback line" && python examples/alternate-screen/alternate_screen.py``
   shows the example full-screen UI.
2. Quit with Esc or Ctrl+C — the terminal returns to the previous
   buffer and ``scrollback line`` is still on screen.
"""

from __future__ import annotations

import sys

from pyink import Box, Text, create_element, render, signal, use_app, use_input
from pyink.core.element import Element
from pyink.render.keys import Key


def AlternateScreen() -> Element:
    """Build the demo tree (factory + Impl so hooks run during mount)."""

    def Impl() -> Element:
        # A signal to demonstrate that interactions still work inside
        # the alternate screen — press ``i`` to toggle a "Pressed!" badge.
        pressed = signal(0)
        app = use_app()

        def on_key(key: Key) -> None:
            if key.escape:
                app.exit(None)
                return
            # Any other key bumps the press counter — proves the live
            # render loop is fully wired inside the alternate screen.
            pressed.value += 1

        use_input(on_key)

        return Box(
            Text("Alternate Screen Demo", bold=True, color="cyan"),
            Text(""),
            Text(
                "The terminal is now showing the alternate screen buffer.",
                dimColor=True,
            ),
            Text(
                "Your previous scrollback is preserved — quit to restore it.",
                dimColor=True,
            ),
            Text(""),
            Text(lambda: f"Keystrokes seen this session: {pressed.value}"),
            Text(""),
            Text("Press Esc or Ctrl+C to exit and restore the screen.", bold=True),
            flexDirection="column",
            paddingX=2,
            paddingY=1,
            borderStyle="round",
            borderColor="cyan",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(AlternateScreen(), alternate_screen=True, exit_on_ctrl_c=True)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
