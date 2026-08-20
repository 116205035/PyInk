"""IME caret parking — physical cursor parks on the software caret.

08-20-ime-caret-anchor-park-physical-cursor-at-software-caret.

Terminal IME engines anchor the composition / candidate window on the
**physical** cursor's cell (visibility-independent; verified against
Windows Terminal's TSF chain). These tests pin the paint-sequence
contract introduced by the task:

* a frame with a caret declaration (focused ``TextInput``) ends its
  paint by moving the physical cursor to the caret cell (kept hidden);
* the NEXT paint undoes the park (returns to bottom-park) BEFORE the
  diff writes, so the diff engine's bottom-parked cursor math holds;
* frames without a caret (plain ``Text``) emit a byte stream identical
  to the pre-feature behaviour — no column-position (``CUF``/``CUP``
  column) escapes appear;
* ``cursor_visible=False`` suppresses the park;
* the anchor follows **focus** (``is_active``): only the input that
  would receive keystrokes reports a caret, so in a multi-input form
  the IME preview anchors to the focused input, not the one that
  paints last.
"""

from __future__ import annotations

import io
import re
import time
from collections.abc import Callable

from ink import Box, Text, render
from ink.core.signal import signal
from ink.externals.text_input import TextInput

#: Column-position escape (``CSI n G``) — emitted ONLY by the caret
#: park (the diff engine never uses absolute column addressing).
_COL_POS_RE = re.compile(r"\x1b\[[0-9]+G")


def _mount(
    tree: object,
    *,
    columns: int = 30,
    rows: int = 10,
) -> tuple[object, io.StringIO]:
    out = io.StringIO()
    inst = render(
        tree,  # type: ignore[arg-type]
        stdout=out,
        columns=columns,
        rows=rows,
        exit_on_ctrl_c=False,
    )
    return inst, out


def _wait_for(predicate: Callable[[], object], *, attempts: int = 100, delay: float = 0.02) -> bool:
    for _ in range(attempts):
        if predicate():
            return True
        time.sleep(delay)
    return bool(predicate())


