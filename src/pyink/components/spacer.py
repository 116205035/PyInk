"""``Spacer`` — flexible space consumer (PR4).

``Spacer(**props)`` returns a ``box`` element with ``flexGrow=1``. The
``size`` prop sets an explicit width (mapped to ``width`` so the flex
engine reserves that many cells).
"""

from __future__ import annotations

from typing import Any

from pyink.core.element import Element, create_element

__all__ = ["Spacer"]


def Spacer(**props: Any) -> Element:
    """Return a flex-grow box that consumes available main-axis space.

    Recognised props:

    * ``size`` — int. When set, the spacer is a fixed-width box of
      that many cells (overriding the default ``flexGrow=1``).
    * Any other ``Box`` prop is forwarded (e.g. ``flexDirection``,
      ``margin``).
    """
    size = props.pop("size", None)
    if size is not None:
        props.setdefault("width", int(size))
    else:
        props.setdefault("flexGrow", 1)
    return create_element("box", **props)
