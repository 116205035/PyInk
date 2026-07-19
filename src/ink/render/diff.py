"""Frame-level diff — only repaint rows that actually changed (PR5).

Inline mode (PRD Decision 3) requires the renderer to never destroy the
shell's scrollback history. ``\\x1b[2J`` (clear screen) is therefore
banned; instead we move the cursor to each changed row, clear it with
``\\x1b[2K`` and rewrite it, then return the cursor to the bottom of the
painted region.

Algorithm choice for PR5: **simple line-by-line comparison**. We do not
run a Myers diff to align line insertions/deletions because (a) ink
itself uses the same approach in its MVP, (b) our output rows are
deliberately the layout engine's grid rows — there's no real "line
insertion in the middle" scenario unless the tree itself changes shape,
and (c) a Myers diff would add ~300 LOC for marginal savings. The
trade-off is documented here so a future PR can revisit it.

Cursor convention (bottom-parked, 07-19-input-pyink-cursor-markdown):
after every :func:`write_diff` (and after the initial paint) the cursor
sits on **column 1 of the LAST row** of the painted region (the bottom
of the live frame). This mirrors Claude Code's ink renderer, which parks
``cursor.y = screen.height``. Two practical wins:

* The rows that change most often (input prompt, spinner, status bar)
  live at the BOTTOM of the frame, so a typical repaint is a 1-2 row
  micro-move instead of a full-height CUD/CUU round trip.
* The cursor never approaches the viewport's top edge during normal
  repaints. Large cursor-up excursions into (or toward) the scrollback
  are what trigger Windows Terminal's viewport-yank bug
  (``microsoft/terminal#14774``) — the "user scrolls up, next repaint
  yanks the viewport back to the cursor" symptom. Bottom-parked micro
  moves stay far from that trigger.

Frame-shrink / overflow cursor safety (PR for 07-07-tui-palette-layout-bug,
direction inverted in 07-19): when the frame is taller than the viewport
its top rows live in the scrollback. Cursor-up moves toward those rows
clamp at the viewport's top edge; a retreat computed from the INTENDED
row count (rather than the actual clamped position) then overshoots
downward past the frame's last row, so the next paint anchors at the
wrong y and drifts until live content (input prompt, dividers) is wiped
— the "input row disappears after Esc" bug, mirrored.

The fix caps upward travel via ``available_rows`` (passed in from the
caller when known): from the frame's last row the cursor may climb at
most ``available_rows - 1`` rows. Rows higher than that have scrolled
into the scrollback and are skipped (leaving them uncleared is the safe
default — the same policy the pre-inversion code applied to rows below
the viewport bottom). ``available_rows`` is computed by
:class:`Instance` as the number of frame rows actually visible on
screen (``min(rows, frame_h)`` on overflow, else
``viewport_rows - static_rows_above``) — an approximation that errs on
the generous side (it counts ``\\n`` characters in flushed static text,
ignoring eager-wrap extra rows).

Public API: :func:`write_diff`. Everything else is internal.
"""

from __future__ import annotations

from typing import TextIO

__all__ = ["write_diff", "repaint_frame"]

# When the live frame shrinks by this many rows or more, Instance._paint_now
# routes through repaint_frame() (full erase + repaint) instead of write_diff()
# (incremental). Below this threshold the incremental path is good enough —
# small height changes (1-5 rows) don't trigger the cursor-drift bug.
#
# The threshold is currently advisory: Instance._paint_now intercepts every
# height_delta >= 1 (conservative). When a future task wants to optimize
# small-height diff performance, raise the Instance threshold to this value.
_SHRINK_FULL_REPAINT_THRESHOLD = 6


def _emit_cursor_move(
    out: list[str], target: int, cur_row: int, row_floor: int,
) -> int:
    """Clamp ``target`` to ``row_floor``, emit cursor-up/down into ``out``.

    Returns the new ``cur_row`` (equal to the clamped target). Shared by
    ``_repaint`` and ``_erase_reachable_rows`` so cursor-move logic lives
    in exactly one place — duplicated cursor-drift fixes were the original
    bug source.

    ``row_floor`` is the minimum row index the cursor may climb to from
    the frame's bottom (rows above the floor have scrolled into the
    scrollback; cursor-up moves toward them would clamp at the viewport's
    top edge). Downward targets are never clamped here: the cursor only
    descends back toward its parked row, which is always on-screen.
    """
    target = max(target, row_floor)
    delta = target - cur_row
    if delta > 0:
        out.append("\x1b[" + str(delta) + "B")
    elif delta < 0:
        out.append("\x1b[" + str(-delta) + "A")
    return target


