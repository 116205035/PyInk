"""``Divider`` — visual section separator (Phase 2 PR4).

Mirrors :mod:`ink-divider`: a single horizontal or vertical line that
optionally carries a centred label. PyInk's flavour collapses
ink-divider's ``dividerChar`` / ``dividerColor`` / ``titleColor`` /
``titlePadding`` knobs into the props the PRD calls out — ``border_style``
picks the character family, ``color`` paints the whole line (and any
label) uniformly, and ``padding`` controls the label's left/right gutters.

Design (per PRD PR4 scope):

* ``Divider`` is a thin factory that returns a ``box`` host element —
  no hooks, no function component. This puts it in the same category as
  :func:`pyink.components.Spacer`: a declarative composition over the
  existing ``borderStyle`` machinery rather than a component with a
  lifecycle. Renderers already know how to paint single edges (the
  ``borderTop`` / ``borderRight`` / ``borderBottom`` / ``borderLeft``
  visibility flags shipped in PR4), so we just configure them.
* Horizontal layout builds a ``box`` whose only visible edge is the
  bottom one and lets ``flexGrow=1`` stretch it across the available
  width. This is exactly the trick :mod:`ink-divider`'s
  ``BaseDivider`` uses (``borderBottom / flexGrow=1``); we keep the
  pattern verbatim because the renderer already fills an edge across
  the full box width, which is what makes the line span the parent
  column automatically.
* Vertical layout flips the same idea: only the left edge is shown and
  ``flexGrow=1`` makes the box grow vertically inside a row parent.
* Label mode wraps two of those single-edge boxes around a :func:`Text`
  leaf. Both flanking dividers carry ``flexGrow=1`` so the flex engine
  splits the remaining main-axis space evenly and the label lands
  centred without us having to measure it ourselves. ``padding`` adds
  the requested spaces on either side of the label.
* Unknown ``border_style`` falls back to ``"single"`` rather than
  raising — the renderer's ``resolve_border_chars`` would otherwise
  reject the prop with a ``ValueError``, which is too loud for a
  cosmetic choice. (Callers who want strict validation can pass a
  custom ``dict`` through ``border_style`` just like they can to
  ``Box(borderStyle=...)``.)

Width / height: the PRD exposes them as overrides for cases where the
parent doesn't have a usable main axis (a ``Box`` with
``flexDirection="column"`` and no explicit width, or a ``Box`` with
``flexDirection="row"`` and no explicit height). When ``None`` we
rely on ``flexGrow=1``; when set, we pin the dimension and skip the
grow so the caller's value wins.

PR4 scope: ships ``Divider`` only. Context / focus land in PR5/6.
"""

from __future__ import annotations

from typing import Any

from pyink.components.box import Box
from pyink.components.text import Text
from pyink.core.element import Element
from pyink.render.ansi import BORDER_STYLES, BorderStyle

__all__ = ["Divider"]

#: Default padding (in cells) on each side of a label. ``0`` matches
#: the PRD signature; ink-divider defaults to ``titlePadding=1`` but the
#: PRD collapses that into a single ``padding`` prop with ``0`` as the
#: neutral default — callers who want the upstream look pass
#: ``padding=1``.
_DEFAULT_PADDING: int = 0


def _resolve_border_chars(border_style: str | BorderStyle) -> BorderStyle:
    """Return the 8-key char map for ``border_style``.

    Mirrors :func:`pyink.render.ansi.resolve_border_chars` but falls
    back to ``"single"`` for unknown string names instead of raising —
    a cosmetic prop shouldn't crash the render pipeline. Custom
    ``dict`` values are accepted verbatim so callers can plug in their
    own character set (same contract as ``Box(borderStyle=...)``).
    """
    if isinstance(border_style, dict):
        return border_style
    return BORDER_STYLES.get(border_style, BORDER_STYLES["single"])


def _edge_box(
    *,
    direction: str,
    border_chars: BorderStyle,
    color: str | None,
    width: int | None,
    height: int | None,
) -> Element:
    """Build a single-edge ``box`` representing one divider segment.

    Horizontal segments show only their bottom edge; vertical segments
    show only their left edge. The character used is whatever the
    resolved ``border_chars`` says for that edge, so different
    ``border_style`` choices (single / double / round / bold / …)
    flow through automatically. ``flexGrow=1`` makes the segment
    stretch along its parent's main axis when no explicit size was
    requested.
    """
    # Resolve character by edge. We pass a custom ``borderStyle`` dict
    # whose only populated edge is the one we want visible; the
    # renderer reads the matching char and the visibility flags below
    # ensure nothing else is drawn. Using a dict (rather than the named
    # alias) means ``border_style`` aliases like "round" still get
    # honoured here even when the renderer's own alias lookup would
    # reject them — but since we already pre-resolved via
    # ``_resolve_border_chars``, this is just defensive.
    style_dict: BorderStyle = dict(BORDER_STYLES["single"])
    style_dict.update(border_chars)

    if direction == "horizontal":
        # borderBottom=True makes the renderer paint the bottom edge
        # across the full box width. flexGrow=1 stretches that width
        # inside the parent's main axis (a row, when used standalone in
        # a column — see horizontal divider below) so the line fills
        # the available space.
        grow = width is None
        return Box(
            borderStyle=style_dict,
            borderBottom=True,
            borderTop=False,
            borderLeft=False,
            borderRight=False,
            borderColor=color,
            flexGrow=1 if grow else 0,
            width=width,
        )
    # direction == "vertical"
    grow = height is None
    return Box(
        borderStyle=style_dict,
        borderLeft=True,
        borderTop=False,
        borderBottom=False,
        borderRight=False,
        borderColor=color,
        flexGrow=1 if grow else 0,
        height=height,
    )


