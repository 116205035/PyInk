"""Tests for :func:`pyink.externals.SelectInput` (Phase 4 PR3).

We exercise the component through the live :func:`pyink.render.render`
pipeline because ``SelectInput`` mounts :func:`pyink.hooks.use_input`,
whose guard requires the active ``_current_instance`` ContextVar that
only ``render`` sets up. The mount / feed / unmount helpers mirror
:mod:`tests.externals.test_text_input` so the two suites share a
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
from pyink.externals import SelectInput
from pyink.externals.select_input import _normalize_items, _SelectInputImpl
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
# Fake TTY + input pipeline patches (mirrors test_text_input.py)
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
    columns: int = 40,
    rows: int = 8,
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


# Byte sequences for the keys we feed.
_UP = b"\x1b[A"
_DOWN = b"\x1b[B"
_ENTER = b"\r"
_SPACE = b" "


# ---------------------------------------------------------------------------
# Element shape
# ---------------------------------------------------------------------------


def test_select_input_returns_function_component_element() -> None:
    el = SelectInput(items=["a", "b"])
    assert isinstance(el, Element)
    assert callable(el.type)
    assert el.type is _SelectInputImpl
    # No children — the function component builds its own subtree.
    assert el.children == ()


def test_select_input_props_capture_defaults() -> None:
    el = SelectInput(items=["a"])
    assert el.props["items"] == [{"label": "a", "value": "a"}]
    assert el.props["initial_index"] == 0
    assert el.props["on_select"] is None
    assert el.props["on_change"] is None
    assert el.props["multi_select"] is False
    assert el.props["indicator"] == "❯"
    assert el.props["selected_indicator"] == "✓"
    assert el.props["unselected_indicator"] == " "
    assert el.props["color"] is None
    assert el.props["selected_color"] == "green"
    assert el.props["is_active"] is True
    assert el.props["box_props"] == {}


def test_select_input_props_capture_caller_values() -> None:
    def on_select(v: Any) -> None:
        pass

    def on_change(idx: int) -> None:
        pass

    el = SelectInput(
        items=[{"label": "A", "value": 1}],
        initial_index=2,
        on_select=on_select,
        on_change=on_change,
        multi_select=True,
        indicator=">",
        selected_indicator="x",
        unselected_indicator="-",
        color="blue",
        selected_color="yellow",
        is_active=False,
        padding=1,
    )
    assert el.props["items"] == [{"label": "A", "value": 1}]
    assert el.props["initial_index"] == 2
    assert el.props["on_select"] is on_select
    assert el.props["on_change"] is on_change
    assert el.props["multi_select"] is True
    assert el.props["indicator"] == ">"
    assert el.props["selected_indicator"] == "x"
    assert el.props["unselected_indicator"] == "-"
    assert el.props["color"] == "blue"
    assert el.props["selected_color"] == "yellow"
    assert el.props["is_active"] is False
    assert el.props["box_props"] == {"padding": 1}


# ---------------------------------------------------------------------------
# _normalize_items
# ---------------------------------------------------------------------------


def test_normalize_string_list() -> None:
    result = _normalize_items(["a", "b", "c"])
    assert result == [
        {"label": "a", "value": "a"},
        {"label": "b", "value": "b"},
        {"label": "c", "value": "c"},
    ]


def test_normalize_dict_list() -> None:
    items: list[dict[str, Any]] = [
        {"label": "Read", "value": "r"},
        {"label": "Write", "value": "w"},
    ]
    result = _normalize_items(items)
    assert result == items


def test_normalize_mixed_str_and_dict() -> None:
    mixed: list[str | dict[str, Any]] = ["a", {"label": "B", "value": 2}]
    result = _normalize_items(mixed)  # type: ignore[arg-type]
    assert result == [
        {"label": "a", "value": "a"},
        {"label": "B", "value": 2},
    ]


def test_normalize_preserves_arbitrary_value_types() -> None:
    """``value`` may be any domain object — the impl just forwards it."""
    sentinel = object()
    items: list[dict[str, Any]] = [{"label": "thing", "value": sentinel}]
    result = _normalize_items(items)
    assert result[0]["value"] is sentinel


def test_normalize_dict_missing_label_raises() -> None:
    with pytest.raises(ValueError, match="label"):
        _normalize_items([{"value": 1}])


def test_normalize_dict_missing_value_raises() -> None:
    with pytest.raises(ValueError, match="label"):
        _normalize_items([{"label": "x"}])


def test_normalize_dict_with_extra_keys_keeps_them() -> None:
    """Extra dict keys are preserved (callers can stash their own metadata)."""
    item = {"label": "x", "value": 1, "extra": "stuff"}
    items: list[dict[str, Any]] = [item]
    result = _normalize_items(items)
    assert result == [item]


def test_normalize_unsupported_type_raises() -> None:
    with pytest.raises(TypeError, match="items"):
        _normalize_items([123])  # type: ignore[arg-type]


def test_normalize_empty_list_returns_empty_list() -> None:
    assert _normalize_items([]) == []


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_externals_init_exports_select_input() -> None:
    from pyink.externals import SelectInput as InitSelectInput

    assert InitSelectInput is SelectInput


def test_select_input_not_in_pyink_top_level() -> None:
    """PRD Decision 5 — externals stay opt-in."""
    import pyink

    assert not hasattr(pyink, "SelectInput"), (
        "SelectInput must NOT be top-level"
    )


# ---------------------------------------------------------------------------
# Initial index clamp
# ---------------------------------------------------------------------------


def test_initial_index_negative_clamps_to_zero() -> None:
    el = SelectInput(items=["a", "b", "c"], initial_index=-5)
    # The clamp happens in the impl, not the factory — props carry the
    # raw value through so users can introspect what they passed.
    assert el.props["initial_index"] == -5


def test_initial_index_past_end_clamps(monkeypatch: pytest.MonkeyPatch) -> None:
    """An out-of-range positive ``initial_index`` settles on the last row.

    We verify by mounting with initial_index=10 (only 3 items) and
    checking the focused row is the last one (its label carries the
    indicator + selected_color).
    """
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"], initial_index=10),
        monkeypatch=monkeypatch,
    )
    assert _wait_for(lambda: "c" in _visible(_frame(inst)))
    # The third row should be the focused one — verify by counting
    # indicator occurrences (only the focused row carries the indicator
    # glyph in single-select mode).
    assert _wait_for(lambda: _visible(_frame(inst)).count("❯") == 1)
    assert _wait_for(lambda: "❯ c" in _visible(_frame(inst)))
    inst.unmount()


def test_initial_index_negative_mounts_focused_on_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"], initial_index=-3),
        monkeypatch=monkeypatch,
    )
    assert _wait_for(lambda: "❯ a" in _visible(_frame(inst)))
    inst.unmount()


# ---------------------------------------------------------------------------
# ArrowUp / ArrowDown focus movement
# ---------------------------------------------------------------------------


def test_arrow_down_moves_focus_down(monkeypatch: pytest.MonkeyPatch) -> None:
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"]),
        monkeypatch=monkeypatch,
        feed=[_DOWN],
    )
    assert _wait_for(lambda: "❯ b" in _visible(_frame(inst)))
    inst.unmount()


def test_arrow_up_moves_focus_up(monkeypatch: pytest.MonkeyPatch) -> None:
    # Start at index 1 (initial), ArrowUp → focus on index 0.
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"], initial_index=1),
        monkeypatch=monkeypatch,
        feed=[_UP],
    )
    assert _wait_for(lambda: "❯ a" in _visible(_frame(inst)))
    inst.unmount()


def test_arrow_up_at_top_is_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    """ArrowUp past the top row stays at the top — no wrap-around."""
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"]),
        monkeypatch=monkeypatch,
        feed=[_UP, _UP],
    )
    assert _wait_for(lambda: "❯ a" in _visible(_frame(inst)))
    inst.unmount()


def test_arrow_down_at_bottom_is_clamped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"]),
        monkeypatch=monkeypatch,
        feed=[_DOWN, _DOWN, _DOWN, _DOWN],
    )
    assert _wait_for(lambda: "❯ c" in _visible(_frame(inst)))
    inst.unmount()


# ---------------------------------------------------------------------------
# j / k vim-style movement
# ---------------------------------------------------------------------------


def test_j_moves_focus_down(monkeypatch: pytest.MonkeyPatch) -> None:
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"]),
        monkeypatch=monkeypatch,
        feed=[b"j"],
    )
    assert _wait_for(lambda: "❯ b" in _visible(_frame(inst)))
    inst.unmount()


def test_k_moves_focus_up(monkeypatch: pytest.MonkeyPatch) -> None:
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"], initial_index=2),
        monkeypatch=monkeypatch,
        feed=[b"k"],
    )
    assert _wait_for(lambda: "❯ b" in _visible(_frame(inst)))
    inst.unmount()


def test_uppercase_J_also_moves(monkeypatch: pytest.MonkeyPatch) -> None:
    """``J`` (Shift+j) moves down too — PRD spec uses ``.lower()`` so case-insensitive."""
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"]),
        monkeypatch=monkeypatch,
        feed=[b"J"],
    )
    assert _wait_for(lambda: "❯ b" in _visible(_frame(inst)))
    inst.unmount()


# ---------------------------------------------------------------------------
# Numeric key jump
# ---------------------------------------------------------------------------


def test_number_key_jumps_to_index(monkeypatch: pytest.MonkeyPatch) -> None:
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c", "d"]),
        monkeypatch=monkeypatch,
        feed=[b"3"],
    )
    assert _wait_for(lambda: "❯ c" in _visible(_frame(inst)))
    inst.unmount()


def test_number_key_out_of_range_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pressing ``9`` when there are only 3 items leaves focus where it is."""
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"]),
        monkeypatch=monkeypatch,
        feed=[b"9"],
    )
    time.sleep(0.2)
    assert "❯ a" in _visible(_frame(inst))
    inst.unmount()


