"""ANSI escape sequence helpers (PR4).

Three responsibilities live here:

* :func:`parse_color` — translate a user-facing colour string
  (``"red"``, ``"#ff0000"``, ``"rgb(255,0,0)"``, ``"ansi256(9)"``) into
  the body of an SGR escape sequence (e.g. ``"38;2;255;0;0"``).
* :func:`apply_style` — wrap ``text`` with the SGR ``open`` sequence(s)
  for the requested styles and a single ``reset`` at the end.
* :func:`render_border` / :func:`style_segment` — composite border
  characters with their per-edge colour, background and dim styles.

Named colours use the 16-colour basic SGR codes (``30``-``37`` and
``90``-``97`` for foreground, ``40``-``47`` / ``100``-``107`` for
background). Hex / rgb colours always emit truecolor (``38;2;r;g;b`` /
``48;2;r;g;b``). There is no automatic fallback to 256-colour — every
modern terminal handles truecolor and emitting one form keeps the
renderer output deterministic and matches chalk's level-3 mode.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

__all__ = [
    "BORDER_STYLES",
    "BorderStyle",
    "Color",
    "ColorType",
    "NAMED_COLORS",
    "apply_style",
    "parse_color",
    "render_border",
    "resolve_border_chars",
    "style_segment",
]

#: User-facing colour spec — a string alias.
Color = str

#: Whether a colour is applied to the foreground or background.
ColorType = Literal["foreground", "background"]

#: A 4-direction border character set.
BorderStyle = dict[str, str]

#: Mapping of named colours to ``(fg_code, bg_code)`` SGR parameters.
#: Matches ``chalk`` / CSS basic-colour names; "bright" variants use the
#: high-intensity range (``90``-``97`` / ``100``-``107``).
NAMED_COLORS: dict[str, tuple[int, int]] = {
    "black": (30, 40),
    "red": (31, 41),
    "green": (32, 42),
    "yellow": (33, 43),
    "blue": (34, 44),
    "magenta": (35, 45),
    "cyan": (36, 46),
    "white": (37, 47),
    "gray": (90, 100),
    "grey": (90, 100),
    "blackBright": (90, 100),
    "redBright": (91, 101),
    "greenBright": (92, 102),
    "yellowBright": (93, 103),
    "blueBright": (94, 104),
    "magentaBright": (95, 105),
    "cyanBright": (96, 106),
    "whiteBright": (97, 107),
}

#: Predefined character sets, mirroring ``cli-boxes``.
BORDER_STYLES: dict[str, BorderStyle] = {
    "single": {
        "topLeft": "┌",
        "top": "─",
        "topRight": "┐",
        "right": "│",
        "bottomRight": "┘",
        "bottom": "─",
        "bottomLeft": "└",
        "left": "│",
    },
    "double": {
        "topLeft": "╔",
        "top": "═",
        "topRight": "╗",
        "right": "║",
        "bottomRight": "╝",
        "bottom": "═",
        "bottomLeft": "╚",
        "left": "║",
    },
    "round": {
        "topLeft": "╭",
        "top": "─",
        "topRight": "╮",
        "right": "│",
        "bottomRight": "╯",
        "bottom": "─",
        "bottomLeft": "╰",
        "left": "│",
    },
    "bold": {
        "topLeft": "┏",
        "top": "━",
        "topRight": "┓",
        "right": "┃",
        "bottomRight": "┛",
        "bottom": "━",
        "bottomLeft": "┗",
        "left": "┃",
    },
    "singleDouble": {
        "topLeft": "╓",
        "top": "─",
        "topRight": "╖",
        "right": "║",
        "bottomRight": "╜",
        "bottom": "─",
        "bottomLeft": "╙",
        "left": "║",
    },
    "doubleSingle": {
        "topLeft": "╒",
        "top": "═",
        "topRight": "╕",
        "right": "│",
        "bottomRight": "╛",
        "bottom": "═",
        "bottomLeft": "╘",
        "left": "│",
    },
    "classic": {
        "topLeft": "+",
        "top": "-",
        "topRight": "+",
        "right": "|",
        "bottomRight": "+",
        "bottom": "-",
        "bottomLeft": "+",
        "left": "|",
    },
    "arrow": {
        "topLeft": "↘",
        "top": "↓",
        "topRight": "↙",
        "right": "←",
        "bottomRight": "↖",
        "bottom": "↑",
        "bottomLeft": "↗",
        "left": "→",
    },
}

# Regexes used to parse hex / rgb / ansi256 specs. Tolerant of optional
# whitespace inside the parentheses (matches chalk's parser).
_RGB_RE = re.compile(r"^rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$", re.IGNORECASE)
_ANSI256_RE = re.compile(r"^ansi256\(\s*(\d+)\s*\)$", re.IGNORECASE)
_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# SGR parameter codes for non-colour attributes.
_SGR_RESET = "\x1b[0m"
_SGR_BOLD = "\x1b[1m"
_SGR_DIM = "\x1b[2m"
_SGR_ITALIC = "\x1b[3m"
_SGR_UNDERLINE = "\x1b[4m"
_SGR_INVERSE = "\x1b[7m"
_SGR_STRIKETHROUGH = "\x1b[9m"


def _normalize_hex(spec: str) -> tuple[int, int, int]:
    """Expand a ``#rgb`` / ``#rrggbb`` literal to an ``(r, g, b)`` triple."""
    body = spec[1:]
    if len(body) == 3:
        r = int(body[0] * 2, 16)
        g = int(body[1] * 2, 16)
        b = int(body[2] * 2, 16)
    else:
        r = int(body[0:2], 16)
        g = int(body[2:4], 16)
        b = int(body[4:6], 16)
    return r, g, b


