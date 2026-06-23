"""Tests for :func:`pyink.externals.ProgressBar` (Phase 6 PR2).

``ProgressBar`` is a function component wrapping a layout-time
callable child — the same pattern :func:`Spinner` uses — so signal
writes re-paint on the next frame. For unit tests we use the
synchronous :func:`render_to_string` test renderer, which evaluates
the callable exactly once.

Coverage:

* Element shape — ``ProgressBar`` returns an element whose ``type`` is
  a function component.
* 0% / 50% / 100% render the right fill counts.
* ``width`` controls the bar cell count.
* Custom ``character`` / ``remaining_character`` are honoured.
* ``show_percentage=False`` suppresses the suffix; the default
  suffix format is ``" NN%"`` (3-digit zero-padded).
* ``color`` is forwarded to the emitted :func:`Text` leaf.
* Signal / callable sources resolve at layout time.
* ``value`` outside ``[0, 1]`` is clamped.
* Style props (``bold``) are forwarded.
* Validation: bad ``value`` / ``width`` / ``character`` raises
  ``TypeError``.
* ``ProgressBar`` is exported from ``pyink.externals`` but NOT from
  the top-level ``pyink`` package.
"""

from __future__ import annotations

from typing import Any

from pyink import Box, render_to_string, signal
from pyink.core.element import Element
from pyink.externals import ProgressBar

#: Default filled character (U+2588 FULL BLOCK).
_FILL = "█"

#: Default remaining character (U+2591 LIGHT SHADE).
_REMAIN = "░"


# ---------------------------------------------------------------------------
# Element shape
# ---------------------------------------------------------------------------


def test_progress_bar_returns_function_component_element() -> None:
    """``ProgressBar`` wraps its props in a function component element."""
    el = ProgressBar(value=0.5)
    assert isinstance(el, Element)
    assert callable(el.type)


# ---------------------------------------------------------------------------
# Value → fill count
# ---------------------------------------------------------------------------


