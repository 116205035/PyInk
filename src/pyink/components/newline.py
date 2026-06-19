"""``Newline`` — emit one or more ``\\n`` characters (PR4).

``Newline(count=1)`` returns a ``text`` element whose body is
``"\\n" * count``. It must be a child of :func:`Text` (or appear in a
context that allows raw text leaves).
"""

from __future__ import annotations

from pyink.core.element import Element, create_element

__all__ = ["Newline"]


def Newline(count: int = 1) -> Element:
    """Return a text leaf containing ``count`` newline characters.

    ``count`` defaults to 1. ``count <= 0`` produces an empty text
    element (matches ink — no negative-line semantics).
    """
    if count < 0:
        count = 0
    return create_element("text", "\n" * count)
