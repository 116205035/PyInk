"""``TextInput`` — single-line + multi-line text input (Phase 4 PR1 + PR2).

Mirrors ``ink-text-input`` at the level Phase 4 needs:

* PR1 — single-line editing with a visible cursor, basic Emacs-style
  word / line kill shortcuts, password masking and ``max_length``
  truncation.
* PR2 — multi-line editing (Enter inserts ``\\n``), cross-line cursor
  movement (ArrowUp / ArrowDown), selection (Shift + arrows / Home /
  End, with inverse-video rendering), and bracketed-paste handling.

Design (per PRD Decision 1):

* ``TextInput`` is a factory returning an :class:`Element` whose
  ``type`` is the :func:`_TextInputImpl` function component. The
  factory itself never runs hooks; only the wrapped function does,
  when the reconciler mounts it.
* Three writable :class:`Signal` s live inside the component: ``value``
  (current string), ``cursor`` (character offset,
  ``0 <= cursor <= len(value)``), and ``selection`` (``None`` or a
  ``[start, end)`` pair). Every keypress updates one or more; the
  layout-time callable child subscribes to all of them so the render
  loop re-paints on each change.
* Callbacks (``on_change`` / ``on_submit``) are captured in
  :class:`Ref` s refreshed every mount — this lets the caller swap
  callbacks between mounts without resubscribing the input handler.
  The handler closure is registered exactly once via
  :func:`ink.hooks.use_input`.
* Long content **wraps, never truncates**: each logical line is
  word-wrapped into display rows (:func:`ink.layout.measure.wrap_text_rows`)
  and cursor / selection SGR is spliced per row using the row's source
  span; the input grows vertically (ink.js parity — ``ink-text-input``
  renders a plain wrapping ``<Text>``). The Text leaf keeps
  ``wrap="truncate-end"`` purely as a safety net.

Cursor rendering (per PRD Section "cursor 渲染细节"):

* ``bar`` — a one-cell inverse-video space is inserted at the cursor
  position; the surrounding characters keep their normal style. This
  matches the VSCode / vim insert-mode feel.
* ``block`` — the character *under* the cursor is rendered with
  inverse video; if the cursor is at end-of-input an inverse space is
  shown instead. Classic terminal "overwrite" cursor.
* ``underline`` — the character under the cursor gets an underline; at
  end-of-input a bare underline escape with no glyph is shown.

Selection rendering (PR2): the ``[start, end)`` range of the buffer is
wrapped in SGR "inverse video" escapes. The escapes are inserted
*before* the cursor splice so the selection paint is independent of the
cursor paint; the layout measure pass strips CSI sequences so neither
selection nor cursor escapes inflate the column budget.

IME caret anchor (08-20-ime-caret-anchor-…): ``_render_lines`` also
writes a payload-relative ``(display_row, visible_col)`` caret signal
(hung on the Text leaf via the ``caret`` decoration prop). The layout
painter translates it to absolute frame coordinates and the renderer
parks the *physical* terminal cursor there after every paint (keeping
it hidden) — terminal IME engines anchor their composition /
candidate window on the physical cursor, so this is what makes CJK
preedit appear inside the input row instead of one row below it. The
anchor is gated on ``is_active AND cursor_visible``: the anchor must
follow the *keystroke target* (``is_active`` — where typed text will
land), not the visual cursor alone. In a multi-input form every input
would otherwise report a caret and the painter's last-writer-wins
rule would pin the IME preview to whichever input paints last, even
though keystrokes land in the focused one. IME preedit is an *input*
preview, not a visual decoration, so it belongs to the input that is
about to receive the text.

Paste (PR2): when :class:`ink.render.keys.Key` arrives with a
``paste`` payload (delivered by the Terminal's bracketed-paste
dispatcher) the whole payload is spliced into the buffer at the cursor
in a single edit, replacing any active selection, and ``on_change``
fires exactly once.

Out of scope (Phase 4):

* Vim mode (Phase 4.5).
* Complex input history (Up / Down scrolling previous values).

Esc / Ctrl+C are deliberately *not* swallowed so the surrounding
``exit_on_ctrl_c`` pipeline still owns them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from ink.components.box import Box
from ink.components.text import Text
from ink.core.element import Element, create_element
from ink.core.signal import Ref, Signal, effect, ref, signal
from ink.hooks.input import use_input
from ink.layout._text_width_context import get_current_text_width
from ink.layout.measure import string_width, wrap_text_rows
from ink.render.ansi import apply_style, parse_color
from ink.render.keys import Key

__all__ = [
    "TextInput",
    "cursor_column",
    "cursor_line",
]

#: Allowed values for the ``cursor_style`` prop.
CursorStyle = Literal["bar", "block", "underline"]


def _resolve_is_active(is_active: object) -> bool:
    """Evaluate an ``is_active`` prop value into a plain ``bool``.

    Accepts three shapes:

    * :class:`Signal` — read ``.value`` *without* subscribing so the
      input handler closure is not tied to the signal's reactivity
      (keystroke dispatch must stay imperative).
    * 0-arg callable — invoked.
    * anything else — coerced via ``bool(...)``.

    Order matters: ``Signal`` objects are callable (``.value`` is a
    ``@property``), so the ``isinstance(..., Signal)`` branch must come
    before the ``callable(...)`` branch — otherwise a signal would be
    treated as a generic callable and we'd silently read ``.value`` via
    attribute access (which still works), but the intent of the branch
    ordering is to make the dispatch explicit and future-proof against
    subclasses adding ``__call__``.
    """
    if isinstance(is_active, Signal):
        return bool(is_active.value)
    if callable(is_active):
        return bool(is_active())
    return bool(is_active)

#: SGR "inverse video" sequence — used by ``bar`` / ``block`` cursors and
#: by selection rendering.
_SGR_INVERSE = "\x1b[7m"
#: SGR "underline" sequence — used by the ``underline`` cursor.
_SGR_UNDERLINE = "\x1b[4m"
#: SGR reset — closes every cursor / selection SGR run.
_SGR_RESET = "\x1b[0m"


# ---------------------------------------------------------------------------
# Line / column helpers (PRD Decision 2)
# ---------------------------------------------------------------------------


def cursor_line(value: str, cursor: int) -> int:
    """Return the 0-based line number of ``cursor`` within ``value``.

    The line number is the count of ``\\n`` characters strictly before
    the cursor offset. Line 0 is the first line; an offset past the
    final newline sits on the last line.
    """
    clamped = max(0, min(cursor, len(value)))
    return value[:clamped].count("\n")


def cursor_column(value: str, cursor: int) -> int:
    """Return the 0-based column number of ``cursor`` within its line.

    The column resets to 0 at the start of every line; for an offset on
    line N it is ``cursor - (index of the line's leading ``\\n``) - 1``.
    On line 0 (no preceding newline) the column is simply ``cursor``.
    """
    clamped = max(0, min(cursor, len(value)))
    last_newline = value.rfind("\n", 0, clamped)
    if last_newline == -1:
        return clamped
    return clamped - last_newline - 1


# ---------------------------------------------------------------------------
# Internal helpers — word boundary, cursor cell, displayed text
# ---------------------------------------------------------------------------


def _is_word_char(ch: str) -> bool:
    """``True`` for characters that count as part of a word (alphanumeric / underscore)."""
    return ch.isalnum() or ch == "_"


def _line_bounds(value: str, cursor: int) -> tuple[int, int]:
    """Return the ``[start, end)`` offsets of the line containing ``cursor``.

    ``end`` is the offset of the next ``\\n`` (or ``len(value)`` if the
    cursor is on the last line). ``start`` is the offset right after the
    previous ``\\n`` (or 0). Both bounds are exclusive of the newline
    character itself.
    """
    last_newline = value.rfind("\n", 0, cursor)
    start = 0 if last_newline == -1 else last_newline + 1
    next_newline = value.find("\n", cursor)
    end = len(value) if next_newline == -1 else next_newline
    return start, end


def _line_start(value: str, cursor: int) -> int:
    return _line_bounds(value, cursor)[0]


def _line_end(value: str, cursor: int) -> int:
    return _line_bounds(value, cursor)[1]


def _offset_above(value: str, cursor: int) -> int | None:
    """Offset of the same column on the previous line, or ``None``.

    Returns the offset of line 0's start (column 0) when ``cursor`` is
    on the first line — the caller treats this as "stay on the first
    line" by clamping to ``_line_start``.
    """
    start, _ = _line_bounds(value, cursor)
    if start == 0:
        return None
    # The newline that ends the previous line sits at ``start - 1``.
    prev_end = start - 1
    prev_start = _line_start(value, prev_end)
    col = cursor - start
    target = prev_start + col
    prev_line_end = _line_end(value, prev_end)
    return min(target, prev_line_end)


def _offset_below(value: str, cursor: int) -> int | None:
    """Offset of the same column on the next line, or ``None``.

    Returns the offset of the last line's end when ``cursor`` is on the
    last line — caller treats this as "stay on the last line" by
    clamping to ``_line_end``.
    """
    _, end = _line_bounds(value, cursor)
    if end >= len(value):
        return None
    # The next line begins right after the newline at ``end``.
    next_start = end + 1
    next_end = _line_end(value, next_start)
    col = cursor - _line_start(value, cursor)
    target = next_start + col
    return min(target, next_end)


def _word_boundary_left(value: str, cursor: int) -> int:
    """Offset of the start of the word (or whitespace run) before ``cursor``.

    Mirrors readline's ``backward-word`` motion: skip whitespace first,
    then skip the alphanumeric/underscore run that precedes it.
    """
    pos = cursor
    while pos > 0 and value[pos - 1].isspace():
        pos -= 1
    while pos > 0 and _is_word_char(value[pos - 1]):
        pos -= 1
    return pos


def _word_boundary_right(value: str, cursor: int) -> int:
    """Offset of the start of the next word (or whitespace run) after ``cursor``.

    Mirrors readline's ``forward-word`` motion: skip the current
    alphanumeric/underscore run, then skip the trailing whitespace.
    """
    pos = cursor
    while pos < len(value) and _is_word_char(value[pos]):
        pos += 1
    while pos < len(value) and value[pos].isspace():
        pos += 1
    return pos


def _cursor_cell(
    style: CursorStyle,
    *,
    char: str,
    cursor_color: str | None,
) -> str:
    """Render a single cursor cell.

    Parameters
    ----------
    style:
        One of ``"bar"`` / ``"block"`` / ``"underline"``.
    char:
        The character at the cursor position. Empty string means the
        cursor sits at end-of-input and a space cell should be used.
    cursor_color:
        Optional colour spec applied to the cursor cell.
    """
    cell = char if char else " "
    if style == "bar":
        # A bar cursor occupies its own cell — an inverse-video space.
        # ``cursor_color`` tints the foreground so terminals that paint
        # ``inverse`` by swapping fg/bg render the bar in that colour.
        if cursor_color:
            body = parse_color(cursor_color, type_="foreground")
            open_seq = f"\x1b[{body}m{_SGR_INVERSE}" if body else _SGR_INVERSE
        else:
            open_seq = _SGR_INVERSE
        return f"{open_seq} {_SGR_RESET}"
    if style == "block":
        # Inverse video on the cursor character itself. Colour is
        # applied under the inverse so the cell reads as coloured-on-
        # inverted background.
        if cursor_color:
            body = parse_color(cursor_color, type_="foreground")
            open_seq = f"\x1b[{body}m{_SGR_INVERSE}" if body else _SGR_INVERSE
        else:
            open_seq = _SGR_INVERSE
        return f"{open_seq}{cell}{_SGR_RESET}"
    # underline — apply underline (and optional colour) to the cursor
    # character without inverting it.
    if cursor_color:
        body = parse_color(cursor_color, type_="foreground")
        open_seq = f"\x1b[{body}m{_SGR_UNDERLINE}" if body else _SGR_UNDERLINE
    else:
        open_seq = _SGR_UNDERLINE
    return f"{open_seq}{cell}{_SGR_RESET}"


def _apply_selection(
    displayed: str,
    *,
    value_start: int,
    value_end: int,
    sel_start: int,
    sel_end: int,
) -> str:
    """Wrap the ``[sel_start, sel_end)`` slice of ``displayed`` in inverse video.

    ``value_start`` / ``value_end`` are the half-open range of the
    underlying value that ``displayed`` corresponds to (selection is
    per-line — only the slice that overlaps the current line is
    highlighted). Offsets outside the range contribute no escape.

    .. note::
       This helper is retained for simple no-cursor cases. Cursor rows
       go through :func:`_build_displayed_line`, which walks characters
       by **display width** so that CJK wide characters align the
       selection SGR with the cursor's visible column (Bug 4 fix).
    """
    if sel_end <= value_start or sel_start >= value_end:
        return displayed
    # Translate selection offsets into the local coordinate of this
    # displayed run.
    local_start = max(0, sel_start - value_start)
    local_end = min(value_end, sel_end) - value_start
    local_start = max(0, min(local_start, len(displayed)))
    local_end = max(local_start, min(local_end, len(displayed)))
    before = displayed[:local_start]
    body = displayed[local_start:local_end]
    after = displayed[local_end:]
    if not body:
        return displayed
    return f"{before}{_SGR_INVERSE}{body}{_SGR_RESET}{after}"


def _build_displayed_line(
    line_text: str,
    cursor_in_line: bool,
    cursor: int,
    line_start: int,
    *,
    cursor_style: CursorStyle,
    cursor_color: str | None,
    selection: tuple[int, int] | None,
    mask: str | None = None,
    line_text_raw: str | None = None,
    cursor_visible: bool = True,
) -> str:
    """Render one line of the buffer with cursor + selection overlays.

    Walks the line **by display width**, not character offset, so the
    cursor SGR lands on the exact visible column returned by
    :func:`_cursor_visible_column`. For CJK / wide-character rows the
    visible column diverges from the character offset; this walk keeps
    cursor + selection aligned with the row-span mapping the wrapper
    computes (Bug 4).

    Parameters
    ----------
    line_text:
        Visible text for the line (already mask-applied, no newline).
    cursor_in_line:
        Whether the cursor sits in this line (any column, including the
        end-of-line position right before a ``\\n``).
    cursor:
        Absolute cursor offset.
    line_start:
        Offset of the first character of this line.
    cursor_style / cursor_color:
        Forwarded from props.
    selection:
        Active selection as an absolute ``[start, end)`` pair, or
        ``None``.
    mask:
        The mask glyph in use (or ``None``). Used to translate
        selection offsets to visible columns consistently with the
        cursor (masked chars have width 1 regardless of glyph, except
        for wide-char masks which fall back to their real width).
    line_text_raw:
        The un-masked source line. Required when ``mask`` is set so
        selection offsets can be translated by character index through
        :func:`_cursor_visible_column`. When ``mask`` is ``None``,
        ``line_text`` *is* the raw line and this parameter is ignored.
    """
    if selection is not None:
        sel_start, sel_end = selection
    else:
        sel_start = sel_end = -1

    if not cursor_in_line:
        # No cursor here — paint selection overlap by visible column.
        return _apply_selection_by_width(
            line_text,
            line_start=line_start,
            sel_start=sel_start,
            sel_end=sel_end,
            mask=mask,
            line_text_raw=line_text_raw if line_text_raw is not None else line_text,
        )

    local_cursor = cursor - line_start
    raw_for_cols = line_text_raw if line_text_raw is not None else line_text
    cursor_col = _cursor_visible_column(raw_for_cols, mask, local_cursor)

    # Selection visible-column boundaries within this line. Clamped to
    # the line's visible extent. ``None`` when there's no selection
    # overlap or no selection at all.
    sel_start_col: int | None = None
    sel_end_col: int | None = None
    if selection is not None:
        line_end_value = line_start + len(raw_for_cols)
        if sel_end > line_start and sel_start < line_end_value:
            local_sel_start = max(0, sel_start - line_start)
            local_sel_end = min(len(raw_for_cols), sel_end - line_start)
            sel_start_col = _cursor_visible_column(raw_for_cols, mask, local_sel_start)
            sel_end_col = _cursor_visible_column(raw_for_cols, mask, local_sel_end)

    from ink.layout.measure import _char_width

    out: list[str] = []
    width = 0
    # Selection state: are we currently inside the selection run?
    in_selection = False
    # Whether the cursor has been emitted.
    cursor_emitted = False

    def emit_selection_open() -> None:
        nonlocal in_selection
        if not in_selection:
            out.append(_SGR_INVERSE)
            in_selection = True

    def emit_selection_close() -> None:
        nonlocal in_selection
        if in_selection:
            out.append(_SGR_RESET)
            in_selection = False

    i = 0
    n = len(line_text)
    while i < n:
        ch = line_text[i]
        w = _char_width(ch)

        if cursor_style == "bar" and width == cursor_col and not cursor_emitted and cursor_visible:
            # Close any active selection so the bar's reset doesn't get
            # tangled with the selection's reset.
            emit_selection_close()
            out.append(_cursor_cell(cursor_style, char="", cursor_color=cursor_color))
            cursor_emitted = True

        # Check whether to open / close selection at this column.
        if sel_start_col is not None and width == sel_start_col:
            emit_selection_open()
        if sel_end_col is not None and width == sel_end_col:
            emit_selection_close()

        # If the block / underline cursor sits on this character, emit
        # the cursor cell (with selection *closed* around it so the two
        # don't double-invert).
        if (
            cursor_style in ("block", "underline")
            and width == cursor_col
            and not cursor_emitted
            and cursor_visible
        ):
            emit_selection_close()
            out.append(_cursor_cell(cursor_style, char=ch, cursor_color=cursor_color))
            cursor_emitted = True
            # Skip the underlying char (the cursor cell replaced it).
            i += 1
            if w > 0:
                width += w
            continue

        out.append(ch)
        if w > 0:
            width += w
        i += 1

    # End-of-line cursor position (cursor_col == total line width).
    if not cursor_emitted and width == cursor_col and cursor_visible:
        emit_selection_close()
        out.append(_cursor_cell(cursor_style, char="", cursor_color=cursor_color))
        cursor_emitted = True

    # Close any trailing selection.
    emit_selection_close()
    return "".join(out)


def _apply_selection_by_width(
    displayed: str,
    *,
    line_start: int,
    sel_start: int,
    sel_end: int,
    mask: str | None,
    line_text_raw: str,
) -> str:
    """Wrap the selection range of ``displayed`` in inverse video by visible width.

    Used on rows where the cursor is not present. Translates the
    selection's character offsets (relative to the line) into visible
    columns via :func:`_cursor_visible_column`, then walks the
    displayed string by display width inserting the SGR boundaries at
    those columns. Keeps CJK rows aligned with the cursor-bearing row
    (Bug 4).
    """
    if sel_start < 0:
        return displayed
    line_end_value = line_start + len(line_text_raw)
    if sel_end <= line_start or sel_start >= line_end_value:
        return displayed
    local_sel_start = max(0, sel_start - line_start)
    local_sel_end = min(len(line_text_raw), sel_end - line_start)
    start_col = _cursor_visible_column(line_text_raw, mask, local_sel_start)
    end_col = _cursor_visible_column(line_text_raw, mask, local_sel_end)
    if start_col >= end_col:
        return displayed

    from ink.layout.measure import _char_width

    out: list[str] = []
    width = 0
    in_selection = False
    for ch in displayed:
        w = _char_width(ch)
        if w > 0:
            if not in_selection and width >= start_col and width < end_col:
                out.append(_SGR_INVERSE)
                in_selection = True
            elif in_selection and width >= end_col:
                out.append(_SGR_RESET)
                in_selection = False
        out.append(ch)
        if w > 0:
            width += w
    if in_selection:
        out.append(_SGR_RESET)
    return "".join(out)


def _split_lines(value: str) -> list[tuple[int, str]]:
    """Split ``value`` into ``[(line_start_offset, line_text), ...]``.

    Trailing empty line: when ``value`` ends with ``\\n`` we emit a
    final ``("")]`` entry so the cursor can rest on the empty last
    line. When ``value`` is empty we emit a single ``[(0, "")]`` row.
    """
    if value == "":
        return [(0, "")]
    out: list[tuple[int, str]] = []
    offset = 0
    for piece in value.split("\n"):
        out.append((offset, piece))
        offset += len(piece) + 1  # +1 for the consumed newline.
    return out


def _cursor_visible_column(
    line_text_raw: str,
    mask: str | None,
    local_cursor: int,
) -> int:
    """Return the visible cell column of the cursor within one row.

    ``local_cursor`` is the cursor's character offset within the line
    (``cursor - line_start``). Masking collapses each character to a
    single-cell glyph, so the column equals ``local_cursor`` multiplied
    by the mask glyph's display width (Bug 4: a wide-char mask like a
    CJK glyph occupies 2 cells per source character). Without a mask
    the column is the cumulative :func:`string_width` of the characters
    before the cursor.
    """
    if mask:
        from ink.layout.measure import _char_width

        mask_w = _char_width(mask)
        # Masked chars are normally width 1 (``*`` / ``•``); wide masks
        # (CJK glyphs) scale the column proportionally.
        return local_cursor * max(1, mask_w)
    return string_width(line_text_raw[:local_cursor])


# ---------------------------------------------------------------------------
# Function component
# ---------------------------------------------------------------------------


def _TextInputImpl(**props: Any) -> Element:
    """Function component body — runs inside the reconciler render context.

    Owns the ``value`` / ``cursor`` / ``selection`` signals, registers
    the keyboard handler via :func:`use_input`, and returns a :func:`Box`
    containing one :func:`Text` per rendered line. Each :func:`Text`
    child is a callable that re-evaluates on every signal write.
    """
    initial_value: str = props["initial_value"]
    placeholder: str | None = props["placeholder"]
    on_change: Callable[[str], None] | None = props["on_change"]
    on_submit: Callable[[str], None] | None = props["on_submit"]
    on_cursor_change: Callable[[int], None] | None = props["on_cursor_change"]
    mask: str | None = props["mask"]
    max_length: int | None = props["max_length"]
    color: str | None = props["color"]
    cursor_color: str | None = props["cursor_color"]
    cursor_style: CursorStyle = props["cursor_style"]
    # ``is_active`` may be a ``bool``, a ``Signal[bool]``, or a 0-arg
    # callable returning ``bool``. We re-evaluate it on every keypress
    # inside :func:`handle_key` so the caller can swap focus dynamically
    # (e.g. ``is_active=lambda: handle.is_focused.value``) without
    # re-mounting the component. ``use_input`` is always subscribed
    # (``is_active=True``) and ``handle_key`` filters out inactive keys.
    is_active: object = props["is_active"]
    multiline: bool = props["multiline"]
    rows: int | None = props["rows"]
    box_props: dict[str, Any] = props["box_props"]
    # ``ignore_arrows_when`` may be a ``bool``, a ``Signal[bool]``, or a
    # 0-arg callable returning ``bool``. When truthy the handler skips
    # ArrowUp / ArrowDown so an external consumer (e.g. a command palette
    # navigated by ↑↓) can own those keys without the TextInput moving
    # its cursor. Left / right / Home / End and editing keys are
    # unaffected. Re-evaluated per keypress (same shape as ``is_active``).
    ignore_arrows_when: object = props["ignore_arrows_when"]
    # Optional external ``value`` signal — when supplied, the component
    # writes its edits into this signal instead of an internal one, so
    # the caller can clear the buffer after a submit (or otherwise
    # mutate it programmatically). When ``None`` (default), the
    # component owns its own internal signal — same as before.
    value_signal: Signal[str] | None = props.get("value_signal")
    # Optional external ``cursor`` signal — symmetric to ``value_signal``.
    # Lets the caller move the caret programmatically (e.g. after writing
    # a Tab-completion into ``value_signal`` the caller also pushes the
    # cursor to the end). When ``None`` (default) the component owns an
    # internal signal seeded at ``len(initial_value)``.
    cursor_signal: Signal[int] | None = props.get("cursor_signal")
    # ``cursor_visible`` may be a ``bool``, a ``Signal[bool]``, or a
    # 0-arg callable returning ``bool``. Re-evaluated inside
    # :func:`_render_lines` (which runs in the render-loop effect's
    # tracking context) so Signal / callable reads establish
    # subscriptions. When false the cursor SGR cell is suppressed on
    # every line and the placeholder branch falls back to plain dim
    # text — visual signal that the input is inactive without disabling
    # :func:`handle_key` (use ``is_active`` for that).
    cursor_visible: object = props["cursor_visible"]

    # Initial cursor sits at end of the initial value — matches
    # ink-text-input and the common "open input, type to append" UX.
    # When the caller supplied an external ``value`` signal we adopt it
    # as-is (initial_value is ignored for the buffer, but the cursor
    # still seeds at ``len(initial_value)`` for backward compatibility).
    if value_signal is not None:
        value: Signal[str] = value_signal
    else:
        value = signal(initial_value)
    if cursor_signal is not None:
        cursor: Signal[int] = cursor_signal
    else:
        cursor = signal(len(initial_value))
    selection: Signal[tuple[int, int] | None] = signal(None)

    # Keep the latest callback without resubscribing the input handler.
    # ``use_input``'s closure captures the ref objects, so updating
    # ``.value`` here is enough to redirect future keypresses.
    on_change_ref: Ref[Callable[[str], None] | None] = ref(on_change)
    on_submit_ref: Ref[Callable[[str], None] | None] = ref(on_submit)
    on_cursor_change_ref: Ref[Callable[[int], None] | None] = ref(on_cursor_change)
    on_change_ref.value = on_change
    on_submit_ref.value = on_submit
    on_cursor_change_ref.value = on_cursor_change

    def _notify_change(old: str, new: str) -> None:
        if new != old:
            cb = on_change_ref.value
            if cb is not None:
                cb(new)

    def _clear_selection() -> None:
        if selection.value is not None:
            selection.value = None

    def _set_cursor(pos: int, *, keep_selection: bool = False) -> None:
        clamped = max(0, min(pos, len(value.value)))
        if not keep_selection:
            _clear_selection()
        cursor.value = clamped

    def _extend_selection(target: int) -> None:
        """Move the cursor to ``target`` and grow the selection.

        On the first Shift+motion the selection is anchored at the
        current cursor; subsequent Shift+motions keep the same anchor
        and slide the cursor. ``target`` is clamped to ``[0, len(value)]``.

        The anchor is the *opposite* end of the existing selection from
        the current cursor — when the cursor sits at ``sel[0]`` the
        anchor is ``sel[1]``, and vice versa. This keeps Shift+Left /
        Shift+Right symmetric: extending right past the original anchor
        then back leftward properly collapses the range.
        """
        old_cursor = cursor.value
        cur_sel = selection.value
        if cur_sel is not None:
            sel_start, sel_end = cur_sel
            # The anchor is whichever end of the selection the cursor is
            # currently *not* sitting on.
            if old_cursor == sel_start:
                anchor = sel_end
            elif old_cursor == sel_end:
                anchor = sel_start
            else:
                # Cursor drifted outside the selection (shouldn't happen
                # in normal use); fall back to the nearest end.
                anchor = sel_end if abs(old_cursor - sel_end) < abs(
                    old_cursor - sel_start
                ) else sel_start
        else:
            anchor = old_cursor
        new_cursor = max(0, min(target, len(value.value)))
        cursor.value = new_cursor
        if new_cursor == anchor:
            selection.value = None
        else:
            lo = min(anchor, new_cursor)
            hi = max(anchor, new_cursor)
            selection.value = (lo, hi)

    def _splice(
        text: str,
        *,
        start: int,
        end: int,
        new_cursor: int,
    ) -> bool:
        """Replace ``value[start:end]`` with ``text``.

        Honours ``max_length``: returns ``False`` (no edit) if the
        resulting length would exceed the cap. On success writes the
        new value, advances the cursor, clears the selection, fires
        ``on_change`` once, and returns ``True``.
        """
        old_value = value.value
        new_value = old_value[:start] + text + old_value[end:]
        if max_length is not None and len(new_value) > max_length:
            return False
        value.value = new_value
        clamped = max(0, min(new_cursor, len(new_value)))
        cursor.value = clamped
        _clear_selection()
        _notify_change(old_value, new_value)
        return True

    def _delete_selection_or_deselect() -> bool:
        """If a selection is active, delete it and return ``True``.

        Returns ``False`` when there is no active selection so the
        caller can fall through to character-based behaviour.
        """
        sel = selection.value
        if sel is None:
            return False
        start, end = sel
        _splice("", start=start, end=end, new_cursor=start)
        return True

    def handle_key(key: Key) -> None:
        if not _resolve_is_active(is_active):
            return

        old_value = value.value
        old_cursor = cursor.value

        # ----------------------------------------------------------------
        # Paste — single multi-char edit.
        # ----------------------------------------------------------------
        if key.paste:
            payload = key.paste
            if multiline:
                # Multi-line paste: keep newlines verbatim.
                insert_text = payload
            else:
                # Single-line paste: strip newlines so a pasted paragraph
                # collapses to one row (matches ink-text-input).
                insert_text = payload.replace("\r\n", " ").replace(
                    "\n", " "
                ).replace("\r", " ")
            sel = selection.value
            if sel is not None:
                start, end = sel
            else:
                start = end = old_cursor
            _splice(
                insert_text,
                start=start,
                end=end,
                new_cursor=start + len(insert_text),
            )
            return

        # ----------------------------------------------------------------
        # Selection-aware editing keys.
        # ----------------------------------------------------------------
        # Any non-Shift editing key (typing, Backspace, Delete, Ctrl+K/U/W,
        # paste, etc.) collapses the selection. ``_splice`` /
        # ``_delete_selection_or_deselect`` clear it; for plain
        # navigation we go through ``_set_cursor``.

        # Printable single character (no Ctrl / Alt).
        if (
            key.input
            and not key.ctrl
            and not key.alt
            and not key.up_arrow
            and not key.down_arrow
            and not key.left_arrow
            and not key.right_arrow
            and not key.return_key
            and not key.tab
            and not key.backspace
            and not key.delete
            and not key.escape
            and not key.home
            and not key.end
            and not key.paste_start
            and not key.paste_end
            and not key.paste
            and key.input.isprintable()
        ):
            sel = selection.value
            if sel is not None:
                start, end = sel
            else:
                start = end = old_cursor
            _splice(key.input, start=start, end=end, new_cursor=start + 1)
            return

        # Backspace — Alt+Backspace falls through to Ctrl+W; plain /
        # Ctrl+Backspace deletes one char or selection.
        if key.backspace:
            if key.alt and not key.ctrl:
                # Alt+Backspace == Ctrl+W (PRD PR2 requirement).
                _do_backward_kill_word(old_value, old_cursor)
                return
            if _delete_selection_or_deselect():
                return
            if key.ctrl:
                # Ctrl+Backspace — backward-kill-word, same as Ctrl+W.
                _do_backward_kill_word(old_value, old_cursor)
                return
            if old_cursor > 0:
                _splice(
                    "",
                    start=old_cursor - 1,
                    end=old_cursor,
                    new_cursor=old_cursor - 1,
                )
            return

        if key.delete and not key.ctrl and not key.alt:
            if _delete_selection_or_deselect():
                return
            if old_cursor < len(old_value):
                _splice(
                    "",
                    start=old_cursor,
                    end=old_cursor + 1,
                    new_cursor=old_cursor,
                )
            return

        # ----------------------------------------------------------------
        # ``ignore_arrows_when`` lets an external consumer (e.g. a
        # command palette) own ArrowUp / ArrowDown without the
        # TextInput moving its cursor. We evaluate it once per
        # keypress and short-circuit only the up / down handlers below
        # — left / right / Home / End and editing keys still work so
        # the user can keep typing the palette query. Shift+Up/Down
        # selection is also skipped so the selection anchor doesn't
        # drift while the palette owns vertical navigation.
        ignore_arrows = _resolve_is_active(ignore_arrows_when)

        # Selection-extending navigation (Shift + arrows / Home / End).
        # ----------------------------------------------------------------
        if key.shift and not key.ctrl and not key.alt:
            if key.left_arrow:
                _extend_selection(old_cursor - 1)
                return
            if key.right_arrow:
                _extend_selection(old_cursor + 1)
                return
            if key.up_arrow and not ignore_arrows:
                target = _offset_above(old_value, old_cursor)
                if target is None:
                    target = _line_start(old_value, old_cursor)
                _extend_selection(target)
                return
            if key.down_arrow and not ignore_arrows:
                target = _offset_below(old_value, old_cursor)
                if target is None:
                    target = _line_end(old_value, old_cursor)
                _extend_selection(target)
                return
            if key.home:
                _extend_selection(_line_start(old_value, old_cursor))
                return
            if key.end:
                _extend_selection(_line_end(old_value, old_cursor))
                return

        # Ctrl+Shift+Left/Right — extend selection by one word. Optional
        # but cheap to wire up since we already have the helpers.
        if key.shift and key.ctrl and not key.alt:
            if key.left_arrow:
                _extend_selection(_word_boundary_left(old_value, old_cursor))
                return
            if key.right_arrow:
                _extend_selection(_word_boundary_right(old_value, old_cursor))
                return

        # ----------------------------------------------------------------
        # Plain cursor movement (no selection modification).
        # ----------------------------------------------------------------
        if key.left_arrow and not key.ctrl and not key.alt:
            _set_cursor(old_cursor - 1)
            return

        if key.right_arrow and not key.ctrl and not key.alt:
            _set_cursor(old_cursor + 1)
            return

        if key.up_arrow and not key.ctrl and not key.alt and not ignore_arrows:
            target = _offset_above(old_value, old_cursor)
            if target is None:
                _set_cursor(_line_start(old_value, old_cursor))
            else:
                _set_cursor(target)
            return

        if key.down_arrow and not key.ctrl and not key.alt and not ignore_arrows:
            target = _offset_below(old_value, old_cursor)
            if target is None:
                _set_cursor(_line_end(old_value, old_cursor))
            else:
                _set_cursor(target)
            return

        if key.home and not key.ctrl and not key.alt:
            _set_cursor(_line_start(old_value, old_cursor))
            return

        if key.end and not key.ctrl and not key.alt:
            _set_cursor(_line_end(old_value, old_cursor))
            return

        # ----------------------------------------------------------------
        # Ctrl+letter shortcuts — Emacs-style line / word editing.
        # ----------------------------------------------------------------
        if key.ctrl and not key.alt and key.input:
            letter = key.input.lower()
            if letter == "a":
                _set_cursor(0)
                return
            if letter == "e":
                _set_cursor(len(old_value))
                return
            if letter == "k":
                # Kill to end of line — drop everything past the cursor
                # up to (and excluding) the next newline.
                line_end = _line_end(old_value, old_cursor)
                if old_cursor < line_end:
                    _splice(
                        "",
                        start=old_cursor,
                        end=line_end,
                        new_cursor=old_cursor,
                    )
                return
            if letter == "u":
                # Kill to start of line — drop everything from the line
                # start up to the cursor.
                line_start = _line_start(old_value, old_cursor)
                if old_cursor > line_start:
                    _splice(
                        "",
                        start=line_start,
                        end=old_cursor,
                        new_cursor=line_start,
                    )
                return
            if letter == "w":
                _do_backward_kill_word(old_value, old_cursor)
                return

        # ----------------------------------------------------------------
        # Enter — newline (multiline) or submit (single-line).
        # ----------------------------------------------------------------
        if key.return_key and not key.alt:
            if multiline:
                sel = selection.value
                if sel is not None:
                    start, end = sel
                else:
                    start = end = old_cursor
                _splice(
                    "\n",
                    start=start,
                    end=end,
                    new_cursor=start + 1,
                )
                return
            cb = on_submit_ref.value
            if cb is not None:
                cb(value.value)
            return

        # All other keys (Esc, Tab, Alt+X, function keys, …) are
        # intentionally left for the surrounding pipeline to handle.

    def _do_backward_kill_word(value_str: str, cur: int) -> None:
        """Shared Ctrl+W / Alt+Backspace body — delete the previous word.

        If a selection is active it is replaced wholesale; otherwise the
        whitespace + word run to the left of the cursor is removed.
        Mirrors readline's ``backward-kill-word``.
        """
        sel = selection.value
        if sel is not None:
            start, end = sel
            _splice("", start=start, end=end, new_cursor=start)
            return
        if cur <= 0:
            return
        new_cursor = cur
        # Skip whitespace to the left.
        while new_cursor > 0 and value_str[new_cursor - 1].isspace():
            new_cursor -= 1
        # Skip the word to the left.
        while new_cursor > 0 and _is_word_char(value_str[new_cursor - 1]):
            new_cursor -= 1
        if new_cursor != cur:
            _splice(
                "",
                start=new_cursor,
                end=cur,
                new_cursor=new_cursor,
            )

    # Always subscribe the input handler; ``handle_key`` re-evaluates
    # ``is_active`` per keypress so the caller can flip it via a Signal
    # or callable without unmount/remount. ``use_input``'s own
    # ``is_active`` is kept ``True`` for backward compatibility (the
    # hook only supports a mount-time ``bool``).
    use_input(handle_key, is_active=True)

    # Reactive side-effect: fire ``on_cursor_change`` whenever the
    # internal ``cursor`` signal moves. Subscribing via ``effect`` means
    # we automatically pick up every code path that writes the cursor —
    # keyboard navigation, text edits, selection-extension motions, and
    # programmatic writes from outside — without having to thread a
    # notify call through every ``_set_cursor`` / ``_splice`` helper.
    # ``effect`` also fires once on mount with the initial offset; that
    # mirrors ``on_change``'s "fire on first write" behaviour and lets
    # callers use the callback to seed their own cursor mirror.
    def _notify_cursor_change() -> None:
        cb = on_cursor_change_ref.value
        if cb is not None:
            cb(cursor.value)

    effect(_notify_cursor_change)

    # Vertical scroll window — the line offset the layout's text painter
    # should start displaying at. Phase 5 replaces the historical
    # ``_ink_scroll`` mutable-mapping side-channel with a public
    # ``scroll_offset`` Signal: writing it from inside ``_render_lines``
    # is read by the layout at paint time (the layout engine resolves
    # Signal / callable decoration props at layout time, so the
    # subscription is established automatically). When ``rows`` is set
    # and the buffer has more rendered lines than the viewport height,
    # we slide the window so the cursor line stays visible — this is
    # what keeps the last line the user typed on-screen instead of
    # letting the layout's top-keeping clip drop it past the viewport.
    scroll_offset_sig: Signal[int] = signal(0)

    # IME caret anchor (08-20-ime-caret-anchor-…): payload-relative
    # ``(display_row, visible_col)`` of the software caret, or ``None``
    # when there is no *reportable* caret — gated on
    # ``is_active AND cursor_visible`` (the anchor must follow the
    # keystroke target; see the module docstring). Written from inside
    # ``_render_lines`` (same channel pattern as ``scroll_offset_sig``)
    # and read by the layout painter via the ``caret`` Text decoration
    # prop, which translates it to absolute frame coordinates so the
    # renderer can park the physical cursor on the software caret —
    # that's what makes the terminal's IME composition window anchor
    # inside the input row instead of at the frame's bottom.
    caret_sig: Signal[tuple[int, int] | None] = signal(None)

    def _render_lines() -> list[str]:
        """Build a list of per-display-row rendered strings (cursor + selection).

        Every logical line of the buffer wraps into as many display
        rows as the available width requires (ink.js parity — the input
        grows vertically; no ellipsis truncation). The list always
        covers **every** display row — vertical scrolling is delegated
        to the layout's text painter via the public ``scroll_offset``
        signal (Phase 5). The painter slices ``[scroll_offset,
        scroll_offset + height)`` from the joined payload, so the
        cursor stays visible even when the surrounding box is shrunk
        below the buffer's natural row count.
        """
        cur_value = value.value
        cur_cursor = cursor.value
        cur_sel = selection.value
        cur_cursor_visible = _resolve_is_active(cursor_visible)
        # Unlike ``handle_key`` (which resolves ``is_active`` WITHOUT
        # subscribing, so keystroke dispatch stays imperative), here the
        # resolution happens inside the render effect's tracking
        # context on purpose: ``Signal`` / ``Callable`` shapes establish
        # a subscription, so a focus change (Tab between inputs) re-runs
        # ``_render_lines``, updates ``caret_sig``, and the IME anchor
        # moves to the newly focused input.
        cur_active = _resolve_is_active(is_active)

        # Empty buffer with placeholder: render the cursor at column 0
        # (on the placeholder's first character for block/underline, or
        # before the placeholder for bar) and the rest of the placeholder
        # as dim text. Routing this through the same ``_cursor_cell``
        # helper used by the editing paths keeps CJK / wide-char cursor
        # cells aligned with their normal-editing appearance.
        if not cur_value and placeholder:
            scroll_offset_sig.value = 0
            if not cur_cursor_visible or not cur_active:
                caret_sig.value = None
                return [apply_style(placeholder, dimColor=True)]
            # The placeholder caret sits at column 0 whether it is a
            # bar in its own cell or a block/underline on the first
            # character — both anchor IME preedit at the same cell.
            caret_sig.value = (0, 0)
            if cursor_style == "bar":
                # Bar cursor sits in its own 1-cell slot at column 0;
                # placeholder trails it in dim.
                cursor_cell = _cursor_cell(
                    cursor_style, char="", cursor_color=cursor_color
                )
                return [cursor_cell + apply_style(placeholder, dimColor=True)]
            # block / underline: cursor lives *on* the first character
            # of the placeholder (matches ink's TypeScript behaviour).
            # The outer ``and placeholder`` guard promises a non-empty
            # string here, so ``placeholder[0]`` is always safe.
            first = placeholder[0]
            rest = placeholder[1:]
            cursor_cell = _cursor_cell(
                cursor_style, char=first, cursor_color=cursor_color
            )
            tail = apply_style(rest, dimColor=True) if rest else ""
            return [cursor_cell + tail]

        # Width budget for wrapping. Each logical line wraps into as
        # many display rows as the available width requires (ink.js
        # parity: the input grows vertically instead of truncating).
        # Wrapping happens on the RAW text first; cursor / selection
        # SGR is spliced per row afterwards using the row's source
        # span — wrapping the styled string instead could split an
        # SGR run from the cell it styles at a row boundary.
        avail_width = get_current_text_width()

        lines: list[str] = []
        # Display-row index of the row carrying the cursor (wrapping can
        # spread one logical line over several rows — the viewport must
        # follow the *display* row, not the logical line number).
        cursor_row_idx: int | None = None
        for line_start, line_text_raw in _split_lines(cur_value):
            line_text = mask * len(line_text_raw) if mask else line_text_raw
            line_end = line_start + len(line_text_raw)
            cursor_in_line = line_start <= cur_cursor <= line_end
            row_spans: list[tuple[str, int, int]]
            if avail_width is None or avail_width < 1:
                row_spans = [(line_text, 0, len(line_text))]
            else:
                # Reserve one cell on the cursor's logical line: the
                # bar cursor inserts its own cell and a block /
                # underline cursor at end-of-row renders an extra
                # inverse space, so wrapping at the full width could
                # push the spliced row past the budget.
                budget = max(
                    1,
                    avail_width - (1 if cursor_in_line and cur_cursor_visible else 0),
                )
                if string_width(line_text) <= budget:
                    row_spans = [(line_text, 0, len(line_text))]
                else:
                    row_spans = wrap_text_rows(line_text, budget)
            for row_text, row_off, row_end in row_spans:
                row_abs_start = line_start + row_off
                row_abs_end = line_start + row_end
                # Exactly one row owns the cursor: the row whose span
                # contains the offset (the last row also owns the
                # end-of-line position). An offset inside a wrap's
                # dropped-space tail renders as end-of-row.
                cursor_in_row = row_abs_start <= cur_cursor < row_abs_end or (
                    row_abs_end == line_end and cur_cursor == line_end
                )
                rendered = _build_displayed_line(
                    row_text,
                    cursor_in_line=cursor_in_row,
                    cursor=cur_cursor,
                    line_start=row_abs_start,
                    cursor_style=cursor_style,
                    cursor_color=cursor_color,
                    selection=cur_sel,
                    mask=mask,
                    line_text_raw=line_text_raw[row_off:row_end] if mask else row_text,
                    cursor_visible=cur_cursor_visible,
                )
                if cursor_in_row and cursor_row_idx is None:
                    cursor_row_idx = len(lines)
                    if cur_cursor_visible and cur_active:
                        # Visible column of the caret within this
                        # display row — same helper (and same raw /
                        # mask inputs) ``_build_displayed_line`` uses
                        # to place the SGR cursor cell, so the physical
                        # cursor parks on exactly the cell the software
                        # cursor occupies.
                        local = cur_cursor - row_abs_start
                        raw_row = (
                            line_text_raw[row_off:row_end] if mask else row_text
                        )
                        caret_sig.value = (
                            cursor_row_idx,
                            _cursor_visible_column(raw_row, mask, local),
                        )
                lines.append(rendered)

        # Vertical viewport (multi-line scroll-to-cursor). When the
        # requested viewport height (``rows`` prop, falling back to a
        # ``height`` passed via ``box_props``) is set and smaller than
        # the buffer's rendered line count, slide a viewport-tall
        # window so the cursor line stays visible. Phase 5 routes this
        # through the public ``scroll_offset`` signal (read by the
        # layout's text painter) instead of the historical
        # ``_ink_scroll`` mutable-mapping side-channel — the painter
        # slices ``[scroll_offset, scroll_offset + height)`` from the
        # full joined payload, so:
        #
        # * When the layout grants ``height >= viewport``, the window
        #   contains exactly the viewport lines around the cursor.
        # * When the layout grants ``height < viewport`` (a tight
        #   column squeezes the box below the requested viewport), the
        #   painter re-slices within our window, still keeping the
        #   cursor row on screen (regression: cursor on the last line
        #   vanished because the top-keeping clip dropped the bottom
        #   rows).
        #
        # ``viewport_h`` is the *intended* viewport height — the layout
        # may ultimately grant less, but we size the scroll window to
        # the intent so the painter's secondary slice still contains
        # the cursor. ``rows`` wins over ``box_props.height`` when both
        # are set (rows is the dedicated TextInput viewport knob).
        viewport_h: int | None
        if rows is not None and rows >= 1:
            viewport_h = rows
        else:
            raw_h = box_props.get("height")
            viewport_h = raw_h if isinstance(raw_h, int) and not isinstance(raw_h, bool) else None
        cur_line = cursor_row_idx if cursor_row_idx is not None else 0
        if viewport_h is not None and viewport_h >= 1 and len(lines) > viewport_h:
            start = 0
            if cur_line >= viewport_h:
                start = cur_line - viewport_h + 1
            start = max(0, min(start, len(lines) - viewport_h))
            scroll_offset_sig.value = start
        else:
            scroll_offset_sig.value = 0
        # No visible caret this frame (cursor_visible=False, the input
        # is not active — i.e. not the keystroke target — or the buffer
        # produced no cursor row) — clear the anchor so the renderer
        # keeps the physical cursor bottom-parked.
        if cursor_row_idx is None or not cur_cursor_visible or not cur_active:
            caret_sig.value = None
        return lines

    def render_text() -> str:
        # Build the full rendered buffer (with cursor + selection overlays
        # already applied per display row) and join rows with ``\n``. The
        # single Text child carries the multi-line payload; layout's wrap
        # engine (``truncate-end``) preserves embedded newlines and
        # measures each paragraph independently, so the Box grows to N
        # rows when the buffer renders to N display rows.
        #
        # Why one Text instead of one Text per line: PyInk function
        # components run *exactly once* at mount (PRD Decision 1). The
        # children list passed to :func:`Box` is captured at that single
        # mount, so a per-line scheme would freeze the row count at the
        # initial value and never reflect lines the user adds by pressing
        # Enter later. A single Text whose callable re-evaluates the
        # current buffer on every signal write sidesteps the freeze
        # entirely — the layout engine sees the up-to-date ``\n``-joined
        # string on every paint.
        return "\n".join(_render_lines())

    # ``wrap="truncate-end"`` is now a SAFETY NET only: `_render_lines`
    # pre-wraps every logical line to the granted width (with a
    # cursor-cell reserve), so rows arrive already within budget and
    # nothing is ellipsized in normal operation. It still guards the
    # placeholder path (a long placeholder clips to one row) and any
    # width the pre-wrap could not observe (unbounded first layout).
    #
    # Phase 5 scroll routing: the public ``scroll_offset`` signal drives
    # the layout's text painter to slice ``[offset, offset + height)``
    # from the joined buffer, keeping the cursor row on screen as the
    # user types past the viewport. ``rows`` (when set) caps the
    # surrounding Box's *max* height so a multi-line input grows from
    # one row up to ``rows`` rows and then scrolls to follow the
    # cursor — the caller-provided ``box_props.height`` (or
    # ``box_props.maxHeight``) wins over ``rows`` if set (the caller
    # opted into a tighter bound).
    resolved_box_props: dict[str, Any] = dict(box_props)
    if (
        rows is not None
        and rows >= 1
        and "height" not in resolved_box_props
        and "maxHeight" not in resolved_box_props
    ):
        resolved_box_props["maxHeight"] = rows
    return Box(
        Text(
            render_text,
            color=color,
            wrap="truncate-end",
            scroll_offset=scroll_offset_sig,
            caret=caret_sig,
        ),
        **resolved_box_props,
    )


def TextInput(
    *,
    initial_value: str = "",
    placeholder: str | None = None,
    on_change: Callable[[str], None] | None = None,
    on_submit: Callable[[str], None] | None = None,
    on_cursor_change: Callable[[int], None] | None = None,
    multiline: bool = False,
    rows: int | None = None,
    mask: str | None = None,
    max_length: int | None = None,
    color: str | None = None,
    cursor_color: str | None = None,
    cursor_style: CursorStyle = "block",
    is_active: bool | Signal[bool] | Callable[[], bool] = True,
    ignore_arrows_when: bool | Signal[bool] | Callable[[], bool] = False,
    cursor_visible: bool | Signal[bool] | Callable[[], bool] = True,
    value_signal: Signal[str] | None = None,
    cursor_signal: Signal[int] | None = None,
    **box_props: Any,
) -> Element:
    """Create a single-line or multi-line text input.

    Parameters
    ----------
    initial_value:
        Initial string placed in the buffer on mount. The cursor is
        positioned at the end of this string so the user can append by
        typing. May contain newlines when ``multiline=True``.
    placeholder:
        Dimmed hint shown when the buffer is empty. ``None`` (default)
        renders an empty cell.
    on_change:
        Called with the new buffer value whenever the user edits it
        (typing, deleting, Ctrl+K/U/W, paste). Not invoked for
        cursor-only moves (arrows / Home / End / Ctrl+A/E).
    on_submit:
        Called with the current buffer value when the user presses
        Enter *in single-line mode* (``multiline=False``). In
        ``multiline=True`` Enter inserts a newline instead.
        ``None`` (default) disables submit.
    on_cursor_change:
        Called with the absolute cursor offset (a ``int`` in
        ``[0, len(value)]``) whenever the cursor moves. Fires for
        keyboard navigation (arrows / Home / End / Ctrl+A/E), text
        edits that advance the cursor (typing, Backspace, Delete,
        paste, Ctrl+K/U/W), selection-extension motions, and
        programmatic writes to the internal ``cursor`` signal. Also
        fires once on mount with the initial offset (end of
        ``initial_value``), mirroring ``on_change``'s first-write
        semantics — this lets the caller seed a cursor mirror without
        a separate initialisation code path. ``None`` (default)
        disables the callback.
    multiline:
        When ``True``, Enter inserts a ``\\n`` (multi-line editing);
        ArrowUp / ArrowDown move across lines. When ``False`` (default),
        Enter triggers :attr:`on_submit` and the input stays single-line.
    rows:
        Maximum number of text rows the (multi-line) input shows at
        once — its scroll viewport height. ``None`` (default) lets the
        input grow without bound, matching the historic behaviour. When
        set, the input grows from one row up to ``rows`` rows and then
        *scrolls* to follow the cursor: the visible window always
        contains the cursor line, so the last line you type stays in
        view instead of being clipped off the bottom by the surrounding
        layout's height budget. Bounding the height this way also keeps
        a tall buffer from squeezing sibling elements. Ignored for
        single-line inputs (which are always one row).
    mask:
        When set, every visible character is replaced by this string
        (password mode). E.g. ``mask="*"`` renders ``"secret"`` as
        ``"******"``. The buffer itself is unaffected — only the
        rendered output is masked.
    max_length:
        Hard ceiling on the buffer length. Keystrokes (and pastes)
        that would exceed it are silently dropped.
    color:
        Colour spec for the rendered text (``"red"``, ``"#ff0000"``,
        ``"rgb(255,0,0)"``, ``"ansi256(9)"``). Forwarded to
        :func:`Text`.
    cursor_color:
        Colour spec applied to the cursor cell. Independent of
        ``color``.
    cursor_style:
        Cursor visual: ``"block"`` (default — inverse video *over* the
        cursor character so the character "lights up", Claude Code /
        classic terminal feel), ``"bar"`` (thin inverse bar between
        characters, VSCode / vim insert feel), or ``"underline"``
        (underline under the cursor character).
    is_active:
        Controls whether :func:`handle_key` processes the keystroke.
        Evaluated on **every keypress** (not at mount time). Accepts
        three shapes:

        * ``bool`` — fixed at mount time. ``False`` makes the input
          ignore every keystroke (handy for disabling an input
          permanently or based on a one-shot condition known at mount).
        * ``Signal[bool]`` — re-evaluated on every keypress via
          ``.value`` (without subscribing, so the input handler closure
          is not tied to the signal's reactivity).
        * ``Callable[[], bool]`` — re-evaluated on every keypress. The
          canonical use case is wiring focus into the component:
          ``is_active=lambda: handle.is_focused.value`` so only the
          focused input among several receives keystrokes.

        ``Signal`` / ``Callable`` changes do **not** trigger handler
        re-registration — only **subsequent** keystrokes are affected.
        The underlying ``use_input`` subscription is always live
        (``is_active=True``); ``handle_key`` filters out keystrokes
        whenever the resolved value is false. This keeps the dispatch
        overhead flat regardless of which shape the caller picked.

        For visual feedback on focus changes (e.g. dimming an inactive
        input), use reactive props such as
        ``color=lambda: "gray" if not focused else None`` rather than
        relying on ``is_active`` to re-render the component.
    cursor_visible:
        Controls whether the in-text cursor cell (bar / block /
        underline) is rendered. Evaluated inside the render callable
        so Signal / Callable shapes establish subscriptions and the
        cursor re-appears / disappears reactively. Accepts the same
        three shapes as ``is_active``: ``bool``, ``Signal[bool]``,
        ``Callable[[], bool]``. When false the buffer / placeholder
        is rendered as plain text — use this to give a "disabled"
        visual signal without disabling keystroke handling (which
        is ``is_active``'s job). Defaults to ``True``.

        The IME caret anchor (where the terminal parks its physical
        cursor so the IME composition window follows) is reported only
        when ``is_active AND cursor_visible`` both resolve true: the
        anchor belongs to the input that is about to receive keystrokes
        (``is_active``) *and* shows a visible cursor. In a multi-input
        form this keeps the IME preedit anchored to the focused input
        instead of whichever input happens to paint last.
    ignore_arrows_when:
        Controls whether :func:`handle_key` skips ArrowUp / ArrowDown.
        Evaluated on **every keypress** (same shape dispatch as
        ``is_active`` — ``bool`` / ``Signal[bool]`` / ``Callable[[], bool]``).
        When truthy, vertical arrow navigation is yielded to an
        external consumer (the canonical use case is a command palette
        mounted alongside the input — the palette's own handler moves
        its focus while the input's cursor stays put). Left / right /
        Home / End and all editing keys are unaffected, so the user
        can keep typing the palette query. Defaults to ``False``
        (arrows always move the cursor).
    value_signal:
        Optional external :class:`Signal` ``[str]`` to use as the
        component's value buffer. When supplied, the component writes
        its edits into this signal instead of an internal one, so the
        caller can clear the buffer after a submit (or otherwise
        mutate it programmatically) by writing
        ``value_signal.value = ""``. When ``None`` (default), the
        component owns its own internal signal — same as before. The
        cursor and selection state remain internal regardless. The
        ``initial_value`` is honoured only when this is ``None``;
        when ``value_signal`` is provided the buffer starts at that
        signal's current value.
    **box_props:
        Forwarded to the wrapping :func:`Box` (``padding``,
        ``borderStyle``, ``width``, …).

    Returns
    -------
    Element
        An element whose ``type`` is the :func:`_TextInputImpl`
        function component. The factory itself never runs hooks —
        the reconciler mounts the function, which is what makes
        ``Box(TextInput(...), Text(...))`` safe to call from outside
        a render context.

    Selection (PR2)
    ---------------
    Shift + Left / Right / Up / Down / Home / End extend the selection
    from the current cursor. Ctrl+Shift+Left / Right extend by one
    word. Any non-Shift edit (typing, Backspace, Delete, paste,
    Ctrl+K/U/W) collapses the selection; Backspace / Delete on an
    active selection deletes the selection wholesale. The selected
    range is rendered with inverse video.

    Paste (PR2)
    -----------
    When the host terminal supports *bracketed paste mode* (DECSET
    2004, auto-enabled by :class:`ink.render.terminal.Terminal`
    while in raw mode), pasted text is delivered as a single
    :class:`Key` event with a ``paste`` payload. ``TextInput`` splices
    the payload into the buffer at the cursor as one edit, fires
    ``on_change`` once, and replaces any active selection. When the
    terminal does *not* support bracketed paste the payload arrives as
    individual character events — typing still works, just without the
    single-edit semantics.

    Usage
    -----
    ::

        Box(
            Text("Name: "),
            TextInput(
                placeholder="Enter your name...",
                on_submit=lambda v: print(f"Hello, {v}"),
                cursor_color="green",
            ),
        )

    Multi-line editor::

        TextInput(
            multiline=True,
            placeholder="Type here...",
            on_change=lambda v: ...,
        )
    """
    # ``mask`` must be a single-cell glyph so the displayed width
    # matches the buffer length; silently take the first character if
    # the caller passed something longer.
    if mask is not None and len(mask) != 1:
        mask = None if mask == "" else mask[0]

    if cursor_style not in ("bar", "block", "underline"):
        raise ValueError(
            f"cursor_style must be 'bar' | 'block' | 'underline', got {cursor_style!r}"
        )

    if max_length is not None and max_length < 0:
        raise ValueError(f"max_length must be >= 0, got {max_length}")

    if rows is not None and rows < 1:
        raise ValueError(f"rows must be >= 1, got {rows}")

    # Clip the initial value to ``max_length`` so the buffer never
    # starts out over-budget. Matches ink-text-input's behaviour.
    if max_length is not None and len(initial_value) > max_length:
        initial_value = initial_value[:max_length]

    return create_element(
        _TextInputImpl,
        initial_value=initial_value,
        placeholder=placeholder,
        on_change=on_change,
        on_submit=on_submit,
        on_cursor_change=on_cursor_change,
        mask=mask,
        max_length=max_length,
        color=color,
        cursor_color=cursor_color,
        cursor_style=cursor_style,
        is_active=is_active,
        ignore_arrows_when=ignore_arrows_when,
        cursor_visible=cursor_visible,
        multiline=multiline,
        rows=rows,
        box_props=box_props,
        value_signal=value_signal,
        cursor_signal=cursor_signal,
    )
