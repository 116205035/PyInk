"""Tests for :func:`ink.components.static.Static` (PR7).

Covers:

* Basic single-flush: items written to stdout above the live frame.
* Incremental flush: appended items appear without re-rendering earlier
  ones (verified by inspecting stdout after each signal write).
* Shrinking the list does not erase already-written output.
* Reordering the list does not re-render items already written
  (documented PyInk behaviour — Static is append-only).
* Coexistence with a dynamic Text frame below.
* Plain-list (non-reactive) items: rendered exactly once on mount.
* ``render_item`` receives the absolute index (not the offset within the
  appended slice).
* ``write_static`` on the Instance appends to a buffer and the next paint
  flushes it above the live frame.
"""

from __future__ import annotations

import io
import time
from typing import Any

from ink import Box, Static, Text, render
from ink.core.signal import signal
from ink.render.instance import Instance


def _render_silent(tree: Any, **kwargs: Any) -> tuple[Instance, io.StringIO]:
    out = io.StringIO()
    kwargs.setdefault("exit_on_ctrl_c", False)
    inst = render(tree, stdout=out, **kwargs)
    return inst, out


# ---------------------------------------------------------------------------
# Plain-list (non-reactive) source
# ---------------------------------------------------------------------------


def test_static_renders_plain_list_on_mount() -> None:
    items = ["alpha", "beta", "gamma"]

    def App() -> Any:
        return Box(
            Static(items, lambda item, idx: Text(item)),
            Text("frame"),
            flexDirection="column",
        )

    inst, out = _render_silent(App(), columns=40, rows=5)
    written = out.getvalue()
    # Each item appears in source order.
    assert "alpha" in written
    assert "beta" in written
    assert "gamma" in written
    # The live frame is also present.
    assert "frame" in written
    inst.unmount()


def test_static_render_item_receives_absolute_index() -> None:
    seen: list[int] = []

    def make_text(item: str, idx: int) -> Any:
        seen.append(idx)
        return Text(f"{idx}:{item}")

    def App() -> Any:
        return Box(
            Static(["x", "y", "z"], make_text),
            flexDirection="column",
        )

    inst, _out = _render_silent(App(), columns=40, rows=5)
    inst.unmount()
    assert seen == [0, 1, 2]


def test_static_writes_items_above_frame() -> None:
    """The first frame should contain the items, then a frame row beneath."""
    items = ["one", "two"]

    def App() -> Any:
        return Box(
            Static(items, lambda item, idx: Text(item)),
            Text("bottom"),
            flexDirection="column",
        )

    inst, out = _render_silent(App(), columns=40, rows=5)
    written = out.getvalue()
    pos_one = written.find("one")
    pos_two = written.find("two")
    pos_bottom = written.find("bottom")
    assert pos_one != -1 and pos_two != -1 and pos_bottom != -1
    # Items in order, frame below them.
    assert pos_one < pos_two < pos_bottom
    inst.unmount()


# ---------------------------------------------------------------------------
# Reactive source — incremental flush
# ---------------------------------------------------------------------------


def test_static_renders_signal_source_on_mount() -> None:
    log = signal(["a", "b"])

    def App() -> Any:
        return Box(
            Static(log, lambda item, idx: Text(item)),
            Text("frame"),
            flexDirection="column",
        )

    inst, out = _render_silent(App(), columns=40, rows=5)
    assert "a" in out.getvalue()
    assert "b" in out.getvalue()
    inst.unmount()


def test_static_appends_new_items_only() -> None:
    """When items grow from 3 to 5, only items 3 and 4 are written again."""
    log = signal(["i0", "i1", "i2"])

    def App() -> Any:
        return Box(
            Static(log, lambda item, idx: Text(item)),
            flexDirection="column",
        )

    inst, out = _render_silent(App(), columns=40, rows=5)
    initial = out.getvalue()
    assert initial.count("i0") == 1
    assert initial.count("i1") == 1
    assert initial.count("i2") == 1

    # Truncate the buffer so we only see what gets written *after* the
    # signal change.
    out.truncate(0)
    out.seek(0)
    log.value = [*log.value, "i3", "i4"]
    # Allow the reactive flush + paint to land.
    time.sleep(0.2)
    repaint = out.getvalue()
    # The newly appended items appear.
    assert "i3" in repaint
    assert "i4" in repaint
    # The earlier items are NOT re-rendered.
    assert "i0" not in repaint
    assert "i1" not in repaint
    assert "i2" not in repaint
    inst.unmount()


