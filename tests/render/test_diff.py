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
    # Each row is pre-cleared with ``\r\x1b[2K`` (first row) /
    # ``\n\x1b[2K`` (subsequent rows) so shorter new rows don't leave
    # stale tails from a previous frame (Jarvis TUI regression fix).
    # Bottom-parked convention (07-19): after the last row the cursor
    # stays on that row — a single ``\r`` parks it at column 1. No
    # cursor-up retreat back to row 0.
    assert out == "\r\x1b[2Khello\n\x1b[2Kworld\r"
    assert _CLEAR_SCREEN not in out


def test_initial_paint_single_row_no_cursor_up() -> None:
    out = _capture(None, "single")
    # Single row: leading ``\r\x1b[2K`` + content + ``\r``. No cursor
    # move because the first row IS the last row.
    assert out == "\r\x1b[2Ksingle\r"
    assert _CLEAR_SCREEN not in out


# ---------------------------------------------------------------------------
# Initial paint — row pre-clear regression (Jarvis TUI Phase B/C bugs)
#
# Root cause: ``_paint_initial`` used to do a bare ``stdout.write(new_frame)``
# without any ``\x1b[2K``. When a repaint overwrote a previous frame whose
# row was LONGER than the new row at the same position, the old row's tail
# bled through, producing "duplicate hint" / "thinking overflow" /
# "double status_bar" visual artifacts in the Jarvis TUI under Phase C1's
# high-frequency spinner repaints.
#
# Fix: pre-clear every row of the new frame before writing its content.
# These tests pin that contract.
# ---------------------------------------------------------------------------


def test_initial_paint_clears_every_row() -> None:
    """Multi-row frame: each row gets its own ``\\x1b[2K``."""
    out = _capture(None, "r0\nr1\nr2")
    # First row gets ``\r\x1b[2K``, subsequent rows get ``\n\x1b[2K``.
    # Total clears == number of rows.
    assert out.count("\x1b[2K") == 3
    assert _CLEAR_SCREEN not in out


def test_initial_paint_single_row_has_leading_cr_and_clear() -> None:
    """Single-row frame: ``\\r\\x1b[2K`` precedes the content."""
    out = _capture(None, "only")
    assert out.startswith("\r\x1b[2Konly")
    assert _CLEAR_SCREEN not in out


def test_initial_paint_empty_frame_does_not_crash() -> None:
    """Empty ``new_frame`` (no content) must still emit a valid sequence.

    ``"".split("\\n")`` returns ``[""]`` (length 1), so we emit
    ``\\r\\x1b[2K`` + "" + ``\\r`` — clearing the single empty row and
    parking the cursor. No cursor-up because there's only one row.
    """
    out = _capture(None, "")
    assert out == "\r\x1b[2K\r"
    assert _CLEAR_SCREEN not in out


def test_initial_paint_content_preserved_between_clears() -> None:
    """The frame body is still written verbatim between the clear sequences."""
    out = _capture(None, "alpha\nbeta\ngamma")
    # All three row contents must appear in order.
    assert "alpha" in out
    assert "beta" in out
    assert "gamma" in out
    # And they must follow their respective ``\x1b[2K`` clears.
    assert "\r\x1b[2Kalpha" in out
    assert "\n\x1b[2Kbeta" in out
    assert "\n\x1b[2Kgamma" in out
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
    # Bottom-parked: the cursor starts on the last row (row 2), moves UP
    # to the changed row 1, clears + rewrites it, then returns DOWN to
    # the last row.
    assert out == "\x1b[1A\r\x1b[2KBETA\x1b[1B\r"


def test_first_row_change_moves_up_from_bottom() -> None:
    old = "alpha\nbeta"
    new = "ALPHA\nbeta"
    out = _capture(old, new)
    # First row changed: from the parked last row the diff climbs 1,
    # rewrites, then descends back to the last row.
    assert out == "\x1b[1A\r\x1b[2KALPHA\x1b[1B\r"
    assert _CLEAR_SCREEN not in out


def test_last_row_change() -> None:
    old = "a\nb\nc"
    new = "a\nb\nC"
    out = _capture(old, new)
    # Bottom-parked micro-move: the changed row IS the cursor's parked
    # row, so the whole repaint is a bare clear + rewrite + CR with no
    # cursor travel at all. This is the common case (typing in the
    # input row) that 07-19 optimises for.
    assert out == "\r\x1b[2KC\r"
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
# When ``available_rows`` is provided, ``_repaint`` must cap cursor-UP
# movements so the cursor never climbs into the scrollback (where it
# would clamp at the viewport's top edge). Without the cap, a clamped
# cursor-down retreat at the end of the diff overshoots past the frame's
# last row and the next paint anchors at the wrong y — wiping live
# content (input row, dividers). See ``diff.py`` docstring for the full
# rationale.
# ---------------------------------------------------------------------------


