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

Public API: :func:`write_diff`. Everything else is internal.
"""

from __future__ import annotations

from typing import TextIO

__all__ = ["write_diff"]


def write_diff(
    old_frame: str | None,
    new_frame: str,
    stdout: TextIO,
) -> None:
    """Write the difference between ``old_frame`` and ``new_frame`` to stdout.

    ``old_frame`` is ``None`` on the very first render — the new frame is
    written verbatim followed by a cursor-up sequence that parks the
    cursor at the top-left of the painted region. On subsequent renders
    we walk every row, clear+rewrite only those that differ, append any
    extra rows in ``new_frame``, and finally move the cursor back to the
    top.

    The function never writes ``\\x1b[2J`` (full-screen clear) — that
    destroys scrollback (PRD Decision 3).
    """
    if old_frame is None:
        _paint_initial(new_frame, stdout)
        return

    if old_frame == new_frame:
        return

    _repaint(old_frame, new_frame, stdout)


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


def _repaint(old_frame: str, new_frame: str, stdout: TextIO) -> None:
    """Emit just the changed rows between ``old_frame`` and ``new_frame``.

    The cursor is assumed to start (and end) at column 1 of the FIRST
    painted row. For each row we want to change we move the cursor down
    to that row, ``\\r\\x1b[2K`` it, write the new content, then move
    back up.
    """
    old_lines = old_frame.split("\n")
    new_lines = new_frame.split("\n")

    out: list[str] = []
    max_len = max(len(old_lines), len(new_lines))
    # Track our current row offset (0 = first painted row, positive = below).
    cur_row = 0

    def goto(target: int) -> None:
        nonlocal cur_row
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
        goto(row_idx)
        out.append("\r")
        out.append("\x1b[2K")
        if new_row is not None:
            out.append(new_row)

    # Park the cursor back on the first painted row, column 1.
    goto(0)
    out.append("\r")
    stdout.write("".join(out))
