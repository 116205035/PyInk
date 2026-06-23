"""``BigText`` — ASCII art banner text via :mod:`pyfiglet` (Phase 6 PR2).

Mirrors :mod:`ink-big-text`, which delegates to :mod:`cfonts` for the
glyph data. PyInk's flavour delegates to :mod:`pyfiglet` (a FIGlet
port) — 300+ fonts, multi-row output, and full Unicode / ASCII
coverage out of the box. The hand-rolled font registry that shipped
in earlier PRs is gone; ``pyfiglet`` covers strictly more glyphs and
fonts than we could ever ship inline.

Design (per PRD PR2 scope):

* ``BigText`` is a thin declarative factory that returns a ``box``
  column element — no hooks, no function component. The whole banner
  is built eagerly at call time via a single
  :func:`pyfiglet.Figlet.renderText` call.
* :mod:`pyfiglet` is an *optional* dependency. The factory
  ``import``\\ s it lazily inside the function body; if the package is
  missing we raise an ``ImportError`` whose message points the caller
  at the right extra (``pip install pyink[big-text]``). Nothing else
  in PyInk imports ``pyfiglet``, so the optional group only matters
  when this component is actually used.
* ``font`` is forwarded to :class:`pyfiglet.Figlet` verbatim
  (``"standard"`` / ``"block"`` / ``"shadow"`` / ``"digital"`` /
  ``"banner"`` / ``"big"`` / ...). Unknown font names raise
  :class:`pyfiglet.FontNotFound` — we let that propagate rather than
  swallowing it, because a typo in the font name is almost always a
  bug the caller wants to hear about (matching
  :func:`HighlightedCode`'s "no silent fallback" stance).
* ``align`` is forwarded to pyfiglet's ``justify`` option (one of
  ``"left"`` / ``"center"`` / ``"right"``). Unknown values fall back
  to ``"left"`` so a typo doesn't crash the render — pyfiglet itself
  only accepts those three strings, so we normalise before handing
  off.
* ``colors`` paints the banner's rows in alternating hues. The list
  is cycled modulo its length: ``["red", "yellow"]`` paints row 0
  red, row 1 yellow, row 2 red, …, mirroring :mod:`ink-big-text`'s
  ``colors`` prop (which does the same per-row cycle). ``color`` (the
  single-colour prop) still works and is applied uniformly to every
  row; when both ``color`` and ``colors`` are passed, ``colors``
  wins on its rows and ``color`` fills any rows beyond
  ``len(colors)`` (defensive — callers should pick one).
* ``width`` is forwarded to pyfiglet's ``width`` option (default
  ``80``). The renderer wraps long banners at this column budget;
  pass a wider value for long strings, narrower for tight layouts.

Cross-layer note: pyfiglet output is plain ASCII (or Unicode block
characters for some fonts) with no ANSI sequences, so
:func:`pyink.layout.string_width` measures every cell as one column
and the banner's rendered width matches pyfiglet's output exactly.

PR2 scope: ships ``BigText`` only. The ``block`` / ``simple``
hand-rolled fonts that shipped before this PR are gone — ``pyfiglet``
covers them and hundreds more.
"""

from __future__ import annotations

from typing import Any

from pyink.components.box import Box
from pyink.components.text import Text
from pyink.core.element import Element

__all__ = ["BigText"]

#: Default font name. ``"standard"`` is pyfiglet's default and matches
#: what callers get from ``figlet`` on the command line.
_DEFAULT_FONT: str = "standard"

#: Default alignment within the parent's main axis. Mirrors
#: :mod:`ink-big-text`'s default.
_DEFAULT_ALIGN: str = "left"

#: Default rendering width handed to pyfiglet. Matches the standard
#: ``figlet`` command-line default and what most terminals still are
#: when narrowed by a parent Box.
_DEFAULT_WIDTH: int = 80

#: Valid alignment values; passed straight to pyfiglet's ``justify``
#: option. Anything outside this set falls back to ``"left"``.
_ALIGN_VALUES: frozenset[str] = frozenset({"left", "center", "right"})


