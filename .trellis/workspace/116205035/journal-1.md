# Journal - 116205035 (Part 1)

> AI development session journal
> Started: 2026-06-19

---



## Session 1: PyInk MVP: 8 PRs from empty repo to 516-test TUI framework

**Date**: 2026-06-20
**Task**: PyInk MVP: 8 PRs from empty repo to 516-test TUI framework
**Branch**: `main`

### Summary

Built complete PyInk MVP from scratch in one session: 8 PRs covering signals core, reconciler (signals model — components mount once, no React-style rerun), pure-Python flex engine (ink oracle-aligned), Box/Text/Newline/Spacer/Static/Transform components with ANSI rendering, render pipeline (inline frame diff + alternate screen + FPS throttle), hooks (use_input/use_app/use_window_size with Unix termios + Windows VT input raw mode), 6 examples (counter/select-input/borders/static/use-input/use-focus), README + LICENSE + py.typed. Two post-MVP bug fixes: Windows arrow/Tab/F-key capture (missing ENABLE_VIRTUAL_TERMINAL_INPUT + msvcrt chunk drain) and reactive style props (callable support for Box/Text decoration + border visibility props so signal changes update border/color without remount). Final state: 516 passed + 22 xfailed, mypy strict + ruff green across 70 files. 13 ADR-lite decisions captured in PRD.

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `101a04a` | (see git log) |
| `4315595` | (see git log) |
| `c6aa70c` | (see git log) |
| `98d9826` | (see git log) |
| `b657951` | (see git log) |
| `00357db` | (see git log) |
| `d7c3bbe` | (see git log) |
| `3c42a4d` | (see git log) |
| `cefbdde` | (see git log) |
| `e7c0869` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 2: PyInk MVP-fits examples + 3 bug fixes

**Date**: 2026-06-20
**Task**: PyInk MVP-fits examples + 3 bug fixes
**Branch**: `main`

### Summary

