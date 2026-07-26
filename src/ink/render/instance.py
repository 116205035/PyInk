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
from ink.layout.measure import string_width
from ink.render.diff import _visual_height, repaint_frame, write_diff
from ink.render.terminal import Terminal

if TYPE_CHECKING:
    from ink.core.component import HostInstance
    from ink.render.pipeline import RenderOptions

__all__ = ["Instance"]

# Root P: how many logical lines of flushed static text are retained for
# the force_repaint static-tail redraw. Must comfortably exceed the
# largest realistic viewport height (rows) so the redraw can always
# refill the region above the frame.
_STATIC_TAIL_MAX_LINES = 500

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
        "_static_tail_text",
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
        # Root M (DSR Cursor Query) — see ``_query_cursor`` / ``_handle_cpr``.
        # ``_cpr_event`` is signalled by the input thread when a CPR response
        # arrives; ``_cpr_row`` holds the row reported by the terminal;
        # ``_cpr_generation`` tags each DSR so stale responses (from a prior
        # resize, arrived after another DSR was issued) can be discarded;
        # ``_cpr_pending`` is the generation currently awaited (``None`` when
        # no DSR is in flight); ``_cpr_lock`` guards all of those fields so
        # ``_handle_cpr`` (running on the input thread) can acquire it
        # without deadlocking against the painter thread's ``self._lock``
        # (which is held across ``_paint_now`` → ``_query_cursor`` →
        # ``_cpr_event.wait``).
        "_cpr_event",
        "_cpr_row",
        "_cpr_generation",
        "_cpr_pending",
        "_cpr_lock",
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
        # Root P: verbatim copy of the most recent flushed static text
        # (ANSI included), trimmed to the last ``_STATIC_TAIL_MAX_LINES``
        # logical lines. ``_flush_static_and_frame`` is the only writer
        # (every static byte to the terminal passes through it), and the
        # ``force_repaint`` branch re-emits a height-budgeted suffix of
        # it on resize so erasing the viewport is no longer destructive
        # to static content. Cleared by ``reset_static`` (the user asked
        # for a clear — a later resize must not resurrect the text).
        self._static_tail_text: str = ""
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
        # Root M (DSR Cursor Query) state. ``_cpr_event`` bridges the input
        # thread (which sees the CPR response) and whatever thread is running
        # ``_paint_now`` (typically the resize-scheduled throttle thread).
        # ``_cpr_row`` is written by ``_handle_cpr`` under ``self._cpr_lock``
        # (a SEPARATE lock from ``self._lock`` — see its docstring for why);
        # ``_cpr_event.wait()`` in ``_query_cursor`` wakes up and reads it.
        # ``_cpr_generation`` is bumped on every DSR; ``_cpr_pending`` holds
        # the generation the instance is currently waiting on (``None`` when
        # not awaiting). See ``_query_cursor`` / ``_handle_cpr`` for the
        # protocol details.
        self._cpr_event: threading.Event = threading.Event()
        self._cpr_row: int | None = None
        self._cpr_generation: int = 0
        self._cpr_pending: int | None = None
        # Dedicated, non-reentrant lock for CPR coordination fields above.
        # MUST be separate from ``self._lock``: ``_paint_now`` holds
        # ``self._lock`` across the entire paint, including the call to
        # ``_query_cursor``'s ``_cpr_event.wait(timeout=0.05)``. The
        # input thread's ``_handle_cpr`` would deadlock trying to acquire
        # ``self._lock`` if CPR state shared it. Using a dedicated lock
        # means the painter's outer ``self._lock`` is irrelevant to the
        # CPR protocol — no nested locking, no release/reacquire gymnastics.
        self._cpr_lock = threading.Lock()
        self._lock = threading.RLock()
        # Register the CPR handler on the terminal so the input loop can
        # route CSI CPR responses here. The terminal's dispatcher checks
        # ``_cpr_pending`` (via the registered handler's return value) to
        # decide whether to consume the sequence as CPR or emit it as a
        # normal key. See ``Terminal.set_cpr_callback``.
        self.terminal.set_cpr_callback(self._handle_cpr)

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
            self._static_tail_text = ""
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
                if force_repaint:
                    # Root P (static tail redraw): erase the whole
                    # viewport, redraw the retained static tail, then
                    # paint the new frame bottom-anchored.
                    #
                    # Supersedes Root O (DSR-anchored erase). Root O
                    # derived ``erase_top`` from the post-rewrap cursor
                    # row, but cpr_debug.log showed Windows Terminal's
                    # horizontal reflow drifts the cursor wildly
                    # (cursor_row 40-48 with rows=48 constant, earlier
                    # 7-47). Every erase that overshot the old frame's
                    # top permanently blanked static rows — PyInk never
                    # repaints static — so the visible gap above the
                    # frame grew monotonically (union of all historical
                    # erase overshoots; the +2 margin alone ate 2 static
                    # rows per resize even with a perfect cursor_row).
                    #
                    # Root P makes the erase reversible: whatever static
                    # we blank, we immediately redraw from
                    # ``_static_tail_text``. The cursor row is no longer
                    # an input to any computation, so WT's reflow quirks
                    # (pending-wrap resolution, blank-line reclamation)
                    # can't corrupt the layout. DSR is not sent.
                    visual_h_new = _visual_height(new_frame, cols or 80)
                    new_frame_top = max(1, (rows or 1) - visual_h_new + 1)
                    # Rows above the frame available for static. One row
                    # of safety margin: ``_visual_height`` may
                    # under-estimate (emoji/CJK width drift, Root J/K/L
                    # lesson), and an over-tall static redraw would end
                    # up hidden behind the frame — bounded blank rows
                    # are the safe failure direction.
                    budget = new_frame_top - 1
                    static_lines, static_h = self._select_static_tail(
                        max(0, budget - 1), cols or 80
                    )
                    self.stdout.write("\x1b[1;1H\x1b[0J")
                    if static_lines:
                        # Bottom-anchor the suffix directly above the
                        # frame so the newest static line stays adjacent
                        # to it; under-estimates surface as blank rows
                        # at the viewport top, never as lost messages.
                        start_row = max(1, budget - static_h + 1)
                        # Per-line absolute CUP — NEVER a free-flowing
                        # "\n" stream. The retained lines were rendered
                        # at some older width, so after a shrink most of
                        # them re-wrap and ``_visual_height``'s
                        # cumulative error over a full-viewport suffix
                        # is unbounded: a free-flowing emission that
                        # overruns the viewport bottom SCROLLS, dumping
                        # the redrawn (duplicate) content into the
                        # scrollback — the "table fragments multiplying
                        # at several widths in mid-scrollback" bug.
                        # Absolute positioning makes overrun impossible:
                        # a mis-estimated line lands on a slightly wrong
                        # row inside the viewport (self-heals on the
                        # next repaint) but the cursor can never run off
                        # the bottom edge, so scrollback stays pristine.
                        parts: list[str] = []
                        row = start_row
                        for line in static_lines:
                            parts.append(f"\x1b[{row};1H")
                            parts.append(line)
                            w = string_width(line)
                            if w and w % (cols or 80) == 0:
                                # The line fills its last visual row
                                # exactly, leaving the cursor in
                                # pending-wrap. Left unresolved (the next
                                # op is a CUP, not text), Windows
                                # Terminal keeps the row's soft-wrap flag
                                # set and its reflow will JOIN the next
                                # row into this one on grow / re-split on
                                # shrink — the glued mega-line fragments
                                # seen in scrollback after resize storms
                                # (long table rows of width 87/90/94 hit
                                # exact multiples at common widths).
                                # ``\r\n`` resolves the wrap as a HARD
                                # line end, mirroring ``_paint_initial``.
                                # Safe: the estimate is exact for these
                                # lines, so the cursor sits at
                                # ``row + h - 1 <= budget < rows`` —
                                # never the bottom row, never a scroll.
                                parts.append("\r\n")
                            row += max(1, (w + (cols or 80) - 1) // (cols or 80))
                        # The suffix may cut a logical line that opened
                        # an SGR style whose reset lies outside the
                        # retained window — don't let it bleed into the
                        # frame paint.
                        parts.append("\x1b[0m")
                        self.stdout.write("".join(parts))
                    if new_frame:
                        # Per-line CUP for the frame too — the WHOLE
                        # force_repaint path must be free of
                        # free-flowing output. ``_paint_initial`` walks
                        # down with newlines, so a frame whose actual
                        # height exceeds ``visual_h_new`` (emoji
                        # statusbar wrap miscount — cpr_debug.log shows
                        # visual_h flapping 4↔6 for the same content)
                        # overruns the viewport bottom and SCROLLS,
                        # dumping the just-redrawn static top rows into
                        # the scrollback (observed: history incl. the
                        # welcome banner duplicated after resize
                        # storms). Absolute CUP caps every line at its
                        # computed row; a wrapping line's overflow is
                        # overwritten by the next line's CUP and the
                        # last line's wrap stays pending at the
                        # bottom-right corner — control sequences don't
                        # resolve pending-wrap, so nothing scrolls.
                        frame_parts: list[str] = []
                        frame_row = new_frame_top
                        frame_lines = new_frame.split("\n")
                        for i, line in enumerate(frame_lines):
                            frame_parts.append(f"\x1b[{frame_row};1H\x1b[2K")
                            frame_parts.append(line)
                            w = string_width(line)
                            if (
                                i < len(frame_lines) - 1
                                and w
                                and w % (cols or 80) == 0
                            ):
                                # Same pending-wrap hard-break as the
                                # static loop above — but never on the
                                # LAST frame line: that one can end on
                                # the viewport's bottom row, where a
                                # newline would scroll. It keeps the
                                # bare ``\r`` park below.
                                frame_parts.append("\r\n")
                            frame_row += max(1, (w + (cols or 80) - 1) // (cols or 80))
                        # Park at column 1 of the last line's row
                        # (bottom-parked contract for later diff paints).
                        frame_parts.append("\r")
                        self.stdout.write("".join(frame_parts))
                    else:
                        self.stdout.write("\r")
                    # The frame now occupies [new_frame_top, rows];
                    # everything above counts as static for the
                    # cursor-up cap on later diff paints.
                    self._static_rows_approx = budget
                    frame_top = new_frame_top  # for diagnostic log
                    visual_h = visual_h_new  # for diagnostic log
                    branch = "root_p"
                    self._cpr_debug_log(
                        "force_repaint",
                        cols=cols,
                        rows=rows,
                        visual_h=visual_h,
                        frame_top=frame_top,
                        branch=branch,
                        static_h=static_h,
                        new_frame_repr=(new_frame[:80].replace("\n", "\\n") if new_frame else ""),
                    )
                elif height_delta >= 1 and available_rows:
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
        # Root P: retain the raw text for the force_repaint static-tail
        # redraw. Appending to one string (rather than a line list)
        # handles chunks that don't end on a line boundary — a trailing
        # partial line is continued by the next chunk automatically.
        self._static_tail_text += static_text
        tail_lines = self._static_tail_text.split("\n")
        if len(tail_lines) > _STATIC_TAIL_MAX_LINES:
            self._static_tail_text = "\n".join(tail_lines[-_STATIC_TAIL_MAX_LINES:])
        # Step 3 — paint the new frame from the cursor's current position.
        if new_frame:
            write_diff(None, new_frame, self.stdout)
        else:
            # No live frame — emit CR so subsequent paints start at col 1.
            self.stdout.write("\r")

    def _select_static_tail(self, budget: int, cols: int) -> tuple[list[str], int]:
        """Pick a suffix of the retained static text that fits ``budget``.

        Returns ``(lines, visual_height_estimate)``. Walks the retained
        logical lines from newest to oldest, accumulating per-line wrap
        height, and stops before the first line that would exceed the
        budget. Under-selection is deliberate: the failure mode of an
        over-estimated line is a few blank rows at the viewport top,
        while an over-tall selection would slide behind the live frame
        and hide the newest messages.

        A single trailing newline is stripped before splitting so the
        newest real content line (not a phantom empty line) anchors the
        bottom of the redraw.
        """
        if budget <= 0 or not self._static_tail_text:
            return [], 0
        text = self._static_tail_text
        if text.endswith("\n"):
            text = text[:-1]
        lines = text.split("\n")
        chosen: list[str] = []
        height = 0
        for line in reversed(lines):
            line_h = max(1, (string_width(line) + cols - 1) // cols)
            if height + line_h > budget:
                break
            chosen.append(line)
            height += line_h
        chosen.reverse()
        return chosen, height
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

    # ------------------------------------------------------------------
    # Root M (DSR Cursor Query) — CPR protocol
    # ------------------------------------------------------------------

    def _query_cursor(self) -> int | None:
        """Send DSR (``\\x1b[6n``) and wait for the terminal's CPR reply.

        Returns the viewport-relative row (1-indexed) the terminal reports
        as the cursor's current position, or ``None`` on timeout (50 ms).

        Protocol:

        1. Bump ``_cpr_generation`` and mark it pending. The input loop
           will only honour a CPR response whose implicit generation
           matches ``_cpr_pending`` — protecting against stale replies
           from a previous DSR arriving after a newer one was issued
           (the ConPTY reorder race; see ``research/cpr-validation.md``
           §5 race-condition warning).
        2. Clear ``_cpr_event`` and ``_cpr_row`` so a stale signal from
           a previous query doesn't bleed in.
        3. Write ``\\x1b[6n`` and flush. The terminal's reply
           (``\\x1b[<row>;<col>R``) arrives on stdin; the input thread
           parses it and calls :meth:`_handle_cpr`.
        4. ``_cpr_event.wait(timeout=0.05)``. Windows Terminal's
           end-to-end latency is typically 1–5 ms; 50 ms is generous.
           On timeout we clear ``_cpr_pending`` (so a late CPR can't
           poison a future query) and return ``None``.

        The bottom-parked cursor invariant (every paint leaves the
        cursor at the frame's last visual row) means the reported row
        IS the new frame's bottom — callers derive the new frame's top
        as ``max(0, cpr_row - visual_h_new)``.

        Locking: this method may be called with ``self._lock`` held
        (it usually is — ``_paint_now`` acquires it for the whole paint
        and calls us from inside the ``force_repaint`` branch). The
        wait therefore runs while ``self._lock`` is held; this is safe
        ONLY because :meth:`_handle_cpr` does NOT acquire ``self._lock``
        — it uses the dedicated ``self._cpr_lock`` instead. Sharing
        ``self._lock`` here would deadlock the input thread (a separate
        thread from the painter) for the duration of the 50 ms wait,
        guaranteeing that every real-terminal force_repaint times out.
        See ``_cpr_lock``'s docstring for the full rationale.
        """
        with self._cpr_lock:
            self._cpr_event.clear()
            self._cpr_row = None
            self._cpr_generation += 1
            self._cpr_pending = self._cpr_generation
        try:
            self.stdout.write("\x1b[6n")
            self.stdout.flush()
        except (ValueError, OSError):
            # stdout closed during shutdown — treat as timeout.
            with self._cpr_lock:
                self._cpr_pending = None
            return None
        if self._cpr_event.wait(timeout=0.05):
            return self._cpr_row
        # Timeout. Clear pending so a late CPR (arriving after the
        # timeout window) is discarded by ``_handle_cpr``'s generation
        # check rather than corrupting a future query.
        with self._cpr_lock:
            self._cpr_pending = None
        return None

    def _handle_cpr(self, row: int, col: int) -> bool:
        """Input-thread callback for a CSI CPR response.

        Returns ``True`` when the response was consumed (i.e. the
        instance was awaiting a CPR for the matching generation),
        ``False`` when it should be treated as a regular keystroke
        (no DSR in flight, or a stale generation arrived).

        Thread coordination:

        * Acquire ``self._cpr_lock`` (a dedicated lock, NOT the
          painter's ``self._lock``) so reads of ``_cpr_pending`` and
          writes of ``_cpr_row`` are atomic relative to ``_query_cursor``
          on the painter thread. Using the painter's ``self._lock``
          here would deadlock: ``_paint_now`` holds it across the
          call to ``_query_cursor``'s ``_cpr_event.wait``. The input
          thread (which runs this handler) is a different thread from
          the painter, so it cannot re-enter the RLock — it would
          block until the 50 ms timeout fires, guaranteeing every
          real-terminal force_repaint hits the timeout fallback.
        * If ``_cpr_pending is None`` (no DSR awaited) → return ``False``
          so the terminal's dispatcher emits the sequence as a Key.
          This preserves the safety default: a stray CPR from a
          misbehaving terminal or a literal ``R`` keystroke is never
          silently swallowed.
        * Otherwise write ``_cpr_row``, clear ``_cpr_pending`` and
          signal ``_cpr_event`` — the painter wakes up and reads
          ``_cpr_row``.

        Note: ``col`` is currently unused (Root M only needs the row)
        but is accepted so the terminal's dispatcher signature stays
        symmetric with the CPR wire format.
        """
        with self._cpr_lock:
            if self._cpr_pending is None:
                return False
            self._cpr_row = row
            self._cpr_pending = None
        self._cpr_event.set()
        return True

    # TEMP Root M diagnostic — remove after cpr_debug.log analysis.
    # Captures per-event data so we can correlate ``cpr_row`` vs
    # ``new_rows`` and confirm (or rule out) the bottom-parked cursor
    # invariant violation on Windows Terminal resize.
    def _cpr_debug_log(self, event: str, **fields) -> None:
        """TEMP diagnostic for Root M — remove after debug cycle completes."""
        import datetime

        path = (
            r"D:/Projects/Jarvis/.trellis/tasks/"
            r"07-24-resize-anchor-cursor-bottom/cpr_debug.log"
        )
        ts = datetime.datetime.now().isoformat(timespec="milliseconds")
        parts = [f"event={event}", f"ts={ts}"]
        for k, v in fields.items():
            parts.append(f"{k}={v!r}")
        line = " ".join(parts) + "\n"
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            # Don't let logging break rendering (e.g. missing parent dir
            # in CI / non-Jarvis consumers of PyInk).
            pass

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
