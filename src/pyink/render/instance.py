"""Instance — the live handle returned by :func:`pyink.render.render` (PR5).

A :class:`Instance` owns:

* The mounted root host instance (produced by the reconciler on mount).
* The render-loop effect that re-paints the screen whenever a signal the
  tree reads has changed.
* Bookkeeping for terminal state (alternate-screen toggle, atexit /
  SIGINT hooks).

PyInk is **not React** — function-component bodies run exactly once on
mount. State changes propagate via signals to subscribers in the
render-loop effect; the component tree itself never re-invokes. As a
consequence :meth:`Instance.rerender` is an unmount+mount, not a prop
diff.

Render-loop architecture:

1. On mount we create an :class:`pyink.core.signal.effect` whose body is
   :meth:`_effect_body`. The body schedules the actual paint
   through :class:`_FpsThrottle`; the throttle coalesces many writes
   into one paint per ``1/max_fps`` window.
2. *But* we still need the effect to subscribe to the signals the tree
   reads. The body therefore performs a layout pass too — the layout
   evaluates any callable ``Text`` children, which read ``signal.value``
   *inside* the effect's tracking context. Those reads establish the
   subscriptions; the resulting ``LayoutNode`` is thrown away (the
   throttle runs the layout again on the real paint). The cost is one
   extra layout per signal flush, which is negligible compared to a
   stdout write.

   Alternative considered (and rejected): exposing the signal module's
   tracking context so we could "subscribe without running the body".
   That would be a deeper change to :mod:`pyink.core.signal` and is out
   of scope for PR5.
"""

from __future__ import annotations

import atexit
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, TextIO, cast

from pyink.core.element import Element
from pyink.core.reconciler import Reconciler
from pyink.core.signal import effect
from pyink.layout import layout, render_layout_to_string
from pyink.render.diff import write_diff
from pyink.render.terminal import Terminal

if TYPE_CHECKING:
    from pyink.core.component import HostInstance
    from pyink.render.pipeline import RenderOptions

__all__ = ["Instance"]


