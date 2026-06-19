"""Flex layout engine — Yoga subset (PR3).

The engine operates on :class:`FlexNode` trees built from the reconciler's
:class:`pyink.core.component.Instance` tree. Layout proceeds in two passes:

1. **Measure** every text leaf to obtain an intrinsic content size.
2. **Resolve** each container: compute main/cross sizes for children,
   distribute free space (grow/shrink/justify), then position children
   using align/align-content rules.

The result is a tree of :class:`LayoutNode` carrying absolute coordinates
relative to its parent, plus the resolved ``width``/``height`` and the
text ``content`` for leaves.

This is intentionally a *subset* of Yoga that covers the cases exercised
by ``ink``'s ``flex-*.tsx`` test suite. Out-of-scope (percentages,
``aspectRatio``, absolute positioning, ``order``) raise or are silently
ignored depending on what makes the call site safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pyink.layout.measure import string_width, wrap_text

__all__ = [
    "AlignContent",
    "AlignItems",
    "AlignSelf",
    "FlexDirection",
    "FlexWrap",
    "JustifyContent",
    "FlexNode",
    "LayoutNode",
    "MeasureMode",
    "build_flex_tree",
    "layout",
    "layout_root",
]

# ---------------------------------------------------------------------------
# Style enums
# ---------------------------------------------------------------------------

FlexDirection = Literal["row", "row-reverse", "column", "column-reverse"]
JustifyContent = Literal[
    "flex-start", "center", "flex-end", "space-between", "space-around", "space-evenly"
]
AlignItems = Literal["flex-start", "center", "flex-end", "stretch", "baseline"]
AlignSelf = Literal["flex-start", "center", "flex-end", "stretch", "baseline", "auto"]
AlignContent = Literal[
    "flex-start", "center", "flex-end", "stretch", "space-between", "space-around", "space-evenly"
]
FlexWrap = Literal["nowrap", "wrap", "wrap-reverse"]
MeasureMode = Literal["unbounded", "exactly", "at-most"]


# ---------------------------------------------------------------------------
# Style model
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Edges:
    """Four-sided box model values (padding or margin)."""

    top: int = 0
    right: int = 0
    bottom: int = 0
    left: int = 0

    @property
    def horizontal(self) -> int:
        return self.left + self.right

    @property
    def vertical(self) -> int:
        return self.top + self.bottom


@dataclass(slots=True)
class FlexStyle:
    """Normalised view of the supported flex props."""

    direction: FlexDirection = "row"
    justify: JustifyContent = "flex-start"
    align_items: AlignItems = "stretch"
    align_content: AlignContent = "flex-start"
    flex_wrap: FlexWrap = "nowrap"

    padding: Edges = field(default_factory=Edges)
    margin: Edges = field(default_factory=Edges)

    width: int | None = None
    height: int | None = None
    min_width: int | None = None
    max_width: int | None = None
    min_height: int | None = None
    max_height: int | None = None

    gap: int = 0
    column_gap: int = 0
    row_gap: int = 0

    flex_grow: float = 0.0
    flex_shrink: float = 1.0
    flex_basis: int | None = None  # ``None`` => "auto"

    align_self: AlignSelf = "auto"

    # PR4: border consumes layout cells. ``has_border`` is True when the
    # element carries a ``borderStyle`` prop; the four booleans mirror the
    # ``borderTop`` / ``borderRight`` / … overrides (default True).
    has_border: bool = False
    border_top: bool = True
    border_right: bool = True
    border_bottom: bool = True
    border_left: bool = True

    # -- factories ---------------------------------------------------------

    @classmethod
    def from_props(cls, props: dict[str, Any]) -> FlexStyle:
        """Extract a :class:`FlexStyle` from raw element ``props``.

        Supports both camelCase keys (``flexDirection``, ``paddingTop`` …)
        and the shorthand ``padding`` / ``paddingX`` / ``paddingY`` /
        ``margin`` / ``marginX`` / ``marginY`` convenience forms used by
        ink. Unknown keys are ignored so callers can pass the whole
        ``element.props`` mapping.
        """
        s = cls()
        s.direction = props.get("flexDirection", s.direction)
        s.justify = props.get("justifyContent", s.justify)
        s.align_items = props.get("alignItems", s.align_items)
        s.align_content = props.get("alignContent", s.align_content)
        s.flex_wrap = props.get("flexWrap", s.flex_wrap)
        s.align_self = props.get("alignSelf", s.align_self)

        s.padding = _edges_from(props, "padding")
        s.margin = _edges_from(props, "margin")

        # PR4: fold border presence into padding so layout reserves the
        # cells the renderer later fills with border characters. Per ink
        # semantics, an explicit ``borderTop=False`` etc. removes only
        # that one side. ``borderStyle`` may be a string or a dict.
        if props.get("borderStyle") is not None:
            s.has_border = True
            s.border_top = props.get("borderTop", True) is not False
            s.border_right = props.get("borderRight", True) is not False
            s.border_bottom = props.get("borderBottom", True) is not False
            s.border_left = props.get("borderLeft", True) is not False
            s.padding = Edges(
                top=s.padding.top + (1 if s.border_top else 0),
                right=s.padding.right + (1 if s.border_right else 0),
                bottom=s.padding.bottom + (1 if s.border_bottom else 0),
                left=s.padding.left + (1 if s.border_left else 0),
            )

        s.width = _int_or_none(props.get("width"))
        s.height = _int_or_none(props.get("height"))
        s.min_width = _int_or_none(props.get("minWidth"))
        s.max_width = _int_or_none(props.get("maxWidth"))
        s.min_height = _int_or_none(props.get("minHeight"))
        s.max_height = _int_or_none(props.get("maxHeight"))

        s.gap = props.get("gap", 0) or 0
        s.column_gap = props.get("columnGap", 0) or 0
        s.row_gap = props.get("rowGap", 0) or 0

        grow = props.get("flexGrow", 0)
        s.flex_grow = float(grow) if grow else 0.0
        shrink = props.get("flexShrink")
        s.flex_shrink = float(shrink) if shrink is not None else 1.0

        basis = props.get("flexBasis")
        if isinstance(basis, str) and basis.endswith("%"):
            # Percent basis unsupported — treat as auto.
            s.flex_basis = None
        elif isinstance(basis, int):
            s.flex_basis = basis
        else:
            s.flex_basis = None
        return s

    # -- helpers -----------------------------------------------------------

    def main_gap(self) -> int:
        """Gap applied along the main axis (between siblings)."""
        if self.is_column():
            # Column main axis is vertical → use row_gap (or generic gap).
            return self.row_gap or self.gap
        # Row main axis is horizontal → use column_gap (or generic gap).
        return self.column_gap or self.gap

    def is_row(self) -> bool:
        return self.direction in ("row", "row-reverse")

    def is_column(self) -> bool:
        return self.direction in ("column", "column-reverse")

    def is_reverse(self) -> bool:
        return self.direction in ("row-reverse", "column-reverse")


def _int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, str):
        # Percent values are out of scope — drop them silently.
        if v.endswith("%"):
            return None
        try:
            return int(v)
        except ValueError:
            return None
    if isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _edges_from(props: dict[str, Any], prefix: str) -> Edges:
    """Build an :class:`Edges` from ``<prefix>`` / ``<prefix>X`` / … keys."""

    def _opt(name: str) -> int | None:
        return _int_or_none(props.get(name))

    base = _opt(prefix)
    px = _opt(f"{prefix}X")
    py = _opt(f"{prefix}Y")
    top = _opt(f"{prefix}Top")
    right = _opt(f"{prefix}Right")
    bottom = _opt(f"{prefix}Bottom")
    left = _opt(f"{prefix}Left")

    def _resolve(specific: int | None, axis_default: int | None, base_default: int | None) -> int:
        if specific is not None:
            return specific
        if axis_default is not None:
            return axis_default
        if base_default is not None:
            return base_default
        return 0

    return Edges(
        top=_resolve(top, py, base),
        right=_resolve(right, px, base),
        bottom=_resolve(bottom, py, base),
        left=_resolve(left, px, base),
    )


# ---------------------------------------------------------------------------
# Flex tree (mutable, internal)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FlexNode:
    """Internal layout node — corresponds 1:1 to a host instance.

    Function-component instances are flattened away during
    :func:`build_flex_tree`: only host nodes survive into the layout
    engine.
    """

    kind: str  # "text" or other host tag ("box", …)
    style: FlexStyle
    children: list[FlexNode] = field(default_factory=list)
    text: str | None = None  # for kind == "text"
    # Cached measurement results (intrinsic content size).
    measured_width: int = 0
    measured_height: int = 0
    # Resolved layout output (filled during :func:`layout`).
    layout_x: int = 0
    layout_y: int = 0
    layout_width: int = 0
    layout_height: int = 0
    # Source host instance (kept so render can read original element).
    source: Any = None
    # Raw element ``props`` — PR4 reads border / colour / text-style
    # overrides from here when painting.
    props: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LayoutNode:
    """Post-layout node tree consumed by the renderer.

    ``x`` / ``y`` are coordinates **relative to the parent's top-left
    corner** (including the parent's padding box). ``content`` is set
    only for text leaves and contains the rendered text for that leaf
    (may span multiple newline-separated lines).

    ``props`` carries the raw element props verbatim so the PR4 renderer
    can apply border / background / text-style without re-reading the
    source instance.
    """

    x: int
    y: int
    width: int
    height: int
    content: str | None = None
    children: list[LayoutNode] = field(default_factory=list)
    style: dict[str, Any] = field(default_factory=dict)
    kind: str = "box"
    props: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tree construction (host instance → FlexNode)
# ---------------------------------------------------------------------------


def build_flex_tree(instance: Any) -> FlexNode | None:
    """Materialise a :class:`FlexNode` tree from a host instance tree.

    Function-component instances are skipped — only host nodes appear in
    the resulting tree. Returns ``None`` if ``instance`` is ``None`` or
    a non-renderable fragment.
    """
    from pyink.core.component import ComponentInstance, HostInstance

    if instance is None:
        return None
    if isinstance(instance, ComponentInstance):
        # A component returns a list of children — collapse to the first
        # host child for layout purposes. PR3 test trees always have a
        # single root host, so this is the common case.
        if len(instance.children) == 1:
            return build_flex_tree(instance.children[0])
        # Multi-root component: wrap in an anonymous row container.
        kids: list[FlexNode] = []
        for c in instance.children:
            kid = build_flex_tree(c)
            if kid is not None:
                kids.append(kid)
        if not kids:
            return None
        return FlexNode(kind="box", style=FlexStyle(), children=kids, source=instance)
    if isinstance(instance, HostInstance):
        return _build_host_node(instance)
    return None


def _inline_text_element(element: Any) -> str | None:
    """Recursively flatten a nested ``text`` Element into its string body.

    A ``text`` element's children may include ``str``, lazy callables,
    or further nested ``text`` elements (the latter coming from
    :func:`pyink.components.Newline` or nested :func:`Text` calls). This
    helper joins them in source order, evaluating callables once.
    """
    from pyink.core.element import Element

    if not isinstance(element, Element):
        return None
    if element.type != "text":
        return None
    parts: list[str] = []
    for child in element.children:
        if isinstance(child, str):
            parts.append(child)
        elif callable(child):
            result = child()
            if result is not None:
                parts.append(str(result))
        elif isinstance(child, Element):
            sub = _inline_text_element(child)
            if sub is not None:
                parts.append(sub)
    return "".join(parts)


def _build_host_node(instance: Any) -> FlexNode:
    from pyink.core.component import HostInstance

    assert isinstance(instance, HostInstance)
    element_type = instance.element.type
    # Host instances always carry a string tag (function components are
    # represented by ``ComponentInstance``), but the static type is the
    # wider ``ElementType`` alias — narrow explicitly.
    assert isinstance(element_type, str)
    style = FlexStyle.from_props(instance.element.props)
    node = FlexNode(
        kind=element_type,
        style=style,
        source=instance,
        props=dict(instance.element.props),
    )
    if element_type == "text":
        # Resolve text leaves immediately (callables evaluated once).
        # Nested text host elements (e.g. ``Newline()``) are inlined
        # verbatim — their body joins this leaf's body. Styling on a
        # nested Text element is dropped (PR4 does not implement ink's
        # nested-Text transform pipeline).
        from pyink.core.element import Element

        parts: list[str] = []
        for leaf in instance.children:
            if isinstance(leaf, str):
                parts.append(leaf)
            elif callable(leaf):
                result = leaf()
                if result is not None:
                    parts.append(str(result))
            elif isinstance(leaf, Element):
                # Inline nested text host (Newline / nested Text).
                sub = _inline_text_element(leaf)
                if sub is not None:
                    parts.append(sub)
        node.text = "".join(parts)
        return node
    # Non-text host: mount child host instances (skip component instances
    # by flattening to their inner host children).
    for child in instance.children:
        sub_node = build_flex_tree(child)
        if sub_node is not None:
            node.children.append(sub_node)
    return node


# ---------------------------------------------------------------------------
# Measurement (text leaves and leaf boxes)
# ---------------------------------------------------------------------------


def _measure_text(node: FlexNode, max_width: float, max_height: float) -> tuple[int, int]:
    """Return ``(width, height)`` for a text leaf under bounded maxima.

    ``max_width`` / ``max_height`` use ``math.inf`` to mean "unbounded".
    Text wrapping honours an int ``max_width``; height is the number of
    wrapped lines.
    """
    text = node.text or ""
    if "\n" in text:
        # Multi-paragraph: measure each segment.
        max_line_w = 0
        total_h = 0
        for para in text.split("\n"):
            w, h = _measure_paragraph(para, max_width)
            max_line_w = max(max_line_w, w)
            total_h += h
        return max_line_w, max(1, total_h)
    return _measure_paragraph(text, max_width)


def _measure_paragraph(text: str, max_width: float) -> tuple[int, int]:
    if not text:
        return 0, 1
    natural_w = string_width(text)
    if max_width == float("inf") or natural_w <= max_width:
        return natural_w, 1
    # Wrap and recount.
    lines = wrap_text(text, int(max_width), mode="wrap")
    width = max((string_width(ln) for ln in lines), default=0)
    return width, len(lines)


# ---------------------------------------------------------------------------
# Layout main entry
# ---------------------------------------------------------------------------


def layout_root(
    root: FlexNode,
    *,
    columns: int = 80,
    rows: int | None = None,
) -> LayoutNode:
    """Top-level entry point — lays out ``root`` against the viewport."""
    # Root: width/height defaults to the viewport.
    style = root.style
    avail_w = style.width if style.width is not None else columns
    if style.height is not None:
        avail_h = style.height
    elif rows is not None:
        avail_h = rows
    else:
        avail_h = -1  # indeterminate
    _layout_node(
        node=root,
        avail_w=avail_w,
        avail_h=avail_h,
        avail_w_mode="exactly",
        avail_h_mode="exactly" if style.height is not None or rows is not None else "at-most",
    )
    # Apply the root's own margin: offset content by margin.left/top and
    # grow the outer dimensions so the renderer includes the margin band.
    _apply_root_margin(root)
    # Final pass: assign coordinates & produce LayoutNode tree.
    return _to_layout_tree(root, offset_x=0, offset_y=0)


def _apply_root_margin(root: FlexNode) -> None:
    """Offset the root's content by its own margin and grow its size."""
    m = root.style.margin
    if m.left == 0 and m.top == 0 and m.right == 0 and m.bottom == 0:
        return
    if root.kind == "text":
        # Text root — shift its own position so the renderer paints
        # the content into the inner content box.
        root.layout_x = m.left
        root.layout_y = m.top
    else:
        for child in root.children:
            child.layout_x += m.left
            child.layout_y += m.top
    root.layout_width += m.horizontal
    root.layout_height += m.vertical


def _to_layout_tree(node: FlexNode, offset_x: int, offset_y: int) -> LayoutNode:
    out = LayoutNode(
        x=node.layout_x,
        y=node.layout_y,
        width=node.layout_width,
        height=node.layout_height,
        content=node.text if node.kind == "text" else None,
        kind=node.kind,
        style=_snapshot_style(node.style),
        props=dict(node.props),
    )
    for child in node.children:
        out.children.append(
            _to_layout_tree(child, offset_x + node.layout_x, offset_y + node.layout_y)
        )
    return out


def _snapshot_style(style: FlexStyle) -> dict[str, Any]:
    return {
        "direction": style.direction,
        "justify": style.justify,
        "align_items": style.align_items,
        "align_content": style.align_content,
        "flex_wrap": style.flex_wrap,
        "padding": (
            style.padding.top,
            style.padding.right,
            style.padding.bottom,
            style.padding.left,
        ),
        "margin": (
            style.margin.top,
            style.margin.right,
            style.margin.bottom,
            style.margin.left,
        ),
        "width": style.width,
        "height": style.height,
        "hasBorder": style.has_border,
        "borderTop": style.border_top,
        "borderRight": style.border_right,
        "borderBottom": style.border_bottom,
        "borderLeft": style.border_left,
    }


# ---------------------------------------------------------------------------
# Layout algorithm
# ---------------------------------------------------------------------------


def _layout_node(
    node: FlexNode,
    avail_w: float,
    avail_h: float,
    avail_w_mode: MeasureMode,
    avail_h_mode: MeasureMode,
) -> None:
    """Recursive layout. Fills ``layout_*`` on ``node`` and descendants."""

    style = node.style

    # ----- 1. Resolve self size (border-box-style: includes padding) -----
    if style.width is not None:
        own_w = max(0, style.width - style.padding.horizontal)
    elif avail_w_mode == "exactly":
        own_w = max(0, int(avail_w) - style.padding.horizontal)
    else:
        own_w = -1  # indeterminate — fit content

    if style.height is not None:
        own_h = max(0, style.height - style.padding.vertical)
    elif avail_h_mode == "exactly":
        own_h = max(0, int(avail_h) - style.padding.vertical)
    else:
        own_h = -1

    # When ``width`` is indeterminate but a ``maxWidth`` is set, treat
    # the max as an upper bound for children measurement. Similarly for
    # height. Also clamp an explicit ``avail_w`` "at-most" measurement
    # down to the max.
    effective_max_w = avail_w
    if style.max_width is not None:
        capped = float(style.max_width - style.padding.horizontal)
        effective_max_w = (
            min(effective_max_w, capped) if effective_max_w != float("inf") else capped
        )
    effective_max_h = avail_h
    if style.max_height is not None:
        capped = float(style.max_height - style.padding.vertical)
        effective_max_h = (
            min(effective_max_h, capped) if effective_max_h != float("inf") else capped
        )

    # Apply min/max constraints.
    if own_w >= 0:
        if style.min_width is not None:
            own_w = max(own_w, style.min_width - style.padding.horizontal)
        if style.max_width is not None:
            own_w = min(own_w, max(0, style.max_width - style.padding.horizontal))
    if own_h >= 0:
        if style.min_height is not None:
            own_h = max(own_h, style.min_height - style.padding.vertical)
        if style.max_height is not None:
            own_h = min(own_h, max(0, style.max_height - style.padding.vertical))

    # ----- 2. Text leaf -----
    if node.kind == "text":
        max_w_for_text = (
            own_w if own_w >= 0
            else (effective_max_w if effective_max_w != float("inf") else float("inf"))
        )
        w, h = _measure_text(node, max_w_for_text, float("inf"))
        node.measured_width = w
        node.measured_height = h
        # Reflow the text content at the resolved width so the renderer
        # receives multi-line content directly. PR4 honours the Text
        # ``wrap`` prop (``"wrap"`` default, ``"hard"`` /
        # ``"truncate"`` family for explicit control).
        text_wrap = node.props.get("wrap", "wrap")
        if (
            node.text
            and max_w_for_text != float("inf")
            and max_w_for_text >= 1
            and "\n" not in node.text
            and string_width(node.text) > max_w_for_text
        ):
            wrapped = wrap_text(node.text, int(max_w_for_text), mode=text_wrap)
            node.text = "\n".join(wrapped)
            # Recompute measurements from wrapped content.
            w = max((string_width(line) for line in wrapped), default=0)
            h = len(wrapped)
        if own_w < 0:
            # Apply min/max width even for auto-width text nodes.
            if style.min_width is not None:
                w = max(w, style.min_width - style.padding.horizontal)
            if style.max_width is not None:
                w = min(w, max(0, style.max_width - style.padding.horizontal))
        if own_h < 0:
            if style.min_height is not None:
                h = max(h, style.min_height - style.padding.vertical)
            if style.max_height is not None:
                h = min(h, max(0, style.max_height - style.padding.vertical))
        node.layout_width = (w if own_w < 0 else own_w) + style.padding.horizontal
        node.layout_height = (h if own_h < 0 else own_h) + style.padding.vertical
        return

    # ----- 3. Container: lay out children -----
    if not node.children:
        empty_w = own_w if own_w >= 0 else 0
        empty_h = own_h if own_h >= 0 else 0
        if own_w < 0:
            if style.min_width is not None:
                empty_w = max(empty_w, style.min_width - style.padding.horizontal)
            if style.max_width is not None:
                empty_w = min(empty_w, max(0, style.max_width - style.padding.horizontal))
        if own_h < 0:
            if style.min_height is not None:
                empty_h = max(empty_h, style.min_height - style.padding.vertical)
            if style.max_height is not None:
                empty_h = min(empty_h, max(0, style.max_height - style.padding.vertical))
        node.layout_width = empty_w + style.padding.horizontal
        node.layout_height = empty_h + style.padding.vertical
        return

    if style.is_column():
        _layout_column(node, own_w, own_h, avail_w, avail_h, effective_max_w, effective_max_h)
    else:
        _layout_row(node, own_w, own_h, avail_w, avail_h, effective_max_w, effective_max_h)

    # After child layout, apply min/max to the resolved size when we
    # were in "fit-content" mode (own_w/own_h started as -1).
    if own_w < 0 and style.min_width is not None:
        node.layout_width = max(
            node.layout_width, style.min_width
        )
    if own_w < 0 and style.max_width is not None:
        node.layout_width = min(node.layout_width, max(0, style.max_width))
    if own_h < 0 and style.min_height is not None:
        node.layout_height = max(node.layout_height, style.min_height)
    if own_h < 0 and style.max_height is not None:
        node.layout_height = min(node.layout_height, max(0, style.max_height))


def _resolve_child_main_basis(child: FlexNode, parent_is_column: bool) -> int:
    """Return the child's flex-basis as a fixed main-axis size hint.

    Falls back to the child's natural main-axis content size when
    ``flex_basis`` is ``None`` ("auto").
    """
    if parent_is_column:
        if child.style.flex_basis is not None:
            return child.style.flex_basis
        if child.style.height is not None:
            return child.style.height
        return -1  # auto — measure later
    else:
        if child.style.flex_basis is not None:
            return child.style.flex_basis
        if child.style.width is not None:
            return child.style.width
        return -1


def _layout_row(
    node: FlexNode,
    own_w: int,
    own_h: int,
    avail_w: float,
    avail_h: float,
    effective_max_w: float,
    effective_max_h: float,
) -> None:
    style = node.style
    children = _ordered_children(style, node.children)
    gap = style.column_gap or style.gap

    # Phase 1: measure each child's main-size (width) and cross-size (height).
    main_sizes: list[int] = []  # natural desired main size
    cross_sizes: list[int] = []  # natural cross size
    main_is_fixed: list[bool] = []  # child has fixed main axis (no flex-shrink targets)

    # For "at-most" width measurement (when our own width is indeterminate,
    # the children still wrap at the parent's available width).
    child_max_w = own_w if own_w >= 0 else (
        effective_max_w if effective_max_w != float("inf") else float("inf")
    )

    for child in children:
        basis = _resolve_child_main_basis(child, parent_is_column=False)
        # Fixed main size — but still measure text children to know cross
        # size and to apply min/max constraints. Auto measures content width.
        child_w = basis if basis >= 0 else -1

        # Determine cross size hint (height):
        child_h_hint = child.style.height if child.style.height is not None else -1

        # Recursively layout child with constraint modes.
        # When child_w is fixed → exactly; when auto → at-most; when
        # child has flex-grow potential and parent is bounded exactly,
        # we'll re-do this after main-axis distribution.
        c_w = float(child_w) if child_w >= 0 else child_max_w
        c_w_mode: MeasureMode = "exactly" if child_w >= 0 else "at-most"
        # Cross-axis (height): at-most by default so children keep
        # intrinsic height for cross-axis alignment. Stretch applied
        # later via a re-pass.
        c_h = float(child_h_hint) if child_h_hint >= 0 else (
            float(own_h) if own_h >= 0 else avail_h
        )
        c_h_mode: MeasureMode = "exactly" if child_h_hint >= 0 else "at-most"

        _layout_node(child, c_w, c_h, c_w_mode, c_h_mode)
        # After layout, child.layout_width/height are filled. For
        # auto-width children we keep the measured width. Margins add
        # to the space the child occupies in the parent.
        m = child.style.margin
        actual_w = child.layout_width if child_w < 0 else child_w
        main_sizes.append(actual_w + m.horizontal)
        cross_sizes.append(child.layout_height + m.vertical)
        main_is_fixed.append(child_w >= 0 or child.style.flex_grow > 0)

    n = len(children)
    total_gap = gap * (n - 1) if n > 1 else 0
    natural_main = sum(main_sizes) + total_gap

    if own_w >= 0:
        free = own_w - natural_main
        if free != 0 and n > 0:
            main_sizes = _distribute_main(
                children, main_sizes, main_is_fixed, free, gap
            )

    # Cross-axis size: max child cross size, or own_h if bounded.
    cross_size = own_h if own_h >= 0 else (max(cross_sizes) if cross_sizes else 0)

    # Phase 2: position children along main axis (justify) and cross (align).
    container_main = own_w if own_w >= 0 else natural_main
    positions = _position_main(style, main_sizes, total_gap, container_main)
    positions = _mirror_main_if_reverse(style, positions, main_sizes, container_main)

    for i, child in enumerate(children):
        m = child.style.margin
        child.layout_x = positions[i] + m.left + style.padding.left
        # Cross-axis alignment.
        align = _resolve_align(child.style.align_self, style.align_items)
        cross_extent = cross_sizes[i]
        child.layout_y = (
            _align_cross(align, cross_extent, cross_size, child) + m.top + style.padding.top
        )

    # Recompute children layout_width/height when main axis was modified
    # by grow/shrink (set width to allocated main size). Also re-run
    # layout with "exactly" mode so descendants pick up the new size.
    for i, child in enumerate(children):
        allocated_w = main_sizes[i]
        if (
            child.style.flex_basis is None
            and child.layout_width != allocated_w
            and (child.style.width is None or child.style.flex_shrink > 0)
        ):
            # Temporarily clear the explicit width so the re-layout
            # honours the shrunk allocation rather than re-applying
            # the original fixed width.
            saved_width = child.style.width
            if child.style.flex_shrink > 0:
                child.style.width = None
            _layout_node(child, float(allocated_w), float(own_h) if own_h >= 0 else avail_h,
                         "exactly", "exactly" if own_h >= 0 else "at-most")
            child.style.width = saved_width
            # Force the layout_width to the allocated size — re-layout
            # may re-measure content and keep natural width.
            child.layout_width = allocated_w

    # Stretch align for cross axis: re-layout children that were stretched.
    for i, child in enumerate(children):
        align = _resolve_align(child.style.align_self, style.align_items)
        if align == "stretch" and child.style.height is None:
            target_h = cross_size
            if child.layout_height != target_h:
                _layout_node(
                    child,
                    float(main_sizes[i]),
                    float(target_h),
                    "exactly",
                    "exactly",
                )

    # Re-apply positions — the re-layout passes above reset
    # ``layout_x`` / ``layout_y`` on each child.
    for i, child in enumerate(children):
        m = child.style.margin
        child.layout_x = positions[i] + m.left + style.padding.left
        align = _resolve_align(child.style.align_self, style.align_items)
        cross_extent = child.layout_height + m.vertical
        child.layout_y = (
            _align_cross(align, cross_extent, cross_size, child) + m.top + style.padding.top
        )

    node.layout_width = (own_w if own_w >= 0 else natural_main) + style.padding.horizontal
    node.layout_height = cross_size + style.padding.vertical


def _layout_column(
    node: FlexNode,
    own_w: int,
    own_h: int,
    avail_w: float,
    avail_h: float,
    effective_max_w: float,
    effective_max_h: float,
) -> None:
    style = node.style
    children = _ordered_children(style, node.children)
    gap = style.row_gap or style.gap

    main_sizes: list[int] = []
    cross_sizes: list[int] = []
    main_is_fixed: list[bool] = []

    child_max_h = own_h if own_h >= 0 else (
        effective_max_h if effective_max_h != float("inf") else float("inf")
    )
    child_max_w = own_w if own_w >= 0 else (
        effective_max_w if effective_max_w != float("inf") else float("inf")
    )

    for child in children:
        basis = _resolve_child_main_basis(child, parent_is_column=True)
        child_h = basis if basis >= 0 else -1
        child_w_hint = child.style.width if child.style.width is not None else -1

        c_h = float(child_h) if child_h >= 0 else child_max_h
        c_h_mode: MeasureMode = "exactly" if child_h >= 0 else "at-most"
        # Width is at-most by default so children keep intrinsic width
        # for cross-axis alignment. Stretch is applied later via a
        # re-pass when ``align_items="stretch"`` resolves.
        c_w = float(child_w_hint) if child_w_hint >= 0 else child_max_w
        c_w_mode: MeasureMode = "exactly" if child_w_hint >= 0 else "at-most"

        _layout_node(child, c_w, c_h, c_w_mode, c_h_mode)
        m = child.style.margin
        actual_h = child.layout_height if child_h < 0 else child_h
        main_sizes.append(actual_h + m.vertical)
        cross_sizes.append(child.layout_width + m.horizontal)
        main_is_fixed.append(child_h >= 0 or child.style.flex_grow > 0)

    n = len(children)
    total_gap = gap * (n - 1) if n > 1 else 0
    natural_main = sum(main_sizes) + total_gap

    if own_h >= 0:
        free = own_h - natural_main
        if free != 0 and n > 0:
            main_sizes = _distribute_main(
                children, main_sizes, main_is_fixed, free, gap
            )

    cross_size = own_w if own_w >= 0 else (max(cross_sizes) if cross_sizes else 0)

    container_main = own_h if own_h >= 0 else natural_main
    positions = _position_main(style, main_sizes, total_gap, container_main)
    positions = _mirror_main_if_reverse(style, positions, main_sizes, container_main)

    for i, child in enumerate(children):
        m = child.style.margin
        child.layout_y = positions[i] + m.top + style.padding.top
        align = _resolve_align(child.style.align_self, style.align_items)
        cross_extent = cross_sizes[i]
        child.layout_x = (
            _align_cross(align, cross_extent, cross_size, child) + m.left + style.padding.left
        )

    for i, child in enumerate(children):
        allocated_h = main_sizes[i]
        if (
            child.style.flex_basis is None
            and child.layout_height != allocated_h
            and (child.style.height is None or child.style.flex_shrink > 0)
        ):
            saved_height = child.style.height
            if child.style.flex_shrink > 0:
                child.style.height = None
            _layout_node(child,
                         float(own_w) if own_w >= 0 else avail_w,
                         float(allocated_h),
                         "exactly" if own_w >= 0 else "at-most",
                         "exactly")
            child.style.height = saved_height
            child.layout_height = allocated_h

    for i, child in enumerate(children):
        align = _resolve_align(child.style.align_self, style.align_items)
        if align == "stretch" and child.style.width is None:
            target_w = cross_size
            if child.layout_width != target_w:
                _layout_node(
                    child,
                    float(target_w),
                    float(main_sizes[i]),
                    "exactly",
                    "exactly",
                )

    # Re-apply positions — the re-layout passes above reset
    # ``layout_x`` / ``layout_y`` on each child.
    for i, child in enumerate(children):
        m = child.style.margin
        child.layout_y = positions[i] + m.top + style.padding.top
        align = _resolve_align(child.style.align_self, style.align_items)
        cross_extent = child.layout_width + m.horizontal
        child.layout_x = (
            _align_cross(align, cross_extent, cross_size, child) + m.left + style.padding.left
        )

    node.layout_width = cross_size + style.padding.horizontal
    node.layout_height = (own_h if own_h >= 0 else natural_main) + style.padding.vertical


def _ordered_children(style: FlexStyle, children: list[FlexNode]) -> list[FlexNode]:
    # Keep source order — reverse directions are handled by mirroring
    # main-axis positions in :func:`_mirror_main_if_reverse`.
    return list(children)


def _distribute_main(
    children: list[FlexNode],
    sizes: list[int],
    fixed: list[bool],
    free: float,
    gap: int,
) -> list[int]:
    """Distribute ``free`` space via grow (positive) or shrink (negative)."""
    n = len(children)
    out = list(sizes)
    if free > 0:
        # Grow: children with flex_grow > 0 share proportionally.
        total_grow = sum(
            children[i].style.flex_grow
            for i in range(n)
            if children[i].style.flex_grow > 0
        )
        if total_grow <= 0:
            return out
        shares = [0.0] * n
        for i in range(n):
            g = children[i].style.flex_grow
            if g > 0:
                shares[i] = free * (g / total_grow)
        # Floor each at 0 and assign — keep simple (no rounding dance).
        for i in range(n):
            out[i] = sizes[i] + int(round(shares[i]))
        return out
    if free < 0:
        # Shrink: weighted by flexShrink × main_size (Yoga behaviour).
        overflow = -free
        total_weight = 0.0
        weights = [0.0] * n
        for i in range(n):
            sh = children[i].style.flex_shrink
            if sh > 0 and sizes[i] > 0:
                weights[i] = sh * sizes[i]
                total_weight += weights[i]
        if total_weight <= 0:
            return out
        # Scale so all weights sum to overflow.
        for i in range(n):
            if weights[i] > 0:
                shrink_amount = overflow * (weights[i] / total_weight)
                out[i] = max(0, sizes[i] - int(round(shrink_amount)))
        return out
    return out


def _position_main(
    style: FlexStyle,
    sizes: list[int],
    total_gap: int,
    container_main: int,
) -> list[int]:
    """Return absolute main-axis offsets for each child."""
    n = len(sizes)
    if n == 0:
        return []
    content = sum(sizes) + total_gap
    free = container_main - content
    positions: list[int] = []
    cursor = 0
    if style.justify == "flex-start" or free < 0:
        for i, s in enumerate(sizes):
            positions.append(cursor)
            cursor += s + (style.main_gap() if i < n - 1 else 0)
    elif style.justify == "flex-end":
        cursor = container_main - content
        for i, s in enumerate(sizes):
            positions.append(cursor)
            cursor += s + (style.main_gap() if i < n - 1 else 0)
    elif style.justify == "center":
        cursor = (container_main - content) // 2
        for i, s in enumerate(sizes):
            positions.append(cursor)
            cursor += s + (style.main_gap() if i < n - 1 else 0)
    elif style.justify == "space-between":
        gaps = n - 1
        between = free // gaps if gaps > 0 else 0
        for i, s in enumerate(sizes):
            positions.append(cursor)
            cursor += s + (style.main_gap() + between if i < n - 1 else 0)
    elif style.justify == "space-around":
        # n children → 2n half-gaps. Distribute remainder to the trailing
        # half-gaps (matches ink's "extra space at end" rounding quirk;
        # the known yoga bug for the first child means this can deviate
        # by one cell — see test_flex_justify_space_around_xfail).
        gaps = 2 * n
        unit = free / gaps if gaps > 0 else 0.0
        leading = int(unit / 2) if unit >= 1 else 0
        cursor = leading
        between = int(unit)
        for _i, s in enumerate(sizes):
            positions.append(cursor)
            cursor += s + style.main_gap() + between
    elif style.justify == "space-evenly":
        # n+1 gaps. Each gap = free / (n+1), with the **remainder**
        # distributed to the trailing gaps (matches ink test fixtures).
        gaps = n + 1
        if gaps > 0:
            base_unit = free // gaps
            extra = free - base_unit * gaps
        else:
            base_unit = 0
            extra = 0
        gap_sizes = [base_unit + (1 if i >= gaps - extra else 0) for i in range(gaps)]
        cursor = gap_sizes[0] if gap_sizes else 0
        for i, s in enumerate(sizes):
            positions.append(cursor)
            mid = gap_sizes[i + 1] if i + 1 < len(gap_sizes) else 0
            cursor += s + style.main_gap() + mid
    else:  # pragma: no cover - exhaustive
        for i, s in enumerate(sizes):
            positions.append(cursor)
            cursor += s + (style.main_gap() if i < n - 1 else 0)
    return positions


def _resolve_align(self_align: AlignSelf, parent_align: AlignItems) -> AlignItems:
    if self_align == "auto":
        return parent_align
    return self_align


def _mirror_main_if_reverse(
    style: FlexStyle,
    positions: list[int],
    sizes: list[int],
    container_main: int,
) -> list[int]:
    """Mirror main-axis positions for ``row-reverse`` / ``column-reverse``.

    The reversed directions lay children out from the container's end
    rather than its start. Since :func:`_ordered_children` already
    flipped the child list, mirroring each ``pos`` to
    ``container_main - pos - size`` keeps the justify semantics intact
    while shifting everything to the opposite edge.
    """
    if not style.is_reverse():
        return positions
    return [container_main - p - s for p, s in zip(positions, sizes, strict=True)]


def _align_cross(
    align: AlignItems,
    child_cross: int,
    container_cross: int,
    child: FlexNode,
) -> int:
    """Return cross-axis offset for ``child`` given alignment."""
    if align == "flex-start":
        return 0
    if align == "flex-end":
        return max(0, container_cross - child_cross)
    if align == "center":
        return max(0, (container_cross - child_cross) // 2)
    if align == "baseline":
        # Approximation: top-align (true baseline tracking needs PR4 fonts).
        return 0
    # stretch — offset 0; the child's cross-size was grown to fill.
    return 0


# ---------------------------------------------------------------------------
# Public entry point (operates on host instance tree)
# ---------------------------------------------------------------------------


def layout(
    root: Any,
    *,
    columns: int = 80,
    rows: int | None = None,
) -> LayoutNode:
    """Convenience entry — accepts a host instance (from the reconciler).

    Builds a :class:`FlexNode` tree, runs layout, returns the
    :class:`LayoutNode` tree. ``root`` may also be a pre-built
    :class:`FlexNode` (advanced use).
    """
    if isinstance(root, FlexNode):
        flex_root = root
    else:
        maybe = build_flex_tree(root)
        if maybe is None:
            return LayoutNode(x=0, y=0, width=0, height=0)
        flex_root = maybe
    return layout_root(flex_root, columns=columns, rows=rows)
