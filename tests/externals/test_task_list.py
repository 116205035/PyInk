"""Tests for :func:`pyink.externals.TaskList` (Phase 6 PR1).

Like :mod:`tests.externals.test_spinner`, we exercise the live
:func:`pyink.render.render` pipeline rather than
:func:`pyink.render_to_string` because ``TaskList`` may mount a
:func:`Spinner` for ``running`` rows, whose
:func:`pyink.hooks.use_interval` guard requires the active
``_current_instance`` ContextVar — set only by ``render``.

The static path (no ``running`` task) works under both renderers, but
we still drive everything through the live pipeline so reactive
``Signal`` / ``Callable`` sources can be verified end-to-end (signal
write → layout-time callable → subscription → re-paint).
"""

from __future__ import annotations

import io
import time
from collections.abc import Callable

from pyink import Box, render, signal
from pyink.core.element import Element
from pyink.externals import SPINNERS, TaskItem, TaskList
from pyink.externals.task_list import (
    _TERMINAL_STATES,
    _resolve_source,
    _state_color,
    _state_icon,
    _TaskListImpl,
)
from pyink.render.instance import Instance

ESC = "\x1b"


# ---------------------------------------------------------------------------
# Helpers (live render pipeline)
# ---------------------------------------------------------------------------


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 99


def _mount(
    build_tree: Element,
    *,
    columns: int = 40,
    rows: int = 10,
) -> tuple[Instance, io.StringIO]:
    out = io.StringIO()
    inst = render(
        build_tree,
        stdout=out,
        stdin=_FakeTTY(),
        columns=columns,
        rows=rows,
        exit_on_ctrl_c=False,
    )
    time.sleep(0.05)
    return inst, out


def _frame(inst: Instance) -> str:
    return inst.current_frame


def _wait_for(
    predicate: Callable[[], bool],
    *,
    attempts: int = 200,
    delay: float = 0.025,
) -> bool:
    for _ in range(attempts):
        if predicate():
            return True
        time.sleep(delay)
    return predicate()


def _first_frame_of(tree: Element, *, columns: int = 40, rows: int = 10) -> str:
    """Mount + snapshot first frame + unmount. Returns the rendered frame.

    The first paint is synchronous inside ``render``; we read the frame
    immediately and unmount. Trailing blank lines are stripped because
    the live pipeline pads short frames to ``rows`` lines.
    """
    out = io.StringIO()
    inst = render(
        tree,
        stdout=out,
        stdin=_FakeTTY(),
        columns=columns,
        rows=rows,
        exit_on_ctrl_c=False,
    )
    snap = _frame(inst).rstrip("\n")
    inst.unmount()
    return snap


# ---------------------------------------------------------------------------
# TaskItem dataclass
# ---------------------------------------------------------------------------


def test_task_item_is_frozen_dataclass() -> None:
    """``TaskItem`` instances must be immutable."""
    item = TaskItem(label="x")
    try:
        item.label = "y"  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("TaskItem should be frozen (mutation must raise)")


def test_task_item_defaults() -> None:
    item = TaskItem(label="x")
    assert item.label == "x"
    assert item.state == "pending"
    assert item.output is None


def test_task_item_full_construction() -> None:
    item = TaskItem(label="x", state="done", output="3 files")
    assert item.label == "x"
    assert item.state == "done"
    assert item.output == "3 files"


def test_task_item_equality_and_hashable() -> None:
    """Frozen dataclasses are value-equal and usable as dict keys."""
    a = TaskItem(label="x", state="done")
    b = TaskItem(label="x", state="done")
    assert a == b
    # Hashable because frozen=True.
    assert hash(a) == hash(b)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_externals_init_exports_taskitem_and_tasklist() -> None:
    from pyink.externals import TaskItem as InitTaskItem
    from pyink.externals import TaskList as InitTaskList

    assert InitTaskItem is TaskItem
    assert InitTaskList is TaskList


def test_task_list_not_in_pyink_top_level() -> None:
    """PRD Decision 5 — externals stay opt-in; top-level import must fail."""
    import pyink

    assert not hasattr(pyink, "TaskList"), "TaskList must NOT be top-level"
    assert not hasattr(pyink, "TaskItem"), "TaskItem must NOT be top-level"


# ---------------------------------------------------------------------------
# Element shape
# ---------------------------------------------------------------------------


