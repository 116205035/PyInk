# long-code-line-wrap

## Goal

Fix PyInk's code-block rendering so long source lines **wrap to the next visual row** (CC parity) instead of being **horizontally shrunk by the flex layout** which currently eats trailing characters from every Pygments token. The bug silently corrupts code — `print` becomes `pri`, `item['code']` becomes `it['co'` — with no visual indicator that content is missing.

Affects both `HighlightedCode` (Write tool body) and `StructuredDiff` (Edit tool diff body) in Jarvis TUI; long lines with CJK chars trigger it most often because CJK width=2 pushes lines past `columns` faster.

## What I already know

**Symptom** (verified via `D:/Projects/Jarvis/.trellis/workspace/claude/diag_render.py`):
- `HighlightedCode(line137, language='python', line_numbers=True, indent='     ', first_row_prefix='  ⎿  ', start_line=137)` with `columns=120`:
  - Each Pygments token gets its tail eaten: `print→pri`, `item→it`, `'code'→'co'`, `'break_high'→'break'`, `'pct_from_20w'→'pct_fr'`
  - Total row forced to exactly 120 cols (wcswidth=120)
- Same call with `columns=300` renders the line intact — confirms the bug is column-driven, not CJK-width-driven
- Source line 137 wcswidth=184, so a 120-col terminal always triggers the bug for this line

**Root cause (per `D:/Projects/PyInk/src/ink/layout/flex.py`)**:
- A row Box with multiple Text leaves, when total width > `columns`, invokes the flex shrink algorithm
- Shrink penalises every flexible child proportionally — each Text leaf loses trailing chars to satisfy the budget
- CJK is NOT miscalculated: `wcswidth` is used correctly throughout (`measure.py:50-102`); the bug is purely the shrink contract on code-text leaves

**CC reference** (`D:/Projects/github/claude-code/src/components/HighlightedCode/Fallback.tsx:69`):
- CC's HighlightedCode is `<Text dimColor={dim}><Ansi>{highlighted_code}</Ansi></Text>`
- No horizontal truncation — relies on ink's wrap
- CC's `renderTruncatedContent` (`src/utils/terminal.ts:71`) uses `… +N lines` ONLY for vertical (too many lines), never for horizontal code overflow

**User decision (already locked)**:
- Approach A: wrap long lines (CC parity)
- NOT approach B (tail `…` hard truncation) — CC doesn't do this for code
- NOT approach C (flex_shrink=0 + terminal wrap) — measurement desync would break layout

## Decisions (locked)

- **Scope**: Both `HighlightedCode` AND `StructuredDiff` — they share the same multi-Text-leaf-per-row pattern; fixing both at once lets us share a `tokens_to_ansi_string` helper.
- **Implementation path**: Architectural refactor — each source line becomes ONE `Text` leaf with inline ANSI color sequences (CC `<Ansi>` parity). The existing `_measure_paragraph → wrap_text(mode="wrap")` path handles wrap automatically; continuation rows self-align under the first row's code start column (col `len(indent) + len(gutter)`).
- **language="text" fast path** is already single-Text-per-line — no change needed.

## How the wrap mechanics work (implementation note)

Row Box layout when given `[indent_leaf, gutter_leaf, code_text]`:
- `indent_leaf` (5 spaces, `flexShrink=0` implicit via fixed-width Text) → cols 0-4
- `gutter_leaf` (`"137 "`, dim) → cols 5-8
- `code_text` (ANSI-colored single string) → col 9 onwards

When `code_text` natural width > granted width, `_measure_paragraph` returns wrapped dimensions and the flex algorithm grants it the height of N visual rows. Continuation visual rows live inside `code_text`'s box → start at col 9 → align under first char of code. **Zero factory-level wrap code needed.**

The factory's job is just: convert Pygments `[(token_type, value), ...]` → single ANSI-coded string per source line. A new `tokens_to_ansi_string(tokens, theme) -> str` helper lives next to `_token_text` in `highlighted_code.py`; StructuredDiff gets its own equivalent for `+`/`-`/context rows.

## Open Questions

(none remaining — all resolved, see Decisions)

## Implementation Plan (small PRs)

**PR1: `tokens_to_ansi_string` helper + HighlightedCode refactor**
- Add `tokens_to_ansi_string(tokens, theme) -> str` in `highlighted_code.py` (lives next to `_token_text`)
- Refactor per-line emission: `_group_tokens_by_line` returns `list[str]` (ANSI strings) instead of `list[list[Element]]`
- `_build_line_rows` wraps each ANSI string in a single `Text` (not a Box of Text leaves)
- Tests: long Python line wraps without char loss; short lines render identically to before (snapshot diff)

