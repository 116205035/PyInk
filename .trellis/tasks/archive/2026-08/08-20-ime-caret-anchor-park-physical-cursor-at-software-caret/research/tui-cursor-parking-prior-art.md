# Research: TUI 框架"物理终端光标停靠到组件声明的光标位置"先例

- **Query**: 主流 TUI 框架渲染后物理光标停靠策略；是否有"组件声明光标位置、渲染器停靠"先例；IME preedit 锚点处理
- **Scope**: external（官方源码 / 文档 / release notes / issues）
- **Date**: 2026-08-20
- **本地证据副本**: 本目录 `_src/` 下保存了关键源码文件（ink-cursor-helpers.ts、ink-use-cursor.ts、ink-cursor-ime.tsx、ink-log-update.ts、textual-input.py 等）

## 结论速览

**PyInk 计划的方案（组件回报软件光标帧内坐标，渲染器每帧把物理光标停靠到该单元格）正是 Ink v6.7.0 (2026-02) 为解决 CJK IME preedit 锚点问题而引入的官方 API 的做法**，Textual 也是同构设计（Input 组件把屏幕偏移写给 app.cursor_position，driver 停靠物理光标）。唯一重要差异：**Ink 是把物理光标"显示"在插入点（DECTCEM on），而不是隐藏**——preedit 由终端画在物理光标处，与物理光标是否可见无关，但显示光标同时解决了"软件光标反显与物理光标重叠"的视觉问题，值得 PyInk 考虑。

---

## 1. Ink (vadimdemedes/ink) — 最直接的先例

### 1.1 背景：Ink 4/5 的旧行为

Ink 4/5（Claude Code 早期所用）：

- 渲染基于 log-update 思路：每帧从帧尾 `cursorUp(n)` 回到帧首，逐行擦除重写，写完后物理光标自然停在**帧最后一行行尾**（bottom-park）。
- 全程 `cliCursor.hide()`（DECTCEM off，`ansi-escapes` 的 `\x1b[?25l`）。
- 因此旧版 ink-text-input / Claude Code 的 IME preedit 之所以"看起来在正文行"，**纯属巧合**：输入框恰好是帧的最后一行，物理光标 bottom-park 的位置就在输入行附近。输入框一旦不在最后一行（例如上方还有 spinner/输出），preedit 就会锚错位置——这正是后来 PR #866 要解决的问题。

### 1.2 Ink v6.7.0：官方 "IME cursor positioning API"

Release notes（v6.7.0, 2026-02-10）：

