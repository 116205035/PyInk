# PyInk MVP-fits examples

## Goal

补齐 PyInk MVP 已支持但缺 example 演示的 6 个场景，让用户从 examples 就能学到所有 MVP 能力。不动核心实现，不依赖 Phase 2 组件。

## Background

PyInk MVP 已完成（task `06-19-pyink-mvp` 已归档，516 测试通过）。当前 7 个 examples（counter/select-input/borders/static/use-input/use-focus/debug-input）覆盖了核心交互，但**这些 MVP 能力没 demo**：

- alternate screen（PRD Decision 3 关键特性）
- Transform 组件（PR7 实现了但没 demo）
- computed + batch（PR1 重要 reactive 概念）
- 嵌套 flex 布局
- ANSI 全色彩 + 全样式
- use_window_size hook（PR6 实现）

## Requirements

### 必交付的 6 个 examples

| Example | 路径 | 演示什么 | 退出方式 |
|---|---|---|---|
| `alternate-screen` | `examples/alternate-screen/alternate_screen.py` | `render(tree, alternate_screen=True)` 全屏接管 + 退出还原 scrollback。展示一个带边框的全屏 UI，按 Esc 或 Ctrl+C 退出，退出后终端恢复之前内容 | Esc / Ctrl+C |
| `transform` | `examples/transform/transform_demo.py` | Transform 组件的 3 个用法：uppercase、hanging indent、line numbering（每行加 `N: ` 前缀） | Ctrl+C |
| `computed-batch` | `examples/computed-batch/computed_batch.py` | computed 派生 + batch 合并。计数器 + 显示 double=computed(count*2)。按 `+` 触发 batch 内连续 5 次 count+=1，UI 只 rerender 一次（用日志展示 effect 触发次数） | Ctrl+C |
| `nested-layout` | `examples/nested-layout/nested_layout.py` | 复杂嵌套：外层 column 包 (row 包 [column, column]) + flexGrow。展示 sidebar + main content 区双栏布局 | Ctrl+C |
| `ansi-colors` | `examples/ansi-colors/ansi_colors.py` | 全部 16 命名色（黑/红/绿/黄/蓝/品红/青/白 + bright 系列）+ hex 示例 + rgb 示例 + 全样式（bold/italic/underline/strikethrough/inverse/dimColor） | Ctrl+C |
| `use-window-size` | `examples/use-window-size/use_window_size.py` | 显示当前 columns × rows。用户 resize 终端窗口，UI 实时更新。还展示一个根据宽度切换布局的 demo（width<60 单栏，>=60 双栏） | Ctrl+C |

### 每个 example 必须

- 能 `python examples/<name>/<file>.py` 直接跑
- 头部 docstring 说明演示什么 + 控制方式
- 用 `from pyink import ...`（不用相对 import）
- 退出方式统一（Esc 或 Ctrl+C）
- 类型注解完整，mypy strict 通过

### 集成测试

更新 `tests/test_examples.py`：
- 加 6 个新 example 的 mount + paint + unmount 测试
- 加到 `test_example_file_exists` parametrize 列表
- 关键 landmark 内容断言（alternate-screen 含边框字符、transform 含大写文本、ansi-colors 含 ANSI escape 等）

### README 更新

更新 `README.md` 的 examples 索引，加 6 个新条目。

## Acceptance Criteria

- [ ] 6 个 example 文件存在且能跑
- [ ] 每个 example 在真实终端验证：演示功能正确、退出干净（终端恢复正常）
- [ ] 6 个新集成测试通过
- [ ] mypy strict + ruff 全绿（含 examples）
- [ ] 全部测试通过（应该 516 + ~10 新 = ~526+）
- [ ] README examples 索引更新

## Definition of Done

- 6 个 example 文件 + docstring + 类型注解
- 集成测试覆盖
- README 更新
- 全部 quality gates 绿
- 不破坏现有 516 测试

## Out of Scope

- 不实现 Phase 2 组件（Spinner / Link / Markdown / TextInput 等）—— 那是下一个 task
- 不修改核心 PyInk 实现（除非 example 暴露 bug，此时单独立项）
- 不写 use_focus hook（保留手写 signal 模式）
- 不删除/修改现有 7 个 examples

## Bug Fixes Prompted by Examples

