# Research: 表格边框渲染选项 —— Box border 能画内部 `┬┴┼` 分隔吗？

- **Query**: `Box(borderStyle="single")` 实际画什么；支持哪些 borderStyle 值；表格内部 `┬┴┼` 分隔怎么画；仓内有没有别的组件画过类似表格边框
- **Scope**: internal
- **Date**: 2026-07-11

## Findings

### Box border 的能力边界

**文件**: `src/ink/render/ansi.py:74-155`（字符集）+ `src/ink/layout/render_layout.py:732-795`（绘制实现）

**支持的 `borderStyle` 值**（`BORDER_STYLES` dict，`ansi.py:74-155`）：

| 名称 | topLeft | top | topRight | right | bottomRight | bottom | bottomLeft | left |
|---|---|---|---|---|---|---|---|---|
| `single` | `┌` | `─` | `┐` | `│` | `┘` | `─` | `└` | `│` |
| `double` | `╔` | `═` | `╗` | `║` | `╝` | `═` | `╚` | `║` |
| `round` | `╭` | `─` | `╮` | `│` | `╯` | `─` | `╰` | `│` |
| `bold` | `┏` | `━` | `┓` | `┃` | `┛` | `━` | `┗` | `┃` |
| `singleDouble` | `╓` | `─` | `╖` | `║` | `╜` | `─` | `╙` | `║` |
| `doubleSingle` | `╒` | `═` | `╕` | `│` | `╛` | `═` | `╘` | `│` |
| `classic` | `+` | `-` | `+` | `|` | `+` | `-` | `+` | `|` |
| `arrow` | `↘` | `↓` | `↙` | `←` | `↖` | `↑` | `↗` | `→` |

每个字符集只包含 **8 个 key**：`topLeft` / `top` / `topRight` / `right` / `bottomRight` / `bottom` / `bottomLeft` / `left`。**没有** `cross` / `topMid` / `bottomMid` / `leftMid` / `rightMid` 这些"内部交叉点"字符（`┬┴┼├┤`）。

也支持自定义 dict：`resolve_border_chars(style)` 接受 8-key dict（`ansi.py:302-313`）。

### `_paint_box_border` 实际画什么

**文件**: `render_layout.py:732-795`

`_paint_box_border` 只画 **4 条外边**：

- 顶边：`topLeft + top * content_w + topRight`（一行）
- 底边：`bottomLeft + bottom * content_w + bottomRight`（一行）
- 左边：`left` 字符在 `middle_start..middle_end` 每行写一次
- 右边：`right` 字符在 `middle_start..middle_end` 每行写一次

**完全不画任何内部线**。content 区域中间没有任何分隔线绘制逻辑。

### 仓内有没有别的组件画过表格边框（`┬┴┼├┤`）？

**grep 结果**：

- `src/ink/externals/spinner.py:73` —— `"pipe": ("┤", "┘", "┴", "└", "├", "┌", "┬", "┐")` —— 这是 Braille spinner 的动画帧序列，**不是表格边框**。每个字符是动画的一帧。
- 仓内 **没有任何组件** 画过 `┌─┬─┐ │ │ │ ├─┼─┤ └─┴─┘` 这种完整带内部列分隔的表格边框。

### 已有的 `Table` 外部组件怎么处理？

**文件**: `src/ink/externals/table.py:1-288`

PyInk 已经有一个独立的 `Table` 外部组件（Phase 6 PR2），它的设计明确说（`table.py:34-37`）：

> "PyInk favours the borderless look; callers who want a border can wrap the table in a `Box(borderStyle="single")`"

即：`Table` 组件本身 **不画任何边框**，只做列对齐。如果调用方想要边框，自己包一层 `Box(borderStyle=...)`，但那样只能拿到外框，没有内部列分隔。

### header / body 之间的分隔线怎么画？

Box border 没有 `middle` 行的概念。要画 `├─┼─┤` 这种分隔线，**必须自己用 `Text` 行拼**。

## 结论与推荐的表格边框实现策略

### 推荐方案：自画 Text 行（不依赖 Box border）

参考 claude-code 的 `MarkdownTable.tsx`（`renderBorderLine` 函数，行 226-238）：把整个表格渲染成 **一列 `Text` leaves**，每行是一个完整的预拼字符串。具体：

