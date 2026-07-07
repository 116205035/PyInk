"""Frame-level diff — only repaint rows that actually changed (PR5).

Inline mode (PRD Decision 3) requires the renderer to never destroy the
shell's scrollback history. ``\\x1b[2J`` (clear screen) is therefore
banned; instead we move the cursor up to the first painted row, clear
each changed row with ``\\x1b[2K`` and rewrite it, then move the cursor
back to the top of the painted region.

Algorithm choice for PR5: **simple line-by-line comparison**. We do not
run a Myers diff to align line insertions/deletions because (a) ink
itself uses the same approach in its MVP, (b) our output rows are
deliberately the layout engine's grid rows — there's no real "line
insertion in the middle" scenario unless the tree itself changes shape,
and (c) a Myers diff would add ~300 LOC for marginal savings. The
trade-off is documented here so a future PR can revisit it.

Cursor convention: after every :func:`write_diff` (and after the initial
paint) the cursor sits on **column 1 of the FIRST row** of the painted
region (i.e. one row above where the next application output would
land). This makes re-entrancy trivial: each subsequent call measures
cursor offsets relative to a stable origin.

Frame-shrink cursor retreat (PR for 07-07-tui-palette-layout-bug):
when ``new_frame`` has fewer rows than ``old_frame``, the extra rows
must be erased AND the cursor must retreat to ``new_frame``'s row 0 so
the next paint anchors correctly. The previous implementation emitted a
large cursor-down to the last extra row, cleared each row, then a single
large cursor-up back to row 0. That approach was fragile when the frame
overflowed the viewport:

* The cursor would try to descend to rows the terminal clamps at the
  bottom edge. After the clears, the cursor-up retreat (computed from
  the INTENDED row count, not the actual clamped position) overshot
  upward past frame row 0. The next diff then anchored at the wrong y,
  slowly drifting until live content (input prompt, dividers) was
  wiped — the "input row disappears after Esc" bug.

The fix caps the cursor-down target via ``available_rows`` (passed in
from the caller when known). With the cap, the cursor never reaches a
row the terminal will clamp. The cursor-up at the end of ``_repaint``
then retreats from the ACTUAL max row reached (which equals the capped
target), so it always returns to frame row 0 exactly. ``available_rows``
is computed by :class:`Instance` as ``viewport_rows - static_rows_above``
— an approximation that errs on the generous side (it counts ``\\n``
characters in flushed static text, ignoring eager-wrap extra rows).

Public API: :func:`write_diff`. Everything else is internal.
"""

from __future__ import annotations

from typing import TextIO

__all__ = ["write_diff"]


def write_diff(
    old_frame: str | None,
    new_frame: str,
    stdout: TextIO,
    available_rows: int | None = None,
) -> None:
    """Write the difference between ``old_frame`` and ``new_frame`` to stdout.

    ``old_frame`` is ``None`` on the very first render — the new frame is
    written verbatim followed by a cursor-up sequence that parks the
    cursor at the top-left of the painted region. On subsequent renders
    we walk every row, clear+rewrite only those that differ, append any
    extra rows in ``new_frame``, and finally move the cursor back to the
    top.

    ``available_rows`` is the number of terminal rows the frame region
    can occupy (typically ``viewport_rows - static_rows_above``). When
    provided, the diff caps cursor-down movements so they never push
    past the viewport bottom — preventing the cursor-drift bug where
    a clamped cursor-up overshoots frame row 0 and wipes live content
    (input row, dividers). ``None`` disables the cap (legacy behaviour,
    preserved for backward compatibility with the diff unit tests).

    The function never writes ``\\x1b[2J`` (full-screen clear) — that
    destroys scrollback (PRD Decision 3).
    """
    if old_frame is None:
        _paint_initial(new_frame, stdout)
        return

    if old_frame == new_frame:
        return

    _repaint(old_frame, new_frame, stdout, available_rows)


def _paint_initial(new_frame: str, stdout: TextIO) -> None:
    """Write ``new_frame`` and park the cursor at its top-left."""
    stdout.write(new_frame)
    # Walk the cursor back up to the first row. We always end at column 1
    # of row 0 of the painted region so subsequent writes have a stable
    # origin.
    new_lines = new_frame.split("\n")
    up = len(new_lines) - 1
    parts: list[str] = []
    if up > 0:
        parts.append("\x1b[" + str(up) + "A")
    parts.append("\r")
    stdout.write("".join(parts))


def _repaint(
    old_frame: str,
    new_frame: str,
    stdout: TextIO,
    available_rows: int | None = None,
) -> None:
    """Emit just the changed rows between ``old_frame`` and ``new_frame``.

    The cursor is assumed to start (and end) at column 1 of the FIRST
    painted row. For each row we want to change we move the cursor down
    to that row, ``\\r\\x1b[2K`` it, write the new content, then move
    back up.

    ``available_rows`` caps the maximum row index the cursor can reach
    (default: no cap, computed from the frame itself). Without the cap,
    a frame taller than the viewport causes the cursor to clamp at the
    viewport bottom — the final cursor-up then overshoots past frame
    row 0 and the next paint anchors at the wrong y, eventually wiping
    live content (input row, dividers). With the cap, the cursor never
    reaches a clamped row, so the cursor-up retreat is always exact.

    Rows whose index exceeds the cap are skipped (their content is
    already off-screen; we can't reach them without clamping, and
    leaving them uncleared is the safe default).
    """
    old_lines = old_frame.split("\n")
    new_lines = new_frame.split("\n")

    out: list[str] = []
    max_len = max(len(old_lines), len(new_lines))
    # Track our current row offset (0 = first painted row, positive = below).
    cur_row = 0
    # When the frame is taller than the viewport, cap cursor-down targets
    # so the cursor never reaches a row the terminal will clamp. The cap
    # is the maximum VALID row index within the painted region.
    row_cap = (available_rows - 1) if (available_rows and available_rows > 0) else (max_len - 1)
    # The cap cannot exceed the layout's own row count — otherwise we'd
    # invent rows that don't exist.
    row_cap = min(row_cap, max_len - 1)

    def goto(target: int) -> None:
        nonlocal cur_row
        # Clamp the target to the row cap so we never emit a cursor-down
        # that would push past the viewport bottom.
        target = min(target, row_cap)
        delta = target - cur_row
        if delta > 0:
            out.append("\x1b[" + str(delta) + "B")
        elif delta < 0:
            out.append("\x1b[" + str(-delta) + "A")
        cur_row = target

    for row_idx in range(max_len):
        old_row = old_lines[row_idx] if row_idx < len(old_lines) else None
        new_row = new_lines[row_idx] if row_idx < len(new_lines) else None
        if old_row == new_row:
            continue
        # Skip rows beyond the cap entirely — we can't reach them and
        # trying would clamp the cursor. Their content (if any) is
        # already off-screen; leaving them uncleared is the best we can
        # do without a full repaint.
        if row_idx > row_cap:
            continue
        goto(row_idx)
        out.append("\r")
        out.append("\x1b[2K")
        if new_row is not None:
            out.append(new_row)

    # Park the cursor back on the first painted row, column 1. We emit
    # a cursor-up from ``cur_row`` (which was capped above), so the
    # retreat distance matches what the terminal actually moved — never
    # overshooting past frame row 0.
    goto(0)
    out.append("\r")
    stdout.write("".join(out))
