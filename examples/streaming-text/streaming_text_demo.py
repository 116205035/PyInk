"""StreamingText example — animated streaming text (Phase 3 PR6).

Reference: Claude Code's ``<StreamingText>`` pattern. A background thread
appends characters to a pair of ``Signal[str]`` buffers one at a time,
simulating an AI token stream. Two ``StreamingText`` mounts read from
the buffers with different ``reveal_speed`` settings so the user can
compare the **instant** path (``reveal_speed=0`` — characters appear as
soon as the buffer grows) with the **smooth** path (``reveal_speed=20``
chars/s — a typing animation that trails the buffer).

Both mounts use ``cursor='▋'`` and ``cursor_color='green'`` so the
leading cursor glyph is visible while the stream is in flight.

No optional dependency is required — ``StreamingText`` ships in core
``pyink.externals``.

Run::

    python examples/streaming-text/streaming_text_demo.py

Controls:

* Esc / Ctrl+C — quit
"""

from __future__ import annotations

import sys
import threading
import time

from pyink import Box, Text, create_element, render, signal, use_app, use_input
from pyink.core.element import Element
from pyink.externals import StreamingText
from pyink.render.keys import Key

#: The simulated AI reply. Picked to span multiple sentences so the
#: difference between instant and smooth reveal is visible.
REPLY: str = (
    "Hello! I'm an AI assistant streaming a reply token by token. "
    "Watch the two panels below: the top one shows text as soon as it "
    "arrives, the bottom one smooths the reveal into a typing animation."
)

#: Inter-character delay (seconds). Smaller => faster stream.
CHAR_DELAY: float = 0.04


def StreamingTextDemo() -> Element:
    """Build the demo tree.

    Hooks run inside the Impl closure so they execute during mount.
    The stream worker thread is started once at mount time and runs
    until the buffer is exhausted; the daemon flag means it is reaped
    automatically when the process exits.
    """

    def Impl() -> Element:
        app = use_app()
        # Two independent buffers, one per panel. The worker thread
        # writes the same characters into both — visually the two
        # panels show the same content, but each ``StreamingText``
        # mounts against a distinct signal so the two subscribe paths
        # don't collide. (A single shared signal works too in the
        # common case but ticks a known limitation where two sibling
        # function-component children subscribing to the same signal
        # can keep the render-loop's re-paint from firing on every
        # write; separate signals sidestep that entirely.)
        instant_buffer = signal("")
        smooth_buffer = signal("")

        def on_key(key: Key) -> None:
            if key.escape:
                app.exit(None)

        use_input(on_key)

        def stream_worker() -> None:
            """Background thread: append one character at a time."""
            for ch in REPLY:
                instant_buffer.value = instant_buffer.value + ch
                smooth_buffer.value = smooth_buffer.value + ch
                time.sleep(CHAR_DELAY)

        threading.Thread(target=stream_worker, daemon=True).start()

        # ``use_app`` does not expose a teardown hook, so we rely on the
        # daemon-thread pattern: when the process exits the worker is
        # reaped by the daemon flag; when Esc is pressed the worker
        # keeps writing into the unmounted signal (safe — signal
        # writes are no-ops once nobody subscribes).
        return Box(
            Text("StreamingText demo", bold=True),
            Text("Esc / Ctrl+C to quit", dimColor=True),
            Text("Instant (reveal_speed=0):", dimColor=True),
            StreamingText(instant_buffer, cursor="▋", cursor_color="green"),
            Text("Smooth (reveal_speed=20):", dimColor=True),
            StreamingText(
                smooth_buffer,
                cursor="▋",
                cursor_color="green",
                reveal_speed=20,
            ),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(StreamingTextDemo(), columns=70, rows=12)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
