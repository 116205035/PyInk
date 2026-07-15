"""Render a :class:`LayoutNode` tree to a string (PR3 + PR4).

The renderer walks the layout tree and writes each text leaf into a 2D
character grid keyed by the leaf's absolute coordinates. Cells are then
joined into lines separated by ``\\n``; trailing whitespace is stripped
from each line (matching ink's behaviour — padding/gap filler that lives
at the end of a row must not leak into the snapshot).

Wide characters (CJK / most emoji) occupy two cells. ANSI escape
sequences are passed through verbatim without consuming any cell.

PR4 additions on top of PR3:

* Text leaves are wrapped with :func:`ink.render.ansi.apply_style`
  when the originating element props carry colour / bold / italic /
  underline / strikethrough / inverse / dimColor.
* Box hosts paint a background fill across their content area when
  ``backgroundColor`` is set, then draw a border around the outer
  edge when ``borderStyle`` is set. Border cells are reserved in the
  layout pass (``FlexStyle`` folds them into padding) so the renderer
  simply writes the characters at the box's coordinate frame.
"""

from __future__ import annotations

import re
from typing import Any

from ink.layout.flex import LayoutNode
from ink.layout.measure import _char_width, _split_visible_chunks, string_width

__all__ = ["render_layout_to_string"]

#: SGR reset sequence used to terminate every colour / style run.
_SGR_RESET = "\x1b[0m"

#: Regex that matches ANSI SGR (Select Graphic Rendition) sequences —
#: ``\x1b[...m`` where ``...`` is any run of ``;``-separated numbers (or
#: empty, for the ``\x1b[m`` shorthand). Used by
#: :func:`_line_has_visible_content` to strip styling before checking
#: whether a rendered line carries any user-visible characters.
_ANSI_SGR_RE = re.compile(r"\x1b\[[0-9;]*m")


def _line_has_visible_content(styled_line: str) -> bool:
    """Check if a styled line has any visible (non-whitespace) characters.

    Strips ANSI SGR sequences from ``styled_line`` and reports whether the
    remaining content has any non-whitespace characters. Used by the text
    leaf painter to decide whether to flush the background band to the full
    layout width — an empty/SGR-only row with ``flushBackgroundToWidth=True``
    would otherwise paint a phantom coloured stripe the user sees as a
    "ghost message" (e.g. Jarvis' ``pending_user_row`` when idle).
    """
    bare = _ANSI_SGR_RE.sub("", styled_line)
    return bool(bare.strip())


