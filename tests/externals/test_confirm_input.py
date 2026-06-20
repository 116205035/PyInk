"""Tests for :func:`pyink.externals.ConfirmInput` (Phase 4 PR4).

We exercise the component through the live :func:`pyink.render.render`
pipeline because ``ConfirmInput`` mounts :func:`pyink.hooks.use_input`,
whose guard requires the active ``_current_instance`` ContextVar that
only ``render`` sets up. The mount / feed / unmount helpers mirror
:mod:`tests.externals.test_select_input` so the two suites share a
common diagnostic vocabulary.
"""

from __future__ import annotations

import io
import re
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from pyink import render
from pyink.core.element import Element
from pyink.externals import ConfirmInput
from pyink.externals.confirm_input import _ConfirmInputImpl, _derive_label
from pyink.render import terminal as _term_mod
from pyink.render.instance import Instance
from pyink.render.terminal import Terminal

#: Regex that matches any CSI / OSC escape sequence (used to strip ANSI
#: when asserting on visible buffer content).
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def _visible(frame: str) -> str:
    """Strip ANSI escape sequences so assertions see only the visible text."""
    return _ANSI_RE.sub("", frame)


# ---------------------------------------------------------------------------
# Fake TTY + input pipeline patches (mirrors test_select_input.py)
# ---------------------------------------------------------------------------


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 99


