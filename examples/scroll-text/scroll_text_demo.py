"""Text.scroll_offset example — public scroll API (Phase 5 PR3).

Demonstrates the public ``scroll_offset`` prop on :func:`pyink.Text`:
a long multi-line Text (50 numbered lines) is mounted inside a
fixed-height Box; arrow keys advance / retreat the scroll offset by
one line, and a status line reports the current offset.

Callers who only need scrolling over a single text payload can reach
for ``scroll_offset`` directly — no external list virtualisation
needed.

Run::

    python examples/scroll-text/scroll_text_demo.py

Controls:

* Up / Down    — scroll one line.
* PageUp / PageDown — scroll five lines.
* Home / End   — jump to the top / bottom.
* Esc / Ctrl+C — quit.
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
from pyink.core.signal import Signal
from pyink.render.keys import Key

#: Number of source lines. Larger than the visible viewport so the
#: scroll_offset actually shifts what is visible.
TOTAL_LINES: int = 50

#: Visible rows inside the box (matches Box height).
VIEWPORT_HEIGHT: int = 10

#: Page-up / page-down step. Slightly smaller than the viewport so
#: context from the previous page stays on screen.
PAGE_STEP: int = 5

#: Maximum valid offset — past this the viewport would show empty rows
#: at the bottom. Pre-computed so the Home / End handler can clamp
#: without re-reading the text.
MAX_OFFSET: int = TOTAL_LINES - VIEWPORT_HEIGHT


def _clamp(value: int) -> int:
    """Clamp ``value`` to ``[0, MAX_OFFSET]``."""
    return max(0, min(value, MAX_OFFSET))


def ScrollTextDemo() -> Element:
    """Build the demo tree.

    Hooks run inside the Impl closure so they execute during mount.
    """

    def Impl() -> Element:
        app = use_app()
        offset: Signal[int] = signal(0)

        def on_key(key: Key) -> None:
            if key.escape:
                app.exit(None)
                return
            current = offset.value
            if key.up_arrow:
                offset.value = _clamp(current - 1)
            elif key.down_arrow:
                offset.value = _clamp(current + 1)
            elif key.page_up:
                offset.value = _clamp(current - PAGE_STEP)
            elif key.page_down:
                offset.value = _clamp(current + PAGE_STEP)
            elif getattr(key, "home", False) or key.input in ("\x1b[H", "\x1b[1~"):
                offset.value = 0
            elif getattr(key, "end", False) or key.input in ("\x1b[F", "\x1b[4~"):
                offset.value = MAX_OFFSET

        use_input(on_key)

        # Build the source text once — 50 numbered lines joined by
        # newlines. ``Text.scroll_offset`` slices this payload at paint
        # time, so the cost of a scroll is O(1) signal write, not
        # O(TOTAL_LINES) string rebuild.
        body = "\n".join(f"Line {i:02d}" for i in range(TOTAL_LINES))

        return Box(
            Text("Text.scroll_offset demo", bold=True),
            Text(
                "Up/Down scroll, PageUp/PageDown jump, Home/End ends, Esc quits.",
                dimColor=True,
            ),
            # The inner Box pins its height to VIEWPORT_HEIGHT so the
            # layout grants exactly that many rows; Text.scroll_offset
            # then decides which slice of the 50 lines is visible.
            Box(
                Text(body, scroll_offset=offset),
                height=VIEWPORT_HEIGHT,
                borderStyle="single",
            ),
            Text(
                lambda: f"scroll_offset: {offset.value}/{MAX_OFFSET}",
                dimColor=True,
            ),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(ScrollTextDemo(), columns=50, rows=18)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
