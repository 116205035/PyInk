"""PyInk extension components.

Externals are opt-in: users do ``from pyink.externals import Spinner``
rather than importing them from the top-level package (PRD Decision 5 —
externals carry heavier dependencies / non-essential surface area and
stay out of the default namespace).
"""

from pyink.externals.big_text import FONTS as BIG_TEXT_FONTS
from pyink.externals.big_text import BigText
from pyink.externals.confirm_input import ConfirmInput
from pyink.externals.diff import StructuredDiff
from pyink.externals.divider import Divider
from pyink.externals.gradient import Gradient
from pyink.externals.highlighted_code import DEFAULT_THEME, HighlightedCode
from pyink.externals.link import Link
from pyink.externals.markdown import DEFAULT_MARKDOWN_THEME, Markdown
from pyink.externals.progress_bar import ProgressBar
from pyink.externals.select_input import SelectInput
from pyink.externals.spinner import SPINNERS, Spinner
from pyink.externals.streaming_text import StreamingText
from pyink.externals.table import Table
from pyink.externals.task_list import TaskItem, TaskList
from pyink.externals.text_input import TextInput

__all__ = [
    "BIG_TEXT_FONTS",
    "BigText",
    "ConfirmInput",
    "DEFAULT_MARKDOWN_THEME",
    "DEFAULT_THEME",
    "Divider",
    "Gradient",
    "HighlightedCode",
    "Link",
    "Markdown",
    "ProgressBar",
    "SPINNERS",
    "SelectInput",
    "Spinner",
    "StreamingText",
    "StructuredDiff",
    "Table",
    "TaskItem",
    "TaskList",
    "TextInput",
]
