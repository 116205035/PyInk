"""Terminal abstraction — size detection, resize callbacks, alt screen,
raw mode, and keyboard input (PR5 + PR6).

The pipeline talks to the user's stdout through a :class:`Terminal` so the
cross-platform differences (Unix ``SIGWINCH`` vs Windows polling threads,
alternate-screen escape sequences, ``termios`` vs ``msvcrt`` raw mode)
live in one place.

Design constraints (PRD Decision 3):

* Inline mode is the default — alternate screen is opt-in via
  :meth:`enter_alternate_screen`.
* Never emit ``\\x1b[2J`` (full-screen clear) — it destroys scrollback.
  Inline repaints use cursor-move + line-clear sequences from
  :mod:`pyink.render.diff`.

Raw mode (PR6):

* :meth:`enter_raw_mode` flips stdin into a no-echo / no-line-buffer
  configuration so keystrokes are delivered immediately. Unix uses
  ``termios``; Windows uses ``SetConsoleMode``.
* :meth:`on_key` subscribes a callback to parsed :class:`Key` events. A
  daemon thread reads stdin continuously, feeds bytes through a
  :class:`pyink.render.key_parser.KeyParser`, and dispatches each parsed
  key to every subscriber. The thread stops when the last subscriber
  unsubscribes.
* In raw mode Ctrl+C arrives as the byte ``\\x03`` instead of raising
  ``SIGINT`` — callers that want ``Ctrl+C`` to exit must check for it in
  their key handler (the default :func:`use_input` wiring does this).

UTF-8 input (Bug 9):

* Multi-byte UTF-8 sequences (CJK characters, emoji) can be split across
  two ``os.read`` / ``msvcrt.getch`` chunks — the kernel doesn't promise
  atomic delivery of a whole codepoint. The Terminal wraps every raw
  chunk in an :func:`codecs.getincrementaldecoder` so a partial UTF-8
  lead byte is buffered internally and only emitted to the key parser
  once the trailing continuation bytes arrive. Without this layer a
  single ``你`` (``\\xe4\\xbd\\xa0``) keystroke surfaced as three
  separate ``Key`` events whose ``input`` was one junk byte each, which
  TextInput rendered as mojibake. The decoder is per-Terminal and lives
  for the lifetime of raw mode; it is reset whenever raw mode re-arms.
"""

from __future__ import annotations

import codecs
import os
import select
import shutil
import signal as _signal
import sys
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from typing import TextIO

from pyink.render.key_parser import KeyParser, parse_key
from pyink.render.keys import Key

__all__ = ["Terminal"]


# CSI escape sequences.
#
# ``\x1b 7`` (DECSC) / ``\x1b 8`` (DECRC) bracket the alternate-screen
# swap so the cursor position is explicitly saved before the buffer
# switch and restored after we return. Private mode 1049 already
# implies a save+restore on conformant terminals (xterm, modern
# gnome-terminal, Windows Terminal, iTerm2), but some holdouts —
# older ``cmd.exe`` / conhost builds and a few embedded terminals —
# honour ``1049`` only as a buffer switch and forget to save the
# cursor, which left users seeing the cursor jump to the top of the
# screen after exit. Emitting DECSC/DECRC ourselves covers those
# terminals without harming the conformant ones (the redundant
# restore lands on the same cell).
_DEC_SAVE_CURSOR = "\x1b7"  # DECSC — save cursor position + attrs
_DEC_RESTORE_CURSOR = "\x1b8"  # DECRC — restore cursor position + attrs
_ENTER_ALT = "\x1b[?1049h"  # swap to alternate screen buffer
_EXIT_ALT = "\x1b[?1049l"  # restore main screen buffer
_HIDE_CURSOR = "\x1b[?25l"
_SHOW_CURSOR = "\x1b[?25h"
#: DECSET 2004 — enable bracketed paste mode so the terminal wraps paste
#: payloads in ``\\x1b[200~ ... \\x1b[201~``. The Terminal raw-mode entry
#: emits this once per session and the matching DECRST on exit. Terminals
#: that do not implement the mode simply ignore the escape, so the
#: fallback (single-char events) keeps working.
_ENTER_BRACKETED_PASTE = "\x1b[?2004h"
_EXIT_BRACKETED_PASTE = "\x1b[?2004l"