def test_frame_shrink_with_available_rows_caps_cursor_ascent() -> None:
    """When ``available_rows`` is set, the cursor never climbs above the
    reachable floor (``len(old) - available_rows``)."""
    # Old frame is 10 rows; new is 2. With ``available_rows=5`` the
    # cursor (parked on old row 9) may climb at most 4 rows, so rows
    # 9..5 are cleared and rows 0..4 — already in the scrollback — are
    # skipped. The total ascent must not exceed 4.
    old = "\n".join(["row%d" % i for i in range(10)])
    new = "row0\nrow1"
    out = StringIO()
    write_diff(old, new, out, available_rows=5)
    diff = out.getvalue()
    assert _CLEAR_SCREEN not in diff
    import re
    ups = [int(n) for n in re.findall(r"\x1b\[(\d+)A", diff)]
    assert ups, "expected at least one cursor-up in frame-shrink diff"
    # Total upward travel from the parked last row (index 9) is capped
    # at available_rows - 1 = 4 — the cursor stops at row 5 and never
    # climbs into the scrollback.
    assert sum(ups) <= 4, (
        f"cursor-up total {sum(ups)} exceeds available_rows-1=4: {diff!r}"
    )
    # No single large jump either — the walk is row-by-row.
    assert max(ups) <= 4
    # Rows in the scrollback (indices 0..4) are left uncleared: exactly
    # 5 rows (9..5) get erased.
    assert diff.count("\x1b[2K") == 5


def test_frame_shrink_without_available_rows_keeps_legacy_behaviour() -> None:
    """``available_rows=None`` (default) preserves the original full walk."""
    old = "\n".join(["row%d" % i for i in range(10)])
    new = "row0\nrow1"
    out = StringIO()
    write_diff(old, new, out, available_rows=None)
    diff = out.getvalue()
    # The un-capped path climbs from old row 9 up to row 1 (the new
    # frame's last row): 7 climbs across the erased rows (9→2) plus the
    # final retreat to row 1 — 8 rows of total ascent, mirrored from the
    # legacy top-down walk's descent. Net movement equals
    # ``old_last_row - new_last_row`` = 9 - 1 = 8. Backward compat with
    # existing diff callers that don't pass viewport info.
    import re
    ups = [int(n) for n in re.findall(r"\x1b\[(\d+)A", diff)]
    assert sum(ups) == 8, f"uncapped path should ascend 8 rows total, got {sum(ups)}: {diff!r}"
    assert _CLEAR_SCREEN not in diff


def test_frame_shrink_ends_cursor_at_new_frame_last_row() -> None:
    """After a frame-shrink diff the cursor must park on the NEW frame's
    last row so the next paint anchors correctly. Net vertical movement
    therefore equals ``old_height - new_height`` upward (not zero — the
    park point moved). This holds whether or not ``available_rows`` is
    provided."""
    old = "a\nb\nc\nd\ne"
    new = "a\nb"
    height_delta = 5 - 2  # 3 rows
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
        # The cursor starts on the old frame's last row and ends on the
        # new frame's last row: net movement = height_delta upward. If
        # this invariant breaks, the next diff anchors at the wrong y
        # and drifts until live content is wiped.
        assert total_up - total_down == height_delta, (
            f"{label}: cursor net movement != height delta "
            f"(down={total_down}, up={total_up}, expected net up={height_delta}, "
            f"diff={diff!r})"
        )


def test_repaint_frame_shrink_keeps_top_origin() -> None:
    """Shrinking via ``repaint_frame`` must not bottom-align.

    Bottom-align (cursor-down by shrink before paint) left a hollow
    band above Jarvis' input that grew by the command-list height on
    every ``/`` → Esc cycle. Top-align keeps the paint origin stable
    so the next grow reuses the same footprint.
    """
    from ink.render.diff import repaint_frame

    old = "\n".join(f"row{i}" for i in range(8))
    new = "a\nb\nc"  # shrink by 5
    out = StringIO()
    repaint_frame(old, new, out, available_rows=8)
    diff = out.getvalue()
    assert _CLEAR_SCREEN not in diff
    # After erase the cursor is already on the footprint's first row;
    # do not emit a shrink cursor-down before painting.
    assert "\x1b[5B" not in diff, f"unexpected bottom-align in {diff!r}"
    assert "\r\x1b[2Ka" in diff


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
