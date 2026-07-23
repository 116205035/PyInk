"""Tests for :func:`ink.hooks.use_window_size` (PR6)."""

from __future__ import annotations

import io

import pytest

from ink import Text, create_element, render, use_window_size
from ink.core.element import Element
from ink.hooks.window_size import WindowSize


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 99


def test_use_window_size_returns_initial_size() -> None:
    size_box: dict[str, WindowSize] = {}

    def Comp() -> Element:
        size = use_window_size()
        size_box["size"] = size
        return Text("hi")

    inst = render(
        create_element(Comp),
        stdout=_FakeTTY(),
        stdin=_FakeTTY(),
        columns=42,
        rows=10,
        exit_on_ctrl_c=False,
    )
    size = size_box["size"]
    assert size.columns == 42
    assert size.rows == 10
    inst.unmount()


def test_use_window_size_returns_snapshot_columns_rows() -> None:
    size_box: dict[str, WindowSize] = {}

    def Comp() -> Element:
        size = use_window_size()
        size_box["size"] = size
        return Text("hi")

    inst = render(
        create_element(Comp),
        stdout=_FakeTTY(),
        stdin=_FakeTTY(),
        columns=80,
        rows=24,
        exit_on_ctrl_c=False,
    )
    size = size_box["size"]
    assert size.columns == 80
    assert size.rows == 24
    inst.unmount()


def test_use_window_size_outside_render_falls_back() -> None:
    size = use_window_size()
    assert size.columns >= 1
    assert size.rows >= 1


def test_window_size_columns_is_readonly() -> None:
    """Assigning to ``.columns`` / ``.rows`` must raise ``AttributeError``.

    Carries the legacy frozen-dataclass contract forward now that
    ``WindowSize`` is a property-based class — callers can't mutate the
    size, only read it.
    """
    size = use_window_size()
    with pytest.raises(AttributeError):
        size.columns = 0  # type: ignore[misc]
    with pytest.raises(AttributeError):
        size.rows = 0  # type: ignore[misc]


def test_use_window_size_updates_after_signal_write() -> None:
    """A captured ``WindowSize`` reflects signal updates without remount.

    Simulates the resize → ``_paint_now`` → signal-write path by
    writing ``inst._size_signal.value`` directly (what ``_paint_now``
    does on every paint, including the resize-triggered one). The
    proxy must read the fresh value on the next ``.columns`` access —
    not the value that was current when ``use_window_size()`` was
    called at mount time.
    """
    size_box: dict[str, WindowSize] = {}

    def Comp() -> Element:
        size = use_window_size()
        size_box["size"] = size
        return Text("hi")

    # Mount WITHOUT options.columns / options.rows override so the
    # proxy reads the live signal instead of the options pin.
    inst = render(
        create_element(Comp),
        stdout=_FakeTTY(),
        stdin=_FakeTTY(),
        exit_on_ctrl_c=False,
    )
    try:
        captured = size_box["size"]
        initial_cols = captured.columns
        initial_rows = captured.rows

        # Simulate the signal write that ``_paint_now`` performs at
        # the end of a resize-triggered paint.
        inst._size_signal.value = (initial_cols + 100, initial_rows + 50)

        assert captured.columns == initial_cols + 100
        assert captured.rows == initial_rows + 50
    finally:
        inst.unmount()
