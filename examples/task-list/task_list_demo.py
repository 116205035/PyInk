"""TaskList example — task-state list with per-task spinner (Phase 6 PR1).

Reference: ink-task-list's CLI demo. PyInk ships the canonical
:func:`pyink.externals.TaskList` + :class:`TaskItem` pair; this example
mounts a 5-task pipeline (3 already done, 1 running, 1 pending) and a
background thread advances the pending / running tasks through their
lifecycle every 2 seconds until every row reaches a terminal state.

The demo also wires ``on_complete`` so the moment every task is done the
header subtitle flips to ``"All tasks complete!"`` — a one-line
integration check that the effect-driven completion callback fires.

Run::

    python examples/task-list/task_list_demo.py

Controls:

* Esc / Ctrl+C — quit
"""

from __future__ import annotations

import sys
import threading
import time

from pyink import Box, Text, create_element, render, signal, use_app, use_input
from pyink.core.element import Element
from pyink.core.signal import Signal
from pyink.externals import TaskItem, TaskList
from pyink.render.keys import Key

#: Seconds between background state transitions. Slow enough that the
#: spinner frame animates visibly before the row flips to ``done``.
TICK_SECONDS: float = 2.0


def TaskListDemo() -> Element:
    """Build the demo tree.

    Hooks run inside the Impl closure so they execute during mount.
    """

    def Impl() -> Element:
        app = use_app()

        # The reactive task list. The initial state has three done
        # tasks (initial paint shows the green checkmarks), one running
        # task (animated spinner), and one pending task (dim circle).
        tasks: Signal[list[TaskItem]] = signal(
            [
                TaskItem(label="Read config file", state="done"),
                TaskItem(label="Parse schema", state="done"),
                TaskItem(label="Connect to database", state="done"),
                TaskItem(label="Run migrations", state="running"),
                TaskItem(label="Seed demo data", state="pending"),
            ]
        )
        complete = signal(False)

        def on_key(key: Key) -> None:
            if key.escape:
                app.exit(None)

        use_input(on_key)

        def on_complete(_final: list[TaskItem]) -> None:
            # Fires once from inside ``TaskList``'s effect when every
            # task reaches a terminal state — flipping ``complete``
            # re-paints the header subtitle.
            complete.value = True

        # Background thread walks the remaining tasks forward: the
        # ``running`` row flips to ``done`` and the next ``pending``
        # row flips to ``running``. After the last pending task
        # transitions to running we let the next tick finish it, at
        # which point ``on_complete`` fires from the TaskList effect.
        def advance() -> None:
            while True:
                time.sleep(TICK_SECONDS)
                current = tasks.value
                # Find the running row (if any) and flip to done.
                updated = [
                    TaskItem(
                        label=item.label,
                        state="done" if item.state == "running" else item.state,
                        output=item.output,
                    )
                    for item in current
                ]
                # Promote the first pending row to running.
                for idx, item in enumerate(updated):
                    if item.state == "pending":
                        updated[idx] = TaskItem(
                            label=item.label,
                            state="running",
                            output=item.output,
                        )
                        break
                if updated != current:
                    tasks.value = updated
                else:
                    # No pending / running rows remain — the TaskList
                    # effect fires ``on_complete`` on the next flush,
                    # so the worker can exit.
                    return

        threading.Thread(target=advance, daemon=True).start()

        return Box(
            Text("TaskList demo", bold=True),
            Text(
                lambda: "All tasks complete!"
                if complete.value
                else "Esc / Ctrl+C to quit",
                dimColor=True,
            ),
            TaskList(
                tasks,
                spinner_color="green",
                on_complete=on_complete,
            ),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(TaskListDemo(), columns=48, rows=12)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
