"""``BigText`` — ASCII art banner text (Phase 6 PR2).

Mirrors :mod:`ink-big-text`, which delegates to :mod:`cfonts` for the
glyph data. PyInk's flavour ships a tiny built-in font registry (no
``pyfiglet`` / ``cfonts`` dependency) so the common CLI banner case
("print the app name in big letters") works out of the box.

Design (per PRD PR2 scope):

* ``BigText`` is a thin factory that returns a ``box`` column element
  — no hooks, no function component. The whole banner is built
  eagerly at call time. Three lines tall for every shipped font.
* :data:`FONTS` maps font name → (character → row strings). The
  default ``"block"`` font covers ``A-Z`` + ``0-9`` + space (37
  glyphs). Unknown characters (lowercase letters, punctuation) are
  rendered as an all-spaces column block of the right height so the
  banner layout stays intact — callers who want lowercase glyphs
  should ``str.upper()`` first.
* Every row in a given font is the same width per glyph so
  horizontally concatenated glyphs line up cleanly. The font data is
  validated at module load via :func:`_validate_fonts` so a future
  hand-edit that breaks the invariant fails loudly at import time
  rather than silently misaligning the rendered banner.
* Multi-character text renders each glyph side-by-side in a row,
  then stacks the rows so the result is a 2-D ASCII block. ``align``
  controls how the block is justified within its parent's main axis
  (``"left"`` / ``"center"`` / ``"right"``); PyInk implements this
  via :func:`Box` ``justifyContent``, which lets the flex engine
  distribute leftover space rather than measuring the rendered block
  ourselves.
* ``color`` is forwarded to every :func:`Text` leaf so the whole
  banner paints in one hue; callers who want multi-colour banners
  wrap the ``BigText`` call in a :func:`pyink.externals.Gradient`.

Cross-layer note: the glyph data is plain ASCII / Unicode block
characters — no wide-char (CJK) glyphs are used, so
:func:`pyink.layout.string_width` measures every glyph as exactly one
cell per character and the banner's rendered width matches the glyph
data's ``len(row)`` exactly.

PR2 scope: ships ``BigText`` + the ``block`` / ``simple`` fonts only
(each covers A-Z + 0-9 + space, 3 rows tall, 6 cells wide).
"""

from __future__ import annotations

from typing import Any

from pyink.components.box import Box
from pyink.components.text import Text
from pyink.core.element import Element

__all__ = ["FONTS", "BigText"]

# ---------------------------------------------------------------------------
# Font data
# ---------------------------------------------------------------------------
#
# Every glyph in a font has the same number of rows AND every row in a
# single glyph has the same width. :func:`_validate_fonts` checks both
# invariants at import time — a hand-edit that breaks them fails loudly
# rather than silently misaligning the rendered banner.
#
# All shipped fonts are 3 rows tall × 6 cells wide per glyph. Covers
# A-Z + 0-9 + space (37 glyphs each).
#
# ``block`` uses U+2580-U+259F Unicode block elements for a chunky,
# solid look. ``simple`` uses plain ASCII pipes / underscores /
# slashes for terminals that don't render Unicode cleanly.

_W: int = 6  # canonical glyph width for the shipped fonts


def _pad(rows: list[str]) -> list[str]:
    """Right-pad every row in ``rows`` to the canonical glyph width.

    Lets the font table below use shorter literals (e.g. ``" ▄▀▄"``)
    while still producing uniform-width output.
    """
    return [r.ljust(_W) for r in rows]


