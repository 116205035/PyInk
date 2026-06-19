"""Terminal abstraction — size detection, resize callbacks, alt screen (PR5).

The pipeline talks to the user's stdout through a :class:`Terminal` so the
cross-platform differences (Unix ``SIGWINCH`` vs Windows polling threads,
alternate-screen escape sequences) live in one place. ``use_input`` (PR6)
will eventually extend this with raw-mode entry/exit; for now we expose
only what the render pipeline needs.

Design constraints (PRD Decision 3):

* Inline mode is the default — alternate screen is opt-in via
  :meth:`enter_alternate_screen`.
* Never emit ``\\x1b[2J`` (full-screen clear) — it destroys scrollback.
  Inline repaints use cursor-move + line-clear sequences from
  :mod:`pyink.render.diff`.
"""

from __future__ import annotations

import os
import shutil
import signal as _signal
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from typing import TextIO

__all__ = ["Terminal"]


# CSI escape sequences.
_ENTER_ALT = "\x1b[?1049h"  # swap to alternate screen buffer
_EXIT_ALT = "\x1b[?1049l"  # restore main screen buffer
_HIDE_CURSOR = "\x1b[?25l"
_SHOW_CURSOR = "\x1b[?25h"


class Terminal:
    """Cross-platform terminal wrapper around a :class:`TextIO` stdout.

    Size detection falls back to a 80×24 default when ``stdout`` is not a
    real TTY (e.g. captured by tests). Resize callbacks fire on Unix via
    ``SIGWINCH``; on Windows we poll the size every 200 ms from a daemon
    thread because Windows has no per-process resize signal.
    """

    #: Polling interval (seconds) for the Windows resize watcher.
    POLL_INTERVAL: float = 0.2

    __slots__ = (
        "stdout",
        "_callbacks",
        "_poll_thread",
        "_poll_stop",
        "_sigwinch_installed",
        "_prev_sigwinch_handler",
        "_alt_active",
        "_lock",
    )

    def __init__(self, stdout: TextIO | None = None) -> None:
        self.stdout: TextIO = stdout if stdout is not None else sys.stdout
        self._callbacks: list[Callable[[int, int], None]] = []
        self._poll_thread: threading.Thread | None = None
        self._poll_stop: threading.Event | None = None
        self._sigwinch_installed: bool = False
        self._prev_sigwinch_handler: object | None = None
        self._alt_active: bool = False
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Size detection
    # ------------------------------------------------------------------

    def get_size(self) -> tuple[int, int]:
        """Return ``(columns, rows)``; ``(80, 24)`` fallback on failure."""
        try:
            ts = shutil.get_terminal_size()
            return max(1, ts.columns), max(1, ts.lines)
        except (OSError, ValueError):
            return 80, 24

    @property
    def columns(self) -> int:
        return self.get_size()[0]

    @property
    def rows(self) -> int:
        return self.get_size()[1]

    # ------------------------------------------------------------------
    # Resize callbacks
    # ------------------------------------------------------------------

    def on_resize(
        self,
        callback: Callable[[int, int], None],
    ) -> Callable[[], None]:
        """Register ``callback(columns, rows)`` for terminal-resize events.

        Returns an unsubscribe function. On Unix we install a single
        ``SIGWINCH`` handler the first time a callback is registered. On
        Windows (or any platform lacking ``SIGWINCH``) we spawn a daemon
        thread that polls the size every :attr:`POLL_INTERVAL` seconds.

        Calling this with a non-TTY ``stdout`` is safe — no callbacks fire
        until the size actually changes.
        """
        with self._lock:
            self._callbacks.append(callback)
            if not self._sigwinch_installed and not self._poll_thread:
                if hasattr(_signal, "SIGWINCH") and self._is_real_tty():
                    self._install_sigwinch()
                else:
                    self._start_poller()
        return lambda: self._unsubscribe(callback)

    def _unsubscribe(self, callback: Callable[[int, int], None]) -> None:
        with self._lock:
            with suppress(ValueError):
                self._callbacks.remove(callback)
            # Tear down the watcher when the last callback goes away. We
            # keep the SIGWINCH handler installed for the process lifetime
            # — replacing signals on the fly is risky on Windows threads
            # and not worth the bookkeeping.
            if not self._callbacks and self._poll_thread is not None:
                self._stop_poller()

    def _is_real_tty(self) -> bool:
        try:
            return bool(self.stdout.isatty())
        except (AttributeError, ValueError):
            return False

    def _install_sigwinch(self) -> None:
        # ``signal.signal`` only works from the main thread. Swallow the
        # ``ValueError`` so non-main-thread callers degrade gracefully to
        # polling. ``SIGWINCH`` is Unix-only; on Windows we fall back to
        # the polling thread.
        sigwinch = getattr(_signal, "SIGWINCH", None)
        if sigwinch is None:  # pragma: no cover
            self._sigwinch_installed = False
            return
        try:
            self._prev_sigwinch_handler = _signal.getsignal(sigwinch)
            _signal.signal(sigwinch, self._on_sigwinch)
            self._sigwinch_installed = True
        except (ValueError, AttributeError, OSError):  # pragma: no cover
            self._sigwinch_installed = False

    def _on_sigwinch(self, _signum: int, _frame: object) -> None:
        # Re-route to the registered callbacks. We snapshot the list so a
        # callback that unregisters itself mid-dispatch doesn't mutate the
        # iteration.
        with self._lock:
            callbacks = list(self._callbacks)
        cols, rows = self.get_size()
        for cb in callbacks:
            _safe_invoke(cb, cols, rows)
        # Chain to the previous handler if there was one.
        prev = self._prev_sigwinch_handler
        if callable(prev) and prev is not None:
            with suppress(Exception):
                prev(_signum, _frame)

    def _start_poller(self) -> None:
        if self._poll_thread is not None:
            return
        self._poll_stop = threading.Event()
        t = threading.Thread(
            target=self._poll_loop,
            args=(self._poll_stop,),
            name="pyink-resize-poll",
            daemon=True,
        )
        self._poll_thread = t
        t.start()

    def _stop_poller(self) -> None:
        if self._poll_stop is None or self._poll_thread is None:
            return
        self._poll_stop.set()
        # Join with a bounded timeout so a re-entrant ``on_resize`` after
        # teardown cannot race the old poller thread against a freshly
        # spawned one (both would otherwise invoke callbacks
        # concurrently). The thread is a daemon so a timeout-bounded
        # join that expires still lets the process exit.
        with suppress(RuntimeError):
            self._poll_thread.join(timeout=self.POLL_INTERVAL * 2)
        self._poll_thread = None
        self._poll_stop = None

    def _poll_loop(self, stop: threading.Event) -> None:
        last = self.get_size()
        while not stop.wait(self.POLL_INTERVAL):
            cur = self.get_size()
            if cur == last:
                continue
            last = cur
            with self._lock:
                callbacks = list(self._callbacks)
            for cb in callbacks:
                _safe_invoke(cb, *cur)

    # ------------------------------------------------------------------
    # Alternate screen
    # ------------------------------------------------------------------

    def enter_alternate_screen(self) -> None:
        """Switch to the alternate screen buffer and hide the cursor.

        Idempotent — calling twice is a no-op. The corresponding
        :meth:`exit_alternate_screen` restores the main buffer and cursor
        visibility. We deliberately avoid saving/restoring the cursor
        position with ``\\x1b 8`` / ``\\x1b 7`` — not every terminal
        supports it and ``\\x1b[?1049h`` already implies a save+restore
        on the platforms that matter.
        """
        if self._alt_active:
            return
        self.write(_ENTER_ALT + _HIDE_CURSOR)
        self.flush()
        self._alt_active = True

    def exit_alternate_screen(self) -> None:
        """Restore the main screen buffer and show the cursor."""
        if not self._alt_active:
            return
        self.write(_SHOW_CURSOR + _EXIT_ALT)
        self.flush()
        self._alt_active = False

    @property
    def in_alternate_screen(self) -> bool:
        return self._alt_active

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    def write(self, text: str) -> None:
        with suppress(ValueError, OSError):
            # stdout may be closed during interpreter shutdown.
            self.stdout.write(text)

    def flush(self) -> None:
        with suppress(ValueError, OSError, AttributeError):
            self.stdout.flush()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_invoke(callback: Callable[[int, int], None], cols: int, rows: int) -> None:
    with suppress(Exception):
        # A misbehaving subscriber must not crash the resize watcher.
        callback(cols, rows)


def isatty_safe(stream: TextIO) -> bool:
    """``stream.isatty()`` that never raises on missing attribute."""
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def environ_columns() -> int | None:
    """Read the ``COLUMNS`` env var, returning ``None`` if unset/invalid."""
    raw = os.environ.get("COLUMNS")
    if not raw:
        return None
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return None
