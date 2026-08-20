# TextInput auto-wrap instead of ellipsis truncation

## Goal

Make `TextInput` wrap its content to multiple display rows when the
buffer exceeds the available width (ink.js / Claude Code composer
behaviour) instead of truncating with a leading/trailing `…` around a
cursor-centred window. Reported from jarvis: typing a long message
shows `...` at the front/back of the input row.

## What I already know (research 2026-08-20)

**ink.js reference (D:\Projects\github\ink)**

* `components/ink-text-input/source/index.tsx` does **zero
  truncation/scrolling** — it splices cursor / highlight SGR into the
  value and renders a plain `<Text>{renderedValue}</Text>`.
* `ink-master/src/components/Text.tsx` defaults `wrap='wrap'`;
  `wrap-text.ts` calls `wrapAnsi(text, maxWidth, {trim: false,
  hard: true})` — long values **word-wrap to extra rows**, overly long
  words hard-break. So in ink.js a long input simply grows the input
  vertically; the cursor flows with the wrap.
* Horizontal scroll-window + ellipsis was PyInk's own invention
  (`_truncate_line_around_cursor` + `wrap="truncate-end"`), not
  upstream behaviour.

**PyInk current state (src/ink/externals/text_input.py)**

* `_render_lines()` pre-truncates every logical line via
  `_truncate_line_around_cursor` (cursor-visible window, `…` at
  front/back) and the Text leaf sets `wrap="truncate-end"`.
* Vertical scrolling (`rows` prop + `scroll_offset` signal) already
  works on **display rows** (`len(lines)`), so wrapped rows integrate
  with it naturally.
* `render_text()` joins logical lines with `\n` into ONE Text whose
  callable re-evaluates per signal write (function components run
  once at mount — per-line Text children would freeze).

**Why not just flip `wrap="truncate-end"` → `wrap="wrap"` on the
existing SGR-spliced string**

* Cursor cell = `\x1b[7m<char>\x1b[0m`; selection = one
  `\x1b[7m…\x1b[0m` pair spanning many chars. `_word_break` treats
  escapes as zero-width tokens that attach to the current line — a
  flush can land **between** an SGR opener and its char (dangling
  opener at EOL leaks inverse video; selection loses paint on
  continuation rows). wrap-ansi re-opens SGR state per line; PyInk's
  wrap does not.

**Correct design (component-level wrap, then splice SGR per row)**

1. Wrap the *raw* (un-styled) line text with `wrap_text(mode="wrap")`
   — now CJK-aware — into display rows, tracking each row's source
   char-offset range `[row_start, row_start_next)`.
2. Map cursor / selection char offsets to (display row, visible
   column) via the offset map.
3. Splice cursor + selection SGR into the specific row only (reuse
   `_build_displayed_line` per row with the row's raw slice).
4. Join rows with `\n`; keep `wrap="truncate-end"` as a no-op safety
   net (rows already ≤ width).
5. Reserve 1 cell on the cursor's row for bar cursor / end-of-row
   block cursor (same budget rule `_truncate_line_around_cursor`
   used).

**jarvis usage (D:\Projects\JarvisCopilot\jarvis\tui\app_shell.py:2124)**

* Single-line mode, Enter submits, no `rows`, no `multiline`,
  `ignore_arrows_when=True`, sits in a row Box after a `· `/`❯ `
  prompt glyph — wrapped rows must stay in the same Box (indent under
  the glyph is NOT required — CC continuation rows are not indented
  under the prompt either).

## Assumptions (temporary)

* Offset-aware wrapping needs a small internal helper (either in
  measure.py returning rows + source ranges, or a local walk in
  text_input.py reusing `_iter_clusters` / `_is_cjk_breakable`).
* `mask` path: wrap the masked string (width-1 glyphs) with the same
  offset map (1 char → 1 cell keeps mapping trivial).

## Open Questions

* _None — growth behaviour resolved (see Decision)._

## Requirements (evolving)

* No `…` truncation of input content in the default configuration.
* Long single-line input wraps to extra display rows; Enter still
  submits the whole buffer (single-line mode).
* Cursor + selection render correctly on wrapped rows (no SGR leak,
  no lost paint on continuation rows).
* CJK input wraps between characters (inherits measure.py wrap).
* `rows` viewport semantics preserved: cap + scroll to cursor over
  wrapped display rows.
* Existing tests for truncation behaviour updated/replaced.
* Unbounded growth by default (ink.js parity); `rows` (or
  `box_props.height`/`maxHeight`) is the opt-in cap.

## Decision (ADR-lite)

**Context**: TextInput overflow needs a behaviour; ink.js wraps and
grows (no truncation anywhere in ink-text-input), PyInk truncates
with a cursor-centred ellipsis window.

**Decision** (user-confirmed 2026-08-20): **unbounded wrap-and-grow**,
ink.js / Claude Code composer parity. No horizontal-scroll opt-in
prop in this task (add later if a caller asks). Component-level
wrapping: wrap the raw line into display rows with source-offset
ranges, then splice cursor/selection SGR per row — the generic wrap
engine keeps its raw-text escape-rides-along contract (no SGR state
re-opening).

**Consequences**: input height grows with content (jarvis controls
capping via `rows` if desired); `_truncate_line_around_cursor` and
its helpers are removed; `_word_break` internals are refactored to
an offset-aware walker (`wrap_text_rows`) so wrap rules stay in one
place.

## Acceptance Criteria (evolved — final)

* [x] TextInput with content > width renders N wrapped rows, zero
      `…` in output, all chars present (40/80-char cases + CJK).
* [x] Cursor visible on its wrapped row at the correct column for
      all three cursor styles (parametrised bar/block/underline).
* [x] Selection spanning a wrap boundary paints on both rows
      ([0,25) over 3 rows → rows 0 and 1 painted).
* [x] `rows` still caps height and scrolls to the cursor's *display*
      row (fixed a latent bug: viewport used the logical line number,
      which never follows wrapped rows).
* [x] jarvis-style nested test: row Box (glyph + TextInput) with
      long CJK content — wraps between characters, rows fit width.
* [x] `wrap_text_rows` unit tests: spans partition the source, dropped
      wrap-boundary spaces stay in the preceding row's span, texts
      match `wrap_text` exactly (single wrap-rule source).
* [x] ink.js reference verified: ink-text-input does no truncation;
      Text default wrap grows the input (research above).
* [x] lint / typecheck / pytest green on touched files (feed-driven
      input tests have known timing flakiness under load; re-ran
      clean).

## Definition of Done (team quality bar)

* Tests added/updated (unit + nested constrained-viewport
  integration)
* Lint / typecheck / pytest green (touched files)
* Spec updated (component-guidelines or rendering-contracts §2 note
  on TextInput wrap contract)

## Out of Scope (candidate)

* wrap-ansi-style SGR state re-opening in the generic wrap engine
  (component-level splicing makes it unnecessary here)
* Display-row-granular ArrowUp/Down navigation in multiline mode
  (stays logical-line based)
* Horizontal scrolling mode as an opt-in (`scroll="horizontal"` prop)
  — can be added later if some caller wants the old UX

## Technical Notes

* Reference: `components/ink-text-input/source/index.tsx`,
  `ink-master/src/wrap-text.ts`, `ink-master/src/components/Text.tsx`
* Implementation site: `src/ink/externals/text_input.py`
  (`_render_lines`, new offset-aware wrap helper), possibly
  `src/ink/layout/measure.py` (expose offset-aware wrap)
* Tests: `tests/externals/test_text_input.py`