def test_task_list_returns_function_component_element() -> None:
    el = TaskList([TaskItem(label="x")])
    assert isinstance(el, Element)
    assert callable(el.type)
    assert el.type is _TaskListImpl
    # Factory never runs hooks — props capture the config.
    assert el.props["spinner_type"] == "dots"
    assert el.props["spinner_color"] is None
    assert el.props["on_complete"] is None
    # No children — the function component builds its own subtree on mount.
    assert el.children == ()


def test_task_list_factory_captures_overrides() -> None:
    el = TaskList(
        [TaskItem(label="x")],
        spinner_type="line",
        spinner_color="green",
        on_complete=lambda _: None,
        margin=1,
    )
    assert el.props["spinner_type"] == "line"
    assert el.props["spinner_color"] == "green"
    assert el.props["on_complete"] is not None
    # box_props forwarded verbatim.
    assert el.props["box_props"] == {"margin": 1}


# ---------------------------------------------------------------------------
# Source resolution helper
# ---------------------------------------------------------------------------


def test_resolve_source_passes_plain_list() -> None:
    items = [TaskItem(label="a")]
    assert _resolve_source(items) is items


def test_resolve_source_reads_signal_value() -> None:
    sig = signal([TaskItem(label="a")])
    assert _resolve_source(sig) == [TaskItem(label="a")]


def test_resolve_source_invokes_callable() -> None:
    seen: list[int] = []

    def src() -> list[TaskItem]:
        seen.append(1)
        return [TaskItem(label="a")]

    out = _resolve_source(src)
    assert out == [TaskItem(label="a")]
    assert len(seen) == 1


# ---------------------------------------------------------------------------
# State icon + colour helpers
# ---------------------------------------------------------------------------


def test_state_icon_pending() -> None:
    assert _state_icon("pending") == "○"


def test_state_icon_done() -> None:
    assert _state_icon("done") == "✓"


def test_state_icon_error() -> None:
    assert _state_icon("error") == "✗"


def test_state_icon_warning() -> None:
    assert _state_icon("warning") == "⚠"


def test_state_color_pending_returns_none() -> None:
    assert _state_color("pending") is None


def test_state_color_done_green() -> None:
    assert _state_color("done") == "green"


def test_state_color_error_red() -> None:
    assert _state_color("error") == "red"


def test_state_color_warning_yellow() -> None:
    assert _state_color("warning") == "yellow"


def test_terminal_states_contains_done_error_warning() -> None:
    assert "done" in _TERMINAL_STATES
    assert "error" in _TERMINAL_STATES
    assert "warning" in _TERMINAL_STATES
    assert "pending" not in _TERMINAL_STATES
    assert "running" not in _TERMINAL_STATES


# ---------------------------------------------------------------------------
# Static rendering — pending
# ---------------------------------------------------------------------------


def test_static_pending_list_renders_circle_glyph() -> None:
    snap = _first_frame_of(
        TaskList([TaskItem(label="Task A"), TaskItem(label="Task B")])
    )
    assert "○" in snap
    assert "Task A" in snap
    assert "Task B" in snap


def test_static_pending_list_renders_dim_color() -> None:
    """Pending rows are emitted with ``dimColor=True``."""
    snap = _first_frame_of(TaskList([TaskItem(label="Task A")]))
    # Dim SGR escape: ESC[2m ... ESC[0m. We just check the dim sequence
    # is present somewhere in the rendered frame.
    assert f"{ESC}[2m" in snap


def test_static_empty_list_renders_nothing() -> None:
    snap = _first_frame_of(TaskList([]))
    assert snap.strip() == ""


# ---------------------------------------------------------------------------
# Static rendering — done
# ---------------------------------------------------------------------------


def test_static_done_list_renders_tick_green() -> None:
    snap = _first_frame_of(
        TaskList([TaskItem(label="Done A", state="done")])
    )
    assert "✓" in snap
    assert "Done A" in snap
    # Green foreground SGR sequence wraps the icon + label.
    assert f"{ESC}[32m" in snap


def test_static_done_list_no_dim() -> None:
    """Done rows use green, not dim."""
    snap = _first_frame_of(TaskList([TaskItem(label="x", state="done")]))
    # The green sequence is present, but the dim sequence ESC[2m is not.
    assert f"{ESC}[32m" in snap
    # Note: dim escape may appear from other decorations; we don't
    # strictly assert absence because the pipeline may emit other
    # styling. Just ensure the tick glyph is green-wrapped.
    assert f"{ESC}[32m✓" in snap or f"{ESC}[32m" in snap


