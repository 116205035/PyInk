# zero-pad-line-number-gutter

## Goal

Highlight 团块的 line-number gutter 在跨数字位数边界（最典型是 99 → 100）时，前几行只有 1–2 位数字、后几行 3 位数字，右对齐后视觉上数字左边缘锯齿严重。改成在自然宽度上零填充（`94 → 094`），让所有行号的数字位数相同，左边缘对齐成一条直线。

## What I already know

- 当前 gutter 宽度计算已经是"自然宽度"：`gutter_width = max(1, len(str(last_line)))`。即整段代码最大行号是几位数，gutter 就几列宽——不存在预留浪费。
- 当前填充用的是空格（右对齐）：
  - `highlighted_code.py:433` — `Text(f"{i:>{gutter_width}} ", dimColor=True)`
  - `diff.py:324` — `num_str = str(line_num).rjust(width)`（在 `_line_number_gutter` 内）
  - `diff.py:965` — `str(line_num).rjust(gutter_width)`（在 `_render_diff_row_cc` 内）
- `line_num=None` 的 continuation row（软换行的第二视觉行）应该保持全空格，不要补 `0`。
- 影响：HighlightedCode 团块（write/edit 摘要、代码展示）+ StructuredDiff 团块（edit/undo 前后对比）。
- CC Fallback.tsx 用的是 `padStart(maxWidth)`（空格填充），不是补 0——但这是品味选择，CC 的代码块很少跨过 99 行，而我们 TUI write 摘要经常显示 100+ 行的整文件。

## Decision (ADR-lite)

**Context**: gutter 跨位数边界时右对齐空格填充，视觉上左边缘锯齿。
**Decision**: 改为零填充（`{i:0{w}}`），宽度继续用自然宽度（`max(1, len(str(last_line)))`），无额外预留。
**Consequences**:
- 视觉上所有行号左边缘对齐成一条直线。
- `094` 这种前导零在某些语言里看起来像八进制字面量——但这是只读的行号列，不会被当作数字解析，风险可忽略。
- 最大行号本身永远不补零（`100` 保持 `100`）。
- 当 max < 10 时，`gutter_width = 1`，零填充和空格填充等价（`f"{5:01}" = "5"`），1 位数场景无视觉变化——符合用户"10 行以内不需要补 0"的预期。

## Requirements

- HighlightedCode gutter：`f"{i:>{gutter_width}} "` → `f"{i:0{gutter_width}} "`
- StructuredDiff gutter（2 处）：`rjust(width)` → `rjust(width, "0")`
- Continuation row（`line_num=None`）保持全空格
- `gutter_width` 计算逻辑不动（自然宽度）

## Acceptance Criteria

- [x] 10 行以内的代码块，行号无 `0` 前缀（1 位宽度零填充 = 原样）— `test_gutter_single_digit_width_emits_no_leading_zero`
- [x] 跨 99/100 边界的代码块，94–99 行号显示为 `094`–`099`，100+ 显示为 `100`+ — `test_gutter_zero_pads_across_99_100_boundary` + diag_gutter.out
- [x] StructuredDiff 的 `+`/`-` 行号同样补零 — `test_line_numbers_zero_pads_to_gutter_width` + `test_line_numbers_zero_pads_across_99_100_boundary` + diag_diff_pr2.out
- [x] 软换行第二视觉行的 gutter 列保持全空格（不补零）— `test_line_numbers_continuation_row_gutter_stays_spaces`
- [x] 现有测试全部通过（flex wrap fix 的测试不应被破坏）— 175/175 passed
- [x] 新增单元测试覆盖补零行为 — 5 个新测试

## Definition of Done

- Tests added/updated
- Lint / typecheck / CI green
- `diag_gutter.py` 风格的端到端验证通过（94–103 行号视觉对齐）

## Out of Scope

- 改 `gutter_width` 的计算方式（不做固定最小宽度）
- 改 CC Fallback.tsx 的 parity（CC 用空格，我们刻意分叉）
- 改 continuation row 的渲染

## Technical Notes

**改动位置（3 处）：**

1. `D:/Projects/PyInk/src/ink/externals/highlighted_code.py:433`
   ```python
   gutter = Text(f"{i:0{gutter_width}} ", dimColor=True)
   ```

2. `D:/Projects/PyInk/src/ink/externals/diff.py:324`（`_line_number_gutter` helper，被 `_render_diff_line` 调用）
   ```python
   num_str = str(line_num).rjust(width, "0")
   ```

3. `D:/Projects/PyInk/src/ink/externals/diff.py:965`（`_render_diff_row_cc` inline gutter）
   ```python
   num_str = (
       " " * gutter_width
       if line_num is None
       else str(line_num).rjust(gutter_width, "0")
   )
   ```

**测试：**
- `tests/externals/test_highlighted_code.py` — 加跨 99/100 边界用例
- `tests/externals/test_diff.py` — 加跨 99/100 边界用例
- 两处都要有一个 "1 位宽度不补零" 的回归测试

**参考：**
- 前一个任务：`07-23-long-code-line-wrap`（已 commit b2ca2b2，重构成单 Text+ANSI per line）
- Jarvis 端复现脚本：`D:/Projects/Jarvis/.trellis/workspace/claude/diag_gutter.py`