1. **顶边行**：`┌` + `─` * (col_w[0] + 2) + `┬` + `─` * (col_w[1] + 2) + `┬` + ... + `┐`
2. **header 行**：`│` + ` ` + pad(header[0]) + ` ` + `│` + ` ` + pad(header[1]) + ` ` + `│` + ...
3. **header/body 分隔行**：`├` + `─` * (col_w[0] + 2) + `┼` + `─` * (col_w[1] + 2) + `┼` + ... + `┤`
4. **body 行**：同 header 格式
5. **行间分隔行**（可选）：`├─┼─┼─┤`
6. **底边行**：`└` + `─` * (col_w[0] + 2) + `┴` + `─` * (col_w[1] + 2) + `┴` + ... + `┘`

每行作为一个 `Text` leaf，外面套一个 `Box(*Texts, flexDirection="column")`。**不要用 `Box(borderStyle=...)`** —— 它画不了内部 `┬┴┼`。

### 代码示例（伪代码）

```python
def _render_table_bordered(header: list[str], rows: list[list[str]], widths: list[int], theme) -> Element:
    """Render a bordered table with internal column separators."""
    def hline(left: str, mid: str, cross: str, right: str) -> str:
        parts = [left]
        for i, w in enumerate(widths):
            parts.append(mid * (w + 2))  # +2 for the 1-space padding on each side
            parts.append(cross if i < len(widths) - 1 else right)
        return "".join(parts)

    def dataline(cells: list[str]) -> str:
        parts = ["│"]
        for i, (cell, w) in enumerate(zip(cells, widths)):
            parts.append(f" {cell.ljust(w)} │")
        return "".join(parts)

    lines: list[Element] = [
        Text(hline("┌", "─", "┬", "┐")),
        Text(dataline(header)),  # header may be bold via apply_style
        Text(hline("├", "─", "┼", "┤")),
    ]
    for row in rows:
        lines.append(Text(dataline(row)))
    lines.append(Text(hline("└", "─", "┴", "┘")))
    return Box(*lines, flexDirection="column")
```

### 替代方案（不推荐）：嵌套 Box

理论上可以用嵌套 `Box(borderStyle="single")` 模拟列分隔：每个 cell 是一个带左右边框的 `Box`，行是 `Box(*cells, flexDirection="row")`，表格是 `Box(*rows, flexDirection="column")`。但：

- 每个 cell 的左右边框会**重复**（相邻 cell 的右边 + 左边 = `││`，不是单根 `│`）。
- 顶/底/中间分隔线 `┌─┬─┐` / `├─┼─┤` / `└─┴─┘` 仍然画不出来 —— Box border 只有 `┌─┐` / `└─┘`，没有 `┬┴┼`。
- 嵌套 Box 还会引入 padding / 布局复杂度。

### 实现 `apply_style` 给边框上色

`render_layout.py:772` 已经支持 `borderColor` prop，但那是 Box border 的着色路径。自画 Text 行要上色，用 `ansi.style_segment`（`ansi.py:269-300`）或直接 `apply_style` 包整个 hline / dataline 字符串。

```python
from ink.render.ansi import apply_style
border_color = theme.get("table_border_color")  # 新增 theme 键
line = apply_style(hline("┌", "─", "┬", "┐"), color=border_color)
```

注意 `apply_style` 会在末尾加 `\x1b[0m` reset，layout 的 `string_width` 会剥 CSI（见 `ansi-width-measurement.md`），所以边框字符宽度计算不受影响。

## 对实现的影响

1. **不要试图扩展 `BORDER_STYLES` 加 `cross` / `topMid` 等 key** —— `_paint_box_border` 不会读它们，改 layout renderer 代价太大。
2. **自画 Text 行是唯一可行方案**，与 claude-code 的实现方式一致。
3. **每行作为一个 `Text` leaf**，外层 `Box(flexDirection="column")`。这样 layout 会把每行当一整块文本，不会拆单元格 —— 这是 claude-code `<Ansi>{tableLines.join('\n')}</Ansi>` 的等效做法。
4. **降级 key-value 时**（窄终端），直接渲染成 `Text` 行（`label: value`），不需要边框。
5. **theme 新增键建议**：`table_border_color`（默认 `None` 或 `"gray"`）、`table_header_bold`（默认 `True`）、`table_cell_color`（默认 `None`）。

## Caveats / Not Found

- 仓内没有现成的"自画表格边框"参考实现，需要从零写。
- `ansi.style_segment` 和 `apply_style` 都能给边框字符上色，但 `style_segment` 是 flat（非嵌套），适合单段边框线；`apply_style` 适合整行（含 cell 内容 + 边框）。选哪个取决于是否要让 cell 内容和边框同色 —— 通常不同色（边框 dim，cell 正常），所以推荐 **边框字符单独 `apply_style`，cell 内容单独 `apply_style`，然后字符串拼接**。