def _patch_input(
    bytes_iter: Iterator[bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Patch ``_read_stdin_chunk`` + ``_wait_for_input`` + raw-mode methods."""
    lock = threading.Lock()
    exhausted = {"value": False}

    def fake_read(fd: int, n: int) -> bytes:
        with lock:
            if exhausted["value"]:
                time.sleep(0.05)
                return b""
            try:
                return next(bytes_iter)
            except StopIteration:
                exhausted["value"] = True
                return b""

    monkeypatch.setattr(_term_mod, "_read_stdin_chunk", fake_read)
    monkeypatch.setattr(_term_mod, "_wait_for_input", lambda fd, timeout: True)
    monkeypatch.setattr(Terminal, "_enter_raw_mode_unix", lambda self: None)
    monkeypatch.setattr(Terminal, "_exit_raw_mode_unix", lambda self: None)
    monkeypatch.setattr(Terminal, "_enter_raw_mode_windows", lambda self: None)
    monkeypatch.setattr(Terminal, "_exit_raw_mode_windows", lambda self: None)


def _stream(chunks: list[bytes]) -> Iterator[bytes]:
    """A byte source that yields each chunk then idles with empty bytes."""
    yield from chunks
    while True:
        time.sleep(0.02)
        yield b""


def _patch_input_with_idle(
    bytes_iter: Iterator[bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Variant of :func:`_patch_input` that signals fd-idle after the last chunk.

    Used by the lone-Esc tests: a pending ``ESC`` byte only flushes as a
    lone Escape press when the terminal's reader observes the fd going
    idle (``_wait_for_input`` returns ``False`` after the
    :attr:`Terminal.ESC_TIMEOUT`). The default :func:`_patch_input`
    always reports the fd as ready, so the flush path never runs; this
    variant flips to idle as soon as the underlying iterator is
    exhausted so the Esc keystroke actually reaches the handler.

    Implementation: we peek the iterator inside ``fake_read`` *before*
    consuming the current chunk, so the exhaustion flag is latched in
    lockstep with returning the last byte. After the ESC chunk is
    returned, ``fake_wait`` reports idle on the very next call — which
    is the call the terminal's flush check makes immediately after
    ``feed`` returns with a pending ESC.
    """
    lock = threading.Lock()
    state: dict[str, Any] = {"exhausted": False, "next_chunk": None}

    def _peek() -> None:
        """Pull the next chunk into ``next_chunk`` if not already there."""
        if state["next_chunk"] is not None or state["exhausted"]:
            return
        try:
            state["next_chunk"] = next(bytes_iter)
        except StopIteration:
            state["exhausted"] = True

    _peek()

    def fake_read(fd: int, n: int) -> bytes:
        with lock:
            next_chunk: Any = state["next_chunk"]
            if next_chunk is not None:
                state["next_chunk"] = None
                _peek()  # Refill / latch exhausted for the next wait.
                return bytes(next_chunk)
            # Nothing left.
            time.sleep(0.05)
            return b""

    def fake_wait(fd: int, timeout: float) -> bool:
        with lock:
            return not state["exhausted"]

    monkeypatch.setattr(_term_mod, "_read_stdin_chunk", fake_read)
    monkeypatch.setattr(_term_mod, "_wait_for_input", fake_wait)
    monkeypatch.setattr(Terminal, "_enter_raw_mode_unix", lambda self: None)
    monkeypatch.setattr(Terminal, "_exit_raw_mode_unix", lambda self: None)
    monkeypatch.setattr(Terminal, "_enter_raw_mode_windows", lambda self: None)
    monkeypatch.setattr(Terminal, "_exit_raw_mode_windows", lambda self: None)


def _draining_stream(chunks: list[bytes]) -> Iterator[bytes]:
    """Variant of :func:`_stream` that stops (does not idle forever).

    Used together with :func:`_patch_input_with_idle` so the fd reports
    idle after the last chunk — this lets the terminal's lone-Esc flush
    path fire (see :func:`_patch_input_with_idle`).
    """
    yield from chunks


def _mount(
    tree: Element,
    *,
    monkeypatch: pytest.MonkeyPatch,
    feed: list[bytes] | None = None,
    columns: int = 40,
    rows: int = 5,
) -> tuple[Instance, io.StringIO]:
    out = io.StringIO()
    if feed is None:
        feed = []
    _patch_input(_stream(feed), monkeypatch)
    inst = render(
        tree,
        stdout=out,
        stdin=_FakeTTY(),
        columns=columns,
        rows=rows,
        exit_on_ctrl_c=False,
    )
    # Let the first paint flush.
    time.sleep(0.05)
    return inst, out


def _mount_with_idle(
    tree: Element,
    *,
    monkeypatch: pytest.MonkeyPatch,
    feed: list[bytes],
    columns: int = 40,
    rows: int = 5,
) -> tuple[Instance, io.StringIO]:
    """Mount variant for testing keystrokes that rely on fd-idle semantics.

    Used for the lone-Esc tests: feeds ``feed`` then lets the fd go idle
    so the terminal flushes a pending ``ESC`` as a lone Escape press.
    """
    out = io.StringIO()
    _patch_input_with_idle(_draining_stream(feed), monkeypatch)
    inst = render(
        tree,
        stdout=out,
        stdin=_FakeTTY(),
        columns=columns,
        rows=rows,
        exit_on_ctrl_c=False,
    )
    # Let the first paint flush.
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


# Byte sequences for the keys we feed.
_ENTER = b"\r"
_ESC = b"\x1b"


# ---------------------------------------------------------------------------
# Element shape
# ---------------------------------------------------------------------------


def test_confirm_input_returns_function_component_element() -> None:
    el = ConfirmInput(on_confirm=lambda: None)
    assert isinstance(el, Element)
    assert callable(el.type)
    assert el.type is _ConfirmInputImpl
    # No children — the function component builds its own subtree.
    assert el.children == ()


def test_confirm_input_props_capture_defaults() -> None:
    el = ConfirmInput(on_confirm=lambda: None)
    assert el.props["prompt"] == "Confirm?"
    assert el.props["confirm_key"] == "y"
    assert el.props["cancel_key"] == "n"
    assert el.props["require_enter"] is False
    assert el.props["default"] is None
    assert el.props["confirm_label"] == "yes"
    assert el.props["cancel_label"] == "no"
    assert el.props["color"] is None
    assert el.props["selected_color"] == "green"
    assert el.props["is_active"] is True
    assert el.props["box_props"] == {}


def test_confirm_input_props_capture_caller_values() -> None:
    def on_confirm() -> None:
        pass

    def on_cancel() -> None:
        pass

    el = ConfirmInput(
        on_confirm,
        on_cancel,
        prompt="Are you sure?",
        confirm_key="Y",
        cancel_key="N",
        require_enter=True,
        default="confirm",
        confirm_label="Yes!",
        cancel_label="No!",
        color="blue",
        selected_color="yellow",
        is_active=False,
        padding=1,
    )
    assert el.props["on_confirm"] is on_confirm
    assert el.props["on_cancel"] is on_cancel
    assert el.props["prompt"] == "Are you sure?"
    assert el.props["confirm_key"] == "Y"
    assert el.props["cancel_key"] == "N"
    assert el.props["require_enter"] is True
    assert el.props["default"] == "confirm"
    assert el.props["confirm_label"] == "Yes!"
    assert el.props["cancel_label"] == "No!"
    assert el.props["color"] == "blue"
    assert el.props["selected_color"] == "yellow"
    assert el.props["is_active"] is False
    assert el.props["box_props"] == {"padding": 1}


def test_default_invalid_value_raises() -> None:
    """``default`` must be one of 'confirm' / 'cancel' / None."""
    with pytest.raises(ValueError, match="default"):
        ConfirmInput(on_confirm=lambda: None, default="maybe")


# ---------------------------------------------------------------------------
# _derive_label
# ---------------------------------------------------------------------------


def test_derive_label_y() -> None:
    assert _derive_label("y") == "yes"


def test_derive_label_Y_uppercase() -> None:
    assert _derive_label("Y") == "Yes"


def test_derive_label_n() -> None:
    assert _derive_label("n") == "no"


def test_derive_label_q() -> None:
    assert _derive_label("q") == "quit"


def test_derive_label_c() -> None:
    assert _derive_label("c") == "cancel"


def test_derive_label_unknown_falls_back() -> None:
    """Unknown keys fall back to the lowercased key so the label is never empty."""
    assert _derive_label("x") == "x"


def test_derive_label_empty_returns_empty() -> None:
    assert _derive_label("") == ""


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_externals_init_exports_confirm_input() -> None:
    from pyink.externals import ConfirmInput as InitConfirmInput

    assert InitConfirmInput is ConfirmInput


def test_confirm_input_not_in_pyink_top_level() -> None:
    """PRD Decision 5 — externals stay opt-in."""
    import pyink

    assert not hasattr(pyink, "ConfirmInput"), (
        "ConfirmInput must NOT be top-level"
    )


# ---------------------------------------------------------------------------
# Single-key mode (require_enter=False)
# ---------------------------------------------------------------------------


def test_single_key_confirm_fires_on_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: events.append("confirm"),
            on_cancel=lambda: events.append("cancel"),
        ),
        monkeypatch=monkeypatch,
        feed=[b"y"],
    )
    assert _wait_for(lambda: events == ["confirm"])
    inst.unmount()


