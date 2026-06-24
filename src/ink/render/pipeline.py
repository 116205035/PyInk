"""Render pipeline — entry point for live TUI rendering (PR5).

``render(tree, ...)`` mounts an Element tree, sets up the reactive
render loop, registers terminal-resize and Ctrl-C handlers, and returns
an :class:`ink.render.instance.Instance`.

The render loop is a regular :func:`ink.core.signal.effect`. Its body
runs a layout pass so any callable ``Text`` children that read
``signal.value`` do so *inside* the effect's tracking context — those
reads establish the subscriptions. The actual stdout write goes through
a :class:`ink.render.instance._FpsThrottle`, which coalesces a burst
of signal writes into at most one paint per ``1/max_fps`` seconds.

Inline mode (PRD Decision 3) is the default. Alternate screen is
opt-in via ``alternate_screen=True``.
"""

from __future__ import annotations

import atexit
import shutil
import signal
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import TextIO

from ink.core.element import Element
from ink.core.reconciler import Reconciler
from ink.hooks._runtime import (
    _reset_current_instance,
    _set_current_instance,
)
from ink.render.instance import Instance, _FpsThrottle
from ink.render.terminal import _HIDE_CURSOR, _SHOW_CURSOR, Terminal

__all__ = ["RenderOptions", "render"]


def _detect_terminal_size() -> tuple[int, int]:
    """Return ``(columns, rows)`` of the actual terminal.

    Wraps :func:`shutil.get_terminal_size`, which itself falls back to
    ``(80, 24)`` when stdout is not a real TTY (the common case in CI and
    in tests that drive PyInk through :class:`io.StringIO`). The helper
    exists as a single seam so :func:`render` and tests can mock the
    detection without reaching into :mod:`shutil`.
    """
    ts = shutil.get_terminal_size()
    return ts.columns, ts.lines


