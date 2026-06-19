"""Sync test renderer — flattens an Element tree to a string.

PR2 introduced a no-layout concatenation renderer. PR3 replaces it with
a real flex pipeline:

1. **Mount** the tree via the reconciler (so effects register their
   cleanup, mirroring ink's lifecycle semantics).
2. **Layout** the resulting host-instance tree via
   :mod:`pyink.layout.flex` to obtain a positioned
   :class:`LayoutNode` tree.
3. **Paint** the layout tree via :func:`render_layout_to_string` —
   plain text only (ANSI styling arrives in PR4).
4. **Unmount** so effect cleanups run.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyink.core.element import Element
from pyink.core.reconciler import Reconciler
from pyink.layout import layout, render_layout_to_string

__all__ = ["RenderOptions", "render_to_string"]


@dataclass(frozen=True, slots=True)
class RenderOptions:
    """Options bag for :func:`render_to_string`."""

    columns: int = 80
    rows: int | None = None


def render_to_string(
    tree: Element,
    *,
    columns: int = 80,
) -> str:
    """Render ``tree`` to a plain string snapshot.

    The tree is mounted, laid out, painted, then unmounted — the
    function does not keep the tree alive. Callable leaves are
    evaluated synchronously a single time; signal reads inside them
    return current snapshot values but do **not** establish
    subscriptions.
    """
    options = RenderOptions(columns=columns)
    reconciler = Reconciler()
    root = reconciler.mount(tree, parent=None)
    try:
        if root is None:
            return ""
        layout_tree = layout(root, columns=options.columns, rows=options.rows)
        return render_layout_to_string(layout_tree)
    finally:
        reconciler.unmount(root)
