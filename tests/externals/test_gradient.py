"""Tests for :func:`pyink.externals.Gradient` (Phase 6 PR2).

``Gradient`` is a function component that wraps an inner layout pass
(just like :func:`Transform` / :func:`Link`), so all assertions use
the synchronous :func:`render_to_string` test renderer.

Coverage:

* Element shape — ``Gradient`` returns an element whose ``type`` is a
  function component (not a host string like :func:`Text`, not a host
  ``box`` like :func:`Divider`).
* 2-colour gradient — each character carries a truecolor SGR run with
  the right RGB at each end.
* 3-colour gradient — middle character lands on the midpoint RGB.
* Hex specs, ``rgb(...)`` specs, named specs all resolve to RGB.
* Single-character input uses the first endpoint verbatim.
* Empty children render an empty text leaf.
* Style props (``bold``) are forwarded to the emitted leaf and wrap
  the gradient string in an outer SGR run.
* ``ansi256`` endpoints cannot interpolate in RGB — gradient silently
  falls back to leaving the text unstyled (rather than crashing).
* ``colors`` validation — non-list / empty list raises ``TypeError``.
* Integration: ``Gradient`` wrapping :func:`BigText` paints the
  banner's characters with gradient hues.
* ``Gradient`` is exported from ``pyink.externals`` but NOT from the
  top-level ``pyink`` package (PRD Decision 5 — externals stay opt-in).
"""

from __future__ import annotations

import re
from typing import Any

from pyink import Box, Text, render_to_string
from pyink.core.element import Element
from pyink.externals import BigText, Gradient

#: Regex that matches a single truecolor SGR run ``\\x1b[38;2;R;G;Bm``.
#: Used to count the per-character SGR runs the gradient emits.
_TRUECOLOR_RE = re.compile(r"\x1b\[38;2;(\d+);(\d+);(\d+)m")


# ---------------------------------------------------------------------------
# Element shape
# ---------------------------------------------------------------------------


def test_gradient_returns_function_component_element() -> None:
    """``Gradient`` wraps its props in a function component element.

    Unlike :func:`Divider` (which returns a ``box`` host directly),
    Gradient needs an inner layout pass to render the children before
    painting the gradient, so it uses the same
    function-component-around-an-inner-reconciler pattern as
    :func:`Transform` / :func:`Link`.
    """
    el = Gradient("hi", colors=["red", "blue"])
    assert isinstance(el, Element)
    # Function component (callable), not a host string.
    assert callable(el.type)


def test_gradient_colors_must_be_non_empty_list() -> None:
    """``colors`` must be a non-empty list/tuple."""
    try:
        Gradient("hi", colors=[])
    except TypeError as exc:
        assert "colors" in str(exc)
    else:
        raise AssertionError("empty colors should raise TypeError")


def test_gradient_colors_must_be_a_list() -> None:
    """A bare string is not a valid ``colors`` value."""
    try:
        Gradient("hi", colors="red")  # type: ignore[arg-type]
    except TypeError as exc:
        assert "colors" in str(exc)
    else:
        raise AssertionError("string colors should raise TypeError")


# ---------------------------------------------------------------------------
# 2-colour gradient
# ---------------------------------------------------------------------------