def _load_ansi() -> Any:
    """Lazy import of :mod:`ink.render.ansi` to avoid a load cycle.

    ``ink.render`` imports from :mod:`ink.layout`, so importing
    :mod:`ink.render.ansi` at module load time here would re-enter
    the partially-initialised ``ink.render`` package.
    """
    from ink.render import ansi

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
        # Stack of active content-box clips (inclusive bounds). Top of
        # stack is the currently active clip; writes outside it are
        # dropped. Used by the box renderer to mask content that would
        # otherwise leak past a parent Box's border into adjacent rows
        # when the layout engine was forced to overflow (sum of child
        # min-contents > container main axis — see
        # :func:`_distribute_main`'s overflow escape hatch).
        self._clip_stack: list[tuple[int, int, int, int]] = []
        # Row-level background fills (Path-A fix). Maps a row index to
        # ``(x1, x2, bg_open_sgr, bg_reset_sgr)``; :meth:`to_string`
        # applies the opener + reset around the row's visible content
        # so the bg spans the full layout width without leaking to
        # sibling rows. See :meth:`mark_row_background` for the
        # rationale (single open+reset per row vs. cell-level chaining).
        self._row_backgrounds: dict[int, tuple[int, int, str, str]] = {}

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def clip(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """Push a content-box clip (inclusive ``(x1, y1, x2, y2)``).

        Subsequent writes via :meth:`put` / :meth:`fill_row` that land
        entirely outside the active clip rectangle are dropped. The new
        clip is **intersected** with the currently active clip (if any)
        so nested boxes only ever *narrow* the visible region — a child
        cannot expand the writable area its parent already clipped.
        """
        if self._clip_stack:
            cx1, cy1, cx2, cy2 = self._clip_stack[-1]
            x1 = max(x1, cx1)
            y1 = max(y1, cy1)
            x2 = min(x2, cx2)
            y2 = min(y2, cy2)
        self._clip_stack.append((x1, y1, x2, y2))

    def unclip(self) -> None:
        """Pop the most recent :meth:`clip` push.

        Safe to call with an empty stack (no-op) so painters can use a
        ``try/finally`` unclip without tracking whether the matching
        ``clip`` actually pushed.
        """
        if self._clip_stack:
            self._clip_stack.pop()

    def _active_clip(self) -> tuple[int, int, int, int] | None:
        return self._clip_stack[-1] if self._clip_stack else None

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
        # Active content-box clip: drop writes whose starting cell falls
        # outside the clip rectangle. This is the PR3 escape-hatch for
        # overflow when the sum of children's min-contents exceeds the
        # container's main axis — the layout still positions those
        # children beyond the container's content box, but the painter
        # masks them here so they don't leak into sibling rows. Partial
        # overlap (text starting inside the clip but extending past the
        # far edge) is not truncated cell-by-cell: by construction every
        # text leaf that lands its first cell inside its parent's content
        # box has already been measured/wrapped to fit, so the overlap
        # case does not arise in practice.
        clip = self._active_clip()
        if clip is not None:
            cx1, cy1, cx2, cy2 = clip
            if x < cx1 or x > cx2 or y < cy1 or y > cy2:
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
        """Fill ``width`` empty cells of row ``y`` starting at column ``x``.

        Used by the background painter for the *empty-cell* region of a
        Box's interior — text leaves and border edges already carry their
        own colours via :func:`apply_style`. The payload is expected to
        span exactly ``width`` visible cells (one ANSI open + ``width``
        spaces + reset). If any cell in the span was already written to
        (text leaf or border), the fill is skipped — the inheriting text
        already carries the background colour via ``apply_style``.
        """
        if width <= 0:
            return
        # Active content-box clip: drop fills whose row or span falls
        # entirely outside the clip rectangle. Background fills are
        # emitted by the box painter *after* its children were painted,
        # so the fill covers only cells the children have not already
        # claimed — the clip here ensures that when the box was forced
        # to overflow (a child's content extends past the bottom edge)
        # the background does not smear past the border either.
        clip = self._active_clip()
        if clip is not None:
            cx1, cy1, cx2, cy2 = clip
            if y < cy1 or y > cy2 or (x + width - 1) < cx1 or x > cx2:
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

    def mark_row_background(
        self,
        x: int,
        y: int,
        width: int,
        bg_open: str,
        bg_reset: str,
    ) -> None:
        """Mark ``width`` cells of row ``y`` as carrying a background fill.

        Path-A fix for the "background only covers text width" /
        "background leaks to next line" bugs. Instead of relying on
        :func:`apply_style` to wrap each cell's text with the bg SGR
        (which breaks when the layout soft-wraps the styled string
        and the closing reset ends up on a wrapped trailing-whitespace
        line that ``rstrip`` then drops), the box / text-leaf background
        painter records a *row-level* bg that :meth:`to_string` applies
        when serialising each row.

        The bg is applied as: ``bg_open`` + the row's existing visible
        content (cells ``x..x+width-1``) padded to ``width`` visible
        cells + ``bg_reset``. Each visible row thus carries its own
        open + reset pair, so:

        * the bg spans the full layout width (not just the text width);
        * a wrapped trailing-whitespace line cannot lose its reset
          (the reset is anchored to the row's last byte, not to a
          cell that ``rstrip`` might erase);
        * sibling rows painted afterwards never inherit the bg SGR
          because each row's reset lives at the row's end.

        ``bg_open`` is the full SGR opener (e.g. ``\\x1b[48;2;r;g;bm``)
        and ``bg_reset`` is the full reset (``\\x1b[0m``). The caller
        pre-computes both so this method is a pure bookkeeping write.
        """
        if width <= 0 or not bg_open:
            return
        clip = self._active_clip()
        if clip is not None:
            cx1, cy1, cx2, cy2 = clip
            if y < cy1 or y > cy2 or (x + width - 1) < cx1 or x > cx2:
                return
        # Ensure capacity so the painter can read existing cells when
        # it later decides whether a span already has visible content.
        self._ensure_capacity(y, x + width)
        # Multiple backgrounds on the same row (e.g. a parent Box bg
        # plus a child Text bg) — last write wins, which matches the
        # CSS-style "nearest ancestor" intuition: children paint after
        # parents, so a child Text's bg supersedes the parent Box's bg
        # for the span it actually covers.
        self._row_backgrounds[y] = (x, x + width, bg_open, bg_reset)

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
            bg = self._row_backgrounds.get(y)
            if bg is not None:
                x1, x2, bg_open, bg_reset = bg
                # The bg band spans ``[x1, x2)`` visible cells. The
                # opener goes right before the row's first visible
                # byte (skipping any leading whitespace cells that sit
                # *inside* the bg span — those keep the default bg,
                # which the opener would otherwise stain). The reset
                # sits at the row's end after padding the visible
                # content to the full span width, so the bg fills the
                # entire layout-granted width.
                #
                # Why row-level (not cell-level): the bg SGR is a
                # terminal-mode switch — once ``bg_open`` runs, every
                # subsequent byte inherits the bg until ``bg_reset``
                # runs. Anchoring the reset to the row's last byte
                # means neither the layout's soft-wrap nor the
                # ``rstrip`` here can drop the reset on a different
                # line; the band stays self-contained per row.
                stripped = joined.rstrip()
                if stripped:
                    # If the bg span starts at column 0 there is no
                    # leading-padding to preserve; otherwise preserve
                    # any cells before the bg span as-is so a left
                    # indent (parent padding, leading space tokens)
                    # keeps its default bg.
                    head = ""
                    body = stripped
                    if x1 > 0:
                        # Strip up to ``x1`` leading spaces (the cells
                        # before the bg span); preserve the rest of
                        # the leading prefix verbatim.
                        cut = 0
                        while cut < x1 and cut < len(body) and body[cut] == " ":
                            cut += 1
                        if cut:
                            head = body[:cut]
                            body = body[cut:]
                    # Pad the body to the bg span's visible width so
                    # the bg fills the entire row, not just the text.
                    # Visible width excludes ANSI escapes already
                    # embedded in the body (e.g. foreground colours).
                    body_w = string_width(body)
                    target_w = x2 - x1
                    if body_w < target_w:
                        body = body + " " * (target_w - body_w)
                    lines.append(head + bg_open + body + bg_reset)
                else:
                    # Row was all whitespace — emit the opener + the
                    # row's natural width worth of spaces + reset so
                    # the bg band still shows up.
                    lines.append(bg_open + " " * (x2 - x1) + bg_reset)
            else:
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

    Vertical-overflow graceful truncation: when a node's resolved
    ``height`` is too small to render anything meaningful the renderer
    skips painting the node (and its subtree) entirely. This prevents
    the "dangling half-border" artefact where a bordered Box the flex
    engine shrank to zero or one row would otherwise emit a single
    orphaned edge (top OR bottom, never both) and then leak its
    children's content past the parent's border into adjacent rows.
    The contract is: a bordered Box is either painted whole
    (top + content + bottom) or not at all.
    """
    abs_x = base_x + node.x
    abs_y = base_y + node.y

    # Vertical-overflow graceful truncation for bordered Boxes: when a
    # bordered Box is shrunk so small that it cannot fit both its top
    # and bottom edges on separate rows, skip painting the node (and
    # its subtree) entirely. Without this guard the painter would emit
    # a single orphaned edge — top OR bottom, never both — because
    # ``_paint_box_border`` writes the top edge at ``abs_y`` and the
    # bottom edge at ``abs_y + height - 1``; with ``height == 1`` both
    # land on the same row and the later write wins, leaving a
    # dangling ``┌─┐`` (or ``└─┘``) with no matching edge. The
    # children's content would then leak past the parent's border into
    # adjacent rows because the layout positioned them as if the box
    # still had its natural interior. Skipping the whole node keeps
    # the contract "a bordered Box is either painted whole (top +
    # content + bottom) or not at all".
    #
    # The check honours per-edge overrides: ``borderTop=False`` removes
    # the top-edge constraint, ``borderBottom=False`` the bottom-edge,
    # so a borderless-top-and-bottom Box with height 1 still renders
    # (the user explicitly opted out of those edges). Other kinds of
    # nodes (text leaves, unbordered Boxes) are left to the existing
    # ``_paint_text`` height-clip path — they have no edges to dangle
    # and the historic "shrink-to-zero but still paint naturally"
    # behaviour is preserved (some examples rely on this).
    if node.kind == "box" and node.style.get("hasBorder"):
        show_top = node.style.get("borderTop", True) is not False
        show_bottom = node.style.get("borderBottom", True) is not False
        # Minimum height required to fit the requested edges without
        # them colliding on the same row. Both edges → 2 rows; one
        # edge → 1 row; neither → 0 rows (no constraint).
        needed = (1 if show_top else 0) + (1 if show_bottom else 0)
        if node.height < needed:
            return

    own_bg = node.props.get("backgroundColor") if node.kind == "box" else None
    effective_bg = own_bg or inherited_bg
    # Path-A fix: when this Box has ``flushBackgroundToWidth=True`` the
    # bg is applied at the row level by ``_paint_box_background`` (one
    # open + reset per interior row, spanning the full layout width).
    # Children would otherwise inherit the bg via the legacy
    # ``apply_style`` path and double-paint. Suppress inheritance for
    # descendants of a flushing Box so the row-level bg is the sole
    # source of truth.
    flush_bg = bool(node.props.get("flushBackgroundToWidth")) if node.kind == "box" else False
    child_inherited_bg = None if flush_bg else effective_bg

    if node.content is not None:
        _paint_text(grid, node, abs_x, abs_y, inherited_bg=child_inherited_bg)
        return

    # PR3 Bug 1 part 2: clip children to this Box's outer rectangle so
    # any overflow content (when the sum of children's min-contents
    # exceeds the container's main axis) is masked instead of leaking
    # past the box's reserved area into sibling rows. The clip uses the
    # *outer* rectangle (``abs_x..abs_x+width-1`` ×
    # ``abs_y..abs_y+height-1``) rather than the inner content box so a
    # child that the layout positioned in the bottom-padding band can
    # still paint — the historic behaviour relied on this to keep small
    # overflows visible, and the border painter runs *after* the
    # children so it cleanly overwrites any cell a child claimed in the
    # padding band. Real overflow (children whose positions push past
    # ``abs_y + height - 1``) is what the clip targets. The clip is
    # intersected with whatever active clip the ancestor pushed so
    # nested boxes only narrow the writable region.
    if node.kind == "box":
        grid.clip(abs_x, abs_y, abs_x + node.width - 1, abs_y + node.height - 1)
    try:
        # Recurse into children first so text leaves are painted before the
        # background fill — the background painter then only fills cells
        # that the children have not already populated, which preserves any
        # colour / style already attached to those cells.
        for child in node.children:
            _paint_node(grid, child, abs_x, abs_y, inherited_bg=child_inherited_bg)

        if node.kind == "box":
            _paint_box_background(grid, node, abs_x, abs_y)
    finally:
        if node.kind == "box":
            grid.unclip()

    # Border paints OUTSIDE the content-box clip — border edges live in
    # the padding band (PR4 folded border into padding so layout reserved
    # the cells) and must never be clipped by this box's own content box.
    if node.kind == "box":
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

    Vertical scrolling (Phase 5): the public ``scroll_offset`` prop
    decides which row the visible window starts at when the text has
    more lines than the layout's granted height. The layout engine
    resolves callable / Signal forms at layout time (see
    :func:`ink.layout.flex._resolve_decoration_props`) so the value
    read here is already a plain ``int`` or ``None``. ``None`` keeps
    the historic top-keeping behaviour.

    Styling is applied **per line** rather than over the whole multi-line
    string. A whole-string wrap would put the SGR opener on the first
    line and the reset on the last line; the renderer writes each line
    into a separate grid row, so middle lines would carry no opener and
    the first line would carry no reset — letting the foreground colour
    / dim / bold run leak past the text into adjacent cells (border
    edges, background fill, padding) on that row.

    Path-A background fix: when the text leaf carries its own
    ``backgroundColor`` (or inherits one) AND the
    ``flushBackgroundToWidth`` prop is set, the renderer registers a
    row-level bg via :meth:`_Grid.mark_row_background` instead of
    relying on :func:`apply_style` to wrap each line. The legacy
    wrap-then-paint path put the bg open SGR before the first visible
    cell and the reset after the last visible cell of *one logical
    line*; when the layout soft-wrapped that line the reset ended up
    on a trailing-whitespace cell that ``rstrip`` would erase, leaking
    the bg SGR onto sibling rows. The row-level approach anchors the
    reset to the row's last visible byte, so neither wrap nor rstrip
    can drop it.

    ``flushBackgroundToWidth`` defaults to ``False`` so existing
    inline-coloured-token usage keeps the legacy "bg only covers the
    text" behaviour. Block-level surfaces (CC-style user-message
    bands) opt in by setting the prop to ``True``.
    """
    text = node.content or ""
    raw_lines = text.split("\n")
    if node.height > 0 and len(raw_lines) > node.height:
        raw_lines = _clip_lines_to_height(raw_lines, node.height, node.props)
    own_bg = node.props.get("backgroundColor") if node.props else None
    effective_bg = own_bg if own_bg is not None else inherited_bg
    flush_bg = bool(node.props.get("flushBackgroundToWidth")) if node.props else False
    if effective_bg and flush_bg:
        # Strip ``backgroundColor`` from the props we hand to
        # ``_apply_text_style`` so the legacy wrap path does not double-
        # apply the bg SGR; the row-level painter is the sole source of
        # truth for bg under Path-A.
        text_props = {k: v for k, v in node.props.items() if k != "backgroundColor"}
        ansi = _load_ansi()
        bg_body = ansi.parse_color(effective_bg, type_="background")
    else:
        # Legacy path: ``_apply_text_style`` wraps each line with the
        # bg SGR via ``apply_style`` (single open before the text,
        # single reset after). This is fine for inline coloured tokens
        # but breaks for block-level surfaces where the layout may
        # soft-wrap the line and the closing reset ends up on a
        # trailing-whitespace cell that ``rstrip`` then drops.
        text_props = node.props
        bg_body = None
    leaf_w = max(1, node.width)
    for i, raw_line in enumerate(raw_lines):
        if bg_body is not None:
            styled = _apply_text_style(raw_line, text_props, inherited_bg=None)
        else:
            styled = _apply_text_style(raw_line, text_props, inherited_bg=inherited_bg)
        grid.put(abs_x, abs_y + i, styled)
        if bg_body is not None:
            # Only flush background when the row has visible content —
            # an empty/whitespace-only row with flushBackgroundToWidth=True
            # would otherwise paint a phantom coloured stripe the user
            # sees as a "ghost message" (e.g. Jarvis' pending_user_row
            # when idle: the leaf stays mounted but its text is "").
            if _line_has_visible_content(styled):
                bg_open = f"\x1b[{bg_body}m"
                grid.mark_row_background(abs_x, abs_y + i, leaf_w, bg_open, _SGR_RESET)


def _clip_lines_to_height(
    lines: list[str], height: int, props: dict[str, Any]
) -> list[str]:
    """Clip ``lines`` to ``height`` rows, honouring an optional scroll window.

    The default policy keeps the *leading* ``height`` lines (matches
    ink's ``<Box height={n}>`` truncation). A text leaf may opt into
    vertical scrolling by carrying a ``scroll_offset`` prop — a 0-based
    line offset of the row the caller wants to be the top of the visible
    window. When set, we slice ``lines`` to
    ``[scroll_offset, scroll_offset + height)`` so the window starts at
    the requested row. The offset is clamped to ``[0, len(lines) -
    height]`` so a value past the end just pins to the last window
    (matches the natural bottom-of-list scroll behaviour).

    ``scroll_offset`` is the public Phase 5 replacement for the
    historical ``_ink_scroll`` private prop — it accepts plain
    ``int``, :class:`ink.Signal`, or ``Callable[[], int]`` and is
    resolved at layout time, so a function component whose cursor moves
    on every keystroke can simply write a signal and have the render
    loop pick up the change. This is what lets a multi-line
    :func:`ink.externals.TextInput` keep its cursor in view when the
    surrounding layout grants the leaf fewer rows than the buffer has
    lines — without it the top-keeping clip would drop the bottom line
    the cursor sits on, so the cursor "disappears" past the last
    visible row and only re-appears after scrolling back up.
    """
    raw = props.get("scroll_offset")
    scroll_offset: int | None
    if isinstance(raw, bool):
        # ``bool`` is an ``int`` subclass — never a meaningful scroll
        # value, so normalise to "unset".
        scroll_offset = None
    elif isinstance(raw, int):
        scroll_offset = raw
    else:
        scroll_offset = None
    if scroll_offset is None or scroll_offset <= 0:
        return lines[:height]
    max_start = max(0, len(lines) - height)
    start = max(0, min(scroll_offset, max_start))
    return lines[start : start + height]


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

    Path-A fix (``flushBackgroundToWidth``): instead of (only)
    stamping each empty cell with a self-closing
    ``\\x1b[48;...m   \\x1b[0m`` payload (which worked for truly-empty
    rows but skipped any row containing a text leaf because
    :meth:`_Grid.fill_row` bails the moment it sees a non-empty cell),
    the painter *also* registers a row-level bg via
    :meth:`_Grid.mark_row_background`. :meth:`_Grid.to_string` then
    wraps each affected row's visible content with the bg SGR open +
    reset, so the band:

    * spans the full interior width regardless of where the text cells
      sit (the bug behind "user message bg only covers the text");
    * never lets the closing reset be stripped by ``rstrip`` (the reset
      is appended after the row's last visible byte, never on a
      trailing-whitespace cell);
    * never leaks onto sibling rows (each row carries its own open +
      reset, so the next painted Text starts in a known-reset state).

    Opt-in via the ``flushBackgroundToWidth`` prop; legacy callers see
    no behaviour change.
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
    # Stamp empty cells with the bg payload so rows that have no text
    # leaf at all (e.g. a row of pure padding) still get the bg band.
    payload = _bg_payload(bg, inner_w)
    for row in range(inner_h):
        grid.fill_row(inner_x, inner_y + row, inner_w, payload)
    # Path-A row-level bg — opt-in via ``flushBackgroundToWidth``. The
    # row-level approach is what makes the bg span the full layout
    # width on text-bearing rows; without it the band collapses to
    # the text width (the legacy "bg only covers the text" bug).
    if not node.props.get("flushBackgroundToWidth"):
        return
    bg_open = f"\x1b[{body}m"
    bg_reset = _SGR_RESET
    for row in range(inner_h):
        grid.mark_row_background(inner_x, inner_y + row, inner_w, bg_open, bg_reset)


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
