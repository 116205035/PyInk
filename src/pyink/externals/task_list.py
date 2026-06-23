"""``TaskList`` — task-state list with per-task spinner (Phase 6 PR1).

Mirrors :mod:`ink-task-list`: a column of tasks where each row carries
an icon that reflects the task's current lifecycle state. PyInk's
flavour keeps the upstream state set verbatim (``pending`` /
``running`` / ``done`` / ``error`` / ``warning``) but collapses the
label / status / output triplet into ``label`` + ``output`` — the
optional ``status`` bracket-text was rarely used upstream and removing
it keeps the dataclass self-explanatory.

Design (per PRD PR1 scope):

* :class:`TaskItem` is a ``@dataclass(frozen=True)`` value object. The
  immutability lets callers treat each task as a stable identity and
  swap state by replacing the entry rather than mutating it — the
  common pattern for a CLI that progresses a task list through stages.
* ``TaskList`` is a factory returning an :class:`Element` whose
  ``type`` is a function component (:func:`_TaskListImpl`). The factory
  itself never runs hooks — only the wrapped function does, when the
  reconciler mounts it. This matches :func:`pyink.externals.Spinner`'s
  contract so ``Box(TaskList(tasks), Text("..."))`` is safe to call
  from outside a render context.
* Three ``tasks`` shapes are accepted (mirroring
  :mod:`pyink.externals.markdown` / :mod:`pyink.externals.diff` /
  :mod:`pyink.externals.streaming_text`):

  * ``list[TaskItem]`` — static. Rendered eagerly at mount time; the
    tree never re-renders on its own.
  * :class:`Signal` ``[list[TaskItem]]`` — reactive on the *row
    content*. Each row's icon + label + output is wrapped in a
    layout-time callable child that re-reads the signal, so state
    transitions (pending → done) repaint on the next frame. New /
    removed tasks still require a remount (the row count is fixed at
    mount time — documented limitation; see ``_TaskListImpl``).
  * ``Callable[[], list[TaskItem]]`` — evaluated lazily at layout
    time, same reactivity story as ``Signal``.

* ``running`` rows mount a real :func:`Spinner` so the icon animates
  independently on its own interval worker. The other states emit
  static unicode glyphs (``○`` / ``✓`` / ``✗`` / ``⚠``) coloured to
  match the upstream palette (gray / green / red / yellow).
* ``on_complete`` fires once when every task reaches a terminal state
  (``done`` / ``error`` / ``warning``). The check runs inside a
  :func:`pyink.core.signal.effect` so a transition into the
  all-terminal configuration re-evaluates immediately; a module-level
  ref guards against duplicate invocation (the effect re-runs on every
  relevant signal write even when the result is unchanged).

PR1 scope: ships ``TaskList`` and ``TaskItem`` only.
ProgressBar / Gradient / Table / BigText land in later PRs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pyink.components.box import Box
from pyink.components.text import Text
from pyink.core.element import Element, create_element
from pyink.core.signal import Signal, effect, ref
from pyink.externals.spinner import Spinner

__all__ = ["TaskItem", "TaskList"]

#: Lifecycle states a :class:`TaskItem` may occupy. Mirrors
#: ``ink-task-list``'s ``State`` union (``pending`` / ``loading`` /
#: ``success`` / ``warning`` / ``error``), renamed to PyInk's verbs:
#: ``loading`` → ``running`` and ``success`` → ``done`` (the latter
#: matches the more common "task completed" phrasing).
TaskState = Literal["pending", "running", "done", "error", "warning"]

#: Terminal states — once a task reaches any of these it no longer
#: participates in the ``running`` / ``pending`` flux. ``on_complete``
#: fires when *every* task in the list is in this set.
_TERMINAL_STATES: frozenset[str] = frozenset({"done", "error", "warning"})


@dataclass(frozen=True)
class TaskItem:
    """One task's visible state.

    The dataclass is frozen so callers can use instances as dict keys /
    set members and rely on identity-stable swaps (replace the whole
    item rather than mutating a field). This mirrors how
    :mod:`ink-task-list` consumers typically model task progress: the
    CLI thread emits a fresh :class:`TaskItem` per state transition.

    Attributes
    ----------
    label:
        Primary text shown on the task row.
    state:
        Current lifecycle state. Drives the icon glyph and colour:
        ``pending`` (gray ``○``) / ``running`` (animated spinner) /
        ``done`` (green ``✓``) / ``error`` (red ``✗``) /
        ``warning`` (yellow ``⚠``).
    output:
        Optional secondary line shown below the label, dimmed. Useful
        for a one-line summary of what the task produced (e.g. ``"3
        files written"``). ``None`` suppresses the line.
    """

    label: str
    state: TaskState = "pending"
    output: str | None = None


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------


def _resolve_source(
    source: list[TaskItem] | Signal[list[TaskItem]] | Callable[[], list[TaskItem]],
) -> list[TaskItem]:
    """Return the current list carried by ``source``.

    Centralises the three-shape dispatch (``list[TaskItem]`` /
    ``Signal[list[TaskItem]]`` / ``Callable[[], list[TaskItem]]``) so
    both the eager mount-time build and the per-row layout-time
    callables share the resolution logic. Mirrors the helpers in
    :mod:`pyink.externals.streaming_text` / :mod:`pyink.externals.diff`.
    """
    if isinstance(source, Signal):
        return source.value
    if callable(source):
        return source()
    return source


def _state_icon(state: TaskState) -> str:
    """Return the unicode glyph for a non-``running`` state.

    ``running`` is handled separately by mounting a real
    :func:`Spinner`; this helper covers the four static states so the
    per-row layout-time callable can resolve the icon cheaply.
    """
    if state == "done":
        return "✓"
    if state == "error":
        return "✗"
    if state == "warning":
        return "⚠"
    # ``pending`` (or any unknown value defensively treated as pending).
    return "○"


def _state_color(state: TaskState) -> str | None:
    """Return the colour spec for a non-``running`` state.

    ``pending`` uses ``dimColor=True`` rather than a colour spec —
    callers apply the dim flag separately. The other states pin their
    palette entry so the icon and label share a hue, matching
    :mod:`ink-task-list`'s visual treatment.
    """
    if state == "done":
        return "green"
    if state == "error":
        return "red"
    if state == "warning":
        return "yellow"
    return None


def _build_static_row(
    source: list[TaskItem] | Signal[list[TaskItem]] | Callable[[], list[TaskItem]],
    index: int,
) -> Element:
    """Build a non-``running`` task row.

    The icon, label, colour, and dim flag are all emitted as
    layout-time callables that re-resolve the task at ``source[index]``
    on every paint. This is what makes a state transition
    (e.g. ``pending`` → ``done``) repaint with the new glyph *and* the
    new colour on the next frame, even though the row's identity was
    fixed at mount time.

    For the static-input case (``tasks`` is a plain ``list[TaskItem]``)
    the callables still work — they read a stable value every time,
    which is a no-op re-render. For a ``Signal`` / ``Callable`` source,
    the callables' read inside the layout pass establishes the
    subscription that drives re-paints.
    """

    def _current() -> TaskItem:
        tasks = _resolve_source(source)
        # Defensive: if the source shrank below ``index`` after mount
        # (caller replaced the list), render an empty placeholder
        # rather than raising — the parent is expected to remount to
        # pick up the new row count.
        if 0 <= index < len(tasks):
            return tasks[index]
        return TaskItem(label="", state="pending")

    def _icon() -> str:
        return _state_icon(_current().state)

    def _label() -> str:
        return _current().label

    def _color() -> str | None:
        return _state_color(_current().state)

    def _dim() -> bool:
        return _current().state == "pending"

    return Box(
        Text(_icon, color=_color, dimColor=_dim),
        Text(" "),
        Text(_label, color=_color, dimColor=_dim),
        flexDirection="row",
    )


def _build_running_row(
    source: list[TaskItem] | Signal[list[TaskItem]] | Callable[[], list[TaskItem]],
    index: int,
    *,
    spinner_type: str,
    spinner_color: str | None,
) -> Element:
    """Build a ``running`` task row.

    Mounts a real :func:`Spinner` so the icon animates independently on
    its own interval worker. The label is a layout-time callable so a
    reactive source that renames the running task repaints the new
    text on the next frame.
    """

    def _label() -> str:
        tasks = _resolve_source(source)
        if 0 <= index < len(tasks):
            return f" {tasks[index].label}"
        return ""

    return Box(
        Spinner(type=spinner_type, color=spinner_color),
        Text(_label),
        flexDirection="row",
    )


def _build_row(
    source: list[TaskItem] | Signal[list[TaskItem]] | Callable[[], list[TaskItem]],
    index: int,
    *,
    spinner_type: str,
    spinner_color: str | None,
) -> list[Element]:
    """Build the element(s) for one task.

    Returns a list so the optional ``output`` line can be appended as
    a second row below the icon + label row. An empty list is never
    returned — every task produces at least its primary row.

    The ``running`` branch is selected at mount time (a Spinner is
    mounted or it isn't — there's no in-place upgrade from a static
    icon to an animated one). All other state transitions are handled
    by the per-row layout-time callables inside ``_build_static_row``.
    """
    snapshot = _resolve_source(source)
    if 0 <= index < len(snapshot) and snapshot[index].state == "running":
        primary = _build_running_row(
            source,
            index,
            spinner_type=spinner_type,
            spinner_color=spinner_color,
        )
    else:
        primary = _build_static_row(source, index)

    rows: list[Element] = [primary]

    snapshot_task = snapshot[index] if 0 <= index < len(snapshot) else None
    if snapshot_task is not None and snapshot_task.output:
        # The output line is a layout-time callable so a reactive
        # ``tasks`` source that updates ``output`` mid-stream still
        # repaints. Two-space indent matches the spinner + space
        # gutter of the primary row so the output visually nestles
        # under the label rather than the icon.
        #
        # Mount-time presence check: if the snapshot task had no
        # output, the row never gets an output leaf, so a transition
        # from "no output" to "has output" requires a remount. The
        # inverse (output cleared) still works — the callable returns
        # the new (empty) value and the row repaints blank.
        def _output() -> str:
            tasks = _resolve_source(source)
            if 0 <= index < len(tasks):
                out = tasks[index].output
                if out:
                    return f"  {out}"
            return ""

        rows.append(Text(_output, dimColor=True))
    return rows


# ---------------------------------------------------------------------------
# Function component body
# ---------------------------------------------------------------------------


def _TaskListImpl(**props: Any) -> Element:
    """Function component body for ``TaskList``.

    Runs inside the reconciler render context, so hooks
    (:func:`pyink.core.signal.effect`) are valid here.

    Reactivity contract:

    * The row *count* is fixed at mount time. The body snapshots the
      ``tasks`` source once to decide how many rows to build and
      whether each row should mount a :func:`Spinner` (``running``)
      or a static icon (everything else). Adding / removing tasks
      therefore requires a remount — call ``Instance.rerender`` from
      the parent if the list identity changes.
    * The row *content* is reactive: icon, label colour, and the
      ``output`` line are wrapped in layout-time callables that
      re-resolve the task from the source, so a state transition
      (e.g. ``pending`` → ``done``) repaints on the next frame.
    * ``on_complete`` runs inside an :func:`effect` that re-evaluates
      on every relevant source write; a ref guard prevents double
      invocation once the list has reached the all-terminal state.
    """
    source: list[TaskItem] | Signal[list[TaskItem]] | Callable[[], list[TaskItem]] = props[
        "source"
    ]
    spinner_type: str = props["spinner_type"]
    spinner_color: str | None = props["spinner_color"]
    on_complete: Callable[[list[TaskItem]], None] | None = props["on_complete"]
    box_props: dict[str, Any] = props["box_props"]

    # Snapshot once to fix the row count and decide spinner vs static
    # icon. Reading a Signal here does *not* subscribe the component
    # body (function-component bodies run outside the render-loop
    # effect's tracking context); the per-row layout-time callables
    # below are what establish the reactive subscriptions.
    tasks_snapshot = _resolve_source(source)

    children: list[Element] = []
    for index in range(len(tasks_snapshot)):
        children.extend(
            _build_row(
                source,
                index,
                spinner_type=spinner_type,
                spinner_color=spinner_color,
            )
        )

    # on_complete: fire once when every task reaches a terminal state.
    # The effect body re-resolves the source so a Signal-driven list
    # re-checks on every write; a ref guard prevents duplicate
    # invocations once we've already fired.
    if on_complete is not None:
        fired = ref(False)

        def _check() -> None:
            current = _resolve_source(source)
            if not current:
                return
            if fired.value:
                return
            if all(t.state in _TERMINAL_STATES for t in current):
                fired.value = True
                on_complete(current)

        effect(_check)

    # The outer Box carries the caller's box_props verbatim. We force
    # flexDirection="column" (overriding any caller value) because the
    # row list is intrinsically vertical — a row-oriented TaskList
    # has no meaningful visual interpretation.
    resolved_props: dict[str, Any] = dict(box_props)
    resolved_props["flexDirection"] = "column"
    return Box(*children, **resolved_props)


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def TaskList(
    tasks: list[TaskItem] | Signal[list[TaskItem]] | Callable[[], list[TaskItem]],
    *,
    spinner_type: str = "dots",
    spinner_color: str | None = None,
    on_complete: Callable[[list[TaskItem]], None] | None = None,
    **box_props: Any,
) -> Element:
    """Render a column of tasks with per-task state icons.

    Parameters
    ----------
    tasks:
        Task list. Three shapes are accepted (see module docstring):

        * ``list[TaskItem]`` — static.
        * :class:`Signal` ``[list[TaskItem]]`` — reactive row content.
        * ``Callable[[], list[TaskItem]]`` — lazily evaluated at layout
          time, same reactivity story as ``Signal``.
    spinner_type:
        Frame sequence name forwarded to :func:`Spinner` for
        ``running`` rows. Defaults to ``"dots"``. Unknown names
        silently fall back to ``"dots"`` (Spinner's own behaviour).
    spinner_color:
        Optional colour spec forwarded to :func:`Spinner`. ``None``
        inherits the terminal default.
    on_complete:
        Callback invoked once when every task reaches a terminal
        state (``done`` / ``error`` / ``warning``). Receives the final
        task list. ``None`` (default) disables the check.

        The callback fires from inside a reactive
        :func:`pyink.core.signal.effect`, which runs on the render
        thread — keep the body cheap (no blocking I/O) to avoid
        stalling the frame loop.
    **box_props:
        Forwarded to the outer ``Box`` container. ``flexDirection`` is
        always overridden to ``"column"`` (the row list is intrinsically
        vertical); pass-through props like ``margin`` / ``padding`` /
        ``gap`` are applied verbatim.

    Returns
    -------
    Element
        An element whose ``type`` is a function component
        (:func:`_TaskListImpl`). The factory itself never runs hooks —
        the wrapped function is invoked by the reconciler on mount,
        which is what makes ``Box(TaskList(tasks), Text("..."))`` safe
        to call from outside a render context.

    Usage
    -----
    ::

        from pyink.externals import TaskItem, TaskList

        tasks = [
            TaskItem(label="Install deps", state="done"),
            TaskItem(label="Run tests", state="running"),
            TaskItem(label="Deploy", state="pending"),
        ]

        Box(
            TaskList(tasks, spinner_color="green",
                     on_complete=lambda ts: print("all done")),
        )
    """
    return create_element(
        _TaskListImpl,
        source=tasks,
        spinner_type=spinner_type,
        spinner_color=spinner_color,
        on_complete=on_complete,
        box_props=box_props,
    )