def _stdout_is_tty(stdout: TextIO) -> bool:
    """``stdout.isatty()`` that never raises on missing attribute."""
    try:
        return bool(stdout.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def _clamp_dimension(
    override: int | None,
    actual: int,
    *,
    is_tty: bool,
) -> int:
    """Resolve a viewport dimension against the real terminal size.

    * ``override is None`` → use the detected ``actual`` size.
    * ``is_tty and override > actual`` → clamp down to ``actual`` so the
      painted frame cannot overflow the screen (the root-cause fix for
      the inline cursor-up garbage bug).
    * otherwise → honour the caller-supplied ``override`` as-is. This
      covers both "caller explicitly wants a smaller viewport" and
      "stdout is not a TTY" (captured streams cannot scroll, so the
      oversized-frame bug cannot fire and the caller's explicit size
      is trustworthy).
    """
    if override is None:
        return actual
    if is_tty and override > actual:
        return actual
    return override


@dataclass(slots=True)
class RenderOptions:
    """Options bag for :func:`render`.

    Kept deliberately small — terminal-specific knobs (alternate screen,
    exit_on_ctrl_c, max_fps) are keyword-only on :func:`render` so the
    call-site is self-documenting.
    """

    columns: int | None = None
    rows: int | None = None


def render(
    tree: Element,
    *,
    stdout: TextIO | None = None,
    stdin: TextIO | None = None,
    columns: int | None = None,
    rows: int | None = None,
    alternate_screen: bool = False,
    exit_on_ctrl_c: bool = True,
    max_fps: int = 30,
) -> Instance:
    """Mount ``tree`` and start the reactive render loop.

    Parameters
    ----------
    tree:
        Root Element. Function-component bodies run exactly once during
        mount; subsequent state changes propagate through signals to the
        render loop.
    stdout:
        Where to write ANSI. Defaults to :data:`sys.stdout`.
    stdin:
        Where to read keyboard input from. Defaults to :data:`sys.stdin`.
        Useful for tests that drive synthetic input through the Terminal.
    columns / rows:
        Fixed viewport size. ``None`` auto-detects the real terminal
        size. Values that exceed the real terminal size are **clamped**
        to it — this is the root-cause fix for the "garbled borders on
        a short terminal" bug: when a caller hard-codes ``rows=30`` but
        the terminal only has 20 lines, the frame used to overflow the
        screen, the terminal scrolled, and the inline repaint's
        relative cursor-up math (which assumes every painted row is
        still on-screen) landed on the wrong rows — corrupting every
        subsequent frame. Values smaller than the real terminal are
        honoured as-is (the caller explicitly asked for a smaller
        viewport). Non-TTY envs fall back to the standard ``(80, 24)``
        default so tests driving PyInk through :class:`io.StringIO`
        keep working unchanged.
    alternate_screen:
        Enter the terminal's alternate screen buffer on mount and exit
        it on unmount. Default ``False`` (inline mode, PRD Decision 3).
        In both modes the system cursor is hidden on mount and restored
        on unmount — PyInk renders its own cursors, so the terminal's
        blinking cursor would be redundant.
    exit_on_ctrl_c:
        Treat ``SIGINT`` as a request to call :meth:`Instance.unmount`.
        Default ``True``. Disable to drive the lifecycle from Python
        directly.
    max_fps:
        Maximum frame rate the render loop will honour. Multiple signal
        writes inside one ``1/max_fps`` window collapse into a single
        paint.

    Returns
    -------
    Instance
        The live handle. Call :meth:`Instance.wait_until_exit` to block
        the calling thread, :meth:`Instance.unmount` to tear down.
    """
    out: TextIO = stdout if stdout is not None else sys.stdout
    terminal = Terminal(out, stdin=stdin)
    # Clamp the caller-supplied viewport to the real terminal size. The
    # frame diff engine assumes the painted frame stays on-screen; an
    # oversized frame overflows the viewport, the terminal scrolls, and
    # the next repaint's cursor-up math lands in the scrollback — the
    # root cause of the "inline cursor-up garbage on a short terminal"
    # bug.
    #
    # The clamp only fires when stdout is a real TTY: the bug is a
    # terminal-scrollback artefact, and a non-TTY stream (CI, tests
    # driving PyInk through :class:`io.StringIO`, output piped to a
    # file) cannot scroll, so the caller's explicit viewport is
    # trustworthy. When the caller leaves the viewport unset we still
    # auto-detect so behaviour matches the real terminal.
    actual_cols, actual_rows = _detect_terminal_size()
    is_tty = _stdout_is_tty(out)
    resolved_columns = _clamp_dimension(columns, actual_cols, is_tty=is_tty)
    resolved_rows = _clamp_dimension(rows, actual_rows, is_tty=is_tty)
    options = RenderOptions(columns=resolved_columns, rows=resolved_rows)
    reconciler = Reconciler()
    throttle = _FpsThrottle(max_fps=max_fps)
    inst = Instance(
        stdout=out,
        terminal=terminal,
        options=options,
        reconciler=reconciler,
        throttle=throttle,
    )
    inst.columns = resolved_columns
    inst.rows = resolved_rows

    # Mount the tree (component functions run here, exactly once). We
    # bind the Instance to a ContextVar for the duration of mount so
    # hooks (``use_input`` / ``use_app`` / ``use_window_size``) called
    # from inside component bodies can find it.
    token = _set_current_instance(inst)
    try:
        inst._mount_initial(tree)
    finally:
        _reset_current_instance(token)

    # Optional alternate screen.
    if alternate_screen:
        terminal.enter_alternate_screen()

    # Hide the system cursor on mount — PyInk renders its own cursors
    # (TextInput block cursor, Spinner animation, …) so the terminal's
    # blinking cursor is redundant and visually noisy.
    # ``enter_alternate_screen`` already hides the cursor as part of the
    # alt-screen swap and ``exit_alternate_screen`` restores it; inline
    # mode (the default) never did, which left users seeing two cursors
    # until exit. The inline-mode-only hide + restore here keeps alt
    # mode emitting exactly one of each sequence (preserves the count
    # contract asserted in ``test_alt_screen_emits_hide_cursor_only_once``).
    if not alternate_screen:
        terminal.write(_HIDE_CURSOR)
        terminal.flush()

        def _restore_cursor() -> None:
            terminal.write(_SHOW_CURSOR)
            terminal.flush()

        inst.on_exit(_restore_cursor)

    # Build + start the render loop (initial paint runs synchronously).
    inst._start_render_loop()

    # Resize -> force-flush a paint (bypasses the FPS throttle so a
    # window resize is reflected immediately).
    inst._resize_dispose = terminal.on_resize(lambda _c, _r: throttle.schedule(inst._paint_now))

    # atexit + SIGINT handlers.
    inst._atexit_registered = True
    atexit.register(inst.cleanup)
    if exit_on_ctrl_c:
        inst._sigint_dispose = _install_sigint(inst)

    # Mark mount complete before subscribing the raw-mode Ctrl+C
    # listener — that subscription starts the input-reader thread, and
    # we don't want a queued key to fire ``app.exit()`` → ``unmount``
    # before the rest of the setup is finished.
    inst._mount_complete = True

    if exit_on_ctrl_c:
        _install_raw_ctrl_c(terminal, inst)

    # Now that all disposes are wired up, start the input-reader thread
    # (if any subscribers exist). Order matters: subscribers register
    # during component mount, but the reader only starts here.
    terminal.enable_input()

    return inst


# ---------------------------------------------------------------------------
# SIGINT handler
# ---------------------------------------------------------------------------


#: Process-wide SIGINT registry. ``signal.signal`` may only be called from
#: the main thread; off-main-thread callers silently skip SIGINT handling
#: (the atexit hook still runs on interpreter exit).
_sigint_lock = threading.Lock()
_sigint_installed: bool = False
_sigint_prev_handler: object | None = None
_sigint_instances: list[Instance] = []


def _install_sigint(inst: Instance) -> Callable[[], None]:
    """Register ``inst`` for unmount-on-SIGINT. Returns a dispose callable."""
    global _sigint_installed, _sigint_prev_handler
    with _sigint_lock:
        _sigint_instances.append(inst)
        if not _sigint_installed:
            try:
                _sigint_prev_handler = signal.getsignal(signal.SIGINT)
                signal.signal(signal.SIGINT, _on_sigint)
                _sigint_installed = True
            except (ValueError, OSError):  # pragma: no cover
                # Off-main-thread callers fall back to atexit-only.
                _sigint_installed = False

    def dispose() -> None:
        with _sigint_lock, suppress(ValueError):
            _sigint_instances.remove(inst)

    return dispose


def _on_sigint(signum: int, frame: object) -> None:
    """SIGINT handler — unmount every active Instance, then chain.

    Uses a snapshot to survive a callback that removes itself mid-loop.
    """
    with _sigint_lock:
        snapshot = list(_sigint_instances)
    for inst in snapshot:
        with suppress(Exception):
            inst.unmount()
    prev = _sigint_prev_handler
    if callable(prev) and prev is not None:
        with suppress(Exception):
            prev(signum, frame)


def _install_raw_ctrl_c(terminal: Terminal, inst: Instance) -> None:
    """Subscribe a Ctrl+C listener for raw mode. No-op on non-TTY.

    Should only be called once ``inst._mount_complete`` is ``True`` —
    otherwise the reader thread may fire a handler that calls
    ``inst.unmount()`` before the rest of the mount has finished.
    """
    from ink.render.keys import Key

    def on_key(key: Key) -> None:
        # In raw mode Ctrl+C arrives as byte 0x03 → Key(input='c', ctrl=True).
        if key.ctrl and key.input == "c":
            with suppress(Exception):
                inst.unmount()

    inst._ctrl_c_dispose = terminal.on_key(on_key)