def test_number_zero_is_not_a_jump(monkeypatch: pytest.MonkeyPatch) -> None:
    """``0`` does not map to index -1; the focus stays put."""
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"]),
        monkeypatch=monkeypatch,
        feed=[b"0"],
    )
    time.sleep(0.2)
    assert "❯ a" in _visible(_frame(inst))
    inst.unmount()


# ---------------------------------------------------------------------------
# Enter — single-select on_select
# ---------------------------------------------------------------------------


def test_enter_single_select_fires_on_select_with_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: list[Any] = []
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"], on_select=selected.append),
        monkeypatch=monkeypatch,
        feed=[_DOWN, _ENTER],
    )
    assert _wait_for(lambda: len(selected) == 1)
    inst.unmount()
    assert selected == ["b"]


def test_enter_without_callback_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"]),
        monkeypatch=monkeypatch,
        feed=[_ENTER],
    )
    time.sleep(0.1)
    assert "a" in _visible(_frame(inst))
    inst.unmount()


def test_enter_with_dict_items_passes_value_not_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: list[Any] = []
    items: list[dict[str, Any]] = [
        {"label": "Read", "value": "r"},
        {"label": "Write", "value": "w"},
    ]
    inst, _ = _mount(
        SelectInput(
            items=items,
            initial_index=1,
            on_select=selected.append,
        ),
        monkeypatch=monkeypatch,
        feed=[_ENTER],
    )
    assert _wait_for(lambda: len(selected) == 1)
    inst.unmount()
    assert selected == ["w"]


