"""computed + batch example — derived state and write coalescing.

Reference: SolidJS / Vue 3 / Preact Signals' ``computed`` + ``batch``.

PyInk ships two reactive primitives that this demo exercises together:

* :func:`pyink.computed` — a lazily-evaluated derived value that
  re-computes whenever one of its source signals changes. ``double``
  here is ``computed(lambda: count.value * 2)``.
* :func:`pyink.batch` — coalesce multiple writes into a single
  notification flush. Writing 5 times inside a ``with batch:`` block
  notifies every subscriber **once**, not five times.

The demo tracks how many times an ``effect`` body has run via a
separate counter signal. Compare:

* Press ``+`` — runs ``count.value += 1`` five times inside one
  ``batch`` → ``effect`` fires **once**, but ``count`` jumps by 5.
* Press ``-`` — runs ``count.value -= 1`` once per press → every
  press triggers an ``effect`` re-run.

The side-by-side comparison makes the value of ``batch`` obvious:
one user keystroke can mutate many signals while still producing
exactly one repaint.

Run::

    python examples/computed-batch/computed_batch.py

Controls:

* ``+`` — batched increment (5 writes, 1 effect run).
* ``-`` — single decrement (1 write, 1 effect run each).
* Ctrl+C — quit.
"""

from __future__ import annotations

import sys

from pyink import (
    Box,
    Text,
    batch,
    computed,
    create_element,
    effect,
    render,
    signal,
    use_input,
)
from pyink.core.element import Element
from pyink.core.signal import Signal
from pyink.render.keys import Key

#: How many writes a single ``+`` press performs inside one batch.
BATCH_SIZE: int = 5


def ComputedBatch() -> Element:
    """Factory + Impl so hooks run during mount."""

    def Impl() -> Element:
        count: Signal[int] = signal(0)
        # ``double`` is recomputed lazily — its function only runs when
        # ``count`` changes and someone reads ``double.value``.
        double = computed(lambda: count.value * 2)

        # Tracks how many times the effect body has executed. The body
        # reads ``count.value`` so it subscribes to ``count``; every
        # notification flush that includes ``count`` re-runs it.
        effect_runs: Signal[int] = signal(0)

        def render_loop() -> None:
            # Reading ``count.value`` here establishes the subscription;
            # the bump records how many times this body has run.
            _ = count.value
            effect_runs.value += 1

        effect(render_loop)
        # The initial effect run during mount bumps ``effect_runs`` to a
        # small non-zero baseline. We don't try to zero it here — the
        # important comparison for the user is *relative*: a ``+`` press
        # adds 1 (batched), a ``-`` press adds 1 per press (unbatched).

        def on_key(key: Key) -> None:
            if key.input == "+":
                # Five writes, one notification flush — the effect
                # body runs exactly once even though ``count`` changed
                # five times.
                def batched() -> None:
                    for _ in range(BATCH_SIZE):
                        count.value += 1

                batch(batched)
            elif key.input == "-":
                # A single write — every press re-runs the effect.
                count.value -= 1

        # Hook up keyboard handling.
        use_input(on_key)

        return Box(
            Text("computed + batch demo", bold=True),
            Text("Press + for a batched +5, - for a single -1.", dimColor=True),
            Text("Ctrl+C to quit.", dimColor=True),
            Text(""),
            Text(lambda: f"Count:  {count.value}", bold=True),
            Text(lambda: f"Double: {double.value}", color="green"),
            Text(
                lambda: (
                    f"Effect runs: {effect_runs.value} "
                    f"(1 per '+' press, 1 per '-' press)"
                ),
                color="yellow",
            ),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(ComputedBatch(), columns=60, rows=10)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
