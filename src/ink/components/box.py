"""``Box`` — flex container host element (PR4).

``Box(*children, **props)`` is the Pythonic spelling of
``create_element("box", *children, **props)``. Per PRD Decision 8 the
intended child type is ``Element`` (raw strings should be wrapped in
:class:`Text`). ``Box`` itself does not enforce this — it forwards
every child to :func:`create_element` unchanged. When a stray ``str``
or callable does sneak through, the reconciler auto-wraps it as a
``"text"`` host so the tree still renders; callers get a clear
``TypeError`` from :func:`create_element` only for genuinely
unsupported types (ints, dicts, …).

Supported ``props`` come in two groups:

* **Layout** (handled by :class:`ink.layout.flex.FlexStyle`):
  ``flexDirection`` / ``justifyContent`` / ``alignItems`` / ``alignSelf``
  / ``flexWrap`` / ``padding`` (and the per-side / X / Y shorthand
  variants) / ``margin`` (same) / ``width`` / ``height`` / ``minWidth``
  / ``maxWidth`` / ``minHeight`` / ``maxHeight`` / ``gap`` /
  ``columnGap`` / ``rowGap`` / ``flexGrow`` / ``flexShrink`` /
  ``flexBasis``.
* **Decoration** (PR4 — consumed by the renderer, see
  :mod:`ink.render.ansi`):
  ``borderStyle`` (string alias or 8-key dict), ``borderColor`` /
  ``borderTopColor`` / ``borderRightColor`` / ``borderBottomColor`` /
  ``borderLeftColor``, the matching ``…BackgroundColor`` family, the
  matching ``…DimColor`` family, and the four ``borderTop`` /
  ``borderRight`` / ``borderBottom`` / ``borderLeft`` visibility flags.
  ``backgroundColor`` paints the box interior.
  ``flushBackgroundToWidth`` (Path-A fix, default ``False``) — when
  ``True``, the ``backgroundColor`` spans the box's full layout width
  even on rows that contain text leaves (legacy behaviour only
  covered the text cells on those rows because the per-cell fill
  bailed on non-empty cells). Each affected row carries its own
  ``open + reset`` SGR pair so the closing reset is never stripped by
  ``rstrip`` (the bug behind the SGR leak onto sibling rows after a
  soft-wrap). Block-level surfaces (CC-style user-message bands) opt
  in; inline coloured tokens leave it ``False``.
"""

from __future__ import annotations

from typing import Any

from ink.core.element import Element, create_element

__all__ = ["Box"]


def Box(*children: Any, **props: Any) -> Element:
    """Create a ``box`` host element.

    ``children`` is the variadic positional list (Decision 8 — children
    before props, matching JSX). Nested ``tuple`` / ``list`` are
    flattened (fragment unpacking) and ``None`` / ``True`` / ``False``
    are filtered by :func:`create_element`.

    Per Decision 8 the preferred child type is :class:`Element` — raw
    strings should be wrapped in :func:`Text`. ``Box`` does not enforce
    this at the type level (it forwards children to
    :func:`create_element` unchanged); the reconciler auto-wraps stray
    ``str`` / callable children as ``"text"`` hosts so the tree still
    renders. Callers get a clear ``TypeError`` from
    :func:`create_element` only for genuinely unsupported child types
    such as ``int`` or ``dict``.
    """
    return create_element("box", *children, **props)
