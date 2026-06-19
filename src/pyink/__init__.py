"""PyInk — a Python ink-style TUI framework built on signals.

PR1 exports the reactive core (``signal`` / ``computed`` / ``effect`` /
``ref`` / ``batch``). PR2 adds the Element/Component/Reconciler surface
and a sync ``render_to_string`` test renderer. Layout, real components,
the full render pipeline and hooks arrive in later PRs.
"""

from pyink.core.component import (
    ComponentInstance,
    HostInstance,
    Instance,
)
from pyink.core.element import (
    Element,
    ElementChild,
    ElementType,
    HostType,
    create_element,
)
from pyink.core.reconciler import Reconciler
from pyink.core.scheduler import Scheduler
from pyink.core.signal import (
    Computed,
    CyclicDependency,
    Dispose,
    Effect,
    Ref,
    Signal,
    batch,
    computed,
    effect,
    ref,
    signal,
)
from pyink.layout import (
    Edges,
    FlexNode,
    FlexStyle,
    LayoutNode,
    build_flex_tree,
    layout,
    layout_root,
    render_layout_to_string,
    string_width,
    wrap_text,
)
from pyink.render import render_to_string

__all__ = [
    # Signals
    "CyclicDependency",
    "Computed",
    "Dispose",
    "Effect",
    "Ref",
    "Signal",
    "batch",
    "computed",
    "effect",
    "ref",
    "signal",
    # Element / Component / Reconciler (PR2)
    "ComponentInstance",
    "Element",
    "ElementChild",
    "ElementType",
    "HostInstance",
    "HostType",
    "Instance",
    "Reconciler",
    "Scheduler",
    "create_element",
    # Layout (PR3)
    "Edges",
    "FlexNode",
    "FlexStyle",
    "LayoutNode",
    "build_flex_tree",
    "layout",
    "layout_root",
    "render_layout_to_string",
    "string_width",
    "wrap_text",
    # Render (PR2/PR3 test renderer)
    "render_to_string",
]

__version__ = "0.1.0"

# TODO(PR4): from pyink.components.box import Box, Text, Newline, Spacer, Static, Transform
# TODO(PR5): from pyink.render import render, Instance.rerender / wait_until_exit / clear
# TODO(PR6): from pyink.hooks import use_input, use_app, use_window_size
