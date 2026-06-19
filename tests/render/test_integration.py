"""Integration test — counter demo with a real signal + threaded timer (PR5).

Mirrors the kind of "live" demo we expect users to write. Uses a fake
:class:`io.StringIO` stdout (no real TTY required) so it runs in CI.

The contract: after starting a render that updates a signal from a
background thread, the captured stdout must contain at least two
distinct frame contents (the initial paint plus at least one repaint
after the signal tick).
"""

from __future__ import annotations

import io
import threading
import time

from pyink import Box, Text, render
from pyink.core.signal import signal


def test_counter_renders_multiple_frames_over_time() -> None:
    counter = signal(0)

    def Counter() -> object:
        return Box(
            Text(lambda: f"{counter.value} ticks"),
            flexDirection="column",
            padding=1,
        )

    out = io.StringIO()

    def tick() -> None:
        time.sleep(0.05)
        counter.value = 1

    t = threading.Thread(target=tick, daemon=True)
    inst = render(
        Counter(),  # type: ignore[arg-type]
        stdout=out,
        columns=40,
        rows=4,
        exit_on_ctrl_c=False,
    )
    t.start()
    time.sleep(0.25)
    inst.unmount()
    t.join(timeout=1.0)

    captured = out.getvalue()
    assert "0 ticks" in captured
    assert "1 ticks" in captured
    # Never use 2J (PRD Decision 3).
    assert "\x1b[2J" not in captured


def test_clear_then_rerender_works() -> None:
    counter = signal(0)

    def Counter() -> object:
        return Text(lambda: f"count={counter.value}")

    out = io.StringIO()
    inst = render(
        Counter(),  # type: ignore[arg-type]
        stdout=out,
        columns=40,
        rows=2,
        exit_on_ctrl_c=False,
    )
    # Sanity: initial frame landed.
    assert "count=0" in out.getvalue()
    inst.clear()
    # After clear the next signal write should land as a fresh initial
    # paint (current_frame forgotten).
    counter.value = 7
    time.sleep(0.2)
    captured_after_clear = out.getvalue()
    assert "count=7" in captured_after_clear
    inst.unmount()


def test_signal_change_does_not_destroy_scrollback() -> None:
    """Inline-mode invariant: never emit a full-screen clear sequence."""
    counter = signal(0)

    def Counter() -> object:
        return Box(
            Text(lambda: f"n={counter.value}"),
            flexDirection="column",
        )

    out = io.StringIO()
    inst = render(
        Counter(),  # type: ignore[arg-type]
        stdout=out,
        columns=30,
        rows=3,
        exit_on_ctrl_c=False,
    )
    for _ in range(5):
        counter.value += 1
        time.sleep(0.05)
    inst.unmount()
    captured = out.getvalue()
    assert "\x1b[2J" not in captured
    # Final state visible.
    assert "n=5" in captured
