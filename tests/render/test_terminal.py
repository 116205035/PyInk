"""Tests for :mod:`pyink.render.terminal` (PR5).

The Terminal wraps stdout with size detection, resize-callback
registration (Unix SIGWINCH vs Windows polling), and the alternate
screen toggle. We mock stdout via :class:`io.StringIO` so no escape
sequences hit the real terminal during the test run.
"""

from __future__ import annotations

import io
import time

from pyink.render.terminal import Terminal


def test_columns_and_rows_fallback_when_not_a_tty() -> None:
    term = Terminal(io.StringIO())
    cols, rows = term.get_size()
    # Falls back to 80×24 when stdout is not a real TTY.
    assert cols >= 1
    assert rows >= 1


def test_explicit_size_override_via_env(monkeypatch: object) -> None:
    # COLUMNS env var is honoured by shutil.get_terminal_size when stdout
    # is not a TTY.
    import os

    prev_cols = os.environ.get("COLUMNS")
    prev_lines = os.environ.get("LINES")
    os.environ["COLUMNS"] = "120"
    os.environ["LINES"] = "40"
    try:
        term = Terminal(io.StringIO())
        assert term.columns == 120
        assert term.rows == 40
    finally:
        if prev_cols is None:
            os.environ.pop("COLUMNS", None)
        else:
            os.environ["COLUMNS"] = prev_cols
        if prev_lines is None:
            os.environ.pop("LINES", None)
        else:
            os.environ["LINES"] = prev_lines


def test_enter_exit_alternate_screen_emits_correct_sequences() -> None:
    out = io.StringIO()
    term = Terminal(out)
    term.enter_alternate_screen()
    written = out.getvalue()
    # 1049 = alt screen buffer, 25 = hide cursor.
    assert "\x1b[?1049h" in written
    assert "\x1b[?25l" in written
    assert term.in_alternate_screen
    term.exit_alternate_screen()
    written2 = out.getvalue()
    assert "\x1b[?1049l" in written2
    assert "\x1b[?25h" in written2
    assert not term.in_alternate_screen


def test_enter_alternate_screen_idempotent() -> None:
    out = io.StringIO()
    term = Terminal(out)
    term.enter_alternate_screen()
    term.enter_alternate_screen()
    # Only one enter sequence.
    assert out.getvalue().count("\x1b[?1049h") == 1


def test_exit_alternate_screen_without_enter_is_noop() -> None:
    out = io.StringIO()
    term = Terminal(out)
    term.exit_alternate_screen()
    assert out.getvalue() == ""


def test_resize_poll_callback_fires_when_size_changes() -> None:
    # Force the poller path by using a StringIO (no SIGWINCH routing).
    out = io.StringIO()
    term = Terminal(out)
    fired: list[tuple[int, int]] = []

    # Patch get_size so the poller sees a "change".
    original = Terminal.get_size
    counter = {"n": 0}

    def fake_size(self: Terminal) -> tuple[int, int]:
        counter["n"] += 1
        # Alternate between two sizes on every call so the poller always
        # detects a "change".
        if counter["n"] % 2 == 0:
            return (100, 30)
        return (80, 24)

    Terminal.get_size = fake_size  # type: ignore[method-assign]
    try:
        dispose = term.on_resize(lambda c, r: fired.append((c, r)))
        # Wait long enough for the 200ms poll interval to fire at least
        # a couple of times.
        time.sleep(0.6)
        dispose()
    finally:
        Terminal.get_size = original  # type: ignore[method-assign]
    assert len(fired) >= 1


def test_resize_unsubscribe_stops_callbacks() -> None:
    out = io.StringIO()
    term = Terminal(out)
    fired: list[tuple[int, int]] = []

    dispose = term.on_resize(lambda c, r: fired.append((c, r)))
    dispose()
    # Wait one poll cycle — no callbacks should fire because we just
    # unsubscribed (and the poller was torn down).
    time.sleep(0.3)
    assert fired == []


def test_write_and_flush_go_to_stdout() -> None:
    out = io.StringIO()
    term = Terminal(out)
    term.write("abc")
    term.flush()
    assert out.getvalue() == "abc"