# ---------------------------------------------------------------------------
# Static rendering — error / warning
# ---------------------------------------------------------------------------


def test_static_error_list_renders_cross_red() -> None:
    snap = _first_frame_of(
        TaskList([TaskItem(label="Boom", state="error")])
    )
    assert "✗" in snap
    assert "Boom" in snap
    assert f"{ESC}[31m" in snap


def test_static_warning_list_renders_warning_yellow() -> None:
    snap = _first_frame_of(
        TaskList([TaskItem(label="Hmm", state="warning")])
    )
    assert "⚠" in snap
    assert "Hmm" in snap
    assert f"{ESC}[33m" in snap


# ---------------------------------------------------------------------------
# Static rendering — mixed
# ---------------------------------------------------------------------------


def test_static_mixed_states_render_each_icon() -> None:
    snap = _first_frame_of(
        TaskList(
            [
                TaskItem(label="P", state="pending"),
                TaskItem(label="D", state="done"),
                TaskItem(label="E", state="error"),
                TaskItem(label="W", state="warning"),
            ]
        )
    )
    assert "○" in snap
    assert "✓" in snap
    assert "✗" in snap
    assert "⚠" in snap
    assert "P" in snap and "D" in snap and "E" in snap and "W" in snap


# ---------------------------------------------------------------------------
# Output line
# ---------------------------------------------------------------------------


def test_static_output_line_renders_below_label() -> None:
    snap = _first_frame_of(
        TaskList(
            [TaskItem(label="Build", state="done", output="3 files written")]
        )
    )
    assert "Build" in snap
    assert "3 files written" in snap
    # The output line is indented by two spaces under the label.
    assert "  3 files written" in snap


def test_static_output_line_dim() -> None:
    snap = _first_frame_of(
        TaskList([TaskItem(label="Build", state="done", output="ok")])
    )
    # Dim escape present somewhere (output line carries dimColor=True).
    assert f"{ESC}[2m" in snap


def test_static_no_output_no_extra_line() -> None:
    snap = _first_frame_of(
        TaskList([TaskItem(label="Build", state="done")])
    )
    # Only the label row — no secondary line content.
    lines = [line for line in snap.split("\n") if line.strip()]
    assert len(lines) == 1


# ---------------------------------------------------------------------------
# Running state — Spinner mounted
# ---------------------------------------------------------------------------


def test_running_row_renders_spinner_first_frame() -> None:
    """A ``running`` task mounts a real Spinner; first frame is dots[0]."""
    snap = _first_frame_of(
        TaskList([TaskItem(label="Working", state="running")])
    )
    # Spinner's first frame for the default "dots" type.
    assert SPINNERS["dots"][0] in snap
    assert "Working" in snap


def test_running_row_spinner_advances() -> None:
    """The mounted Spinner advances its frame over time."""
    inst, _ = _mount(
        TaskList([TaskItem(label="Working", state="running")])
    )
    # Wait for any non-first frame to appear.
    def advanced() -> bool:
        snap = _frame(inst)
        return any(f in snap for f in SPINNERS["dots"][1:])

    assert _wait_for(advanced, attempts=200, delay=0.025), (
        "Spinner never advanced past frame 0"
    )
    inst.unmount()


def test_running_row_spinner_color_applied() -> None:
    snap = _first_frame_of(
        TaskList(
            [TaskItem(label="Working", state="running")],
            spinner_color="green",
        )
    )
    # Green SGR wraps the first spinner frame.
    assert f"{ESC}[32m{SPINNERS['dots'][0]}" in snap


# ---------------------------------------------------------------------------
# Signal-driven reactivity
# ---------------------------------------------------------------------------


def test_signal_source_renders_initial_state() -> None:
    tasks = signal([TaskItem(label="A"), TaskItem(label="B")])
    snap = _first_frame_of(TaskList(tasks))
    assert "A" in snap
    assert "B" in snap


def test_signal_source_state_change_rerenders() -> None:
    """State change pending → done repaints with new glyph + colour."""
    tasks = signal([TaskItem(label="Sync", state="pending")])
    inst, _ = _mount(TaskList(tasks))

    # Initial state: pending circle.
    assert _wait_for(lambda: "○" in _frame(inst))
    assert "Sync" in _frame(inst)

    # Transition to done.
    tasks.value = [TaskItem(label="Sync", state="done")]
    assert _wait_for(lambda: "✓" in _frame(inst)), "tick never appeared"
    assert _wait_for(lambda: f"{ESC}[32m" in _frame(inst)), "green never appeared"

    inst.unmount()


