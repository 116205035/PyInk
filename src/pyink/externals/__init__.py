"""PyInk extension components.

Externals are opt-in: users do ``from pyink.externals import Spinner``
rather than importing them from the top-level package (PRD Decision 5 —
externals carry heavier dependencies / non-essential surface area and
stay out of the default namespace).
"""

from pyink.externals.divider import Divider
from pyink.externals.link import Link
from pyink.externals.spinner import SPINNERS, Spinner
from pyink.externals.streaming_text import StreamingText

__all__ = [
    "Divider",
    "Link",
    "SPINNERS",
    "Spinner",
    "StreamingText",
]
