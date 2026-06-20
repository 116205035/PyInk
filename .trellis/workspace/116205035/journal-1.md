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
