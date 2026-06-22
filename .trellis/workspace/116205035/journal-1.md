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
