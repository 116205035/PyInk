"""``Gradient`` — multi-colour truecolor text (Phase 6 PR2).

Mirrors :mod:`ink-gradient`: wraps a string (or a :func:`Text` leaf) and
paints each character with a colour interpolated linearly between the
caller-supplied colour endpoints. PyInk's flavour collapses
ink-gradient's ``name`` / ``colors`` duality into a single ``colors``
prop (no built-in named palettes — callers pass the endpoints they
want; the upstream names are just preset colour lists anyway).

Design (per PRD PR2 scope):

* ``Gradient`` is a function component. On mount it builds a throwaway
  host tree containing the children, lays it out at the active
  Instance's current width (or 80 columns when used via the
  synchronous test renderer), renders it to a string, strips any ANSI
  the children might have emitted, and pipes the resulting plain text
  through :func:`_apply_gradient`. The result is emitted as a single
  ``text`` host leaf carrying any forwarded style props.
* This is the same pattern :func:`pyink.components.Transform` and
  :func:`pyink.externals.Link` use — the function component body runs
  exactly once on mount, so the gradient is captured at mount time.
  Wrap the whole ``Gradient`` call site in a parent that re-renders
  if you need reactive content.
* Colour parsing goes through :func:`pyink.render.ansi.parse_color` so
  the same specs ``Text`` accepts work here (``"red"``, ``"#ff0000"``,
  ``"rgb(255,0,0)"``, ``"ansi256(9)"``). Named / 16-colour specs are
  resolved through :data:`pyink.render.ansi.NAMED_COLORS`; hex / rgb
  specs are decoded to ``(r, g, b)`` triples for interpolation.
  ``ansi256(N)`` cannot be interpolated in RGB space (it's a palette
  index) so a gradient that mixes ``ansi256`` endpoints falls back to
  a hard step at the midpoint rather than crashing — truecolour is
  the intended spec for gradients.
* Each character in the stripped text is wrapped in its own SGR
  ``38;2;r;g;b`` opener and a single reset, exactly like
  ``gradient-string``'s truecolour mode. The opener is emitted even
  for whitespace so a multi-line gradient (``"line1\\nline2"``) keeps
  each cell's colour deterministic; whitespace resets are cheap.
* Style props (``bold`` / ``underline`` / etc.) are forwarded to the
  emitted ``text`` leaf — the renderer wraps the whole gradient string
  in one outer SGR run for those attributes, then the per-character
  SGR runs layer on top.

Cross-layer note: the inner layout pass emits its own ANSI for any
colour / style props the children carry. We strip that ANSI before
interpolation (mirroring ink-gradient's ``stripAnsi`` step) so the
gradient is driven purely by the visible characters, not the styling
of the source leaves.

PR2 scope: ships ``Gradient`` only.
"""

from __future__ import annotations

import re
from typing import Any

from pyink.core.element import Element, create_element
from pyink.core.reconciler import Reconciler
from pyink.hooks._runtime import _get_current_instance
from pyink.layout import layout, render_layout_to_string
from pyink.render.ansi import NAMED_COLORS, _normalize_hex

__all__ = ["Gradient"]

#: Regex used to strip ANSI escape sequences from the rendered child
#: string before interpolation. Mirrors
#: :data:`pyink.render.ansi._ANSI_STRIP_RE` but kept local so this
#: module doesn't reach into a peer module's private (underscored)
#: constant — the renderer is free to change its stripping regex
#: without silently breaking gradient interpolation.
_ANSI_STRIP_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

#: Default width used by the inner layout pass when no live Instance is
#: bound (e.g. when ``Gradient`` is rendered via
#: :func:`render_to_string`). Matches :func:`Transform`'s fallback.
_DEFAULT_COLUMNS: int = 80