**PR2: StructuredDiff refactor**
- Same architectural pattern: each `+`/`-`/context row → one Text with ANSI sequences
- ANSI string carries: line-number gutter + sigil (`+`/`-`/` `) + bg color band + Pygments tokens
- Verify `add_bg_color`/`del_bg_color`/`inline_add_color`/`inline_del_color` still produce correct visual (bg band should extend to wrap width on every visual row of a wrapped line — CC parity, may need explicit handling)
- Tests: long diff line wraps; bg band continues on wrapped rows

**PR3: Edge cases + smoke test**
- Single token wider than `columns` (verify `_word_break` hard-splits, no overflow)
- CJK-heavy line (repro the original `底部周线突破.py` case)
- `language="text"` fast path unchanged
- Manual smoke: render the original repro (`D:/Projects/Jarvis/.trellis/workspace/claude/diag_render.py`) at `columns=120`, confirm `print` / `item['code']` / `break_high` all intact

## Decision (ADR-lite)

**Context**: Long code lines in HighlightedCode / StructuredDiff silently lose characters because the flex shrink algorithm penalizes each Pygments token Text leaf proportionally when a row exceeds `columns`. CJK accelerates the trigger (width=2 per char) but is not the root cause.

**Decision**: Architectural refactor to CC's `<Ansi>{code}</Ansi>` pattern — emit ONE Text leaf per source line with inline ANSI color sequences. The existing `_measure_paragraph → wrap_text(mode="wrap")` pipeline wraps the single leaf correctly when it overflows. Continuation visual rows self-align under the first row's code start column.

**Consequences**:
- ✅ Zero factory-level wrap code (leveraging existing layout path)
- ✅ CC parity for both HighlightedCode and StructuredDiff
- ✅ Edge cases (single wide token, CJK) handled by existing `_word_break` / `_hard_break`
- ⚠️ Per-token Element structure lost (tests that introspect Element tree need rewrite to check ANSI string content)
- ⚠️ StructuredDiff bg band on wrapped rows needs explicit verification — may need the bg color to extend across the full granted width per visual row

## Requirements (evolving)

- Long code lines MUST render with zero character loss (no `print→pri` corruption)
- Long lines wrap to subsequent visual rows when they exceed `columns`
- Continuation visual rows align under the first row's code start column (via `_DIFF_GUTTER_INDENT` = 5 spaces)
- Line-number gutter appears ONLY on the first visual row of a source line (CC parity)
- `language="text"` fast path also wraps correctly

## Acceptance Criteria (evolving)

- [ ] `HighlightedCode(long_line, language='python', line_numbers=True)` with `columns=120` produces output containing `print`, `item['code']`, `break_high` intact
- [ ] Output width never exceeds `columns`
- [ ] Continuation rows do not repeat the line-number gutter
- [ ] `StructuredDiff` with a long `+`/`-` line behaves the same way
- [ ] Existing HighlightedCode / StructuredDiff tests pass unchanged (no regressions for short lines)
- [ ] New unit tests cover: line exactly at `columns`, line 1 char over, line 2x `columns`, single token wider than `columns`

## Definition of Done

- Tests added (unit + integration for both HighlightedCode and StructuredDiff paths)
- Lint / typecheck green
- Manual smoke test in Jarvis TUI (render `底部周线突破.py` line 137 at 120 cols, confirm no corruption)
- PRD + ADR-lite decision recorded

## Out of Scope (explicit)

- Brand color / palette work (tracked separately in `07-23-markdown-inline-color-brand-alignment`)
- Jarvis-side adapter changes (the adapter already captures content correctly)
- Horizontal scroll UI (would require input handling, not in scope)
- CC's `… +N lines` vertical truncation (already implemented in Jarvis via `_build_stdout_preview_elements`)

## Technical Notes

- **Bug location**: `D:/Projects/PyInk/src/ink/layout/flex.py` (shrink algorithm)
- **Affected renderers**:
  - `D:/Projects/PyInk/src/ink/externals/highlighted_code.py` (HighlightedCode factory)
  - `D:/Projects/PyInk/src/ink/externals/diff.py` (StructuredDiff factory)
- **Width util**: `D:/Projects/PyInk/src/ink/layout/measure.py` (`string_width` / `wcswidth` — correct, no changes needed)
- **Repro script**: `D:/Projects/Jarvis/.trellis/workspace/claude/diag_render.py`
- **CC references**:
  - `D:/Projects/github/claude-code/src/components/HighlightedCode/Fallback.tsx:69` — wrap, no truncation
  - `D:/Projects/github/claude-code/src/utils/terminal.ts:71` — `… +N lines` vertical only

## Research References

(to be populated by trellis-research sub-agents — see Phase 1.3)