def Divider(
    *,
    label: str | None = None,
    direction: str = "horizontal",
    border_style: str | BorderStyle = "single",
    color: str | None = None,
    width: int | None = None,
    height: int | None = None,
    padding: int = _DEFAULT_PADDING,
) -> Element:
    """Render a visual divider line, optionally carrying a centred label.

    Parameters
    ----------
    label:
        Text shown in the middle of a horizontal divider
        (``"── label ──"``). ``None`` renders a plain line. Only the
        horizontal layout honours this prop — vertical dividers ignore
        it (mirroring ink-divider, which is horizontal-only upstream).
    direction:
        ``"horizontal"`` (default) paints a single row of the border's
        ``bottom`` character. ``"vertical"`` paints a single column of
        the border's ``left`` character — useful between siblings in a
        row container. Any other value raises ``ValueError``.
    border_style:
        Named border alias (``"single"`` / ``"double"`` / ``"round"``
        / ``"bold"`` / ``"singleDouble"`` / ``"doubleSingle"`` /
        ``"classic"`` / ``"arrow"``) or a custom 8-key
        :data:`pyink.render.ansi.BorderStyle` dict. Unknown aliases
        fall back to ``"single"`` so a typo never crashes the render.
    color:
        Colour spec forwarded to ``borderColor`` (and to the label's
        ``color``). Applied uniformly so the line and label share the
        same hue — pass ``None`` to inherit the terminal default.
    width:
        Pin the horizontal divider's width in cells. ``None`` (default)
        lets ``flexGrow=1`` fill the parent's main axis; useful when
        the parent has no resolvable width (e.g. a top-level
        ``flexDirection="column"`` Box without a width). Ignored for
        vertical dividers.
    height:
        Pin the vertical divider's height in rows. ``None`` (default)
        lets ``flexGrow=1`` fill the parent's main axis. Ignored for
        horizontal dividers.
    padding:
        Number of spaces to insert on each side of ``label``. Defaults
        to ``0``; pass ``1`` to reproduce ink-divider's look.

    Returns
    -------
    Element
        A ``box`` host element (plain or wrapped around children when
        a label is present). No function component is involved — the
        factory is purely declarative, which makes
        ``Box(Divider(), Text("..."))`` safe to call from any context.

    Raises
    ------
    ValueError
        If ``direction`` is neither ``"horizontal"`` nor ``"vertical"``.

    Usage
    -----
    ::

        # Plain horizontal line that fills the parent column
        Divider()

        # Centred label
        Divider(label="Section A", color="green")

        # Vertical divider between two siblings
        Box(
            Text("left"),
            Divider(direction="vertical"),
            Text("right"),
        )

        # Explicit width when the parent can't supply one
        Divider(width=40)
    """
    if direction not in ("horizontal", "vertical"):
        raise ValueError(
            f"Divider 'direction' must be 'horizontal' or 'vertical', "
            f"got {direction!r}"
        )

    border_chars = _resolve_border_chars(border_style)

    # ---- Vertical ------------------------------------------------------
    # ink-divider is horizontal-only upstream; PyInk extends the
    # component to cover the row-sibling case. A vertical divider has
    # no meaningful label, so we ignore ``label`` / ``padding`` here
    # (no warning — keeping the prop accepted makes call sites
    # direction-agnostic).
    if direction == "vertical":
        return _edge_box(
            direction="vertical",
            border_chars=border_chars,
            color=color,
            width=None,
            height=height,
        )

    # ---- Horizontal ----------------------------------------------------
    if label is None:
        return _edge_box(
            direction="horizontal",
            border_chars=border_chars,
            color=color,
            width=width,
            height=None,
        )

    # Label mode: a row of [left divider | label | right divider]. Both
    # dividers carry flexGrow=1 so the flex engine splits the leftover
    # main-axis space evenly and the label lands centred. ``padding``
    # expands the label's natural width on each side so the line
    # doesn't hug the text.
    if width is not None:
        # Explicit width: pin the outer row and let the inner dividers
        # share whatever's left after the label. flexGrow on the outer
        # box would compete with the explicit width, so we set it 0.
        outer_width_props: dict[str, Any] = {"width": width, "flexGrow": 0}
    else:
        # Auto-fill: the outer box itself grows to fill its parent's
        # main axis; the inner dividers then split the leftover space
        # once the label has been measured.
        outer_width_props = {"flexGrow": 1}

    left = _edge_box(
        direction="horizontal",
        border_chars=border_chars,
        color=color,
        width=None,
        height=None,
    )
    right = _edge_box(
        direction="horizontal",
        border_chars=border_chars,
        color=color,
        width=None,
        height=None,
    )

    label_text = " " * padding + label + " " * padding

    return Box(
        left,
        Text(label_text, color=color),
        right,
        flexDirection="row",
        alignItems="center",
        **outer_width_props,
    )
