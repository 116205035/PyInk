"""Layout-time context for the current text-leaf measurement width.

Some text leaves carry callables whose rendered output depends on the
available width (e.g. ``Markdown``'s reactive branch, which pre-renders
the Markdown to a snapshot string). Without knowing the actual width
the parent's content box grants the leaf, the callable falls back to
the viewport width and produces snapshot strings that overflow their
container — manifesting as scrambled nested-box borders during
streaming Markdown re-render.

The width is exposed via a :class:`contextvars.ContextVar` set by the
layout engine around text-leaf measurement. Code that needs the
current width calls :func:`get_current_text_width`; ``None`` means
"the layout pass did not establish a finite width" (e.g. measurement
under unbounded width), in which case callers should fall back to a
sensible default (typically the viewport width).
"""

from __future__ import annotations

from contextvars import ContextVar, Token

__all__ = [
    "get_current_text_width",
    "set_current_text_width",
    "reset_current_text_width",
]

#: Active measurement width during a text-leaf layout pass. ``None``
#: means "no finite width established" (the layout pass is measuring
#: under an unbounded width). Callers should fall back to a sensible
#: default in that case.
_current_text_width: ContextVar[int | None] = ContextVar(
    "pyink_current_text_width", default=None
)


def get_current_text_width() -> int | None:
    """Return the width the active text-leaf measurement is fitting to.

    ``None`` means no finite width has been established (the layout
    pass is running under unbounded width).
    """
    return _current_text_width.get()


def set_current_text_width(width: int | None) -> Token[int | None]:
    """Bind ``width`` as the active text-leaf measurement width.

    Returns a token that must be passed to :func:`reset_current_text_width`
    to restore the prior value. ``None`` clears the binding (meaning
    "unbounded").
    """
    return _current_text_width.set(width)


def reset_current_text_width(token: Token[int | None]) -> None:
    """Restore the prior text-leaf measurement width."""
    _current_text_width.reset(token)
