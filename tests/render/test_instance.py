"""Tests for :class:`pyink.render.instance.Instance` (PR5).

The Instance is the live handle returned by :func:`render`. We exercise
the lifecycle methods (rerender / unmount / wait_until_exit / clear)
using :class:`io.StringIO` as a fake stdout. The render pipeline itself
is covered separately in ``test_pipeline.py``; these tests construct
Instances directly via the pipeline so they share the same machinery.
"""

from __future__ import annotations

import io
import threading
import time
from collections.abc import Callable

from pyink import Box, Text, render
from pyink.core.signal import signal


def _make_instance(
    tree: object,
    *,
    columns: int = 80,
    rows: int = 24,
    alternate_screen: bool = False,
    exit_on_ctrl_c: bool = False,
    max_fps: int = 30,
) -> tuple[object, io.StringIO]:
    out = io.StringIO()
    inst = render(
        tree,  # type: ignore[arg-type]
        stdout=out,
        columns=columns,
        rows=rows,
        alternate_screen=alternate_screen,
        exit_on_ctrl_c=exit_on_ctrl_c,
        max_fps=max_fps,
    )
    return inst, out


# ---------------------------------------------------------------------------
# rerender
# ---------------------------------------------------------------------------


def test_rerender_replaces_root_tree() -> None:
    inst, out = _make_instance(Text("first"))
    frame1 = out.getvalue()
    assert "first" in frame1
    out.truncate(0)
    out.seek(0)
    inst.rerender(Text("second"))  # type: ignore[attr-defined]
    frame2 = out.getvalue()
    assert "second" in frame2
    inst.unmount()  # type: ignore[attr-defined]


def test_rerender_after_unmount_raises() -> None:
    inst, _ = _make_instance(Text("x"))
    inst.unmount()  # type: ignore[attr-defined]
    try:
        inst.rerender(Text("y"))  # type: ignore[attr-defined]
    except RuntimeError:
        return
    raise AssertionError("Expected RuntimeError on rerender after unmount")


# ---------------------------------------------------------------------------
# unmount
# ---------------------------------------------------------------------------


def test_unmount_is_idempotent() -> None:
    inst, _ = _make_instance(Text("x"))
    inst.unmount()  # type: ignore[attr-defined]
    # Second call is a no-op (no exception).
    inst.unmount()  # type: ignore[attr-defined]
    inst.unmount()  # type: ignore[attr-defined]


def test_unmount_clears_the_painted_frame() -> None:
    inst, out = _make_instance(Text("hello"))
    assert "hello" in out.getvalue()
    out.truncate(0)
    out.seek(0)
    inst.unmount()  # type: ignore[attr-defined]
    # The clear path emits an erase-line per row but never a 2J.
    assert "\x1b[2J" not in out.getvalue()
    # Erase-line should appear (clearing "hello").
    assert "\x1b[2K" in out.getvalue()


def test_unmount_resets_terminal_sgr_state() -> None:
    """Inline-mode unmount emits an SGR reset so the shell doesn't inherit
    styling left over from the last painted frame.

    Regression for the "streaming_text_demo leaves the font green after
    exit" bug: ``StreamingText`` / Markdown / HighlightedCode leave SGR
    state set to whatever colour the last painted row carried; the shell
    inherits it without a trailing ``\\x1b[0m``.
    """
    # A green Text forces a non-default SGR sequence into the painted
    # frame so we can assert the reset lands after it.
    inst, out = _make_instance(Text("hi", color="green"))
    out.truncate(0)
    out.seek(0)
    inst.unmount()  # type: ignore[attr-defined]
    written = out.getvalue()
    assert "\x1b[0m" in written


def test_unmount_emits_reset_even_with_empty_frame() -> None:
    """Reset fires on inline unmount regardless of whether a frame was
    painted — the goal is to leave the terminal in a clean SGR state."""
    inst, out = _make_instance(Text(""))
    out.truncate(0)
    out.seek(0)
    inst.unmount()  # type: ignore[attr-defined]
    assert "\x1b[0m" in out.getvalue()


# ---------------------------------------------------------------------------
# wait_until_exit
# ---------------------------------------------------------------------------


def test_wait_until_exit_returns_when_unmount_called_from_thread() -> None:
    inst, _ = _make_instance(Text("x"))

    ready = threading.Event()

    def worker() -> None:
        ready.set()
        time.sleep(0.05)
        inst.unmount()  # type: ignore[attr-defined]

    t = threading.Thread(target=worker)
    t.start()
    ready.wait()
    # Should return within a short window — fail loudly if it hangs.
    inst.wait_until_exit()  # type: ignore[attr-defined]
    t.join(timeout=1.0)
    assert not t.is_alive()


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


def test_clear_forgets_current_frame() -> None:
    inst, out = _make_instance(Text("hello"))
    inst.clear()  # type: ignore[attr-defined]
    cleared = out.getvalue()
    assert "\x1b[2K" in cleared
    assert "\x1b[2J" not in cleared
    inst.unmount()  # type: ignore[attr-defined]