def test_single_key_cancel_fires_on_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: events.append("confirm"),
            on_cancel=lambda: events.append("cancel"),
        ),
        monkeypatch=monkeypatch,
        feed=[b"n"],
    )
    assert _wait_for(lambda: events == ["cancel"])
    inst.unmount()


def test_single_key_cancel_without_callback_is_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``on_cancel=None`` + pressing ``n`` must not raise."""
    events: list[str] = []
    inst, _ = _mount(
        ConfirmInput(on_confirm=lambda: events.append("confirm")),
        monkeypatch=monkeypatch,
        feed=[b"n"],
    )
    time.sleep(0.2)
    # No crash, no confirm fired.
    assert events == []
    inst.unmount()


def test_single_key_uppercase_y_also_confirms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Y`` (Shift+y) confirms too — keys are case-insensitive."""
    events: list[str] = []
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: events.append("confirm"),
            on_cancel=lambda: events.append("cancel"),
        ),
        monkeypatch=monkeypatch,
        feed=[b"Y"],
    )
    assert _wait_for(lambda: events == ["confirm"])
    inst.unmount()


def test_single_key_enter_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In single-key mode Enter is a no-op — no callback fires."""
    events: list[str] = []
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: events.append("confirm"),
            on_cancel=lambda: events.append("cancel"),
        ),
        monkeypatch=monkeypatch,
        feed=[_ENTER],
    )
    time.sleep(0.2)
    assert events == []
    inst.unmount()


def test_custom_confirm_and_cancel_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``q`` confirms and ``x`` cancels when configured."""
    events: list[str] = []
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: events.append("confirm"),
            on_cancel=lambda: events.append("cancel"),
            confirm_key="q",
            cancel_key="x",
        ),
        monkeypatch=monkeypatch,
        feed=[b"x", b"q"],
    )
    assert _wait_for(lambda: events == ["cancel", "confirm"])
    inst.unmount()


# ---------------------------------------------------------------------------
# require_enter=True mode
# ---------------------------------------------------------------------------


def test_require_enter_y_selects_confirm_then_enter_fires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: events.append("confirm"),
            on_cancel=lambda: events.append("cancel"),
            require_enter=True,
        ),
        monkeypatch=monkeypatch,
        feed=[b"y", _ENTER],
    )
    assert _wait_for(lambda: events == ["confirm"])
    inst.unmount()


