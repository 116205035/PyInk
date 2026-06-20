"""Tests for callable style props (PRD Decision 13).

Per Decision 13, every Box / Text style prop (``color`` / ``bold`` /
``borderStyle`` / ``borderColor`` / ``backgroundColor`` / …) accepts
``T | Callable[[], T]``. The callable is evaluated during the render
layout pass — i.e. inside the render-loop effect's tracking context —
so any ``signal.value`` reads inside it establish subscriptions.

These tests mount the tree via the live :func:`pyink.render.render`
pipeline (``render_to_string`` is purely synchronous and does not
establish subscriptions), mutate the signal, then inspect stdout to
verify the new frame reflects the new value.
"""

from __future__ import annotations

import io
import time
from typing import Any

from pyink import Box, Text, render
from pyink.core.element import Element, create_element
from pyink.core.signal import Signal, signal
from pyink.render.instance import Instance

ESC = "\x1b"


def _mount(
    build_tree: Any,
    *,
    columns: int = 30,
    rows: int = 5,
) -> tuple[Instance, io.StringIO]:
    out = io.StringIO()
    inst = render(
        build_tree,
        stdout=out,
        stdin=io.StringIO(),
        columns=columns,
        rows=rows,
        exit_on_ctrl_c=False,
    )
    # Initial paint runs synchronously; allow the throttle thread a beat
    # so a subsequent signal write can't race the first frame.
    time.sleep(0.05)
    return inst, out


def _flush_throttle() -> None:
    """Give the FPS throttle thread time to apply a signal-driven paint."""
    time.sleep(0.15)


def _frame(inst: Instance) -> str:
    """Return the current frame string from the Instance.

    We read ``Instance.current_frame`` instead of stdout because the
    diff writer emits cursor-move / clear sequences (``\\x1b[2K`` /
    ``\\x1b[<n>A``) that are not part of the actual rendered content;
    ``current_frame`` is the clean snapshot of just the rendered frame.
    """
    return inst.current_frame


# ---------------------------------------------------------------------------
# Text — colour
# ---------------------------------------------------------------------------


def test_text_color_callable_updates_on_signal_change() -> None:
    color_sig: Signal[str | None] = signal("red")

    def App() -> Element:
        return Text("hi", color=lambda: color_sig.value)

    inst, _out = _mount(App())
    assert f"{ESC}[31mhi{ESC}[0m" in _frame(inst)

    color_sig.value = "green"
    _flush_throttle()
    assert f"{ESC}[32mhi{ESC}[0m" in _frame(inst)
    inst.unmount()


def test_text_callable_returning_none_disables_style() -> None:
    color_sig: Signal[str | None] = signal(None)

    def App() -> Element:
        return Text("hi", color=lambda: color_sig.value)

    inst, _out = _mount(App())
    # No colour applied initially.
    assert _frame(inst).startswith("hi")

    color_sig.value = "blue"
    _flush_throttle()
    assert _frame(inst).startswith(f"{ESC}[34mhi{ESC}[0m")
    inst.unmount()


def test_text_bold_callable_updates() -> None:
    bold_sig: Signal[bool] = signal(False)

    def App() -> Element:
        return Text("X", bold=lambda: bold_sig.value)

    inst, _out = _mount(App())
    assert _frame(inst).startswith("X")

    bold_sig.value = True
    _flush_throttle()
    assert _frame(inst).startswith(f"{ESC}[1mX{ESC}[0m")
    inst.unmount()


def test_text_multiple_callable_props_change_together() -> None:
    color_sig: Signal[str] = signal("red")
    bold_sig: Signal[bool] = signal(False)

    def App() -> Element:
        return Text(
            "X",
            color=lambda: color_sig.value,
            bold=lambda: bold_sig.value,
        )

    inst, _out = _mount(App())
    assert _frame(inst).startswith(f"{ESC}[31mX{ESC}[0m")

    color_sig.value = "green"
    bold_sig.value = True
    _flush_throttle()
    # Order per apply_style: dim, fg, bg, bold, ...
    assert _frame(inst).startswith(f"{ESC}[32m{ESC}[1mX{ESC}[0m")
    inst.unmount()


def test_text_callable_reads_multiple_signals() -> None:
    a: Signal[bool] = signal(False)
    b: Signal[bool] = signal(False)

    def App() -> Element:
        # Both must be True to get bold + underline.
        return Text(
            "X",
            bold=lambda: a.value and b.value,
        )

    inst, _out = _mount(App())
    assert _frame(inst).startswith("X")

    a.value = True
    _flush_throttle()
    # Still false — b is False.
    assert _frame(inst).startswith("X")

    b.value = True
    _flush_throttle()
    assert _frame(inst).startswith(f"{ESC}[1mX{ESC}[0m")
    inst.unmount()


def test_text_background_color_callable_updates() -> None:
    bg_sig: Signal[str] = signal("red")

    def App() -> Element:
        return Text("hi", backgroundColor=lambda: bg_sig.value)

    inst, _out = _mount(App())
    assert _frame(inst).startswith(f"{ESC}[41mhi{ESC}[0m")

    bg_sig.value = "blue"
    _flush_throttle()
    assert _frame(inst).startswith(f"{ESC}[44mhi{ESC}[0m")
    inst.unmount()


# ---------------------------------------------------------------------------
# Box — borderStyle / borderColor / backgroundColor
# ---------------------------------------------------------------------------


