# Research: 终端 IME 组合窗锚点行为（WT/conhost + 其他终端）

- **Query**: Windows Terminal / conhost 的 IME preedit/candidate 锚点是否固定在物理光标所在单元格；DECTCEM 隐藏是否影响锚点；有无 VT 序列可显式设置 IME 锚点；相关已知 issue；其他终端对照
- **Scope**: external（源码 + GitHub issues + 文档）
- **Date**: 2026-08-20

## 问题 1：WT/conhost 的 IME 组合窗锚点位置，DECTCEM 隐藏是否影响？

**结论：是。Windows Terminal 的 IME 组合串/候选窗锚定在终端（VT）光标所在单元格的屏幕矩形，且锚点计算完全不检查光标可见性 —— DECTCEM (`CSI ?25l`) 只是渲染层面的显示开关，不影响锚点。**

源码证据（microsoft/terminal, main 分支）：

1. TSF 侧锚点来源 — `src/tsf/Implementation.cpp`（`ITfContextOwner::GetTextExt`，候选窗定位回调）：

   ```cpp
   // The returned rectangle is used to position the TSF candidate window.
   STDMETHODIMP Implementation::GetTextExt(LONG acpStart, LONG acpEnd, RECT* prc, BOOL* pfClipped) noexcept
   {
       if (prc)
       {
           *prc = _provider ? _provider->GetCursorPosition() : RECT{};
       }
       ...
   }
   ```

   即：TSF 询问任意 range 的屏幕矩形时，WT 一律返回"光标单元格"矩形（不区分 acpStart/acpEnd）。

2. Provider 实现 — `src/cascadia/TerminalControl/TermControl.cpp` 中 `TsfDataProvider::GetCursorPosition()`：

   ```cpp
   const auto cursorPosition = core->CursorPosition();
   const auto fontSize = core->FontSize();
   const auto left = clientRect.left + ... + cursorPosition.X * fontSize.Width;
   const auto top  = clientRect.top + ... + cursorPosition.Y * fontSize.Height;
   ```

   纯粹由 `CursorPosition()`（视口相对光标格坐标）× 字体尺寸换算屏幕像素矩形。

3. `ControlCore::CursorPosition()`（`ControlCore.cpp:2047`）返回 `_terminal->GetViewportRelativeCursorPosition()` —— 这是逻辑光标位置，整条链路上没有任何对 DECTCEM / `cursor.IsVisible()` 的检查。光标隐藏只影响 Atlas 渲染器是否画光标，不影响 TSF 锚点。

4. 组合串（preedit）本身也是覆盖式画在光标列：issue #20040（见问题 3）确认 `Renderer::_PaintBufferOutput` 用 `ReplaceText` 把 tsfPreview 覆盖写在光标列。

这直接支持 PyInk 的诊断与修复方案：**把物理光标（bottom-park）改为每帧移动到软件光标位置（CUP 后再保持隐藏），IME 锚点就会跟着软件光标走** —— 因为 WT 锚点跟随的是 VT 光标位置，与显示与否无关。

conhost：老 conhost 走 IMM32/conime 浮动组合窗，同样以光标位置为参照（历史上 #459 "IME UI does not follow the cursor" 即 conhost 时代问题）；现代 conhost 在 ConPTY/DefTerm 场景下与 WT 共用同一 TSF 栈。

## 问题 2：应用侧有无 VT 序列显式设置 IME 锚点？

**结论：没有。** 没有任何标准或事实标准的 VT 转义序列可以显式指定 IME 组合窗锚点。VT 标准里与光标相关的只有位置（CUP/CHT/CUP 等）、样式（DECSCUSR `CSI Ps SP q`）、显示（DECTCEM `CSI ?25l/h`）、Locking Shift 等，均无 IME 语义。

- 唯一的间接机制就是移动 VT 光标（CUP/VPA/CHT 等）：所有主流终端的 IME 锚点都跟随光标位置（见问题 1、4），因此"移光标"是事实上的通用锚点控制手段。
- 不存在类似 kitty keyboard-protocol 那样的 IME-anchor escape。kitty 有 IME 相关选项（`wayland_enable_ime` 等）也是终端本地配置，非应用可控转义序列。
- WezTerm 的 `use_ime`、`ime_preedit_rendering` 同样是终端配置项，非应用侧协议。

## 问题 3：已知 WT IME 锚点/组合串渲染 issue（microsoft/terminal）

