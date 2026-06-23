"""ProgressBar example — animated character-cell progress bars (Phase 6 PR2).

Reference: ink-progress-bar's README examples. PyInk's ``ProgressBar``
renders a horizontal bar whose filled portion grows proportionally with
``value``; the component accepts a ``Signal[float]`` so a background
thread can drive the animation simply by writing to the signal.

This demo mounts three bars side by side, each driven by its own
background thread at a different rate:

* a green bar filling over ~3 s, then looping back to 0,
* a yellow bar filling over ~5 s, then looping back to 0,
* a cyan ASCII-style bar (``=`` / ``-``) filling over ~2 s, then
  looping back to 0.

The looping behaviour is the demo's whole point: once the value reaches
1.0 the worker resets to 0.0 and the bar starts the next pass. Esc
tears everything down.

Run::

    python examples/progress-bar/progress_bar_demo.py

Controls:

* Esc / Ctrl+C — quit
"""

from __future__ import annotations

import sys
import threading
import time

from pyink import Box, Text, create_element, render, signal, use_app, use_input
from pyink.core.element import Element
from pyink.core.signal import Signal
from pyink.externals import ProgressBar
from pyink.render.keys import Key

#: Loop period for each bar — smaller means a faster fill.
PERIODS: tuple[float, ...] = (3.0, 5.0, 2.0)


def _drive(bar: Signal[float], period: float) -> None:
    """Advance ``bar`` from 0.0 to 1.0 over ``period`` seconds, then loop.

    Writes happen at ~30 Hz so the animation reads smoothly without
    swamping the render-loop's frame diff. ``period`` defines the wall-
    clock time for a full 0% → 100% sweep; once the value hits 1.0 we
    reset to 0.0 and start the next pass — the bar never stops until
    the component unmounts and the daemon thread is torn down.
    """
    step_seconds = 1.0 / 30.0
    increment = step_seconds / period
    while True:
        time.sleep(step_seconds)
        next_value = bar.value + increment
        if next_value >= 1.0:
            bar.value = 0.0
        else:
            bar.value = next_value


def ProgressBarDemo() -> Element:
    """Build the demo tree.

    Hooks run inside the Impl closure so they execute during mount.
    """

    def Impl() -> Element:
        app = use_app()

        def on_key(key: Key) -> None:
            if key.escape:
                app.exit(None)

        use_input(on_key)

        # Three signals / three threads / three different speeds. Each
        # signal starts at 0.0 so the initial paint shows empty bars.
        green_bar: Signal[float] = signal(0.0)
        yellow_bar: Signal[float] = signal(0.0)
        ascii_bar: Signal[float] = signal(0.0)

        for bar, period in (
            (green_bar, PERIODS[0]),
            (yellow_bar, PERIODS[1]),
            (ascii_bar, PERIODS[2]),
        ):
            threading.Thread(
                target=_drive, args=(bar, period), daemon=True
            ).start()

        return Box(
            Text("ProgressBar demo", bold=True),
            Text("Esc / Ctrl+C to quit", dimColor=True),
            Text("Slow (3s loop):", dimColor=True),
            ProgressBar(value=green_bar, color="green"),
            Text("Slower (5s loop):", dimColor=True),
            ProgressBar(
                value=yellow_bar, width=24, color="yellow"
            ),
            Text("Fast ASCII (2s loop):", dimColor=True),
            ProgressBar(
                value=ascii_bar,
                width=20,
                character="=",
                remaining_character="-",
                color="cyan",
            ),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(ProgressBarDemo(), columns=48, rows=14)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