def test_two_color_gradient_paints_endpoints() -> None:
    """A 2-colour gradient paints the first char with endpoint 0 and
    the last char with endpoint 1.

    ``"red"`` resolves to ``(205, 0, 0)`` and ``"blue"`` to
    ``(0, 0, 238)`` (canonical ANSI 16-colour palette RGBs). The
    middle character lands at the midpoint.
    """
    tree: Any = Box(
        Gradient("ABC", colors=["red", "blue"]),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    matches = _TRUECOLOR_RE.findall(out)
    # Three characters -> three SGR runs (each char wrapped in its own).
    assert len(matches) == 3, f"expected 3 SGR runs, got {matches}"
    r0, g0, b0 = (int(x) for x in matches[0])
    r2, g2, b2 = (int(x) for x in matches[2])
    # First character is pure red endpoint.
    assert (r0, g0, b0) == (205, 0, 0)
    # Last character is pure blue endpoint.
    assert (r2, g2, b2) == (0, 0, 238)


def test_two_color_gradient_midpoint_is_average() -> None:
    """The middle character of a 2-colour gradient is the RGB average.

    For 3 chars ``ABC``, ``B`` (index 1) sits at ``t=0.5``, so its RGB
    is the average of the two endpoints. Using pure ``rgb(...)`` specs
    avoids the named-colour palette rounding (``"red"`` is 205 not
    255 in the canonical ANSI palette).
    """
    tree: Any = Box(
        Gradient("ABC", colors=["rgb(0,0,0)", "rgb(100,100,100)"]),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    matches = _TRUECOLOR_RE.findall(out)
    assert len(matches) == 3
    r1, g1, b1 = (int(x) for x in matches[1])
    # Midpoint of 0 and 100 is 50.
    assert (r1, g1, b1) == (50, 50, 50)


def test_two_color_gradient_single_char_uses_first_endpoint() -> None:
    """A single-character gradient paints with the first endpoint."""
    tree: Any = Box(
        Gradient("X", colors=["red", "blue"]),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    matches = _TRUECOLOR_RE.findall(out)
    assert len(matches) == 1
    r, g, b = (int(x) for x in matches[0])
    assert (r, g, b) == (205, 0, 0)


# ---------------------------------------------------------------------------
# 3-colour gradient
# ---------------------------------------------------------------------------


def test_three_color_gradient_visits_each_endpoint() -> None:
    """A 3-colour gradient paints char 0 with endpoint 0, the middle
    char near endpoint 1, and the last char with endpoint 2.

    Using ``rgb(...)`` specs makes the endpoint assertions exact.
    """
    tree: Any = Box(
        Gradient("ABCDE", colors=["rgb(0,0,0)", "rgb(100,0,0)", "rgb(0,100,0)"]),
        flexDirection="column",
        width=60,
    )
    out = render_to_string(tree)
    matches = _TRUECOLOR_RE.findall(out)
    assert len(matches) == 5
    # Index 0: endpoint 0 = (0,0,0)
    assert tuple(int(x) for x in matches[0]) == (0, 0, 0)
    # Index 4: endpoint 2 = (0,100,0)
    assert tuple(int(x) for x in matches[4]) == (0, 100, 0)
    # Index 2: midpoint of endpoints 0,1,2 along the ramp
    # = average of endpoint 1's segment endpoints (endpoint 1 itself).
    r2, g2, b2 = (int(x) for x in matches[2])
    # At t=0.5, position is 1.0 on the [0, 2] endpoint axis, so the
    # RGB is exactly endpoint 1 = (100, 0, 0).
    assert (r2, g2, b2) == (100, 0, 0)


# ---------------------------------------------------------------------------
# Colour spec formats
# ---------------------------------------------------------------------------


def test_gradient_accepts_hex_colors() -> None:
    """Hex specs resolve to RGB and interpolate."""
    tree: Any = Box(
        Gradient("AB", colors=["#ff0000", "#00ff00"]),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    matches = _TRUECOLOR_RE.findall(out)
    assert len(matches) == 2
    assert tuple(int(x) for x in matches[0]) == (255, 0, 0)
    assert tuple(int(x) for x in matches[1]) == (0, 255, 0)


def test_gradient_accepts_short_hex_colors() -> None:
    """3-char hex specs expand to the doubled-digit RGB."""
    tree: Any = Box(
        Gradient("AB", colors=["#f00", "#0f0"]),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    matches = _TRUECOLOR_RE.findall(out)
    assert len(matches) == 2
    # #f00 → (255, 0, 0)
    assert tuple(int(x) for x in matches[0]) == (255, 0, 0)
    # #0f0 → (0, 255, 0)
    assert tuple(int(x) for x in matches[1]) == (0, 255, 0)


def test_gradient_accepts_named_colors() -> None:
    """Named colours use the canonical ANSI 16-colour palette RGBs."""
    tree: Any = Box(
        Gradient("A", colors=["green", "yellow"]),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    matches = _TRUECOLOR_RE.findall(out)
    assert len(matches) == 1
    # green = (0, 205, 0) per the canonical ANSI palette.
    assert tuple(int(x) for x in matches[0]) == (0, 205, 0)


def test_gradient_accepts_bright_named_colors() -> None:
    """``redBright`` et al. resolve to the high-intensity palette RGB."""
    tree: Any = Box(
        Gradient("A", colors=["redBright", "blueBright"]),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    matches = _TRUECOLOR_RE.findall(out)
    assert len(matches) == 1
    # redBright = (255, 85, 85)
    assert tuple(int(x) for x in matches[0]) == (255, 85, 85)


# ---------------------------------------------------------------------------
# Empty / degenerate cases
# ---------------------------------------------------------------------------


def test_gradient_empty_children_renders_empty() -> None:
    """Empty children render an empty text leaf (no crash)."""
    tree: Any = Box(
        Gradient("", colors=["red", "blue"]),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    # No characters -> no SGR runs.
    assert _TRUECOLOR_RE.findall(out) == []


def test_gradient_ansi256_endpoints_fall_back_unstyled() -> None:
    """``ansi256(N)`` cannot interpolate in RGB.

    Both endpoints failing resolution means the gradient degenerates
    to the unstyled text rather than crashing.
    """
    tree: Any = Box(
        Gradient("ABC", colors=["ansi256(9)", "ansi256(11)"]),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    # No truecolor SGR runs — endpoints couldn't be resolved to RGB.
    assert _TRUECOLOR_RE.findall(out) == []
    # The visible text is still present.
    assert "ABC" in out


# ---------------------------------------------------------------------------
# Style prop forwarding
# ---------------------------------------------------------------------------


def test_gradient_bold_wraps_outer_sgr() -> None:
    """``bold=True`` is forwarded to the emitted text leaf and wraps
    the whole gradient string in an SGR bold run."""
    tree: Any = Box(
        Gradient("AB", colors=["red", "blue"], bold=True),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    # SGR bold = \x1b[1m
    assert out.startswith("\x1b[1m")
    assert out.endswith("\x1b[0m")


def test_gradient_strips_inner_ansi_before_painting() -> None:
    """Children with their own style (e.g. colored Text) are stripped
    before gradient interpolation so the gradient paints only the
    visible characters."""
    tree: Any = Box(
        Gradient(
            Text("AB", color="green"),
            colors=["red", "blue"],
        ),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    matches = _TRUECOLOR_RE.findall(out)
    # 2 characters -> 2 gradient SGR runs. The green SGR from the
    # inner Text is stripped before painting so it doesn't inflate
    # the count.
    assert len(matches) == 2


# ---------------------------------------------------------------------------
# Long text
# ---------------------------------------------------------------------------


def test_gradient_long_text_paints_every_character() -> None:
    """A 20-character string gets 20 gradient SGR runs."""
    text = "abcdefghijklmnopqrst"
    tree: Any = Box(
        Gradient(text, colors=["red", "blue"]),
        flexDirection="column",
        width=80,
    )
    out = render_to_string(tree)
    matches = _TRUECOLOR_RE.findall(out)
    assert len(matches) == len(text)


def test_gradient_multiline_paints_across_lines() -> None:
    """``\\n`` in the children doesn't break the gradient — every
    character (including the newline) is painted in sequence."""
    tree: Any = Box(
        Gradient("AB\nCD", colors=["red", "blue"]),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    matches = _TRUECOLOR_RE.findall(out)
    # 5 characters in the source string (A B \n C D), each painted
    # with its own SGR run. The newline character also receives a
    # gradient run; it remains a real newline in the output so the
    # rendered string still wraps onto two lines.
    assert len(matches) == 5


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


def test_gradient_wrapping_big_text() -> None:
    """``Gradient(BigText(...))`` paints the banner with gradient hues.

    The BigText output is a 3-line block; each visible glyph character
    receives its own gradient SGR run. We assert at least one SGR run
    appears rather than the exact count because the banner width
    depends on the glyph data.
    """
    tree: Any = Box(
        Gradient(BigText("AB"), colors=["red", "blue"]),
        flexDirection="column",
        width=80,
    )
    out = render_to_string(tree)
    matches = _TRUECOLOR_RE.findall(out)
    assert len(matches) > 0


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_externals_init_exports_gradient() -> None:
    from pyink.externals import Gradient as InitGradient

    assert InitGradient is Gradient


def test_gradient_not_in_pyink_top_level() -> None:
    """PRD Decision 5 — externals stay opt-in; top-level import must fail."""
    import pyink

    assert not hasattr(pyink, "Gradient"), "Gradient must NOT be top-level"