def BigText(
    text: str,
    *,
    font: str = _DEFAULT_FONT,
    colors: list[str] | None = None,
    align: str = _DEFAULT_ALIGN,
    width: int | None = None,
    color: str | None = None,
    **box_props: Any,
) -> Element:
    """Render ASCII art banner text via :mod:`pyfiglet`.

    Parameters
    ----------
    text:
        String to render. pyfiglet handles the full Unicode range;
        characters outside the font's coverage render as a blank
        glyph of the font's standard row width so the banner layout
        stays intact.
    font:
        pyfiglet font name (``"standard"`` / ``"block"`` /
        ``"shadow"`` / ``"digital"`` / ``"banner"`` / ``"big"`` /
        … — pyfiglet ships 300+). Unknown names raise
        :class:`pyfiglet.FontNotFound` rather than falling back; a
        typo in the font name is a caller bug worth surfacing.
    colors:
        Per-row colour cycle. The list is cycled modulo its length:
        ``["red", "yellow"]`` paints row 0 red, row 1 yellow, row 2
        red, … (mirroring :mod:`ink-big-text`'s ``colors`` prop).
        Colour specs use PyInk's named-colour vocabulary (``"red"``,
        ``"#ff0000"``, ``"rgb(255,0,0)"``, ``"ansi256(9)"``). When
        ``None``, every row inherits the terminal default unless
        ``color`` is set.
    align:
        Horizontal alignment within the pyfiglet render box. One of
        ``"left"`` / ``"center"`` / ``"right"``. Anything else falls
        back to ``"left"``. This is *pyfiglet's* alignment (it
        pre-pads the rendered rows to ``width``) — it is independent
        of the surrounding flex layout's ``justifyContent``.
    width:
        Maximum column budget handed to pyfiglet (default ``80``).
        Long banners wrap at this width; pass a larger value for
        long strings, narrower for tight layouts.
    color:
        Optional single colour applied uniformly to every row. When
        both ``color`` and ``colors`` are passed, ``colors`` wins on
        its rows and ``color`` fills any rows beyond
        ``len(colors)`` (defensive — callers should pick one).
    **box_props:
        Forwarded to the outer ``Box`` container (``flexDirection``
        is always set to ``"column"`` and cannot be overridden — the
        component's contract is "one row per pyfiglet output line").
        Useful props include ``borderStyle`` / ``padding`` /
        ``width`` / ``backgroundColor``.

    Returns
    -------
    Element
        A ``box`` host element (``flexDirection="column"``) whose
        children are one :func:`Text` leaf per pyfiglet output row.
        No function component is involved — the factory is purely
        declarative.

    Raises
    ------
    ImportError
        If :mod:`pyfiglet` is not installed. The error message points
        the caller at ``pip install pyink[big-text]``.
    TypeError
        If ``text`` is not a ``str``.

    Usage
    -----

    ::

        BigText("Hello")
        BigText("PyInk", font="block", colors=["red", "yellow"])
        BigText("v1.0", align="center", width=60)
    """
    if not isinstance(text, str):
        raise TypeError(
            f"BigText 'text' must be a str, got {type(text).__name__!r}"
        )

    try:
        import pyfiglet  # lazy: keeps ``pyfiglet`` off the core path
    except ImportError as exc:
        # Re-raise with the friendly "install the extra" message; the
        # original ``ImportError`` is chained via ``from`` so callers
        # can still inspect what went wrong (matches
        # :func:`HighlightedCode` / :func:`Markdown`).
        raise ImportError(
            "BigText requires pyfiglet. "
            "Install: pip install pyink[big-text]"
        ) from exc

    # Normalise alignment — pyfiglet only accepts the three standard
    # values. An unknown align falls back to ``"left"`` rather than
    # crashing the render (a typo here is much more likely than a
    # caller asking for a non-standard alignment on purpose).
    justify = align if align in _ALIGN_VALUES else _DEFAULT_ALIGN

    fig = pyfiglet.Figlet(
        font=font,
        justify=justify,
        width=width if width is not None else _DEFAULT_WIDTH,
    )
    rendered = fig.renderText(text)

    # pyfiglet always emits a trailing newline; strip it so we don't
    # produce a blank bottom row. Empty / whitespace-only input
    # produces an all-newline string; ``rstrip`` collapses that to
    # the empty string, which renders as an empty column.
    lines = rendered.rstrip("\n").split("\n")

    # Drop trailing all-blank rows pyfiglet sometimes pads with (the
    # block / banner fonts in particular). We only strip rows that
    # are *entirely* whitespace, and only from the bottom — mid-row
    # blank lines are part of the font's design and stay.
    while lines and lines[-1].strip() == "":
        lines.pop()

    if not lines:
        return Box(flexDirection="column")

    # Build one Text leaf per row. When ``colors`` is set, the row at
    # index ``i`` takes ``colors[i % len(colors)]`` — mirroring
    # ink-big-text's per-row cycle. When ``color`` is also set, it
    # fills any row beyond ``len(colors)`` (defensive — callers
    # should pick one or the other).
    row_elements: list[Element] = []
    for i, line in enumerate(lines):
        row_color: str | None = None
        if colors:
            row_color = colors[i % len(colors)]
            # ``color`` only fills when ``colors`` doesn't cover this
            # row's index — i.e. ``i >= len(colors)``. This is a
            # defensive mix mode; the documented path is "one of
            # ``color`` / ``colors``, not both".
            if row_color is None and color is not None:
                row_color = color
        elif color is not None:
            row_color = color
        # Blank rows still render as a single-space Text so the layout
        # engine sees a measurable row — without this, a blank middle
        # row in a multi-row font would collapse the banner's height.
        body = line if line else " "
        row_elements.append(Text(body, color=row_color))

    # ``flexDirection`` is forced to ``"column"`` even if the caller
    # passed a conflicting value via ``box_props`` — the component's
    # contract is one row per pyfiglet output line.
    box_props.pop("flexDirection", None)
    return Box(*row_elements, flexDirection="column", **box_props)