# ---------------------------------------------------------------------------
# multi_select — Space toggles
# ---------------------------------------------------------------------------


def test_multi_select_space_toggles_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"], multi_select=True),
        monkeypatch=monkeypatch,
        feed=[_SPACE],
    )
    assert _wait_for(lambda: "✓ ❯ a" in _visible(_frame(inst)))
    inst.unmount()


def test_multi_select_space_toggles_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Toggle on, then off — indicator should revert to unselected.
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"], multi_select=True),
        monkeypatch=monkeypatch,
        feed=[_SPACE, _SPACE],
    )
    # Eventually the selected indicator disappears; the focused row
    # remains marked with the focus indicator.
    assert _wait_for(lambda: "  ❯ a" in _visible(_frame(inst)))
    inst.unmount()


def test_multi_select_space_ignored_in_single_select_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``multi_select=False`` (default), ``Space`` is a no-op."""
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"]),
        monkeypatch=monkeypatch,
        feed=[_SPACE],
    )
    time.sleep(0.2)
    # No ✓ in the rendered frame (single-select never shows the check).
    assert "✓" not in _visible(_frame(inst))
    inst.unmount()


def test_multi_select_persists_selection_when_focus_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Toggle on row 0, move down — row 0 keeps its ``✓``."""
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"], multi_select=True),
        monkeypatch=monkeypatch,
        feed=[_SPACE, _DOWN],
    )
    # Row 0 is selected (✓) and no longer focused (space pad for indicator).
    assert _wait_for(lambda: "✓   a" in _visible(_frame(inst)))
    # Row 1 is focused (❯) and unselected (single space for unselected_indicator).
    assert _wait_for(lambda: "  ❯ b" in _visible(_frame(inst)))
    inst.unmount()