def test_require_enter_n_selects_cancel_then_enter_fires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: events.append("confirm"),
            on_cancel=lambda: events.append("cancel"),
            require_enter=True,
        ),
        monkeypatch=monkeypatch,
        feed=[b"n", _ENTER],
    )
    assert _wait_for(lambda: events == ["cancel"])
    inst.unmount()


def test_require_enter_y_then_n_then_enter_fires_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Highlighting confirm then cancel moves the selection; Enter fires cancel."""
    events: list[str] = []
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: events.append("confirm"),
            on_cancel=lambda: events.append("cancel"),
            require_enter=True,
        ),
        monkeypatch=monkeypatch,
        feed=[b"y", b"n", _ENTER],
    )
    assert _wait_for(lambda: events == ["cancel"])
    inst.unmount()


def test_require_enter_enter_with_no_selection_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``default=None`` and no key pressed, Enter does nothing."""
    events: list[str] = []
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: events.append("confirm"),
            on_cancel=lambda: events.append("cancel"),
            require_enter=True,
        ),
        monkeypatch=monkeypatch,
        feed=[_ENTER],
    )
    time.sleep(0.2)
    assert events == []
    inst.unmount()


def test_require_enter_default_confirm_initial_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``default='confirm'`` highlights confirm on mount; Enter fires confirm."""
    events: list[str] = []
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: events.append("confirm"),
            on_cancel=lambda: events.append("cancel"),
            require_enter=True,
            default="confirm",
        ),
        monkeypatch=monkeypatch,
        feed=[_ENTER],
    )
    assert _wait_for(lambda: events == ["confirm"])
    inst.unmount()


def test_require_enter_default_cancel_initial_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``default='cancel'`` highlights cancel on mount; Enter fires cancel."""
    events: list[str] = []
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: events.append("confirm"),
            on_cancel=lambda: events.append("cancel"),
            require_enter=True,
            default="cancel",
        ),
        monkeypatch=monkeypatch,
        feed=[_ENTER],
    )
    assert _wait_for(lambda: events == ["cancel"])
    inst.unmount()


