# ime-caret-anchor: park physical cursor at software caret

## Goal

中文 IME 组合预览（preedit 拼音串）显示在 input 正文**下方一行**而非正文行内。根因：PyInk 隐藏物理光标、自绘 SGR 假光标，但 IME preedit 由终端模拟器绘制并锚定在**物理光标**所在单元格；PyInk 的 bottom-park 约定把物理光标停在帧最后一行行首（input 下方通常是 hint/status 行）。

目标：每次 paint 后把物理光标停靠到焦点输入组件的软件光标（caret）单元格，使 IME 预览与视觉光标重合（对齐 Claude Code 的体验）。

## What I already know

* IME preedit 由终端绘制；组合期间应用收不到任何输入字节，应用唯一的杠杆是物理光标位置。
* 物理光标隐藏（DECTCEM `CSI ?25l`，pipeline.py:229）只影响可见性，位置仍存在 → IME 仍锚定（用户实验佐证；T1 研究待确认）。
* **Bottom-park 约定**（diff.py:17-31）：`write_diff` / `_paint_initial` / `_repaint` 后光标停在帧最后一行行首（diff.py:323-326）。设计动机是 1-2 行 micro-move + 规避 Windows Terminal viewport-yank bug（microsoft/terminal#14774）。
* TextInput 自绘假光标（SGR 反显 cell），`_render_lines` 已算出 `cursor_row_idx`（payload 内 display row）和 `_cursor_visible_column`（可见列）。**但组件不知道自己在帧里的绝对坐标** — 需要 layout 回报。
* 已有先例通道：Text 的 `scroll_offset` 装饰 prop 由 layout painter 解析（render_layout.py:626-659、flex.py:561 decoration props 列表）。
* `_paint_now`（instance.py:527+）调 `write_diff` / `repaint_frame` — 停靠移动应追加在这里，不侵入 diff.py 内部约定。
* Claude Code 之所以正常：其 input 是帧最后一行，bottom-park 恰好落在 input 行。

## Assumptions（已验证）

* ~~Windows Terminal / conhost 的 IME 锚定隐藏光标的当前位置~~ **T1 已证实**：WT 锚点链路（`src/tsf/Implementation.cpp` 的 `GetTextExt` → `TsfDataProvider::GetCursorPosition` → `ControlCore::CursorPosition`）不检查光标可见性，DECTCEM 纯显示开关；WezTerm / Alacritty / kitty / iTerm2 同为「锚点=光标格」。
* 不存在显式设置 IME 锚点的 VT 序列（T1）→ 移动物理光标是唯一手段，方案成立。
* paint 后追加一次光标移动无副作用（业界同构实现 Ink/Textual 已验证）；micro-move 不触发 WT viewport-yank。

## Open Questions

* （机制问题已决策，见 Decision；research T1/T2 结论回来后复核 Assumptions）

## Requirements (evolving)

* paint 完成后物理光标停靠在焦点 TextInput 的软件光标单元格（保持隐藏状态）。
* 帧内无 caret 时维持现状 bottom-park（零行为变化）。
* inline 与 alternate screen 两种模式都生效。
* `scroll_offset` 视口滚动时锚点跟随**可见** caret 行（不可见时回落 bottom-park）。

## Acceptance Criteria (evolving)

* [ ] 单测：paint 输出字节流尾部含把光标移到 caret (row,col) 的序列（input 下方有 hint 行的场景）。
* [ ] 单测：无 caret 组件的帧字节流与现状完全一致（回归保护）。
* [ ] 手动：Windows Terminal + 微软拼音，input 下方有 hint 行的布局中 preedit 出现在正文行内。
* [ ] 存量基线不回归（对照 memory: project_preexisting_check_failures）。

## Definition of Done

* 新增/更新单测；ruff / mypy 相对存量基线无新增问题。
* text_input.py docstring、diff.py cursor-convention 段落更新（bottom-park → caret-park 优先）。

## Out of Scope