def parse_color(color: str, *, type_: ColorType = "foreground") -> str | None:
    """Return the SGR parameter body for ``color`` or ``None`` if unknown.

    The returned string is the *body* of an SGR sequence (without the
    leading ``\\x1b[`` and trailing ``m``); the caller wraps it via
    :func:`_sgr`. ``type_`` selects foreground vs background — the only
    difference is that background truecolor uses ``48`` instead of
    ``38`` and named colours swap to the ``40``-range codes.
    """
    if not color:
        return None
    named = NAMED_COLORS.get(color)
    if named is not None:
        code = named[0] if type_ == "foreground" else named[1]
        return str(code)
    if color.startswith("#") and _HEX_RE.match(color):
        r, g, b = _normalize_hex(color)
        prefix = "38" if type_ == "foreground" else "48"
        return f"{prefix};2;{r};{g};{b}"
    m = _RGB_RE.match(color)
    if m:
        r, g, b = (int(m.group(i)) for i in (1, 2, 3))
        prefix = "38" if type_ == "foreground" else "48"
        return f"{prefix};2;{r};{g};{b}"
    m = _ANSI256_RE.match(color)
    if m:
        value = int(m.group(1))
        prefix = "38" if type_ == "foreground" else "48"
        return f"{prefix};5;{value}"
    return None


def _sgr(body: str) -> str:
    """Wrap a parameter body as a full CSI-SGR escape sequence."""
    return f"\x1b[{body}m"


def apply_style(
    text: str,
    *,
    color: str | None = None,
    backgroundColor: str | None = None,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
    strikethrough: bool = False,
    inverse: bool = False,
    dimColor: bool = False,
) -> str:
    """Wrap ``text`` in SGR sequences for the requested styles.

    Order matches ink's ``Text`` transform (dim → fg colour → bg colour
    → bold → italic → underline → strikethrough → inverse), then a
    single ``reset`` is appended. If no style was applied the text is
    returned unchanged.
    """
    if not text:
        return text
    opens: list[str] = []
    if dimColor:
        opens.append(_SGR_DIM)
    fg = parse_color(color, type_="foreground") if color else None
    if fg:
        opens.append(_sgr(fg))
    bg = parse_color(backgroundColor, type_="background") if backgroundColor else None
    if bg:
        opens.append(_sgr(bg))
    if bold:
        opens.append(_SGR_BOLD)
    if italic:
        opens.append(_SGR_ITALIC)
    if underline:
        opens.append(_SGR_UNDERLINE)
    if strikethrough:
        opens.append(_SGR_STRIKETHROUGH)
    if inverse:
        opens.append(_SGR_INVERSE)
    if not opens:
        return text
    return "".join(opens) + text + _SGR_RESET


def style_segment(
    segment: str,
    *,
    fg: str | None = None,
    bg: str | None = None,
    dim: bool = False,
) -> str:
    """Apply colour / background / dim to a single border segment.

    Emits all openers (foreground, then background, then dim) before
    the segment, then a single reset — flat (non-nested) form. This is
    a deliberate simplification of ink's ``render-border.ts`` chaining
    behaviour; the resulting ANSI is valid and renders identically in
    any terminal that supports SGR.
    """
    if not segment:
        return segment
    opens: list[str] = []
    if fg:
        body = parse_color(fg, type_="foreground")
        if body:
            opens.append(_sgr(body))
    if bg:
        body = parse_color(bg, type_="background")
        if body:
            opens.append(_sgr(body))
    if dim:
        opens.append(_SGR_DIM)
    if not opens:
        return segment
    return "".join(opens) + segment + _SGR_RESET


def resolve_border_chars(style: str | BorderStyle) -> BorderStyle:
    """Return the character set for a ``borderStyle`` prop.

    Accepts either a known name (``"single"``, ``"round"`` …) or a
    custom ``dict`` with the eight ``topLeft`` / ``top`` / … keys.
    Raises ``ValueError`` for unknown names.
    """
    if isinstance(style, dict):
        return style
    if style in BORDER_STYLES:
        return BORDER_STYLES[style]
    raise ValueError(f"Unknown borderStyle: {style!r}")