def test_multi_select_enter_passes_sorted_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enter on a multi-select fires ``on_select`` with the values in index order."""
    selected: list[Any] = []
    # Move to index 2, toggle it on; move back up to 0, toggle it on;
    # then Enter. The callback should receive ["a", "c"] (sorted by
    # index, not selection order).
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"], multi_select=True, on_select=selected.append),
        monkeypatch=monkeypatch,
        feed=[_DOWN, _DOWN, _SPACE, _UP, _UP, _SPACE, _ENTER],
    )
    assert _wait_for(lambda: len(selected) == 1)
    inst.unmount()
    assert selected == [["a", "c"]]


def test_multi_select_enter_with_no_selection_passes_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: list[Any] = []
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"], multi_select=True, on_select=selected.append),
        monkeypatch=monkeypatch,
        feed=[_ENTER],
    )
    assert _wait_for(lambda: len(selected) == 1)
    inst.unmount()
    assert selected == [[]]


# ---------------------------------------------------------------------------
# on_change — fires only when focus actually moves
# ---------------------------------------------------------------------------


def test_on_change_fires_on_arrow_down(monkeypatch: pytest.MonkeyPatch) -> None:
    changes: list[int] = []
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"], on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=[_DOWN],
    )
    assert _wait_for(lambda: len(changes) == 1)
    inst.unmount()
    assert changes == [1]


def test_on_change_not_fired_on_noop_move(monkeypatch: pytest.MonkeyPatch) -> None:
    """ArrowUp at the top does not fire ``on_change`` (focus didn't move)."""
    changes: list[int] = []
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"], on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=[_UP, _UP],
    )
    time.sleep(0.2)
    assert changes == []
    inst.unmount()


def test_on_change_not_fired_for_space_toggle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Space`` toggles selection but doesn't move focus, so ``on_change`` stays silent."""
    changes: list[int] = []
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"], multi_select=True, on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=[_SPACE],
    )
    time.sleep(0.2)
    assert changes == []
    inst.unmount()


def test_on_change_fires_on_number_jump(monkeypatch: pytest.MonkeyPatch) -> None:
    changes: list[int] = []
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c", "d"], on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=[b"3"],
    )
    assert _wait_for(lambda: changes == [2])
    inst.unmount()


# ---------------------------------------------------------------------------
# is_active=False — keystrokes ignored
# ---------------------------------------------------------------------------


