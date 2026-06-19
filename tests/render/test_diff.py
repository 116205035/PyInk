"""Tests for :mod:`pyink.render.diff` — frame-level inline diff (PR5).

The diff module emits cursor-move + line-clear sequences to repaint only
the rows that actually changed. We never use ``\\x1b[2J`` (full-screen
clear) — that would destroy scrollback (PRD Decision 3). Every test in
this file grep-asserts that invariant.
"""

from __future__ import annotations

from io import StringIO

from pyink.render.diff import write_diff

#: Forbidden sequence — never allowed in inline mode (PRD Decision 3).
_CLEAR_SCREEN = "\x1b[2J"


def _capture(old: str | None, new: str) -> str:
    out = StringIO()
    write_diff(old, new, out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Initial paint
# ---------------------------------------------------------------------------


def test_initial_paint_writes_frame_then_parks_cursor() -> None:
    out = _capture(None, "hello\nworld")
    # The frame itself...
    assert "hello\nworld" in out
    # ...followed by a cursor-up to the first row + CR.
    assert out.endswith("\x1b[1A\r")
    assert _CLEAR_SCREEN not in out


def test_initial_paint_single_row_no_cursor_up() -> None:
    out = _capture(None, "single")
    assert out == "single\r"
    assert _CLEAR_SCREEN not in out


# ---------------------------------------------------------------------------
# Identical frames emit nothing
# ---------------------------------------------------------------------------


def test_identical_frames_emit_nothing() -> None:
    out = _capture("a\nb", "a\nb")
    assert out == ""
    assert _CLEAR_SCREEN not in out


# ---------------------------------------------------------------------------
# Single-line change
# ---------------------------------------------------------------------------


def test_single_line_change_only_touches_that_row() -> None:
    old = "alpha\nbeta\ngamma"
    new = "alpha\nBETA\ngamma"
    out = _capture(old, new)
    assert "BETA" in out
    # The unchanged rows must NOT be rewritten.
    assert "alpha" not in out
    assert "gamma" not in out
    assert _CLEAR_SCREEN not in out
    # We expect: move down to row 1, CR + erase-line + new content,
    # then back up to row 0.
    assert "\x1b[1B" in out  # cursor down 1
    assert "\r\x1b[2KBETA" in out
    assert out.endswith("\x1b[1A\r")


def test_first_row_change_does_not_move_down() -> None:
    old = "alpha\nbeta"
    new = "ALPHA\nbeta"
    out = _capture(old, new)
    # First row → no down move; rewrite + return to row 0.
    assert out.startswith("\r\x1b[2KALPHA")
    assert _CLEAR_SCREEN not in out


def test_last_row_change() -> None:
    old = "a\nb\nc"
    new = "a\nb\nC"
    out = _capture(old, new)
    assert "\x1b[2B" in out  # down to row 2
    assert "\r\x1b[2KC" in out
    assert out.endswith("\x1b[2A\r")
    assert _CLEAR_SCREEN not in out


# ---------------------------------------------------------------------------
# Multi-row change (consecutive)
# ---------------------------------------------------------------------------


def test_multi_consecutive_row_change() -> None:
    old = "r0\nr1\nr2\nr3"
    new = "R0\nR1\nr2\nr3"
    out = _capture(old, new)
    assert "R0" in out
    assert "R1" in out
    # r2/r3 unchanged.
    assert "r2" not in out
    assert "r3" not in out
    assert _CLEAR_SCREEN not in out


# ---------------------------------------------------------------------------
# Row count changes
# ---------------------------------------------------------------------------


def test_new_frame_has_more_rows_appends_them() -> None:
    old = "a\nb"
    new = "a\nb\nc\nd"
    out = _capture(old, new)
    # Rows 2 and 3 are "appended" — they didn't exist in old. We move
    # down to each, erase the (empty) line, write content.
    assert "c" in out
    assert "d" in out
    assert _CLEAR_SCREEN not in out


def test_new_frame_has_fewer_rows_clears_leftover() -> None:
    old = "a\nb\nc\nd"
    new = "a\nb"
    out = _capture(old, new)
    # Rows 2/3 are cleared (old "c"/"d" disappear). The cleared rows
    # don't contribute content; only an erase-line + CR.
    assert "c" not in out
    assert "d" not in out
    # We do expect the erase-line sequence to appear at least twice for
    # the removed rows.
    assert out.count("\x1b[2K") >= 2
    assert _CLEAR_SCREEN not in out


def test_full_clear_uses_line_clears_not_full_screen_clear() -> None:
    out = _capture("a\nb\nc", "")
    assert _CLEAR_SCREEN not in out
    # Three rows cleared.
    assert out.count("\x1b[2K") == 3


# ---------------------------------------------------------------------------
# Invariant sweep — every public test path
# ---------------------------------------------------------------------------


def test_no_2j_across_all_cases() -> None:
    """Sanity sweep — never emit ``\\x1b[2J`` from any diff path."""
    cases: list[tuple[str | None, str]] = [
        (None, "x"),
        ("a", "a"),
        ("a\nb\nc", "a\nB\nc"),
        ("a", "a\nb\nc"),
        ("a\nb\nc", "a"),
        ("a\nb\nc", ""),
    ]
    for old, new in cases:
        out = _capture(old, new)
        assert _CLEAR_SCREEN not in out, f"2J leaked for case {(old, new)!r}"
