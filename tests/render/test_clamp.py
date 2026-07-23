"""Tests for :func:`ink.render.render` terminal-size clamp.

Bug context: when a caller hard-codes ``rows=30`` (or ``columns=…``)
but the real terminal is shorter, the oversized frame overflows the
screen, the terminal scrolls, and the inline repaint's relative
cursor-up math (which assumes every painted row is still on-screen)
lands on the wrong rows — corrupting every subsequent frame.

The fix lives in :func:`ink.render.render`: the caller-supplied
viewport size is clamped to the real terminal size detected via
:func:`shutil.get_terminal_size`. The clamp only fires when stdout is
a real TTY — a non-TTY stream (CI, tests driving PyInk through
:class:`io.StringIO`) cannot scroll, so the caller's explicit
viewport is trustworthy.

Each test monkey-patches the size-detection seam
(:func:`ink.render.pipeline._detect_terminal_size`) and the TTY
check (:func:`ink.render.pipeline._stdout_is_tty`) so the clamp
logic is deterministic and does not depend on the CI runner's actual
terminal dimensions.
"""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest

from ink import Text, render
from ink.render import pipeline


@pytest.fixture
def fake_tty_terminal_20x20(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin detected terminal size to 20×20 and report stdout as a TTY.

    Patches ``shutil.get_terminal_size`` so both the mount-time
    detection (``pipeline._detect_terminal_size``) and the per-paint
    detection (``Terminal.get_size`` / ``Instance._resolve_columns``)
    see the same fake size — required because ``use_window_size``
    re-reads the live signal on every access, and the signal is
    written from ``_paint_now`` which goes through ``_resolve_columns``.
    """

    import os as _os
    import shutil as _shutil

    _fake_size_obj = _os.terminal_size((20, 20))

    def _fake_get_terminal_size(*args, **kwargs):
        return _fake_size_obj

    def _fake_size() -> tuple[int, int]:
        return 20, 20

    def _fake_is_tty(_stdout: object) -> bool:
        return True

    monkeypatch.setattr(_shutil, "get_terminal_size", _fake_get_terminal_size)
    monkeypatch.setattr(pipeline, "_detect_terminal_size", _fake_size)
    monkeypatch.setattr(pipeline, "_stdout_is_tty", _fake_is_tty)
    yield


@pytest.fixture
def fake_non_tty_terminal_20x20(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Pin detected terminal size to 20×20 but report stdout as non-TTY.

    This mirrors the CI / captured-stdout path: the clamp must not fire
    on a non-TTY stream because there's no scrollback to corrupt.
    """

    import os as _os
    import shutil as _shutil

    _fake_size_obj = _os.terminal_size((20, 20))

    def _fake_get_terminal_size(*args, **kwargs):
        return _fake_size_obj

    def _fake_size() -> tuple[int, int]:
        return 20, 20

    def _fake_is_tty(_stdout: object) -> bool:
        return False

    monkeypatch.setattr(_shutil, "get_terminal_size", _fake_get_terminal_size)
    monkeypatch.setattr(pipeline, "_detect_terminal_size", _fake_size)
    monkeypatch.setattr(pipeline, "_stdout_is_tty", _fake_is_tty)
    yield


def _make_inst(
    *,
    columns: int | None = None,
    rows: int | None = None,
) -> tuple[object, io.StringIO]:
    out = io.StringIO()
    inst = render(
        Text("x"),
        stdout=out,
        stdin=io.StringIO(),
        columns=columns,
        rows=rows,
        exit_on_ctrl_c=False,
    )
    return inst, out


def test_render_clamps_rows_to_terminal_size(
    fake_tty_terminal_20x20: None,
) -> None:
    """Caller asks for 30 rows on a 20-row terminal → frame is clamped to 20."""
    inst, _ = _make_inst(columns=20, rows=30)
    try:
        # Instance.rows reflects the clamped value used for layout.
        assert inst.rows == 20  # type: ignore[attr-defined]
        # RenderOptions also carries the clamped value so subsequent
        # paints (including resize-driven repaints) keep honouring it.
        assert inst.options.rows == 20  # type: ignore[attr-defined]
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_render_clamps_columns_to_terminal_size(
    fake_tty_terminal_20x20: None,
) -> None:
    """Caller asks for 40 cols on a 20-col terminal → frame is clamped to 20."""
    inst, _ = _make_inst(columns=40, rows=20)
    try:
        assert inst.columns == 20  # type: ignore[attr-defined]
        assert inst.options.columns == 20  # type: ignore[attr-defined]
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_render_default_uses_terminal_size(
    fake_tty_terminal_20x20: None,
) -> None:
    """No explicit viewport → use the detected terminal size."""
    inst, _ = _make_inst()
    try:
        assert inst.columns == 20  # type: ignore[attr-defined]
        assert inst.rows == 20  # type: ignore[attr-defined]
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_render_rows_below_terminal_unchanged(
    fake_tty_terminal_20x20: None,
) -> None:
    """Caller asks for 10 rows on a 20-row terminal → 10 is honoured.

    The clamp must not inflate a caller's explicit "I want a smaller
    viewport" request up to the terminal size; only oversized values
    are clamped down.
    """
    inst, _ = _make_inst(columns=15, rows=10)
    try:
        assert inst.rows == 10  # type: ignore[attr-defined]
        assert inst.options.rows == 10  # type: ignore[attr-defined]
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_render_columns_below_terminal_unchanged(
    fake_tty_terminal_20x20: None,
) -> None:
    """Caller asks for 10 cols on a 20-col terminal → 10 is honoured."""
    inst, _ = _make_inst(columns=10, rows=15)
    try:
        assert inst.columns == 10  # type: ignore[attr-defined]
        assert inst.options.columns == 10  # type: ignore[attr-defined]
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_render_clamp_clamps_both_dimensions_together(
    fake_tty_terminal_20x20: None,
) -> None:
    """Clamp fires on both rows and columns when both are oversized."""
    inst, _ = _make_inst(columns=100, rows=100)
    try:
        assert inst.columns == 20  # type: ignore[attr-defined]
        assert inst.rows == 20  # type: ignore[attr-defined]
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_render_does_not_clamp_when_stdout_is_not_a_tty(
    fake_non_tty_terminal_20x20: None,
) -> None:
    """Non-TTY stdout (CI / captured) → caller-supplied viewport honoured.

    The scroll-corruption bug the clamp guards against is a TTY-only
    artefact; a captured stream has no scrollback to corrupt, so the
    caller's explicit ``rows=30`` must survive untouched.
    """
    inst, _ = _make_inst(columns=40, rows=30)
    try:
        assert inst.columns == 40  # type: ignore[attr-defined]
        assert inst.rows == 30  # type: ignore[attr-defined]
        assert inst.options.columns == 40  # type: ignore[attr-defined]
        assert inst.options.rows == 30  # type: ignore[attr-defined]
    finally:
        inst.unmount()  # type: ignore[attr-defined]
