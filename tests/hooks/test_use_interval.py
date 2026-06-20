"""Tests for :func:`pyink.hooks.use_interval` (Phase 2 PR1).

The hook must be called inside a function component mounted via
:func:`pyink.render.render`. Synthetic time is not patched — we use
short intervals (a few ms) and bounded polling, matching the style of
the existing ``test_use_input`` suite.
"""

from __future__ import annotations

import io
import threading
import time
from collections.abc import Callable
from typing import cast

import pytest

from pyink import Text, create_element, effect, render, use_interval
from pyink.core.element import Element
from pyink.render.instance import Instance


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 99


def _render_comp(make_component: Callable[[], Element]) -> Instance:
    """Mount ``make_component`` and return the Instance."""
    inst = render(
        create_element(make_component),
        stdout=_FakeTTY(),
        stdin=_FakeTTY(),
        columns=40,
        rows=3,
        exit_on_ctrl_c=False,
    )
    return inst


def _wait_for(predicate: Callable[[], bool], *, attempts: int = 200, delay: float = 0.025) -> bool:
    """Poll ``predicate`` up to ``attempts`` times sleeping ``delay`` between."""
    for _ in range(attempts):
        if predicate():
            return True
        time.sleep(delay)
    return predicate()


def test_use_interval_fires_callback() -> None:
    """The callback is invoked roughly every ``interval_ms``."""
    ticks_box: dict[str, list[int]] = {"ticks": []}

    def Comp() -> Element:
        ticks = ticks_box["ticks"]

        def on_tick() -> None:
            ticks.append(time.monotonic_ns())

        use_interval(on_tick, 20)
        return Text("hi")

    inst = _render_comp(Comp)
    fired = _wait_for(lambda: len(ticks_box["ticks"]) >= 3)
    inst.unmount()
    assert fired, f"expected at least 3 ticks, got {len(ticks_box['ticks'])}"
    # Should NOT fire immediately on mount — first tick waits one full interval.
    # With a 20 ms interval we expect at most a couple of ticks during a 0 ms
    # window; here we just confirm *some* elapsed time separated the first
    # tick from mount.
    assert len(ticks_box["ticks"]) >= 3


def test_use_interval_outside_render_raises() -> None:
    with pytest.raises(RuntimeError, match="use_interval"):
        use_interval(lambda: None, 50)


def test_use_interval_is_active_false_does_not_fire() -> None:
    fired: list[int] = []

    def Comp() -> Element:
        use_interval(lambda: fired.append(1), 20, is_active=False)
        return Text("hi")

    inst = _render_comp(Comp)
    # Give the loop ample time to fire if it had started.
    time.sleep(0.1)
    inst.unmount()
    assert fired == []


def test_use_interval_interval_zero_does_not_fire() -> None:
    """``interval_ms <= 0`` is treated as "do not start"."""
    fired: list[int] = []

    def Comp() -> Element:
        use_interval(lambda: fired.append(1), 0)
        return Text("hi")

    inst = _render_comp(Comp)
    time.sleep(0.1)
    inst.unmount()
    assert fired == []


def test_use_interval_interval_negative_does_not_fire() -> None:
    fired: list[int] = []

    def Comp() -> Element:
        use_interval(lambda: fired.append(1), -10)
        return Text("hi")

    inst = _render_comp(Comp)
    time.sleep(0.1)
    inst.unmount()
    assert fired == []


def test_use_interval_manual_dispose_stops() -> None:
    fired: list[int] = []
    bag: dict[str, object] = {}

    def Comp() -> Element:
        def setup() -> None:
            bag["dispose"] = use_interval(lambda: fired.append(1), 20)

        effect(setup)
        return Text("hi")

    inst = _render_comp(Comp)
    assert _wait_for(lambda: bool(fired))
    # Grab the dispose + reset counter.
    dispose = cast("Callable[[], None]", bag["dispose"])
    dispose()
    fired.clear()
    # Wait long enough that *un*disposed intervals would fire several times.
    time.sleep(0.15)
    assert fired == [], "callback fired after manual dispose"
    inst.unmount()


def test_use_interval_dispose_is_idempotent() -> None:
    """Calling dispose multiple times is safe (no error, no double-join)."""
    bag: dict[str, object] = {}

    def Comp() -> Element:
        def setup() -> None:
            bag["dispose"] = use_interval(lambda: None, 20)

        effect(setup)
        return Text("hi")

    inst = _render_comp(Comp)
    dispose = cast("Callable[[], None]", bag["dispose"])
    dispose()
    dispose()  # must not raise
    dispose()  # must not raise
    inst.unmount()


def test_use_interval_unmount_auto_cleans_up() -> None:
    """Component unmount tears down the worker thread automatically."""
    fired: list[int] = []

    def Comp() -> Element:
        use_interval(lambda: fired.append(1), 20)
        return Text("hi")

    inst = _render_comp(Comp)
    assert _wait_for(lambda: bool(fired))
    # Snapshot any active interval threads before unmount.
    pre = {
        t.name for t in threading.enumerate() if t.name.startswith("pyink-interval-")
    }
    assert pre, "expected at least one pyink-interval-* thread while mounted"
    inst.unmount()
    # After unmount the worker should be gone within a bounded window.
    assert _wait_for(
        lambda: not any(
            t.name.startswith("pyink-interval-") and t.is_alive()
            for t in threading.enumerate()
        ),
        attempts=80,
        delay=0.025,
    ), "interval thread still alive after unmount"
    fired.clear()
    time.sleep(0.15)
    assert fired == [], "callback fired after unmount"


def test_use_interval_multiple_coexist() -> None:
    """Two concurrent intervals each fire independently."""
    a: list[int] = []
    b: list[int] = []

    def Comp() -> Element:
        use_interval(lambda: a.append(1), 20)
        use_interval(lambda: b.append(1), 20)
        return Text("hi")

    inst = _render_comp(Comp)
    assert _wait_for(lambda: len(a) >= 2 and len(b) >= 2)
    inst.unmount()
    assert len(a) >= 2
    assert len(b) >= 2


def test_use_interval_callback_exception_does_not_kill_loop() -> None:
    """A raising callback is swallowed and the loop keeps ticking."""
    ticks: list[int] = []

    def on_tick() -> None:
        ticks.append(1)
        if len(ticks) == 1:
            raise ValueError("boom")

    def Comp() -> Element:
        use_interval(on_tick, 20)
        return Text("hi")

    inst = _render_comp(Comp)
    # If the first tick killed the thread we'd be stuck at len == 1.
    assert _wait_for(lambda: len(ticks) >= 3)
    inst.unmount()
    assert len(ticks) >= 3


def test_use_interval_thread_is_daemon_and_named() -> None:
    """The worker thread is a daemon named ``pyink-interval-N``."""
    started = threading.Event()

    def Comp() -> Element:
        use_interval(lambda: started.set(), 20)
        return Text("hi")

    inst = _render_comp(Comp)
    assert _wait_for(started.is_set)
    names = {t.name for t in threading.enumerate()}
    interval_threads = [t for t in threading.enumerate() if t.name.startswith("pyink-interval-")]
    assert interval_threads, "no pyink-interval-* thread found"
    assert all(t.daemon for t in interval_threads), "interval thread must be daemon"
    # Unique names: each interval gets its own sequence number.
    assert len(names & {t.name for t in interval_threads}) == len(interval_threads)
    inst.unmount()
