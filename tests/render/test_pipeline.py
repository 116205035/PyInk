"""Tests for :func:`pyink.render.render` and the live render pipeline (PR5).

These cover the public entry point and its integration with the
reconciler, scheduler, terminal abstraction, and the reactive render
loop. Most tests use :class:`io.StringIO` as a fake stdout.
"""

from __future__ import annotations

import io
import threading
import time

from pyink import Box, Newline, Text, render
from pyink.core.signal import signal


def _render_silent(tree: object, **kwargs: object) -> tuple[object, io.StringIO]:
    out = io.StringIO()
    kwargs.setdefault("exit_on_ctrl_c", False)
    inst = render(tree, stdout=out, **kwargs)  # type: ignore[arg-type]
    return inst, out


# ---------------------------------------------------------------------------
# Mount + initial paint
# ---------------------------------------------------------------------------


def test_mount_writes_initial_frame() -> None:
    inst, out = _render_silent(Text("hello"), columns=40, rows=3)
    assert "hello" in out.getvalue()
    # No full-screen clear ever (PRD Decision 3).
    assert "\x1b[2J" not in out.getvalue()
    inst.unmount()  # type: ignore[attr-defined]


def test_mount_box_with_multiple_rows() -> None:
    tree = Box(
        Text("row1"),
        Text("row2"),
        Text("row3"),
        flexDirection="column",
    )
    inst, out = _render_silent(tree, columns=20, rows=5)
    written = out.getvalue()
    assert "row1" in written
    assert "row2" in written
    assert "row3" in written
    inst.unmount()  # type: ignore[attr-defined]


def test_mount_with_newline_in_text() -> None:
    inst, out = _render_silent(Text("a\nb\nc"), columns=10, rows=5)
    written = out.getvalue()
    assert "a" in written
    assert "b" in written
    assert "c" in written
    inst.unmount()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# rerender via Instance.rerender
# ---------------------------------------------------------------------------


def test_rerender_writes_diff_not_full_paint() -> None:
    inst, out = _render_silent(Text("first"), columns=20, rows=2)
    # After the initial paint the cursor is parked at the top row.
    out.truncate(0)
    out.seek(0)
    inst.rerender(Text("second"))  # type: ignore[attr-defined]
    repaint = out.getvalue()
    assert "second" in repaint
    # Inline diff uses cursor-move + line-clear; never a full repaint
    # (the new content is NOT preceded by the full frame because the
    # initial paint was already in place — the diff only writes the
    # changed row).
    assert "\x1b[2K" in repaint
    assert "\x1b[2J" not in repaint
    inst.unmount()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Frame-level diff correctness
# ---------------------------------------------------------------------------


def test_diff_only_repaints_changed_row() -> None:
    tree = Box(
        Text("alpha"),
        Text("beta"),
        Text("gamma"),
        flexDirection="column",
    )
    inst, out = _render_silent(tree, columns=20, rows=5)

    new_tree = Box(
        Text("alpha"),
        Text("BETA"),
        Text("gamma"),
        flexDirection="column",
    )
    out.truncate(0)
    out.seek(0)
    inst.rerender(new_tree)  # type: ignore[attr-defined]
    repaint = out.getvalue()
    assert "BETA" in repaint
    # The unchanged rows must NOT appear in the repaint.
    assert "alpha" not in repaint
    assert "gamma" not in repaint
    assert "\x1b[2J" not in repaint
    inst.unmount()  # type: ignore[attr-defined]


def test_diff_handles_growing_frame() -> None:
    inst, out = _render_silent(Text("one"), columns=20, rows=5)
    out.truncate(0)
    out.seek(0)
    new_tree = Box(
        Text("one"),
        Text("two"),
        Text("three"),
        flexDirection="column",
    )
    inst.rerender(new_tree)  # type: ignore[attr-defined]
    repaint = out.getvalue()
    assert "two" in repaint
    assert "three" in repaint
    # The unchanged row "one" must NOT be rewritten.
    assert "one" not in repaint
    assert "\x1b[2J" not in repaint
    inst.unmount()  # type: ignore[attr-defined]


def test_diff_handles_shrinking_frame() -> None:
    tree = Box(
        Text("a"),
        Text("b"),
        Text("c"),
        flexDirection="column",
    )
    inst, out = _render_silent(tree, columns=20, rows=5)
    out.truncate(0)
    out.seek(0)
    inst.rerender(Text("a"))  # type: ignore[attr-defined]
    repaint = out.getvalue()
    # Rows b/c must be cleared (line-erase) but never re-emitted as content.
    assert "b" not in repaint
    assert "c" not in repaint
    assert repaint.count("\x1b[2K") >= 2
    assert "\x1b[2J" not in repaint
    inst.unmount()  # type: ignore[attr-defined]


