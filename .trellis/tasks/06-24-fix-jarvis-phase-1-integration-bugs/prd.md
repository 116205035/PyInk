# Fix Jarvis Phase 1 Integration Bugs

## Goal

修复 Jarvis Phase 1 集成 PyInk 时发现的三个 bug，让 inline TUI 模式在真实使用场景（Jarvis 的 Static + TextInput + 动态面板组合）下可用。这三个 bug 都属于"PyInk 自己接管渲染时与系统/终端行为冲突"或"layout 语义错位"，不修的话 inline 模式没法作为 Jarvis 的底层。

## What I already know

### Issue 1 — 系统 cursor 不隐藏（inline 模式）

- 现象：inline 模式启动后系统光标在 frame 起点持续闪烁，和 TextInput/Spinner 自己画的 block cursor 重影。
- 根因（已确认）：`src/ink/render/terminal.py:360` 的 `_HIDE_CURSOR` 只在 `enter_alternate_screen()` 里发出；`src/ink/render/pipeline.py:200-201` 只在 `alternate_screen=True` 时才调用。inline 模式（默认）从来不发 `?25l`。
- 嫌疑修复位置：`pipeline.py:render()` 在 mount 之后、`alternate_screen` 分支之前发 `_HIDE_CURSOR`，并注册 `on_exit` 恢复 `_SHOW_CURSOR`。

### Issue 2 — TextInput 空内容时 cursor 位置错

- 现象：placeholder 显示后 cursor 跑到 placeholder 末尾，而不是 column 0。
- 根因（已确认）：`src/ink/externals/text_input.py:1307-1312`：

  ```python
  if not cur_value and placeholder:
      cursor_cell = _cursor_cell(cursor_style, char="", cursor_color=cursor_color)
      return [apply_style(placeholder, dimColor=True) + cursor_cell]
  ```

  `cursor_cell` 拼在 placeholder 之后 → 可见列 = `len(placeholder)`。
- 嫌疑修复方向：把 cursor 渲染在 column 0；具体视觉（block 反相首字符 / bar 在前 / 其他）待定。

### Issue 3 — Layout 高度不能自适应（核心设计问题）

- 现象：`render(tree)` 不传 `rows` 时 frame 占满整个终端高度，Static 写入时屏幕滚动、Static 内容被挤出视野。Jarvis 当前 workaround 是 `render(tree, rows=10)` 死值。
- 根因（已确认）：
  1. `pipeline.py:172-175`：`rows is None` 时 `_clamp_dimension` 返回 `actual_rows = terminal.rows`（比如 30），写入 `RenderOptions.rows`。
  2. `instance.py:419-423` `_resolve_rows()` 直接透传给 `layout()`。
  3. `flex.py:723-734` `layout_root`：只要 `rows is not None`，`avail_h_mode = "exactly"`，根 Box 被强制撑满到 `terminal.rows`。
- `_paint_initial`（`diff.py:60-72`）已经按 `new_frame` 实际行数算 cursor-up，所以只要 layout 不再强撑，frame 就只占实际高度。

## Assumptions (temporary)

- 三个 bug 在同一个 task 里修复（都是 Jarvis Phase 1 的阻塞项），但要确认是否拆 PR。
- Issue 1/2 是 localized patch；Issue 3 涉及 `RenderOptions` / `layout_root` 的契约变化。
- 修复后 Jarvis 侧的 `rows=10` workaround 可以移除。

## Decision (ADR-lite)

### Issue 3 — `rows` 统一为 max-rows 语义

**Context**: `render(tree)` 不传 rows 时 frame 强撑整屏，导致 Static 写入时滚动。根因是 `rows` 同时承担了"viewport 上限"和"frame 强制高度"两种语义。

**Decision**: 统一为 max-rows 语义。`render(tree)` 默认 max-rows=terminal.rows；`render(tree, rows=N)` 同样是 max-rows。layout_root 在 `rows is not None` 时把 `avail_h_mode` 从 `"exactly"` 改成 `"at-most"`，frame 高度 = min(实际 fit-content, max-rows)。

**Consequences**:
- 匹配 ink (TypeScript) 的行为。
- Jarvis 可以删掉 `rows=10` workaround（不再需要）。
- **Breaking**：如果有 caller 依赖 `render(tree, rows=N)` 强撑 N 行的行为，会改成 fit-content。需要在实现前 grep 确认没有内部调用方依赖旧行为；如有则迁移。
- `<Box height={N}>` 的 exactly 语义保留，caller 仍然可以显式 pin 高度。

### Issue 2 — cursor 落在 placeholder 首字符

**Context**: 空内容时 cursor 跑到 placeholder 末尾，违反"cursor 在 column 0"的心智模型。

**Decision**: placeholder 作为虚拟内容参与 cursor 渲染。block 风格 → 反相 placeholder 首字符；bar 风格 → 在 placeholder 前画反相空格；underline → 给首字符加下划线。复用现有 `_build_displayed_line` 走宽度对齐路径，避免 CJK placeholder 错位。

**Consequences**:
- 视觉与 ink (TypeScript) 一致。
- placeholder 长度不再影响 cursor 列位置。
- 实现：把 placeholder 当作"虚拟 value"走正常的 cursor 渲染分支，而不是当前 `placeholder + cursor_cell` 拼接。

## Open Questions

- 三个 bug 一个 PR 还是拆 PR？（倾向：一个 task 三个 commit 一个 PR，便于 review 但一起 ship）

## Requirements (evolving)