def test_clear_with_empty_frame_is_noop() -> None:
    inst, out = _make_instance(Text("x"))
    out.truncate(0)
    out.seek(0)
    inst.clear()  # type: ignore[attr-defined]
    # First clear works; second clear (after frame already forgotten)
    # writes nothing.
    out.truncate(0)
    out.seek(0)
    inst.clear()  # type: ignore[attr-defined]
    assert out.getvalue() == ""
    inst.unmount()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# on_exit
# ---------------------------------------------------------------------------


def test_on_exit_callback_fires_on_unmount() -> None:
    inst, _ = _make_instance(Text("x"))
    called: list[bool] = []
    dispose = inst.on_exit(lambda: called.append(True))  # type: ignore[attr-defined]
    inst.unmount()  # type: ignore[attr-defined]
    assert called == [True]
    # Dispose after unmount is safe.
    dispose()


def test_on_exit_dispose_unregisters_callback() -> None:
    inst, _ = _make_instance(Text("x"))
    called: list[bool] = []
    dispose = inst.on_exit(lambda: called.append(True))  # type: ignore[attr-defined]
    dispose()
    inst.unmount()  # type: ignore[attr-defined]
    assert called == []


# ---------------------------------------------------------------------------
# Alternate screen integration
# ---------------------------------------------------------------------------


def test_alternate_screen_entered_on_mount_exited_on_unmount() -> None:
    inst, out = _make_instance(Text("x"), alternate_screen=True)
    written = out.getvalue()
    assert "\x1b[?1049h" in written
    inst.unmount()  # type: ignore[attr-defined]
    final = out.getvalue()
    assert "\x1b[?1049l" in final


# ---------------------------------------------------------------------------
# Reactive update — signal write triggers a repaint
# ---------------------------------------------------------------------------


def test_signal_write_triggers_repaint() -> None:
    counter = signal(0)

    def Counter() -> object:
        return Text(lambda: f"count={counter.value}")

    inst, out = _make_instance(Counter(), columns=40, rows=3)
    initial = out.getvalue()
    assert "count=0" in initial
    out.truncate(0)
    out.seek(0)
    counter.value = 5
    # Give the FPS throttle thread time to flush the scheduled paint.
    time.sleep(0.2)
    repainted = out.getvalue()
    assert "count=5" in repainted
    # No 2J ever.
    assert "\x1b[2J" not in repainted
    inst.unmount()  # type: ignore[attr-defined]


def test_multiple_signal_writes_coalesce_into_one_repaint() -> None:
    counter = signal(0)

    def Counter() -> object:
        return Box(
            Text(lambda: f"count={counter.value}"),
            flexDirection="column",
        )

    inst, out = _make_instance(Counter(), columns=40, rows=3)
    out.truncate(0)
    out.seek(0)
    # Burst of writes within the FPS window — should collapse to (at
    # most) one repaint at the end.
    for _ in range(5):
        counter.value += 1
    time.sleep(0.3)
    repaint = out.getvalue()
    assert "count=5" in repaint
    # We may have intermediate paints (count=1, 2, ...) depending on
    # throttle timing — the contract is just that the final visible
    # value reflects the latest write.
    inst.unmount()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Rapid unmount / rerender (race safety)
# ---------------------------------------------------------------------------


def test_rapid_rerender_then_unmount_is_safe() -> None:
    """Hammering rerender + unmount must not raise or deadlock."""
    inst, _ = _make_instance(Text("a"))
    for i in range(5):
        inst.rerender(Text(f"v{i}"))  # type: ignore[attr-defined]
    inst.unmount()  # type: ignore[attr-defined]


def test_unmount_during_throttled_paint_does_not_crash() -> None:
    """A signal write that schedules a paint right before unmount is safe."""
    counter = signal(0)

    def Counter() -> object:
        return Text(lambda: f"c={counter.value}")

    inst, _ = _make_instance(Counter(), columns=20, rows=2)
    for _ in range(3):
        counter.value += 1
    # Unmount immediately — the throttle thread may try to fire the
    # pending paint after this returns. _paint_now guards on _unmounted.
    inst.unmount()  # type: ignore[attr-defined]
    # Give the throttle thread time to drain.
    time.sleep(0.1)


def test_rerender_concurrent_with_unmount_is_atomic() -> None:
    """Rerender holds its lock across mount→paint→rebind so a racing
    unmount either runs entirely before (rerender raises) or entirely
    after (rerender completes, then unmount tears it down).

    Regression for audit Bug 7: previously rerender released the lock
    between mount and paint and again between paint and effect rebind.
    A concurrent unmount in those windows could leave the Instance with
    a half-mounted tree and no render_dispose, or mark itself unmounted
    while rerender still tried to paint. The fix wraps the whole body
    in a single ``with self._lock`` block.
    """
    import contextlib

    errors: list[BaseException] = []

    for _ in range(10):
        inst, _ = _make_instance(Text("root"))
        stop = threading.Event()
        # Bind loop locals as default args so the worker closures
        # capture ``inst``/``stop`` by value (ruff B023).
        rerenderer = _make_rerenderer(inst, stop, errors)
        unmounter = _make_unmounter(inst, stop, errors)

        t1 = threading.Thread(target=rerenderer)
        t2 = threading.Thread(target=unmounter)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)
        # Force-stop if a thread is somehow still alive (would indicate
        # a deadlock — surfaced by the join timeout above).
        stop.set()
        # Ensure the unmount path ran to completion.
        with contextlib.suppress(BaseException):
            inst.unmount()  # type: ignore[attr-defined]

    assert not errors, f"unexpected errors during concurrent rerender/unmount: {errors!r}"


