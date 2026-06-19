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
from typing import Literal

__all__ = [
    "WrapMode",
    "string_width",
    "wcswidth",
    "wrap_text",
]

#: Supported wrap modes for :func:`wrap_text`.
WrapMode = Literal["wrap", "hard", "truncate", "truncate-start", "truncate-middle", "truncate-end"]

# Matches CSI-style escape sequences — covers SGR (colours/styles) and a
# handful of related control sequences that may appear in ``str`` text
# leaves produced by future PR4 helpers. ``\x1b`` is the ESC character.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# Combining / zero-width / control characters stripped by ``wcwidth`` but
# also useful to detect for the ASCII fallback path.
_COMBINING_RE = re.compile("[̀-ͯ᪰-᫿᷀-᷿⃐-⃿︠-︯]")

try:  # pragma: no cover - import-guard depends on environment
    from wcwidth import wcwidth as _wcwidth

    _HAS_WCWIDTH = True
except ImportError:  # pragma: no cover - exercised only without the dep
    _HAS_WCWIDTH = False


def _char_width(ch: str) -> int:
    """Display width of a single non-ANSI character (0 for combining)."""
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

    Equivalent to :func:`wcwidth.wcswidth` but ANSI-stripped and tolerant
    of unprintable characters (returns 0 for them instead of -1).
    """
    stripped = _ANSI_RE.sub("", s)
    if _HAS_WCWIDTH:
        total = 0
        for ch in stripped:
            w = _wcwidth(ch)
            total += w if w >= 0 else 0
        return total
    # ASCII fallback: combining marks don't count.
    return sum(0 if _COMBINING_RE.match(ch) else 1 for ch in stripped)


def string_width(s: str) -> int:
    """Return the display width of ``s``.

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
    """Return ``[(char, width)]`` for every visible character in ``s``.

    Combining marks are returned with width 0 and stay attached to their
    preceding base character at the cursor's current position.
    """
    out: list[tuple[str, int]] = []
    for ch in _strip_ansi(s):
        out.append((ch, _char_width(ch)))
    return out


def _hard_break(s: str, width: int) -> list[str]:
    """Force-break ``s`` every ``width`` display cells (may split words)."""
    if width <= 0:
        return [_strip_ansi(s)]
    lines: list[str] = []
    chunks = _split_visible_chunks(s)
    # Build a list of (text, is_escape) tokens, then iterate.
    current: list[str] = []
    current_w = 0
    pending_escape: list[str] = []  # escapes carried into the next line
    for chunk, is_escape in chunks:
        if is_escape:
            # Escape runs attach to whatever cursor position we're at; if
            # we're at the start of a line they get emitted first.
            current.append(chunk)
            continue
        for ch in chunk:
            w = _char_width(ch)
            if current_w + w > width and current_w > 0:
                lines.append("".join(current))
                current = list(pending_escape)
                current_w = 0
            current.append(ch)
            current_w += w
    if current_w > 0 or any(c for c in current if _ANSI_RE.fullmatch(c or "")):
        lines.append("".join(current))
    if not lines:
        lines.append("")
    return lines


def _word_break(s: str, width: int) -> list[str]:
    """Word-wrap ``s`` to ``width`` cells, breaking spaces only."""
    if width <= 0:
        return [_strip_ansi(s)]
    # Tokenise keeping whitespace runs and ANSI runs as separate tokens.
    tokens = _tokenise_words(s)
    lines: list[str] = []
    current: list[str] = []
    current_w = 0
    for tok_kind, tok_text in tokens:
        if tok_kind == "escape":
            current.append(tok_text)
            continue
        if tok_kind == "space":
            ws_w = len(tok_text)  # spaces are width 1 each
            if current_w + ws_w > width:
                # Break here — drop trailing whitespace on this line.
                lines.append("".join(current))
                current = []
                current_w = 0
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
        # Doesn't fit on current line — flush if there's content.
        if current_w > 0:
            # Trim trailing spaces on the line we're about to flush.
            while current and current[-1] == " ":
                current.pop()
            if current:
                lines.append("".join(current))
            current = []
            current_w = 0
        # Word is now at the start of a fresh line.
        if tok_w <= width:
            current.append(tok_text)
            current_w = tok_w
            continue
        # Word longer than ``width``: hard-split it.
        for part in _hard_break(tok_text, width):
            lines.append(part)
    if current_w > 0 or not lines:
        while current and current[-1] == " ":
            current.pop()
        lines.append("".join(current))
    return lines if lines else [""]


def _tokenise_words(s: str) -> list[tuple[str, str]]:
    """Yield ``(kind, text)`` tokens; kind ∈ {"word", "space", "escape"}.

    A "word" is a maximal run of non-space visible characters; "space" is
    a run of ASCII whitespace; "escape" is one ANSI sequence.
    """
    tokens: list[tuple[str, str]] = []
    chunks = _split_visible_chunks(s)
    for chunk, is_escape in chunks:
        if is_escape:
            tokens.append(("escape", chunk))
            continue
        i = 0
        n = len(chunk)
        while i < n:
            if chunk[i].isspace():
                j = i
                while j < n and chunk[j].isspace():
                    j += 1
                tokens.append(("space", chunk[i:j]))
                i = j
            else:
                j = i
                while j < n and not chunk[j].isspace():
                    j += 1
                tokens.append(("word", chunk[i:j]))
                i = j
    return tokens


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
    """Return the longest prefix of ``s`` whose visible width ≤ ``width``."""
    if width <= 0:
        return ""
    chunks = _split_visible_chunks(s)
    out: list[str] = []
    used = 0
    for chunk, is_escape in chunks:
        if is_escape:
            out.append(chunk)
            continue
        for ch in chunk:
            w = _char_width(ch)
            if used + w > width:
                return "".join(out)
            out.append(ch)
            used += w
    return "".join(out)


def _take_visible_tail(s: str, width: int) -> str:
    """Return the longest suffix of ``s`` whose visible width ≤ ``width``.

    ANSI escapes at the tail of the result are kept.
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
        for ch in reversed(chunk):
            w = _char_width(ch)
            if used + w > width:
                return "".join(reversed(out))
            out.append(ch)
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
