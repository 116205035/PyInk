"""PyInk layout engine (PR3).

The layout package is responsible for turning a mounted host-instance
tree into a positioned :class:`LayoutNode` tree, then rendering that
tree to a plain-text snapshot string.

Public surface:

* :func:`layout` — entry point that takes a host instance and returns
  a :class:`LayoutNode`.
* :class:`LayoutNode`, :class:`FlexStyle`, :class:`FlexNode` — the
  internal and external data structures.
* :func:`string_width`, :func:`wrap_text` — text measurement helpers.
* :func:`render_layout_to_string` — final plain-text renderer.
"""

from pyink.layout.flex import (
    AlignContent,
    AlignItems,
    AlignSelf,
    Edges,
    FlexDirection,
    FlexNode,
    FlexStyle,
    FlexWrap,
    JustifyContent,
    LayoutNode,
    MeasureMode,
    build_flex_tree,
    layout,
    layout_root,
)
from pyink.layout.measure import WrapMode, string_width, wrap_text
from pyink.layout.render_layout import render_layout_to_string

__all__ = [
    "AlignContent",
    "AlignItems",
    "AlignSelf",
    "Edges",
    "FlexDirection",
    "FlexNode",
    "FlexStyle",
    "FlexWrap",
    "JustifyContent",
    "LayoutNode",
    "MeasureMode",
    "WrapMode",
    "build_flex_tree",
    "layout",
    "layout_root",
    "render_layout_to_string",
    "string_width",
    "wrap_text",
]