def test_progress_bar_zero_value() -> None:
    """``value=0.0`` renders an all-remaining bar."""
    tree: Any = Box(
        ProgressBar(value=0.0, width=10, show_percentage=False),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    assert out == _REMAIN * 10


def test_progress_bar_full_value() -> None:
    """``value=1.0`` renders an all-filled bar."""
    tree: Any = Box(
        ProgressBar(value=1.0, width=10, show_percentage=False),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    assert out == _FILL * 10


def test_progress_bar_half_value() -> None:
    """``value=0.5`` renders half filled, half remaining."""
    tree: Any = Box(
        ProgressBar(value=0.5, width=10, show_percentage=False),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    assert out == _FILL * 5 + _REMAIN * 5


def test_progress_bar_quarter_value() -> None:
    """``value=0.25`` renders 1/4 filled, 3/4 remaining."""
    tree: Any = Box(
        ProgressBar(value=0.25, width=8, show_percentage=False),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    assert out == _FILL * 2 + _REMAIN * 6


def test_progress_bar_clamps_overshoot_value() -> None:
    """``value > 1.0`` is clamped to 1.0."""
    tree: Any = Box(
        ProgressBar(value=1.5, width=10, show_percentage=False),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    assert out == _FILL * 10


def test_progress_bar_clamps_negative_value() -> None:
    """``value < 0.0`` is clamped to 0.0."""
    tree: Any = Box(
        ProgressBar(value=-0.5, width=10, show_percentage=False),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    assert out == _REMAIN * 10


# ---------------------------------------------------------------------------
# Width
# ---------------------------------------------------------------------------


def test_progress_bar_custom_width() -> None:
    """``width`` controls the bar cell count."""
    tree: Any = Box(
        ProgressBar(value=0.5, width=20, show_percentage=False),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    assert out == _FILL * 10 + _REMAIN * 10


def test_progress_bar_zero_width_shows_only_percentage() -> None:
    """``width=0`` suppresses the bar portion, leaving only the suffix."""
    tree: Any = Box(
        ProgressBar(value=0.5, width=0),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    assert out == "  50%"


# ---------------------------------------------------------------------------
# Custom characters
# ---------------------------------------------------------------------------


def test_progress_bar_custom_characters() -> None:
    """``character`` / ``remaining_character`` are honoured."""
    tree: Any = Box(
        ProgressBar(
            value=0.5,
            width=10,
            character="=",
            remaining_character="-",
            show_percentage=False,
        ),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    assert out == "=====" + "-----"


# ---------------------------------------------------------------------------
# Percentage suffix
# ---------------------------------------------------------------------------


def test_progress_bar_default_shows_percentage() -> None:
    """``show_percentage=True`` (default) appends ``" NN%"``."""
    tree: Any = Box(
        ProgressBar(value=0.5, width=10),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    assert out == _FILL * 5 + _REMAIN * 5 + "  50%"


def test_progress_bar_zero_percentage_is_padded() -> None:
    """0% renders as ``"   0%"`` (3-digit zero-padded)."""
    tree: Any = Box(
        ProgressBar(value=0.0, width=10),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    assert out == _REMAIN * 10 + "   0%"


def test_progress_bar_full_percentage_is_padded() -> None:
    """100% renders as ``" 100%"``."""
    tree: Any = Box(
        ProgressBar(value=1.0, width=10),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    assert out == _FILL * 10 + " 100%"


def test_progress_bar_hide_percentage() -> None:
    """``show_percentage=False`` suppresses the suffix."""
    tree: Any = Box(
        ProgressBar(value=0.5, width=10, show_percentage=False),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    assert "%" not in out


def test_progress_bar_percentage_rounds() -> None:
    """``value=0.333`` rounds to ``"  33%"`` (not 33.3%)."""
    tree: Any = Box(
        ProgressBar(value=0.333, width=10),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    assert "  33%" in out


# ---------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------


def test_progress_bar_color_applied() -> None:
    """``color`` is forwarded to the emitted :func:`Text` leaf."""
    tree: Any = Box(
        ProgressBar(value=0.5, width=10, color="green", show_percentage=False),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    assert out.startswith("\x1b[32m")
    assert out.endswith("\x1b[0m")


def test_progress_bar_no_color_no_sgr() -> None:
    """``color=None`` (default) leaves the bar unstyled."""
    tree: Any = Box(
        ProgressBar(value=0.5, width=10, show_percentage=False),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    assert "\x1b[" not in out


# ---------------------------------------------------------------------------
# Reactive sources
# ---------------------------------------------------------------------------


def test_progress_bar_signal_source() -> None:
    """A :class:`Signal` source resolves to ``.value`` at layout time."""
    s = signal(0.3)
    tree: Any = Box(
        ProgressBar(value=s, width=10, show_percentage=False),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    assert out == _FILL * 3 + _REMAIN * 7


def test_progress_bar_callable_source() -> None:
    """A ``Callable[[], float]`` source is invoked at layout time."""
    tree: Any = Box(
        ProgressBar(value=lambda: 0.7, width=10, show_percentage=False),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    assert out == _FILL * 7 + _REMAIN * 3


# ---------------------------------------------------------------------------
# Style forwarding
# ---------------------------------------------------------------------------


def test_progress_bar_bold_forwarded() -> None:
    """Extra kwargs (``bold``) are forwarded to the :func:`Text` leaf."""
    tree: Any = Box(
        ProgressBar(value=0.5, width=10, bold=True, show_percentage=False),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    assert "\x1b[1m" in out  # SGR bold


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_progress_bar_invalid_value_type_raises() -> None:
    """``value`` must be float / Signal / callable."""
    try:
        ProgressBar(value="50")  # type: ignore[arg-type]
    except TypeError as exc:
        assert "value" in str(exc)
    else:
        raise AssertionError("string value should raise TypeError")


def test_progress_bar_bool_value_raises() -> None:
    """``bool`` is a subclass of ``int`` but should be rejected."""
    try:
        ProgressBar(value=True)
    except TypeError:
        pass
    else:
        raise AssertionError("bool value should raise TypeError")


def test_progress_bar_invalid_width_raises() -> None:
    """``width`` must be an int."""
    try:
        ProgressBar(value=0.5, width="10")  # type: ignore[arg-type]
    except TypeError as exc:
        assert "width" in str(exc)
    else:
        raise AssertionError("string width should raise TypeError")


def test_progress_bar_empty_character_raises() -> None:
    """``character`` must be a non-empty string."""
    try:
        ProgressBar(value=0.5, character="")
    except TypeError:
        pass
    else:
        raise AssertionError("empty character should raise TypeError")


def test_progress_bar_empty_remaining_character_raises() -> None:
    """``remaining_character`` must be a non-empty string."""
    try:
        ProgressBar(value=0.5, remaining_character="")
    except TypeError:
        pass
    else:
        raise AssertionError("empty remaining_character should raise TypeError")


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


def test_progress_bar_inside_column() -> None:
    """``ProgressBar`` composes inside a column with sibling Text."""
    tree: Any = Box(
        ProgressBar(value=0.4, width=10, show_percentage=False),
        flexDirection="column",
        width=20,
    )
    out = render_to_string(tree)
    assert _FILL * 4 in out
    assert _REMAIN * 6 in out


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_externals_init_exports_progress_bar() -> None:
    from pyink.externals import ProgressBar as InitProgressBar

    assert InitProgressBar is ProgressBar


def test_progress_bar_not_in_pyink_top_level() -> None:
    """PRD Decision 5 — externals stay opt-in."""
    import pyink

    assert not hasattr(pyink, "ProgressBar")