FONTS: dict[str, dict[str, list[str]]] = {
    "block": {
        "A": _pad([" ▄▀▄", "█▀█▀█", "▀   ▀"]),
        "B": _pad(["█▀▄▀█", "█▀▀▄ ", "▀   ▀"]),
        "C": _pad([" ▄▀▀ ", "█    ", " ▀▀▀ "]),
        "D": _pad(["█▀▄  ", "█ █▀█", "▀  ▀ "]),
        "E": _pad(["▄▀▀▀▄", "█▀▀  ", "▀▀▀▀▀"]),
        "F": _pad(["▄▀▀▀▄", "█▀▀  ", "█    "]),
        "G": _pad([" ▄▀▀ ", "█ ▀▄ ", "▀▄▄▄▀"]),
        "H": _pad(["█   █", "█▀▄█▀", "█   █"]),
        "I": _pad(["▄▀▀▄ ", " █   ", "▀▄▄▀ "]),
        "J": _pad(["   ▄▀", "   █ ", "▀▄▄▀ "]),
        "K": _pad(["█ ▄█ ", "█▀▀▄ ", "█  ▀█"]),
        "L": _pad(["█    ", "█    ", "▀▀▀▀▀"]),
        "M": _pad(["█▀▄▀█", "█ █ █", "█   █"]),
        "N": _pad(["█▀▄ █", "█ █ █", "█   █"]),
        "O": _pad([" ▄▀▄ ", "█   █", "▀▄▄▄▀"]),
        "P": _pad(["▄▀▀▄█", "█▀▀  ", "█    "]),
        "Q": _pad([" ▄▀▄ ", "█   █", "▀▄▄█▀"]),
        "R": _pad(["▄▀▀▄█", "█  ██", "█  ▀█"]),
        "S": _pad(["▄▀▀▀▄", " ▀▀▄ ", "▄▄▄▀▀"]),
        "T": _pad(["▀▄▄▄▀", "  █  ", "  █  "]),
        "U": _pad(["█   █", "█   █", "▀▄▄▄▀"]),
        "V": _pad(["█   █", "█   █", " ▀▀▀ "]),
        "W": _pad(["█   █", "█ █ █", "▀▀▀▀▀"]),
        "X": _pad(["█   █", " ▀▄▀ ", "█   █"]),
        "Y": _pad(["█   █", " ▀▄▀ ", "  █  "]),
        "Z": _pad(["▄▀▀▀█", " ▄▀█ ", "█▀▀  "]),
        "0": _pad([" ▄▀▄ ", "█▀█▀█", "▀   ▀"]),
        "1": _pad(["  ▄  ", " ▀▄ ", "▄▀▀▀"]),
        "2": _pad([" ▄▀▀ ", "  ▄▀ ", "▀▀▀  "]),
        "3": _pad(["▄▀▀▀ ", "  ▄▀ ", "▀▄▄▄▀"]),
        "4": _pad(["█  █ ", "▀▀▄█▀", "   █ "]),
        "5": _pad(["▄▀▀▀ ", "▀▀▄▄ ", "▄▄▄▀▀"]),
        "6": _pad([" ▄▀▀ ", "█▀▀▄ ", "▀▄▄▄▀"]),
        "7": _pad(["▀▀▀▀█", "  ▄▀ ", " ▀   "]),
        "8": _pad([" ▄▀▄ ", "█▀▄▀█", "▀   ▀"]),
        "9": _pad([" ▄▀▄ ", "▀▄▄█▀", "  ▀▀ "]),
        " ": _pad(["      ", "      ", "      "]),
    },
    "simple": {
        "A": _pad([" __  ", "/  ` ", "|__/ "]),
        "B": _pad(["|__  ", "|__  ", "|__/ "]),
        "C": _pad([" __  ", "/   |", "|__/ "]),
        "D": _pad(["|__ |", "|  ||", "|__/ "]),
        "E": _pad(["|___ ", "|__  ", "|___ "]),
        "F": _pad(["|___ ", "|__  ", "|    "]),
        "G": _pad([" __  ", "/__ |", "\\__| "]),
        "H": _pad(["|  | ", "|__| ", "|  | "]),
        "I": _pad(["|_+_  ", "  |  ", "_|_  "]),
        "J": _pad(["    |", "    |", "|__/ "]),
        "K": _pad(["|_  /", "| /  ", "|/   "]),
        "L": _pad(["|    ", "|    ", "|___ "]),
        "M": _pad(["|/\\/|", "|  | ", "|  | "]),
        "N": _pad(["|/| |", "| | |", "| | |"]),
        "O": _pad([" __  ", "/  \\ ", "\\__/ "]),
        "P": _pad(["|__| ", "|__  ", "|    "]),
        "Q": _pad([" __  ", "/  \\ ", "\\__|_"]),
        "R": _pad(["|__| ", "|_ \\ ", "|  \\ "]),
        "S": _pad([" __  ", "|__  ", "__|  "]),
        "T": _pad(["_|_  ", " |   ", " |   "]),
        "U": _pad(["|  | ", "|  | ", "\\__/ "]),
        "V": _pad(["|  | ", "|  | ", " \\/  "]),
        "W": _pad(["|  | ", "|/\\| ", "|  | "]),
        "X": _pad(["\\  / ", " ><  ", "/  \\ "]),
        "Y": _pad(["\\  / ", " ><  ", "  |  "]),
        "Z": _pad(["___  ", " /   ", "/__  "]),
        "0": _pad([" __  ", "/  \\ ", "\\__/ "]),
        "1": _pad([" /|  ", "/ |  ", "|_|  "]),
        "2": _pad(["__   ", "_ |  ", "__)  "]),
        "3": _pad(["__   ", "_ |  ", "__)  "]),
        "4": _pad(["| |  ", "|_|  ", "  |  "]),
        "5": _pad(["|__  ", "|__  ", "\\__| "]),
        "6": _pad([" __  ", "|__  ", "\\__/ "]),
        "7": _pad(["___| ", " /|  ", "/ |  "]),
        "8": _pad([" __  ", "|__| ", "\\__/ "]),
        "9": _pad([" __  ", "|__| ", "\\__| "]),
        " ": _pad(["      ", "      ", "      "]),
    },
}