class Instance:
    """Live render handle."""

    __slots__ = (
        "reconciler",
        "mounted_tree",
        "current_frame",
        "columns",
        "rows",
        "stdout",
        "terminal",
        "options",
        "throttle",
        "render_dispose",
        "exit_callbacks",
        "_unmounted",
        "_exit_event",
        "_atexit_registered",
        "_resize_dispose",
        "_sigint_dispose",
        "_lock",
    )

    def __init__(
        self,
        *,
        stdout: TextIO,
        terminal: Terminal,
        options: RenderOptions,
        reconciler: Reconciler,
        throttle: _FpsThrottle,
    ) -> None:
        self.reconciler: Reconciler = reconciler
        self.mounted_tree: HostInstance | None = None
        self.current_frame: str = ""
        self.columns: int = 0
        self.rows: int = 0
        self.stdout: TextIO = stdout
        self.terminal: Terminal = terminal
        self.options: RenderOptions = options
        self.throttle: _FpsThrottle = throttle
        self.render_dispose: Callable[[], None] | None = None
        self.exit_callbacks: list[Callable[[], None]] = []
        self._unmounted: bool = False
        self._exit_event: threading.Event = threading.Event()
        self._atexit_registered: bool = False
        self._resize_dispose: Callable[[], None] | None = None
        self._sigint_dispose: Callable[[], None] | None = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def rerender(self, tree: Element) -> None:
        """Replace the root element tree.

        Signals-model semantics: unmount the old tree (running its effect
        cleanups), mount the new tree, then trigger a fresh paint. We do
        not diff the old vs new Element tree — components only run once
        on mount regardless.

        ``current_frame`` is preserved so the diff against the new tree
        emits only the changed rows (rather than a full repaint).
        """
        with self._lock:
            if self._unmounted:
                raise RuntimeError("Cannot rerender an unmounted Instance")
            # Tear down the old tree's effect + tree, but keep
            # ``current_frame`` so the next paint diffs against it.
            self._dispose_render_loop_locked()
            if self.mounted_tree is not None:
                self.reconciler.unmount(self.mounted_tree)
                self.mounted_tree = None
            mounted = self.reconciler.mount(tree)
            self.mounted_tree = cast("HostInstance", mounted) if mounted is not None else None
        # Run a synchronous paint so callers see the new frame before
        # ``rerender`` returns.
        self._paint_now()
        # Re-bind the render-loop effect against the new tree.
        with self._lock:
            if not self._unmounted:
                self.render_dispose = effect(self._effect_body)

    def unmount(self) -> None:
        """Tear everything down. Idempotent."""
        with self._lock:
            if self._unmounted:
                return
            self._unmounted = True
        try:
            self._do_unmount_tree()
        finally:
            if self.terminal.in_alternate_screen:
                self.terminal.exit_alternate_screen()
            self._clear_frame_for_exit()
            for cb in list(self.exit_callbacks):
                _safe_call(cb)
            self._exit_event.set()

    def wait_until_exit(self) -> None:
        """Block the calling thread until :meth:`unmount` is invoked."""
        self._exit_event.wait()

    def clear(self) -> None:
        """Clear the current frame using cursor-move + line-clear."""
        with self._lock:
            frame = self.current_frame
            self.current_frame = ""
        if not frame:
            return
        write_diff(frame, "", self.stdout)
        self.stdout.flush()

    def cleanup(self) -> None:
        """unmount + remove from atexit registry. Safe to call multiple times."""
        # Drop our entry from atexit so a process-wide teardown doesn't
        # call back into an already-unmounted Instance (which would be a
        # harmless no-op, but adds noise and a final stdout write that
        # could clobber real output on shutdown).
        with self._lock:
            if self._atexit_registered:
                self._atexit_registered = False
                with suppress(Exception):
                    atexit.unregister(self.cleanup)
        self.unmount()

    # ------------------------------------------------------------------
    # on_exit hooks
    # ------------------------------------------------------------------

    def on_exit(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register ``callback`` to run when :meth:`unmount` is called."""
        self.exit_callbacks.append(callback)

        def dispose() -> None:
            with suppress(ValueError):
                self.exit_callbacks.remove(callback)

        return dispose

    # ------------------------------------------------------------------
    # Internals — used by :func:`pyink.render.render`
    # ------------------------------------------------------------------

    def _mount_initial(self, tree: Element) -> None:
        """Mount ``tree`` for the first time."""
        mounted = self.reconciler.mount(tree)
        if mounted is not None:
            self.mounted_tree = cast("HostInstance", mounted)

    def _start_render_loop(self) -> None:
        """Mount the render-loop effect and run the first paint synchronously."""
        # First paint lands before the effect is registered so any
        # exception during initial layout surfaces immediately.
        self._paint_now()
        # The effect body runs a "subscription layout" (cheap — it's the
        # same layout pass the paint later performs) so the signals
        # callable children read are tracked, then schedules a real
        # paint through the throttle.
        self.render_dispose = effect(self._effect_body)

    def _effect_body(self) -> None:
        # Run a layout so callable Text children's signal reads happen
        # *inside* the effect's tracking context — that's what
        # establishes the subscriptions. Throw away the result; the
        # throttled paint performs its own layout.
        mounted = self.mounted_tree
        if mounted is None:
            return
        cols = self._resolve_columns()
        rows = self._resolve_rows()
        try:
            layout(mounted, columns=cols, rows=rows)
        except Exception:  # pragma: no cover
            return
        self.throttle.schedule(self._paint_now)

    def _paint_now(self) -> None:
        """Lay out + paint + diff-write one frame immediately."""
        with self._lock:
            if self._unmounted:
                return
            mounted = self.mounted_tree
            prev_frame = self.current_frame
            cols = self._resolve_columns()
            rows = self._resolve_rows()
        if mounted is None:
            new_frame = ""
        else:
            try:
                layout_tree = layout(mounted, columns=cols, rows=rows)
                new_frame = render_layout_to_string(layout_tree)
            except Exception:  # pragma: no cover
                # A broken layout must not blank the screen.
                return
        if prev_frame == new_frame and prev_frame:
            return
        if not prev_frame:
            write_diff(None, new_frame, self.stdout)
        else:
            write_diff(prev_frame, new_frame, self.stdout)
        self.stdout.flush()
        with self._lock:
            if not self._unmounted:
                self.current_frame = new_frame
                self.columns = cols
                self.rows = rows if rows is not None else 0

    def _resolve_columns(self) -> int:
        override = getattr(self.options, "columns", None)
        if isinstance(override, int) and override > 0:
            return override
        return self.terminal.columns

    def _resolve_rows(self) -> int | None:
        override = getattr(self.options, "rows", None)
        if isinstance(override, int) and override > 0:
            return override
        return self.terminal.rows

    def _dispose_render_loop_locked(self) -> None:
        """Stop the render-loop effect without touching ``current_frame``.

        Caller must hold ``self._lock``.
        """
        dispose = self.render_dispose
        self.render_dispose = None
        if dispose is not None:
            _safe_call(dispose)

    def _do_unmount_tree(self) -> None:
        # Dispose the render-loop effect first so a late signal write
        # can't try to paint into a half-torn-down tree.
        self._dispose_render_loop_locked()
        resize_dispose = self._resize_dispose
        self._resize_dispose = None
        if resize_dispose is not None:
            _safe_call(resize_dispose)
        sigint_dispose = self._sigint_dispose
        self._sigint_dispose = None
        if sigint_dispose is not None:
            _safe_call(sigint_dispose)
        # Stop the FPS throttle thread — it would otherwise keep running
        # for the lifetime of the process.
        self.throttle.stop()
        if self.mounted_tree is not None:
            self.reconciler.unmount(self.mounted_tree)
            self.mounted_tree = None

    def _clear_frame_for_exit(self) -> None:
        with self._lock:
            frame = self.current_frame
            self.current_frame = ""
        if frame:
            write_diff(frame, "", self.stdout)
            self.stdout.flush()


def _safe_call(fn: Callable[[], None]) -> None:
    with suppress(Exception):
        # Cleanup must never cascade.
        fn()


# ---------------------------------------------------------------------------
# FPS throttle
# ---------------------------------------------------------------------------


class _FpsThrottle:
    """Coalesce a burst of ``schedule`` calls into one execution per interval.

    A daemon thread sleeps on an :class:`threading.Event`; ``schedule``
    sets the event and the thread runs the latest callback after waiting
    out the remaining interval. If multiple callbacks arrive within the
    same window only the *last* one runs (callers all paint the same
    tree, so this is correct).
    """

    __slots__ = ("min_interval", "_stop", "_wakeup", "_thread", "_pending")

    def __init__(self, *, max_fps: int) -> None:
        self.min_interval: float = (1.0 / max_fps) if max_fps > 0 else 0.0
        self._stop = threading.Event()
        self._wakeup = threading.Event()
        self._pending: list[Callable[[], None]] = []
        self._thread = threading.Thread(
            target=self._loop,
            name="pyink-fps-throttle",
            daemon=True,
        )
        self._thread.start()

    def schedule(self, callback: Callable[[], None]) -> None:
        self._pending.append(callback)
        self._wakeup.set()

    def stop(self) -> None:
        self._stop.set()
        self._wakeup.set()
        # Join with a bounded timeout so the daemon thread doesn't keep
        # running after the Instance is torn down. ``_loop`` returns
        # promptly once ``_stop`` is observed (the wait uses the same
        # Event we just set), so the join rarely blocks in practice.
        with suppress(RuntimeError):
            self._thread.join(timeout=1.0)

    def _loop(self) -> None:
        last = 0.0
        while not self._stop.is_set():
            wait_for = self.min_interval - (time.monotonic() - last)
            if wait_for > 0:
                self._wakeup.wait(timeout=wait_for)
                self._wakeup.clear()
            pending = self._take_pending()
            if not pending:
                continue
            # Only the most recent callback survives — they all paint the
            # same tree, so older ones are obsolete by the time we run.
            cb = pending[-1]
            with suppress(Exception):
                cb()
            last = time.monotonic()
        # Final drain on shutdown.
        for cb in self._take_pending():
            with suppress(Exception):
                cb()

    def _take_pending(self) -> list[Callable[[], None]]:
        pending = self._pending
        self._pending = []
        return pending