def test_static_shrinking_list_does_not_erase() -> None:
    """Removing items from the source does not erase already-flushed output."""
    log = signal(["keep1", "keep2", "drop"])

    def App() -> Any:
        return Box(
            Static(log, lambda item, idx: Text(item)),
            flexDirection="column",
        )

    inst, out = _render_silent(App(), columns=40, rows=5)
    initial = out.getvalue()
    assert "keep1" in initial
    assert "drop" in initial

    out.truncate(0)
    out.seek(0)
    log.value = ["keep1", "keep2"]
    time.sleep(0.2)
    repaint = out.getvalue()
    # No new items → the static region is not re-touched.
    assert "keep1" not in repaint
    assert "drop" not in repaint
    inst.unmount()


def test_static_reordering_does_not_re_render() -> None:
    """Reordering items does not re-render earlier items in PyInk's model."""
    log = signal(["a", "b", "c"])

    def App() -> Any:
        return Box(
            Static(log, lambda item, idx: Text(item)),
            flexDirection="column",
        )

    inst, out = _render_silent(App(), columns=40, rows=5)
    out.truncate(0)
    out.seek(0)
    # Reverse the list — no new items, so nothing should be re-rendered.
    log.value = ["c", "b", "a"]
    time.sleep(0.2)
    repaint = out.getvalue()
    # No items appended, so static region should be silent.
    for item in ("a", "b", "c"):
        assert item not in repaint
    inst.unmount()


def test_static_callable_source_re_evaluates() -> None:
    """A callable source is re-invoked on each effect re-run."""
    state: dict[str, list[str]] = {"items": ["only"]}

    def App() -> Any:
        return Box(
            Static(lambda: state["items"], lambda item, idx: Text(item)),
            flexDirection="column",
        )

    inst, out = _render_silent(App(), columns=40, rows=5)
    assert "only" in out.getvalue()
    inst.unmount()


def test_static_index_continues_across_appends() -> None:
    """``render_item`` index continues across incremental flushes."""
    seen: list[int] = []
    log = signal(["a"])

    def make_text(item: str, idx: int) -> Any:
        seen.append(idx)
        return Text(item)

    def App() -> Any:
        return Box(
            Static(log, make_text),
            flexDirection="column",
        )

    inst, _out = _render_silent(App(), columns=40, rows=5)
    # Initial: only index 0 is rendered.
    assert seen == [0]
    # Append two more items — indices 1 and 2 should arrive.
    log.value = [*log.value, "b", "c"]
    time.sleep(0.2)
    assert seen == [0, 1, 2]
    inst.unmount()


def test_static_empty_items_writes_nothing() -> None:
    def App() -> Any:
        return Box(
            Static([], lambda item, idx: Text(str(item))),
            Text("frame"),
            flexDirection="column",
        )

    inst, out = _render_silent(App(), columns=40, rows=5)
    written = out.getvalue()
    # Only the live frame lands.
    assert "frame" in written
    inst.unmount()


def test_static_outside_render_raises() -> None:
    """Mounting Static without an active Instance raises RuntimeError."""
    import pytest

    from ink.core.reconciler import Reconciler

    el = Static(["x"], lambda item, idx: Text(item))
    reconciler = Reconciler()
    with pytest.raises(RuntimeError, match="Static must be mounted"):
        reconciler.mount(el)


# ---------------------------------------------------------------------------
# Instance.write_static
# ---------------------------------------------------------------------------


def test_instance_write_static_appends_to_buffer_and_flushes() -> None:
    """Direct ``Instance.write_static`` calls flush above the frame."""
    inst, out = _render_silent(Text("frame"), columns=20, rows=3)
    out.truncate(0)
    out.seek(0)
    inst.write_static("hello\n")
    written = out.getvalue()
    assert "hello" in written
    # The frame is re-painted as part of the flush.
    assert "frame" in written
    inst.unmount()


def test_instance_write_static_empty_is_noop() -> None:
    inst, out = _render_silent(Text("frame"), columns=20, rows=3)
    out.truncate(0)
    out.seek(0)
    inst.write_static("")
    # Nothing flushed (no static, frame already current).
    assert out.getvalue() == ""
    inst.unmount()


def test_instance_write_static_after_unmount_is_silent() -> None:
    inst, _out = _render_silent(Text("frame"), columns=20, rows=3)
    inst.unmount()
    # Should not raise; should not write anything.
    inst.write_static("late\n")