Added 6 examples (alternate-screen, transform, computed-batch, nested-layout, ansi-colors, use-window-size) covering MVP features that lacked demos. Examples then surfaced 3 real bugs in MVP core, all fixed with regression tests: (1) long text overflowed bordered Box in nested-layout — flex.py text-leaf wrap guard suppressed re-wrapping on subsequent layout passes, fixed via FlexNode.original_text snapshot + monotonic-tighten re-wrap; (2) alternate-screen exit wiped user scrollback — Instance.unmount cleared frame after exit_alternate_screen restored primary buffer + bare \x1b[?1049h/l didn't save cursor on some terminals, fixed by skipping clear-frame in alt mode and bracketing 1049 with explicit DECSC/DECRC; (3) text style leak across wrapped lines (user-reported 'background overflow') — _paint_text wrapped entire multi-line string with one style pair then split, only first line had opener and last had reset, fixed by per-line styling. Final state: 534 passed + 22 xfailed, mypy strict + ruff green. 13 examples total now demonstrate every MVP capability.

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `06c04b7` | (see git log) |
| `c606633` | (see git log) |
| `838f01d` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 3: PyInk Phase 2: externals + Context + new hooks + measure

**Date**: 2026-06-20
**Task**: PyInk Phase 2: externals + Context + new hooks + measure
**Branch**: `main`

### Summary

Phase 2 complete: 8 PRs adding 3 externals (Spinner/Link/Divider), Context system (create_context/Provider/use_context), 5 new hooks (use_interval/use_focus/use_focus_manager/use_context/use_box_metrics), measure_element API + Box ref prop. Key design decisions: Provider pops at end of own subtree mount (not on unmount) because PyInk components run once at mount — prevents sibling-subtree leakage; Spinner wraps a function component via create_element so hooks run in reconciler context; Link extends measure.py ANSI regex to strip OSC 8 (was CSI-only, caused layout width over-allocation); layout_epoch Signal drives use_box_metrics reactivity (bumped after every layout pass, use_box_metrics subscribes via Computed). 5 new examples (spinner/link/divider/use-focus-real/measure-element) bring total to 18. State-management spec updated to document Context + ref/measure patterns. Final state: 693 passed + 22 xfailed, mypy strict + ruff green across 99 source files.

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `4e95086` | (see git log) |
| `357ecfa` | (see git log) |
| `7c73b7e` | (see git log) |
| `faadde0` | (see git log) |
| `a177b15` | (see git log) |
| `d83c985` | (see git log) |
| `b18dbdb` | (see git log) |
| `eb38e58` | (see git log) |
| `c1cdcb5` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 4: PyInk Phase 3: content rendering + 8 bug fixes

**Date**: 2026-06-20
**Task**: PyInk Phase 3: content rendering + 8 bug fixes
**Branch**: `main`

### Summary

Phase 3 complete: 6 PRs adding 4 content-rendering externals (StreamingText/HighlightedCode/Markdown/StructuredDiff) + 5 examples + README + integration tests. Optional-dependencies pattern established (pyink[highlight] / pyink[markdown] / pyink[all]). Post-PR user testing surfaced 8 bugs, all fixed with regression tests: (1) ANSI SGR leak into shell after exit, (2/3) high CPU + invisible streaming, (4) border scramble as visual artifact of 2/3, (5) reference impl consultation (ink/Textual/Claude Code), (6) _FpsThrottle idle busy-spin (4.6M iter/sec, 99.98% CPU — the real root cause that LRU cache missed), (7) reactive Markdown rendered snapshot at viewport width while layout constrained Text leaf to content width, (8) box border truncation on vertical overflow. Key infra additions: _text_width_context ContextVar for layout-aware text rendering, LRU caches for markdown render + pygments tokenize, FpsThrottle rewrite to indefinite-wait when idle. Final state: 885 passed + 22 xfailed, mypy strict + ruff green across 113 source files. Phase 1-3 covers reactive core + 13 components (6 built-in + 7 externals) + 8 hooks + Context system + measure_element API + 28 examples.

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `dd8eb7e` | (see git log) |
| `86a82d8` | (see git log) |
| `d6334fd` | (see git log) |
| `a5e3e1f` | (see git log) |
| `edf5799` | (see git log) |
| `5159eb9` | (see git log) |
| `59a83bc` | (see git log) |
| `42e73f0` | (see git log) |
| `17e7a1d` | (see git log) |
| `9638247` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 5: PyInk Phase 4: input components + UTF-8/CJK bug fixes

**Date**: 2026-06-21
**Task**: PyInk Phase 4: input components + UTF-8/CJK bug fixes
**Branch**: `main`

### Summary

Phase 4 complete: 5 PRs adding 3 input externals (TextInput with single+multi-line+selection+paste+cursor styles, SelectInput single+multi, ConfirmInput single-key+require_enter) + 5 examples + README + integration tests. Cross-layer enhancement: Terminal bracketed paste mode (DECSET 2004) + Key gains paste_start/paste_end/paste fields + parser detects paste markers. Post-PR user testing surfaced 2 bugs: (9) Chinese/emoji input showed mojibake — root cause was per-chunk UTF-8 decode in terminal pipeline (kernel splits multi-byte sequences across chunks); fixed via incremental UTF-8 decoder (codecs.getincrementaldecoder). (9 follow-up) Chinese still broken on Windows zh-CN — root cause was Windows console default codepage 936 (GBK) makes IME emit GBK bytes, not UTF-8; fixed via SetConsoleCP(65001) on enter_raw_mode + restore on exit. Final state: 1097 passed + 22 xfailed, mypy strict + ruff green across 125 source files. Phase 1-4 covers reactive core + 13 components (6 built-in + 10 externals including 3 input) + 8 hooks + Context + measure + bracketed paste + UTF-8/CJK input + 33 examples.

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `dbbc31d` | (see git log) |
| `bcdc021` | (see git log) |
| `a46509e` | (see git log) |
| `77a7389` | (see git log) |
| `408a6f5` | (see git log) |
| `3878651` | (see git log) |
| `087d362` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 6: PyInk TextInput cursor + is_active + overflow cascade fixes

**Date**: 2026-06-22
**Task**: PyInk TextInput cursor + is_active + overflow cascade fixes
**Branch**: `main`

### Summary

8-commit bugfix pass on Phase 4 TextInput: (1) cursor_style default bar→block + on_cursor_change API; (2) truncate-end on single-line + multi-line Text leaves + demo viewport rows=30; (3) multi-line height frozen at mount fix (single Text + callable re-evaluates full buffer); (4) cursor SGR leak covering multiple chars (pending_trailing.clear() bug); (5) cursor-aware per-line truncation helpers; (6) is_active reactive (bool|Signal|Callable) + demo focus-gated inputs (root cause: all 4 inputs received every keystroke); (7) submitted overflow root cause via Cursor agent — flex.py _wrapped_width stale after callable renderer re-runs at different measurement width; also added TextInput rows viewport prop + cursor-aware _pyink_scroll vertical clip; (8) mypy/ruff cleanup of test helper signature. Final state: 1134 passed + 22 xfailed, mypy strict + ruff green across 125 source files.

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `0e61b97` | (see git log) |
| `1ba5576` | (see git log) |
| `d507af6` | (see git log) |
| `bf34457` | (see git log) |
| `5f59462` | (see git log) |
| `49a54a9` | (see git log) |
| `c3c77e6` | (see git log) |
| `2096035` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 7: PyInk layout hardening (Bug 1 + 4 + 6)

**Date**: 2026-06-22
**Task**: PyInk layout hardening (Bug 1 + 4 + 6)
**Branch**: `main`

### Summary

3-PR layout hardening pass from LAYOUT_ANALYSIS.md report. PR1: deleted dead 'fixed' parameter from _distribute_main (Bug 6, pure cleanup) + refactored TextInput cursor SGR splicing to use display width instead of character offset so CJK + ASCII mixed rows align correctly (Bug 4). PR2: FlexNode gained min_content_main field (text leaf=1, box container = sum/max children + padding + gap), _distribute_main shrink branch replaced single-pass max(0,...) with iterative loop that clamps each child to its min-content floor and redistributes remaining overflow to still-shrinkable children (Bug 1 part 1). PR3: _Grid gained clip/unclip API with intersection semantics; _paint_node pushes outer-rectangle clip around each Box's children before painting (border paints outside clip so never masked). Chose outer rect over inner content box to preserve existing test_examples that legitimately paint small overflows into padding band (Bug 1 part 2). Two-layer defense: layout prevents compression below 1, paint masks overflow past container. Final state: 1156 passed + 22 xfailed, mypy strict + ruff green across 126 source files. LAYOUT_ANALYSIS.md Bug 2/3/5/7 left as known MVP tradeoffs.

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `a3ffb03` | (see git log) |
| `56c788f` | (see git log) |
| `c938b27` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 8: PyInk Phase 5: scroll_offset + reactive dims (VirtualList deleted as wrong abstraction)

**Date**: 2026-06-22
**Task**: PyInk Phase 5: scroll_offset + reactive dims (VirtualList deleted as wrong abstraction)
**Branch**: `main`

### Summary

Phase 5 re-focused after discovering VirtualList was wrong abstraction. PR1: Text.scroll_offset public prop replaces private _pyink_scroll side channel (Bug 5 closed); _resolve extended to handle Signal directly (not just Callable). PR2-3: VirtualList external + examples + reactive dimensions (width/height/margin/padding accept Signal/Callable). Then user questioned whether Claude Code actually uses fullscreen virtualization — discovered Claude Code DEFAULT mode is inline (Static + slice recent 200), VirtualMessageList only used when CLAUDE_CODE_NO_FLICKER env enabled. Jarvis matches Claude Code default (inline), so VirtualList was wrong direction. PR4 (this commit): deleted VirtualList + tests + 2 examples, kept scroll_offset + reactive dimensions + scroll-text demo. Lesson: Claude Code's Messages.tsx is business-specific (RenderableMessage type + streaming tool state + compact boundary + MessageRow tool-use rendering), not a generic list. PyInk provides primitives (Static/Text/Box/Markdown/signals); message-list composition belongs in application (Jarvis). Final state: 1165 passed + 22 xfailed, mypy strict + ruff green across 127 source files. Phase 5 true deliverables: Text.scroll_offset public API + reactive dimensions + TextInput cursor-follow viewport via scroll_offset_sig.

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `9c419c0` | (see git log) |
| `b623969` | (see git log) |
| `cbb319c` | (see git log) |
| `25addcb` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 9: render() clamps frame to terminal size (root cause of cursor-up chaos)

**Date**: 2026-06-22
**Task**: render() clamps frame to terminal size (root cause of cursor-up chaos)
**Branch**: `main`

### Summary

Fixed PyInk's long-standing inline-mode bug: when user passed rows/columns exceeding actual terminal size, frame overflowed screen, terminal scrolled, cursor-up math landed in scrollback → garbled rendering. Fix mirrors Claude Code's ink (ink.tsx:191 reads stdout.rows directly, never lets user override to bigger). pipeline.py adds _clamp_dimension(override, actual, *, is_tty): TTY-gated — clamps only when stdout is real terminal AND override > actual. Non-TTY (test capture, piped output) honors user's value unchanged. Caught during impl: unconditional clamp broke test_text_input_example_runs which runs on non-TTY StringIO with rows=30 and asserts bottom status line renders — clamping to 80x24 default truncated the frame. Demo reverted from auto-detect to fixed columns=72, rows=30 (auto-detect was masking the bug on tall terminals). Now safe on short terminals: frame silently shrinks to fit, cursor-up stays in viewport, no corruption. Final state: 1172 passed + 22 xfailed, mypy strict + ruff green across 128 source files. Content that doesn't fit is correctly clipped (matches Claude Code behavior — use Static to push overflow to scrollback).

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `4f3375a` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 10: PyInk audit critical fixes (7 Critical findings, 4 PRs)

**Date**: 2026-06-22
**Task**: PyInk audit critical fixes (7 Critical findings, 4 PRs)
**Branch**: `main`

### Summary

Full code audit by 6 parallel Explore agents surfaced 7 Critical findings. 4-PR fix pass: PR1 hooks double-dispose (_disposed closure flag on use_input/use_interval/use_focus — investigation found use_focus remount bug didn't actually exist due to identity-based FocusManager._unregister, added defensive flag anyway); PR2 signal/computed race hardening (_read_epoch helper acquires _epoch_lock; verified _deferred_effects swap already inside _deferred_lock; Signal setter + Computed compare atomicity already correct via existing self._lock — added docstrings + 4 concurrency stress tests); PR3 is_active not a bug (handle_key already calls _resolve_is_active per keypress — rewrote docstring to spell out 'evaluated on every keypress, Signal changes don't trigger re-registration, only subsequent keys affected'; extended existing test to cover recover-after-re-enable); PR4 flex measured_width sync (defensive — field currently never read; locked in invariant for future readers) + rerender lock atomicity (real race: original released self._lock between two with-blocks, concurrent unmount could observe half-mounted state; merged entire body into one with self._lock, RLock permits same-thread re-entry for _paint_now). Of 7 Critical: 3 real bugs fixed (#3 hooks, #1 epoch read, #7 rerender lock); 1 latent defensive fix (#6 flex measured_width); 3 disproved as non-bugs but docstring/test improvements (#2 already correct, #4 already correct via identity check, #5 already correct via per-keypress resolution). Final state: 1182 passed + 22 xfailed, mypy strict + ruff green across 128 source files.

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `a3af670` | (see git log) |
| `126e5eb` | (see git log) |
| `5b8932b` | (see git log) |
| `94b0694` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 11: Fix TextInput rows should grow not pin (maxHeight)

**Date**: 2026-06-23
**Task**: Fix TextInput rows should grow not pin (maxHeight)
**Branch**: `main`

### Summary

User-reported bug: multi-line TextInput with rows=N didn't grow from 1 to N rows on Enter. Root cause: c3c77e6 commit's own comment said 'grows from one row up to rows rows' but implementation pinned Box height=N (fixed) instead of maxHeight=N (upper bound). One-line fix: height → maxHeight in resolved_box_props assignment. flex.py already supported maxHeight (line 198/858-874 via min(own_h, max_height-padding)). Box now grows naturally with Text leaf height (1 row empty → 2 after Enter → ... → maxHeight → scroll_offset_sig takes over). Added maxHeight-not-in-resolved_box_props guard so caller-explicit maxHeight wins over rows. 2 regression tests: test_multiline_grows_from_1_to_rows_max (6 Enters with rows=5 never exceeds 5 rows) + test_multiline_rows_grows_to_two_after_first_enter (isolates 1→2 transition). Final state: 1184 passed + 22 xfailed, mypy strict + ruff green across 128 source files.

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `f5f4044` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete
