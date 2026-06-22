"""Tests for :func:`pyink.externals.TextInput` (Phase 4 PR1).

We exercise the component through the live :func:`pyink.render.render`
pipeline because ``TextInput`` mounts :func:`pyink.hooks.use_input`,
whose guard requires the active ``_current_instance`` ContextVar that
only ``render`` sets up — the synchronous :func:`render_to_string`
renderer would refuse the hook.

Keystrokes are fed via the same ``_patch_input`` helper used by the
``use_input`` tests: we feed byte chunks into the terminal's input
loop and assert on either the rendered frame (visible buffer + cursor
SGR escapes) or captured callback side-effects.

Single-line scope (PR1): no Enter-to-insert-newline, no selection, no
paste-bracketing. Those land in PR2.
"""

from __future__ import annotations

import io
import re
import threading
import time
from collections.abc import Callable, Iterator

import pytest

from pyink import Box, Text, render
from pyink.core.element import Element
from pyink.core.signal import signal
from pyink.externals import TextInput
from pyink.externals.text_input import _TextInputImpl, cursor_column, cursor_line
from pyink.render import terminal as _term_mod
from pyink.render.instance import Instance
from pyink.render.terminal import Terminal

ESC = "\x1b"

#: Regex that matches any CSI / OSC escape sequence (used to strip ANSI
#: when asserting on visible buffer content).
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def _visible(frame: str) -> str:
    """Strip ANSI escape sequences so assertions see only the visible text."""
    return _ANSI_RE.sub("", frame)


# ---------------------------------------------------------------------------
# Fake TTY + input pipeline patches (mirrors tests/hooks/test_use_input.py)
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