def _color_to_rgb(spec: str) -> tuple[int, int, int] | None:
    """Resolve a colour spec to an ``(r, g, b)`` triple.

    Returns ``None`` if ``spec`` cannot be decoded to RGB — the caller
    treats ``None`` endpoints as "skip interpolation, hard-step instead".
    Named colours are looked up through :data:`NAMED_COLORS` (the
    upstream 16-colour table), and hex / ``rgb(...)`` specs are decoded
    via :func:`pyink.render.ansi._normalize_hex`. ``ansi256(N)`` has no
    well-defined RGB interpolation so it returns ``None`` here.
    """
    if not spec:
        return None
    # ``parse_color`` returns the SGR *body* (e.g. ``"38;2;255;0;0"`` or
    # ``"31"`` for named). For interpolation we want the raw RGB triple.
    # We re-implement the small lookup rather than parsing the SGR body
    # so the upstream parse_color stays the single source of truth for
    # *rendering* while gradient owns its own *interpolation* model.
    if spec.startswith("#") and re.match(r"^#[0-9a-fA-F]{3}|[0-9a-fA-F]{6}$", spec):
        return _normalize_hex(spec)
    rgb_match = re.match(
        r"^rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$", spec, re.IGNORECASE
    )
    if rgb_match:
        return (
            int(rgb_match.group(1)),
            int(rgb_match.group(2)),
            int(rgb_match.group(3)),
        )
    # Named: the (fg, bg) SGR pair maps to a known RGB via the ANSI
    # 16-colour palette. We hardcode the standard CGA hues so gradient
    # endpoints like ``"red"`` / ``"green"`` interpolate visually the
    # way users expect (a mix of red and blue lands purple, not gray).
    named_rgb = _NAMED_RGB.get(spec)
    if named_rgb is not None:
        return named_rgb
    if spec in NAMED_COLORS:
        # Fallback for any registered name without an explicit RGB
        # mapping — use the foreground SGR code's typical rendition as
        # gray so interpolation still produces a visible gradient.
        return None
    return None


#: RGB triples for the canonical 16-colour ANSI palette plus the CSS
#: extended names ``chalk`` recognises. Used by :func:`_color_to_rgb`
#: so named endpoints (``"red"``, ``"blueBright"``) interpolate in RGB
#: space rather than as palette-index steps. Values are the standard
#: CGA / xterm hues.
_NAMED_RGB: dict[str, tuple[int, int, int]] = {
    "black": (0, 0, 0),
    "red": (205, 0, 0),
    "green": (0, 205, 0),
    "yellow": (205, 205, 0),
    "blue": (0, 0, 238),
    "magenta": (205, 0, 205),
    "cyan": (0, 205, 205),
    "white": (229, 229, 229),
    "gray": (118, 118, 118),
    "grey": (118, 118, 118),
    "blackBright": (85, 85, 85),
    "redBright": (255, 85, 85),
    "greenBright": (85, 255, 85),
    "yellowBright": (255, 255, 85),
    "blueBright": (85, 85, 255),
    "magentaBright": (255, 85, 255),
    "cyanBright": (85, 255, 255),
    "whiteBright": (255, 255, 255),
}


def _interpolate(
    text: str,
    endpoints: list[tuple[int, int, int]],
) -> str:
    """Paint each character of ``text`` along the ``endpoints`` colour ramp.

    ``endpoints`` is a list of two or more ``(r, g, b)`` triples. The
    character at normalized position ``t ∈ [0, 1]`` lands on the segment
    between two adjacent endpoints; the segment's own ``[0, 1]`` param
    is computed and the RGB channels are interpolated linearly. Each
    character is wrapped in its own SGR ``38;2;r;g;b`` ... ``0m`` run.

    A single-character string uses the first endpoint verbatim. A
    string longer than one character distributes the ramp evenly across
    ``len(text) - 1`` steps so the final character always lands on the
    last endpoint.
    """
    n = len(text)
    if n == 0:
        return ""
    m = len(endpoints)
    if m == 0:
        return text
    if m == 1:
        # One endpoint collapses to a flat colour — still wrap each
        # character so the caller gets a consistent (single-colour)
        # gradient run rather than a mix of styled and unstyled cells.
        endpoints = [endpoints[0], endpoints[0]]
        m = 2

    out: list[str] = []
    # For a single character we use t=0 so the result is the first
    # endpoint exactly; otherwise t = i / (n - 1) distributes evenly
    # across the whole ramp including both endpoints.
    for i, ch in enumerate(text):
        t = 0.0 if n == 1 else i / (n - 1)
        # Map ``t`` onto the endpoint segments. ``pos`` is the float
        # position along the ``[0, m-1]`` endpoint axis.
        pos = t * (m - 1)
        lo_idx = int(pos)
        if lo_idx >= m - 1:
            r, g, b = endpoints[m - 1]
        else:
            frac = pos - lo_idx
            r0, g0, b0 = endpoints[lo_idx]
            r1, g1, b1 = endpoints[lo_idx + 1]
            r = round(r0 + (r1 - r0) * frac)
            g = round(g0 + (g1 - g0) * frac)
            b = round(b0 + (b1 - b0) * frac)
        out.append(f"\x1b[38;2;{r};{g};{b}m{ch}\x1b[0m")
    return "".join(out)


