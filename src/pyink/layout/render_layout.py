"""Render a :class:`LayoutNode` tree to a string (PR3).

The renderer walks the layout tree and writes each text leaf into a 2D
character grid keyed by the leaf's absolute coordinates. Cells are then
joined into lines separated by ``\\n``; trailing whitespace is stripped
from each line (matching ink's behaviour — padding/gap filler that lives
at the end of a row must not leak into the snapshot).

Wide characters (CJK / most emoji) occupy two cells. ANSI escape
sequences are passed through verbatim without consuming any cell —
that lets PR4 reintroduce colour/style runs without changing the
renderer's bookkeeping.

PR3 renders **plain text only** — no borders. Colours, backgrounds and
explicit styles land in PR4 (but already pass through here safely).
"""

from __future__ import annotations

from pyink.layout.flex import LayoutNode
from pyink.layout.measure import _char_width, _split_visible_chunks, string_width

__all__ = ["render_layout_to_string"]


def render_layout_to_string(root: LayoutNode) -> str:
    """Render ``root`` to a plain string snapshot.

    The string uses ``\\n`` between rows and may contain ANSI escapes
    when the source text leaves did. Rows whose width would otherwise
    be padded with trailing spaces are right-trimmed (matches ink).
    """
    grid = _Grid(width=root.width, height=root.height)
    _paint_node(grid, root, base_x=0, base_y=0)
    return grid.to_string()


class _Grid:
    """Sparse 2D character buffer keyed by ``(x, y)`` cell coordinates.

    The grid has a fixed width × height established by the root layout
    box; cells outside that rectangle are still written but rows beyond
    ``height`` are dropped on serialization.

    Wide characters occupy two adjacent cells — the second cell holds
    an empty string sentinel so it is skipped during serialization.
    """

    # Sentinel marking the trailing half of a wide character — not
    # rendered to the output, just used to keep two cells reserved.
    _WIDE_TAIL = ""

    def __init__(self, *, width: int, height: int) -> None:
        self._width = max(0, width)
        self._height = max(0, height)
        # row index -> list of cells (one per column). Cells are either
        # a single visible character, an empty string (wide-tail marker)
        # or a single space (default filler for unwritten cells).
        self._rows: dict[int, list[str]] = {}

    def _ensure_capacity(self, y: int, up_to: int) -> list[str]:
        row = self._rows.setdefault(y, [])
        if len(row) < up_to:
            row.extend(" " * (up_to - len(row)))
        return row

    def put(self, x: int, y: int, text: str) -> None:
        """Write ``text`` at ``(x, y)`` overwriting existing cells.

        ``text`` may mix visible characters, ANSI escape sequences and
        wide (CJK / emoji) characters. Each visible character occupies
        one or two cells per its display width; ANSI escapes attach to
        the cell of the most recently written visible character (or the
        upcoming one if they appear before any visible text).
        """
        if not text:
            return
        # Reserve enough cells for the visible width, plus one extra so
        # a leading ANSI run can attach to the first cell without an
        # extra allocation.
        visible_w = string_width(text)
        needed = x + max(1, visible_w)
        row = self._ensure_capacity(y, needed)
        cursor = x
        last_visible_cell: int | None = None
        pending_leading_escape: list[str] = []
        for chunk, is_escape in _split_visible_chunks(text):
            if is_escape:
                if last_visible_cell is not None:
                    # We have a prior visible char — attach the escape to it.
                    row[last_visible_cell] = row[last_visible_cell] + chunk
                else:
                    # Leading escape — buffer until the first visible
                    # char lands, then prepend to that cell's content.
                    pending_leading_escape.append(chunk)
                continue
            for ch in chunk:
                w = _char_width(ch)
                if w <= 0:
                    # Combining mark / zero-width: attach to the current
                    # cell's existing content without advancing.
                    if last_visible_cell is not None:
                        row[last_visible_cell] = row[last_visible_cell] + ch
                    continue
                if w == 1:
                    cell = ch
                    if pending_leading_escape:
                        cell = "".join(pending_leading_escape) + cell
                        pending_leading_escape.clear()
                    row[cursor] = cell
                    last_visible_cell = cursor
                    cursor += 1
                else:
                    # Wide character: occupies two cells. Reserve the
                    # trailing cell with the wide-tail sentinel so later
                    # writes at that index know it's taken.
                    cell = ch
                    if pending_leading_escape:
                        cell = "".join(pending_leading_escape) + cell
                        pending_leading_escape.clear()
                    row[cursor] = cell
                    if cursor + 1 < len(row):
                        row[cursor + 1] = self._WIDE_TAIL
                    last_visible_cell = cursor
                    cursor += 2

    def to_string(self) -> str:
        if self._height == 0:
            return ""
        lines: list[str] = []
        for y in range(self._height):
            row = self._rows.get(y)
            if row is None:
                lines.append("")
                continue
            # Drop wide-tail sentinels before joining; rstrip trailing
            # whitespace per ink behaviour.
            joined = "".join(cell for cell in row if cell != self._WIDE_TAIL)
            lines.append(joined.rstrip())
        return "\n".join(lines)


def _paint_node(grid: _Grid, node: LayoutNode, base_x: int, base_y: int) -> None:
    """Paint ``node`` (and its subtree) onto ``grid``.

    ``base_x`` / ``base_y`` are the absolute coordinates of the parent's
    top-left content box; each child's own ``x`` / ``y`` is relative to
    that and already includes the child's own margin (the layout engine
    folds margin into ``layout_x`` / ``layout_y``).
    """
    abs_x = base_x + node.x
    abs_y = base_y + node.y

    if node.content is not None:
        _paint_text(grid, node, abs_x, abs_y)
        return

    # Recurse into children — non-text nodes don't paint themselves,
    # they only establish a coordinate frame for descendants.
    for child in node.children:
        _paint_node(grid, child, abs_x, abs_y)


def _paint_text(grid: _Grid, node: LayoutNode, abs_x: int, abs_y: int) -> None:
    """Write a text leaf's content into the grid honouring its box.

    If the node has an explicit height that's smaller than the natural
    line count, lines beyond the height are clipped (matches ink's
    ``<Box height={n}>`` truncation behaviour).
    """
    text = node.content or ""
    lines = text.split("\n")
    if node.height > 0:
        lines = lines[: node.height]
    for i, line in enumerate(lines):
        grid.put(abs_x, abs_y + i, line)
