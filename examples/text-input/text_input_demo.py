"""TextInput example — single-line + multi-line + password + placeholder.

Reference: ink's ``ink-text-input`` CLI demo. PyInk ships the real
:func:`pyink.externals.TextInput` external (Phase 4 PR1 + PR2); this
example mounts four inputs side by side inside a
:func:`pyink.use_focus_manager` subtree so ``Tab`` cycles the active
input and keystrokes land in whichever input currently has focus.

The four inputs demonstrate:

* single-line editing (Emacs-style Ctrl+A / Ctrl+E / Ctrl+K / Ctrl+U /
  Ctrl+W + plain typing + Backspace / Delete + arrow navigation).
* multi-line editing (``multiline=True`` — Enter inserts a newline,
  ArrowUp / ArrowDown cross lines).
* password masking (``mask="*"`` — every visible character collapses
  to a star; the buffer is unaffected, only the rendered text is masked).
* placeholder display (``placeholder="..."`` — the dimmed hint shows
  while the buffer is empty).

Each input renders its current value (and ``on_change`` / ``on_submit``
fire counters) in a status line so the demo doubles as a manual
verification harness for the external.

Run::

    python examples/text-input/text_input_demo.py

Controls:

* Type          — edit the focused input.
* Tab           — cycle focus to the next input.
* Shift+Tab     — cycle focus backwards.
* Enter         — submit (single-line inputs) or insert newline
                  (multi-line input).
* Esc / Ctrl+C  — quit.
"""

from __future__ import annotations

import sys

from pyink import (
    Box,
    Text,
    create_element,
    render,
    signal,
    use_app,
    use_focus,
    use_focus_manager,
    use_input,
)
from pyink.core.element import Element
from pyink.externals import TextInput
from pyink.render.keys import Key


def _LabeledInput(
    *,
    input_id: str,
    label: str,
    auto_focus: bool,
    hint: str,
    build_input: Element,
) -> Element:
    """One labelled input row.

    The input element is built by the caller (so this helper stays
    agnostic to ``multiline`` / ``mask`` / ``placeholder``); this
    wrapper adds the focus border, the label, and the dimmed hint.

    The wrapper reads :func:`use_focus` so the active border tracks the
    manager's state without re-mounting.
    """

    def Impl() -> Element:
        handle = use_focus({"id": input_id, "auto_focus": auto_focus})

        def border_style() -> str:
            return "round" if handle.is_focused.value else "single"

        def border_color() -> str | None:
            return "green" if handle.is_focused.value else None

        def label_color() -> str | None:
            return "green" if handle.is_focused.value else None

        return Box(
            Text(label, bold=True, color=label_color),
            build_input,
            Text(hint, dimColor=True),
            flexDirection="column",
            borderStyle=border_style,
            borderColor=border_color,
            paddingX=1,
        )

    return create_element(Impl)


def TextInputDemo() -> Element:
    """Build the demo tree.

    Hooks run inside the Impl closure so they execute during mount.
    """

    def Impl() -> Element:
        app = use_app()
        focus = use_focus_manager()

        # Status signals — counters for each input's on_change calls and
        # captured submit payloads, so the user can see the callbacks
        # firing while they type.
        name_changes = signal(0)
        notes_changes = signal(0)
        password_changes = signal(0)
        search_changes = signal(0)

        name_submit = signal("(not submitted yet)")
        search_submit = signal("(not submitted yet)")

        def on_key(key: Key) -> None:
            if key.escape:
                app.exit(None)
                return
            if key.tab:
                # Tab is owned by the focus manager (PRD Decision 4) —
                # the surrounding app-level handler rotates focus.
                if key.shift:
                    focus.focus_previous()
                else:
                    focus.focus_next()

        use_input(on_key)

        def _name_change(_v: str) -> None:
            name_changes.value = name_changes.value + 1

        def _notes_change(_v: str) -> None:
            notes_changes.value = notes_changes.value + 1

        def _password_change(_v: str) -> None:
            password_changes.value = password_changes.value + 1

        def _search_change(_v: str) -> None:
            search_changes.value = search_changes.value + 1

        def _name_submit(v: str) -> None:
            name_submit.value = f"Submitted: {v!r}"

        def _search_submit(v: str) -> None:
            search_submit.value = f"Submitted: {v!r}"

        name_input = TextInput(
            placeholder="Type your name...",
            on_change=_name_change,
            on_submit=_name_submit,
            cursor_color="green",
        )
        notes_input = TextInput(
            placeholder="Multi-line notes — Enter inserts a newline.",
            multiline=True,
            on_change=_notes_change,
        )
        password_input = TextInput(
            placeholder="Password (masked)",
            mask="*",
            on_change=_password_change,
        )
        search_input = TextInput(
            placeholder="Press Enter to submit...",
            on_change=_search_change,
            on_submit=_search_submit,
        )

        body: Element = focus.wrap(
            Box(
                _LabeledInput(
                    input_id="name",
                    label="Single-line",
                    auto_focus=True,
                    hint="Type, then Enter to submit.",
                    build_input=name_input,
                ),
                _LabeledInput(
                    input_id="notes",
                    label="Multi-line",
                    auto_focus=False,
                    hint="Enter inserts a newline; Up/Down cross lines.",
                    build_input=notes_input,
                ),
                _LabeledInput(
                    input_id="password",
                    label="Password",
                    auto_focus=False,
                    hint='mask="*" — characters render as stars.',
                    build_input=password_input,
                ),
                _LabeledInput(
                    input_id="search",
                    label="Placeholder",
                    auto_focus=False,
                    hint="Type, then Enter to submit.",
                    build_input=search_input,
                ),
                Text(
                    lambda: f"Active: {focus.active_id or '(none)'}",
                    dimColor=True,
                ),
                Text(
                    lambda: (
                        f"on_change calls  name={name_changes.value} "
                        f"notes={notes_changes.value} "
                        f"password={password_changes.value} "
                        f"search={search_changes.value}"
                    ),
                    dimColor=True,
                ),
                Text(lambda: name_submit.value, dimColor=True),
                Text(lambda: search_submit.value, dimColor=True),
                flexDirection="column",
            )
        )

        return Box(
            Text("TextInput demo", bold=True),
            Text(
                "Tab cycles focus — Esc / Ctrl+C to quit.",
                dimColor=True,
            ),
            body,
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(TextInputDemo(), columns=72, rows=24)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