- inline 模式默认隐藏系统 cursor，退出时恢复。
- TextInput 空内容 + placeholder 时，cursor 落在 placeholder 首字符（block 反相 / bar 在前 / underline 加下划线）。
- `render(tree)` 不传 `rows` 时 frame 高度 = min(layout 实际 fit-content, terminal.rows)，不再强撑整屏。
- `render(tree, rows=N)` 语义从"强制 N 行"改为"上限 N 行"。
- `<Box height={N}>` 的 exactly 语义保留。
- 保持 alternate_screen 模式行为不变。

## Acceptance Criteria (evolving)

- [ ] `python examples/static/static.py` inline 模式下看不到系统 cursor 闪烁；Ctrl+C / unmount 后系统 cursor 恢复。
- [ ] TextInput 空 value + placeholder 时 cursor 位于 column 0；输入字符后 cursor 跟随 value 末尾，placeholder 消失。
- [ ] `render(App())`（不传 rows）frame 高度 = 实际 layout 行数，Static 上方有空间，不出现滚动条。
- [ ] `render(App(), rows=10)` 在内容不足 10 行时 frame 高度 = 实际内容行数（不再强撑 10 行）；内容超过 10 行时 frame 高度 = 10。
- [ ] Jarvis 侧移除 `rows=10` workaround 后正常工作。
- [ ] 现有 alternate_screen 测试用例继续通过。
- [ ] 新增单测覆盖三个修复路径。

## Definition of Done

- 单测覆盖三个 bug 的修复路径（cursor hide / placeholder cursor / layout fit-content）。
- Lint / typecheck / 现有测试全绿。
- 在真实终端跑一次 Jarvis（或最小 repro）确认无回归。
- 如果 `RenderOptions` 公共契约变化，更新 docstring / changelog 注释。

## Out of Scope

- Vim mode / 输入历史 / 其他 TextInput 新功能。
- layout 引擎重构（只改 root 的 avail_h 语义，不动 flex 主流程）。
- alternate_screen 模式的任何改动。

## Technical Notes

### 关键文件

- `src/ink/render/pipeline.py` — `render()` 入口
- `src/ink/render/terminal.py` — `_HIDE_CURSOR` / `_SHOW_CURSOR` 常量、`enter_alternate_screen()`
- `src/ink/render/instance.py` — `_paint_now` / `_clear_frame_for_exit` / `unmount`
- `src/ink/render/diff.py` — `_paint_initial`
- `src/ink/externals/text_input.py` — `_render_lines()` placeholder 分支
- `src/ink/layout/flex.py` — `layout_root()` 的 `avail_h_mode` 判定（第 734 行）

### Research 结论（Issue 3 breaking risk）

- `tests/render/test_clamp.py` 只断言 `inst.options.rows == N`（input 字段），不断言 frame 高度 = N。改 avail_h_mode 语义后 `inst.options.rows` 仍然是 N，测试通过。
- `tests/render/test_pipeline.py` / `test_integration.py` 都用 `"content" in output` 内容断言，不依赖 frame 撑满 N 行。
- `tests/layout/test_flex.py:810-855` 已经为 inner `<Box maxHeight=N>` 建立了 "cap, not fill" 语义（来自 06-23 任务），我们把 root `rows=N` 对齐到同语义。
- Jarvis 的 `render(tree, rows=10)` workaround 在新语义下含义变成"上限 10 行"，比旧行为更宽松，移除 workaround 也能正常工作。

### 实现路径

**Issue 1**（pipeline.py）：

- `render()` 在 `inst._mount_initial(tree)` 之后、`alternate_screen` 分支之前：写 `_HIDE_CURSOR` + flush。
- 注册 `inst.on_exit(...)` 写 `_SHOW_CURSOR` + flush。
- 顺序确认：`unmount()` 的 finally 里 `_clear_frame_for_exit()` 在 `exit_callbacks` 遍历之前，所以 cursor 恢复写在 frame 清理之后——符合"先清屏再恢复 cursor"的顺序。alt 模式下 `exit_alternate_screen` 会写一次 `_SHOW_CURSOR`，on_exit 再写一次，幂等无害。

**Issue 2**（text_input.py `_render_lines` 的 placeholder 分支）：

- 把 `placeholder + cursor_cell` 改为 cursor 落在 placeholder 首字符。
- block 风格：cursor_cell 包含 `placeholder[0]`，剩余 `placeholder[1:]` 用 dim 包裹。
- bar 风格：`cursor_cell + dim(placeholder)`。
- underline 风格：同 block 路径，只是 SGR 是 underline 而不是 inverse。
- 空 placeholder 边界：直接返回 `_cursor_cell(char="")`。
- 不走 `_build_displayed_line`（CJK placeholder 也能工作——cursor 在 column 0，无需宽度对齐）。

**Issue 3**（flex.py:layout_root）：

- 第 734 行 `avail_h_mode` 判定：移除 `or rows is not None` 条件。
- 修改后：`"exactly" if style.height is not None else "at-most"`。
- `<Box height=N>` 仍然 exactly（caller 显式 pin）；`render(tree, rows=N)` 改为 at-most（cap）；`render(tree)` 自动检测 terminal.rows 后也走 at-most。
- 更新 `layout_root` docstring 解释新语义。

### 已知约束

- PRD Decision 3：inline 模式不能用 `\x1b[2J`，cursor 隐藏/恢复走 `?25l/h`。
- pipeline.py 现有 `_clamp_dimension` 在 TTY 下把 caller 传的 rows 钳到 terminal.rows，这块逻辑保留——它影响 `inst.options.rows` 的值，不改 layout 的 avail_h_mode 语义。
