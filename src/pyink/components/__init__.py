"""PyInk built-in host components (PR4).

These four factories wrap :func:`pyink.core.element.create_element` for
the four host tags the layout engine understands:

* :func:`Box` — ``"box"`` flex container.
* :func:`Text` — ``"text"`` leaf.
* :func:`Newline` — convenience ``text`` leaf with ``"\\n"`` body.
* :func:`Spacer` — ``box`` with ``flexGrow=1`` (or fixed ``width`` when
  ``size=`` is given).
"""

from pyink.components.box import Box
from pyink.components.newline import Newline
from pyink.components.spacer import Spacer
from pyink.components.text import Text

__all__ = ["Box", "Newline", "Spacer", "Text"]
