"""PyInk extension components.

Externals are opt-in: users do ``from pyink.externals import Spinner``
rather than importing them from the top-level package (PRD Decision 5 —
externals carry heavier dependencies / non-essential surface area and
stay out of the default namespace).
"""

from pyink.externals.divider import Divider
from pyink.externals.highlighted_code import DEFAULT_THEME, HighlightedCode
from pyink.externals.link import Link
from pyink.externals.markdown import DEFAULT_MARKDOWN_THEME, Markdown
from pyink.externals.spinner import SPINNERS, Spinner
from pyink.externals.streaming_text import StreamingText

__all__ = [
    "DEFAULT_MARKDOWN_THEME",
    "DEFAULT_THEME",
    "Divider",
    "HighlightedCode",
    "Link",
    "Markdown",
    "SPINNERS",
    "Spinner",
    "StreamingText",
]
