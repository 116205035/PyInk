"""PyInk — a Python ink-style TUI framework built on signals.

PR1 only exports the reactive core (``signal`` / ``computed`` / ``effect`` /
``ref`` / ``batch``). Component, layout, render and hooks APIs are filled in
by later PRs.
"""

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

__all__ = [
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
]

__version__ = "0.1.0"

# TODO(PR2): from pyink.core.reconciler import ...
# TODO(PR3): layout.flex / layout.measure
# TODO(PR4): from pyink.components.box import Box, Text, Newline, Spacer, Static, Transform
# TODO(PR5): from pyink.render import render, render_to_string
# TODO(PR6): from pyink.hooks import use_input, use_app, use_window_size