Examples surfaced two real bugs in the MVP core; both were small fixes
so they were addressed in this task rather than spinning up separate
tasks (the "single立项" carve-out above).

### Bug 1: long text overflowed bordered Box in nested-layout

**Root cause**: the flex engine's text-leaf wrap guard
(``"\n" not in node.text``) suppressed re-wrapping on subsequent
layout passes. The flex engine re-lays-out children multiple times
(initial measure, after grow/shrink, after cross-axis stretch), and
each pass can feed a different ``max_width``. The first pass used
the outer column's wide estimate, joined the wrapped lines with
``\n``, and the guard then blocked re-wrapping when the inner
fixed-width Box later shrunk — letting the long first line poke
through the side border.

**Fix**: snapshot the unwrapped source text on the ``FlexNode``
(``original_text``) and re-wrap from source, but only when the
current constraint is **strictly tighter** than any prior wrap
(tracked via ``props["_wrapped_width"]``). This monotonically
tightens the wrap as layout converges and never unwraps. See
``src/pyink/layout/flex.py`` FlexNode / ``_layout_node`` text-leaf
branch. Covered by
``test_long_text_in_flex_grow_box_wraps_to_final_width``.

### Bug 2: alternate-screen exit wiped the user's scrollback

**Root cause**: ``Instance.unmount`` cleared the live frame *after*
``exit_alternate_screen`` restored the primary buffer. The clear-frame
diff (cursor-up + line-clear) landed on the user's real scrollback
and erased content above the cursor. Compounding factor: the bare
``\\x1b[?1049h`` / ``\\x1b[?1049l`` pair relied on the terminal's
``1049`` implementation to save+restore the cursor; some terminals
(notably older conhost) honour the buffer swap but skip the cursor
save, so the cursor jumped to the top of the screen on exit.

**Fix**:

1. ``Instance.unmount`` now skips ``_clear_frame_for_exit`` when the
   instance was in alt-screen mode (the alt buffer is disposable;
   nothing to clear on the primary buffer).
2. ``Terminal.enter_alternate_screen`` / ``exit_alternate_screen``
   now bracket the ``1049`` swap with explicit DECSC
   (``\\x1b 7``) / DECRC (``\\x1b 8``) so the cursor position is
   saved and restored on every terminal, not just the conformant
   ones. Covered by ``test_alt_screen_brackets_decsc_decrc_around_buffer_swap``
   and ``test_alt_screen_unmount_does_not_clear_frame_on_primary_buffer``.

## Technical Notes

### 关键 API 用法提示

#### alternate-screen
```python
render(Tree(), alternate_screen=True).wait_until_exit()
# Instance 自动发 \x1b[?1049h 进入、\x1b[?1049l 退出
# atexit 钩子保证崩溃也能恢复
```

#### transform
```python
# uppercase
Transform(Text("hello world"), transform=lambda line, i: line.upper())

# hanging indent (第一行顶格，后续缩进 4 空格)
Transform(Box(...), transform=lambda line, i: line if i == 0 else "    " + line)

# line numbering
Transform(Box(...), transform=lambda line, i: f"{i+1:3d}: {line}")
```

#### computed-batch
```python
count = signal(0)
double = computed(lambda: count.value * 2)
render_count = signal(0)  # 记录 effect 触发次数

def render_loop():
    render_count.value += 1
effect(render_loop)

def on_key(key):
    if key.input == '+':
        # batch 内 5 次写入，只触发 1 次 rerender
        with batch:
            for _ in range(5):
                count.value += 1
use_input(on_key)
```

#### use-window-size
```python
size = use_window_size()  # WindowSize(columns, rows)
# 在 render layout 中读 size.columns，自动订阅 resize
```

### 参考实现

- 现有 examples：`D:/Projects/PyInk/examples/` 下的 7 个
- ink examples：`D:/Projects/github/ink/ink-master/examples/`（alternate-screen、transform、terminal-resize 等都有）

## Implementation Plan

单 PR 即可（工作量小）：
- 6 个 example 文件（每个 50-150 行）
- 更新 test_examples.py（+~10 测试）
- 更新 README

预计半天完成。

## Research References

（无；本任务设计来自上一轮对话讨论 + 现有 PyInk 实现）