def test_identical_rerender_emits_nothing() -> None:
    inst, out = _render_silent(Text("same"), columns=20, rows=2)
    out.truncate(0)
    out.seek(0)
    inst.rerender(Text("same"))  # type: ignore[attr-defined]
    assert out.getvalue() == ""
    inst.unmount()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Alternate screen
# ---------------------------------------------------------------------------


def test_alternate_screen_enters_and_exits() -> None:
    inst, out = _render_silent(Text("hi"), columns=20, rows=2, alternate_screen=True)
    written = out.getvalue()
    assert "\x1b[?1049h" in written
    assert "\x1b[?25l" in written
    inst.unmount()  # type: ignore[attr-defined]
    final = out.getvalue()
    assert "\x1b[?1049l" in final
    assert "\x1b[?25h" in final
    # Scrollback is preserved — never use 2J.
    assert "\x1b[2J" not in final


# ---------------------------------------------------------------------------
# max_fps coalescing
# ---------------------------------------------------------------------------


def test_max_fps_coalesces_burst_writes() -> None:
    counter = signal(0)

    def Counter() -> object:
        return Text(lambda: f"count={counter.value}")

    inst, out = _render_silent(Counter(), columns=40, rows=3, max_fps=30)
    out.truncate(0)
    out.seek(0)
    for _ in range(10):
        counter.value += 1
    time.sleep(0.25)
    repaint = out.getvalue()
    assert "count=10" in repaint
    inst.unmount()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Reactive repaint via signal + callable Text
# ---------------------------------------------------------------------------


def test_reactive_counter_repaints_on_signal_change() -> None:
    counter = signal(0)

    def Counter() -> object:
        return Box(
            Text(lambda: f"count={counter.value}"),
            flexDirection="column",
        )

    inst, out = _render_silent(Counter(), columns=40, rows=3)
    initial = out.getvalue()
    assert "count=0" in initial
    out.truncate(0)
    out.seek(0)
    counter.value = 42
    time.sleep(0.25)
    repaint = out.getvalue()
    assert "count=42" in repaint
    assert "\x1b[2J" not in repaint
    inst.unmount()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Threaded integration (wait_until_exit)
# ---------------------------------------------------------------------------


def test_wait_until_exit_blocks_then_returns() -> None:
    counter = signal(0)

    def Counter() -> object:
        return Text(lambda: f"{counter.value} ticks")

    inst, out = _render_silent(Counter(), columns=40, rows=3)

    def worker() -> None:
        time.sleep(0.05)
        counter.value = 1
        time.sleep(0.1)
        inst.unmount()  # type: ignore[attr-defined]

    t = threading.Thread(target=worker)
    t.start()
    inst.wait_until_exit()  # type: ignore[attr-defined]
    t.join(timeout=1.0)
    assert not t.is_alive()
    # At least the initial frame landed.
    assert "0 ticks" in out.getvalue()


# ---------------------------------------------------------------------------
# Cleanup — atexit registration doesn't crash on re-entry
# ---------------------------------------------------------------------------


def test_unmount_then_atexit_cleanup_does_not_raise() -> None:
    inst, _ = _render_silent(Text("x"), columns=10, rows=2)
    inst.unmount()  # type: ignore[attr-defined]
    # atexit will call cleanup() at interpreter exit; calling it manually
    # here is also safe (idempotent).
    inst.cleanup()  # type: ignore[attr-defined]


def test_cleanup_unregisters_from_atexit() -> None:
    """cleanup() should drop the Instance from atexit so a later
    process-wide teardown doesn't re-enter. We verify the internal
    flag flips to False on first cleanup and stays False on repeat
    calls."""
    inst, _ = _render_silent(Text("x"), columns=10, rows=2)
    assert inst._atexit_registered  # type: ignore[attr-defined]
    inst.cleanup()  # type: ignore[attr-defined]
    assert not inst._atexit_registered  # type: ignore[attr-defined]
    # Idempotent: second cleanup doesn't touch atexit again.
    inst.cleanup()  # type: ignore[attr-defined]
    assert not inst._atexit_registered  # type: ignore[attr-defined]


def test_render_with_newline_component() -> None:
    """A small end-to-end test using multiple built-in components."""
    tree = Box(
        Text("title", bold=True),
        Newline(),
        Text("body"),
        flexDirection="column",
    )
    inst, out = _render_silent(tree, columns=40, rows=5)
    written = out.getvalue()
    assert "title" in written
    assert "body" in written
    inst.unmount()  # type: ignore[attr-defined]
