"""Tests for :mod:`ink.render.diff` — frame-level inline diff (PR5).

The diff module emits cursor-move + line-clear sequences to repaint only
the rows that actually changed. We never use ``\\x1b[2J`` (full-screen
clear) — that would destroy scrollback (PRD Decision 3). Every test in
this file grep-asserts that invariant.
"""

from __future__ import annotations

from io import StringIO

from ink.render.diff import write_diff

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
# Frame-shrink cursor retreat — viewport-clamp robustness
#
# When ``available_rows`` is provided, ``_repaint`` must cap cursor-down
# movements so the cursor never reaches a row the terminal will clamp.
# Without the cap, a clamped cursor-up at the end of the diff overshoots
# past frame row 0 and the next paint anchors at the wrong y — wiping
# live content (input row, dividers). See ``diff.py`` docstring for the
# full rationale.
# ---------------------------------------------------------------------------


def test_frame_shrink_with_available_rows_caps_cursor_descent() -> None:
    """When ``available_rows`` is set, the cursor never moves past that row."""
    # Old frame is 10 rows; new is 2. Without a cap the diff would emit
    # a ``\x1b[9B`` (down to row 9) — with ``available_rows=5`` it must
    # stop at row 4 and clear only rows 2-4. The cursor-up at the end
    # retreats from row 4 (not row 9), so the next paint anchors at the
    # right y.
    old = "\n".join(["row%d" % i for i in range(10)])
    new = "row0\nrow1"
    out = StringIO()
    write_diff(old, new, out, available_rows=5)
    diff = out.getvalue()
    assert _CLEAR_SCREEN not in diff
    # No cursor-down by more than 4 rows (cap - 1) — a single ``\x1b[4B``
    # is the largest down-move we should see. ``\x1b[9B`` (the un-capped
    # descent to the bottom of the old frame) must NOT appear.
    assert "\x1b[9B" not in diff
    assert "\x1b[5B" not in diff
    # The largest down-move in the diff should be at most 4 rows.
    import re
    downs = [int(n) for n in re.findall(r"\x1b\[(\d+)B", diff)]
    assert downs, "expected at least one cursor-down in frame-shrink diff"
    assert max(downs) <= 4, f"cursor-down {max(downs)} exceeds available_rows-1=4: {diff!r}"


def test_frame_shrink_without_available_rows_keeps_legacy_behaviour() -> None:
    """``available_rows=None`` (default) preserves the original top-down walk."""
    old = "\n".join(["row%d" % i for i in range(10)])
    new = "row0\nrow1"
    out = StringIO()
    write_diff(old, new, out, available_rows=None)
    diff = out.getvalue()
    # The un-capped path reaches the bottom (row 9) of the old frame via
    # a descent to row 2 then 7× cursor-down-by-1. The total descent
    # equals 9 rows — backward compat with existing diff callers that
    # don't pass viewport info.
    import re
    downs = [int(n) for n in re.findall(r"\x1b\[(\d+)B", diff)]
    assert sum(downs) == 9, f"uncapped path should descend 9 rows total, got {sum(downs)}: {diff!r}"
    assert _CLEAR_SCREEN not in diff


def test_frame_shrink_ends_cursor_at_frame_row_zero() -> None:
    """After a frame-shrink diff, the cursor must park at row 0 of the
    painted region so the next paint anchors correctly. This holds whether
    or not ``available_rows`` is provided."""
    old = "a\nb\nc\nd\ne"
    new = "a\nb"
    # Without cap
    out1 = StringIO()
    write_diff(old, new, out1, available_rows=None)
    diff1 = out1.getvalue()
    # With cap
    out2 = StringIO()
    write_diff(old, new, out2, available_rows=4)
    diff2 = out2.getvalue()
    import re
    for label, diff in [("uncapped", diff1), ("capped", diff2)]:
        downs = [int(n) for n in re.findall(r"\x1b\[(\d+)B", diff)]
        ups = [int(n) for n in re.findall(r"\x1b\[(\d+)A", diff)]
        total_down = sum(downs)
        total_up = sum(ups)
        # After all cursor moves, the net vertical movement is 0 (cursor
        # returns to row 0 of the frame). If this invariant breaks, the
        # next diff anchors at the wrong y and drifts until live content
        # is wiped.
        assert total_down == total_up, (
            f"{label}: cursor net drift non-zero "
            f"(down={total_down}, up={total_up}, diff={diff!r})"
        )


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
