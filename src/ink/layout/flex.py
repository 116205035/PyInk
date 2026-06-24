"""Flex layout engine — Yoga subset (PR3).

The engine operates on :class:`FlexNode` trees built from the reconciler's
:class:`ink.core.component.Instance` tree. Layout proceeds in two passes:

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

PRD Decision 13 — style props accept ``T | Callable[[], T]``: the helper
:func:`_resolve` evaluates a prop value at layout time. ``build_flex_tree``
runs inside the render-loop effect's tracking context, so callable props
that read ``signal.value`` here establish the subscription.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from ink.core.signal import Signal
from ink.layout.measure import string_width, wrap_text

_T = TypeVar("_T")


def _resolve(prop: _T | Callable[[], _T] | Signal[_T]) -> _T:
    """Evaluate ``prop`` if it's a Signal / callable, else return unchanged.

    Per PRD Decision 13, every style prop (``color`` / ``bold`` /
    ``borderStyle`` / …) accepts a ``Callable[[], T]`` in addition to a
    plain ``T``. Phase 5 extends the accepted shapes to include
    :class:`ink.Signal` directly (so ``scroll_offset`` can be passed
    as ``signal(0)`` without a wrapping lambda). The unwrap happens
    **here** — at layout time — so when the layout pass runs inside the
    render-loop effect's tracking context, the ``.value`` read on a
    Signal establishes a subscription to it.
    """
    if isinstance(prop, Signal):
        return prop.value
    if callable(prop):
        return prop()
    return prop

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
    "clear_box_refs",
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
        # PRD Decision 13: ``borderStyle`` and the per-side booleans may
        # be callables — evaluate them here so the layout effect
        # subscribes to any signals read inside.
        if _resolve(props.get("borderStyle")) is not None:
            s.has_border = True
            s.border_top = _resolve(props.get("borderTop", True)) is not False
            s.border_right = _resolve(props.get("borderRight", True)) is not False
            s.border_bottom = _resolve(props.get("borderBottom", True)) is not False
            s.border_left = _resolve(props.get("borderLeft", True)) is not False
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
    # PRD Decision 13 / Phase 5: dimension + box-model props
    # (``width`` / ``height`` / ``minWidth`` / … and every margin /
    # padding edge, which all funnel through here) accept a
    # ``Signal`` / ``Callable[[], int]`` in addition to a plain int —
    # the unwrap happens at layout time so the render-loop effect
    # subscribes to any signal read here. This is what lets a component
    # drive a reactive ``marginTop`` (e.g. a scroll offset) or a
    # reactive ``height`` (e.g. a resize-aware viewport) the same way
    # ``borderStyle`` / ``color`` already support callables.
    v = _resolve(v)
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
    # Deferred text renderer for leaves whose source is a single
    # callable. When set, the text body is produced *inside* the
    # layout pass (see :func:`_layout_node`'s text branch) rather than
    # at flex-tree construction. This is what lets width-aware
    # callables (e.g. ``Markdown``'s reactive branch, which pre-renders
    # the document to a snapshot string) read the actual measurement
    # width via :mod:`ink.layout._text_width_context` and size their
    # output to fit, instead of overflowing the parent's content box.
    # ``None`` for plain-string / mixed leaves, which are still
    # evaluated eagerly at construction time.
    text_renderer: Callable[[], str] | None = None
    # Original unwrapped source text — preserved verbatim so that
    # repeated layout passes (the engine re-lays-out children after
    # grow/shrink distribution) can re-wrap at the *current* resolved
    # width instead of being locked into the first pass's width. Only
    # the first wrap mutates ``text`` (joining wrapped lines with
    # ``\n``), so without this snapshot later passes see the joined
    # text, hit the ``"\n" in node.text`` guard and skip re-wrapping,
    # which lets long paragraphs overflow their narrower containers.
    original_text: str | None = None
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
    # Phase 2 PR7: a ``Ref[LayoutNode] | None`` propagated from the Box
    # element's ``ref`` prop. ``None`` for nodes without a ref. We attach
    # it to the FlexNode (rather than re-reading ``props`` downstream) so
    # the layout pass can clear the ref's value before the next paint and
    # re-populate it from the freshly built :class:`LayoutNode` — keeping
    # callers from observing stale measurements. The cleared-then-set
    # sequence is also what enables ``Instance.unmount`` to detect "the
    # element is gone" by leaving the ref at ``None``.
    ref: Any = None
    # Bug 1 (PR2): minimum main-axis content size. Acts like CSS flex's
    # ``min-height: auto`` / ``min-width: auto`` (default: min-content).
    # Shrink distribution clamps each child's main-axis allocation to at
    # least this value so a text leaf can never be compressed below a
    # single row / column (which would otherwise cause sibling overlap
    # when the leaf paints at height 0 but still draws its content).
    # Text leaves are ``1``; box containers recursively aggregate their
    # children's min-content (see :func:`_compute_min_content_main`).
    min_content_main: int = 1


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
    from ink.core.component import ComponentInstance, HostInstance

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
        # PR7: a host box flagged ``_ink_static`` (emitted by the Static
        # component) is a layout sentinel — its content has already been
        # written to stdout above the live frame, so the layout engine
        # must treat it as zero-sized and never position its children.
        if (
            instance.element.type == "box"
            and instance.element.props.get("_ink_static") is True
        ):
            return None
        # Phase 2 PR5: a ``"provider"`` host is a layout-transparent
        # wrapper whose only job is to push/pop the context stack at
        # mount/unmount time. Collapse it to a fragment of its children
        # so it contributes no box of its own.
        if instance.element.type == "provider":
            kids2: list[FlexNode] = []
            for c in instance.children:
                kid = build_flex_tree(c)
                if kid is not None:
                    kids2.append(kid)
            if not kids2:
                return None
            if len(kids2) == 1:
                return kids2[0]
            return FlexNode(kind="box", style=FlexStyle(), children=kids2, source=instance)
        return _build_host_node(instance)
    return None


def clear_box_refs(instance: Any) -> None:
    """Recursively clear ``ref.value`` for every Box in ``instance``'s tree.

    Phase 2 PR7 hook used by :meth:`ink.render.instance.Instance.unmount`
    so consumers calling :func:`measure_element` / :func:`use_box_metrics`
    after unmount see ``has_measured=False`` (rather than a stale snapshot
    pointing at a LayoutNode whose host instance has been torn down).

    Walks the same host-instance shape the reconciler produces — a tree of
    :class:`ink.core.component.HostInstance` / ``ComponentInstance`` with
    a ``children`` list (raw leaves for ``"text"`` hosts). Non-element
    inputs are a no-op so callers can pass ``None`` defensively.
    """
    from ink.core.component import ComponentInstance, HostInstance

    if instance is None:
        return
    if isinstance(instance, ComponentInstance):
        for child in instance.children:
            clear_box_refs(child)
        return
    if isinstance(instance, HostInstance):
        # Only Boxes (and other non-text hosts) carry a ``ref`` prop.
        if instance.element.type != "text":
            ref = instance.element.props.get("ref")
            if ref is not None:
                ref.value = None
        for child in instance.children:
            # Text leaves are raw str / callable — skip them.
            if isinstance(child, (HostInstance, ComponentInstance)):
                clear_box_refs(child)
        return


def _inline_text_element(element: Any) -> str | None:
    """Recursively flatten a nested ``text`` Element into its string body.

    A ``text`` element's children may include ``str``, lazy callables,
    or further nested ``text`` elements (the latter coming from
    :func:`ink.components.Newline` or nested :func:`Text` calls). This
    helper joins them in source order, evaluating callables once.
    """
    from ink.core.element import Element

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


#: Style props consumed by the renderer (:mod:`ink.layout.render_layout`)
#: that accept ``T | Callable[[], T]`` (PRD Decision 13). Layout-affecting
#: props (``borderStyle`` / ``borderTop`` / …) are also resolved inside
#: :meth:`FlexStyle.from_props`; we re-list the booleans here so the
#: snapshot ``node.props`` carries the resolved values too.
_DECORATION_PROPS: tuple[str, ...] = (
    # Text style
    "color",
    "backgroundColor",
    "bold",
    "italic",
    "underline",
    "strikethrough",
    "inverse",
    "dimColor",
    "wrap",
    # Box border style + colours
    "borderStyle",
    "borderColor",
    "borderTopColor",
    "borderRightColor",
    "borderBottomColor",
    "borderLeftColor",
    "borderBackgroundColor",
    "borderTopBackgroundColor",
    "borderRightBackgroundColor",
    "borderBottomBackgroundColor",
    "borderLeftBackgroundColor",
    "borderDimColor",
    "borderTopDimColor",
    "borderRightDimColor",
    "borderBottomDimColor",
    "borderLeftDimColor",
    # Per-edge visibility flags
    "borderTop",
    "borderRight",
    "borderBottom",
    "borderLeft",
    # Phase 5 — vertical scroll window for text leaves whose content
    # overflows the granted height. Resolved here so the value the
    # renderer reads from ``node.props`` is already a plain ``int`` (or
    # ``None``) and so signal / callable forms subscribe the render
    # loop.
    "scroll_offset",
)


def _resolve_decoration_props(props: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``props`` with every callable decoration prop resolved.

    Per PRD Decision 13 the renderer reads resolved (non-callable) values
    from ``node.props``. We invoke each callable here — at layout time —
    so when the layout pass runs inside the render-loop effect's tracking
    context the signal reads establish subscriptions.
    """
    out = dict(props)
    for key in _DECORATION_PROPS:
        if key in out:
            out[key] = _resolve(out[key])
    return out


