"""Render a :class:`LayoutNode` tree to a string (PR3 + PR4).

The renderer walks the layout tree and writes each text leaf into a 2D
character grid keyed by the leaf's absolute coordinates. Cells are then
joined into lines separated by ``\\n``; trailing whitespace is stripped
from each line (matching ink's behaviour — padding/gap filler that lives
at the end of a row must not leak into the snapshot).

Wide characters (CJK / most emoji) occupy two cells. ANSI escape
sequences are passed through verbatim without consuming any cell.

PR4 additions on top of PR3:

* Text leaves are wrapped with :func:`pyink.render.ansi.apply_style`
  when the originating element props carry colour / bold / italic /
  underline / strikethrough / inverse / dimColor.
* Box hosts paint a background fill across their content area when
  ``backgroundColor`` is set, then draw a border around the outer
  edge when ``borderStyle`` is set. Border cells are reserved in the
  layout pass (``FlexStyle`` folds them into padding) so the renderer
  simply writes the characters at the box's coordinate frame.
"""

from __future__ import annotations

from typing import Any

from pyink.layout.flex import LayoutNode
from pyink.layout.measure import _char_width, _split_visible_chunks, string_width

__all__ = ["render_layout_to_string"]

#: SGR reset sequence used to terminate every colour / style run.
_SGR_RESET = "\x1b[0m"


def _load_ansi() -> Any:
    """Lazy import of :mod:`pyink.render.ansi` to avoid a load cycle.

    ``pyink.render`` imports from :mod:`pyink.layout`, so importing
    :mod:`pyink.render.ansi` at module load time here would re-enter
    the partially-initialised ``pyink.render`` package.
    """
    from pyink.render import ansi

    return ansi