| Issue | 状态 | 摘要 |
|---|---|---|
| [#459 / PR #1919](https://github.com/microsoft/terminal/pull/1919) | closed | 最初实现："IME UI does not follow the cursor in Windows Terminal"。引入 TSFInputControl（现已被新 `src/tsf` + TsfDataProvider 架构取代），锚点即光标位置 |
| [#20318](https://github.com/microsoft/terminal/issues/20318) | open | "The input line is obscured by the Chinese IME candidate box" —— 中文 IME 候选框遮挡输入行，与 PyInk 场景（候选框/组合窗出现在非预期行）直接相关 |
| [#20323](https://github.com/microsoft/terminal/issues/20323) | closed | 同标题重复报告 |
| [#20040](https://github.com/microsoft/terminal/issues/20040) | closed | "Korean IME: Composing character visually hides the character to its right"（v1.24+）。正文明确根因：`Renderer::_PaintBufferOutput` 把 tsfPreview 用 `ReplaceText` 覆盖写在**光标列**；#19738 让韩文 IME 首次走 TSF 内联渲染（此前 IMM32 浮动窗）。确认组合串锚定光标单元格 |
| [#20038](https://github.com/microsoft/terminal/issues/20038) / [#20039](https://github.com/microsoft/terminal/issues/20039) | closed | 组合期间按方向键插入位置错误 / 组合字符移到错误位置 —— 均为锚点与光标列耦合的表现 |
| [#20041](https://github.com/microsoft/terminal/pull/20041) | closed | "Fix Korean IME overlay hiding characters to the right of cursor" |
| [#18354](https://github.com/microsoft/terminal/issues/18354) | open | "Suggestion ghost text gets left behind when using IME" |
| [#19670](https://github.com/microsoft/terminal/issues/19670) / [#19722](https://github.com/microsoft/terminal/issues/19722) | closed | 搜狗 / CorvusSKK 候选窗在 1.23/1.24 不可见（渲染/TSF 集成 bug） |
| [#17972](https://github.com/microsoft/terminal/issues/17972) | open | 日文 IME 输入时文字颜色随条件变化（SGR 与组合串着色交互） |
| [#19491](https://github.com/microsoft/terminal/issues/19491) | open | "Indicate status of IME by changing cursor color" —— 说明 WT 团队也把光标视为 IME 状态载体 |

没有找到专门针对"DECTCEM 隐藏光标时锚点行为"的 issue —— 结合源码（锚点链路不读可见性），可认定隐藏不影响锚点，属于未写入文档的实现事实。

## 问题 4（次要）：其他终端的 IME 锚点处理

- **WezTerm**：有 `use_ime`（Windows 上控制 TSF/IMM32 输入路径）与 `ime_preedit_rendering` 配置；锚点同样跟随光标格。已知 Windows 锚点 bug：[#7780](https://github.com/wezterm/wezterm/issues/7780)（副屏负坐标时候选窗钉死主屏，说明它也是用光标像素矩形喂给 IME）。文档：https://wezterm.org/config/lua/config/use-ime.html
- **kitty**：macOS 上 preedit 由 IME 自绘；Linux/Wayland 有 `wayland_enable_ime` 选项，文本输入协议（zwp_text_input）中锚点由终端报告光标矩形。无应用侧锚点转义序列。
- **Alacritty**：通过 winit 支持 IME，锚点跟随光标；见 [#7341](https://github.com/alacritty/alacritty/issues/7341) "Update IME position opportunistically"（讨论光标移动后何时刷新 IME 位置）、[#8709](https://github.com/alacritty/alacritty/issues/8709)（preedit 渲染与提交文本不一致）。
- **iTerm2**：macOS 上组合串内联渲染于光标处（candidate window 由 NSTextInputClient 的 firstRectForCharacterRange 返回，通常即光标矩形）。

**共性结论**：所有主流终端都是"IME 锚点 = VT 光标单元格矩形"，无一提供应用侧显式锚点协议；移动 VT 光标是唯一通用手段。WT 的独特之处（对 PyInk 重要）在于：锚点用逻辑光标位置，隐藏光标不解除锚定。

## Caveats / Not Found

- 未找到微软官方文档明确写"锚点不随 DECTCEM 改变"——此结论来自 main 分支源码阅读（`src/tsf/Implementation.cpp` GetTextExt → `TermControl.cpp` TsfDataProvider::GetCursorPosition → `ControlCore.cpp:2047`），属实现行为，未来版本可能变化。
- conhost（经典控制台主机、非 WT 承载）在旧 IMM32 路径下的精确锚点逻辑未逐行核实，仅由 #459 历史推断其同样以光标为参照。
- WezTerm/kitty/iTerm2 的锚点描述基于配置项与 issue 佐证，未逐一读其源码。
