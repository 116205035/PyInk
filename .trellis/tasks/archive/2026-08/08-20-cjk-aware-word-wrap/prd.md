# CJK-aware word wrap in measure.py

## Goal

Make `_word_break` (wrap mode) CJK-aware so unspaced CJK text wraps
**between characters** instead of being treated as one oversized word.
Visible symptom (reported from jarvis): the user echo
`Text("❯ " + content)` wraps as `❯` alone on line 1 and the Chinese
content hard-broken from line 2 — the prefix is torn from its content.

## Root cause (verified 2026-08-20)

* `_tokenise_words` (src/ink/layout/measure.py:282) splits only on
  `isspace()`. An unspaced CJK run (~100+ display cols) becomes a
  single "word" token.
* `_word_break` (measure.py:228): oversized word after `❯ ` doesn't
  fit → flush current line (trailing space trimmed) → `❯` alone on
  line 1 → the word is then `_hard_break`-split from line 2.
* Assistant messages show the same distortion (first "word" = the
  whole paragraph), it just looks less broken without a prefix.
* Downstream: `wrap_text(mode="wrap")` is consumed by flex
  `_measure_paragraph` (flex.py:703, flex.py:996), markdown, diff,
  highlighted_code — all inherit the broken CJK line breaking.

## Approach (UAX #14 pragmatic subset)

1. **Tokenise per grapheme cluster** in `_tokenise_words`: a cluster
   classified as CJK-breakable becomes its own `word` token; all
   other clusters accumulate into atomic word runs (latin words stay
   unbroken). Break opportunities: between CJK clusters, between CJK
   and latin, at spaces — as before.
2. **CJK-breakable classification**: `unicodedata.east_asian_width`
   in `("W", "F")`, **excluding Hangul ranges** (Korean line breaking
   is space-based per UAX #14 LB28 — no break between Hangul
   syllables).
3. **Kinsoku (禁則) post-pass**: CJK closing punctuation
   (`。，、！？；：…）】》」』` + small kana) never starts a line —
   merged into the previous token; CJK opening brackets
   (`（【《〈「『`) never end a line — merged into the next token.
4. **Space-run width fix** (same function): measure space tokens with
   `wcswidth` instead of `len()` so U+3000 ideographic space counts
   as 2 cells, not 1.

## Requirements

* `_tokenise_words` MUST iterate grapheme clusters (reuse
  `_iter_clusters`), never split a cluster.
* Latin words MUST remain atomic (existing behavior preserved).
* `wrap_text("你好世界再见", 4, mode="wrap")` → `["你好", "世界", "再见"]`.
* Prefix regression: `wrap_text("❯ " + "你好"*30, 20)` → first line
  starts with `"❯ 你"`; no line exceeds width.
* Kinsoku: `wrap_text("你好，世界真好", 4)` → `["你", "好，", "世界", "真好"]`
  (comma glues to the preceding cluster — greedy push-out, the
  standard browser/terminal behaviour; comma never at line start).
* Existing wrap tests MUST stay green (ascii word wrap, long-word
  hard-split, cluster atomicity for emoji sequences).
* All widths still flow through `wcswidth` — no new width logic.

## Acceptance Criteria

* [x] Cluster-level tokenisation in `_tokenise_words`; latin atomic
* [x] CJK chars (Han, Kana, fullwidth punct) are individual tokens
* [x] Hangul NOT broken between syllables (excluded from breakable)
* [x] Kinsoku: closing punct not at line start, opening brackets not
      at line end (glue semantics; greedy push-out result
      `["你", "好，", "世界", "真好"]` verified)
* [x] `❯ ` prefix stays on first line with content (jarvis regression)
* [x] Space-run width via `wcswidth` (U+3000 = 2)
* [x] Unit tests in tests/layout/test_measure.py for all of the above
* [x] 1 nested/constrained-viewport integration test (Box → column →
      Text with CJK content) per layout-bug-testing discipline
* [x] rendering-contracts.md wrap contract section updated
* [x] lint / typecheck / pytest all green (touched files clean;
      pre-existing baseline failures in unrelated files unchanged:
      18 ruff / 27 mypy / test_diff_handles_growing_frame)

## Out of Scope

* Full UAX #14 implementation (only the CJK subset above)
* Korean intra-word breaking (Hangul stays atomic via hard-break
  fallback — matches terminal behavior for overlong runs)
* No-break-inside rules for bracket pairs (`（…）` content can still
  wrap internally)
* Trailing multi-space-run trim cosmetic bug (pre-existing, ASCII)
* jarvis-side changes (that repo only tracks/verifies)

## Technical Notes

* Implementation site: `src/ink/layout/measure.py` — `_tokenise_words`
  rewrite + `_word_break` space-width line + `_is_cjk_breakable`
  helper + kinsoku sets/merge.
* Tests: `tests/layout/test_measure.py` (unit) +
  `tests/layout/test_measure_integration.py` (nested render).
* Spec: `.trellis/spec/frontend/rendering-contracts.md` wrap section.
* Downstream consumers (flex/markdown/diff/highlighted_code) need no
  changes — they call `wrap_text` only.