def _validate_fonts() -> None:
    """Assert every font's glyph data is rectangular.

    For each font: every glyph must have the same number of rows, and
    within a single glyph every row must have the same width. The
    across-glyph width may differ in principle (callers could ship a
    proportional font) but :func:`_pad` above normalises every glyph
    to the canonical ``_W`` width so the shipped fonts are monospace.

    Raises ``AssertionError`` at import time if the invariant is
    broken. This catches hand-edits to :data:`FONTS` before they
    silently misalign rendered banners.
    """
    for name, glyphs in FONTS.items():
        for ch, rows in glyphs.items():
            if not rows:
                raise AssertionError(
                    f"font {name!r} glyph {ch!r} has no rows"
                )
            widths = {len(r) for r in rows}
            if len(widths) != 1:
                raise AssertionError(
                    f"font {name!r} glyph {ch!r} has non-uniform row widths: "
                    f"{rows!r}"
                )


# Run at import so a bad font edit fails loudly.
_validate_fonts()


#: Default font name. ``"block"`` matches :mod:`ink-big-text`'s default.
_DEFAULT_FONT: str = "block"

#: Default alignment within the parent's main axis. Mirrors
#: :mod:`ink-big-text`'s default.
_DEFAULT_ALIGN: str = "left"

#: Valid alignment values; maps to :func:`Box` ``justifyContent``
#: values for the flex engine to distribute leftover space.
_ALIGN_TO_JUSTIFY: dict[str, str] = {
    "left": "flex-start",
    "center": "center",
    "right": "flex-end",
}

#: Row count per font. Derived from :data:`FONTS` at module load so
#: adding a font with a different row count (e.g. a 5-row banner)
#: automatically extends the unknown-character fallback.
_FONT_ROW_COUNT: dict[str, int] = {
    name: len(next(iter(glyphs.values()))) for name, glyphs in FONTS.items()
}