def _build_host_node(instance: Any) -> FlexNode:
    from ink.core.component import HostInstance

    assert isinstance(instance, HostInstance)
    element_type = instance.element.type
    # Host instances always carry a string tag (function components are
    # represented by ``ComponentInstance``), but the static type is the
    # wider ``ElementType`` alias — narrow explicitly.
    assert isinstance(element_type, str)
    style = FlexStyle.from_props(instance.element.props)
    # PRD Decision 13 — resolve callable style props at layout time so the
    # render-loop effect subscribes to any signals they read. We resolve
    # only the decoration props the renderer reads downstream; layout
    # props (flexDirection / padding / …) are still considered plain
    # values (those that *do* affect layout — borderStyle / borderTop /
    # etc. — are resolved inside :meth:`FlexStyle.from_props`).
    resolved_props = _resolve_decoration_props(instance.element.props)
    # Phase 2 PR7: Box's ``ref`` prop is a measurement handle, not a
    # style/decoration prop. Pull it out of the props dict before the
    # renderer (or the layout style parser) sees it — otherwise it would
    # leak into ``FlexStyle.from_props`` (ignored) and into the resolved
    # ``node.props`` snapshot that the renderer iterates. The ref is
    # carried on the FlexNode via the dedicated ``ref`` field so the
    # post-layout traversal can back-fill ``ref.value``.
    box_ref = resolved_props.pop("ref", None)
    node = FlexNode(
        kind=element_type,
        style=style,
        source=instance,
        props=resolved_props,
        ref=box_ref,
    )
    if element_type == "text":
        # Resolve text leaves immediately (callables evaluated once).
        # Nested text host elements (e.g. ``Newline()``) are inlined
        # verbatim — their body joins this leaf's body. Styling on a
        # nested Text element is dropped (PR4 does not implement ink's
        # nested-Text transform pipeline).
        from ink.core.element import Element

        # Deferred-rendering fast path: a leaf whose only child is a
        # single callable. We store the callable on the FlexNode and
        # let the layout pass invoke it once the available width is
        # known (see :func:`_layout_node`'s text branch). This is what
        # lets width-aware renderers (e.g. ``Markdown``'s reactive
        # branch) size their output to fit, instead of overflowing
        # their parent's content box. Mixed str+callable leaves are
        # rare; they fall through to the eager path below.
        if (
            len(instance.children) == 1
            and callable(instance.children[0])
            and not isinstance(instance.children[0], Element)
        ):
            renderer = instance.children[0]
            # The narrowing above (callable + not Element) plus the
            # ``list[Any]`` annotation on ``HostInstance.children``
            # makes this assignment safe under mypy strict. The
            # renderer returns ``str`` per the ``Text`` contract.
            node.text_renderer = renderer
            node.text = ""
            # ``original_text`` stays ``None`` so the wrap pass below
            # never tries to re-wrap a string we have not produced yet.
            return node

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
        # Snapshot the unwrapped source so re-layout passes can re-wrap
        # at the current resolved width (see ``FlexNode.original_text``).
        node.original_text = node.text
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
    """Top-level entry point — lays out ``root`` against the viewport.

    ``rows`` is treated as a **max-rows upper bound**, not a forced
    height. The root box auto-sizes to its content and only clips when
    content actually exceeds the cap. This matches the CSS
    ``maxHeight`` semantics that inner boxes already honour (see
    ``test_flex.py::test_max_height_allows_content_to_drive_size``) and
    matches ink's TypeScript behaviour. Callers wanting a forced
    height pin ``<Box height=N>`` at the root instead — ``style.height``
    still triggers the ``exactly`` path below.

    Bug context (Jarvis Phase 1): when the render pipeline auto-detected
    ``rows=terminal.rows`` and passed it down, the old ``exactly`` mode
    forced the frame to fill the whole screen. Static-output writes
    above the frame then overflowed the viewport and scrolled, pushing
    older static lines out of view. With ``at-most`` the frame only
    claims the rows it actually needs.
    """
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
        # ``style.height`` is the only "force exactly N rows" trigger.
        # ``rows`` (from the render pipeline) is now a max-rows cap —
        # content fits inside it, the frame no longer stretches to fill.
        avail_h_mode="exactly" if style.height is not None else "at-most",
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
    # Phase 2 PR7 — back-fill the Box's ``ref`` with the freshly-built
    # LayoutNode so :func:`measure_element` / :func:`use_box_metrics` see
    # the post-layout coordinates. The ref was attached to the FlexNode
    # in :func:`_build_host_node`. We assign unconditionally (even when
    # the ref already pointed at an older node) because the layout pass
    # may have re-positioned or re-sized the element on this tick.
    if node.ref is not None:
        node.ref.value = out
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
    # CSS max-height/max-width semantics: a set ``max*`` is an upper bound
    # on the content-driven size, NOT a fill target. So when ``width`` /
    # ``height`` are unset but the corresponding ``max*`` is set, we go
    # auto (``-1``) and let content drive the size; the ``max*`` clamps
    # the resolved size afterwards. Only when neither ``max*`` nor an
    # explicit size is set do we obey an "exactly" parent constraint.
    if style.width is not None:
        own_w = max(0, style.width - style.padding.horizontal)
    elif style.max_width is not None:
        own_w = -1  # auto; capped by max_width after measurement
    elif avail_w_mode == "exactly":
        own_w = max(0, int(avail_w) - style.padding.horizontal)
    else:
        own_w = -1  # indeterminate — fit content

    if style.height is not None:
        own_h = max(0, style.height - style.padding.vertical)
    elif style.max_height is not None:
        own_h = -1  # auto; capped by max_height after measurement
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
            else (effective_max_w if effective_max_w != float("inf") else float("inf")
                  )
        )
        # Deferred text renderer (single-callable leaf, see
        # :func:`_build_host_node`). Invoke it now — the measurement
        # width is known — so width-aware renderers (e.g. Markdown's
        # reactive branch) can size their output to fit. The renderer
        # is memoised by the caller (see ``_cached_render`` in
        # ``markdown.py``) so re-invocation across layout passes is
        # cheap; we still cache the last (width, text) on the node so
        # subsequent passes with the same width skip the call. The
        # ``_rendered_width`` prop tracks the width the cached text
        # was rendered at, mirroring the ``_wrapped_width`` bookkeeping
        # used by the wrap path below.
        if node.text_renderer is not None:
            ctx_width = (
                int(max_w_for_text)
                if max_w_for_text != float("inf") and max_w_for_text >= 1
                else None
            )
            prev_rendered_w = node.props.get("_rendered_width")
            if ctx_width is None or prev_rendered_w != ctx_width:
                from ink.layout._text_width_context import (
                    reset_current_text_width,
                    set_current_text_width,
                )

                token = set_current_text_width(ctx_width)
                try:
                    rendered = node.text_renderer()
                finally:
                    reset_current_text_width(token)
                node.text = str(rendered) if rendered is not None else ""
                node.original_text = node.text
                node.props["_rendered_width"] = ctx_width
                # The freshly produced text supersedes whatever the wrap
                # pass cached on a previous layout pass. ``_wrapped_width``
                # records "node.text has already been wrapped to width W";
                # that invariant only holds while ``node.text`` stays the
                # wrapped output. Re-running the renderer overwrites
                # ``node.text`` with the *unwrapped* source, so the stale
                # ``_wrapped_width`` must be cleared — otherwise the wrap
                # guard below (which skips re-wrapping unless the new width
                # is strictly tighter than ``_wrapped_width``) keeps the
                # full-width text whenever a later pass widens the
                # constraint, letting long single-line content overflow the
                # container (Bug: ``truncate-end`` status line poking past
                # the right border after the renderer re-ran at a wider
                # measurement width).
                node.props.pop("_wrapped_width", None)
        # Re-wrap from the **original** source text — but only when the
        # current ``max_w_for_text`` is *tighter* than any width we've
        # already wrapped to. The flex engine re-lays-out children
        # multiple times (initial measure, after grow/shrink, after
        # cross-axis stretch), and each pass may feed a different
        # ``max_w_for_text``. Re-wrapping to a wider width on a later
        # pass would let the joined lines overflow the narrower
        # container they were sized for, so we track the tightest wrap
        # seen and only re-wrap when the constraint shrinks further.
        # The ``original_text`` snapshot is what we re-wrap from so a
        # previous wrap's joined output never pins the content shape.
        source_text = node.original_text if node.original_text is not None else node.text
        text_wrap = node.props.get("wrap", "wrap")
        prev_wrapped_w = node.props.get("_wrapped_width")
        should_wrap = (
            source_text is not None
            and max_w_for_text != float("inf")
            and max_w_for_text >= 1
            and string_width(source_text) > max_w_for_text
            and (prev_wrapped_w is None or max_w_for_text < prev_wrapped_w)
        )
        if should_wrap:
            assert source_text is not None  # narrowed for mypy
            node.text = source_text
            wrapped = wrap_text(source_text, int(max_w_for_text), mode=text_wrap)
            node.text = "\n".join(wrapped)
            node.props["_wrapped_width"] = int(max_w_for_text)
            w = max((string_width(line) for line in wrapped), default=0)
            h = len(wrapped)
        else:
            # Measure the current text (source on first pass, prior
            # wrap on later passes) under the current constraint.
            w, h = _measure_text(node, max_w_for_text, float("inf"))
        node.measured_width = w
        node.measured_height = h
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
            # Honour the parent's cross-axis ``at-most`` bound (which now
            # also carries the parent's ``maxHeight`` cap via
            # ``child_max_h``). When the text content overflows the bound,
            # pin the leaf's resolved ``layout_height`` to the bound so
            # the painter sees a finite ``node.height`` and engages the
            # ``scroll_offset`` window — otherwise the leaf would report
            # its full content height and the painter would paint every
            # line, with the parent's outer clip masking the overflow but
            # never sliding the cursor row into view.
            if (
                avail_h_mode == "at-most"
                and avail_h != float("inf")
                and avail_h >= 0
            ):
                h = min(h, max(0, int(avail_h)))
        node.layout_width = (w if own_w < 0 else own_w) + style.padding.horizontal
        node.layout_height = (h if own_h < 0 else own_h) + style.padding.vertical
        # Text leaf: min-content is 1 main-axis unit (never compresses to 0).
        _compute_min_content_main(node)
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
        # Empty container: min-content collapses to its own padding band.
        _compute_min_content_main(node)
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
    # ``at-most`` avail_h clamp: when the parent handed down an at-most
    # bound (e.g. the render pipeline's ``rows=N`` max-rows) and this
    # box auto-sized to its content, the resolved height must still
    # respect that bound. Without this clamp ``rows=N`` would let
    # overflowing content grow past the cap (the cap becomes a hint
    # rather than an upper bound). Mirrors the ``max_height`` clamp
    # above; ``avail_h > 0`` skips the indeterminate (-1) case.
    if own_h < 0 and avail_h_mode == "at-most" and avail_h > 0:
        node.layout_height = min(node.layout_height, max(0, int(avail_h)))

    # Aggregate the children's min-content (already populated by the
    # recursive calls above) into this container's ``min_content_main``
    # so its own parent's shrink pass has a floor to clamp against.
    _compute_min_content_main(node)


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

    # For "at-most" width measurement (when our own width is indeterminate,
    # the children still wrap at the parent's available width).
    child_max_w = own_w if own_w >= 0 else (
        effective_max_w if effective_max_w != float("inf") else float("inf")
    )
    # Mirror on the cross axis: when our own height is indeterminate but a
    # ``maxHeight`` is set, the cap applies to children's cross-axis
    # measurement as well. A text leaf whose content overflows the cap then
    # resolves its own ``layout_height`` to the cap (not the full content
    # height), which is what lets the painter's ``scroll_offset`` window
    # kick in and slide the cursor row into view.
    child_max_h = own_h if own_h >= 0 else (
        effective_max_h if effective_max_h != float("inf") else float("inf")
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
            float(own_h) if own_h >= 0 else child_max_h
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

    n = len(children)
    total_gap = gap * (n - 1) if n > 1 else 0
    natural_main = sum(main_sizes) + total_gap

    if own_w >= 0:
        free = own_w - natural_main
        if free != 0 and n > 0:
            main_sizes = _distribute_main(
                children, main_sizes, free, gap
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
            # may re-measure content and keep natural width. Keep
            # ``measured_width`` in sync so any downstream reader (e.g.
            # a future cross-axis measurement or border drawer) sees the
            # post-shrink width rather than the stale intrinsic value.
            child.layout_width = allocated_w
            child.measured_width = allocated_w

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

    n = len(children)
    total_gap = gap * (n - 1) if n > 1 else 0
    natural_main = sum(main_sizes) + total_gap

    if own_h >= 0:
        free = own_h - natural_main
        if free != 0 and n > 0:
            main_sizes = _distribute_main(
                children, main_sizes, free, gap
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
            # Mirror the row-pass fix: keep ``measured_height`` in sync
            # with the shrunk allocation so downstream readers don't see
            # a stale intrinsic value.
            child.measured_height = allocated_h

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


def _compute_min_content_main(node: FlexNode) -> int:
    """Compute ``node.min_content_main`` from children (recursive).

    Mirrors CSS flex's ``min-height: auto`` / ``min-width: auto`` default
    (which resolves to min-content):

    * **Text leaf**: fixed at ``1`` (a text leaf occupies at least one
      row/column, even if its content doesn't fit).
    * **Box container**: aggregates children's min-content. Along the
      main axis, a row's min-content is ``max(children min-width)`` (the
      widest child sets the floor); a column's is ``sum(children
      min-height)`` (all children stack vertically). Either way the
      parent's own padding, border (folded into padding by
      :meth:`FlexStyle.from_props`), and main-axis gaps are added.

    The recursion reads each child's ``min_content_main`` field, which
    is populated *before* distribution (see :func:`_layout_row` /
    :func:`_layout_column`). The result is written back to
    ``node.min_content_main`` so :func:`_distribute_main` can read it
    uniformly from any parent.
    """
    if node.kind == "text":
        node.min_content_main = 1
        return 1
    style = node.style
    children = node.children
    if not children:
        # Empty container — min-content is just its own padding band.
        node.min_content_main = 0
        return 0
    if style.is_column():
        # Column main axis = vertical → sum child min-heights + gaps.
        gap = style.row_gap or style.gap
        inner = sum(c.min_content_main for c in children) + gap * (len(children) - 1)
    else:
        # Row main axis = horizontal → max child min-width + gaps.
        gap = style.column_gap or style.gap
        inner = (
            max((c.min_content_main for c in children), default=0)
            + gap * (len(children) - 1)
        )
    # ``style.padding`` already includes the border thickness (PR4 folded
    # border into padding so layout reserves the cells the renderer fills).
    padding_axis = (
        style.padding.vertical if style.is_column() else style.padding.horizontal
    )
    total = inner + padding_axis
    node.min_content_main = total
    return total


def _distribute_main(
    children: list[FlexNode],
    sizes: list[int],
    free: float,
    gap: int,
) -> list[int]:
    """Distribute ``free`` space via grow (positive) or shrink (negative).

    Shrink is **clamped per child** at ``children[i].min_content_main``
    so a text leaf can never be compressed below 1 main-axis unit (the
    classic "0-height row that still paints content → overlap" bug).
    When early clamping leaves leftover overflow, the remainder is
    re-distributed across children that still have shrink headroom
    (size > min_content_main), iterating until either the overflow is
    fully absorbed or every child is pinned at its min-content floor.
    """
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
        # Two-pass: first distribute by weight, then iteratively
        # redistribute any overflow that couldn't be absorbed because a
        # child hit its min-content floor.
        overflow_remaining = -free
        # Track how much shrink headroom each child still has.
        # headroom[i] = current_size - min_content_main.
        headroom = [
            max(0, out[i] - children[i].min_content_main) for i in range(n)
        ]
        # Iteratively absorb overflow across whichever children still
        # have headroom. Each pass recomputes weights from the current
        # remaining ``flexShrink × current_size`` so a child that was
        # already pinned to its floor stops contributing.
        while overflow_remaining > 0:
            total_weight = 0.0
            weights = [0.0] * n
            for i in range(n):
                if headroom[i] <= 0:
                    continue
                sh = children[i].style.flex_shrink
                if sh > 0 and out[i] > 0:
                    weights[i] = sh * out[i]
                    total_weight += weights[i]
            if total_weight <= 0:
                # No shrinkable children left — overflow will escape the
                # container (PR3's clip-to-content-box will mask it).
                break
            absorbed_this_pass = 0
            for i in range(n):
                if weights[i] <= 0 or headroom[i] <= 0:
                    continue
                share = overflow_remaining * (weights[i] / total_weight)
                shrink_amount = int(round(share))
                # Don't shrink past headroom / below min-content.
                shrink_amount = min(shrink_amount, headroom[i])
                if shrink_amount <= 0:
                    continue
                out[i] -= shrink_amount
                headroom[i] = max(0, out[i] - children[i].min_content_main)
                absorbed_this_pass += shrink_amount
            if absorbed_this_pass == 0:
                # Rounding collapsed every share to 0 — stop to avoid
                # an infinite loop.
                break
            overflow_remaining -= absorbed_this_pass
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