def test_box_border_style_callable_updates() -> None:
    style_sig: Signal[str] = signal("single")

    def App() -> Element:
        # Wrap in a Box so the inner Box can fit its content rather than
        # fill the full root width.
        return Box(
            Box(
                Text("hi"),
                borderStyle=lambda: style_sig.value,
                alignSelf="flex-start",
            ),
            flexDirection="column",
        )

    inst, _out = _mount(App())
    initial = _frame(inst)
    # Single border uses ┌ ─ ┐ │ └ ┘.
    assert "┌" in initial and "┐" in initial and "└" in initial and "┘" in initial

    style_sig.value = "round"
    _flush_throttle()
    after = _frame(inst)
    # Round border uses ╭ ╮ ╰ ╯.
    assert "╭" in after and "╮" in after and "╰" in after and "╯" in after
    inst.unmount()


def test_box_border_color_callable_updates() -> None:
    color_sig: Signal[str | None] = signal(None)

    def App() -> Element:
        return Box(
            Box(
                Text("hi"),
                borderStyle="single",
                borderColor=lambda: color_sig.value,
                alignSelf="flex-start",
            ),
            flexDirection="column",
        )

    inst, _out = _mount(App())
    initial = _frame(inst)
    # Plain border chars (no ANSI).
    assert "┌" in initial
    assert ESC not in initial

    color_sig.value = "green"
    _flush_throttle()
    after = _frame(inst)
    # Border now carries green ANSI.
    assert f"{ESC}[32m" in after
    inst.unmount()


def test_box_background_color_callable_updates() -> None:
    bg_sig: Signal[str | None] = signal(None)

    def App() -> Element:
        return Box(
            Box(
                Text("hi"),
                backgroundColor=lambda: bg_sig.value,
                alignSelf="flex-start",
            ),
            flexDirection="column",
        )

    inst, _out = _mount(App())
    initial = _frame(inst)
    assert "hi" in initial
    assert ESC not in initial

    bg_sig.value = "red"
    _flush_throttle()
    after = _frame(inst)
    # Red background fill.
    assert f"{ESC}[41m" in after
    inst.unmount()


def test_box_per_side_border_visibility_callable() -> None:
    show_top_sig: Signal[bool] = signal(True)

    def App() -> Element:
        return Box(
            Box(
                Text("hi"),
                borderStyle="single",
                borderTop=lambda: show_top_sig.value,
                alignSelf="flex-start",
            ),
            flexDirection="column",
        )

    inst, _out = _mount(App())
    initial = _frame(inst)
    # Top border present.
    assert "┌" in initial

    show_top_sig.value = False
    _flush_throttle()
    after = _frame(inst)
    # Top corners / edge removed.
    assert "┌" not in after
    assert "┐" not in after
    inst.unmount()


# ---------------------------------------------------------------------------
# Mixed Text + Box callables
# ---------------------------------------------------------------------------


def test_nested_text_and_box_callables_update_together() -> None:
    color_sig: Signal[str] = signal("red")
    border_sig: Signal[str] = signal("single")

    def App() -> Element:
        return Box(
            Box(
                Text("hi", color=lambda: color_sig.value),
                borderStyle=lambda: border_sig.value,
                alignSelf="flex-start",
            ),
            flexDirection="column",
        )

    inst, _out = _mount(App())
    initial = _frame(inst)
    assert f"{ESC}[31mhi{ESC}[0m" in initial
    assert "┌" in initial

    color_sig.value = "green"
    border_sig.value = "round"
    _flush_throttle()
    after = _frame(inst)
    assert f"{ESC}[32mhi{ESC}[0m" in after
    assert "╭" in after
    inst.unmount()


# ---------------------------------------------------------------------------
# Non-callable props still work (regression guard)
# ---------------------------------------------------------------------------


def test_static_style_props_still_render() -> None:
    """Plain (non-callable) props must continue to render correctly."""

    def App() -> Element:
        return Box(
            Box(
                Text("hi", color="green", bold=True),
                borderStyle="single",
                alignSelf="flex-start",
            ),
            flexDirection="column",
        )

    inst, _out = _mount(App())
    initial = _frame(inst)
    assert f"{ESC}[2m" not in initial  # no dim
    assert f"{ESC}[32m" in initial  # green fg
    assert f"{ESC}[1m" in initial  # bold
    assert "┌" in initial
    inst.unmount()


# ---------------------------------------------------------------------------
# A function-component body using signals with callable props works
# end-to-end (mirrors the select_input example pattern).
# ---------------------------------------------------------------------------


def test_function_component_with_callable_props_subscribes() -> None:
    selected: Signal[int] = signal(0)
    items = ("A", "B", "C")

    def make_item(item: str, idx: int) -> Element:
        # ``idx`` is a function parameter — bound at call time, so each
        # callable reads the right value (unlike a comprehension-bound
        # ``for idx``).
        return Text(
            item,
            color=lambda: "green" if selected.value == idx else None,
            bold=lambda: selected.value == idx,
        )

    def App() -> Element:
        def Impl() -> Element:
            return Box(
                *[make_item(item, idx) for idx, item in enumerate(items)],
                flexDirection="column",
                alignSelf="flex-start",
            )

        return create_element(Impl)

    inst, _out = _mount(App(), columns=20, rows=5)
    initial = _frame(inst)
    # First item selected → green + bold.
    assert f"{ESC}[32m{ESC}[1mA{ESC}[0m" in initial

    selected.value = 1
    _flush_throttle()
    after = _frame(inst)
    # Now B is the highlighted one.
    assert f"{ESC}[32m{ESC}[1mB{ESC}[0m" in after
    inst.unmount()