* 应用侧渲染 IME 组合串内容（不可能：组合期间应用收不到输入）。
* 其它焦点组件（Select 等）的实际接入 — 机制可复用即可，不逐一接线。
* macOS / Linux 终端实测（无环境；机制与平台无关）。

## Technical Notes

* 关键文件：
  * `src/ink/render/diff.py` — bottom-park 约定（17-31、323-326）
  * `src/ink/render/instance.py:527` — `_paint_now` 追加点
  * `src/ink/layout/render_layout.py:534,626` — Text painter、`scroll_offset` 先例
  * `src/ink/layout/flex.py:561` — Text decoration props 白名单
  * `src/ink/externals/text_input.py:1090` — `_render_lines`（cursor_row_idx / visible col 已有）
* 用户已确认症状；`\x1b[?25h` 临时显示物理光标可复现锚点位置。

## Research References

* [`research/ime-anchor-terminal-behavior.md`](research/ime-anchor-terminal-behavior.md) — **WT IME 锚点=VT 光标格且不检查可见性**（TSF 源码级证据）；无显式锚点 VT 序列；佐证 issue microsoft/terminal #20040、#20318。
* [`research/tui-cursor-parking-prior-art.md`](research/tui-cursor-parking-prior-art.md) — **Ink v6.7.0 `useCursor().setCursorPosition({x,y})`（PR #866）与方案 A 完全同构**，官方目的就是 CJK IME composition；Textual `app.cursor_position` 同款；ratatui `Frame::set_cursor_position` 同类。业界已验证此架构。

## Decision (ADR-lite)

**Context**: IME preedit 锚定物理光标；PyInk bottom-park 使锚点落在 input 下方。组件自绘假光标但不知道自己的帧内绝对坐标，需要一条从组件到渲染器的 caret 坐标通道。

**Decision**: 方案 A — 通用精确锚点通道（用户 2026-08-20 选定）：

1. Text 增加 `caret` 装饰 prop（payload 内 (row, col)，`scroll_offset` 同款通道）；
2. layout painter 解析该 prop，结合 Text 在帧内的绝对起点 (x0, y0)（含 scroll_offset 窗口与折行修正），把 anchor = (y0+row, x0+col) 回报给 Instance；
3. `_paint_now` 在 `write_diff` / `repaint_frame` 之后追加一次光标移动（CUU/CUD + 列定位）到 anchor 单元格，物理光标保持隐藏；
4. 帧内无 caret 声明时零变化（维持 bottom-park）。

**Consequences**:
* 改动面跨 layout + render 两层（flex.py decoration 白名单、render_layout.py painter、instance.py _paint_now、text_input.py 上报），是本任务主要成本；
* 精确到列，IME 预览与插入点完全重合（对齐 Claude Code 体验）；
* 未来 Select / 其它焦点组件声明 caret 即可复用，无需再动渲染层；
* 风险：追加移动必须尊重 `available_rows` 上限（不爬进 scrollback）；每次 paint 多 ~6-10 字节 stdout，可忽略。

**来自 Ink PR #866 的实现约束（必须吸收，否则引入回归）**：

1. **paint 序列 = 先归位再停靠**。diff 引擎（`_repaint` / `_paint_initial` / `repaint_frame`）假设 paint 开始时光标在 bottom-park（帧最后一行行首）。因此 `_paint_now` 的顺序必须是：归位 bottom-park → `write_diff` / `repaint_frame` → 停靠 caret。绝不能只停靠不归位，否则下一帧 diff 的光标数学全错（Ink 同款：擦写前先移回帧底）。
2. **停靠后是否显示光标**：Ink 停靠后用 `?25h` 显示物理光标。**T1 已证实 WT 锚定不检查可见性** → PyInk 保持隐藏（`?25l`），自绘假光标独占视觉，不叠加真光标样式差异。
3. （可选加分项）Ink 配了 Synchronized Update Mode（`CSI ?2026h/l`）防 tmux 下中间态撕裂。PyInk 视实现成本决定是否纳入，不作为 MVP 阻塞项。