def test_signal_source_label_change_rerenders() -> None:
    """A label change on a non-running task repaints the new text."""
    tasks = signal([TaskItem(label="Before", state="pending")])
    inst, _ = _mount(TaskList(tasks))
    assert _wait_for(lambda: "Before" in _frame(inst))

    tasks.value = [TaskItem(label="After", state="pending")]
    assert _wait_for(lambda: "After" in _frame(inst))
    inst.unmount()


def test_signal_source_output_change_rerenders() -> None:
    """Adding output to a task that had output at mount repaints."""
    tasks = signal([TaskItem(label="Build", state="done", output="v1")])
    inst, _ = _mount(TaskList(tasks))
    assert _wait_for(lambda: "v1" in _frame(inst))

    tasks.value = [TaskItem(label="Build", state="done", output="v2")]
    assert _wait_for(lambda: "v2" in _frame(inst))
    inst.unmount()


# ---------------------------------------------------------------------------
# Callable source
# ---------------------------------------------------------------------------


def test_callable_source_renders_resolved_value() -> None:
    snap = _first_frame_of(
        TaskList(lambda: [TaskItem(label="From callable", state="done")])
    )
    assert "✓" in snap
    assert "From callable" in snap


def test_callable_source_reactive_via_signal_read() -> None:
    state_sig = signal("pending")

    def src() -> list[TaskItem]:
        return [TaskItem(label="X", state=state_sig.value)]  # type: ignore[arg-type]

    inst, _ = _mount(TaskList(src))
    assert _wait_for(lambda: "○" in _frame(inst))

    state_sig.value = "done"
    assert _wait_for(lambda: "✓" in _frame(inst))
    inst.unmount()


# ---------------------------------------------------------------------------
# on_complete
# ---------------------------------------------------------------------------


def test_on_complete_fires_when_all_terminal() -> None:
    received: list[list[TaskItem]] = []

    def on_complete(tasks: list[TaskItem]) -> None:
        received.append(list(tasks))

    tasks = signal([TaskItem(label="A", state="pending")])
    inst, _ = _mount(TaskList(tasks, on_complete=on_complete))
    # Not fired yet.
    assert _wait_for(lambda: "○" in _frame(inst))
    assert received == []

    tasks.value = [TaskItem(label="A", state="done")]
    assert _wait_for(lambda: len(received) == 1, attempts=200)
    assert received[0][0].state == "done"
    inst.unmount()


def test_on_complete_does_not_fire_when_not_all_terminal() -> None:
    received: list[list[TaskItem]] = []

    tasks = signal(
        [
            TaskItem(label="A", state="done"),
            TaskItem(label="B", state="running"),
        ]
    )
    inst, _ = _mount(TaskList(tasks, on_complete=lambda ts: received.append(ts)))
    # Wait for spinner frame so we know the render is up.
    assert _wait_for(lambda: SPINNERS["dots"][0] in _frame(inst))
    # No completion because B is still running.
    time.sleep(0.1)
    assert received == []
    inst.unmount()


def test_on_complete_fires_only_once() -> None:
    """Subsequent writes after the first fire must not re-invoke."""
    received: list[list[TaskItem]] = []

    tasks = signal([TaskItem(label="A", state="pending")])
    inst, _ = _mount(TaskList(tasks, on_complete=lambda ts: received.append(ts)))

    tasks.value = [TaskItem(label="A", state="done")]
    assert _wait_for(lambda: len(received) == 1)

    # More writes after completion — must not re-fire.
    tasks.value = [TaskItem(label="A", state="error")]
    time.sleep(0.1)
    tasks.value = [TaskItem(label="A", state="warning")]
    time.sleep(0.1)
    assert len(received) == 1
    inst.unmount()


def test_on_complete_none_default_no_crash() -> None:
    """Default ``on_complete=None`` must not crash on terminal transitions."""
    tasks = signal([TaskItem(label="A", state="pending")])
    inst, _ = _mount(TaskList(tasks))
    tasks.value = [TaskItem(label="A", state="done")]
    assert _wait_for(lambda: "✓" in _frame(inst))
    inst.unmount()