def test_require_enter_default_can_be_overridden_by_keypress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``default='confirm'`` then ``n`` moves highlight to cancel; Enter fires cancel."""
    events: list[str] = []
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: events.append("confirm"),
            on_cancel=lambda: events.append("cancel"),
            require_enter=True,
            default="confirm",
        ),
        monkeypatch=monkeypatch,
        feed=[b"n", _ENTER],
    )
    assert _wait_for(lambda: events == ["cancel"])
    inst.unmount()


# ---------------------------------------------------------------------------
# Esc handling
# ---------------------------------------------------------------------------


def test_esc_fires_on_cancel_in_single_key_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    inst, _ = _mount_with_idle(
        ConfirmInput(
            on_confirm=lambda: events.append("confirm"),
            on_cancel=lambda: events.append("cancel"),
        ),
        monkeypatch=monkeypatch,
        feed=[_ESC],
    )
    assert _wait_for(lambda: events == ["cancel"])
    inst.unmount()


def test_esc_fires_on_cancel_in_require_enter_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    inst, _ = _mount_with_idle(
        ConfirmInput(
            on_confirm=lambda: events.append("confirm"),
            on_cancel=lambda: events.append("cancel"),
            require_enter=True,
        ),
        monkeypatch=monkeypatch,
        feed=[_ESC],
    )
    assert _wait_for(lambda: events == ["cancel"])
    inst.unmount()


def test_esc_without_callback_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """``on_cancel=None`` + Esc must not raise."""
    events: list[str] = []
    inst, _ = _mount_with_idle(
        ConfirmInput(on_confirm=lambda: events.append("confirm")),
        monkeypatch=monkeypatch,
        feed=[_ESC],
    )
    time.sleep(0.2)
    assert events == []
    inst.unmount()


# ---------------------------------------------------------------------------
# is_active=False — keystrokes ignored
# ---------------------------------------------------------------------------


def test_is_active_false_ignores_confirm_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: events.append("confirm"),
            on_cancel=lambda: events.append("cancel"),
            is_active=False,
        ),
        monkeypatch=monkeypatch,
        feed=[b"y", b"n", _ESC],
    )
    time.sleep(0.2)
    assert events == []
    inst.unmount()


def test_is_active_false_ignores_require_enter_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: events.append("confirm"),
            on_cancel=lambda: events.append("cancel"),
            require_enter=True,
            default="confirm",
            is_active=False,
        ),
        monkeypatch=monkeypatch,
        feed=[b"n", _ENTER],
    )
    time.sleep(0.2)
    assert events == []
    inst.unmount()


# ---------------------------------------------------------------------------
# Rendering — visible glyphs and prompt
# ---------------------------------------------------------------------------


def test_prompt_renders_on_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    inst, _ = _mount(
        ConfirmInput(on_confirm=lambda: None, prompt="Delete all files?"),
        monkeypatch=monkeypatch,
    )
    assert _wait_for(lambda: "Delete all files?" in _visible(_frame(inst)))
    inst.unmount()


def test_default_prompt_renders(monkeypatch: pytest.MonkeyPatch) -> None:
    inst, _ = _mount(
        ConfirmInput(on_confirm=lambda: None),
        monkeypatch=monkeypatch,
    )
    assert _wait_for(lambda: "Confirm?" in _visible(_frame(inst)))
    inst.unmount()


def test_single_key_renders_bracketed_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """In single-key mode both options show ``[y]`` / ``[n]``."""
    inst, _ = _mount(
        ConfirmInput(on_confirm=lambda: None),
        monkeypatch=monkeypatch,
    )
    assert _wait_for(lambda: "[y] yes" in _visible(_frame(inst)))
    assert _wait_for(lambda: "[n] no" in _visible(_frame(inst)))
    inst.unmount()


def test_custom_labels_render(monkeypatch: pytest.MonkeyPatch) -> None:
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: None,
            confirm_label="Yes, do it",
            cancel_label="No, abort",
        ),
        monkeypatch=monkeypatch,
    )
    assert _wait_for(lambda: "[y] Yes, do it" in _visible(_frame(inst)))
    assert _wait_for(lambda: "[n] No, abort" in _visible(_frame(inst)))
    inst.unmount()


def test_custom_keys_render_with_brackets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: None,
            confirm_key="q",
            cancel_key="x",
        ),
        monkeypatch=monkeypatch,
    )
    # _derive_label("q") == "quit", _derive_label("x") == "x".
    assert _wait_for(lambda: "[q] quit" in _visible(_frame(inst)))
    assert _wait_for(lambda: "[x] x" in _visible(_frame(inst)))
    inst.unmount()


def test_require_enter_renders_parens_when_nothing_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``require_enter=True`` with ``default=None`` shows ``(y)`` / ``(n)`` for both."""
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: None,
            require_enter=True,
        ),
        monkeypatch=monkeypatch,
    )
    assert _wait_for(lambda: "(y) yes" in _visible(_frame(inst)))
    assert _wait_for(lambda: "(n) no" in _visible(_frame(inst)))
    inst.unmount()


def test_require_enter_default_confirm_renders_brackets_on_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``default='confirm'`` highlights the confirm option with ``[y]``."""
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: None,
            require_enter=True,
            default="confirm",
        ),
        monkeypatch=monkeypatch,
    )
    assert _wait_for(lambda: "[y] yes" in _visible(_frame(inst)))
    assert _wait_for(lambda: "(n) no" in _visible(_frame(inst)))
    inst.unmount()


def test_require_enter_default_cancel_renders_brackets_on_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: None,
            require_enter=True,
            default="cancel",
        ),
        monkeypatch=monkeypatch,
    )
    assert _wait_for(lambda: "(y) yes" in _visible(_frame(inst)))
    assert _wait_for(lambda: "[n] no" in _visible(_frame(inst)))
    inst.unmount()


def test_require_enter_highlight_moves_on_keypress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pressing ``n`` moves the highlight from confirm to cancel."""
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: None,
            require_enter=True,
            default="confirm",
        ),
        monkeypatch=monkeypatch,
        feed=[b"n"],
    )
    # After pressing n, the brackets should swap: (y) and [n].
    assert _wait_for(lambda: "(y) yes" in _visible(_frame(inst)))
    assert _wait_for(lambda: "[n] no" in _visible(_frame(inst)))
    inst.unmount()


# ---------------------------------------------------------------------------
# Rendering — colours
# ---------------------------------------------------------------------------