@dataclass(slots=True)
class _BorderEdges:
    """Normalised per-edge border styling (color / bg / dim / visible)."""

    top_color: str | None
    bottom_color: str | None
    left_color: str | None
    right_color: str | None

    top_bg: str | None
    bottom_bg: str | None
    left_bg: str | None
    right_bg: str | None

    top_dim: bool
    bottom_dim: bool
    left_dim: bool
    right_dim: bool

    show_top: bool
    show_bottom: bool
    show_left: bool
    show_right: bool


def _resolve_edges(style: dict[str, Any]) -> _BorderEdges:
    def _opt(key: str, fallback_key: str) -> str | None:
        v = style.get(key)
        if v is None:
            v = style.get(fallback_key)
        return v

    def _opt_bool(key: str, fallback_key: str) -> bool:
        v = style.get(key)
        if v is None:
            v = style.get(fallback_key)
        return bool(v)

    return _BorderEdges(
        top_color=_opt("borderTopColor", "borderColor"),
        bottom_color=_opt("borderBottomColor", "borderColor"),
        left_color=_opt("borderLeftColor", "borderColor"),
        right_color=_opt("borderRightColor", "borderColor"),
        top_bg=_opt("borderTopBackgroundColor", "borderBackgroundColor"),
        bottom_bg=_opt("borderBottomBackgroundColor", "borderBackgroundColor"),
        left_bg=_opt("borderLeftBackgroundColor", "borderBackgroundColor"),
        right_bg=_opt("borderRightBackgroundColor", "borderBackgroundColor"),
        top_dim=_opt_bool("borderTopDimColor", "borderDimColor"),
        bottom_dim=_opt_bool("borderBottomDimColor", "borderDimColor"),
        left_dim=_opt_bool("borderLeftDimColor", "borderDimColor"),
        right_dim=_opt_bool("borderRightDimColor", "borderDimColor"),
        show_top=style.get("borderTop", True) is not False,
        show_bottom=style.get("borderBottom", True) is not False,
        show_left=style.get("borderLeft", True) is not False,
        show_right=style.get("borderRight", True) is not False,
    )


def render_border(
    lines: list[str],
    style: dict[str, Any],
) -> list[str]:
    """Wrap an already-padded list of content ``lines`` with a border.

    Each line in ``lines`` is the *interior* of the box at one row.
    Returns a new list whose first row is the top border (if visible),
    middle rows carry the left/right edges and the original content, and
    the last row is the bottom border (if visible). The caller is
    responsible for ensuring every ``line`` has the same width — that
    invariant holds because the layout engine pads boxes to their
    resolved width.

    The ``style`` mapping is the raw Box ``props`` slice carrying
    ``borderStyle`` plus the optional ``borderColor`` /
    ``borderTopColor`` / ``borderDimColor`` / ``borderTop=False`` family
    of overrides.
    """
    raw = style.get("borderStyle")
    if raw is None or not lines:
        return list(lines)
    chars = resolve_border_chars(raw)
    edges = _resolve_edges(style)

    # ``lines`` carries the *interior* content of the box (already
    # stripped of border columns); the interior width is simply the
    # widest visible row.
    max_visible = 0
    for line in lines:
        stripped = _strip_ansi(line)
        if len(stripped) > max_visible:
            max_visible = len(stripped)
    interior = max_visible

    out: list[str] = []
    if edges.show_top:
        out.append(
            style_segment(
                (chars["topLeft"] if edges.show_left else "")
                + chars["top"] * interior
                + (chars["topRight"] if edges.show_right else ""),
                fg=edges.top_color,
                bg=edges.top_bg,
                dim=edges.top_dim,
            )
        )

    for line in lines:
        left = (
            style_segment(chars["left"], fg=edges.left_color, bg=edges.left_bg, dim=edges.left_dim)
            if edges.show_left
            else ""
        )
        right = (
            style_segment(
                chars["right"], fg=edges.right_color, bg=edges.right_bg, dim=edges.right_dim
            )
            if edges.show_right
            else ""
        )
        out.append(left + line + right)

    if edges.show_bottom:
        out.append(
            style_segment(
                (chars["bottomLeft"] if edges.show_left else "")
                + chars["bottom"] * interior
                + (chars["bottomRight"] if edges.show_right else ""),
                fg=edges.bottom_color,
                bg=edges.bottom_bg,
                dim=edges.bottom_dim,
            )
        )
    return out


_ANSI_STRIP_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _strip_ansi(s: str) -> str:
    """Strip CSI escape sequences from ``s`` for width measurement."""
    return _ANSI_STRIP_RE.sub("", s)