def test_paint_parks_cursor_at_caret_cell_with_hint_below() -> None:
    """Input with a hint row below: park moves the cursor UP to the caret.

    Frame is 2 rows (input row 0, hint row 1); cursor sits after
    ``abc`` at visible column 3. After the paint settles, the physical
    cursor must be parked at (row 0, col 3): ``CUU 1`` + ``CR`` +
    column 4 (1-based), and the Instance must record the park so the
    next paint can undo it.
    """
    inst, out = _mount(
        Box(
            TextInput(initial_value="abc"),
            Text("hint row"),
            flexDirection="column",
        )
    )
    try:
        parked = _wait_for(lambda: getattr(inst, "_cursor_at_caret", None) is not None)
        assert parked, f"caret was never parked — output tail: {out.getvalue()[-80:]!r}"
        assert inst._cursor_at_caret == (0, 3, 1)  # type: ignore[attr-defined]
        assert out.getvalue().endswith("\x1b[1A\r\x1b[4G")
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_next_paint_returns_to_bottom_park_before_diff() -> None:
    """After a park, the next paint FIRST moves back to bottom-park.

    This is the core anti-drift assertion (Ink PR #866 constraint): the
    diff engine computes cursor moves assuming a bottom-parked start,
    so the return move (``CUD 1`` + ``CR``) must precede the diff bytes
    of the changed frame, and the frame must then end re-parked at the
    (moved) caret.
    """
    buf = signal("abc")
    inst, out = _mount(
        Box(
            TextInput(value_signal=buf, initial_value="abc"),
            Text("hint row"),
            flexDirection="column",
        )
    )
    try:
        parked = _wait_for(lambda: getattr(inst, "_cursor_at_caret", None) is not None)
        assert parked
        settled = out.getvalue()
        # Grow the buffer. The external cursor signal stays at offset 3
        # (block cursor lands ON the new 'd'), so the caret column is
        # unchanged at 3 — what matters here is the return-then-diff
        # ordering around the repaint.
        buf.value = "abcd"
        # The block cursor sits ON the new 'd', so the frame carries
        # ``abc`` + SGR + ``d`` — assert on the SGR-wrapped form.
        repainted = _wait_for(
            lambda: "abc\x1b[7md" in inst.current_frame  # type: ignore[attr-defined]
            and out.getvalue().endswith("\x1b[1A\r\x1b[4G")
            and len(out.getvalue()) > len(settled)
        )
        assert repainted, f"no re-park after edit — tail: {out.getvalue()[-80:]!r}"
        # The bytes appended after the settled park must START with the
        # return-to-bottom move (CUD 1 + CR) — the diff writes come
        # after it, never before.
        appended = out.getvalue()[len(settled):]
        assert appended.startswith("\x1b[1B\r"), (
            f"paint did not return to bottom-park before diffing: {appended[:40]!r}"
        )
        assert inst._cursor_at_caret == (0, 3, 1)  # type: ignore[attr-defined]
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_frame_without_caret_emits_no_park_bytes() -> None:
    """Plain Text frame: byte stream contains no column-position escapes.

    ``CSI n G`` is only ever emitted by the caret park, so its absence
    proves the no-caret frame is byte-identical to the pre-feature
    behaviour (regression guard).
    """
    txt = signal("hello")
    inst, out = _mount(Box(Text(lambda: txt.value), Text("hint row")))
    try:
        txt.value = "hello world"  # Force a second (diff) paint too.
        flushed = _wait_for(lambda: "hello world" in inst.current_frame)  # type: ignore[attr-defined]
        assert flushed
        assert _COL_POS_RE.search(out.getvalue()) is None
        assert getattr(inst, "_cursor_at_caret", None) is None
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_cursor_visible_false_suppresses_park() -> None:
    """``cursor_visible=False`` → no caret report, no park bytes."""
    inst, out = _mount(
        Box(
            TextInput(initial_value="abc", cursor_visible=False),
            Text("hint row"),
            flexDirection="column",
        )
    )
    try:
        time.sleep(0.1)
        assert getattr(inst, "_cursor_at_caret", None) is None
        assert _COL_POS_RE.search(out.getvalue()) is None
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_caret_disappearing_after_park_clears_stale_anchor() -> None:
    """Caret leaf vanishing mid-session must not park at a stale anchor.

    Regression guard: the painter only *writes* the anchor when a caret
    leaf paints — it never clears it. Without the per-render reset in
    ``_paint_now``, toggling ``cursor_visible`` off (or unmounting the
    input) would leave the physical cursor parked at the previous
    frame's ghost cell forever, breaking the PRD's "no caret → zero
    behaviour change" contract.
    """
    visible = signal(True)
    inst, out = _mount(
        Box(
            TextInput(initial_value="abc", cursor_visible=visible),
            Text("hint row"),
            flexDirection="column",
        )
    )
    try:
        parked = _wait_for(lambda: getattr(inst, "_cursor_at_caret", None) is not None)
        assert parked
        pre_toggle = len(out.getvalue())
        visible.value = False
        # The cursor row keeps its text but the SGR caret cell is gone —
        # the frame bytes change, so a diff paint runs, and after it the
        # park record must be dropped (anchor stale-cleared, no re-park).
        unparked = _wait_for(
            lambda: getattr(inst, "_cursor_at_caret", None) is None
            and len(out.getvalue()) > pre_toggle
        )
        assert unparked, f"stale park survived caret removal — tail: {out.getvalue()[-80:]!r}"
        assert inst._caret_anchor is None  # type: ignore[attr-defined]
        # No column-position escape past this point (the park is the
        # only emitter of ``CSI n G``).
        assert _COL_POS_RE.search(out.getvalue()[pre_toggle:]) is None
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_anchor_follows_focus_among_multiple_inputs() -> None:
    """Two inputs, one focused: the anchor parks at the focused caret.

    The anchor semantics must match the keystroke target (``is_active``):
    even with ``cursor_visible=True`` on both inputs, only the active
    one reports a caret. Toggling the shared focus signal re-paints and
    moves the park to the other input — proving the render-time
    ``is_active`` subscription is live (Tab-driven focus changes move
    the IME anchor immediately).
    """
    active = signal(True)

    def first_is_active() -> bool:
        return active.value

    def second_is_active() -> bool:
        return not active.value

    inst, out = _mount(
        Box(
            Box(TextInput(initial_value="abc", is_active=first_is_active)),
            Box(TextInput(initial_value="xyzw", is_active=second_is_active)),
            Text("hint row"),
            flexDirection="column",
        )
    )
    try:
        # First input is active: park at row 0 (first input's row),
        # column 3 (after ``abc``).
        parked = _wait_for(lambda: getattr(inst, "_cursor_at_caret", None) is not None)
        assert parked, f"caret was never parked — tail: {out.getvalue()[-80:]!r}"
        # ``(row, col, frame_height)`` — only the row / col matter here.
        assert inst._cursor_at_caret[:2] == (0, 3)  # type: ignore[attr-defined]

        # Swap focus (what Tab does). The anchor must follow to the
        # second input's row (row 1), column 4 (after ``xyzw``).
        active.value = False
        moved = _wait_for(
            lambda: getattr(inst, "_cursor_at_caret", None) is not None
            and inst._cursor_at_caret[:2] == (1, 4)  # type: ignore[attr-defined]
        )
        assert moved, (
            f"anchor did not follow focus — state: "
            f"{getattr(inst, '_cursor_at_caret', None)!r} "
            f"tail: {out.getvalue()[-80:]!r}"
        )
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_inactive_input_never_reports_caret_even_when_cursor_visible() -> None:
    """``is_active=False`` with ``cursor_visible=True`` → no park bytes.

    Regression guard for the demo bug: four inputs all defaulted to
    ``cursor_visible=True`` and every one reported a caret, so the
    painter's last-writer-wins rule pinned the IME anchor to the
    last-painted input regardless of focus.
    """
    inst, out = _mount(
        Box(
            TextInput(initial_value="abc", is_active=False),
            Text("hint row"),
            flexDirection="column",
        )
    )
    try:
        time.sleep(0.1)
        assert getattr(inst, "_cursor_at_caret", None) is None
        assert _COL_POS_RE.search(out.getvalue()) is None
    finally:
        inst.unmount()  # type: ignore[attr-defined]