class Terminal:
    """Cross-platform terminal wrapper around a :class:`TextIO` stdout.

    Size detection falls back to a 80×24 default when ``stdout`` is not a
    real TTY (e.g. captured by tests). Resize callbacks fire on Unix via
    ``SIGWINCH``; on Windows we poll the size every 200 ms from a daemon
    thread because Windows has no per-process resize signal.
    """

    #: Polling interval (seconds) for the Windows resize watcher.
    POLL_INTERVAL: float = 0.2

    #: How long :meth:`read_key` waits for a follow-up byte after seeing
    #: an ``ESC`` before deciding it was a lone Escape press.
    ESC_TIMEOUT: float = 0.05

    #: Maximum bytes to read from stdin in one ``os.read`` call.
    READ_CHUNK: int = 64

    __slots__ = (
        "stdout",
        "stdin",
        "_callbacks",
        "_poll_thread",
        "_poll_stop",
        "_sigwinch_installed",
        "_prev_sigwinch_handler",
        "_alt_active",
        "_raw_active",
        "_bracketed_paste_active",
        "_prev_termios",
        "_prev_console_mode",
        "_key_callbacks",
        "_key_thread",
        "_key_stop",
        "_key_parser",
        "_paste_buffer",
        "_utf8_decoder",
        "_raw_lock",
        "_lock",
    )

    def __init__(
        self,
        stdout: TextIO | None = None,
        *,
        stdin: TextIO | None = None,
    ) -> None:
        self.stdout: TextIO = stdout if stdout is not None else sys.stdout
        # stdin is read via low-level fd (``os.read``) so we don't fight
        # Python's line-buffered ``sys.stdin``. Fall back to sys.stdin
        # when no explicit stream is supplied.
        self.stdin: TextIO = stdin if stdin is not None else sys.stdin
        self._callbacks: list[Callable[[int, int], None]] = []
        self._poll_thread: threading.Thread | None = None
        self._poll_stop: threading.Event | None = None
        self._sigwinch_installed: bool = False
        self._prev_sigwinch_handler: object | None = None
        self._alt_active: bool = False
        # Raw-mode state.
        self._raw_active: bool = False
        # Bracketed-paste mode is only armed while raw mode is on; this
        # flag lets us emit the matching DECRST once on exit.
        self._bracketed_paste_active: bool = False
        self._prev_termios: object | None = None
        self._prev_console_mode: int | None = None
        # Key-event subscription state.
        self._key_callbacks: list[Callable[[Key], None]] = []
        self._key_thread: threading.Thread | None = None
        self._key_stop: threading.Event | None = None
        self._key_parser: KeyParser = KeyParser()
        # Buffer for the current bracketed-paste payload (``None`` when
        # not inside a paste). Built up by the key loop as in-paste
        # single-char events arrive, then flushed as one ``Key(paste=…)``
        # when the closing marker is seen.
        self._paste_buffer: str | None = None
        # Incremental UTF-8 decoder so multi-byte sequences split across
        # two raw reads are reassembled before reaching the key parser.
        # ``errors="replace"`` keeps a malformed lead byte from crashing
        # the reader — it surfaces as U+FFFD instead, matching what a
        # naive ``bytes.decode("utf-8", "replace")`` would have done.
        self._utf8_decoder = codecs.getincrementaldecoder("utf-8")(
            errors="replace"
        )
        self._raw_lock = threading.RLock()
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
            should_stop = not self._callbacks and self._poll_thread is not None
        # Run the join outside the lock — the poller thread takes this
        # lock on each iteration when dispatching callbacks.
        if should_stop:
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
        thread = self._poll_thread
        self._poll_stop.set()
        if thread is threading.current_thread():
            # Called from inside the poll loop — self-join is forbidden;
            # the loop will observe the stop flag and exit on its own.
            self._poll_thread = None
            self._poll_stop = None
            return
        # Join with a bounded timeout so a re-entrant ``on_resize`` after
        # teardown cannot race the old poller thread against a freshly
        # spawned one (both would otherwise invoke callbacks
        # concurrently). The thread is a daemon so a timeout-bounded
        # join that expires still lets the process exit.
        with suppress(RuntimeError):
            thread.join(timeout=self.POLL_INTERVAL * 4)
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
        visibility. We emit ``\\x1b 7`` (DECSC) before the buffer swap
        and :meth:`exit_alternate_screen` emits ``\\x1b 8`` (DECRC)
        afterwards so the cursor position is saved and restored even
        on terminals whose ``1049`` implementation forgets that step.
        """
        if self._alt_active:
            return
        self.write(_DEC_SAVE_CURSOR + _ENTER_ALT + _HIDE_CURSOR)
        self.flush()
        self._alt_active = True

    def exit_alternate_screen(self) -> None:
        """Restore the main screen buffer and show the cursor.

        Emits ``\\x1b 8`` (DECRC) after the buffer swap returns so the
        cursor lands back where :meth:`enter_alternate_screen` saved
        it. See that method's docstring for why the explicit save /
        restore is needed on top of private mode ``1049``.
        """
        if not self._alt_active:
            return
        self.write(_SHOW_CURSOR + _EXIT_ALT + _DEC_RESTORE_CURSOR)
        self.flush()
        self._alt_active = False

    @property
    def in_alternate_screen(self) -> bool:
        return self._alt_active

    # ------------------------------------------------------------------
    # Raw mode
    # ------------------------------------------------------------------

    @property
    def is_raw_mode_supported(self) -> bool:
        """Whether stdin is a real TTY we can put into raw mode.

        Returns ``False`` in CI / when stdin is piped, so callers can
        degrade gracefully without raising.
        """
        try:
            return bool(self.stdin.isatty())
        except (AttributeError, ValueError, OSError):
            return False

    @property
    def in_raw_mode(self) -> bool:
        return self._raw_active

    def enter_raw_mode(self) -> None:
        """Put stdin into raw mode (no echo, no line buffering).

        Idempotent. On Unix saves the original ``termios`` state; on
        Windows saves the original console mode. :meth:`exit_raw_mode`
        restores it. No-op when raw mode is not supported (non-TTY).

        As a side effect this also asks the terminal to enter *bracketed
        paste mode* (DECSET 2004) so subsequent paste events arrive
        wrapped in ``\\x1b[200~ ... \\x1b[201~`` markers — the key loop
        then delivers the payload as a single :class:`Key` with
        ``paste`` populated. Terminals that don't implement the mode
        silently ignore the escape, so single-char events keep working
        as the fallback.
        """
        with self._raw_lock:
            if self._raw_active:
                return
            if not self.is_raw_mode_supported:
                return
            if _PLATFORM == "unix":
                self._enter_raw_mode_unix()
            else:
                self._enter_raw_mode_windows()
            self._raw_active = True
            # Reset the incremental decoder so any partial UTF-8 bytes
            # left over from a previous raw-mode session don't bleed into
            # the new one.
            self._utf8_decoder = codecs.getincrementaldecoder("utf-8")(
                errors="replace"
            )
            # Enable bracketed paste only when writing to a real TTY —
            # otherwise we'd spray escapes into captured stdout during
            # tests. The guard matches the one used for alternate-screen.
            if self._is_real_tty():
                self.write(_ENTER_BRACKETED_PASTE)
                self.flush()
                self._bracketed_paste_active = True

    def exit_raw_mode(self) -> None:
        """Restore the original stdin configuration. Idempotent."""
        with self._raw_lock:
            if not self._raw_active:
                return
            if self._bracketed_paste_active:
                self.write(_EXIT_BRACKETED_PASTE)
                self.flush()
                self._bracketed_paste_active = False
            if _PLATFORM == "unix":
                self._exit_raw_mode_unix()
            else:
                self._exit_raw_mode_windows()
            self._raw_active = False

    def _enter_raw_mode_unix(self) -> None:
        # Local import — termios is Unix-only and would crash the import
        # of this module on Windows if unconditionally imported. Typed as
        # ``Any`` because mypy's stubs don't surface ``termios.error`` /
        # ``VMIN`` / ``VTIME`` as attributes on all platforms.
        import termios
        from typing import Any

        termios_any: Any = termios

        try:
            fd = self.stdin.fileno()
        except (AttributeError, OSError, ValueError):  # pragma: no cover
            return
        try:
            attrs = termios_any.tcgetattr(fd)
        except (termios_any.error, OSError):  # pragma: no cover
            return
        self._prev_termios = attrs
        # cflag / lflag indices in the termios list. See ``termios`` docs.
        IFLAG = 0
        CFLAG = 2
        LFLAG = 3
        CC = 6
        new = list(attrs)  # shallow copy
        lflag = new[LFLAG]
        # Disable echo, canonical mode, signals (SIGINT/SIGQUIT), and
        # any extended processing.
        for flag in ("ECHO", "ECHONL", "ICANON", "ISIG", "IEXTEN"):
            value = getattr(termios_any, flag, None)
            if value is not None:
                lflag &= ~value
        new[LFLAG] = lflag
        iflag = new[IFLAG]
        for flag in ("IXON", "ICRNL", "BRKINT", "INPCK", "ISTRIP"):
            value = getattr(termios_any, flag, None)
            if value is not None:
                iflag &= ~value
        new[IFLAG] = iflag
        # Force 8-bit clean.
        cs8 = getattr(termios_any, "CS8", None)
        if cs8 is not None:
            new[CFLAG] |= cs8
        # VMIN = 1, VTIME = 0 — block until at least one byte is ready.
        cc = list(new[CC])
        cc[termios_any.VMIN] = 1
        cc[termios_any.VTIME] = 0
        new[CC] = cc
        try:
            termios_any.tcsetattr(fd, termios_any.TCSANOW, new)
        except (termios_any.error, OSError):  # pragma: no cover
            self._prev_termios = None

    def _exit_raw_mode_unix(self) -> None:
        import termios
        from typing import Any

        termios_any: Any = termios
        prev = self._prev_termios
        self._prev_termios = None
        if prev is None:
            return
        try:
            fd = self.stdin.fileno()
        except (AttributeError, OSError, ValueError):  # pragma: no cover
            return
        with suppress(termios_any.error, OSError):
            termios_any.tcsetattr(fd, termios_any.TCSANOW, prev)

    def _enter_raw_mode_windows(self) -> None:
        """Flip stdin into raw mode on Windows.

        Two things must happen for keystrokes to arrive byte-by-byte:

        1. Disable ``ENABLE_ECHO_INPUT`` / ``ENABLE_LINE_INPUT`` /
           ``ENABLE_PROCESSED_INPUT`` so input is unbuffered, not echoed,
           and Ctrl+C is delivered as a byte (``\\x03``) instead of raising
           ``SIGINT``.
        2. Enable ``ENABLE_VIRTUAL_TERMINAL_INPUT`` so the console
           translates special keys (arrows, Tab, F1-F12, Home/End, …) into
           ANSI escape sequences (``\\x1b[A``, ``\\x1b[15~``, …) before
           handing them to the reader. Without this flag the console
           emits raw ``INPUT_RECORD`` structs that ``os.read`` cannot
           decode into VT sequences — arrow keys / Tab simply vanish.

        ``ENABLE_VIRTUAL_TERMINAL_INPUT`` is mutually exclusive with the
        legacy echo/line flags, so we set it on top of the cleared bits.
        """
        try:
            import ctypes
            from ctypes import wintypes
        except ImportError:  # pragma: no cover
            return
        try:
            handle = ctypes.windll.kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        except OSError:  # pragma: no cover
            return
        kernel32 = ctypes.windll.kernel32
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return
        self._prev_console_mode = mode.value
        ENABLE_ECHO_INPUT = 0x0004
        ENABLE_LINE_INPUT = 0x0002
        ENABLE_PROCESSED_INPUT = 0x0001
        ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
        new_mode = mode.value & ~(
            ENABLE_ECHO_INPUT | ENABLE_LINE_INPUT | ENABLE_PROCESSED_INPUT
        )
        new_mode |= ENABLE_VIRTUAL_TERMINAL_INPUT
        kernel32.SetConsoleMode(handle, new_mode)

    def _exit_raw_mode_windows(self) -> None:
        """Restore the original Windows console mode (idempotent)."""
        try:
            import ctypes
        except ImportError:  # pragma: no cover
            return
        prev = self._prev_console_mode
        self._prev_console_mode = None
        if prev is None:
            return
        try:
            handle = ctypes.windll.kernel32.GetStdHandle(-10)
        except OSError:  # pragma: no cover
            return
        ctypes.windll.kernel32.SetConsoleMode(handle, prev)

    # ------------------------------------------------------------------
    # Keyboard input
    # ------------------------------------------------------------------

    def read_key(self, timeout: float | None = None) -> Key | None:
        """Block until a key is pressed, returning the parsed :class:`Key`.

        * ``timeout=None`` — block forever.
        * ``timeout=0`` — return immediately (``None`` if no key ready).
        * ``timeout>0`` — wait at most ``timeout`` seconds.

        Must be called *after* :meth:`enter_raw_mode` on Unix; on a
        non-TTY stdin (e.g. CI) it always returns ``None``.
        """
        if not self.is_raw_mode_supported:
            return None
        try:
            fd = self.stdin.fileno()
        except (AttributeError, OSError, ValueError):
            return None
        # Read one chunk; feed it through the parser; if we got at least
        # one sequence return the first, otherwise wait for more.
        deadline = None if timeout is None else _monotonic() + max(0.0, timeout)
        while True:
            wait = self._compute_wait(deadline)
            if wait is not None and wait <= 0:
                return None
            if not _wait_for_input(fd, wait):
                # Timed out.
                if deadline is not None and _monotonic() >= deadline:
                    # Flush any pending ESC as a lone Escape press.
                    seq = self._key_parser.flush_pending_escape()
                    if seq is not None:
                        return parse_key(seq)
                    return None
                continue
            try:
                data = _read_stdin_chunk(fd, self.READ_CHUNK)
            except (BlockingIOError, OSError):
                return None
            if not data:
                return None
            text = self._decode_utf8(data)
            if not text:
                # Incomplete multi-byte UTF-8 sequence — wait for more
                # bytes rather than surfacing a junk Key.
                continue
            sequences = self._key_parser.feed(text)
            if sequences:
                return parse_key(sequences[0])
            # No complete sequence yet — if we're sitting on a pending
            # ESC and the deadline is close, flush it.
            if self._key_parser.has_pending_escape and deadline is not None:
                remaining = deadline - _monotonic()
                if remaining < self.ESC_TIMEOUT:
                    seq = self._key_parser.flush_pending_escape()
                    if seq is not None:
                        return parse_key(seq)

    def on_key(self, callback: Callable[[Key], None]) -> Callable[[], None]:
        """Subscribe ``callback`` to parsed :class:`Key` events.

        Returns an unsubscribe function. A daemon thread is started on
        the first subscriber and torn down when the last one leaves.
        Calling this when raw mode is not supported is safe — the
        callback simply never fires.

        The reader thread is not started until :meth:`enable_input` is
        called — this lets the render pipeline finish setting up
        disposes (resize / SIGINT / Ctrl+C) before any handler can
        fire, avoiding a teardown race.
        """
        with self._raw_lock:
            self._key_callbacks.append(callback)
        return lambda: self._unsub_key(callback)

    def enable_input(self) -> None:
        """Start the reader thread if there are subscribers and raw mode
        is supported. Idempotent.
        """
        with self._raw_lock:
            if (
                self._key_thread is None
                and self._key_callbacks
                and self.is_raw_mode_supported
            ):
                self._start_key_loop()

    def _unsub_key(self, callback: Callable[[Key], None]) -> None:
        with self._raw_lock:
            with suppress(ValueError):
                self._key_callbacks.remove(callback)
            should_stop = not self._key_callbacks and self._key_thread is not None
        # Run teardown outside the lock — ``_stop_key_loop`` joins the
        # reader thread, which itself acquires this lock on each chunk.
        if should_stop:
            self._stop_key_loop()

    def _start_key_loop(self) -> None:
        # Ensure raw mode is on so keystrokes arrive immediately.
        if not self._raw_active:
            self.enter_raw_mode()
        self._key_stop = threading.Event()
        t = threading.Thread(
            target=self._key_loop,
            args=(self._key_stop,),
            name="pyink-input-reader",
            daemon=True,
        )
        self._key_thread = t
        t.start()

    def _stop_key_loop(self) -> None:
        stop = self._key_stop
        thread = self._key_thread
        self._key_thread = None
        self._key_stop = None
        if stop is not None:
            stop.set()
        if thread is not None and thread is not threading.current_thread():
            # Wait for the reader to observe ``stop``. The loop polls
            # every 50 ms so 1 s is ample headroom. Skip the join when
            # ``_stop_key_loop`` is itself running on the reader thread
            # (e.g. unmount triggered from inside a key handler) — a
            # self-join would raise and the reader will observe ``stop``
            # on its next loop iteration anyway.
            with suppress(RuntimeError):
                thread.join(timeout=1.0)
        # Leave raw mode if no one else needs it.
        if self._raw_active and not self._key_callbacks:
            self.exit_raw_mode()

    def _key_loop(self, stop: threading.Event) -> None:
        try:
            fd = self.stdin.fileno()
        except (AttributeError, OSError, ValueError):  # pragma: no cover
            return
        consecutive_errors = 0
        while not stop.is_set():
            # Short wait so we re-check ``stop`` frequently — keeps the
            # join in :meth:`_stop_key_loop` snappy.
            if not _wait_for_input(fd, 0.05):
                continue
            try:
                data = _read_stdin_chunk(fd, self.READ_CHUNK)
            except (BlockingIOError, OSError):
                # Repeated read failures (e.g. fd closed under us)
                # mean the input source is gone — bail out instead of
                # busy-spinning.
                consecutive_errors += 1
                if consecutive_errors > 3:
                    return
                continue
            consecutive_errors = 0
            if not data:
                continue
            text = self._decode_utf8(data)
            if not text:
                # Incomplete multi-byte UTF-8 sequence — wait for the
                # next chunk before feeding the parser so a split
                # codepoint surfaces as one Key instead of N junk ones.
                continue
            with self._raw_lock:
                sequences = self._key_parser.feed(text)
                callbacks = list(self._key_callbacks)
            self._dispatch_sequences(sequences, callbacks)
            # If we're holding a pending ESC and nothing else arrives
            # soon, flush it as a lone Escape press.
            if self._key_parser.has_pending_escape and not _wait_for_input(
                fd, self.ESC_TIMEOUT
            ):
                flushed = self._key_parser.flush_pending_escape()
                if flushed is not None:
                    key = parse_key(flushed)
                    with self._raw_lock:
                        callbacks = list(self._key_callbacks)
                    for cb in callbacks:
                        _safe_invoke_key(cb, key)

    def _dispatch_sequences(
        self,
        sequences: list[str],
        callbacks: list[Callable[[Key], None]],
    ) -> None:
        """Push parsed sequences through the paste-buffering dispatcher.

        Bracketed paste handling: when a ``paste_start`` marker is seen,
        all subsequent single-char events are accumulated into
        :attr:`_paste_buffer` until the matching ``paste_end`` marker.
        The accumulated payload is then delivered as one synthetic
        :class:`Key` with ``paste`` populated, so handlers see the whole
        paste as a single edit (one ``on_change`` instead of N).
        Markers themselves are never forwarded to handlers. Non-paste
        sequences pass straight through.

        ``callbacks`` is the snapshot taken at the top of the loop; we
        reuse it for every event the dispatcher emits so unsubscribe
        mid-dispatch is safe.
        """
        for seq in sequences:
            key = parse_key(seq)
            if key.paste_end:
                # Close the active paste. If we somehow see a closing
                # marker without an opening one, just ignore it.
                payload = self._paste_buffer
                self._paste_buffer = None
                if payload is not None:
                    paste_key = Key(input="", paste=payload)
                    for cb in callbacks:
                        _safe_invoke_key(cb, paste_key)
                continue
            if self._paste_buffer is not None:
                # Inside a paste — accumulate the character payload of
                # every key. Marker / arrow / function keys inside a
                # paste are rare (the terminal strips paste markers
                # before sending); to keep the payload a plain string
                # we ignore keys without ``input`` text.
                if key.input:
                    self._paste_buffer += key.input
                continue
            if key.paste_start:
                self._paste_buffer = ""
                continue
            for cb in callbacks:
                _safe_invoke_key(cb, key)

    @staticmethod
    def _compute_wait(deadline: float | None) -> float | None:
        if deadline is None:
            return None
        return max(0.0, deadline - _monotonic())

    def _decode_utf8(self, raw: bytes) -> str:
        """Incrementally decode ``raw`` as UTF-8.

        The incremental decoder buffers a partial trailing sequence
        (e.g. a lead byte whose continuation bytes haven't arrived yet)
        and returns only the fully-decoded prefix. On the next call the
        buffered bytes are prepended to the new chunk so a multi-byte
        codepoint split across two raw reads reassembles into one
        character before reaching :class:`KeyParser`.

        Returns an empty string when ``raw`` completes no codepoints —
        callers should treat that as "wait for more bytes" and not feed
        the parser at all.
        """
        with self._raw_lock:
            return self._utf8_decoder.decode(raw)

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


def _safe_invoke_key(callback: Callable[[Key], None], key: Key) -> None:
    with suppress(Exception):
        callback(key)


# ---------------------------------------------------------------------------
# Platform detection + low-level input wait helpers
# ---------------------------------------------------------------------------

_PLATFORM: str = "windows" if sys.platform.startswith("win") else "unix"


def _monotonic() -> float:
    return time.monotonic()


def _wait_for_input(fd: int, timeout: float | None) -> bool:
    """Return ``True`` when ``fd`` is readable within ``timeout`` seconds.

    ``timeout=None`` blocks indefinitely. ``timeout=0`` polls. Uses
    :func:`select.select` on Unix; on Windows we approximate with a
    busy-poll since ``select`` only works on sockets there.
    """
    if _PLATFORM == "windows":
        # msvcrt.kbhit polls the console input buffer. Loop with a short
        # sleep so we honour ``timeout``.
        import msvcrt

        deadline = None if timeout is None else _monotonic() + timeout
        while True:
            if msvcrt.kbhit():
                return True
            if deadline is not None and _monotonic() >= deadline:
                return False
            time.sleep(0.01)
    # Unix path.
    if timeout is None:
        rlist, _, _ = select.select([fd], [], [])
        return bool(rlist)
    rlist, _, _ = select.select([fd], [], [], timeout)
    return bool(rlist)


def _read_stdin_chunk(fd: int, max_bytes: int) -> bytes:
    """Read up to ``max_bytes`` from stdin in a platform-correct way.

    On Unix we use :func:`os.read` directly. On Windows we use
    :func:`msvcrt.getch` byte-by-byte and drain every currently-available
    byte in one call — :func:`os.read` on a Windows console handle (when
    VT input mode is enabled) returns a single byte per call and drops
    the rest, which would fragment multi-byte escape sequences
    (``\\x1b[A`` arrives across three ``os.read`` calls and the second
    half gets lost to the next loop iteration). Pulling all available
    bytes in one go keeps each escape sequence atomic.
    """
    if _PLATFORM == "windows":
        import msvcrt

        chunks: list[bytes] = []
        while msvcrt.kbhit() and len(chunks) < max_bytes:
            chunks.append(msvcrt.getch())
        return b"".join(chunks)
    return os.read(fd, max_bytes)


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