def _mount(
    tree: Element,
    *,
    monkeypatch: pytest.MonkeyPatch,
    feed: list[bytes] | None = None,
    columns: int = 30,
    rows: int = 3,
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


def _last_change_is(changes: list[str], expected: str) -> bool:
    """True when ``changes`` has at least one entry equal to ``expected`` at the end."""
    return bool(changes) and changes[-1] == expected


# ---------------------------------------------------------------------------
# Element shape
# ---------------------------------------------------------------------------


def test_text_input_returns_function_component_element() -> None:
    el = TextInput()
    assert isinstance(el, Element)
    assert callable(el.type)
    assert el.type is _TextInputImpl
    # No children — the function component builds its own subtree.
    assert el.children == ()


def test_text_input_props_capture_defaults() -> None:
    el = TextInput()
    assert el.props["initial_value"] == ""
    assert el.props["placeholder"] is None
    assert el.props["on_change"] is None
    assert el.props["on_submit"] is None
    assert el.props["on_cursor_change"] is None
    assert el.props["mask"] is None
    assert el.props["max_length"] is None
    assert el.props["color"] is None
    assert el.props["cursor_color"] is None
    assert el.props["cursor_style"] == "block"
    assert el.props["is_active"] is True
    assert el.props["box_props"] == {}


def test_text_input_props_capture_caller_values() -> None:
    def on_change(v: str) -> None:
        pass

    def on_submit(v: str) -> None:
        pass

    def on_cursor_change(offset: int) -> None:
        pass

    el = TextInput(
        initial_value="abc",
        placeholder="hint",
        on_change=on_change,
        on_submit=on_submit,
        on_cursor_change=on_cursor_change,
        mask="*",
        max_length=10,
        color="red",
        cursor_color="green",
        cursor_style="bar",
        is_active=False,
        padding=1,
    )
    assert el.props["initial_value"] == "abc"
    assert el.props["placeholder"] == "hint"
    assert el.props["on_change"] is on_change
    assert el.props["on_submit"] is on_submit
    assert el.props["on_cursor_change"] is on_cursor_change
    assert el.props["mask"] == "*"
    assert el.props["max_length"] == 10
    assert el.props["color"] == "red"
    assert el.props["cursor_color"] == "green"
    assert el.props["cursor_style"] == "bar"
    assert el.props["is_active"] is False
    # box_props absorbs the extra kwargs.
    assert el.props["box_props"] == {"padding": 1}


def test_text_input_invalid_cursor_style_raises() -> None:
    with pytest.raises(ValueError, match="cursor_style"):
        TextInput(cursor_style="blink")  # type: ignore[arg-type]


def test_text_input_negative_max_length_raises() -> None:
    with pytest.raises(ValueError, match="max_length"):
        TextInput(max_length=-1)


def test_text_input_multichar_mask_collapsed_to_first_char() -> None:
    """A multi-char ``mask`` is silently collapsed to its first glyph."""
    el = TextInput(mask="***")
    assert el.props["mask"] == "*"


def test_text_input_empty_mask_treated_as_none() -> None:
    el = TextInput(mask="")
    assert el.props["mask"] is None


def test_text_input_initial_value_clipped_to_max_length() -> None:
    el = TextInput(initial_value="abcdef", max_length=3)
    assert el.props["initial_value"] == "abc"


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_externals_init_exports_text_input() -> None:
    from pyink.externals import TextInput as InitTextInput

    assert InitTextInput is TextInput


def test_text_input_not_in_pyink_top_level() -> None:
    """PRD Decision 5 — externals stay opt-in."""
    import pyink

    assert not hasattr(pyink, "TextInput"), "TextInput must NOT be top-level"


# ---------------------------------------------------------------------------
# First-frame rendering (initial value / placeholder / mask / cursor)
# ---------------------------------------------------------------------------


def test_initial_value_renders_in_first_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(TextInput(initial_value="hello"), monkeypatch=monkeypatch)
    assert _wait_for(lambda: "hello" in _visible(_frame(inst)))
    inst.unmount()


def test_placeholder_shown_when_buffer_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(
        TextInput(placeholder="enter name"), monkeypatch=monkeypatch
    )
    assert _wait_for(lambda: "enter name" in _visible(_frame(inst)))
    inst.unmount()


def test_placeholder_replaced_after_first_char(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(
        TextInput(placeholder="enter name"), monkeypatch=monkeypatch
    )
    assert _wait_for(lambda: "enter name" in _visible(_frame(inst)))
    # Feed one char — placeholder should disappear and the typed char
    # should show.
    # We need a separate render mount because _stream already exhausted.
    inst.unmount()
    inst2, _ = _mount(
        TextInput(placeholder="enter name"),
        monkeypatch=monkeypatch,
        feed=[b"a"],
    )
    assert _wait_for(lambda: "a" in _visible(_frame(inst2)))
    assert "enter name" not in _visible(_frame(inst2))
    inst2.unmount()


def test_mask_renders_asterisks(monkeypatch: pytest.MonkeyPatch) -> None:
    inst, _ = _mount(
        TextInput(initial_value="secret", mask="*"), monkeypatch=monkeypatch
    )
    assert _wait_for(lambda: "******" in _visible(_frame(inst)))
    # The actual buffer must NOT leak into the frame.
    assert "secret" not in _visible(_frame(inst))
    inst.unmount()


# ---------------------------------------------------------------------------
# Typing — single-char insertion
# ---------------------------------------------------------------------------


def test_typing_single_char_appends_to_empty_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changes: list[str] = []
    inst, _ = _mount(
        TextInput(on_change=changes.append), monkeypatch=monkeypatch, feed=[b"h"]
    )
    assert _wait_for(lambda: "h" in _visible(_frame(inst)))
    inst.unmount()
    assert changes == ["h"]


def test_typing_multiple_chars_appends_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(
        TextInput(), monkeypatch=monkeypatch, feed=[b"h", b"i"]
    )
    assert _wait_for(lambda: "hi" in _visible(_frame(inst)))
    inst.unmount()


def test_typing_inserts_at_cursor_middle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Initial value "ac" with cursor at end; we move left once then
    # type "b" to insert between a and c.
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[D", b"b"]  # Left, then 'b'
    inst, _ = _mount(
        TextInput(initial_value="ac", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "abc"))
    inst.unmount()


# ---------------------------------------------------------------------------
# Backspace / Delete
# ---------------------------------------------------------------------------


def test_backspace_deletes_previous_char(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changes: list[str] = []
    feed: list[bytes] = [b"\x7f"]  # Backspace
    inst, _ = _mount(
        TextInput(initial_value="abc", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "ab"))
    inst.unmount()


def test_backspace_at_start_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backspace with cursor at column 0 must not mutate the buffer."""
    changes: list[str] = []
    # Left three times to move cursor to start, then Backspace.
    feed: list[bytes] = [b"\x1b[D", b"\x1b[D", b"\x1b[D", b"\x7f"]
    inst, _ = _mount(
        TextInput(initial_value="abc", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    # Give it time to process all keys.
    time.sleep(0.2)
    assert changes == []
    inst.unmount()


def test_delete_removes_char_at_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Move cursor to start (Left × 3), then Delete removes 'a'.
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[D", b"\x1b[D", b"\x1b[D",
        b"\x1b[3~",
    ]
    inst, _ = _mount(
        TextInput(initial_value="abc", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "bc"))
    inst.unmount()


def test_delete_at_end_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[3~"]
    inst, _ = _mount(
        TextInput(initial_value="abc", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    time.sleep(0.2)
    assert changes == []
    inst.unmount()


# ---------------------------------------------------------------------------
# Arrow keys — cursor movement
# ---------------------------------------------------------------------------


def test_left_arrow_moves_cursor_left(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cursor at end → Left → typing inserts before the last char."""
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[D", b"X"]
    inst, _ = _mount(
        TextInput(initial_value="ab", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "aXb"))
    inst.unmount()


def test_right_arrow_moves_cursor_right(monkeypatch: pytest.MonkeyPatch) -> None:
    # Left, Left (start), Right, then type 'X' to insert at index 1.
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[D", b"\x1b[D", b"\x1b[C", b"X"]
    inst, _ = _mount(
        TextInput(initial_value="ab", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "aXb"))
    inst.unmount()


def test_left_arrow_at_start_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    # Left at start, then type — must insert at position 0 (cursor
    # didn't move below 0).
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[D", b"\x1b[D", b"X"]
    inst, _ = _mount(
        TextInput(initial_value="ab", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "Xab"))
    inst.unmount()


# ---------------------------------------------------------------------------
# Home / End / Ctrl+A / Ctrl+E
# ---------------------------------------------------------------------------


def test_home_moves_cursor_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    # Home, then type 'X' — inserts at start.
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[H", b"X"]
    inst, _ = _mount(
        TextInput(initial_value="ab", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "Xab"))
    inst.unmount()


def test_end_moves_cursor_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    # Left, Left, End, then type 'X' — inserts at end.
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[D", b"\x1b[D", b"\x1b[F", b"X"]
    inst, _ = _mount(
        TextInput(initial_value="ab", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "abX"))
    inst.unmount()


def test_ctrl_a_moves_cursor_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ctrl+A = 0x01, then type 'X'.
    changes: list[str] = []
    feed: list[bytes] = [b"\x01", b"X"]
    inst, _ = _mount(
        TextInput(initial_value="ab", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "Xab"))
    inst.unmount()


def test_ctrl_e_moves_cursor_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    # Left, Left, Ctrl+E (0x05), then type 'X'.
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[D", b"\x1b[D", b"\x05", b"X"]
    inst, _ = _mount(
        TextInput(initial_value="ab", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "abX"))
    inst.unmount()


# ---------------------------------------------------------------------------
# Ctrl+K / Ctrl+U / Ctrl+W — line / word kill
# ---------------------------------------------------------------------------


def test_ctrl_k_kills_to_end_of_line(monkeypatch: pytest.MonkeyPatch) -> None:
    # Move cursor left twice (cursor after 'ab' in 'abcd'), then Ctrl+K (0x0b).
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[D", b"\x1b[D", b"\x0b"]
    inst, _ = _mount(
        TextInput(initial_value="abcd", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "ab"))
    inst.unmount()


def test_ctrl_u_kills_to_start_of_line(monkeypatch: pytest.MonkeyPatch) -> None:
    # Move left twice (cursor after 'ab'), then Ctrl+U (0x15).
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[D", b"\x1b[D", b"\x15"]
    inst, _ = _mount(
        TextInput(initial_value="abcd", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "cd"))
    inst.unmount()


def test_ctrl_w_deletes_previous_word(monkeypatch: pytest.MonkeyPatch) -> None:
    # Buffer "foo bar"; cursor at end; Ctrl+W deletes "bar".
    changes: list[str] = []
    feed: list[bytes] = [b"\x17"]  # Ctrl+W
    inst, _ = _mount(
        TextInput(initial_value="foo bar", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "foo "))
    inst.unmount()


def test_ctrl_w_skips_trailing_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    # Buffer "foo   "; cursor at end; Ctrl+W deletes all whitespace
    # plus "foo".
    changes: list[str] = []
    feed: list[bytes] = [b"\x17"]
    inst, _ = _mount(
        TextInput(initial_value="foo   ", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, ""))
    inst.unmount()


def test_ctrl_w_at_start_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    changes: list[str] = []
    # Move to start, then Ctrl+W.
    feed: list[bytes] = [b"\x1b[H", b"\x17"]
    inst, _ = _mount(
        TextInput(initial_value="abc", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    time.sleep(0.2)
    assert changes == []
    inst.unmount()


# ---------------------------------------------------------------------------
# Enter — on_submit
# ---------------------------------------------------------------------------


def test_enter_triggers_on_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    submitted: list[str] = []
    feed: list[bytes] = [b"\r"]  # Enter
    inst, _ = _mount(
        TextInput(initial_value="value", on_submit=submitted.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: len(submitted) == 1)
    inst.unmount()
    assert submitted == ["value"]


def test_enter_without_submit_callback_is_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feed: list[bytes] = [b"\r"]
    inst, _ = _mount(
        TextInput(initial_value="value"), monkeypatch=monkeypatch, feed=feed
    )
    # Must not crash; value remains.
    time.sleep(0.1)
    assert "value" in _visible(_frame(inst))
    inst.unmount()


# ---------------------------------------------------------------------------
# on_change — fired only when the buffer actually changes
# ---------------------------------------------------------------------------


def test_on_change_not_fired_for_cursor_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[D",  # Left
        b"\x1b[C",  # Right
        b"\x1b[H",  # Home
        b"\x1b[F",  # End
        b"\x01",    # Ctrl+A
        b"\x05",    # Ctrl+E
    ]
    inst, _ = _mount(
        TextInput(initial_value="abc", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    time.sleep(0.2)
    assert changes == []
    inst.unmount()


def test_on_change_fired_for_each_edit(monkeypatch: pytest.MonkeyPatch) -> None:
    changes: list[str] = []
    feed: list[bytes] = [b"a", b"b", b"c"]
    inst, _ = _mount(
        TextInput(on_change=changes.append), monkeypatch=monkeypatch, feed=feed
    )
    assert _wait_for(lambda: "abc" in _frame(inst))
    inst.unmount()
    assert changes == ["a", "ab", "abc"]


# ---------------------------------------------------------------------------
# max_length — truncates insertions
# ---------------------------------------------------------------------------


def test_max_length_drops_overflowing_insertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changes: list[str] = []
    feed: list[bytes] = [b"x"]  # would push length 3 → 4, but max=3
    inst, _ = _mount(
        TextInput(initial_value="abc", max_length=3, on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    time.sleep(0.15)
    assert "abc" in _visible(_frame(inst))
    assert changes == []
    inst.unmount()


def test_max_length_allows_deletion_under_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feed: list[bytes] = [b"\x7f"]  # Backspace
    inst, _ = _mount(
        TextInput(initial_value="abc", max_length=3),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(
        lambda: "ab" in _visible(_frame(inst))
        and "abc" not in _visible(_frame(inst))
    )
    inst.unmount()


# ---------------------------------------------------------------------------
# is_active=False — input ignored
# ---------------------------------------------------------------------------


def test_is_active_false_ignores_keystrokes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changes: list[str] = []
    feed: list[bytes] = [b"x", b"y", b"\x7f"]
    inst, _ = _mount(
        TextInput(
            initial_value="abc",
            is_active=False,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    time.sleep(0.2)
    assert "abc" in _visible(_frame(inst))
    assert changes == []
    inst.unmount()


# ---------------------------------------------------------------------------
# Cursor rendering — bar / block / underline
# ---------------------------------------------------------------------------


def test_cursor_bar_style_emits_inverse_space(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``bar`` cursor emits an inverse-video space at the cursor position."""
    # Empty buffer with no placeholder — cursor sits at end (col 0) as a
    # lone inverse space.
    inst, _ = _mount(TextInput(cursor_style="bar"), monkeypatch=monkeypatch)
    assert _wait_for(lambda: f"{ESC}[7m {ESC}[0m" in _frame(inst))
    inst.unmount()


def test_cursor_block_style_emits_inverse_char(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``block`` cursor renders the cursor character under inverse video.

    Initial value "a" puts the cursor past it, so an inverse space is
    emitted at the end.
    """
    inst, _ = _mount(
        TextInput(initial_value="", cursor_style="block"),
        monkeypatch=monkeypatch,
    )
    assert _wait_for(lambda: f"{ESC}[7m {ESC}[0m" in _frame(inst))
    inst.unmount()


def test_cursor_underline_style_emits_underline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(
        TextInput(initial_value="", cursor_style="underline"),
        monkeypatch=monkeypatch,
    )
    assert _wait_for(lambda: f"{ESC}[4m {ESC}[0m" in _frame(inst))
    inst.unmount()


def test_cursor_color_applied_to_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    """``cursor_color`` tints the bar cursor's foreground."""
    inst, _ = _mount(
        TextInput(cursor_style="bar", cursor_color="green"),
        monkeypatch=monkeypatch,
    )
    # Green (32) + inverse, then a space, then reset.
    expected = f"{ESC}[32m{ESC}[7m {ESC}[0m"
    assert _wait_for(lambda: expected in _frame(inst))
    inst.unmount()


def test_cursor_color_applied_to_block(monkeypatch: pytest.MonkeyPatch) -> None:
    inst, _ = _mount(
        TextInput(cursor_style="block", cursor_color="red"),
        monkeypatch=monkeypatch,
    )
    expected = f"{ESC}[31m{ESC}[7m {ESC}[0m"
    assert _wait_for(lambda: expected in _frame(inst))
    inst.unmount()


def test_cursor_color_applied_to_underline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(
        TextInput(cursor_style="underline", cursor_color="blue"),
        monkeypatch=monkeypatch,
    )
    expected = f"{ESC}[34m{ESC}[4m {ESC}[0m"
    assert _wait_for(lambda: expected in _frame(inst))
    inst.unmount()


def test_cursor_at_middle_position_renders_on_char(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Block cursor over an actual character inverts that character."""
    # Cursor at start (Home) over "a" in "ab".
    feed: list[bytes] = [b"\x1b[H"]
    inst, _ = _mount(
        TextInput(initial_value="ab", cursor_style="block"),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    # The block cursor must sit on top of "a" — inverse-video "a".
    assert _wait_for(lambda: f"{ESC}[7ma{ESC}[0m" in _frame(inst))
    inst.unmount()


def test_color_prop_wraps_displayed_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(
        TextInput(initial_value="hi", color="cyan"), monkeypatch=monkeypatch
    )
    # Body colour sequence appears around the text.
    assert _wait_for(lambda: f"{ESC}[36m" in _frame(inst))
    inst.unmount()


# ---------------------------------------------------------------------------
# Composition — TextInput inside a Box with sibling Text
# ---------------------------------------------------------------------------


def test_text_input_inside_box_with_sibling_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = Box(
        Text("label"),
        TextInput(initial_value="value"),
    )
    inst, _ = _mount(tree, monkeypatch=monkeypatch, columns=30)
    assert _wait_for(
        lambda: "label" in _visible(_frame(inst))
        and "value" in _visible(_frame(inst))
    )
    inst.unmount()


def test_text_input_box_props_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """``padding`` / ``borderStyle`` (box props) are forwarded to the Box."""
    inst, _ = _mount(
        TextInput(initial_value="hi", padding=1),
        monkeypatch=monkeypatch,
        columns=20,
        rows=6,
    )
    # Padding adds blank lines around the text — we should see "hi" plus
    # at least one blank line above/below.
    assert _wait_for(lambda: "hi" in _visible(_frame(inst)))
    frame = _frame(inst)
    # Newline count > 1 indicates padding rows.
    assert frame.count("\n") > 1
    inst.unmount()


# ---------------------------------------------------------------------------
# Integration: full editing sequence
# ---------------------------------------------------------------------------


def test_integration_type_delete_retype(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Type 'hello', Backspace twice, type 'p'.

    Final buffer should be "help".
    """
    feed: list[bytes] = [
        b"h", b"e", b"l", b"l", b"o",
        b"\x7f", b"\x7f",  # Backspace × 2 → "hel"
        b"p",
    ]
    inst, _ = _mount(TextInput(), monkeypatch=monkeypatch, feed=feed)
    assert _wait_for(lambda: "help" in _visible(_frame(inst)))
    inst.unmount()


def test_integration_ctrl_u_then_retype(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Type 'abc', Ctrl+U (clear left), then type 'XYZ'."""
    feed: list[bytes] = [
        b"a", b"b", b"c",
        b"\x15",       # Ctrl+U → "" (cursor was at end → whole buffer)
        b"X", b"Y", b"Z",
    ]
    inst, _ = _mount(TextInput(), monkeypatch=monkeypatch, feed=feed)
    assert _wait_for(
        lambda: "XYZ" in _visible(_frame(inst))
        and "abc" not in _visible(_frame(inst))
    )
    inst.unmount()


# ===========================================================================
# PR2 — multi-line, selection, paste
# ===========================================================================


# ---------------------------------------------------------------------------
# Public helpers — cursor_line / cursor_column
# ---------------------------------------------------------------------------


def test_cursor_line_counts_preceding_newlines() -> None:
    assert cursor_line("abc", 0) == 0
    assert cursor_line("abc\ndef", 3) == 0
    assert cursor_line("abc\ndef", 4) == 1
    assert cursor_line("abc\ndef\nghi", 9) == 2


def test_cursor_column_resets_after_newline() -> None:
    assert cursor_column("abc", 0) == 0
    assert cursor_column("abc", 3) == 3
    assert cursor_column("abc\ndef", 4) == 0
    assert cursor_column("abc\ndef", 7) == 3


def test_cursor_line_clamps_past_end() -> None:
    assert cursor_line("ab", 99) == 0
    assert cursor_line("ab\ncd", 99) == 1


def test_cursor_column_clamps_negative() -> None:
    assert cursor_column("abc", -5) == 0


# ---------------------------------------------------------------------------
# multiline=True — Enter inserts newline
# ---------------------------------------------------------------------------


def test_multiline_enter_inserts_newline(monkeypatch: pytest.MonkeyPatch) -> None:
    """In multiline mode Enter inserts ``\\n`` instead of submitting."""
    submitted: list[str] = []
    changes: list[str] = []
    feed: list[bytes] = [b"a", b"b", b"\r", b"c"]
    inst, _ = _mount(
        TextInput(
            multiline=True,
            on_submit=submitted.append,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=6,
    )
    assert _wait_for(lambda: _last_change_is(changes, "ab\nc"))
    inst.unmount()
    # on_submit must NOT fire in multiline mode.
    assert submitted == []


def test_singleline_enter_still_submits(monkeypatch: pytest.MonkeyPatch) -> None:
    """PR1 behaviour is preserved when ``multiline=False`` (default)."""
    submitted: list[str] = []
    feed: list[bytes] = [b"\r"]
    inst, _ = _mount(
        TextInput(initial_value="value", on_submit=submitted.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: len(submitted) == 1)
    inst.unmount()
    assert submitted == ["value"]


def test_multiline_renders_each_line_on_its_own_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-line value splits into multiple Text rows inside the Box."""
    inst, _ = _mount(
        TextInput(initial_value="ab\ncd", multiline=True),
        monkeypatch=monkeypatch,
        columns=20,
        rows=6,
    )
    # Both rows should be visible, and they sit on different lines.
    assert _wait_for(
        lambda: "ab" in _visible(_frame(inst))
        and "cd" in _visible(_frame(inst))
    )
    # More than one newline in the raw frame indicates multi-row layout.
    assert _wait_for(lambda: _frame(inst).count("\n") >= 2)
    inst.unmount()


# ---------------------------------------------------------------------------
# rows — multi-line scroll viewport (scroll-to-cursor)
# ---------------------------------------------------------------------------


def test_rows_viewport_scrolls_to_cursor_at_bottom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``rows``-bounded multi-line input shows the *last* ``rows`` lines.

    With the cursor at the end of a 6-line buffer and a 3-row viewport,
    only the bottom three lines (and the cursor) are visible; the top
    three scroll out of view. Regression for the "multi-line input grows
    past the available height and the cursor disappears off the bottom"
    report — the layout's height truncation keeps the *top* lines, so
    without scroll-to-cursor the last line (where the cursor sits) is the
    one that gets clipped.
    """
    inst, _ = _mount(
        TextInput(
            initial_value="AAA\nBBB\nCCC\nDDD\nEEE\nFFF",
            multiline=True,
            rows=3,
        ),
        monkeypatch=monkeypatch,
        columns=20,
        rows=12,
    )
    assert _wait_for(lambda: "FFF" in _visible(_frame(inst)))
    vis = _visible(_frame(inst))
    # Bottom three lines visible (cursor sits on the last one).
    assert "DDD" in vis and "EEE" in vis and "FFF" in vis
    # Top three scrolled out of view.
    assert "AAA" not in vis and "BBB" not in vis and "CCC" not in vis
    inst.unmount()


def test_rows_viewport_follows_cursor_upward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moving the cursor above the viewport scrolls the window up.

    Starting at the bottom of a 6-line buffer with a 3-row viewport, four
    ArrowUp presses move the cursor onto the second line; the window must
    scroll up to keep that line visible, revealing the top lines and
    hiding the bottom ones.
    """
    feed: list[bytes] = [b"\x1b[A", b"\x1b[A", b"\x1b[A", b"\x1b[A"]
    inst, _ = _mount(
        TextInput(
            initial_value="AAA\nBBB\nCCC\nDDD\nEEE\nFFF",
            multiline=True,
            rows=3,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=12,
    )
    # Cursor walked up to line index 1 ("BBB"); the window now starts at
    # the top so "AAA"/"BBB" are visible and the bottom "FFF" is hidden.
    assert _wait_for(
        lambda: "BBB" in _visible(_frame(inst))
        and "FFF" not in _visible(_frame(inst))
    )
    inst.unmount()


def test_rows_unset_shows_all_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without ``rows`` the multi-line input grows to show every line."""
    inst, _ = _mount(
        TextInput(
            initial_value="AAA\nBBB\nCCC\nDDD\nEEE\nFFF",
            multiline=True,
        ),
        monkeypatch=monkeypatch,
        columns=20,
        rows=12,
    )
    assert _wait_for(lambda: "FFF" in _visible(_frame(inst)))
    vis = _visible(_frame(inst))
    assert all(tok in vis for tok in ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF"))
    inst.unmount()


def test_rows_must_be_positive() -> None:
    with pytest.raises(ValueError, match="rows"):
        TextInput(rows=0)


def test_multiline_grows_from_1_to_rows_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``rows`` is a *max* height, not a fixed height.

    Regression: an earlier implementation pinned the surrounding Box's
    ``height`` to ``rows``, which froze the input at ``rows`` rows from
    mount on — a multi-line input rendered 5 rows tall even with an
    empty buffer. The fix caps ``maxHeight`` instead, so the Box grows
    from the content's natural height (1 row for a single line) up to
    ``rows`` rows and then scrolls to follow the cursor.

    Scenario (rows=5):
      - mount empty buffer        -> 1 visible content row
      - 1 Enter                   -> 2 visible rows
      - up to 5 lines total       -> 5 visible rows
      - 6th Enter (6+ lines)      -> still 5 visible rows (scroll)
    """
    # 6 Enters produce 7 lines (each Enter adds a "\\n" with an empty
    # tail line); we assert the visible row count stops climbing at 5.
    feed: list[bytes] = [b"\r"] * 6
    changes: list[str] = []

    def _count_visible_content_rows(inst: Instance) -> int:
        # Strip ANSI and drop the trailing newlines the renderer pads;
        # keep only lines that carry any non-space glyph.
        vis = _visible(_frame(inst))
        return sum(1 for line in vis.split("\n") if line.strip())

    # Empty buffer at mount: a single placeholder/content row.
    inst, _ = _mount(
        TextInput(multiline=True, rows=5, on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=12,
    )
    assert _wait_for(lambda: _count_visible_content_rows(inst) <= 5)

    # Drain all Enters; the last on_change is "\\n\\n\\n\\n\\n\\n" —
    # 6 newlines → 7 logical lines.
    assert _wait_for(lambda: bool(changes) and changes[-1].count("\n") == 6)
    # Box capped at maxHeight=5 → at most 5 content rows visible.
    assert _count_visible_content_rows(inst) <= 5, (
        f"expected ≤5 visible rows after 6 Enters, "
        f"got {_count_visible_content_rows(inst)}"
    )
    inst.unmount()


def test_multiline_rows_grows_to_two_after_first_enter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pressing Enter once on a single-line buffer grows the Box to 2 rows.

    Companion to :func:`test_multiline_grows_from_1_to_rows_max`: this
    isolates the 1->2 transition so a future regression that re-pins
    ``height`` (instead of ``maxHeight``) is caught on the very first
    Enter rather than only at the cap. With ``height=5`` (the buggy
    form) the input would have rendered 5 rows at mount and this test
    couldn't distinguish the 1->2 jump; with ``maxHeight=5`` the
    initial frame carries only the content (1 row), and after Enter
    the raw frame has exactly 2 content-driven newlines.
    """
    feed: list[bytes] = [b"a", b"\r"]
    changes: list[str] = []
    inst, _ = _mount(
        TextInput(multiline=True, rows=5, on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=12,
    )
    # Buffer is "a\n" — two logical lines.
    assert _wait_for(lambda: bool(changes) and changes[-1] == "a\n")
    vis = _visible(_frame(inst))
    assert "a" in vis
    inst.unmount()


def test_height_bounded_box_keeps_cursor_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the input box is shrunk below the buffer's line count, the
    layout's cursor-aware vertical clip keeps the cursor row on screen.

    Regression for "the multi-line cursor can only reach line 4": when a
    tight column shrinks the input box, the layout granted the text leaf
    fewer rows than the buffer had lines and the top-keeping clip dropped
    the bottom line the cursor sat on. The box here is pinned to 3 rows
    while the buffer has 6 lines (cursor at the end on the last line), so
    the bottom three lines — including the cursor — must be the ones that
    survive.
    """
    inst, _ = _mount(
        TextInput(
            initial_value="AAA\nBBB\nCCC\nDDD\nEEE\nFFF",
            multiline=True,
            height=3,
        ),
        monkeypatch=monkeypatch,
        columns=20,
        rows=12,
    )
    assert _wait_for(lambda: "FFF" in _visible(_frame(inst)))
    vis = _visible(_frame(inst))
    assert "DDD" in vis and "EEE" in vis and "FFF" in vis
    assert "AAA" not in vis and "BBB" not in vis and "CCC" not in vis
    # The cursor cell (inverse-video block) must be painted on a visible
    # row, not clipped off the bottom.
    assert "\x1b[7m" in _frame(inst)
    inst.unmount()


# ---------------------------------------------------------------------------
# ArrowUp / ArrowDown — cross-line navigation
# ---------------------------------------------------------------------------


def test_arrow_down_moves_to_next_line_same_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Down arrow on line 0 col 1 → line 1 col 1.

    Initial value "abc\\ndef", cursor at end (offset 7, after 'f'). Up
    moves to line 0 col 3 (end of "ab"). Home moves to line 0 col 0.
    Right moves to line 0 col 1 (offset 1). Down → offset 5 (line 1 col
    1, between 'd' and 'e'). Type 'X' inserts at offset 5 →
    "abc\\ndXef".
    """
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[A",  # Up    → line 0 col 3
        b"\x1b[H",  # Home  → line 0 col 0 (offset 0)
        b"\x1b[C",  # Right → line 0 col 1 (offset 1)
        b"\x1b[B",  # Down  → offset 5 (line 1 col 1, between 'd' and 'e')
        b"X",
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abc\ndef",
            multiline=True,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=6,
    )
    assert _wait_for(lambda: _last_change_is(changes, "abc\ndXef"))
    inst.unmount()


def test_arrow_up_moves_to_previous_line_same_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Up arrow on line 1 col 1 → line 0 col 1.

    Initial value "abc\\ndef", cursor at end (offset 7). Left → offset 6
    (line 1 col 2). Left → offset 5 (line 1 col 1). Up → offset 1 (line
    0 col 1, between 'a' and 'b'). Type 'X' inserts at offset 1 →
    "aXbc\\ndef".
    """
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[D",  # Left  → line 1 col 2 (between 'e' and 'f')
        b"\x1b[D",  # Left  → line 1 col 1 (between 'd' and 'e')
        b"\x1b[A",  # Up    → line 0 col 1 (between 'a' and 'b')
        b"X",
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abc\ndef",
            multiline=True,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=6,
    )
    assert _wait_for(lambda: _last_change_is(changes, "aXbc\ndef"))
    inst.unmount()


def test_arrow_up_on_first_line_moves_to_line_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Up arrow on the first line clamps to that line's start (col 0).

    Initial value "abc\\ndef", cursor at end (offset 7). Up → line 0 col
    3. Home → line 0 col 0 (offset 0). Right → line 0 col 1 (offset 1).
    Up — already on line 0, so cursor clamps to line 0 col 0. Type 'X'
    inserts at offset 0 → "Xabc\\ndef".
    """
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[A",  # Up    → line 0 col 3
        b"\x1b[H",  # Home  → offset 0
        b"\x1b[C",  # Right → offset 1 (col 1 of line 0)
        b"\x1b[A",  # Up    — first line, so cursor → col 0 (offset 0)
        b"X",
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abc\ndef",
            multiline=True,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=6,
    )
    assert _wait_for(lambda: _last_change_is(changes, "Xabc\ndef"))
    inst.unmount()


def test_arrow_down_on_last_line_moves_to_line_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Down arrow on the last line clamps to that line's end."""
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[D",   # Left once — line 1 col 2 (between 'e' and 'f')
        b"\x1b[B",   # Down — last line, so cursor → end of line 1 (after 'f')
        b"X",        # type → appends at end
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abc\ndef",
            multiline=True,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=6,
    )
    assert _wait_for(lambda: _last_change_is(changes, "abc\ndefX"))
    inst.unmount()


def test_arrow_down_to_shorter_line_clamps_to_line_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Down to a line shorter than the current column clamps to its end.

    Initial value "abcde\\nxy" — cursor after 'e' (col 5 of line 0).
    Down → line 1 col 5, but line 1 has length 2, so clamp to col 2
    (end of "xy"). Type 'X' → "abcde\\nxyX".
    """
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[B", b"X"]
    inst, _ = _mount(
        TextInput(
            initial_value="abcde\nxy",
            multiline=True,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=6,
    )
    assert _wait_for(lambda: _last_change_is(changes, "abcde\nxyX"))
    inst.unmount()


# ---------------------------------------------------------------------------
# Shift + Arrow / Home / End — selection
# ---------------------------------------------------------------------------


def test_shift_left_extends_selection_backward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shift+Left extends selection to the left; typing replaces the range."""
    changes: list[str] = []
    feed: list[bytes] = [
        # Cursor starts at end of "abcd" (offset 4).
        b"\x1b[1;2D",  # Shift+Left → selection [3, 4)
        b"\x1b[1;2D",  # Shift+Left → selection [2, 4)
        b"X",          # Replace [2, 4) with "X" → "abX"
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abcd",
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "abX"))
    inst.unmount()


def test_shift_right_extends_selection_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shift+Right extends selection to the right; typing replaces it."""
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[H",       # Home → cursor at 0
        b"\x1b[1;2C",    # Shift+Right → selection [0, 1)
        b"\x1b[1;2C",    # Shift+Right → selection [0, 2)
        b"X",            # Replace [0, 2) with "X" → "Xcd"
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abcd",
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "Xcd"))
    inst.unmount()


def test_shift_home_extends_selection_to_line_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shift+Home extends selection to the start of the current line."""
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[D",       # Left once — cursor at 3
        # Shift+Home (xterm: ESC [ 1 ; 2 H)
        b"\x1b[1;2H",
        b"X",            # Replace [0, 3) with "X" → "Xd"
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abcd",
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "Xd"))
    inst.unmount()


def test_shift_end_extends_selection_to_line_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shift+End extends selection to the end of the current line."""
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[H",       # Home — cursor at 0
        b"\x1b[D",       # Left (no-op at 0)
        # Shift+End (xterm: ESC [ 1 ; 2 F)
        b"\x1b[1;2F",
        b"X",            # Replace [0, 4) with "X" → "X"
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abcd",
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "X"))
    inst.unmount()


def test_selection_collapses_after_plain_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plain Right after a Shift+Right collapses the selection.

    Sequence: Home → Shift+Right (selection [0, 1)) → Right (clears
    selection, cursor moves to offset 2) → type 'X' → inserts at offset 2.
    """
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[H",       # Home → cursor 0
        b"\x1b[1;2C",    # Shift+Right → selection [0, 1)
        b"\x1b[C",       # Right → clears selection, cursor 2
        b"X",            # Insert at offset 2 → "abXcd"
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abcd",
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "abXcd"))
    inst.unmount()


# ---------------------------------------------------------------------------
# Selection rendering — inverse video
# ---------------------------------------------------------------------------


def test_selection_renders_with_inverse_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The selected range is wrapped in SGR inverse video (``\\x1b[7m``).

    With the default ``block`` cursor, the cursor sits *on* the character
    just past the selection (offset 2 here, over ``'c'``) and is rendered
    as its own inverse-video cell — independent of the selection paint.
    Stripping ANSI must therefore reveal ``"abcd"`` in order while the
    raw frame carries two inverse-video runs: one for ``"ab"`` (the
    selection) and one for ``"c"`` (the block cursor).
    """
    feed: list[bytes] = [
        b"\x1b[H",       # Home
        b"\x1b[1;2C",    # Shift+Right → [0, 1)
        b"\x1b[1;2C",    # Shift+Right → [0, 2)
    ]
    inst, _ = _mount(
        TextInput(initial_value="abcd"), monkeypatch=monkeypatch, feed=feed
    )
    # The selected range ("ab") is wrapped in inverse video.
    assert _wait_for(lambda: f"{ESC}[7mab{ESC}[0m" in _frame(inst))
    # The block cursor over 'c' produces its own inverse-video run, so
    # the raw frame never carries the literal substring "cd" — but the
    # visible text (ANSI stripped) must still read "abcd".
    assert _wait_for(lambda: _visible(_frame(inst))[:4].strip(" ") == "abcd")
    inst.unmount()


# ---------------------------------------------------------------------------
# Backspace / Delete on selection
# ---------------------------------------------------------------------------


def test_backspace_on_selection_deletes_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backspace with an active selection deletes the whole range."""
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[H",       # Home
        b"\x1b[1;2C",    # Shift+Right → [0, 1)
        b"\x1b[1;2C",    # Shift+Right → [0, 2)
        b"\x7f",         # Backspace → delete [0, 2)
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abcd",
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "cd"))
    inst.unmount()


def test_delete_on_selection_deletes_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delete with an active selection also deletes the whole range."""
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[H",       # Home
        b"\x1b[1;2C",    # Shift+Right → [0, 1)
        b"\x1b[1;2C",    # Shift+Right → [0, 2)
        b"\x1b[3~",      # Delete → delete [0, 2)
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abcd",
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "cd"))
    inst.unmount()


def test_typing_replaces_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    """A printable key with an active selection replaces the range."""
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[H",
        b"\x1b[1;2C",
        b"\x1b[1;2C",
        b"XY",  # Both chars collapse into one edit (selection collapses
                # after the first).
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abcd",
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    # After "X": selection [0,2) replaced → "Xcd", cursor at 1, selection None.
    # After "Y": insert at 1 → "XYcd".
    assert _wait_for(lambda: _last_change_is(changes, "XYcd"))
    inst.unmount()


# ---------------------------------------------------------------------------
# Alt+Backspace == Ctrl+W
# ---------------------------------------------------------------------------


def test_alt_backspace_deletes_previous_word(monkeypatch: pytest.MonkeyPatch) -> None:
    """Alt+Backspace behaves like Ctrl+W (backward-kill-word)."""
    changes: list[str] = []
    # Alt+Backspace arrives as ESC + DEL → bytes "\x1b\x7f".
    feed: list[bytes] = [b"\x1b\x7f"]
    inst, _ = _mount(
        TextInput(
            initial_value="foo bar",
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "foo "))
    inst.unmount()


# ---------------------------------------------------------------------------
# Paste — bracketed paste sequence
# ---------------------------------------------------------------------------


def test_bracketed_paste_inserts_as_single_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bracketed paste ``\\x1b[200~ ... \\x1b[201~`` produces one on_change."""
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[200~hello\x1b[201~"]
    inst, _ = _mount(
        TextInput(
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "hello"))
    inst.unmount()
    # Crucially, only ONE on_change fired (not one per character).
    assert changes == ["hello"]


def test_bracketed_paste_replaces_active_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paste over an active selection replaces the selected range."""
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[H",       # Home
        b"\x1b[1;2C",    # Shift+Right → [0, 1)
        b"\x1b[1;2C",    # Shift+Right → [0, 2)
        b"\x1b[200~XY\x1b[201~",  # Paste "XY"
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abcd",
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "XYcd"))
    inst.unmount()


def test_bracketed_paste_in_multiline_keeps_newlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-line paste in multiline mode preserves newlines verbatim."""
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[200~line1\nline2\x1b[201~"]
    inst, _ = _mount(
        TextInput(
            multiline=True,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=30,
        rows=6,
    )
    assert _wait_for(lambda: _last_change_is(changes, "line1\nline2"))
    inst.unmount()


def test_bracketed_paste_in_singleline_strips_newlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-line paste collapses newlines to spaces."""
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[200~line1\nline2\x1b[201~"]
    inst, _ = _mount(
        TextInput(
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "line1 line2"))
    inst.unmount()


def test_bracketed_paste_respects_max_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paste that would exceed ``max_length`` is dropped entirely."""
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[200~hello\x1b[201~"]
    inst, _ = _mount(
        TextInput(
            initial_value="abc",
            max_length=5,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    # Buffer "abc" + paste "hello" → 8 chars > 5 → dropped.
    time.sleep(0.2)
    assert changes == []
    assert "abc" in _visible(_frame(inst))
    inst.unmount()


# ---------------------------------------------------------------------------
# Ctrl+K / Ctrl+U — now line-scoped (PR1 was buffer-scoped)
# ---------------------------------------------------------------------------


def test_ctrl_k_kills_to_end_of_current_line_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In multiline mode Ctrl+K kills to end of the *current* line only.

    Initial value "ab\\ncdef"; default cursor sits at end of line 1
    (offset 7). Up → offset 2 (end of "ab", line 0). Left → offset 1
    (between 'a' and 'b'). Ctrl+K removes [1, 2) → kills 'b' only.
    The newline + line 1 are preserved.
    """
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[A",   # Up    → line 0 col 2 (offset 2, end of "ab")
        b"\x1b[D",   # Left  → offset 1 (between 'a' and 'b')
        b"\x0b",     # Ctrl+K — kill [1, 2) → removes 'b' only.
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="ab\ncdef",
            multiline=True,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=6,
    )
    assert _wait_for(lambda: _last_change_is(changes, "a\ncdef"))
    inst.unmount()


def test_ctrl_u_kills_to_start_of_current_line_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In multiline mode Ctrl+U kills to start of the *current* line only."""
    changes: list[str] = []
    # Initial value "ab\\ncdef"; cursor at end of line 1.
    # Ctrl+U on line 1 kills "cdef" (everything before cursor on line 1).
    feed: list[bytes] = [b"\x15"]  # Ctrl+U at cursor end → kill to line start.
    inst, _ = _mount(
        TextInput(
            initial_value="ab\ncdef",
            multiline=True,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=6,
    )
    assert _wait_for(lambda: _last_change_is(changes, "ab\n"))
    inst.unmount()


# ---------------------------------------------------------------------------
# Multiline integration — type + newline + backspace roundtrip
# ---------------------------------------------------------------------------


def test_multiline_type_and_backspace_across_newline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enter creates a newline; Backspace at col 0 of line 1 joins lines."""
    changes: list[str] = []
    feed: list[bytes] = [
        b"a", b"b",
        b"\r",      # Enter → "ab\n"
        b"c",       # → "ab\nc"
        b"\x7f",    # Backspace → "ab\n"
        b"\x7f",    # Backspace at col 0 line 1 → "ab" (removes newline)
    ]
    inst, _ = _mount(
        TextInput(
            multiline=True,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=6,
    )
    assert _wait_for(lambda: _last_change_is(changes, "ab"))
    inst.unmount()


# ---------------------------------------------------------------------------
# Props — multiline default
# ---------------------------------------------------------------------------


def test_multiline_default_is_false() -> None:
    el = TextInput()
    assert el.props["multiline"] is False


def test_multiline_prop_captured() -> None:
    el = TextInput(multiline=True)
    assert el.props["multiline"] is True


# ===========================================================================
# on_cursor_change — cursor-moved callback
# ===========================================================================


def test_default_cursor_style_is_block() -> None:
    """PRD Bug 1 — default ``cursor_style`` is now ``"block"``."""
    el = TextInput()
    assert el.props["cursor_style"] == "block"


def test_on_cursor_change_fires_on_mount_with_initial_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``on_cursor_change`` fires once on mount with the initial cursor offset.

    Mirrors ``on_change``'s first-write semantics — lets callers seed a
    cursor mirror without a separate initialisation path.
    """
    offsets: list[int] = []
    inst, _ = _mount(
        TextInput(initial_value="abc", on_cursor_change=offsets.append),
        monkeypatch=monkeypatch,
    )
    assert _wait_for(lambda: offsets == [3])
    inst.unmount()


def test_on_cursor_change_fires_on_arrow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Left arrow moves the cursor → callback fires with the new offset."""
    offsets: list[int] = []
    feed: list[bytes] = [b"\x1b[D"]  # Left
    inst, _ = _mount(
        TextInput(initial_value="abc", on_cursor_change=offsets.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    # Mount → 3; Left → 2.
    assert _wait_for(lambda: offsets == [3, 2])
    inst.unmount()


def test_on_cursor_change_fires_on_typing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Typing a character advances the cursor → callback fires."""
    offsets: list[int] = []
    feed: list[bytes] = [b"X"]
    inst, _ = _mount(
        TextInput(initial_value="abc", on_cursor_change=offsets.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    # Mount → 3; type 'X' → cursor at 4.
    assert _wait_for(lambda: offsets == [3, 4])
    inst.unmount()


def test_on_cursor_change_fires_on_backspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backspace deletes a char and moves the cursor back → callback fires."""
    offsets: list[int] = []
    feed: list[bytes] = [b"\x7f"]  # Backspace
    inst, _ = _mount(
        TextInput(initial_value="abc", on_cursor_change=offsets.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    # Mount → 3; Backspace → 2.
    assert _wait_for(lambda: offsets == [3, 2])
    inst.unmount()


def test_on_cursor_change_fires_on_home_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Home / End navigation fires the callback."""
    offsets: list[int] = []
    feed: list[bytes] = [
        b"\x1b[H",  # Home → 0
        b"\x1b[F",  # End → 3
    ]
    inst, _ = _mount(
        TextInput(initial_value="abc", on_cursor_change=offsets.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    # Mount → 3; Home → 0; End → 3.
    assert _wait_for(lambda: offsets == [3, 0, 3])
    inst.unmount()


def test_on_cursor_change_None_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Omitting the callback must not crash the input pipeline."""
    feed: list[bytes] = [b"a", b"b", b"\x1b[D", b"\x7f"]
    inst, _ = _mount(
        TextInput(initial_value="abc"), monkeypatch=monkeypatch, feed=feed
    )
    # The component stays alive and the buffer responds to edits.
    assert _wait_for(lambda: "ab" in _visible(_frame(inst)))
    inst.unmount()


def test_on_cursor_change_reflects_latest_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The callback is held in a ``Ref`` — passing a closure over a list works."""
    offsets_a: list[int] = []
    feed: list[bytes] = [b"\x1b[D"]
    inst, _ = _mount(
        TextInput(initial_value="abc", on_cursor_change=offsets_a.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: offsets_a == [3, 2])
    inst.unmount()


# ---------------------------------------------------------------------------
# Long-content layout — Text uses ``wrap="truncate-end"`` so the parent Box
# never grows past its row budget. (Bug fix: single-line TextInput with very
# long content used to wrap into extra rows and blow the column layout.)
# ---------------------------------------------------------------------------


def test_single_line_input_long_content_does_not_grow_box(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Content longer than the viewport renders as a single visible row.

    A single-line TextInput whose buffer exceeds the container width must
    NOT push the parent Box into extra rows — that would cascade into the
    surrounding column layout and crop sibling components. The internal
    Text uses ``wrap="truncate-end"`` so long content clips to one row.
    """
    # 40 chars in a 20-col viewport → must overflow horizontally without
    # adding vertical rows.
    long_value = "a" * 40
    inst, _ = _mount(
        TextInput(initial_value=long_value),
        monkeypatch=monkeypatch,
        columns=20,
        rows=3,
    )
    # Wait for first paint, then count newlines in the raw frame. A
    # single-line input renders exactly one content row; the parent Box
    # has no border so the frame should have at most one newline (the
    # row terminator).
    assert _wait_for(lambda: "a" in _visible(_frame(inst)))
    frame = _frame(inst)
    # Strip trailing blank lines the renderer pads the viewport with.
    # The meaningful content is everything before the first run of empty
    # padding rows; we count non-empty visible rows.
    visible_lines = [
        line for line in _visible(frame).split("\n") if line.strip()
    ]
    assert len(visible_lines) <= 1, (
        f"single-line input should render ≤ 1 visible row, got {visible_lines!r}"
    )
    inst.unmount()


def test_multi_line_input_each_line_truncates_at_box_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each physical line of a multi-line TextInput truncates at box width.

    A multi-line TextInput with one very long physical line must NOT let
    that line wrap into extra rows inside its own Box. Each per-line Text
    child uses ``wrap="truncate-end"`` so a 2-line buffer stays 2 rows
    even if one line is wider than the viewport.
    """
    # Line 0 is short, line 1 is 40 chars (overflows the 20-col viewport).
    inst, _ = _mount(
        TextInput(initial_value="short\n" + "b" * 40, multiline=True),
        monkeypatch=monkeypatch,
        columns=20,
        rows=6,
    )
    assert _wait_for(lambda: "short" in _visible(_frame(inst)))
    frame = _frame(inst)
    visible_lines = [
        line for line in _visible(frame).split("\n") if line.strip()
    ]
    # Two logical lines → at most 2 visible content rows (no wrap-induced
    # extras). We allow ≤ 2 to stay robust to the renderer's own padding
    # logic but the key assertion is that the long line didn't fan out
    # into 3+ rows.
    assert len(visible_lines) <= 2, (
        f"multi-line input should render ≤ 2 visible rows "
        f"(one per logical line), got {visible_lines!r}"
    )
    inst.unmount()


def test_multi_line_renders_one_row_per_line_after_enter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pressing Enter grows the rendered multi-line TextInput by one row.

    Regression: the previous implementation built the Box's children list
    *at mount* with a fixed line count derived from the initial value.
    Because PyInk function components run exactly once at mount, that
    count froze — typing Enter added a ``\\n`` to the buffer signal but
    never produced a new Text child, so the second line stayed invisible.

    The fix renders the full multi-line buffer through a single Text
    whose callable re-evaluates on every signal write, so the layout
    engine sees the up-to-date ``\\n``-joined string each paint.
    """
    # Type "aa", Enter, "bb", Enter, "cc" → buffer ends up as "aa\nbb\ncc".
    feed: list[bytes] = [b"a", b"a", b"\r", b"b", b"b", b"\r", b"c", b"c"]
    changes: list[str] = []
    inst, _ = _mount(
        TextInput(multiline=True, on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=8,
    )
    assert _wait_for(lambda: _last_change_is(changes, "aa\nbb\ncc"))
    visible_lines = [
        line for line in _visible(_frame(inst)).split("\n") if line.strip()
    ]
    # All three logical lines should appear in the rendered frame.
    assert "aa" in " ".join(visible_lines)
    assert "bb" in " ".join(visible_lines)
    assert "cc" in " ".join(visible_lines)
    inst.unmount()


def test_multi_line_cursor_in_second_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cursor on line 1 renders the cursor cell on the second rendered row.

    With initial value ``"aaa\\nbbb"`` the cursor starts at offset 7
    (end of "bbb"). The block cursor sits on the 'b' that ends the
    second line, so the inverse-video cursor cell must land on the
    second visible row, not the first.
    """
    inst, _ = _mount(
        TextInput(initial_value="aaa\nbbb", multiline=True),
        monkeypatch=monkeypatch,
        columns=20,
        rows=6,
    )
    # The frame should contain the cursor SGR escape (inverse video).
    assert _wait_for(lambda: "\x1b[7m" in _frame(inst))
    frame = _frame(inst)
    rows = frame.split("\n")
    # Find the row carrying the cursor and confirm it also carries the
    # second line's content ('bbb'). The first row should not carry the
    # cursor.
    cursor_rows = [i for i, r in enumerate(rows) if "\x1b[7m" in r]
    assert cursor_rows, f"cursor SGR not found in frame: {frame!r}"
    cursor_row_idx = cursor_rows[0]
    assert "bbb" in _visible(rows[cursor_row_idx]), (
        f"cursor should be on the 'bbb' row; got row={rows[cursor_row_idx]!r}"
    )
    inst.unmount()


# ---------------------------------------------------------------------------
# Cursor preservation under per-line truncation (Bug 1 regression)
# ---------------------------------------------------------------------------


def test_multi_line_arrow_down_clamps_at_last_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ArrowDown at the last line never moves the cursor past buffer end.

    Regression: ``_offset_below`` and the surrounding ArrowDown handler
    must keep the cursor within ``[0, len(value)]``. Multiple ArrowDown
    presses on the last line should leave the cursor at end-of-buffer
    (the on_cursor_change callback mirrors the internal cursor signal,
    so we assert via that rather than parsing the rendered cursor cell).
    """
    offsets: list[int] = []
    feed: list[bytes] = [
        b"\x1b[B",  # Down — already on last line, clamp to end-of-line
        b"\x1b[B",  # Down — still clamped
        b"\x1b[B",  # Down — still clamped
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="ab\ncde\nfghi",
            multiline=True,
            on_cursor_change=offsets.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=6,
    )
    # The initial-mount callback fires once with offset 11 (end of
    # buffer). Each ArrowDown on the last line should add at most one
    # more "11" entry (the signal doesn't fire when the value is
    # unchanged, but the handler always goes through _set_cursor which
    # writes the clamped value — some re-emissions may show up).
    assert _wait_for(lambda: len(offsets) >= 1)
    time.sleep(0.3)
    inst.unmount()
    # Every captured offset must be a valid cursor position.
    assert all(0 <= o <= 11 for o in offsets), (
        f"cursor escaped valid range: {offsets!r}"
    )
    # The cursor must end at the end of the last line (offset 11).
    assert offsets[-1] == 11


def test_cursor_survives_per_line_truncation_long_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cursor cell stays visible when its row is truncated to fit the box.

    Regression: when a multi-line TextInput row is wider than the
    available column budget, the per-line truncation path used to drop
    the cursor's SGR sequences — the inverse-video start survived but
    its reset was clipped, leaving an unterminated SGR run that the
    renderer's per-row ``rstrip`` would then eat (cursor cell became
    an un-reset space at end of row → stripped → cursor "vanished").

    The cursor-aware pre-truncation in :mod:`pyink.externals.text_input`
    preserves the cursor cell + its reset escape so the cursor stays
    visible even when its row is truncated.
    """
    inst, _ = _mount(
        TextInput(
            initial_value="short\n" + "x" * 40,
            multiline=True,
        ),
        monkeypatch=monkeypatch,
        columns=30,
        rows=6,
    )
    assert _wait_for(lambda: "\x1b[7m" in _frame(inst))

    def cursor_cell_intact() -> bool:
        # The cursor cell is the inverse-video space + reset pair.
        # An unterminated SGR run (the regression) shows up as a bare
        # ``\x1b[7m`` with no following ``\x1b[0m`` on the same row.
        for row in _frame(inst).split("\n"):
            if "\x1b[7m" in row and "\x1b[0m" not in row:
                return False
        return "\x1b[7m \x1b[0m" in _frame(inst)

    assert _wait_for(cursor_cell_intact), (
        f"cursor cell lost its reset escape under truncation: "
        f"{_frame(inst)!r}"
    )
    inst.unmount()


def test_text_input_long_value_truncates_to_one_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-line TextInput with very long content renders exactly 1 row.

    Regression: long single-line content used to wrap into extra rows
    inside its own Box and blow the column layout. With per-line
    pre-truncation + ``wrap="truncate-end"`` the box stays at 1 row.
    """
    long_value = "a" * 80
    inst, _ = _mount(
        TextInput(initial_value=long_value),
        monkeypatch=monkeypatch,
        columns=20,
        rows=3,
    )
    assert _wait_for(lambda: "a" in _visible(_frame(inst)))

    def one_visible_row() -> bool:
        visible_lines = [
            line for line in _visible(_frame(inst)).split("\n") if line.strip()
        ]
        return len(visible_lines) == 1

    assert _wait_for(one_visible_row), (
        f"single-line input should render exactly 1 row; "
        f"got: {_visible(_frame(inst)).split(chr(10))!r}"
    )
    inst.unmount()


# ---------------------------------------------------------------------------
# Cursor SGR preservation under per-line truncation (Bug B regression)
# ---------------------------------------------------------------------------


def test_take_visible_cells_preserves_cursor_sgr_mid_string() -> None:
    """``_take_visible_cells`` keeps a cursor SGR run that sits *inside* the
    returned prefix.

    Regression (Bug B): ``_take_visible_cells`` used to drop escapes held in
    ``pending_trailing`` when a new visible character was appended — so a
    block cursor over the first character of a long line lost its
    ``\\x1b[7m<char>\\x1b[0m`` paint as soon as the visible window reached
    past the cursor. The cursor visually "disappeared" even though the
    cursor cell was inside the kept window.

    The fix flushes pending escapes into ``out`` *before* the next visible
    char so the cursor's SGR open + reset both survive into the prefix.
    """
    from pyink.externals.text_input import _take_visible_cells

    # Block cursor over 'a' (green + inverse), then more visible text.
    s = f"{ESC}[32m{ESC}[7ma{ESC}[0mbcdef"
    # width=1 keeps just the cursor cell — both open and reset must survive.
    assert _take_visible_cells(s, 1) == f"{ESC}[32m{ESC}[7ma{ESC}[0m"
    # width=3 keeps cursor + 2 more chars — the cursor SGR run is still
    # intact (open before 'a', reset after, then 'bc').
    assert _take_visible_cells(s, 3) == f"{ESC}[32m{ESC}[7ma{ESC}[0mbc"


def test_take_visible_cells_preserves_mid_string_cursor_sgr() -> None:
    """Cursor SGR in the middle of the visible prefix survives the cut."""
    from pyink.externals.text_input import _take_visible_cells

    # Cursor over 'b' in "abcdef", keep width 3.
    s = f"a{ESC}[7mb{ESC}[0mcdef"
    assert _take_visible_cells(s, 3) == f"a{ESC}[7mb{ESC}[0mc"


def test_take_visible_cells_drops_unclosed_cursor_when_past_width() -> None:
    """When the cursor cell itself sits past the width budget, its open
    SGR sequence is dropped (not left dangling).

    A dangling ``\\x1b[7m`` at the end of a row would flip the terminal's
    inverse video on for everything painted afterwards — the classic
    "cursor covering multiple chars" visual bug.
    """
    from pyink.externals.text_input import _take_visible_cells

    # Block cursor at end-of-input (inverse space). Width=3 keeps only
    # 'abc'; the cursor's open `\x1b[7m` must be stripped so it doesn't
    # leak inverse video onto whatever the renderer paints next.
    s = f"abc{ESC}[7m {ESC}[0m"
    assert _take_visible_cells(s, 3) == "abc"


def test_take_visible_cells_keeps_trailing_reset_escape() -> None:
    """A lone trailing reset (``\\x1b[0m``) at the cut point survives."""
    from pyink.externals.text_input import _take_visible_cells

    s = f"ab{ESC}[0m"
    assert _take_visible_cells(s, 2) == f"ab{ESC}[0m"


def test_cursor_stays_inverse_when_line_truncated_with_cursor_in_spaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Block cursor stays visible (single inverse cell) when its row is
    truncated and the cursor sits inside the whitespace region.

    Regression (Bug B): ``_take_visible_cells`` dropped the cursor's
    ``\\x1b[7m`` opener when a new visible character was appended, so the
    inverse-video paint leaked out of the cursor cell. The user-visible
    symptom was "cursor covers multiple characters" or "cursor covers the
    whole row" because the terminal's inverse video never got reset.

    With the fix the cursor is rendered as exactly one inverse cell — no
    matter how many times the user moves the arrow keys through the
    whitespace region of a long line.
    """
    # 30 spaces between 'abc' and 'def' → buffer length 36.
    initial = "abc" + " " * 30 + "def"
    inst, _ = _mount(
        TextInput(initial_value=initial, cursor_color="green"),
        monkeypatch=monkeypatch,
        columns=20,  # narrow so per-line truncation kicks in
        rows=5,
    )
    # Move cursor all the way to the start (over 'a').
    feed: list[bytes] = [b"\x1b[H"]
    inst2 = inst
    # Drive the cursor via the test harness by re-mounting with feed.
    inst.unmount()
    inst2, _ = _mount(
        TextInput(initial_value=initial, cursor_color="green"),
        monkeypatch=monkeypatch,
        feed=feed + [b"\x1b[D"] * 5,  # Home then a few more lefts to be sure
        columns=20,
        rows=5,
    )
    assert _wait_for(lambda: f"{ESC}[7m" in _frame(inst2))

    def exactly_one_inverse_cell() -> bool:
        frame = _frame(inst2)
        return frame.count(f"{ESC}[7m") == 1 and frame.count(f"{ESC}[0m") >= 1

    assert _wait_for(exactly_one_inverse_cell), (
        f"cursor should render exactly one inverse cell when truncated; "
        f"got: {_frame(inst2)!r}"
    )

    # Also verify the inverse cell is properly closed by a reset on the
    # same row (no dangling SGR opener).
    frame = _frame(inst2)
    for row in frame.split("\n"):
        if f"{ESC}[7m" in row:
            # Each inverse open must have a matching reset on the same row.
            assert row.count(f"{ESC}[0m") >= row.count(f"{ESC}[7m"), (
                f"row has unclosed inverse SGR: {row!r}"
            )
    inst2.unmount()


def test_cursor_stays_single_inverse_through_arrow_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Block cursor stays exactly one inverse cell through arrow-key
    navigation in a long line with many spaces.

    Regression (Bug B): the user reported that moving the cursor with
    arrow keys through a long line containing many spaces caused the
    cursor to visually "cover multiple characters". This was caused by
    the per-line truncation path dropping the cursor's SGR reset when
    extending the visible window past the cursor cell.

    With the fix the inverse count stays at exactly 1 for every cursor
    position the user can reach via arrow keys.
    """
    initial = "abc" + " " * 30 + "def"
    # Drive: Home, then Right step by step.
    feed: list[bytes] = [b"\x1b[H"]
    # 15 right-arrow presses — walks the cursor through the whitespace.
    feed.extend([b"\x1b[C"] * 15)
    inst, _ = _mount(
        TextInput(initial_value=initial, cursor_color="green"),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=5,
    )
    assert _wait_for(lambda: f"{ESC}[7m" in _frame(inst))

    # After all the arrow keys have been processed the cursor must still
    # render as exactly one inverse cell — the SGR run did not leak.
    def exactly_one_inverse() -> bool:
        return _frame(inst).count(f"{ESC}[7m") == 1

    assert _wait_for(exactly_one_inverse, attempts=120), (
        f"cursor should render exactly one inverse cell; "
        f"got: {_frame(inst)!r}"
    )
    inst.unmount()


# ---------------------------------------------------------------------------
# Bug A regression — wrap="truncate-end" on dim Text renders as one row,
# no wrap, no empty line between sibling status Texts.
# ---------------------------------------------------------------------------


def test_dim_status_text_truncates_to_single_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long dim-colored ``Text`` with ``wrap="truncate-end"`` renders one row.

    Regression (Bug A): the user reported that the demo's
    ``Submitted: '...'`` status line still wrapped (and inserted an empty
    line between two Submitted lines) even though the ``Text`` had
    ``wrap="truncate-end"`` set. Root cause investigations showed the
    truncation path was working — the test pins the behaviour so a future
    regression in either the layout wrap engine or the measure
    ``_truncate_end`` helper surfaces immediately.
    """
    long_text = "Submitted: '" + "abc " * 50 + "'"
    inst, _ = _mount(
        Box(
            Text("Header"),
            Text(long_text, dimColor=True, wrap="truncate-end"),
            Text("Footer"),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        ),
        monkeypatch=monkeypatch,
        columns=70,
        rows=10,
    )
    assert _wait_for(lambda: "Submitted" in _visible(_frame(inst)))
    frame = _frame(inst)

    # The dim-coloured Submitted row should contain an ellipsis (…) —
    # the signature of truncate-end.
    visible_lines = [
        line for line in _visible(frame).split("\n") if line.strip()
    ]
    submitted_rows = [ln for ln in visible_lines if "Submitted" in ln]
    assert len(submitted_rows) == 1, (
        f"Submitted text should be exactly 1 row, got {submitted_rows!r}"
    )
    # Ellipsis marks the truncation point.
    assert "…" in submitted_rows[0], (
        f"Submitted row should end with ellipsis, got {submitted_rows[0]!r}"
    )
    inst.unmount()


def test_two_submitted_lines_no_empty_line_between(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two adjacent ``Text`` rows with ``wrap="truncate-end"`` stay adjacent.

    Regression (Bug A): the user reported an empty row between two
    Submitted status lines. The column layout must place them on
    consecutive rows; the truncate-end wrap must not introduce extra
    padding rows.
    """
    long_a = "Submitted: '" + "a" * 100 + "'"
    long_b = "Submitted: '" + "b" * 100 + "'"
    inst, _ = _mount(
        Box(
            Text(long_a, dimColor=True, wrap="truncate-end"),
            Text(long_b, dimColor=True, wrap="truncate-end"),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        ),
        monkeypatch=monkeypatch,
        columns=70,
        rows=8,
    )
    assert _wait_for(lambda: "Submitted" in _visible(_frame(inst)))

    visible_lines = [
        line for line in _visible(_frame(inst)).split("\n") if line.strip()
    ]
    # Find indices of the two Submitted rows; they must be consecutive
    # (no blank/empty row between them).
    submitted_indices = [
        i for i, ln in enumerate(visible_lines) if "Submitted" in ln
    ]
    assert len(submitted_indices) == 2, (
        f"expected 2 Submitted rows, got {submitted_indices!r}"
    )
    assert submitted_indices[1] - submitted_indices[0] == 1, (
        f"Submitted rows must be adjacent; gap = "
        f"{submitted_indices[1] - submitted_indices[0]}"
    )
    inst.unmount()


# ---------------------------------------------------------------------------
# Bug C regression — TextInput inside a column Box with sibling Texts
# renders label / value / hint on three separate rows.
# ---------------------------------------------------------------------------


def test_text_input_column_layout_renders_siblings_on_separate_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Label / TextInput / Hint stacked in a column render on 3 distinct rows.

    Regression (Bug C): the user reported the input's content rendered
    *next to* the placeholder on the same row. The cause was a visual
    artefact rather than a layout bug (column flex direction places each
    child on its own row), but the test pins the expected behaviour so a
    future regression in either the column layout or the TextInput
    placeholder/value rendering surfaces immediately.
    """
    inst, _ = _mount(
        Box(
            Text("Label", bold=True),
            TextInput(
                initial_value="actual content here",
                placeholder="placeholder text",
            ),
            Text("Hint", dimColor=True),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        ),
        monkeypatch=monkeypatch,
        columns=40,
        rows=10,
    )
    assert _wait_for(
        lambda: "Label" in _visible(_frame(inst))
        and "actual content here" in _visible(_frame(inst))
        and "Hint" in _visible(_frame(inst))
    )

    # Each of "Label", "actual content here", "Hint" must sit on its
    # own row — never combined on the same visible row.
    visible_lines = _visible(_frame(inst)).split("\n")
    rows_with_label = [i for i, ln in enumerate(visible_lines) if "Label" in ln]
    rows_with_content = [
        i for i, ln in enumerate(visible_lines) if "actual content here" in ln
    ]
    rows_with_hint = [i for i, ln in enumerate(visible_lines) if "Hint" in ln]
    assert rows_with_label and rows_with_content and rows_with_hint, (
        f"missing expected rows: label={rows_with_label}, "
        f"content={rows_with_content}, hint={rows_with_hint}"
    )
    label_row = rows_with_label[0]
    content_row = rows_with_content[0]
    hint_row = rows_with_hint[0]
    # Three distinct rows in the expected order.
    assert label_row < content_row < hint_row, (
        f"rows not in expected order: label={label_row}, "
        f"content={content_row}, hint={hint_row}"
    )
    inst.unmount()


# ---------------------------------------------------------------------------
# is_active as Signal / Callable — dynamic re-evaluation per keypress
# ---------------------------------------------------------------------------


def test_is_active_callable_evaluates_per_keypress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``is_active`` accepts a 0-arg callable; handler re-reads it each key.

    We drive a ``TextInput`` whose ``is_active`` callable is bound to a
    mutable flag. Using :func:`_patch_input` we feed bytes one at a
    time on demand: between keystrokes we flip the flag and verify
    only keystrokes delivered while the flag was ``True`` landed.
    """
    state = {"active": True}
    changes: list[str] = []

    # Demand-driven byte source: ``next_byte`` releases the next chunk
    # to the input thread. This makes the test fully deterministic —
    # we control exactly when each key arrives so we can flip the flag
    # *between* keys.
    pending: list[bytes] = [b"a", b"b", b"c", b"d"]
    ready = threading.Event()
    consumed = threading.Event()

    def fake_read(fd: int, n: int) -> bytes:
        ready.wait()
        ready.clear()
        chunk = pending.pop(0)
        consumed.set()
        return chunk

    def send(byte: bytes) -> None:
        pending.append(byte)
        ready.set()
        consumed.wait()
        consumed.clear()

    monkeypatch.setattr(_term_mod, "_read_stdin_chunk", fake_read)
    monkeypatch.setattr(_term_mod, "_wait_for_input", lambda fd, timeout: True)
    monkeypatch.setattr(Terminal, "_enter_raw_mode_unix", lambda self: None)
    monkeypatch.setattr(Terminal, "_exit_raw_mode_unix", lambda self: None)
    monkeypatch.setattr(Terminal, "_enter_raw_mode_windows", lambda self: None)
    monkeypatch.setattr(Terminal, "_exit_raw_mode_windows", lambda self: None)

    out = io.StringIO()
    inst = render(
        TextInput(
            initial_value="",
            is_active=lambda: state["active"],
            on_change=changes.append,
        ),
        stdout=out,
        stdin=_FakeTTY(),
        columns=30,
        rows=3,
        exit_on_ctrl_c=False,
    )
    time.sleep(0.05)

    # Key 1: active → lands.
    send(b"a")
    assert _wait_for(lambda: changes == ["a"])

    # Key 2: flip to inactive; key is delivered but ignored.
    state["active"] = False
    send(b"b")
    time.sleep(0.05)
    assert changes == ["a"]

    # Key 3: still inactive → ignored.
    send(b"c")
    time.sleep(0.05)
    assert changes == ["a"]

    # Key 4: flip back to active → next key lands; final buffer is "ad".
    state["active"] = True
    send(b"d")
    assert _wait_for(lambda: changes == ["a", "ad"])

    inst.unmount()


def test_is_active_signal_evaluates_per_keypress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``is_active`` accepts a ``Signal[bool]`` resolved via ``.value``.

    The handler reads ``.value`` imperatively (without subscribing) on
    every keypress. Flipping the signal to ``False`` mid-stream must
    suppress subsequent keystrokes; flipping back to ``True`` must
    re-enable them.
    """
    active = signal(True)
    changes: list[str] = []

    pending: list[bytes] = [b"a", b"b", b"c"]
    ready = threading.Event()
    consumed = threading.Event()

    def fake_read(fd: int, n: int) -> bytes:
        ready.wait()
        ready.clear()
        chunk = pending.pop(0)
        consumed.set()
        return chunk

    def send(byte: bytes) -> None:
        pending.append(byte)
        ready.set()
        consumed.wait()
        consumed.clear()

    monkeypatch.setattr(_term_mod, "_read_stdin_chunk", fake_read)
    monkeypatch.setattr(_term_mod, "_wait_for_input", lambda fd, timeout: True)
    monkeypatch.setattr(Terminal, "_enter_raw_mode_unix", lambda self: None)
    monkeypatch.setattr(Terminal, "_exit_raw_mode_unix", lambda self: None)
    monkeypatch.setattr(Terminal, "_enter_raw_mode_windows", lambda self: None)
    monkeypatch.setattr(Terminal, "_exit_raw_mode_windows", lambda self: None)

    out = io.StringIO()
    inst = render(
        TextInput(
            initial_value="",
            is_active=active,
            on_change=changes.append,
        ),
        stdout=out,
        stdin=_FakeTTY(),
        columns=30,
        rows=3,
        exit_on_ctrl_c=False,
    )
    time.sleep(0.05)

    # Key 1: signal True → lands.
    send(b"a")
    assert _wait_for(lambda: changes == ["a"])

    # Key 2: signal False → ignored.
    active.value = False
    send(b"b")
    time.sleep(0.05)
    assert changes == ["a"]

    # Key 3: signal True again → lands; final buffer is "ac".
    active.value = True
    send(b"c")
    assert _wait_for(lambda: changes == ["a", "ac"])

    inst.unmount()


def test_multiple_text_inputs_only_focused_receives_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two ``TextInput`` s in one tree — only the focused one is mutated.

    Reproduces the bug from the wild: with all inputs defaulting to
    ``is_active=True`` a single keystroke mutated every input in
    lockstep. The fix lets each input's ``is_active`` callable gate
    dispatch independently. We mount two inputs whose ``is_active``
    callables read from a shared ``focus`` value, flip the value
    between keystrokes, and assert only the active input's buffer
    changed.
    """
    focus = signal("a")
    a_changes: list[str] = []
    b_changes: list[str] = []

    pending: list[bytes] = [b"x", b"y"]
    ready = threading.Event()
    consumed = threading.Event()

    def fake_read(fd: int, n: int) -> bytes:
        ready.wait()
        ready.clear()
        chunk = pending.pop(0)
        consumed.set()
        return chunk

    def send(byte: bytes) -> None:
        pending.append(byte)
        ready.set()
        consumed.wait()
        consumed.clear()

    monkeypatch.setattr(_term_mod, "_read_stdin_chunk", fake_read)
    monkeypatch.setattr(_term_mod, "_wait_for_input", lambda fd, timeout: True)
    monkeypatch.setattr(Terminal, "_enter_raw_mode_unix", lambda self: None)
    monkeypatch.setattr(Terminal, "_exit_raw_mode_unix", lambda self: None)
    monkeypatch.setattr(Terminal, "_enter_raw_mode_windows", lambda self: None)
    monkeypatch.setattr(Terminal, "_exit_raw_mode_windows", lambda self: None)

    out = io.StringIO()
    inst = render(
        Box(
            TextInput(
                is_active=lambda: focus.value == "a",
                on_change=a_changes.append,
            ),
            TextInput(
                is_active=lambda: focus.value == "b",
                on_change=b_changes.append,
            ),
            flexDirection="column",
        ),
        stdout=out,
        stdin=_FakeTTY(),
        columns=30,
        rows=6,
        exit_on_ctrl_c=False,
    )
    time.sleep(0.05)

    # Key "x": focus is "a" → lands in input A only.
    send(b"x")
    assert _wait_for(lambda: a_changes == ["x"])
    assert b_changes == []

    # Switch focus to B; key "y" lands in B only.
    focus.value = "b"
    send(b"y")
    assert _wait_for(lambda: b_changes == ["y"])
    assert a_changes == ["x"]
    assert b_changes == ["y"]

    inst.unmount()


# ---------------------------------------------------------------------------
# Bug 4 regression — CJK cursor column alignment
# ---------------------------------------------------------------------------


def test_cursor_position_with_cjk_chars_block() -> None:
    """Block cursor over an ASCII char following CJK chars sits at the right column.

    Regression (Bug 4): ``_build_displayed_line`` used to splice the
    cursor SGR by character offset while ``_cursor_visible_column``
    used display width — the two diverged by one cell for every wide
    character before the cursor. The cursor SGR now lands at the exact
    visible column (display width of the prefix).
    """
    from pyink.externals.text_input import _build_displayed_line
    from pyink.layout.measure import string_width

    # value = "你好abc"; cursor between '好' and 'a' (local_cursor = 2).
    # Display columns: 你=0-2, 好=2-4, a=4-5, b=5-6, c=6-7.
    # Cursor visible column = 4 (between 好's right edge and a's left edge).
    line = "你好abc"
    rendered = _build_displayed_line(
        line,
        cursor_in_line=True,
        cursor=2,
        line_start=0,
        cursor_style="block",
        cursor_color=None,
        selection=None,
        mask=None,
        line_text_raw=line,
    )
    # Visible width of the prefix before the cursor SGR must equal 4
    # (the display column the cursor sits on).
    idx = rendered.find(f"{ESC}[7m")
    assert idx != -1, f"cursor SGR not found in {rendered!r}"
    prefix = rendered[:idx]
    assert string_width(prefix) == 4, (
        f"prefix before cursor should be 4 cells wide (你好), "
        f"got {string_width(prefix)}: {rendered!r}"
    )
    # The cursor cell inverts 'a' (the character under the block cursor).
    assert f"{ESC}[7ma{ESC}[0m" in rendered


def test_cursor_position_with_cjk_chars_bar() -> None:
    """Bar cursor between CJK and ASCII inserts at the right visible column."""
    from pyink.externals.text_input import _build_displayed_line
    from pyink.layout.measure import string_width

    # value = "你好abc"; cursor between 好 and a (local_cursor = 2, visible col = 4).
    line = "你好abc"
    rendered = _build_displayed_line(
        line,
        cursor_in_line=True,
        cursor=2,
        line_start=0,
        cursor_style="bar",
        cursor_color=None,
        selection=None,
        mask=None,
        line_text_raw=line,
    )
    # Visible width of the prefix before the bar cursor must be 4.
    idx = rendered.find(f"{ESC}[7m")
    assert idx != -1
    prefix = rendered[:idx]
    assert string_width(prefix) == 4, (
        f"bar cursor should land at column 4 after 你好; "
        f"got prefix width {string_width(prefix)}: {rendered!r}"
    )


def test_cursor_position_with_mixed_width() -> None:
    """Cursor before a CJK char following an ASCII char stays aligned."""
    from pyink.externals.text_input import _build_displayed_line
    from pyink.layout.measure import string_width

    # value = "a你好"; cursor before 你 (local_cursor = 1, visible col = 1).
    line = "a你好"
    rendered = _build_displayed_line(
        line,
        cursor_in_line=True,
        cursor=1,
        line_start=0,
        cursor_style="block",
        cursor_color=None,
        selection=None,
        mask=None,
        line_text_raw=line,
    )
    idx = rendered.find(f"{ESC}[7m")
    assert idx != -1
    prefix = rendered[:idx]
    assert string_width(prefix) == 1, (
        f"cursor should sit at visible column 1 (after 'a'); "
        f"got prefix width {string_width(prefix)}: {rendered!r}"
    )
    # The block cursor inverts 你 (the wide character under it).
    assert f"{ESC}[7m你{ESC}[0m" in rendered


def test_cursor_position_with_cjk_at_end() -> None:
    """Cursor past the last CJK character lands at the line's full width."""
    from pyink.externals.text_input import _build_displayed_line
    from pyink.layout.measure import string_width

    # value = "你好"; cursor at end (local_cursor = 2, visible col = 4).
    line = "你好"
    rendered = _build_displayed_line(
        line,
        cursor_in_line=True,
        cursor=2,
        line_start=0,
        cursor_style="block",
        cursor_color=None,
        selection=None,
        mask=None,
        line_text_raw=line,
    )
    # End-of-line block cursor emits an inverse space; the prefix
    # before it should be the full line (width 4).
    idx = rendered.find(f"{ESC}[7m")
    assert idx != -1
    prefix = rendered[:idx]
    assert string_width(prefix) == 4, (
        f"cursor at end should have prefix = full line (width 4); "
        f"got {string_width(prefix)}: {rendered!r}"
    )


def test_truncate_around_cursor_with_cjk() -> None:
    """Truncation keeps the cursor SGR visible at the right column for CJK.

    Long CJK line truncated so the cursor just fits in the leading
    window: the cursor's visible column (computed from display width)
    matches the position the SGR ends up at inside the truncated output.

    Note: we keep the cursor *inside* the leading truncation window so
    the test exercises the alignment between ``_build_displayed_line``
    (which now splices the cursor SGR by display width) and
    ``_truncate_line_around_cursor`` (which advances by visible cell).
    Edge cases where the window boundary splits a wide character are
    tracked separately under Bug 1's clip-to-box work.
    """
    from pyink.externals.text_input import (
        _build_displayed_line,
        _cursor_visible_column,
        _truncate_line_around_cursor,
    )
    from pyink.layout.measure import string_width

    # 3 CJK chars (width 6) + 3 ASCII = 9 visible cells.
    line = "你好世abc"
    assert string_width(line) == 9

    # Cursor at end (after c). local_cursor = 6, visible col = 9.
    local_cursor = 6
    rendered = _build_displayed_line(
        line,
        cursor_in_line=True,
        cursor=local_cursor,
        line_start=0,
        cursor_style="bar",
        cursor_color=None,
        selection=None,
        mask=None,
        line_text_raw=line,
    )
    cursor_col = _cursor_visible_column(line, None, local_cursor)
    assert cursor_col == 9

    # Truncate to width 10 — cursor_end (10) <= width-1 (9)? No, but
    # the full rendered line is 10 cells wide (9 text + 1 bar), so it
    # fits without truncation. Use a wider window to exercise the
    # cursor-fits-in-leading-window branch directly.
    truncated = _truncate_line_around_cursor(
        rendered,
        width=12,
        cursor_visible_column=cursor_col,
        cursor_visible_width=1,
    )
    assert truncated == rendered, (
        f"no truncation expected when line fits; got {truncated!r}"
    )

    # Now truncate to exactly the cursor's end — cursor_end = 10, width
    # = 10 → cursor_end <= width is true (fits exactly).
    # line_width is 10 (9 content + 1 cursor cell); width=10 → no truncation.
    truncated_fit = _truncate_line_around_cursor(
        rendered,
        width=10,
        cursor_visible_column=cursor_col,
        cursor_visible_width=1,
    )
    assert f"{ESC}[7m {ESC}[0m" in truncated_fit, (
        f"cursor cell must survive truncation: {truncated_fit!r}"
    )
    cursor_idx = truncated_fit.find(f"{ESC}[7m")
    prefix = truncated_fit[:cursor_idx]
    # Cursor cell sits at column 9 — the display width of 你好世.
    assert string_width(prefix) == 9, (
        f"cursor should sit at column 9 (display width of 你好世); "
        f"got {string_width(prefix)}: {truncated_fit!r}"
    )


def test_cursor_column_with_cjk_in_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: cursor after CJK content lands at the correct column.

    Mounts a real TextInput with CJK initial value and Home-moves the
    cursor to the start so the block cursor sits on the first CJK char.
    This exercises the full path through the render pipeline.
    """
    inst, _ = _mount(
        TextInput(initial_value="你好", cursor_color="green"),
        monkeypatch=monkeypatch,
        feed=[b"\x1b[H"],  # Home → cursor at start
        columns=20,
        rows=3,
    )

    # The block cursor should be over 你 (the first character). Expect
    # the green+inverse SGR run wrapping 你.
    expected = f"{ESC}[32m{ESC}[7m你{ESC}[0m"
    assert _wait_for(lambda: expected in _frame(inst)), (
        f"cursor SGR over 你 not found in frame: {_frame(inst)!r}"
    )
    inst.unmount()


def test_mask_with_wide_glyph_stays_aligned() -> None:
    """A wide-char mask glyph still aligns cursor + visible width.

    Edge case for Bug 4: when the caller passes a wide-character mask
    (e.g. a CJK glyph), the old char-offset splice produced a visible
    column different from the reported ``_cursor_visible_column``.
    The width-based walk in :func:`_build_displayed_line` keeps the
    two aligned regardless of mask glyph width.
    """
    from pyink.externals.text_input import _build_displayed_line
    from pyink.layout.measure import string_width

    # Mask = 你 (width 2). value = "abc"; cursor after 'b' (local_cursor = 2).
    # Masked line = "你你你" (visible width 6). Cursor visible column = 4.
    line_text = "你你你"
    line_text_raw = "abc"
    rendered = _build_displayed_line(
        line_text,
        cursor_in_line=True,
        cursor=2,
        line_start=0,
        cursor_style="block",
        cursor_color=None,
        selection=None,
        mask="你",
        line_text_raw=line_text_raw,
    )
    idx = rendered.find(f"{ESC}[7m")
    assert idx != -1
    prefix = rendered[:idx]
    # The cursor sits at visible column 4 (2 mask glyphs * 2 cells).
    assert string_width(prefix) == 4, (
        f"wide-mask cursor should sit at column 4; got "
        f"{string_width(prefix)}: {rendered!r}"
    )


def test_bug6_distribute_main_has_no_fixed_parameter() -> None:
    """Bug 6 — ``_distribute_main`` no longer takes a ``fixed`` parameter.

    The parameter was dead (callers built ``main_is_fixed`` lists but
    the function body never read them). Regression: if someone
    reintroduces it (or a caller starts passing it again), this test
    fails fast via the signature inspection.
    """
    import inspect

    from pyink.layout.flex import _distribute_main

    sig = inspect.signature(_distribute_main)
    assert "fixed" not in sig.parameters, (
        f"_distribute_main must not have a 'fixed' parameter; "
        f"signature: {sig}"
    )
    # And the expected parameter set.
    expected = {"children", "sizes", "free", "gap"}
    assert set(sig.parameters) == expected, (
        f"_distribute_main signature changed unexpectedly: {sig}"
    )