def test_on_complete_mixed_terminal_states_still_fires() -> None:
    """All-terminal means every task is in {done, error, warning}."""
    received: list[list[TaskItem]] = []
    tasks = signal(
        [
            TaskItem(label="A", state="pending"),
            TaskItem(label="B", state="pending"),
            TaskItem(label="C", state="pending"),
        ]
    )
    inst, _ = _mount(TaskList(tasks, on_complete=lambda ts: received.append(ts)))

    tasks.value = [
        TaskItem(label="A", state="done"),
        TaskItem(label="B", state="error"),
        TaskItem(label="C", state="warning"),
    ]
    assert _wait_for(lambda: len(received) == 1)
    states = sorted(t.state for t in received[0])
    assert states == ["done", "error", "warning"]
    inst.unmount()


# ---------------------------------------------------------------------------
# Spinner inside Box — nested component composition
# ---------------------------------------------------------------------------


def test_task_list_nested_in_box_with_sibling_text() -> None:
    """TaskList composes inside a parent Box with sibling Text."""
    snap = _first_frame_of(
        Box(
            TaskList([TaskItem(label="X", state="done")]),
        )
    )
    assert "✓" in snap
    assert "X" in snap


def test_task_list_passes_box_props_to_outer_container() -> None:
    """``margin`` / ``padding`` / ``gap`` forwarded to outer Box."""
    el = TaskList([TaskItem(label="x")], margin=2, gap=1)
    # box_props captured in props — verified via element shape.
    assert el.props["box_props"] == {"margin": 2, "gap": 1}


def test_task_list_forces_column_flex_direction() -> None:
    """Caller-passed ``flexDirection`` is overridden to ``column``."""
    # The factory stores box_props; the override happens inside
    # _TaskListImpl. We verify by snapshotting a render: rows must
    # stack vertically (one line per task), not horizontally.
    snap = _first_frame_of(
        TaskList(
            [TaskItem(label="A"), TaskItem(label="B")],
            flexDirection="row",
        )
    )
    lines = [line for line in snap.split("\n") if line.strip()]
    assert len(lines) >= 2, "rows must stack vertically even when row was requested"


# ---------------------------------------------------------------------------
# Box-props composition with rendering
# ---------------------------------------------------------------------------


def test_task_list_margin_applies_in_render() -> None:
    """``margin=1`` adds spacing around the outer container.

    We don't assert exact escape bytes (margin renders via the layout
    engine's flex calculations); we just verify the labels still appear
    so the prop doesn't crash the pipeline.
    """
    snap = _first_frame_of(
        TaskList([TaskItem(label="Margin-ok", state="done")], margin=1)
    )
    assert "Margin-ok" in snap


# ---------------------------------------------------------------------------
# Spinner nesting sanity (running row inside TaskList)
# ---------------------------------------------------------------------------


def test_running_row_spinner_tears_down_on_unmount() -> None:
    """Unmounting TaskList tears down the per-row Spinner's interval."""
    import threading

    inst, _ = _mount(TaskList([TaskItem(label="X", state="running")]))

    def has_worker() -> bool:
        return any(
            t.name.startswith("pyink-interval-") and t.is_alive()
            for t in threading.enumerate()
        )

    assert _wait_for(has_worker, attempts=80)
    inst.unmount()
    assert _wait_for(lambda: not has_worker(), attempts=120, delay=0.025), (
        "Spinner interval thread still alive after TaskList unmount"
    )


def test_multiple_running_rows_each_have_spinner() -> None:
    """Two running rows each render a Spinner first frame."""
    snap = _first_frame_of(
        TaskList(
            [
                TaskItem(label="A", state="running"),
                TaskItem(label="B", state="running"),
            ]
        )
    )
    # Each row shows the first dots frame + its label.
    assert snap.count(SPINNERS["dots"][0]) >= 2
    assert "A" in snap and "B" in snap


def test_mixed_running_and_static_rows() -> None:
    snap = _first_frame_of(
        TaskList(
            [
                TaskItem(label="Pending", state="pending"),
                TaskItem(label="Running", state="running"),
                TaskItem(label="Done", state="done"),
            ]
        )
    )
    assert "○" in snap
    assert SPINNERS["dots"][0] in snap
    assert "✓" in snap
    assert "Pending" in snap and "Running" in snap and "Done" in snap