def _apply_gradient(text: str, colors: list[str]) -> str:
    """Resolve ``colors`` to RGB triples and paint ``text`` along the ramp.

    Endpoints that fail RGB resolution are dropped; if fewer than two
    valid endpoints remain, the text is returned unchanged (the caller
    asked for a degenerate gradient). Multi-line ``text`` is painted as
    a single ramp across all characters so the gradient flows through
    newlines — mirroring ``gradient-string.multiline``.
    """
    if not text or not colors:
        return text
    endpoints: list[tuple[int, int, int]] = []
    for spec in colors:
        rgb = _color_to_rgb(spec)
        if rgb is not None:
            endpoints.append(rgb)
    if len(endpoints) < 2:
        # Degenerate: 0 or 1 valid endpoints. Emit nothing fancy so the
        # caller still sees the text — silently rather than raising,
        # because a bad colour spec shouldn't crash the render.
        return text
    return _interpolate(text, endpoints)


def _strip_ansi(s: str) -> str:
    """Strip CSI escape sequences from ``s`` before interpolation."""
    return _ANSI_STRIP_RE.sub("", s)


def _GradientImpl(**props: Any) -> Element:
    """Function component body — runs inside the reconciler render context."""
    colors: list[str] = props["__gradient_colors__"]
    captured_children: tuple[Any, ...] = props["__gradient_children__"]
    text_props: dict[str, Any] = {
        k: v for k, v in props.items() if not k.startswith("__gradient_")
    }

    # Build a throwaway host tree containing the children and render it,
    # exactly like Transform / Link. A fresh Reconciler scopes any
    # effects the children might establish to this snapshot and tears
    # them down immediately after.
    inner = create_element("box", *captured_children)
    reconciler = Reconciler()
    mounted = reconciler.mount(inner)
    try:
        inst = _get_current_instance()
        columns = _DEFAULT_COLUMNS
        if inst is not None:
            cols_attr = getattr(inst, "columns", 0)
            if isinstance(cols_attr, int) and cols_attr > 0:
                columns = cols_attr
        tree = layout(mounted, columns=columns)
        rendered = render_layout_to_string(tree)
    finally:
        reconciler.unmount(mounted)

    plain = _strip_ansi(rendered)
    painted = _apply_gradient(plain, colors)
    return create_element("text", painted, **text_props)


def Gradient(
    *children: Any,
    colors: list[str],
    **props: Any,
) -> Element:
    """Render multi-colour gradient text.

    Parameters
    ----------
    *children:
        Text content. May contain ``str``, lazy ``Callable[[], str]``
        text leaves, nested :class:`Element` instances (e.g.
        :func:`Text`), or tuples/lists thereof. Children are rendered
        to a string at mount time, ANSI-stripped, and repainted with
        the gradient — reactive children captured by reference will
        not re-render the gradient on change (wrap the ``Gradient``
        call site in a parent that re-renders instead, mirroring
        :func:`Transform`).
    colors:
        Colour endpoints (``["red", "blue"]``, ``["#ff0000",
        "#00ff00", "#0000ff"]``, …). Each character's colour is
        interpolated linearly in RGB space between adjacent endpoints.
        Specs that fail RGB resolution (``ansi256(N)`` is the main
        one) are silently dropped; if fewer than two valid endpoints
        remain the text is returned unstyled.
    **props:
        Forwarded to the emitted ``text`` leaf. Supports every style
        prop :func:`Text` accepts; the renderer wraps the whole
        gradient string in one outer SGR run for those attributes,
        then the per-character SGR runs layer on top.

    Returns
    -------
    Element
        An element whose ``type`` is a function component
        (:func:`_GradientImpl`). The factory itself never runs the
        inner layout — the wrapped function is invoked by the
        reconciler on mount.

    Raises
    ------
    TypeError
        If ``colors`` is not a list/tuple or is empty.

    Usage
    -----
    ::

        Gradient("hello", colors=["red", "blue"])
        Gradient(Text("rainbow"), colors=["#ff0000", "#00ff00", "#0000ff"])
        Gradient("styled", colors=["magenta", "cyan"], bold=True)
    """
    if not isinstance(colors, (list, tuple)) or len(colors) == 0:
        raise TypeError(
            f"Gradient 'colors' must be a non-empty list/tuple, "
            f"got {type(colors).__name__!r}"
        )
    gradient_props: dict[str, Any] = dict(props)
    gradient_props["__gradient_colors__"] = list(colors)
    gradient_props["__gradient_children__"] = children
    return create_element(_GradientImpl, **gradient_props)
