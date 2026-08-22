"""Text measurement utilities (PR3).

Two responsibilities live here:

* :func:`string_width` — the **display width** of a string as it would
  occupy cells in a fixed-width terminal. CJK characters count as 2,
  combining marks as 0, ANSI escape sequences as 0, everything else as 1.
* :func:`wrap_text` — split a single-paragraph string into a list of
  display-width-bounded lines or into a single truncated line.

The two helpers are ANSI-aware: colour/style escape sequences do not
contribute to the measured width and survive into the wrapped output so
PR4 can re-emit them when rendering styled text.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

__all__ = [
    "WrapMode",
    "string_width",
    "wcswidth",
    "wrap_text",
    "wrap_text_rows",
]

#: Supported wrap modes for :func:`wrap_text`.
WrapMode = Literal["wrap", "hard", "truncate", "truncate-start", "truncate-middle", "truncate-end"]

# Matches ANSI escape sequences that may appear in ``str`` text leaves.
# Two flavours are covered:
#
# * CSI — ``\x1b[...`` sequences (SGR colours/styles and related controls).
# * OSC — ``\x1b]...ST`` operating-system commands, terminated by either
#   BEL (``\x07``) or ST (``\x1b\\``). OSC 8 hyperlinks (emitted by
#   :func:`ink.externals.Link`) are the canonical in-tree user; without
#   this branch the measure layer would count the OSC payload as visible
#   width and the layout would over-allocate cells.
#
# ``\x1b`` is the ESC character.
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"      # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC (BEL- or ST-terminated)
)
# Combining / zero-width / control characters stripped by ``wcwidth`` but
# also useful to detect for the ASCII fallback path.
_COMBINING_RE = re.compile("[̀-ͯ᪰-᫿᷀-᷿⃐-⃿︠-︯]")

try:  # pragma: no cover - import-guard depends on environment
    from wcwidth import iter_graphemes as _iter_graphemes_upstream
    from wcwidth import wcswidth as _wcswidth_upstream
    from wcwidth import wcwidth as _wcwidth

    _HAS_WCWIDTH = True
except ImportError:  # pragma: no cover - exercised only without the dep
    _HAS_WCWIDTH = False


def _char_width(ch: str) -> int:
    """Display width of a single non-ANSI character (0 for combining).

    Tab (``\\t``) is special-cased to 1 cell: POSIX ``wcwidth`` returns
    -1 for control chars which would otherwise map to 0 here, but a
    0-width tab causes :func:`string_width` to under-count the row
    capacity in :func:`ink.layout.render_layout._paint_text` while the
    per-char loop still advances for following visible chars — landing
    them past the allocated row and triggering ``IndexError`` on
    ``grid.put``. TUI convention (matches Textual / Rich): tab renders
    as a single cell, no tab-stop expansion.
    """
    if ch == "\t":
        return 1
    if _HAS_WCWIDTH:
        w = _wcwidth(ch)
        # ``wcwidth`` returns -1 for non-printable control chars; treat
        # those as zero-width so they never contribute to layout.
        return w if w >= 0 else 0
    # ASCII-only fallback: combining marks already filtered by caller.
    if _COMBINING_RE.match(ch):
        return 0
    return 1


def wcswidth(s: str) -> int:
    """Display width of ``s`` ignoring ANSI escape sequences.

    Delegates to upstream ``wcwidth.wcswidth`` (0.8.x), which is
    grapheme-cluster-aware and handles emoji presentation sequences,
    keycaps, ZWJ families, regional flags, and skin-tone modifiers.
    Returns 0 for unprintable / control-character inputs.

    Tab (``\\t``) is pre-replaced with a regular space before
    delegation: POSIX ``wcswidth`` short-circuits to -1 the moment it
    sees any control char, which would otherwise zero out the entire
    string width and crash ``_paint_text`` 's row-capacity calculation.
    Newlines are normalized and split before delegation for the same
    reason: a multi-line string's width is its widest logical line,
    not 0.
    """
    stripped = _ANSI_RE.sub("", s)
    if not stripped:
        return 0
    # Tab → space so wcwidth's POSIX-strict -1 return doesn't poison
    # the whole string. Per-char width (``_char_width``) keeps the tab
    # visible at its own 1-cell value during the paint loop.
    sanitized = stripped.replace("\t", " ")
    sanitized = sanitized.replace("\r\n", "\n").replace("\r", "\n")
    if _HAS_WCWIDTH:
        if "\n" in sanitized:
            widest = 0
            for line in sanitized.split("\n"):
                w = _wcswidth_upstream(line)
                if w > widest:
                    widest = w
            return widest
        w = _wcswidth_upstream(sanitized)
        return w if w >= 0 else 0
    # ASCII fallback (no wcwidth installed) — keep the existing path.
    return sum(0 if _COMBINING_RE.match(ch) else 1 for ch in sanitized)


def string_width(s: str) -> int:
    """Return the display width of ``s``.

    Grapheme-cluster-aware: emoji presentation sequences, keycaps, ZWJ
    families, regional flags, and skin-tone modifiers count as a single
    cluster with the width the terminal will actually render. See
    ``.trellis/spec/frontend/rendering-contracts.md §2`` for the contract.
    Wrap helpers (``wrap_text``, ``_hard_break``, ``_take_visible``)
    iterate clusters atomically and never split one across lines.

    Examples
    --------
    >>> string_width("abc")
    3
    >>> string_width("你好")
    4
    >>> string_width("\\x1b[31mred\\x1b[0m")
    3
    """
    if not s:
        return 0
    return wcswidth(s)


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _iter_clusters(s: str) -> list[tuple[str, int]]:
    """Return ``[(cluster_str, cluster_width), ...]`` for ``s``.

    ANSI escape sequences are stripped first; cluster boundaries come
    from ``wcwidth.iter_graphemes`` and width from ``wcwidth.wcswidth``.
    Falls back to per-codepoint iteration when wcwidth is unavailable,
    matching the legacy behaviour.
    """
    stripped = _strip_ansi(s)
    if _HAS_WCWIDTH:
        out: list[tuple[str, int]] = []
        for cl in _iter_graphemes_upstream(stripped):
            # Single-codepoint clusters (incl. ``\t``) honour the tab
            # 1-cell special case via ``_char_width``; multi-codepoint
            # clusters (emoji presentation, keycap, ZWJ, flag) keep
            # their wcswidth value, which matches ``string_width``.
            if len(cl) == 1:
                out.append((cl, _char_width(cl)))
            else:
                w = _wcswidth_upstream(cl)
                out.append((cl, w if w >= 0 else 0))
        return out
    return [(ch, _char_width(ch)) for ch in stripped]


def _split_visible_chunks(s: str) -> list[tuple[str, bool]]:
    """Split ``s`` into ``(chunk, is_escape)`` pairs preserving order.

    Used so wrappers can advance by *visible* character while keeping the
    original escape runs in the output verbatim.
    """
    chunks: list[tuple[str, bool]] = []
    pos = 0
    for m in _ANSI_RE.finditer(s):
        if m.start() > pos:
            chunks.append((s[pos:m.start()], False))
        chunks.append((m.group(), True))
        pos = m.end()
    if pos < len(s):
        chunks.append((s[pos:], False))
    return chunks


def _visible_chars(s: str) -> list[tuple[str, int]]:
    """Return ``[(cluster, width)]`` for every grapheme cluster in ``s``.

    ANSI escapes are stripped; the unit of iteration is a grapheme
    cluster (so emoji ZWJ families, keycaps, presentation sequences and
    regional flags are returned as atomic units).
    """
    return _iter_clusters(s)


def _hard_break(s: str, width: int) -> list[str]:
    """Force-break ``s`` every ``width`` display cells (may split words).

    Iterates per grapheme cluster so cluster boundaries stay atomic —
    an emoji presentation sequence, keycap, ZWJ family or regional flag
    is never split across two lines. If a single cluster is wider than
    ``width`` it is emitted as its own overflowing line.
    """
    if width <= 0:
        return [_strip_ansi(s)]
    lines: list[str] = []
    chunks = _split_visible_chunks(s)
    current: list[str] = []
    current_w = 0
    pending_escape: list[str] = []  # escapes carried into the next line
    for chunk, is_escape in chunks:
        if is_escape:
            # Escape runs attach to whatever cursor position we're at; if
            # we're at the start of a line they get emitted first.
            current.append(chunk)
            continue
        for cl, w in _iter_clusters(chunk):
            if current_w + w > width and current_w > 0:
                lines.append("".join(current))
                current = list(pending_escape)
                current_w = 0
            current.append(cl)
            current_w += w
    if current_w > 0 or any(c for c in current if _ANSI_RE.fullmatch(c or "")):
        lines.append("".join(current))
    if not lines:
        lines.append("")
    return lines


def _word_break(s: str, width: int) -> list[str]:
    """Word-wrap ``s`` to ``width`` cells.

    Break opportunities: whitespace runs and boundaries between wide
    CJK clusters (UAX #14 subset — see :func:`_tokenise_words`).
    Latin words and Hangul runs stay atomic; a word longer than a
    full line is hard-split via :func:`_hard_break`.
    """
    return [row for row, _start, _end in _word_break_rows(s, width)]


def _word_break_rows(s: str, width: int) -> list[tuple[str, int, int]]:
    """Word-wrap ``s``; return ``(row_text, start, end)`` per row.

    The half-open char spans partition ``[0, len(s))`` in order
    (each row's ``end`` equals the next row's ``start``; the last row
    ends at ``len(s)``). Whitespace dropped at a wrap boundary stays
    inside the preceding row's span — it is not rendered, and a
    cursor offset inside the dropped run reads as end-of-row. This
    offset map lets callers (``TextInput``) wrap the raw text first
    and splice per-row overlays (cursor / selection SGR) afterwards,
    instead of wrapping an already-styled string whose escape runs
    could be split from the cells they style.
    """
    if width <= 0:
        return [(_strip_ansi(s), 0, len(s))]
    tokens = _tokenise_words(s)
    rows: list[tuple[str, int, int]] = []
    current: list[str] = []
    current_w = 0
    row_start: int | None = None

    def flush(end: int) -> None:
        nonlocal current, current_w, row_start
        rows.append(
            ("".join(current), row_start if row_start is not None else end, end)
        )
        current = []
        current_w = 0
        row_start = None

    for tok_kind, tok_text, tok_start, tok_end in tokens:
        if row_start is None:
            row_start = tok_start
        if tok_kind == "escape":
            current.append(tok_text)
            continue
        if tok_kind == "space":
            ws_w = wcswidth(tok_text)  # U+3000 ideographic space is 2 cells
            if current_w + ws_w > width:
                # Break here — drop trailing whitespace on this row.
                # The dropped run stays inside this row's span.
                flush(tok_end)
                continue
            current.append(tok_text)
            current_w += ws_w
            continue
        # word token
        tok_w = wcswidth(tok_text)
        if tok_w <= width - current_w:
            current.append(tok_text)
            current_w += tok_w
            continue
        # Doesn't fit on current row — flush if there's content.
        if current_w > 0:
            # Trim trailing spaces on the row we're about to flush.
            while current and current[-1] == " ":
                current.pop()
            if current:
                flush(tok_start)
            else:
                current = []
                current_w = 0
                row_start = tok_start
        # Word is now at the start of a fresh row.
        if tok_w <= width:
            current.append(tok_text)
            current_w = tok_w
            row_start = tok_start
            continue
        # Word longer than ``width``: hard-split it. The parts are
        # consecutive substrings of the token, so spans accumulate.
        part_off = tok_start
        for part in _hard_break(tok_text, width):
            rows.append((part, part_off, part_off + len(part)))
            part_off += len(part)
        current = []
        current_w = 0
        row_start = None
    if current_w > 0 or not rows:
        while current and current[-1] == " ":
            current.pop()
        rows.append(
            (
                "".join(current),
                row_start if row_start is not None else len(s),
                len(s),
            )
        )
    return rows if rows else [("", 0, 0)]


#: Hangul blocks. Korean line breaking is space-based (UAX #14 LB28
#: keeps Hangul syllable sequences unbroken), so Hangul clusters never
#: become individual break tokens even though they are East Asian "W".
_HANGUL_RANGES: tuple[tuple[int, int], ...] = (
    (0x1100, 0x11FF),
    (0xA960, 0xA97F),
    (0x3130, 0x318F),
    (0xAC00, 0xD7A3),
    (0xD7B0, 0xD7FF),
)

#: Kinsoku (UAX #14 CL/OP approximation): closers never start a line,
#: openers never end one.
_KINSOKU_NO_LINE_START = frozenset(
    "！？｡。．，、；：…‥〜～·）〕］｝》〉」』】"
    "ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶー"
)
_KINSOKU_NO_LINE_END = frozenset("（〔［｛《〈「『【")


def _is_cjk_breakable(cluster: str) -> bool:
    """True when a break is allowed after ``cluster``.

    Approximates the UAX #14 ideographic break opportunities: Han,
    kana, fullwidth punctuation and wide emoji clusters each form
    their own word token, so wrapping may break between any two of
    them. Hangul is excluded (Korean breaks at spaces, LB28).
    """
    ch = cluster[0]
    if unicodedata.east_asian_width(ch) not in ("W", "F"):
        return False
    o = ord(ch)
    return not any(lo <= o <= hi for lo, hi in _HANGUL_RANGES)


def _merge_kinsoku(
    tokens: list[tuple[str, str, int, int]],
) -> list[tuple[str, str, int, int]]:
    """Glue kinsoku punctuation onto the adjacent word token.

    Closers (``。``, ``、``, ``」`` …) merge into the *previous* word
    token so they can never be pushed to a line start; openers
    (``（``, ``「`` …) merge into the *following* word token so they
    can never dangle at a line end. Chained openers accumulate onto
    the next word token together. Spans widen to cover the merged
    text (adjacent tokens are contiguous in the source).
    """
    merged: list[tuple[str, str, int, int]] = []
    for kind, text, start, end in tokens:
        if (
            kind == "word"
            and text in _KINSOKU_NO_LINE_START
            and merged
            and merged[-1][0] == "word"
        ):
            _pk, pt, ps, _pe = merged[-1]
            merged[-1] = ("word", pt + text, ps, end)
        else:
            merged.append((kind, text, start, end))
    out: list[tuple[str, str, int, int]] = []
    glue = ""
    glue_start = -1
    for kind, text, start, end in merged:
        if kind != "word":
            if glue:
                out.append(("word", glue, glue_start, start))
                glue = ""
                glue_start = -1
            out.append((kind, text, start, end))
        elif text in _KINSOKU_NO_LINE_END:
            if glue_start < 0:
                glue_start = start
            glue += text
        else:
            out.append(("word", glue + text, start if not glue else glue_start, end))
            glue = ""
            glue_start = -1
    if glue:
        out.append(("word", glue, glue_start, glue_start + len(glue)))
    return out


def _tokenise_words(s: str) -> list[tuple[str, str, int, int]]:
    """Yield ``(kind, text, start, end)`` tokens; kind ∈ {"word",
    "space", "escape"}; ``start`` / ``end`` are char offsets into
    ``s`` (half-open, contiguous across tokens).

    A "word" is a maximal run of clusters that must stay unbroken:
    latin words, Hangul runs, and kinsoku-glued groups are atomic.
    Each wide CJK cluster (Han, kana, fullwidth punctuation, emoji)
    is its own token, so wrap mode may break between CJK clusters
    (UAX #14 subset). "space" is a run of whitespace clusters;
    "escape" is one ANSI sequence.
    """
    tokens: list[tuple[str, str, int, int]] = []
    pos = 0
    for chunk, is_escape in _split_visible_chunks(s):
        if is_escape:
            tokens.append(("escape", chunk, pos, pos + len(chunk)))
            pos += len(chunk)
            continue
        chunk_base = pos
        cl_off = 0
        word: list[str] = []
        word_start = -1
        pending_space: list[str] = []
        space_start = -1
        for cl, _w in _iter_clusters(chunk):
            cl_start = chunk_base + cl_off
            cl_off += len(cl)
            if cl.isspace():
                if space_start < 0:
                    space_start = cl_start
                pending_space.append(cl)
                continue
            if pending_space:
                if word:
                    tokens.append(("word", "".join(word), word_start, space_start))
                    word = []
                    word_start = -1
                tokens.append(("space", "".join(pending_space), space_start, cl_start))
                pending_space = []
                space_start = -1
            if _is_cjk_breakable(cl):
                if word:
                    tokens.append(("word", "".join(word), word_start, cl_start))
                    word = []
                    word_start = -1
                tokens.append(("word", cl, cl_start, cl_start + len(cl)))
            else:
                if word_start < 0:
                    word_start = cl_start
                word.append(cl)
        if word:
            tokens.append(("word", "".join(word), word_start, chunk_base + cl_off))
        if pending_space:
            tokens.append(
                ("space", "".join(pending_space), space_start, chunk_base + cl_off)
            )
        pos = chunk_base + cl_off
    return _merge_kinsoku(tokens)


_ELLIPSIS = "…"
_ELLIPSIS_WIDTH = 1


def _trailing_escapes(s: str) -> str:
    """Return any ANSI escape runs that sit at the very end of ``s``."""
    chunks = _split_visible_chunks(s)
    tail: list[str] = []
    for chunk, is_escape in reversed(chunks):
        if is_escape:
            tail.append(chunk)
        else:
            break
    return "".join(reversed(tail))


def _truncate_end(s: str, width: int) -> str:
    if width <= 0:
        return ""
    visible = _strip_ansi(s)
    if wcswidth(visible) <= width:
        return s
    target = width - _ELLIPSIS_WIDTH
    if target <= 0:
        return _ELLIPSIS[:width]
    return _take_visible(s, target) + _ELLIPSIS + _trailing_escapes(s)


def _truncate_start(s: str, width: int) -> str:
    if width <= 0:
        return ""
    visible = _strip_ansi(s)
    if wcswidth(visible) <= width:
        return s
    target = width - _ELLIPSIS_WIDTH
    if target <= 0:
        return _ELLIPSIS[:width]
    return _leading_escapes(s) + _ELLIPSIS + _take_visible_tail(s, target)


def _truncate_middle(s: str, width: int) -> str:
    if width <= 0:
        return ""
    visible = _strip_ansi(s)
    if wcswidth(visible) <= width:
        return s
    target = width - _ELLIPSIS_WIDTH
    if target <= 0:
        return _ELLIPSIS[:width]
    head = target // 2
    tail = target - head
    return (
        _leading_escapes(s)
        + _take_visible(s, head)
        + _ELLIPSIS
        + _take_visible_tail(s, tail)
        + _trailing_escapes(s)
    )


def _leading_escapes(s: str) -> str:
    """Return any ANSI escape runs that sit at the very start of ``s``."""
    chunks = _split_visible_chunks(s)
    head: list[str] = []
    for chunk, is_escape in chunks:
        if is_escape:
            head.append(chunk)
        else:
            break
    return "".join(head)


def _take_visible(s: str, width: int) -> str:
    """Return the longest prefix of ``s`` whose visible width ≤ ``width``.

    Grapheme clusters are atomic: a cluster is taken whole or not at all.
    """
    if width <= 0:
        return ""
    chunks = _split_visible_chunks(s)
    out: list[str] = []
    used = 0
    for chunk, is_escape in chunks:
        if is_escape:
            out.append(chunk)
            continue
        for cl, w in _iter_clusters(chunk):
            if used + w > width:
                return "".join(out)
            out.append(cl)
            used += w
    return "".join(out)


def _take_visible_tail(s: str, width: int) -> str:
    """Return the longest suffix of ``s`` whose visible width ≤ ``width``.

    ANSI escapes at the tail of the result are kept. Grapheme clusters
    are atomic: a cluster is taken whole or not at all.
    """
    if width <= 0:
        return ""
    chunks = _split_visible_chunks(s)
    out: list[str] = []
    used = 0
    for chunk, is_escape in reversed(chunks):
        if is_escape:
            out.append(chunk)
            continue
        for cl, w in reversed(_iter_clusters(chunk)):
            if used + w > width:
                return "".join(reversed(out))
            out.append(cl)
            used += w
    return "".join(reversed(out))


def wrap_text(
    s: str,
    width: int,
    *,
    mode: WrapMode = "wrap",
) -> list[str]:
    """Split ``s`` into lines bounded by ``width`` display cells.

    Parameters
    ----------
    s:
        Input string. May contain ANSI escape sequences and embedded
        newlines (which force a line break in every mode).
    width:
        Maximum display width per returned line.
    mode:
        One of ``"wrap"`` (word-wrap), ``"hard"`` (character-forced),
        ``"truncate"`` / ``"truncate-end"`` (single line, ellipsis at the
        end), ``"truncate-start"`` (ellipsis at the beginning) or
        ``"truncate-middle"`` (ellipsis in the middle).

    Returns
    -------
    list[str]
        One entry per line, never containing a trailing newline character.
    """
    if width <= 0:
        return [s]

    # Pre-split on embedded newlines so every mode preserves them.
    paragraphs = s.split("\n")
    out_lines: list[str] = []

    for para in paragraphs:
        if mode == "wrap":
            out_lines.extend(_word_break(para, width) if para else [""])
        elif mode == "hard":
            out_lines.extend(_hard_break(para, width) if para else [""])
        elif mode in ("truncate", "truncate-end"):
            out_lines.append(_truncate_end(para, width))
        elif mode == "truncate-start":
            out_lines.append(_truncate_start(para, width))
        elif mode == "truncate-middle":
            out_lines.append(_truncate_middle(para, width))
        else:  # pragma: no cover - exhaustive enum
            raise ValueError(f"Unknown wrap mode: {mode!r}")

    return out_lines


def wrap_text_rows(s: str, width: int) -> list[tuple[str, int, int]]:
    """Word-wrap ``s`` into rows annotated with source char spans.

    Returns ``[(row_text, start, end), ...]`` using the same
    word-break rules as ``wrap_text(mode="wrap")`` (whitespace and
    CJK cluster boundaries; latin / Hangul words atomic; overly long
    words hard-split). The half-open spans partition ``[0, len(s))``
    so a caller can map a char offset in ``s`` to exactly one row —
    the primitive ``TextInput`` uses to splice cursor / selection
    overlays per row after wrapping the raw text.

    ``s`` must not contain ``\\n`` (pre-split logical lines, as
    ``TextInput`` does). ``width <= 0`` returns the whole (ANSI-
    stripped) string as one row spanning the full range.
    """
    return _word_break_rows(s, width)
