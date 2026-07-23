"""Instance — the live handle returned by :func:`ink.render.render` (PR5).

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

1. On mount we create an :class:`ink.core.signal.effect` whose body is
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
   That would be a deeper change to :mod:`ink.core.signal` and is out
   of scope for PR5.
"""

from __future__ import annotations

import atexit
import logging
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, TextIO, cast

from ink.core.element import Element
from ink.core.reconciler import Reconciler
from ink.core.signal import effect, signal
from ink.hooks._box_metrics_runtime import bump_layout_epoch
from ink.layout import clear_box_refs, layout, render_layout_to_string
from ink.render.diff import repaint_frame, write_diff
from ink.render.terminal import Terminal

if TYPE_CHECKING:
    from ink.core.component import HostInstance
    from ink.render.pipeline import RenderOptions

__all__ = ["Instance"]

logger = logging.getLogger(__name__)


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
        "static_lines",
        "_static_dirty",
        "_static_rows_approx",
        "_static_refs",
        "_unmounted",
        "_mount_complete",
        "_exit_event",
        "_atexit_registered",
        "_resize_dispose",
        "_sigint_dispose",
        "_ctrl_c_dispose",
        "_size_signal",
        "_force_repaint",
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
        # Static-output region (PR7): text written via :meth:`write_static`
        # is flushed above the current frame on the next paint. The buffer
        # holds not-yet-flushed chunks; ``_static_dirty`` flips True on a
        # new write and back to False once the paint loop has flushed.
        self.static_lines: list[str] = []
        self._static_dirty: bool = False
        # Approximate count of terminal rows consumed by ALL flushed
        # static content (cumulative, never reset). Used to compute the
        # frame's available row budget so :func:`write_diff` can cap
        # cursor-UP movements and avoid the viewport-clamp cursor
        # drift that wipes live content (input row, dividers) when the
        # frame + static overflows the viewport. The count is an
        # estimate — it tallies ``\\n`` characters in flushed static
        # text, which under-estimates when a static line wraps (eager
        # wrap) but that just means the cap is slightly generous; the
        # downward retreat in :func:`_repaint` stays correct because
        # it stops at ``cur_row`` regardless.
        #
        # Cumulative growth caveat: the count never decreases even
        # after lines scroll off the top of the viewport. Combined with
        # a non-trivial live frame, ``static_approx + frame_h`` can
        # exceed ``rows`` even when ``static_approx < rows``. Painting
        # that frame scrolls the terminal; afterwards the frame sits at
        # the bottom of the viewport and needs ``frame_h`` reachable
        # rows — not ``rows - static_approx``. See :meth:`_paint_now`
        # for the overflow formula (regression: Jarvis StatusBar stuck
        # on "Initializing..." after history load on medium-tall terminals).
        self._static_rows_approx: int = 0
        # Registered ``last_flushed`` refs from every mounted
        # :func:`ink.components.static.Static` instance. Each Static
        # pushes its closure-local ref here on mount via
        # :meth:`_register_static_ref` so :meth:`reset_static` can zero
        # them in one pass without reaching into the component closure.
        # Cleared on unmount (see :meth:`_do_unmount_tree`).
        self._static_refs: list = []
        self._unmounted: bool = False
        self._mount_complete: bool = False
        self._exit_event: threading.Event = threading.Event()
        self._atexit_registered: bool = False
        self._resize_dispose: Callable[[], None] | None = None
        self._sigint_dispose: Callable[[], None] | None = None
        self._ctrl_c_dispose: Callable[[], None] | None = None
        # Latest ``(columns, rows)`` pair, written at the end of every
        # ``_paint_now`` pass. ``use_window_size()`` returns a proxy that
        # reads this signal on each ``.columns`` / ``.rows`` access, so
        # closures captured at mount time stay reactive after a resize.
        # The signal is written under ``self._lock`` from ``_paint_now``,
        # which already serializes the layout+write path.
        self._size_signal = signal((0, 0))
        # Set by the resize subscription so the next ``_paint_now`` skips
        # the equality early-return and routes through ``repaint_frame``
        # to reset the terminal's passive-wrap state. Reading + clearing
        # happens inside ``_paint_now`` under ``self._lock``.
        self._force_repaint: bool = False
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
        # Atomicity: the entire mount → paint → effect-rebind must hold
        # ``self._lock`` so a concurrent ``unmount()`` cannot observe a
        # half-mounted tree (mounted_tree set but render_dispose not yet
        # re-bound, or vice versa). ``self._lock`` is an ``RLock`` so
        # ``_paint_now`` re-entering it from the same thread is safe —
        # and we deliberately do *not* release the lock between mount
        # and paint (the previous code did), which was the window where
        # state could be observed inconsistently.
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
            # ``rerender`` returns. Called inside the lock — see the
            # rationale above.
            self._paint_now()
            # Re-bind the render-loop effect against the new tree.
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
            was_alt = self.terminal.in_alternate_screen
            if was_alt:
                self.terminal.exit_alternate_screen()
            # Only clear the live frame when we were rendering inline
            # (i.e. on the primary screen). In alternate-screen mode the
            # whole buffer is discarded by ``exit_alternate_screen`` and
            # the prior frame lives in that disposable buffer — issuing a
            # clear-frame diff *after* restoring the primary buffer would
            # move the cursor / erase lines on the user's real scrollback,
            # which is the bug behind "scrollback disappeared after exit".
            if not was_alt:
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

    def write_static(self, text: str) -> None:
        """Append ``text`` to the permanent output region above the frame.

        Used by :func:`ink.components.static.Static`. The text is written
        above the live frame on the next paint — already-rendered lines are
        never re-painted by the frame diff, so they accumulate like ordinary
        stdout output (and scroll off the top of the viewport normally).

        Each call triggers a synchronous paint so the new lines appear
        immediately rather than waiting for the next signal-driven flush.

        ``text`` should be the final rendered string (ANSI styling allowed)
        and may span multiple lines. The caller is responsible for any
        trailing newline — passing ``"Item 0\\nItem 1\\n"`` places each
        item on its own row with the next frame painted immediately below.
        """
        if not text:
            return
        with self._lock:
            if self._unmounted:
                return
            self.static_lines.append(text)
            self._static_dirty = True
        # Synchronous paint so static text appears immediately.
        self._paint_now()

    def _register_static_ref(self, ref) -> None:
        """Register a Static component's ``last_flushed`` ref.

        Called by :func:`ink.components.static.Static` on mount so
        :meth:`reset_static` can zero every registered ref in one pass
        without reaching into the component closure. The ref is a
        :class:`ink.core.signal.ref` wrapping an ``int``; we keep it as
        ``list`` of opaque refs to avoid importing ``Ref`` into this
        module (would create a circular dependency — ``signal`` already
        imports nothing from render, and we want to keep it that way).

        Safe to call from inside ``StaticImpl`` (component mount path)
        — runs under ``self._lock`` so a concurrent ``reset_static``
        cannot observe a half-registered list.
        """
        with self._lock:
            self._static_refs.append(ref)

    def reset_static(self) -> None:
        """Reset scrollback + all Static flush state.

        User-initiated clear gesture (Jarvis ``/clear`` and ``/agent``
        switch). Emits scroll-clear + escape sequences in order:

        1. **Scroll-clear fallback**: write ``\\n`` * (rows + 2) to push
           the current viewport contents into the scrollback region of
           the terminal, then ``\\r`` to return cursor to column 1.
           This is the cross-terminal fallback — every terminal (legacy
           ``conhost`` included) understands raw ``\\n`` and scrolls
           accordingly, so even when the escapes below are no-ops the
           viewport will be visually cleared.
        2. ``\\x1b[3J`` — erase the scrollback buffer above the viewport
           (xterm / iTerm2 / Windows Terminal / modern tmux; silently
           no-op on legacy ``conhost`` — scroll-clear already handled
           it).
        3. ``\\x1b[2J`` — erase the visible viewport. This sequence is
           normally FORBIDDEN by the cursor contract
           (see ``jarvis/.trellis/spec/frontend/pyink-cursor-contract.md``)
           because in inline mode it pushes the viewport content into
           scrollback. Here we *want* it gone — the user explicitly
           asked to clear. Scroll-clear + ``\\x1b[3J`` already ran, so
           nothing is left to push into scrollback.
        4. ``\\x1b[H`` — cursor to home (row 1, column 1).

        ... and resets PyInk state so the next paint is a full repaint:

        * ``current_frame = ""`` — :meth:`_paint_now` will treat the
          next paint as a first paint (``write_diff(None, new_frame)``).
        * ``_static_rows_approx = 0`` — the cursor-up cap formula in
          :meth:`_paint_now` would otherwise under-count reachable rows
          and the next frame could paint in the wrong place.
        * ``static_lines`` cleared + ``_static_dirty = False`` —
          defensive; should be empty already after the previous paint.
        * Every registered Static ref's ``last_flushed`` zeroed — the
          next ``_flush`` effect run will write items from index 0.

        After ``reset_static`` returns, the caller typically swaps the
        Signal feeding ``<Static>`` (e.g. ``messages.value = [welcome]``)
        in the same atomic step. The signal write triggers the existing
        ``_flush`` effect, which sees ``last_flushed == 0`` and writes
        the new items via :meth:`write_static` → :meth:`_paint_now` →
        fresh initial paint anchored at cursor home.
        """
        logger.info(
            "[Instance.reset_static] CALLING id=%r unmounted=%r",
            id(self), self._unmounted,
        )
        with self._lock:
            if self._unmounted:
                logger.info(
                    "[Instance.reset_static] EARLY-RETURN id=%r: unmounted",
                    id(self),
                )
                return
            # Resolve terminal rows for the scroll-clear budget. Defaults
            # to 24 when unknown — over-estimate is harmless since each
            # newline just scrolls the terminal.
            rows = self._resolve_rows() or 24
            # Step 1 — scroll-clear fallback: push the entire viewport
            # into scrollback by emitting (rows + 2) newlines, then CR
            # back to column 1. Every terminal understands raw \n, so
            # this works whether or not VT processing / \x1b[3J is
            # honoured by the host terminal.
            self.stdout.write("\n" * (rows + 2))
            self.stdout.write("\r")
            # Step 2 — escape sequences — emitted in this specific order
            # so that the \x1b[2J (which can push viewport content into
            # scrollback on some terminals) doesn't leave orphaned
            # agent-A lines in scrollback. \x1b[3J first nukes existing
            # scrollback, then \x1b[2J does the visible clear, then
            # \x1b[H parks cursor. Scroll-clear has already emptied the
            # viewport, so these act as a hardening + cursor-home step
            # on Windows Terminal / xterm and as no-ops on legacy
            # terminals (where scroll-clear did the heavy lifting).
            self.stdout.write("\x1b[3J\x1b[2J\x1b[H")
            self.stdout.flush()
            # PyInk state reset.
            self.current_frame = ""
            self._static_rows_approx = 0
            self.static_lines.clear()
            self._static_dirty = False
            for static_ref in self._static_refs:
                try:
                    static_ref.value = 0
                except (AttributeError, TypeError):
                    # Defensive: a misbehaving ref shouldn't crash the
                    # reset. The Static closure owns the ref so a failed
                    # zero here just means that one component re-flushes
                    # from its old position (visual glitch, not a crash).
                    pass
        logger.info(
            "[Instance.reset_static] RETURNED id=%r rows=%r",
            id(self), rows,
        )

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
    # Internals — used by :func:`ink.render.render`
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
        # Phase 2 PR7 — bump the layout epoch so any ``use_box_metrics``
        # consumers downstream refresh their computed value. The Box ref
        # values were already back-filled inside :func:`layout`; bumping
        # here keeps the epoch in sync with what the subscriptions
        # observed.
        bump_layout_epoch()
        self.throttle.schedule(self._paint_now)

    def _paint_now(self) -> None:
        """Lay out + paint + diff-write one frame immediately.

        The entire body runs under ``self._lock`` so concurrent calls
        (e.g. the kernel thread's ``write_static`` racing the throttle
        thread's scheduled repaint) cannot interleave their stdout
        writes. Without this serialization two ``_paint_now`` invocations
        can each emit a ``write_diff`` based on a stale ``prev_frame``
        while the cursor is parked at a different row, corrupting the
        scrollback / static region — the root cause of the
        "history disappears after streaming ends" bug.
        """
        with self._lock:
            if self._unmounted:
                return
            mounted = self.mounted_tree
            prev_frame = self.current_frame
            cols = self._resolve_columns()
            rows = self._resolve_rows()
            static_dirty = self._static_dirty
            static_chunks = list(self.static_lines) if static_dirty else []
            # Capture + clear the force-repaint flag at the top so the
            # early-return decision and the write-path selection below
            # agree on whether this paint is a force repaint. The resize
            # subscription sets the flag; any subsequent paint — whether
            # the resize-scheduled one or an earlier paint from another
            # source — consumes it.
            force_repaint = self._force_repaint
            self._force_repaint = False
            if static_dirty and static_chunks:
                # Compute the new frame first so we can repaint in one pass.
                if mounted is None:
                    new_frame = ""
                else:
                    try:
                        layout_tree = layout(mounted, columns=cols, rows=rows)
                        new_frame = render_layout_to_string(layout_tree)
                    except Exception:  # pragma: no cover
                        return
                # Phase 2 PR7 — bump the layout epoch so ``use_box_metrics``
                # computeds refresh against the just-painted measurements.
                bump_layout_epoch()
                static_text = "".join(static_chunks)
                self._flush_static_and_frame(prev_frame, new_frame, static_text)
                if not self._unmounted:
                    self.static_lines.clear()
                    self._static_dirty = False
                    self.current_frame = new_frame
                    self.columns = cols
                    self.rows = rows if rows is not None else 0
                    self._size_signal.value = (cols, rows if rows is not None else 0)
                return
            if mounted is None:
                new_frame = ""
            else:
                try:
                    layout_tree = layout(mounted, columns=cols, rows=rows)
                    new_frame = render_layout_to_string(layout_tree)
                except Exception:  # pragma: no cover
                    # A broken layout must not blank the screen.
                    return
                # Phase 2 PR7 — bump after the layout pass so ``use_box_metrics``
                # computeds refresh against the just-painted measurements. We
                # bump here (inside the ``else`` branch) so a ``mounted is None``
                # frame doesn't perturb subscribers; the no-op frame still has
                # measurements from the previous successful layout.
                bump_layout_epoch()
            if prev_frame == new_frame and prev_frame and not force_repaint:
                return
            # Compute the frame's available row budget so ``write_diff``
            # can cap cursor-UP movements. Without this cap, a frame
            # taller than the viewport (after accounting for static
            # content above) causes cursor-up moves to clamp at the
            # viewport top, which makes the final cursor-down retreat
            # overshoot past the frame's last row and wipe live content
            # (input row, dividers). See ``_static_rows_approx``
            # docstring for details.
            #
            # ``available_rows`` = number of frame rows actually visible
            # on screen — i.e. how far the cursor may climb from the
            # frame's last row (where it is parked):
            #
            # * Everything fits (``static_approx + frame_h <= rows``):
            #   frame anchor sits at viewport row ``static_approx`` and
            #   ``available_rows = rows - static_approx >= frame_h``
            #   (PR2 formula) — every frame row is reachable.
            # * Content overflows (``static_approx + frame_h > rows`` —
            #   either static alone fills the viewport OR static+frame
            #   scrolls on paint): after scroll the frame sits at the
            #   bottom, so reachable rows equal the on-screen frame
            #   height ``min(rows, frame_h)``. Using the old
            #   ``rows - static_approx`` here under-counts when
            #   ``static_approx < rows`` but static+frame still scrolls
            #   (e.g. static=47, rows=50, frame=14 → spare=3). The
            #   StatusBar near the bottom of the frame then sits past
            #   the reachable floor; ``current_frame`` updates in memory
            #   while stdout never rewrites that row, and the next paint
            #   early-returns on ``prev_frame == new_frame`` — Jarvis's
            #   sticky "Initializing..." after history load.
            available_rows: int | None = None
            prev_line_count = len(prev_frame.split("\n")) if prev_frame else 0
            new_line_count = len(new_frame.split("\n")) if new_frame else 0
            if rows is not None and rows > 0:
                frame_h = max(prev_line_count, new_line_count, 1)
                if self._static_rows_approx + frame_h > rows:
                    available_rows = min(rows, frame_h)
                else:
                    available_rows = max(1, rows - self._static_rows_approx)
            if not prev_frame:
                write_diff(None, new_frame, self.stdout)
            else:
                height_delta = abs(new_line_count - prev_line_count)
                # Palette open/close and similar UI toggles change the live
                # frame height by several rows. Incremental diff cannot
                # reliably erase tail rows beyond ``available_rows`` — use a
                # full erase + repaint so the input row stays anchored.
                # Force-repaint (resize subscription) takes the same path
                # even when height is unchanged: width-independent widgets
                # produce byte-identical frames on resize, so the only way
                # to reset the terminal's passive-wrap state is a full
                # erase + repaint that walks the cursor back to frame
                # origin.
                if force_repaint or (height_delta >= 1 and available_rows):
                    repaint_frame(
                        prev_frame, new_frame, self.stdout, available_rows, cols,
                    )
                else:
                    write_diff(prev_frame, new_frame, self.stdout, available_rows)
            self.stdout.flush()
            if not self._unmounted:
                self.current_frame = new_frame
                self.columns = cols
                self.rows = rows if rows is not None else 0
                self._size_signal.value = (cols, rows if rows is not None else 0)

    def _flush_static_and_frame(
        self,
        prev_frame: str,
        new_frame: str,
        static_text: str,
    ) -> None:
        """Flush accumulated static text and repaint the live frame.

        Sequence:

        1. Erase the previous live frame. The cursor is parked on the
           frame's LAST row, so ``write_diff(prev_frame, "")`` walks
           UPWARD clearing each row and ends on the first row of the
           (now blank) frame region — exactly one line below the
           existing static output.
        2. Write ``static_text`` from that position. Each ``\\n`` inside
           it advances to the next row; when the cursor would walk past
           the bottom of the viewport the terminal scrolls, pushing
           older static lines up — which is precisely the desired
           behaviour for log-style output.
        3. Write the new live frame as a fresh initial paint; its cursor
           park lands on the new frame's LAST row (bottom-parked
           convention, 07-19-input-pyink-cursor-markdown).

        This routine deliberately avoids ``\\x1b[2J`` (full-screen clear)
        so PRD Decision 3 (inline mode never destroys scrollback) holds.
        """
        # Step 1 — clear the previous frame. ``write_diff`` with an empty
        # new frame erases each row (bottom-up) and parks the cursor at
        # row 0 of the frame region.
        if prev_frame:
            write_diff(prev_frame, "", self.stdout)
        # Step 2 — append the new static text. ``write_diff(None, new_frame)``
        # below treats whatever the cursor lands on as the new frame origin,
        # so we do not need to track absolute coordinates here.
        self.stdout.write(static_text)
        self.stdout.flush()
        # Track approximate static row count so subsequent ``_repaint``
        # calls can cap cursor-UP movements and avoid the viewport-clamp
        # cursor drift (see ``_static_rows_approx`` docstring).
        self._static_rows_approx += static_text.count("\n")
        # Step 3 — paint the new frame from the cursor's current position.
        if new_frame:
            write_diff(None, new_frame, self.stdout)
        else:
            # No live frame — emit CR so subsequent paints start at col 1.
            self.stdout.write("\r")
        self.stdout.flush()

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
        ctrl_c_dispose = self._ctrl_c_dispose
        self._ctrl_c_dispose = None
        if ctrl_c_dispose is not None:
            _safe_call(ctrl_c_dispose)
        # Stop the FPS throttle thread — it would otherwise keep running
        # for the lifetime of the process.
        self.throttle.stop()
        if self.mounted_tree is not None:
            # Phase 2 PR7 — clear every Box ``ref`` in the tree *before*
            # the reconciler tears the instances down so consumers calling
            # ``measure_element`` / ``use_box_metrics`` after unmount see
            # ``has_measured=False`` rather than a stale snapshot pointing
            # at a LayoutNode whose host instance is gone.
            clear_box_refs(self.mounted_tree)
            self.reconciler.unmount(self.mounted_tree)
            self.mounted_tree = None
        # Drop registered Static refs — their owning components just got
        # unmounted above, so the refs are unreachable from anywhere
        # except this list. ``rerender`` re-populates it as the new tree
        # mounts its own Static instances.
        self._static_refs.clear()

    def _clear_frame_for_exit(self) -> None:
        with self._lock:
            frame = self.current_frame
            self.current_frame = ""
        if frame:
            write_diff(frame, "", self.stdout)
        # Always emit an SGR reset on inline-mode exit. Components
        # (StreamingText cursor, Markdown inline styling, HighlightedCode
        # token colours) leave terminal SGR state set to whatever the
        # last painted row applied; without a reset the user's shell
        # inherits that styling (the "green cursor leaked into my shell"
        # bug). In alternate-screen mode the buffer swap discards the
        # state and we skip this to avoid perturbing the restored
        # primary buffer; this method is only called in inline mode
        # anyway (see :meth:`unmount`).
        self.stdout.write("\x1b[0m")
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

    Idle behaviour: when there is nothing to run the loop parks on
    ``_wakeup.wait()`` *without* a timeout. This is essential — earlier
    revisions computed a ``wait_for`` based on ``last = 0.0`` and the
    monotonic clock, which made ``wait_for`` negative whenever the loop
    had nothing to do, so the loop degenerated into a tight
    ``while True: continue`` that pinned a CPU core at 100% on static
    frames. The previous "fix" (the LRU cache in the markdown / code
    externals) reduced the per-tick cost but did not address the busy
    spin itself.
    """

    __slots__ = ("min_interval", "_stop", "_wakeup", "_thread", "_pending")

    def __init__(self, *, max_fps: int) -> None:
        self.min_interval: float = (1.0 / max_fps) if max_fps > 0 else 0.0
        self._stop = threading.Event()
        self._wakeup = threading.Event()
        self._pending: list[Callable[[], None]] = []
        self._thread = threading.Thread(
            target=self._loop,
            name="ink-fps-throttle",
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
            pending = self._take_pending()
            if not pending:
                # Park indefinitely until ``schedule`` wakes us. A short
                # timeout would re-introduce the busy spin; the wakeup
                # event is the only legitimate reason to leave this
                # branch.
                self._wakeup.wait()
                self._wakeup.clear()
                if self._stop.is_set():
                    return
                continue
            # We have work. Wait out the remaining interval since the last
            # execution so we honour the FPS cap, then run the latest
            # callback (which may be one that arrived during the wait).
            wait_for = self.min_interval - (time.monotonic() - last)
            if wait_for > 0:
                # ``schedule`` may queue more work while we wait; the
                # extra wakeup shortens the wait so the new callback is
                # observed promptly. ``last`` is the time of the *next*
                # execution, not the time we entered the wait.
                self._wakeup.wait(timeout=wait_for)
                self._wakeup.clear()
                if self._stop.is_set():
                    return
                late = self._take_pending()
                if late:
                    pending = late
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