def test_is_active_false_ignores_arrow_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changes: list[int] = []
    inst, _ = _mount(
        SelectInput(
            items=["a", "b", "c"],
            is_active=False,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=[_DOWN, _DOWN, b"j"],
    )
    time.sleep(0.2)
    assert changes == []
    # Focus stays at index 0.
    assert "❯ a" in _visible(_frame(inst))
    inst.unmount()


def test_is_active_false_ignores_enter(monkeypatch: pytest.MonkeyPatch) -> None:
    selected: list[Any] = []
    inst, _ = _mount(
        SelectInput(
            items=["a", "b", "c"],
            is_active=False,
            on_select=selected.append,
        ),
        monkeypatch=monkeypatch,
        feed=[_ENTER],
    )
    time.sleep(0.2)
    assert selected == []
    inst.unmount()


def test_is_active_false_ignores_space_in_multi_select(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(
        SelectInput(
            items=["a", "b", "c"],
            multi_select=True,
            is_active=False,
        ),
        monkeypatch=monkeypatch,
        feed=[_SPACE],
    )
    time.sleep(0.2)
    assert "✓" not in _visible(_frame(inst))
    inst.unmount()


# ---------------------------------------------------------------------------
# Rendering — indicator + colour
# ---------------------------------------------------------------------------


def test_focused_row_carries_indicator(monkeypatch: pytest.MonkeyPatch) -> None:
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"]),
        monkeypatch=monkeypatch,
    )
    # Exactly one row carries the indicator; the others have a space pad.
    assert _wait_for(lambda: "❯ a" in _visible(_frame(inst)))
    assert _wait_for(lambda: "  b" in _visible(_frame(inst)))
    assert _wait_for(lambda: "  c" in _visible(_frame(inst)))
    inst.unmount()


def test_focused_row_carries_selected_color(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"], selected_color="magenta"),
        monkeypatch=monkeypatch,
    )
    # Magenta SGR is \x1b[35m. The focused row "a" should be wrapped.
    assert _wait_for(lambda: "\x1b[35m" in _frame(inst))
    inst.unmount()


def test_color_applied_to_non_focused_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"], color="cyan"),
        monkeypatch=monkeypatch,
    )
    # Cyan SGR is \x1b[36m. At least the non-focused rows carry it.
    assert _wait_for(lambda: "\x1b[36m" in _frame(inst))
    inst.unmount()


def test_custom_indicator_replaces_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"], indicator=">"),
        monkeypatch=monkeypatch,
    )
    assert _wait_for(lambda: "> a" in _visible(_frame(inst)))
    inst.unmount()


def test_custom_indicators_in_multi_select(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(
        SelectInput(
            items=["a", "b", "c"],
            multi_select=True,
            selected_indicator="[x]",
            unselected_indicator="[ ]",
        ),
        monkeypatch=monkeypatch,
        feed=[_SPACE],
    )
    assert _wait_for(lambda: "[x] ❯ a" in _visible(_frame(inst)))
    assert _wait_for(lambda: "[ ]   b" in _visible(_frame(inst)))
    inst.unmount()


def test_all_labels_visible_on_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every item label renders on first paint, not just the focused one."""
    items = ["Apple", "Banana", "Cherry", "Date", "Elderberry"]
    inst, _ = _mount(
        SelectInput(items=items),
        monkeypatch=monkeypatch,
    )
    for label in items:
        target = label

        def predicate(target: str = target) -> bool:
            return target in _visible(_frame(inst))

        assert _wait_for(predicate)
    inst.unmount()


# ---------------------------------------------------------------------------
# Empty list — graceful
# ---------------------------------------------------------------------------


def test_empty_items_mounts_without_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(SelectInput(items=[]), monkeypatch=monkeypatch)
    time.sleep(0.1)
    # Frame is whatever the surrounding terminal paints; the key
    # invariant is "didn't raise".
    assert _frame(inst) is not None
    inst.unmount()


def test_empty_items_keystrokes_are_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pressing keys against an empty list must not raise / mutate state."""
    changes: list[int] = []
    selected: list[Any] = []
    inst, _ = _mount(
        SelectInput(
            items=[],
            on_change=changes.append,
            on_select=selected.append,
        ),
        monkeypatch=monkeypatch,
        feed=[_DOWN, _UP, b"j", b"k", b"1", _SPACE, _ENTER],
    )
    time.sleep(0.3)
    assert changes == []
    assert selected == []
    inst.unmount()


# ---------------------------------------------------------------------------
# box_props forwarding
# ---------------------------------------------------------------------------


def test_box_props_forwarded_to_wrapping_box(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``padding`` / ``borderStyle`` reach the rendered Box."""
    inst, _ = _mount(
        SelectInput(items=["a", "b", "c"], borderStyle="round", padding=1),
        monkeypatch=monkeypatch,
    )
    # Round border corners use ╭ / ╮ / ╰ / ╯ glyphs.
    assert _wait_for(lambda: "╭" in _frame(inst))
    inst.unmount()