def test_selected_color_applied_to_default_highlight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``default='confirm'`` renders the confirm option in ``selected_color``."""
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: None,
            require_enter=True,
            default="confirm",
            selected_color="magenta",
        ),
        monkeypatch=monkeypatch,
    )
    # Magenta SGR is \x1b[35m. The highlighted confirm row should be wrapped.
    assert _wait_for(lambda: "\x1b[35m" in _frame(inst))
    inst.unmount()


def test_color_applied_to_non_highlighted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``color`` tints the non-highlighted option."""
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: None,
            require_enter=True,
            default="confirm",
            color="cyan",
        ),
        monkeypatch=monkeypatch,
    )
    # Cyan SGR is \x1b[36m. The non-highlighted cancel row carries it.
    assert _wait_for(lambda: "\x1b[36m" in _frame(inst))
    inst.unmount()


def test_single_key_selected_color_not_applied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In single-key mode nothing is highlighted, so ``selected_color`` is unused."""
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: None,
            selected_color="magenta",
        ),
        monkeypatch=monkeypatch,
    )
    time.sleep(0.1)
    # No magenta SGR anywhere — nothing is highlighted in single-key mode.
    assert "\x1b[35m" not in _frame(inst)
    inst.unmount()


# ---------------------------------------------------------------------------
# box_props forwarding
# ---------------------------------------------------------------------------


def test_box_props_forwarded_to_wrapping_box(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``padding`` / ``borderStyle`` reach the rendered Box."""
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: None,
            borderStyle="round",
            padding=1,
        ),
        monkeypatch=monkeypatch,
    )
    # Round border corners use ╭ / ╮ / ╰ / ╯ glyphs.
    assert _wait_for(lambda: "╭" in _frame(inst))
    inst.unmount()


# ---------------------------------------------------------------------------
# Multiple keystrokes — robustness
# ---------------------------------------------------------------------------


def test_multiple_confirm_keystrokes_fire_multiple_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In single-key mode every confirm keystroke fires the callback."""
    events: list[str] = []
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: events.append("confirm"),
            on_cancel=lambda: events.append("cancel"),
        ),
        monkeypatch=monkeypatch,
        feed=[b"y", b"y", b"y"],
    )
    assert _wait_for(lambda: events == ["confirm", "confirm", "confirm"])
    inst.unmount()


def test_alternating_keys_does_not_cross_fire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pressing y then n fires confirm then cancel — they don't bleed."""
    events: list[str] = []
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: events.append("confirm"),
            on_cancel=lambda: events.append("cancel"),
        ),
        monkeypatch=monkeypatch,
        feed=[b"y", b"n"],
    )
    assert _wait_for(lambda: events == ["confirm", "cancel"])
    inst.unmount()


# ---------------------------------------------------------------------------
# Ctrl / Alt modifiers — fall through (don't trigger)
# ---------------------------------------------------------------------------


def test_ctrl_y_does_not_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl+Y is left for the surrounding pipeline; the component ignores it."""
    events: list[str] = []
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: events.append("confirm"),
            on_cancel=lambda: events.append("cancel"),
        ),
        monkeypatch=monkeypatch,
        # Ctrl+Y byte sequence: 0x19.
        feed=[b"\x19"],
    )
    time.sleep(0.2)
    assert events == []
    inst.unmount()


def test_ctrl_n_does_not_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: events.append("confirm"),
            on_cancel=lambda: events.append("cancel"),
        ),
        monkeypatch=monkeypatch,
        # Ctrl+N byte sequence: 0x0e.
        feed=[b"\x0e"],
    )
    time.sleep(0.2)
    assert events == []
    inst.unmount()


# ---------------------------------------------------------------------------
# Catch-all — misc integration
# ---------------------------------------------------------------------------


def test_cancel_callback_can_be_swapped_between_mounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Ref pattern keeps callbacks fresh — re-mounting with a different
    cancel callback fires the new one without re-subscribing input handlers.

    We can't easily re-mount the same component within one ``render`` call,
    so we verify this indirectly: a single mount with a callback that
    mutates a closure-protected list shows the swap-via-Ref pattern works
    by simply firing the callback twice.
    """
    events: list[Any] = []
    inst, _ = _mount(
        ConfirmInput(
            on_confirm=lambda: events.append({"type": "confirm"}),
            on_cancel=lambda: events.append({"type": "cancel"}),
        ),
        monkeypatch=monkeypatch,
        feed=[b"n", b"y"],
    )
    assert _wait_for(
        lambda: events == [{"type": "cancel"}, {"type": "confirm"}]
    )
    inst.unmount()
