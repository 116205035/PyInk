# Changelog

All notable changes to PyInk are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed — StructuredDiff wrap continuation carries indent + bg gutter + sigil

Symptom: when a `+` / `-` row in `StructuredDiff` (full-width-bg mode)
wrapped to a second visual row, the continuation landed at column 0 —
losing the parent `⎿` indent, the line-number gutter column, AND the
`+` / `-` sigil. The wrapped tail was visually misaligned with both
the parent gutter above and the first row's body column. Surfaced in
Jarvis's TUI as Edit/Write diffs with long lines wrapping underneath
the `⎿` gutter to the leftmost column.

Cause: `_build_full_width_bg_row._render`'s wrap-aware continuation
branch emitted `<bg_open><chunk_body><pad><reset>` — no prefix, no
gutter, no sigil. The first visual row's prefix + gutter were
intentionally dropped on continuation under the assumption that the
parent `⎿` "stays on row 1", but that left the wrapped chunk
unanchored.

Fix: continuation rows now emit
`<indent><bg_open><cont_gutter><chunk_body><pad><reset>` where
`<indent>` is the 5-space continuation indent (the parent `⎿` glyph
from `first_row_prefix` is NOT extended to continuation — CC parity:
CC's `⎿` is a separate flex child that doesn't extend down) and
`<cont_gutter>` is `(gutter_w - 1)` bg-filled spaces + the sigil byte
(so the body chunk column-aligns with the first row's body). When
`gutter_w == 0` (no `line_numbers` prop), the gutter and sigil are
omitted — matches PyInk's existing no-gutter first-row semantics.

CC reference: `claude-code/src/components/StructuredDiff/Fallback.tsx:395-419`
renders `lineNumStr` (all spaces on continuation) + sigil on every
visual row, with the parent `⎿` rendered as a separate flex child in
`MessageResponse.tsx:21-37` that doesn't extend down. PyInk achieves
the same visual via in-band ANSI (single Text leaf per row) rather
than separate flex children.

Tests: `test_wrap_continuation_carries_prefix_indent_and_aligned_gutter`
covers the Jarvis scenario (`indent` + `first_row_prefix` + bg_color +
wrap). Existing `test_line_numbers_gutter_only_on_first_visual_row_when_wrapped`
updated to assert CC parity (continuation DOES carry bg-filled gutter
spaces + sigil). Spec: `spec/frontend/rendering-contracts.md` Section
3 byte-layout + Tests Required updated.

### Fixed — frame paint in force_repaint is per-line CUP too (Root P.2)

Symptom: after Root P.1, scrollback still gained duplicate history
(including the welcome banner) during resize storms. Root P.1 made the
static redraw per-line CUP but left the frame paint on
``_paint_initial``'s free-flowing newline walk: a frame whose actual
height exceeds ``visual_h_new`` (emoji statusbar wrap miscount —
cpr_debug.log shows visual_h flapping 4↔6 for the same content)
overran the viewport bottom, scrolled, and dumped the just-redrawn
static top rows into the scrollback.

Fix: the force_repaint frame paint now positions every frame line with
an absolute CUP + ``\x1b[2K``, advancing rows by estimated wrap height
and parking with a final ``\r``. The entire force_repaint byte stream
is now free of newlines: no emission can run off the bottom edge, so
scrolling during a resize repaint is structurally impossible end to
end. The regression test asserts per-line CUP for both static and
frame lines and that no bare ``\n`` appears in the repaint.

### Fixed — Root P static redraw is per-line CUP positioned (scroll-pollution regression)

Symptom: after Root P, repeated alternating resize left **duplicated
markdown table fragments at several widths plus stray blank holes in
mid-scrollback**. Cause: the static-tail redraw was emitted as a
free-flowing ``\n`` stream sized by ``_visual_height`` — but the
retained lines were rendered at an older (wider) width, so after a
shrink they re-wrap and the cumulative estimate error over a
full-viewport suffix (43+ rows) regularly exceeded the safety margin.
The over-tall emission overran the viewport bottom and SCROLLED,
dumping the redrawn duplicate content into the scrollback — once per
resize event, at each event's width.

Fix: the redraw now positions every static line with an absolute CUP
(``\x1b[{row};1H``) instead of newline separators, advancing the row
by each line's estimated wrap height. A mis-estimated line lands on a
slightly wrong row inside the viewport (self-heals on the next
repaint), but the cursor can never run off the bottom edge — scrolling
during the redraw is structurally impossible, so scrollback stays
pristine. ``_select_static_tail`` now returns the chosen lines as a
list to support per-line positioning.

Validation: regression test asserts per-line CUP emission, wrap-aware
row advancement, and that no bare ``\n`` appears in the repaint byte
stream.

### Fixed — resize no longer eats static: erase whole viewport + redraw retained static tail (Root P)

Symptom: after Root O, every horizontal shrink made the blank gap
between the static output (chat history) and the live frame **grow**.
Root O anchored the erase to the DSR-reported cursor row, but
``cpr_debug.log`` showed Windows Terminal's reflow drifts that row
wildly (cursor_row ranged 40-48 — earlier 7-47 — while rows=48 stayed
constant). Every erase that overshot the old frame's top permanently
blanked static rows (PyInk never repaints static), so the visible gap
was the union of all historical erase overshoots — monotonically
growing. Even with a perfect cursor_row, Root O's +2 safety margin ate
2 static rows per resize; its "overshoot is harmless" comment assumed
a pre-existing gap above the frame that Jarvis doesn't have (static
fills the viewport).

Fix (Root P — static tail redraw): make the erase reversible instead
of trying to aim it.

* ``Instance`` retains the most recent 500 logical lines of flushed
  static text verbatim (ANSI included) in ``_static_tail_text``;
  ``_flush_static_and_frame`` is the only writer, ``reset_static``
  clears it.
* ``force_repaint`` no longer queries the cursor at all. It emits
  ``\x1b[1;1H\x1b[0J`` (in-place viewport erase — scrollback
  untouched), redraws a height-budgeted suffix of the retained static
  bottom-anchored directly above the frame, then paints the new frame
  at ``frame_top = rows - visual_h_new + 1``.
* Static selection (``_select_static_tail``) walks retained lines
  newest-first with a 1-row safety margin: ``_visual_height``
  over-estimates surface as a few bounded blank rows at the viewport
  top, never as lost messages or cumulative damage.

DSR/CPR machinery (``_query_cursor`` / ``_handle_cpr`` / key-parser
CPR routing) is preserved but the paint path no longer uses it.

Validation: mock-driven tests cover viewport-erase + frame anchor,
static-tail redraw ordering (erase → static → frame), budget trimming
(oldest lines dropped, newest kept), no-DSR assertion, ``reset_static``
tail clearing, and tail length capping. Manual verification on Windows
Terminal still required.

### Fixed — resize uses DSR for old-frame-bottom, paints new frame at viewport bottom (Root O, superseded by Root P)

Symptom: after Root N, alternating resize on Windows Terminal still left
**severe live residue** — 3 rows of stale frame content above the new
frame on every shrink. Diagnostic data (``cpr_debug.log`` analysis)
showed why: Root N anchored the ERASE extent to ``new_rows``, but
Windows Terminal's horizontal reflow moves the cursor (= old frame's
last row) upward when cols shrink. Across multiple events cursor_row
ranged 7-47 while rows=48 stayed constant — Root N's erase range
(``[new_frame_top+1, end]`` anchored to ``new_rows``) missed the old
frame's actual post-rewrap position by 1-N rows, leaving residue
proportional to the cursor drift.

Fix (Root O — split-anchor): the erase and the new-frame paint use
DIFFERENT anchors because they target DIFFERENT rows.

* **Erase anchor = DSR cursor_row** (= old frame bottom post-rewrap;
  PyInk's pre-resize cursor sits at frame bottom, reflow preserves
  that logical position). Erase extent = ``max(visual_h_old_at_new_cols,
  visual_h_new) + 2`` rows above cursor_row. The +2 covers
  ``_visual_height``'s known inaccuracy vs Windows Terminal's actual
  reflow (the Root J/K/L lesson). Overshoot is harmless (just blanks
  a couple of extra rows in the gap between static content and the
  frame); undershoot leaves residue.
* **New-frame anchor = ``rows``** (from the resize signal). The new
  frame always parks at the viewport bottom regardless of where the
  old frame ended up.

``force_repaint`` branch now does:

1. ``visual_h_new = _visual_height(new_frame, cols)``
2. ``visual_h_old_at_new_cols = _visual_height(prev_frame, cols)`` if
   prev exists; else equal to ``visual_h_new``.
3. ``cursor_row = self._query_cursor()``
4. If ``cursor_row`` returned a row AND ``prev_frame`` exists:
   ``erase_h = max(h_old, h_new) + 2``;
   ``erase_top = max(1, cursor_row - erase_h + 1)``;
   ``branch = "root_o"``.
   Else (DSR timeout, or no prev frame): ``erase_top = new_frame_top``;
   ``branch = "root_o_fallback"`` — same tradeoff Root N made for the
   timeout path.
5. ``\\x1b[{erase_top};1H\\x1b[0J`` blanks from the erase anchor to
   end-of-viewport. Scrollback untouched.
6. ``new_frame_top = rows - visual_h_new + 1``. Cursor-down by
   ``new_frame_top - erase_top`` rows (no movement if equal).
7. ``_paint_initial`` paints the new frame; cursor parks at the
   frame's last visual row (bottom-parked contract).

Known tradeoff: ``_visual_height`` is the same potentially-inaccurate
function Root J/K/L used, but here it only sizes the ERASE extent
(overshoot is harmless). The new frame's anchor is exact (derived
from ``rows``), so paint position is never wrong.

Validation:

* Mock-driven tests cover: Root O primary path (cursor_row drives
  erase_top, ``rows`` drives new_frame_top); DSR-timeout fallback
  (Root N-style anchor when cursor_row is None); cursor-drift
  scenarios (different cursor_row values produce the same final
  new_frame position).
* Manual testing remains required (Jarvis live resize on Windows
  Terminal) per the PRD acceptance criteria.

### Fixed — resize anchors frame to viewport bottom, not cursor row (Root N)

Symptom: after Root M (DSR Cursor Query), force_repaint was still
painting the frame in the middle of the viewport on Windows Terminal
— leaving blank rows below the frame and old-frame residue above.
Diagnostic data (``cpr_debug.log``) confirmed Root M's core
assumption was broken: the CPR-reported cursor row is NEVER equal to
``rows`` post-resize on Windows Terminal. Across 57 force_repaint
events, ``cursor_row`` ranged 7-47 while ``rows=48`` stayed constant.
Horizontal resize causes the cursor's viewport-relative row to drift
wildly (cursor_row dropped from 47 to 10 as cols shrank from 156 to
131). PyInk's "cursor parks at frame bottom" invariant does not hold
across Windows Terminal's reflow.

Fix (Root N — viewport-bottom anchor): ignore ``cursor_row`` entirely
and anchor the new frame to ``rows`` (from the resize signal). The
``force_repaint`` branch now does:

1. ``visual_h = _visual_height(new_frame, cols)``
2. ``frame_top = max(0, rows - visual_h)``
3. ``CUP(frame_top + 1, 1)`` + ``\x1b[0J`` — precise erase of just
   the rows the new frame will occupy. Scrollback untouched
   (no ``\x1b[2J`` push, no ``\x1b[3J`` scrollback wipe). Static
   content above ``frame_top`` is preserved.
4. ``_paint_initial`` paints the new frame; cursor parks at the
   frame's last visual row (bottom-parked contract).

This is Root I's positioning logic combined with Root M's precise
erase range — best of both, no DSR dependency for the anchor. The
``_query_cursor`` call is kept for diagnostic logging only so the
next resize test continues to gather cursor_row data; its result is
NOT used for the anchor.

Known tradeoff: if ``visual_h`` changes between resizes (frame
height shrinks from 6 to 4), 1-2 rows of old frame content may
survive above the new ``frame_top``. Mitigation options if observed:
safety-margin erase, or track ``_last_visual_h`` and erase
``max(prev, new)`` rows.

The DSR machinery (``_query_cursor``, ``_handle_cpr``, ``_cpr_*``
slots, terminal CPR callback) stays in place untouched. Cleanup is
a follow-up if Root N holds up in manual testing.

Validation:

* Mock-driven tests cover: ``frame_top`` is independent of
  ``cursor_row`` (a wildly-wrong cursor row produces the same CUP
  target as a correct one); Root I blind ``\x1b[1;1H`` fallback is
  gone; ``\x1b[2J`` and ``\x1b[999B`` are absent.
* Manual testing remains required (Jarvis live resize on Windows
  Terminal) per the PRD acceptance criteria.

### Fixed — resize uses DSR Cursor Query for precise frame erase (Root M)

Symptom: after Root I, alternating resize on Windows Terminal still
left residue — the live frame's wrapped tails (right-aligned
status_bar with emoji/CJK, full-width dividers) survived the
``\x1b[1;1H\x1b[0J`` clear because PyInk couldn't predict where the
old frame's visual rows actually sat after Windows Terminal's
post-resize re-wrap. ``string_width``-based visual-height prediction
(Root J/K/L) didn't model WT's emoji / CJK / pending-wrap behaviour
and was wrong by ±1 row, leaving a residual copy of the frame above
the new one every cycle.

Root cause: Windows Terminal's re-wrap is a black box from PyInk's
perspective. Any solution that relies on PyInk unilaterally computing
the cursor's post-rewrap row is fighting the terminal instead of
asking it.

Fix (Root M — DSR Cursor Query): use the VT-100 standardized cursor
query mechanism to ask the terminal where the cursor actually sits
after the rewrap, then derive the new frame's visual top from that
row. ``_paint_now``'s ``force_repaint`` branch now does:

1. Send ``\x1b[6n`` (DSR — Device Status Report) to stdout.
2. Wait up to 50 ms for the input thread to receive
   ``\x1b[<row>;<col>R`` (CPR — Cursor Position Report).
3. ``frame_top = max(0, cpr_row - visual_h_new)``.
4. ``CUP(frame_top+1, 1)`` + ``\x1b[0J`` — precise erase of just the
   rows the new frame will occupy. Scrollback is untouched (no
   ``\x1b[2J`` push, no ``\x1b[3J`` scrollback wipe). Viewport-static
   content above the frame is preserved — the core requirement that
   Root I sacrificed.
5. ``_paint_initial`` paints the new frame; cursor parks at the
   frame's last visual row (bottom-parked contract).

PyInk's existing invariant — every paint leaves the cursor at the
frame's last visual row — is what makes step 3 correct: pre-resize
the cursor was at the frame bottom; post-rewrap its viewport-relative
row IS the new frame bottom.

On timeout (rare — Windows Terminal round-trip is typically 1–5 ms,
20 ms under load; 50 ms is generous) the path degrades to Root I
(``\x1b[1;1H\x1b[0J`` + bottom park). Tradeoff there: viewport-static
content is wiped but scrollback is preserved.

Concurrency / thread coordination:

* ``Instance._query_cursor`` runs on the painter thread (typically the
  resize-scheduled throttle thread). It bumps ``_cpr_generation``,
  marks it pending, writes DSR, and waits on ``_cpr_event`` (50 ms
  timeout).
* The Terminal's input thread runs the existing KeyParser; sequences
  are now also matched against the CSI CPR regex
  (``^\x1b\[(\d+);(\d+)R$``). Matches route to
  ``Instance._handle_cpr(row, col)`` instead of being emitted as a
  (harmless empty) Key. The handler validates the generation, writes
  ``_cpr_row`` under ``self._cpr_lock``, and signals ``_cpr_event``.
* **CPR state is guarded by a dedicated ``_cpr_lock``** (a plain
  ``threading.Lock``), NOT the painter's ``self._lock``. This is
  load-bearing: ``_paint_now`` holds ``self._lock`` across the entire
  paint including the call to ``_query_cursor``'s
  ``_cpr_event.wait(timeout=0.05)``. The input thread (which runs
  ``_handle_cpr``) is a different thread from the painter, so if
  ``_handle_cpr`` tried to acquire ``self._lock`` it would block
  until the 50 ms timeout fired — at which point the late CPR would
  write ``_cpr_row`` and signal the event, but the painter had
  already returned ``None`` and fallen back to Root I. In effect
  every real-terminal force_repaint would silently degrade to Root
  I. Mock-based tests don't catch this because they patch
  ``_query_cursor`` and never exercise the cross-thread lock
  acquisition. The dedicated ``_cpr_lock`` keeps the painter's outer
  ``self._lock`` irrelevant to the CPR protocol — no nested locking,
  no release/reacquire gymnastics.
* ``_cpr_pending`` is cleared on timeout or after consumption, so a
  stale late-arriving CPR is rejected (returns ``False``) rather than
  poisoning a future query (the ConPTY reorder race documented in
  ``research/cpr-validation.md`` §5).
* A stray CPR (e.g. misbehaving terminal, literal ``R`` keystroke)
  never matches the regex — bare ``R`` and ``\x1b[R`` are excluded —
  and even if it did, ``_handle_cpr`` returns ``False`` when nothing
  is pending, so the sequence surfaces as a normal Key. Safety
  default preserved.

CPR is opt-out via ``Terminal.set_cpr_callback(None)``; the callback
slot starts ``None`` so a bare CPR before registration is never
silently swallowed.

Validation:

* ``test_cpr.py`` (in Jarvis task
  ``.trellis/tasks/07-24-resize-anchor-cursor-bottom/research/``)
  manually verified on Windows Terminal that DSR/CPR round-trip
  works, honours CUP, and reports viewport-relative post-rewrap row
  semantics. See ``research/cpr-validation.md`` for the full report.
* Mock-driven tests cover: DSR-success path emits precise CUP; DSR
  timeout falls back to Root I; stale-generation CPR is rejected;
  non-CPR sequences (literal ``R``, ``\x1b[R``) are not intercepted.

Manual testing remains required (Jarvis live resize on Windows
Terminal) per the PRD acceptance criteria — automated tests cover
the byte contract but not the visual outcome.

### Fixed — resize clears viewport in place, preserves scrollback (Root I)

Symptom: after Root H, alternating resize on Windows Terminal pushed
a duplicate of the live area into scrollback. Pre-existing scrollback
(content the user had scrolled to before the resize) was shoved
further up out of view. Net effect: scrollback grew by one live-frame
per resize cycle, with the user's real history pushed past the top.

Root cause: Root H used ``\x1b[2J`` (CC-style clear). On Windows
Terminal ``\x1b[2J`` doesn't blank cells in place — it pushes the
visible viewport content into scrollback. PRD Decision 3's original
concern (``\x1b[2J`` destroys scrollback) was right after all. CC
avoids the duplication by pairing ``\x1b[2J`` with ``\x1b[3J`` (clear
scrollback), which is why CC's own renderer doesn't show the same
duplication — but that path wipes the user's pre-existing scrollback,
which Jarvis can't tolerate.

Fix: replace Root H's ``\x1b[2J\x1b[H`` with
``\x1b[1;1H\x1b[0J`` — cursor to (0,0) + clear-to-end-of-viewport.
``\x1b[0J`` blanks visible cells WITHOUT scrolling content into
scrollback. Scrollback is fully preserved; only the in-viewport
content gets wiped on resize (same tradeoff as Root H, minus the
scrollback push).

  ``\x1b[1;1H\x1b[0J\x1b[<frame_top>B`` + ``_paint_initial(new_frame)``

* ``\x1b[1;1H`` homes cursor to (0, 0). Functionally identical to
  ``\x1b[H``; the explicit form documents the (row, col) ordering
  for readers.
* ``\x1b[0J`` clears from cursor to end-of-viewport. No
  scrollback push.
* ``\x1b[<frame_top>B`` moves to the new frame's visual top
  (``rows - _visual_height(new_frame, cols)``).
* ``_paint_initial`` paints the frame; cursor parks at the frame's
  last visual row (bottom-parked contract).

The ``\x1b[2J`` ban from PRD Decision 3 is restored — the diff
module continues to forbid it across all paths (test_no_2j_across_all_cases).

### Fixed — resize uses CC-style clear + absolute cursor (Root H)

Symptom: after Root D (cursor anchor) and Root G (force_wrap_aware),
single-direction resize was clean but **alternating resize** still
left status_bar + divider residuals stacked above the live frame in
real Windows Terminal (pyte simulation couldn't reproduce — pyte
doesn't model Windows Terminal's scrollback re-wrap).

Root cause: the previous approach used **relative cursor math**
(``\x1b[999B`` anchor + walk-up + ``\x1b[0J``). This relied on two
assumptions that Windows Terminal's re-wrap violated in alternating
resize sequences:

1. The cursor was at viewport bottom after the anchor. In practice,
   Windows Terminal's re-wrap can leave the cursor at a different
   visual row than expected (cursor-drift), and ``\x1b[999B`` only
   guarantees "viewport bottom" — not "old frame's bottom".
2. ``old_visual`` (computed via ``string_width``) matched the
   terminal's actual post-re-wrap visual height. Wrap behaviour for
   emoji / CJK / pending-wrap state varies across terminals; any
   mismatch caused the walk-up to land at the wrong row, leaving
   the old frame's wrapped tails uncleaned.

Fix: replace the relative path with CC-style **absolute positioning**
(researching Claude Code's own renderer at
``D:\Projects\github\claude-code\src\ink\``):

  ``\x1b[2J\x1b[H\x1b[<frame_top>B`` + ``_paint_initial(new_frame)``

* ``\x1b[2J`` clears the visible viewport in place on modern
  terminals (Windows Terminal / xterm / mintty). Empirically
  validated by Claude Code, which uses the same sequence for resize.
  The old PRD Decision 3 concern ("``\x1b[2J`` scrolls content into
  scrollback") was based on conservative assumptions that don't
  hold for modern terminals.
* ``\x1b[H`` homes cursor to (0, 0).
* ``\x1b[<frame_top>B`` (cursor-down) moves to the new frame's
  visual top, computed as ``rows - _visual_height(new_frame, cols)``.
* ``_paint_initial`` paints the frame; cursor parks at the frame's
  last visual row = viewport bottom (bottom-parked contract).

Scrollback above the viewport is **preserved** — only the visible
viewport is cleared. Conversation history remains accessible via
scroll-up. This matches Claude Code's main-screen resize behaviour.

Tradeoff: viewport-visible static content (the last few rows of
conversation history that were in viewport, not yet scrolled into
scrollback) is wiped on resize. The frame and everything in
scrollback is intact.

The ``force_wrap_aware`` parameter (Root G) is removed —
``repaint_frame`` is now only used for the height-change path
(palette open/close), where the no-wrap / wrap-aware distinction
still matters. Root D's ``\x1b[999B`` anchor is also removed.

### Fixed — alternating resize no longer scrolls status_bar into scrollback (Root G)

Symptom: after Root D (cursor anchor), Root E (display-width wrap
detection), and Root F (defensive `\r\n` in `_paint_initial`),
single-direction resize was clean but **alternating resize**
(shrink-then-grow OR grow-then-shrink) still left 1-2 residual
`status_bar + divider` pairs floating above the live frame.

Root cause: `repaint_frame` has two erase paths — a no-wrap (top-
aligned) path used when no old row is wider than `cols`, and a
wrap-aware (bottom-aligned) path used when at least one old row
would passively wrap. After a SHRINK, the new (shrunk) frame is
narrower, so all its rows fit in the subsequent GROW's wider cols
— `may_wrap` returns False and the no-wrap path is selected.

The no-wrap path erases only the old frame's footprint, then
`_paint_initial` writes the new frame at the SAME (top-aligned)
origin. When the new frame is taller than the old (GROW case), the
tail rows extend past viewport bottom and the terminal scrolls
them into scrollback. Each alternating resize scrolls one more
status_bar + divider pair into scrollback, where they remain
visible above the live frame.

Fix: add `force_wrap_aware: bool = False` parameter to
`repaint_frame`. When True, the wrap-aware path is selected
regardless of `may_wrap` detection. `_paint_now` passes
`force_wrap_aware=force_repaint`, so resize always uses
clear-to-end-of-viewport + bottom-align:

* `\x1b[0J` blanks from the old frame's visual top to viewport
  bottom — a superset of the old footprint, so any "drifted"
  content above is also cleared.
* Bottom-align positions the cursor at the new frame's visual
  top, computed as `old_visual_top - (new_visual - old_visual)`
  when the new frame is taller. The new frame grows upward into
  the blank area instead of past viewport bottom.

Side effect: the first force_repaint on a frame whose width barely
changed (no wrap, height unchanged) now emits `\x1b[0J` instead of
per-row `\x1b[2K`. Bytes-wasteful for trivial resizes but
semantically equivalent (the cleared region is the same).

The non-force_repaint path (palette open/close — height-change
without resize) still uses the no-wrap top-aligned path, preserving
the "origin stable so the next grow fills the same footprint"
behaviour.

### Fixed — alternating resize no longer leaves residual status_bar copies (Root F)

Symptom: after Root D (cursor anchor) and Root E (string_width wrap
detection), single-direction resize (shrink-only OR grow-only) was
visually clean, but **alternating resize** (shrink-then-grow OR
grow-then-shrink) still left 1-2 residual `status_bar + divider`
pairs floating above the live frame.

Root cause: in `_paint_initial`, subsequent rows were emitted as
`\n\x1b[2K<line>`. The bare `\n` relies on the terminal to reset the
cursor's column to 1 — true when the previous row's last write landed
BEFORE the rightmost column. But when the previous row's last char
landed ON the rightmost column (e.g. Jarvis's `_input_divider`, which
is `DIVIDER_HORIZ * cols` — exactly cols chars wide), the terminal
enters "pending wrap" state: the cursor is logically parked at
column `cols+1` (the wrap-pending position), and a bare `\n` keeps
the column at that pending position instead of resetting to 1.

Result: on the next row, `\x1b[2K` clears from column `cols+1` of
the new row (no-op visible because that column is past the right
edge), and `<line>` is written starting at column `cols+1`, which
the terminal renders as a wrap of the new row to a fresh visual
row below — offset by one row from the intended position. After N
alternating resizes, N residual copies stack up.

Fix: emit `\r\n\x1b[2K<line>` instead. The explicit `\r` forces
column 1 regardless of pending wrap state. The byte difference is
one extra `\r` per subsequent row (negligible), and existing tests
that asserted on the old `\n\x1b[2K` byte sequence are updated to
the new `\r\n\x1b[2K` form.

ASCII behaviour is unchanged — the `\r` is a no-op when the cursor
is already at column 1 (the common case). On terminals that don't
enter pending wrap (most non-Windows terminals treat LF as CR+LF
at the rightmost edge more gracefully), the `\r` is similarly a
no-op. The fix is purely defensive against Windows Terminal's
strict VT-100 pending-wrap semantics.

### Fixed — resize no longer stacks duplicate status_bars (Root E)

`repaint_frame` was using Python's `len(line)` (character count) to
detect wrap and compute visual height. For lines containing wide
characters (CJK / emoji), display width exceeds character count —
Jarvis's `📁 Jarvis 🧠 deepseek 🤖 brain 🔌 2` status_bar is 31 chars
but 35 display cols. With right-aligned indent the rendered line lands
at ~81 chars / ~85 display cols.

At any cols in the window `len(line) <= cols < display_width(line)`
the terminal actually wraps the line to two visual rows, but PyInk's
`len`-based `may_wrap` check returns `False`. The no-wrap erase path
emits one `\x1b[2K` per logical row, which only clears the cursor's
*current* visual row — the passively-wrapped tail above survives the
erase and shows up as a residual status_bar copy. Each resize through
this window leaves one residual; multiple resizes stack duplicates
(the "7 status_bars" symptom).

Root C (commit `689d002`) added the wrap-aware `\x1b[0J` erase path
specifically to handle passive wrap, but its wrap detection used
`len()` too — so it only triggered for ASCII-wide rows (dividers)
and missed emoji/CJK rows.

Fix: import `string_width` from `ink.layout.measure` and replace the
three `len()` calls in the wrap-aware branch (the `may_wrap` check,
the `old_visual` loop, and the `new_visual` loop). `string_width`
strips ANSI escapes and counts display columns (CJK = 2, combining
marks = 0), matching what the terminal actually wraps on. ASCII
behaviour is unchanged (`string_width("xxx") == len("xxx")`), so the
existing divider / palette-open tests still pass.

### Fixed — resize no longer hides scrollback content (Root D)

Windows Terminal (and most VT-100 terminals) re-wrap scrollback content
on resize: a markdown table that fit on one line at 100 cols gets
passively wrapped to two lines at 60 cols, etc. This re-wrap shifts
the cursor's *visual* position — the cursor's absolute row stays put
in the terminal's internal coordinate system, but relative to the
re-wrapped viewport it has drifted away from the old frame's bottom
row.

PyInk's `repaint_frame` uses purely relative cursor math (walk-up
`old_visual - 1` rows, emit `\x1b[0J`, walk back down/up to align the
new frame). When the cursor has drifted, the walk-up lands inside the
scrollback, `\x1b[0J` clears scrollback rows, and the new frame is
painted mid-scrollback — visually covering the re-wrapped table above.
Symptom: shrink the terminal → the markdown table that was visible
above the live frame disappears behind the frame.

The fix anchors the cursor to the viewport bottom **before** the
erase + paint walk. In `_paint_now`'s `force_repaint` branch we now
emit `\x1b[999B\r` (cursor-down by a large constant + carriage
return). The terminal clamps cursor-down to the last row of the
viewport, so 999 is safe regardless of viewport height. From the
correctly-anchored starting position, `repaint_frame`'s relative
math works as intended.

Side effect: if the static region is small (live frame sits mid-
viewport), the first resize after mount will snap the frame down to
the viewport bottom, leaving a blank gap above. The trade-off is
acceptable — in Jarvis's typical session the frame is already at the
viewport bottom, so the snap is invisible.

Non-TTY note: `\x1b[999B` is always written to the byte stream, even
when stdout is a `StringIO` (tests). This is necessary — the byte
contract is what the terminal consumes, and tests that drive PyInk
through `StringIO` verify the byte stream, not visual effect.

### Fixed — terminal resize now redraws the live area

Two independent root causes prevented the live area from visually updating
after a terminal resize on Windows Terminal (and any platform using the
polling-thread path):

1. **`use_window_size()` returned a mount-time snapshot.** Components that
   captured `win = use_window_size()` once at body-run time kept reading
   the original columns/rows forever, so width-dependent widgets (input
   divider, spinner row, tool row, etc.) produced byte-identical frames
   on resize and hit the `prev_frame == new_frame` early-return in
   `_paint_now`. `WindowSize` is now a property-based class whose
   `.columns` / `.rows` re-read a live signal on every access; the
   signal is written at the end of every `_paint_now` pass.
2. **Width-independent widgets never reset the terminal's passive-wrap
   state.** Even with a reactive `WindowSize`, a widget whose rendered
   output doesn't depend on cols (e.g. a status-bar pill row) produced
   the same frame on resize, hit the same early-return, and left the
   terminal showing shrunk-wrap / widen-no-rejoin artefacts. The resize
   callback now sets `Instance._force_repaint = True` before scheduling
   the next paint; `_paint_now` consumes the flag, skips the equality
   early-return, and routes through `repaint_frame` so the cursor is
   walked back to frame origin before the new frame is re-emitted.
   Going through `write_diff(None, new_frame)` (the first-paint branch)
   was rejected because it assumes the cursor is already at frame
   origin — on resize the cursor sits at the bottom of the previous
   frame, so the first-paint branch stacks each new frame below the
   previous one (the "duplicated status_bar / divider / input" bug).
3. **Wrapped row tails survived the force-repaint erase.** A width
   shrink passively wraps wide rows (right-aligned status_bar,
   full-width dividers) across multiple visual rows, so the old
   frame's visual footprint is *taller* than its logical row count.
   Per-row `\x1b[2K` in the erase pass only blanks the visual row the
   cursor lands on, leaving wrapped tails above as residue — the
   "stacking status_bars on every shrink resize" regression after the
   force-repaint flag landed. `repaint_frame` now detects this case
   (any old logical row wider than `cols`) and switches to a
   wrap-aware erase: walk cursor to the visual top of the old
   footprint, emit `\x1b[0J` (clear-to-end-of-viewport, blanks cells
   in place without scrolling — safe under PRD Decision 3), then
   bottom-align the new frame so it parks at the same viewport row as
   before the resize instead of drifting upward on each shrink.

Side fix: `pipeline.render` used to auto-fill `options.columns` /
`options.rows` with the detected terminal size even when the caller
left them unset, which locked `use_window_size` to the mount-time
detection. The options bag now carries an explicit user pin only —
`None` when unset — so the hook can read the live signal. The clamped
values still seed `Instance.columns` / `Instance.rows` for first paint,
and `_resolve_columns` / `_resolve_rows` fall through to
`terminal.columns` / `terminal.rows` when options is `None`.

API surface unchanged: `use_window_size()` still returns a `WindowSize`
with `.columns` / `.rows` int properties; assigning to either still
raises `AttributeError`.

### Fixed — `quote_color` theme key now wired up

The `quote_color` theme key (`DEFAULT_MARKDOWN_THEME["quote_color"]`)
was defined but never read — passing `theme={"quote_color": "red"}`
silently produced the default look. The key is now resolved through
the semantic layer (default `"muted"` → gray, SGR 90) and applied to
every inline text run inside a blockquote. The legacy `__quote__`
boolean flag (which only drove `dimColor=True`, SGR 2) is removed;
its role is folded into `__quote_color__` (a `None` value disables
quote colouring entirely).

### Changed — blockquote visual: `dimColor` (SGR 2) → gray colour (SGR 90)

As a consequence of wiring `quote_color` up, the default blockquote
inline text now carries the resolved quote colour (gray, SGR 90)
instead of the old `dimColor` attribute (SGR 2). Both are muted
treatments, but the SGR code differs and the terminal may render
them slightly differently. This completes PR3's semantic-colour
intent (the default value was already flipped from `"gray"` to
`"muted"` but the change was a no-op because the key was unread).

To opt out entirely, pass `theme={"quote_color": None,
"muted_color": None}` (both keys must be `None` to defeat the
semantic fallback).

### Changed (internal) — table border glyphs de-duplicated

The markdown-internal `_TABLE_BORDER_CHARS` dict (which carried both
the outer corners and the cross pieces) is replaced by
`_get_table_border_chars(style)`: outer corners are now read from
`ink.render.ansi.BORDER_STYLES` (single source of truth), only the
5 table-specific cross glyphs (`top_cross` / `mid_cross` /
`mid_left` / `mid_right` / `bottom_cross`) remain markdown-side via
`_TABLE_CROSS_CHARS`. The cross glyphs are intentionally NOT folded
into `BORDER_STYLES` (the layout renderer's `_paint_box_border`
would not read them and the rework cost is out of scope).

`BORDER_STYLES["rounded"]` is added as an alias for
`BORDER_STYLES["round"]` so the markdown-facing `table_border_style
= "rounded"` (PR2 default) and the ansi-facing `"round"` are now
interchangeable on both sides. `_TABLE_BORDER_ALIASES = {"rounded":
"round"}` maps the markdown name to the ansi name inside
`_get_table_border_chars`.

### Changed (breaking) — `Markdown` default theme rewrite (PR3)

`DEFAULT_MARKDOWN_THEME` has been rewritten to mirror the Claude Code
terminal UX. Existing callers that relied on the pre-PR3 rainbow
palette / red inline code / pure-indent blockquotes will see different
output. **Migration**: pass an explicit `theme={...}` to restore the
old defaults (see the per-key table below).

#### Heading defaults

The rainbow heading colours are gone. All six heading levels now use
the terminal's default text colour (`None`) with bold; h1 additionally
gets italic + underline for emphasis (Claude Code style).

| Key | Pre-PR3 | PR3 | Restore old look |
| --- | --- | --- | --- |
| `h1_color` | `"magenta"` | `None` | `theme={"h1_color": "magenta"}` |
| `h2_color` | `"yellow"` | `None` | `theme={"h2_color": "yellow"}` |
| `h3_color` | `"green"` | `None` | `theme={"h3_color": "green"}` |
| `h4_color` | `"cyan"` | `None` | `theme={"h4_color": "cyan"}` |
| `h5_color` | `"blue"` | `None` | `theme={"h5_color": "blue"}` |
| `h6_color` | `"gray"` | `None` | `theme={"h6_color": "gray"}` |
| `h1_italic` | `False` | `True` | `theme={"h1_italic": False}` |
| `h1_underline` | `False` | `True` | `theme={"h1_underline": False}` |

#### Inline defaults

Inline code and links now use the semantic `accent` colour key
(resolves to `cyan`, SGR 36) instead of hard-coded `red` / `blue`.
Blockquote inline text uses the `muted` semantic key (resolves to
`gray`). The semantic layer lets callers re-skin the whole document
via `theme={"accent_color": "blue"}` rather than overriding every
per-block colour.

| Key | Pre-PR3 | PR3 | Restore old look |
| --- | --- | --- | --- |
| `code_color` | `"red"` | `"accent"` (→ cyan) | `theme={"code_color": "red"}` |
| `link_color` | `"blue"` | `"accent"` (→ cyan) | `theme={"link_color": "blue"}` |
| `quote_color` | `"gray"` | `"muted"` (→ gray) | `theme={"quote_color": "gray"}` |

#### Blockquote defaults

Blockquotes now render with a visible left bar (`▎`, U+258E) in the
`muted` colour, matching Claude Code. Pre-PR3 the default was a
pure-indent look (`paddingLeft=2`, no bar).

| Key | Pre-PR3 | PR3 | Restore old look |
| --- | --- | --- | --- |
| `quote_bar_char` | `None` | `"▎"` | `theme={"quote_bar_char": None}` |
| `quote_bar_color` | `None` | `"muted"` (→ gray) | `theme={"quote_bar_color": None}` |

#### Block spacing

`Markdown` no longer applies a flat `gap=1` between every block. PR3
introduces 14 per-block spacing theme keys (`spacing_before_<type>` /
`spacing_after_<type>`) so a heading gets a 2-row trailing gap, a
paragraph gets 1, etc. The gap between two adjacent blocks is
`max(spacing_after_<prev>, spacing_before_<next>)` — whichever block
wants more space wins.

New keys (all default to `0` or `1` per the Claude Code spacing rules):

```
spacing_before_heading    = 1    spacing_after_heading    = 2
spacing_before_paragraph  = 0    spacing_after_paragraph  = 1
spacing_before_code_block = 1    spacing_after_code_block = 1
spacing_before_blockquote = 1    spacing_after_blockquote = 1
spacing_before_list       = 0    spacing_after_list       = 1
spacing_before_table      = 1    spacing_after_table      = 1
spacing_before_hr         = 1    spacing_after_hr         = 1
```

Callers that want the old flat `gap=1` look can pass all 14 keys set
to `0` and then wrap the `Markdown(...)` element in a parent `Box`
with `gap=1` — but the new defaults are the recommended starting
point.

### Fixed — nested table responsive shrink (PR3)

Tables nested inside a blockquote or list item now responsively shrink
to the parent's available width. Pre-PR3 the recursive `_render_tokens`
call inside `_render_blockquote` / `_render_list` / `_render_list_item`
didn't thread `columns`, so a nested table rendered at its ideal width
and overflowed the parent. PR3 threads `columns - indent_width` so the
table can shrink or degrade to the key-value fallback inside a quote
or list item.

### Added — semantic colour resolution (PR1, wired in PR3)

The semantic colour layer (`text` / `accent` / `secondary` / `muted` /
`border` + `success` / `error` / `warning` / `info`) introduced in PR1
is now wired into the legacy colour keys. A legacy value that is
itself a semantic name (e.g. `theme={"h1_color": "accent"}`) resolves
through `SEMANTIC_COLORS` to the concrete colour (`cyan`). This lets
callers re-skin the whole document via the semantic layer without
overriding every per-block colour.