> - Add [IME cursor positioning API](https://github.com/vadimdemedes/ink?tab=readme-ov-file#usecursor) (#866)
> - Add support for synchronized update ... This fixes flickering

PR [#866 "Add IME cursor positioning and Synchronized Update Mode"](https://github.com/vadimdemedes/ink/pull/866)（其前身是 #846 by @jedipunkz，同一问题的另一种实现）明确说了动机：

> Expose a `useCursor()` hook so apps can position the terminal cursor after each render — **enabling CJK IME composition characters to appear at the correct location**

设计要点（源码级）：

1. **组件声明坐标**：`useCursor()` 返回 `setCursorPosition({x, y})`，坐标是相对 Ink 输出原点的 0-based 帧内坐标。README 明确建议用 `string-width` 计算 x 以处理 CJK/emoji 宽字符。示例见 `examples/cursor-ime/cursor-ime.tsx`（本地副本 `_src/ink-cursor-ime.tsx`）：`setCursorPosition({x: stringWidth(prompt + text), y: 1})`。
2. **渲染器负责停靠**：`src/cursor-helpers.ts`（本地副本）中的
   - `buildCursorSuffix(bottomLine, cursorPosition)`：帧写完后从帧尾 `cursorUp` + `cursorTo(x)` + **`showCursorEscape` (`\x1b[?25h`)** —— 即每帧末尾把物理光标移到组件声明的单元格并**显示**。
   - `buildReturnToBottom(...)`：下一帧擦除重写前，先把光标从上次停靠位置移回帧底（`cursorDown` + `cursorTo(0)`），再执行 eraseLines——保证擦除逻辑仍以"光标在帧底"为前提。
   - `buildCursorOnlySequence(...)`：**cursor-only 快路径**——帧内容不变、仅光标移动时，跳过擦写，只发重定位序列。
3. **脏标记与生命周期**：`src/hooks/use-cursor.ts` 用 `useInsertionEffect` 在 commit 阶段把位置传给 log-update，组件卸载/unmount 时重置为 `undefined`（undefined = 回到隐藏光标）。
4. **Synchronized Update Mode（CSI ? 2026 h/l）配套**：PR 特别提到用 SUM 包裹每帧输出，防止 tmux/Zellij 读到渲染中间态的光标位置。这对 PyInk 是有价值的配套手段。

关键差异 vs PyInk 计划：**Ink 停靠后显示物理光标（?25h），而不是保持隐藏**。README 原话：`Pass undefined to hide the cursor.` 即"设了位置 = 显示"。对 IME 而言 preedit 锚的是物理光标位置（与可见性无关），但显示真实光标可以替代/叠加软件反显光标，避免双重光标错位。

来源：

- https://github.com/vadimdemedes/ink/pull/866
- https://github.com/vadimdemedes/ink/blob/master/src/cursor-helpers.ts
- https://github.com/vadimdemedes/ink/blob/master/src/hooks/use-cursor.ts
- https://github.com/vadimdemedes/ink/tree/master/examples/cursor-ime
- https://github.com/vadimdemedes/ink/releases/tag/v6.7.0
- README `useCursor()` 一节：https://github.com/vadimdemedes/ink#usecursor

## 2. Textual — 同构设计（组件算屏幕偏移，driver 停靠）

Textual 有完整的一等终端光标 API：`App.cursor`（光标形状/可见性，Cursor.BLOCK/UNDERLINE/...，None=隐藏）、`App.cursor_position`（屏幕坐标）、`Screen.cursor_position`。渲染循环每帧后由 driver（windows/linux/web）把物理光标设到该位置。

Input 组件的做法（`src/textual/widgets/_input.py`，本地副本 `_src/textual-input.py`）：

```python
# line 519 (on focus / mount) 与 line 740 (光标移动后)
self.app.cursor_position = self.cursor_screen_offset
```

即 Input 自己计算插入点的屏幕偏移（`cursor_screen_offset`，含 scroll offset），写入全局 `app.cursor_position`，driver 在下一次 flush 时停靠物理光标。这与"组件回报坐标、渲染器停靠"完全同构。

IME 相关：Textual 长期有 Windows IME 问题线（issue [#5456](https://github.com/Textualize/textual/issues/5456) VK=0 过滤丢弃 CJK composition 事件、[#5457](https://github.com/Textualize/textual/issues/5457) conhost 日文输入丢字、[#6667](https://github.com/Textualize/textual/issues/6667) Windows CJK IME 按键修复、"Fix IME pop-up issues" PR 等），主要聚焦**按键事件层**而非 preedit 锚点；preedit 锚点问题靠上述 cursor_position 机制天然缓解。

来源：

- https://github.com/Textualize/textual/blob/main/src/textual/widgets/_input.py（`self.app.cursor_position = self.cursor_screen_offset`）
- https://textual.textualize.io/guide/input/#cursor （cursor API 文档）
- https://github.com/Textualize/textual/issues/5456 , /5457 , /6667

## 3. ratatui / blessed

- **ratatui (Rust)**：提供 `Frame::set_cursor_position(Position)`（终端级 `Terminal::set_cursor_position` 也在 0.28+ 暴露）。设计就是"帧渲染时组件声明光标位置，Terminal flush 后停靠物理光标"——Helix 等编辑器靠它定位 IME。默认光标隐藏与否由应用自己通过 crossterm 控制，框架不强制。未发现官方针对 IME preedit 的专门 issue/changelog（设计上由应用处理）。来源：https://docs.rs/ratatui/latest/ratatui/struct.Frame.html （set_cursor_position）
- **blessed (Node)**：以 bottom-park + 隐藏光标的 buffer diff 渲染为主，没有等价的"组件声明光标位置"API（未验证到官方文档提及光标定位；按未确认处理，见 Caveats）。

## 4. 明确处理过 IME preedit 锚点的先例汇总

| 框架 | 处理方式 | 出处 |
|---|---|---|
| Ink >= 6.7.0 | `useCursor().setCursorPosition({x,y})`，渲染器帧尾停靠物理光标到该格并**显示**；配套 Synchronized Update Mode 防 tmux 中间态；cursor-only 快路径 | PR #866, release v6.7.0 |
| Textual | Input 每次光标变化写 `app.cursor_position = cursor_screen_offset`，driver 每帧停靠 | `_input.py:519,740` |
| ratatui | `Frame::set_cursor_position`（应用层自用，编辑器类应用用于 IME） | docs.rs |

## 对 PyInk 方案的印证与建议

1. 方案与 Ink 官方 API 完全同构，是业界验证过的正确方向（Ink 甚至直说这是为 CJK IME composition 服务的）。
2. 注意两点 Ink 的细节：
   - 停靠后是否显示物理光标：Ink 选择显示（组件设 undefined 才隐藏）。PyInk 若坚持全程隐藏，preedit 仍会锚在停靠位置（锚点=光标位置，与可见性无关），但需实测各终端（Windows Terminal / conhost / tmux）确认。
   - 擦除重写前必须先把光标从停靠点移回帧底再 eraseLines（`buildReturnToBottom` 的对应物），否则 diff/擦除假设被破坏。
3. 可选增强：cursor-only 帧的快路径（只重定位不重绘），以及 Synchronized Update Mode（CSI ? 2026）包裹帧输出，防 tmux 读到中间光标态。

## Caveats / Not Found

- blessed 未找到等价 API，属"未发现"而非"确证不存在"（未拉取其源码逐一确认）。
- raw.githubusercontent.com 在本环境超时，所有源码均经 GitHub Contents API（raw accept）获取，内容与 master 一致。
- Ink 4/5 时代"输入框恰好在最后一行所以 preedit 看起来正常"是从渲染机制推断 + PR #866 动机倒推的，未找到明确陈述此巧合的官方文档。