def write_diff(
    old_frame: str | None,
    new_frame: str,
    stdout: TextIO,
    available_rows: int | None = None,
) -> None:
    """Write the difference between ``old_frame`` and ``new_frame`` to stdout.

    ``old_frame`` is ``None`` on the very first render — the new frame is
    written verbatim and the cursor stays parked at column 1 of the LAST
    row of the painted region. On subsequent renders we walk every row,
    clear+rewrite only those that differ, append any extra rows in
    ``new_frame``, and finally return the cursor to the new frame's last
    row.

    ``available_rows`` is the number of terminal rows the frame region
    actually occupies on screen (``min(rows, frame_h)`` once the paint
    has scrolled, else ``viewport_rows - static_rows_above``). When
    provided, the diff caps cursor-UP movements so they never climb past
    the viewport top into the scrollback — preventing the cursor-drift
    bug where a clamped cursor-down retreat overshoots the frame's last
    row and the next paint anchors at the wrong y, eventually wiping
    live content (input row, dividers). ``None`` disables the cap
    (legacy behaviour, preserved for backward compatibility with the
    diff unit tests).

    The function never writes ``\\x1b[2J`` (full-screen clear) — that
    destroys scrollback (PRD Decision 3).
    """
    if old_frame is None:
        _paint_initial(new_frame, stdout)
        return

    if old_frame == new_frame:
        return

    _repaint(old_frame, new_frame, stdout, available_rows)


def repaint_frame(
    old_frame: str,
    new_frame: str,
    stdout: TextIO,
    available_rows: int | None = None,
) -> None:
    """Erase ``old_frame`` then paint ``new_frame``.

    Used when the live frame height changes enough (e.g. palette open /
    close) that incremental ``write_diff`` would leave unreachable tail
    rows uncleared or park the cursor at the wrong y.

    Shrink is **top-aligned**: the new frame is painted at the same
    origin as the erased footprint. The erase walks UPWARD from the
    frame's last row (where the cursor is parked) clearing each
    reachable row, and ends on the topmost reachable row — row 0 of the
    footprint when the whole frame is on-screen. ``_paint_initial`` then
    repaints from there and parks on the new frame's last row.
    Bottom-aligning the paint (cursor-down by ``old_h - new_h`` before
    paint) looked better for a single collapse — content sat on the old
    bottom edge — but left a hollow band *above* the chrome. On repeated
    palette open/Esc that band grew by the picker height each cycle,
    because the paint origin drifted down and never reclaimed the gap.
    Top-align keeps the origin stable so the next grow fills the same
    footprint instead of stacking gaps.
    """
    if old_frame:
        old_lines = old_frame.split("\n")
        budget = (
            available_rows
            if (available_rows and available_rows > 0)
            else len(old_lines)
        )
        _erase_reachable_rows(old_lines, stdout, budget)
    if new_frame:
        _paint_initial(new_frame, stdout)
    elif old_frame:
        stdout.write("\r")


def _paint_initial(new_frame: str, stdout: TextIO) -> None:
    """Write ``new_frame`` and park the cursor at its bottom-left.

    Each row is pre-cleared with ``\\x1b[2K`` so that when the new row is
    shorter than the previous frame's row at the same position, the old
    tail doesn't bleed through. This was the root cause of Jarvis TUI's
    "duplicate hint" / "thinking overflow" / "double status_bar" bugs
    triggered by Phase C1's high-frequency spinner repaints:
    ``repaint_frame`` routes height changes through
    ``_erase_reachable_rows`` (whose budget is capped by
    ``available_rows``) + ``_paint_initial``. When the budget is smaller
    than the old frame, the top old rows stay uncleared, and the previous
    bare ``write(new_frame)`` left stale tails whenever a new row was
    shorter than its predecessor.

    Sequence shape::

        \\r\\x1b[2K<line0>\\n\\x1b[2K<line1>\\n\\x1b[2K<line2>...<lineN-1>\\r

    The leading ``\\r`` returns the cursor to column 1 of the current row
    (the caller leaves the cursor somewhere on the frame's origin row);
    ``\\x1b[2K`` then clears that whole row before ``<line0>`` is written.
    For each subsequent row, ``\\n`` moves the cursor down one row (column
    stays at 1) and ``\\x1b[2K`` clears that row before its content lands.
    After the final row the cursor is already on the LAST row of the
    painted region — a single ``\\r`` parks it at column 1. No cursor-up
    retreat is emitted: bottom-parking is the whole point of the
    07-19-input-pyink-cursor-markdown change (repaints become 1-2 row
    micro-moves and the cursor stays away from the viewport top edge,
    dodging Windows Terminal's cursor-up viewport-yank bug).

    The cost is N extra ``\\x1b[2K`` writes per repaint (N = line count).
    At ~10 bytes each and a 50ms throttle this is well under 1 KB/s of
    extra stdout I/O, negligible in practice. ``\\x1b[2K`` is already
    used by ``_repaint``, so terminal compatibility is already validated.
    """
    lines = new_frame.split("\n")
    parts: list[str] = []
    for i, line in enumerate(lines):
        if i == 0:
            # Caller leaves the cursor on the frame's origin row; return
            # to column 1 explicitly then clear the entire row before
            # writing it.
            parts.append("\r\x1b[2K")
        else:
            # Move down one row (column stays at 1) and clear it before
            # the new content lands — prevents stale-tail bleed-through.
            parts.append("\n\x1b[2K")
        parts.append(line)
    # Park at column 1 of the LAST row of the painted region. Subsequent
    # diffs measure cursor offsets upward from this stable origin.
    parts.append("\r")
    stdout.write("".join(parts))


