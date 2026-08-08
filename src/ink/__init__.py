"""PyInk — a Python ink-style TUI framework built on signals.

PR1 exports the reactive core (``signal`` / ``computed`` / ``effect`` /
``ref`` / ``batch``). PR2 adds the Element/Component/Reconciler surface
and a sync ``render_to_string`` test renderer. Layout, real components,
the full render pipeline and hooks arrive in later PRs.
"""

from ink.components import Box, Newline, Spacer, Static, Text, Transform
from ink.core.component import (
    ComponentInstance,
    HostInstance,
)
from ink.core.context import Context, Provider, create_context
from ink.core.element import (
    Element,
    ElementChild,
    ElementType,
    HostType,
    create_element,
)
from ink.core.reconciler import Reconciler
from ink.core.scheduler import Scheduler
from ink.core.signal import (
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
from ink.hooks import (
    AppHandle,
    BoxMetrics,
    FocusHandle,
    FocusManager,
    FocusManagerHandle,
    NullFocusManager,
    WindowSize,
    measure_element,
    use_app,
    use_box_metrics,
    use_context,
    use_focus,
    use_focus_manager,
    use_input,
    use_interval,
    use_window_size,
)
from ink.layout import (
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
from ink.render import Instance, render, render_to_string

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
    # Context system (Phase 2 PR5)
    "Context",
    "Provider",
    "create_context",
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
    # Built-in components (PR4 + PR7)
    "Box",
    "Newline",
    "Spacer",
    "Static",
    "Text",
    "Transform",
    # Hooks (PR6 + Phase 2 PR1 + PR5 + PR6 + PR7)
    "AppHandle",
    "BoxMetrics",
    "FocusHandle",
    "FocusManager",
    "FocusManagerHandle",
    "NullFocusManager",
    "WindowSize",
    "measure_element",
    "use_app",
    "use_box_metrics",
    "use_context",
    "use_focus",
    "use_focus_manager",
    "use_input",
    "use_interval",
    "use_window_size",
    # Render (PR2/PR3 test renderer + PR5 live pipeline)
    "render",
    "render_to_string",
]

__version__ = "0.2.2"