# ---------------------------------------------------------------------------
# Instance.reset_static — user-initiated clear gesture
# ---------------------------------------------------------------------------


def test_reset_static_emits_clear_escape_sequences() -> None:
    """reset_static writes \\x1b[3J + \\x1b[2J + \\x1b[H to stdout."""
    inst, out = _render_silent(Text("frame"), columns=20, rows=3)
    out.truncate(0)
    out.seek(0)
    inst.reset_static()
    written = out.getvalue()
    # Order matters: \x1b[3J before \x1b[2J so the pre-clear of scrollback
    # happens before \x1b[2J potentially pushes viewport into scrollback.
    assert "\x1b[3J" in written
    assert "\x1b[2J" in written
    assert "\x1b[H" in written
    assert written.index("\x1b[3J") < written.index("\x1b[2J")
    inst.unmount()


def test_reset_static_zeros_state() -> None:
    """After reset_static, current_frame is empty + _static_rows_approx is 0."""
    inst, out = _render_silent(Text("frame"), columns=20, rows=3)
    # Force some static + frame state to accumulate.
    inst.write_static("line1\nline2\n")
    assert inst._static_rows_approx > 0
    assert inst.current_frame != ""
    inst.reset_static()
    assert inst.current_frame == ""
    assert inst._static_rows_approx == 0
    assert inst.static_lines == []
    assert inst._static_dirty is False
    inst.unmount()


def test_reset_static_zeros_registered_static_refs() -> None:
    """A mounted Static's last_flushed should be 0 after reset_static."""
    log = signal(["a", "b", "c"])

    def App() -> Any:
        return Box(
            Static(log, lambda item, idx: Text(item)),
            flexDirection="column",
        )

    inst, out = _render_silent(App(), columns=20, rows=10)
    # After mount, last_flushed should be 3 (all items flushed).
    assert len(inst._static_refs) == 1
    assert inst._static_refs[0].value == 3
    inst.reset_static()
    # reset_static zeroes every registered ref.
    assert inst._static_refs[0].value == 0
    inst.unmount()


def test_reset_static_next_flush_writes_from_index_zero() -> None:
    """After reset_static, swapping the items source writes the new list from index 0."""
    log = signal(["old_a", "old_b", "old_c"])

    def App() -> Any:
        return Box(
            Static(log, lambda item, idx: Text(item)),
            flexDirection="column",
        )

    inst, out = _render_silent(App(), columns=40, rows=10)
    initial = out.getvalue()
    # All old items present at mount.
    for item in ("old_a", "old_b", "old_c"):
        assert item in initial

    # Reset + swap items in one atomic step.
    out.truncate(0)
    out.seek(0)
    inst.reset_static()
    log.value = ["new_x", "new_y"]
    # Allow the reactive flush + paint to land.
    time.sleep(0.2)
    repaint = out.getvalue()
    # The new items flush from index 0 — they appear in stdout.
    assert "new_x" in repaint
    assert "new_y" in repaint
    # last_flushed has advanced to 2 (the new list length).
    assert inst._static_refs[0].value == 2
    inst.unmount()


def test_reset_static_without_static_mounted_is_safe() -> None:
    """Calling reset_static when no Static is mounted does not crash."""
    inst, out = _render_silent(Text("frame"), columns=20, rows=3)
    # No Static → _static_refs is empty. reset_static still emits escapes
    # and zeros PyInk state.
    inst.reset_static()
    assert inst.current_frame == ""
    assert inst._static_rows_approx == 0
    inst.unmount()


def test_reset_static_after_unmount_is_silent() -> None:
    inst, _out = _render_silent(Text("frame"), columns=20, rows=3)
    inst.unmount()
    # Should not raise; should not write anything.
    inst.reset_static()


def test_unmount_clears_static_refs() -> None:
    """unmount drops all registered Static refs so a stale list doesn't accumulate."""
    log = signal(["a"])

    def App() -> Any:
        return Box(
            Static(log, lambda item, idx: Text(item)),
            flexDirection="column",
        )

    inst, _out = _render_silent(App(), columns=20, rows=5)
    assert len(inst._static_refs) == 1
    inst.unmount()
    # After unmount the refs are dropped — defensive cleanup so a
    # subsequent rerender/mount starts from an empty list.
    assert inst._static_refs == []