def render_layout_to_string(root: LayoutNode) -> str:
    """Render ``root`` to a plain string snapshot.

    The string uses ``\\n`` between rows and may contain ANSI escapes
    when the source text leaves or box borders carried colour. Rows
    whose visible width would otherwise be padded with trailing spaces
    are right-trimmed (matches ink).
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

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

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

    def fill_row(self, x: int, y: int, width: int, payload: str) -> None:
        """Fill ``width`` cells of row ``y`` starting at column ``x``.

        Used by the background painter. The payload is expected to span
        exactly ``width`` visible cells (one ANSI open + ``width``
        spaces + reset). If any cell in the span was already written to
        (text leaf or border), the fill is skipped — the inheriting
        text already carries the background colour via ``apply_style``.
        """
        if width <= 0:
            return
        row = self._ensure_capacity(y, x + width)
        for i in range(width):
            if row[x + i] != " ":
                return
        # Whole span empty — the payload string occupies cell ``x``;
        # the visible spaces inside it visually fill the remaining cells.
        row[x] = payload
        for i in range(1, width):
            row[x + i] = ""

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


def _paint_node(
    grid: _Grid,
    node: LayoutNode,
    base_x: int,
    base_y: int,
    inherited_bg: str | None = None,
) -> None:
    """Paint ``node`` (and its subtree) onto ``grid``.

    ``base_x`` / ``base_y`` are the absolute coordinates of the parent's
    top-left content box; each child's own ``x`` / ``y`` is relative to
    that and already includes the child's own margin (the layout engine
    folds margin into ``layout_x`` / ``layout_y``).

    ``inherited_bg`` carries the nearest ancestor Box's
    ``backgroundColor`` so text leaves can apply it (mirrors ink's
    BackgroundContext behaviour). A Box with its own ``backgroundColor``
    overrides the inheritance for its descendants.
    """
    abs_x = base_x + node.x
    abs_y = base_y + node.y

    own_bg = node.props.get("backgroundColor") if node.kind == "box" else None
    effective_bg = own_bg or inherited_bg

    if node.content is not None:
        _paint_text(grid, node, abs_x, abs_y, inherited_bg=effective_bg)
        return

    # Recurse into children first so text leaves are painted before the
    # background fill — the background painter then only fills cells
    # that the children have not already populated, which preserves any
    # colour / style already attached to those cells.
    for child in node.children:
        _paint_node(grid, child, abs_x, abs_y, inherited_bg=effective_bg)

    if node.kind == "box":
        _paint_box_background(grid, node, abs_x, abs_y)
        _paint_box_border(grid, node, abs_x, abs_y)


def _paint_text(
    grid: _Grid,
    node: LayoutNode,
    abs_x: int,
    abs_y: int,
    *,
    inherited_bg: str | None = None,
) -> None:
    """Write a text leaf's content into the grid honouring its box.

    If the node has an explicit height that's smaller than the natural
    line count, lines beyond the height are clipped (matches ink's
    ``<Box height={n}>`` truncation behaviour). When the source props
    carry colour / bold / italic / … the styled text is wrapped via
    :func:`apply_style` before being painted. The inherited Box
    ``backgroundColor`` (if any) is applied unless the Text declares its
    own ``backgroundColor``.

    Styling is applied **per line** rather than over the whole multi-line
    string. A whole-string wrap would put the SGR opener on the first
    line and the reset on the last line; the renderer writes each line
    into a separate grid row, so middle lines would carry no opener and
    the first line would carry no reset — letting the foreground colour
    / dim / bold run leak past the text into adjacent cells (border
    edges, background fill, padding) on that row.
    """
    text = node.content or ""
    raw_lines = text.split("\n")
    if node.height > 0:
        raw_lines = raw_lines[: node.height]
    for i, raw_line in enumerate(raw_lines):
        styled = _apply_text_style(raw_line, node.props, inherited_bg=inherited_bg)
        grid.put(abs_x, abs_y + i, styled)


def _apply_text_style(
    text: str,
    props: dict[str, Any],
    *,
    inherited_bg: str | None = None,
) -> str:
    """Apply ``Text`` colour / bold / italic / … props to ``text``."""
    if not props and not inherited_bg:
        return text
    color = props.get("color") if props else None
    own_bg = props.get("backgroundColor") if props else None
    bg = own_bg if own_bg is not None else inherited_bg
    bold = bool(props.get("bold")) if props else False
    italic = bool(props.get("italic")) if props else False
    underline = bool(props.get("underline")) if props else False
    strikethrough = bool(props.get("strikethrough")) if props else False
    inverse = bool(props.get("inverse")) if props else False
    dim = bool(props.get("dimColor")) if props else False
    if not any([color, bg, bold, italic, underline, strikethrough, inverse, dim]):
        return text
    ansi = _load_ansi()
    styled: str = ansi.apply_style(
        text,
        color=color,
        backgroundColor=bg,
        bold=bold,
        italic=italic,
        underline=underline,
        strikethrough=strikethrough,
        inverse=inverse,
        dimColor=dim,
    )
    return styled


def _paint_box_background(grid: _Grid, node: LayoutNode, abs_x: int, abs_y: int) -> None:
    """Fill the box's interior with ``backgroundColor`` if set.

    The fill covers the content area *inside* the border (when present)
    so the visible coloured band matches ink's behaviour.
    """
    bg = node.props.get("backgroundColor")
    if not bg:
        return
    ansi = _load_ansi()
    body = ansi.parse_color(bg, type_="background")
    if body is None:
        return
    # Compute interior rectangle after border (1-cell per visible side).
    style = node.style
    has_border = bool(style.get("hasBorder"))
    top = 1 if (has_border and style.get("borderTop", True)) else 0
    bottom = 1 if (has_border and style.get("borderBottom", True)) else 0
    left = 1 if (has_border and style.get("borderLeft", True)) else 0
    right = 1 if (has_border and style.get("borderRight", True)) else 0
    inner_x = abs_x + left
    inner_y = abs_y + top
    inner_w = max(0, node.width - left - right)
    inner_h = max(0, node.height - top - bottom)
    payload = _bg_payload(bg, inner_w)
    for row in range(inner_h):
        grid.fill_row(inner_x, inner_y + row, inner_w, payload)


def _bg_payload(color: str, width: int) -> str:
    """Return the per-row background payload string of ``width`` cells."""
    ansi = _load_ansi()
    body = ansi.parse_color(color, type_="background")
    if body is None:
        return " " * width
    return f"\x1b[{body}m{' ' * width}\x1b[0m"


def _paint_box_border(grid: _Grid, node: LayoutNode, abs_x: int, abs_y: int) -> None:
    """Draw the four border edges directly into the grid."""
    ansi = _load_ansi()

    raw = node.props.get("borderStyle")
    if raw is None:
        return
    chars = ansi.resolve_border_chars(raw)
    style = node.style

    show_top = style.get("borderTop", True) is not False
    show_bottom = style.get("borderBottom", True) is not False
    show_left = style.get("borderLeft", True) is not False
    show_right = style.get("borderRight", True) is not False

    top_color = node.props.get("borderTopColor") or node.props.get("borderColor")
    bottom_color = node.props.get("borderBottomColor") or node.props.get("borderColor")
    left_color = node.props.get("borderLeftColor") or node.props.get("borderColor")
    right_color = node.props.get("borderRightColor") or node.props.get("borderColor")
    top_bg = node.props.get("borderTopBackgroundColor") or node.props.get("borderBackgroundColor")
    bottom_bg = (
        node.props.get("borderBottomBackgroundColor") or node.props.get("borderBackgroundColor")
    )
    left_bg = node.props.get("borderLeftBackgroundColor") or node.props.get("borderBackgroundColor")
    right_bg = (
        node.props.get("borderRightBackgroundColor") or node.props.get("borderBackgroundColor")
    )
    top_dim = _opt_bool(node.props, "borderTopDimColor", "borderDimColor")
    bottom_dim = _opt_bool(node.props, "borderBottomDimColor", "borderDimColor")
    left_dim = _opt_bool(node.props, "borderLeftDimColor", "borderDimColor")
    right_dim = _opt_bool(node.props, "borderRightDimColor", "borderDimColor")

    content_w = max(0, node.width - (1 if show_left else 0) - (1 if show_right else 0))

    if show_top:
        seg = (
            (chars["topLeft"] if show_left else "")
            + chars["top"] * content_w
            + (chars["topRight"] if show_right else "")
        )
        grid.put(abs_x, abs_y, ansi.style_segment(seg, fg=top_color, bg=top_bg, dim=top_dim))

    middle_start = abs_y + (1 if show_top else 0)
    middle_end = abs_y + node.height - (1 if show_bottom else 0)
    if show_left:
        seg = ansi.style_segment(chars["left"], fg=left_color, bg=left_bg, dim=left_dim)
        for y in range(middle_start, middle_end):
            grid.put(abs_x, y, seg)
    if show_right:
        seg = ansi.style_segment(chars["right"], fg=right_color, bg=right_bg, dim=right_dim)
        for y in range(middle_start, middle_end):
            grid.put(abs_x + node.width - 1, y, seg)

    if show_bottom:
        seg = (
            (chars["bottomLeft"] if show_left else "")
            + chars["bottom"] * content_w
            + (chars["bottomRight"] if show_right else "")
        )
        grid.put(
            abs_x,
            abs_y + node.height - 1,
            ansi.style_segment(seg, fg=bottom_color, bg=bottom_bg, dim=bottom_dim),
        )


def _opt_bool(props: dict[str, Any], key: str, fallback: str) -> bool:
    v = props.get(key)
    if v is None:
        v = props.get(fallback)
    return bool(v)
