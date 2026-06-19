"""``Text`` — text leaf host element (PR4).

Unlike :func:`Box`, ``Text`` accepts raw ``str`` / ``Callable[[], str]``
children directly (PRD Decision 8 — strings are only allowed inside
``Text``). It is the spelling of ``create_element("text", ...)``
augmented with type-checked style props.

Supported ``props``:

* ``color`` / ``backgroundColor`` — colour specs (``"red"``,
  ``"#ff0000"``, ``"rgb(255,0,0)"``, ``"ansi256(9)"``).
* ``bold`` / ``italic`` / ``underline`` / ``strikethrough`` /
  ``inverse`` / ``dimColor`` — boolean style toggles.
* ``wrap`` — one of ``"wrap"`` / ``"hard"`` / ``"truncate"`` /
  ``"truncate-start"`` / ``"truncate-middle"`` / ``"truncate-end"``.
  Forwarded to :func:`pyink.layout.measure.wrap_text` by the layout
  engine.
"""

from __future__ import annotations

from typing import Any

from pyink.core.element import Element, create_element

__all__ = ["Text"]


def Text(*children: Any, **props: Any) -> Element:
    """Create a ``text`` host element.

    ``children`` may contain ``str``, callable returning ``str``, or
    nested ``Element`` instances. ``None`` / ``True`` / ``False`` are
    filtered; nested ``tuple`` / ``list`` are flattened (so list
    comprehensions work via ``Text(*parts)`` unpacking).

    The returned element is identical to ``create_element("text", ...)``
    — the renderer reads ``color`` / ``bold`` / etc. from
    ``element.props`` directly.
    """
    return create_element("text", *children, **props)