def _repaint(
    old_frame: str,
    new_frame: str,
    stdout: TextIO,
    available_rows: int | None = None,
) -> None:
    """Emit just the changed rows between ``old_frame`` and ``new_frame``.

    The cursor is assumed to start (and end) at column 1 of the LAST
    painted row. Changed rows are visited in DESCENDING index order —
    the cursor starts at the bottom of the frame, so the nearest changed
    rows come first and every move is a short cursor-up. For each
    changed row we ``\\r\\x1b[2K`` it, write the new content, and continue
    upward; after the topmost change we cursor-down back to the new
    frame's last row.

    ``available_rows`` caps how far the cursor may climb from the frame's
    last row (default: no cap, computed from the frame itself). Without
    the cap, a frame taller than the viewport causes cursor-up moves to
    clamp at the viewport top — the final cursor-down retreat (computed
    from the INTENDED position, not the clamped one) then overshoots
    past the frame's last row and the next paint anchors at the wrong y,
    eventually wiping live content (input row, dividers). With the cap,
    the cursor never climbs into the scrollback, so the retreat is
    always exact.

    Rows whose index is below ``len(old_lines) - available_rows`` are
    skipped (their content has scrolled into the scrollback; we can't
    reach them without clamping, and leaving them uncleared is the safe
    default — the same policy the pre-inversion code applied to rows
    past the viewport bottom).
    """
    old_lines = old_frame.split("\n")
    new_lines = new_frame.split("\n")

    # Note: shrink detection (frame getting shorter) is handled by the
    # CALLER in Instance._paint_now — it routes any height_delta >= 1 to
    # repaint_frame() which calls _erase_reachable_rows + _paint_initial
    # directly. So _repaint itself only needs to handle the same-height
    # / taller-frame cases below (plus direct calls from tests and the
    # full-erase ``write_diff(frame, "")`` path).

    out: list[str] = []
    max_len = max(len(old_lines), len(new_lines))
    # Track our current row offset within the frame's coordinate system.
    # The cursor starts parked on the LAST row of the previous frame.
    cur_row = len(old_lines) - 1
    # When the frame is taller than the viewport, cap cursor-UP travel
    # so the cursor never climbs into the scrollback (where it would
    # clamp at the viewport's top edge and, on Windows Terminal, yank
    # the viewport). ``row_floor`` is the minimum row index reachable
    # from the frame's last row.
    row_floor = (
        max(0, len(old_lines) - available_rows)
        if (available_rows and available_rows > 0)
        else 0
    )

    for row_idx in range(max_len - 1, -1, -1):
        old_row = old_lines[row_idx] if row_idx < len(old_lines) else None
        new_row = new_lines[row_idx] if row_idx < len(new_lines) else None
        if old_row == new_row:
            continue
        # Skip rows above the floor entirely — we can't reach them and
        # trying would clamp the cursor at the viewport top. Their
        # content (if any) has already scrolled into the scrollback;
        # leaving it untouched is the best we can do without a full
        # repaint.
        if row_idx < row_floor:
            continue
        cur_row = _emit_cursor_move(out, row_idx, cur_row, row_floor)
        out.append("\r")
        out.append("\x1b[2K")
        if new_row is not None:
            out.append(new_row)

    # Park the cursor back on the LAST row of the new frame, column 1.
    # The downward retreat never clamps: it returns toward the cursor's
    # parked row, which is always on-screen. (When ``new_frame`` is
    # shorter — e.g. the full-erase ``write_diff(frame, "")`` path —
    # ``home`` is above the current row and this is a cursor-up instead;
    # it still respects ``row_floor``.)
    home = len(new_lines) - 1
    cur_row = _emit_cursor_move(out, home, cur_row, row_floor)
    out.append("\r")
    stdout.write("".join(out))


def _erase_reachable_rows(
    old_lines: list[str],
    stdout: TextIO,
    available_rows: int,
) -> None:
    """Clear every old frame row reachable without viewport clamping.

    The cursor starts parked on the old frame's LAST row and walks
    UPWARD, clearing each row with ``\\r\\x1b[2K``, until it reaches
    ``row_floor`` — the topmost row still on-screen. Rows above the
    floor have scrolled into the scrollback and are left untouched
    (leaving them uncleared is the safe default). On return the cursor
    sits at column 1 of ``row_floor`` — row 0 of the footprint whenever
    the whole frame fits on-screen — which is exactly where
    ``_paint_initial`` expects to start repainting.
    """
    row_floor = max(0, len(old_lines) - available_rows)
    out: list[str] = []
    cur_row = len(old_lines) - 1

    for row_idx in range(len(old_lines) - 1, row_floor - 1, -1):
        cur_row = _emit_cursor_move(out, row_idx, cur_row, row_floor)
        out.append("\r\x1b[2K")
    stdout.write("".join(out))
