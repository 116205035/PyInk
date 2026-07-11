# pyink-markdown-render-polish

## Goal

提升 PyInk 的 Markdown 渲染能力，对标 claude-code 的渲染细节，使 PyInk 作为通用 TUI 框架在表格、标题、行内代码、blockquote、间距、主题语义化等方面达到生产级。改造**只动 PyInk**，不耦合 Jarvis；Jarvis 侧的 theme 覆盖留作后续任务。

## What I already know

### PyInk 现状（D:\Projects\PyInk）

- 渲染入口：`src/ink/externals/markdown.py:1000-1111`（`Markdown()` 工厂）
- 默认主题：`src/ink/externals/markdown.py:108-145`（`DEFAULT_MARKDOWN_THEME`）
- 表格：`markdown.py:694-754`，无边框，header 仅 bold，单元格内不支持 inline 样式，注释里写明 "basic column alignment, no inline styling"
- 标题：`markdown.py:458-485`，h1-h6 用彩虹色（magenta/yellow/green/cyan/blue/gray）+ bold
- 行内代码：`markdown.py:364-368`，默认 red，不继承外层 bold/italic
- Blockquote：`markdown.py:576-598`，2 空格缩进 + dim，无竖线
- 代码块：`src/ink/externals/highlighted_code.py`，Pygments 可选 + fallback
- 主题：硬编码颜色名，无语义层
- 缓存：LRU 64，全量重解析（注释承认 "Re-parsing the whole document is the expected cost"）

### claude-code 对标（D:\Projects\github\claude-code）

- 表格：`src/components/MarkdownTable.tsx` + `src/utils/markdown.ts`，完整 `┌─┬─┐` 边框 + 列分隔 + header 居中 + 单元格 inline 样式 + 响应式收缩 + 降级 key-value
- 标题：`markdown.ts:105-136`，h1 bold+italic+underline，h2 bold，主题文本色，标题后 2 空行
- 行内代码：`markdown.ts:88-91`，主题 `permission` 语义色
- Blockquote：`markdown.ts:58-71`，`▎` (U+258E) dim 竖线 + 斜体
- 主题：`theme.ts:4-89`，`text/permission/success/error/warning` 语义键，6 套主题（light/dark/ansi/daltonized）
- 缓存：LRU 500，stable prefix 流式优化

## Assumptions (temporary)

- PyInk 是独立通用框架，不能 import jarvis
- DEFAULT_MARKDOWN_THEME 改动需考虑向后兼容（PyInk 可能有其他使用者）
- 主题语义键用中性名（accent/secondary/muted）而非业务名（permission）

## Open Questions

（全部已解决，见 Decision）

## Requirements (evolving)

### P0（渲染能力缺失）

- 表格：支持边框开关 + 边框样式（single/rounded/double/none）+ `:---:` 对齐标记 + header/body 分离渲染 + 单元格 inline 样式 + 响应式收缩
- 主题语义化键：引入 `text/accent/secondary/muted/border` + `success/error/warning/info` 语义层（markdown 主要用前 5 个）
- 标题样式可控：`h{n}_underline` / `h{n}_italic` 等键，不写死彩虹色

### P1（细节补齐）

- 行内代码继承外层 inline 样式（bug 修复）
- Blockquote 左竖线能力（`quote_bar_char` / `quote_bar_color` 可配置）
- 块间距规则（`spacing_before/after_*` 主题键）
- 列表多级标记（`1. → a. → i.`，可关闭）

### P2（增强，本任务不做）

- 流式 stable prefix 优化（性能）—— 拆为独立后续任务

## Acceptance Criteria (evolving)

- [ ] 表格在含 `:---:` 标记时按对齐渲染
- [ ] 表格单元格内 `**bold**` / `` `code` `` 正确渲染
- [ ] 表格在窄终端自动收缩，过窄时降级 key-value
- [ ] `Markdown(src, theme={"h1_color": "accent"})` 能覆盖默认
- [ ] `**bold `code`**` 行内代码继承 bold
- [ ] Blockquote 在 `quote_bar_char="▎"` 时显示竖线
- [ ] 所有新增主题键有默认值，默认值即为 claude-code 风格（表格有边框、quote 有竖线等）

## Definition of Done

- PyInk 单元测试覆盖新增能力
- Lint / typecheck 绿
- 不引入 jarvis 耦合
- DEFAULT_MARKDOWN_THEME 明确 break change，在 changelog/README 记录迁移说明

## Out of Scope (explicit)

- Jarvis 侧的 theme 覆盖（下一个任务）
- PyInk 的 6 套主题套件（light/dark/daltonized）—— 只提供语义键，套件由应用层定义
- markdown-it-py 升级
- 新 markdown 元素支持（如 footnotes、definition lists）
- 流式 stable prefix 优化（拆为独立后续任务）

## Decision (ADR-lite)

### 范围决定

**Context**: PyInk markdown 渲染相比 claude-code 缺失多项能力，需决定本次改造范围。
**Decision**: 本任务做 P0（能力缺失）+ P1（细节补齐）+ 多级列表标记。流式 stable prefix 优化拆为独立后续任务。
**Consequences**: 渲染质量一次改到位；流式性能优化风险隔离，单独测试；多级列表虽是 P2 但工作量小，纳入本任务避免另开任务开销。

### 默认策略决定

**Context**: 新增能力（表格边框、blockquote 竖线、行内代码继承、多级列表、间距）的默认开关策略影响向后兼容性。
**Decision**: 新能力默认开启。DEFAULT_MARKDOWN_THEME 改为 claude-code 风格默认值（表格有边框、blockquote 有竖线、行内代码继承外层样式、标题用主题文本色而非彩虹色）。
**Consequences**: PyInk 现有使用者渲染效果会变（break change）；但 PyInk 作为新框架使用者有限，break 成本低；Jarvis 侧无需额外配置即可获得现代渲染效果。在 changelog 中明确记录 break。

### 语义键命名决定

**Context**: 主题语义键命名影响 PyInk 整个主题系统，不只是 markdown。
**Decision**: 引入中性 + 状态色混合集：`text/accent/secondary/muted/border` + `success/error/warning/info`。markdown 渲染主要用前 5 个，状态色为未来其他组件（Button/Toast/Status）预留。
**Consequences**: 与 claude-code 的 `permission` 业务命名脱钩，保持通用框架中立性；状态色键先定义但不强制使用，避免 YAGNI 又预留扩展点。

## Technical Notes

- PyInk 仓库：`D:\Projects\PyInk`
- claude-code 对标：`D:\Projects\github\claude-code\src\components\MarkdownTable.tsx`、`src\utils\markdown.ts`
- PyInk 渲染基于 `markdown-it-py` + 自定义 ANSI 渲染
- 表格响应式收缩可参考 claude-code `MarkdownTable.tsx` 的 min/ideal width 算法