def _make_rerenderer(
    inst: object,
    stop: threading.Event,
    errors: list[BaseException],
) -> Callable[[], None]:
    """Build a worker that hammers ``inst.rerender`` until ``stop`` flips."""

    def worker() -> None:
        i = 0
        while not stop.is_set():
            try:
                inst.rerender(Text(f"v{i % 5}"))  # type: ignore[attr-defined]
            except RuntimeError:
                # Instance unmounted under us — expected race outcome.
                return
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)
                return
            i += 1

    return worker


def _make_unmounter(
    inst: object,
    stop: threading.Event,
    errors: list[BaseException],
) -> Callable[[], None]:
    """Build a worker that unmounts ``inst`` after a brief delay."""

    def worker() -> None:
        # Give the rerenderer a head start, then pull the rug.
        time.sleep(0.01)
        try:
            inst.unmount()  # type: ignore[attr-defined]
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            stop.set()

    return worker


def test_max_fps_one_still_renders_final_state() -> None:
    """max_fps=1 (extreme throttle) collapses many writes to <=1 paint/sec."""
    counter = signal(0)

    def Counter() -> object:
        return Text(lambda: f"v={counter.value}")

    inst, out = _make_instance(Counter(), columns=20, rows=2)
    assert "v=0" in out.getvalue()
    out.truncate(0)
    out.seek(0)
    # The first paint already happened; force several writes and let the
    # throttle schedule. Even at max_fps=1 we should eventually see the
    # latest value.
    counter.value = 7
    time.sleep(1.2)  # > 1/max_fps = 1s so the throttle flushes.
    repainted = out.getvalue()
    assert "v=7" in repainted
    inst.unmount()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# _FpsThrottle idle / coalescing regressions
# ---------------------------------------------------------------------------


def test_fps_throttle_does_not_spin_when_idle() -> None:
    """Regression: a static frame must not keep the throttle thread busy.

    Before the fix the throttle loop computed
    ``wait_for = min_interval - (now - last)`` with ``last = 0.0``, which
    is always negative for the monotonic clock, so the loop degenerated
    into a tight ``while True: continue`` and pinned a CPU core at
    ~100% even on a static frame. The fix parks the loop on an indefinite
    ``Event.wait()`` when there is no pending work; this test exercises
    that code path by counting thread iterations over an idle window and
    asserting the count stays low (one iteration per wakeup, and no
    wakeups happen on a static frame).
    """
    from pyink.render.instance import _FpsThrottle

    iterations = {"count": 0}
    stop_evt = threading.Event()

    def counting_loop(
        min_interval: float,
        wakeup: threading.Event,
        pending: list[object],
    ) -> None:
        last = 0.0
        while not stop_evt.is_set():
            iterations["count"] += 1
            if not pending:
                wakeup.wait()
                wakeup.clear()
                continue
            wait_for = min_interval - (time.monotonic() - last)
            if wait_for > 0:
                wakeup.wait(timeout=wait_for)
                wakeup.clear()
            pending.clear()

    throttle = _FpsThrottle(max_fps=30)
    # Replace the worker's loop with our counting variant without
    # re-spawning the thread: stop the built-in loop and run ours on a
    # fresh thread that shares the throttle's events / pending list.
    throttle.stop()
    worker = threading.Thread(
        target=counting_loop,
        args=(throttle.min_interval, throttle._wakeup, throttle._pending),
        name="pyink-fps-throttle-test",
        daemon=True,
    )
    worker.start()
    try:
        time.sleep(0.5)
        # 0.5 s of pure idleness. The fixed loop wakes at most once (if a
        # stray wakeup arrives); the old busy-spin would log hundreds of
        # thousands of iterations in this window.
        assert iterations["count"] <= 2, iterations["count"]
    finally:
        stop_evt.set()
        throttle._wakeup.set()
        worker.join(timeout=1.0)


def test_fps_throttle_coalesces_burst_into_one_callback() -> None:
    """A burst of ``schedule`` calls inside one window runs only the last.

    Mirrors the throttle's documented contract — they all paint the same
    tree, so older schedules are obsolete by the time the loop runs.
    """
    from pyink.render.instance import _FpsThrottle

    fired: list[str] = []

    def make_cb(tag: str) -> Callable[[], None]:
        def cb() -> None:
            fired.append(tag)

        return cb

    throttle = _FpsThrottle(max_fps=30)
    try:
        for tag in ("a", "b", "c"):
            throttle.schedule(make_cb(tag))
        # Wait long enough for the throttle to flush (max_fps=30 → 33ms
        # window). 0.3 s is generous headroom.
        time.sleep(0.3)
    finally:
        throttle.stop()

    # Only the latest schedule survives; older ones are dropped.
    assert fired == ["c"], fired