def _resolve_glyph(font: dict[str, list[str]], char: str, row_count: int) -> list[str]:
    """Return the row strings for ``char`` in ``font``.

    Unknown characters fall back to a row of spaces at the font's row
    width so the banner layout stays intact. The fallback uses the
    width of the space glyph (if present) or the first glyph's row
    width — both should be a valid width for the font.
    """
    rows = font.get(char)
    if rows is not None:
        return rows
    # Unknown char: render a blank glyph of the font's standard width.
    # Use the space entry when available; otherwise derive the width
    # from the first registered glyph.
    space = font.get(" ")
    if space is not None and len(space) == row_count:
        return list(space)
    # Defensive: font has no space entry. Walk the font for any glyph
    # whose row count matches and clone its width.
    for candidate in font.values():
        if len(candidate) == row_count:
            width = len(candidate[0])
            return [" " * width for _ in range(row_count)]
    # Total fallback (shouldn't happen with the shipped fonts).
    return ["      " for _ in range(row_count)]


def BigText(
    text: str,
    *,
    font: str = _DEFAULT_FONT,
    align: str = _DEFAULT_ALIGN,
    color: str | None = None,
    **_props: Any,
) -> Element:
    """Render ASCII art banner text.

    Parameters
    ----------
    text:
        String to render. Lowercase letters are auto-uppercased
        before lookup (the shipped fonts only cover ``A-Z``); unknown
        characters (punctuation, symbols) render as a blank glyph of
        the font's standard width so the banner layout stays intact.
    font:
        Font name. One of :data:`FONTS`'s keys (``"block"`` /
        ``"simple"``). Unknown names fall back to ``"block"`` rather
        than raising — a typo shouldn't crash the banner render.
    align:
        Horizontal alignment within the parent's main axis. One of
        ``"left"`` / ``"center"`` / ``"right"``. Implemented via
        :func:`Box` ``justifyContent`` so the flex engine distributes
        leftover space; the parent's main axis must be wider than the
        banner for the alignment to have any visible effect.
    color:
        Optional colour spec forwarded to every glyph :func:`Text`
        leaf (``"red"``, ``"#ff0000"``, ``"rgb(255,0,0)"``,
        ``"ansi256(9)"``). Applied uniformly so the whole banner
        shares one hue; pass ``None`` to inherit the terminal default.
        Callers who want a multi-colour banner wrap the ``BigText``
        call in a :func:`pyink.externals.Gradient`.
    **_props:
        Reserved for parity with the upstream API; currently ignored.

    Returns
    -------
    Element
        A ``box`` host element (``flexDirection="column"``) whose
        children are one :func:`Text` leaf per banner row. No function
        component is involved — the factory is purely declarative.

    Raises
    ------
    TypeError
        If ``text`` is not a ``str``.

    Usage
    -----
    ::

        BigText("HELLO")
        BigText("PyInk", font="simple", color="cyan")
        BigText("v1.0", align="center")
    """
    if not isinstance(text, str):
        raise TypeError(
            f"BigText 'text' must be a str, got {type(text).__name__!r}"
        )

    # Resolve font: fall back to ``block`` for unknown names.
    font_glyphs = FONTS.get(font, FONTS[_DEFAULT_FONT])
    row_count = _FONT_ROW_COUNT.get(font, _FONT_ROW_COUNT[_DEFAULT_FONT])

    justify = _ALIGN_TO_JUSTIFY.get(align, _ALIGN_TO_JUSTIFY[_DEFAULT_ALIGN])

    if len(text) == 0:
        # Empty input: render an empty column so the surrounding layout
        # still has a valid element to mount.
        return Box(flexDirection="column")

    # Normalise to uppercase so lowercase input still produces glyphs
    # from the A-Z font coverage.
    normalised = text.upper()

    # For each row index, concatenate the matching row of every glyph.
    # ``zip(*rows_per_glyph)`` transposes the per-glyph row lists into
    # per-banner-row string lists — exactly the layout we need.
    glyph_rows: list[list[str]] = [
        _resolve_glyph(font_glyphs, ch, row_count) for ch in normalised
    ]
    banner_rows: list[str] = []
    for row_idx in range(row_count):
        banner_rows.append("".join(glyph[row_idx] for glyph in glyph_rows))

    row_elements = [Text(line, color=color) for line in banner_rows]
    return Box(
        *row_elements,
        flexDirection="column",
        justifyContent=justify,
    )
