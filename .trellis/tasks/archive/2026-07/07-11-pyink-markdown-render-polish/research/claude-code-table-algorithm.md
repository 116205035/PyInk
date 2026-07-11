# Research: claude-code 表格响应式收缩算法

- **Query**: 读 `D:/Projects/github/claude-code/src/components/MarkdownTable.tsx` 的 min/ideal width 算法、降级 key-value 阈值、列宽分配策略
- **Scope**: external（claude-code 参考实现）
- **Date**: 2026-07-11

## Findings

参考文件：`D:/Projects/github/claude-code/src/components/MarkdownTable.tsx`（321 行）。

### 常量

```typescript
const SAFETY_MARGIN = 4;          // 防止终端 resize race 导致溢出
const MIN_COLUMN_WIDTH = 3;       // 列最小宽度，防止退化布局
const MAX_ROW_LINES = 4;          // 行最大行数，超过则降级 key-value
```

- `SAFETY_MARGIN=4`：预留 4 字符余量，避免终端 resize 时计算宽度与实际渲染不一致导致 Ink clip 在不同帧截断不同位置，引发 flicker loop。
- `MIN_COLUMN_WIDTH=3`：任何列至少 3 字符宽（一个字 + 两个 padding）。
- `MAX_ROW_LINES=4`：某一行 wrap 后超过 4 行就降级 key-value。

### 算法总览

**Step 1** —— 计算每列的 `minWidth` 和 `idealWidth`：

```typescript
function getMinWidth(tokens): number {
    const text = getPlainText(tokens);
    const words = text.split(/\s+/).filter(w => w.length > 0);
    if (words.length === 0) return MIN_COLUMN_WIDTH;
    return Math.max(...words.map(w => stringWidth(w)), MIN_COLUMN_WIDTH);
}

function getIdealWidth(tokens): number {
    return Math.max(stringWidth(getPlainText(tokens)), MIN_COLUMN_WIDTH);
}
```

- `minWidth` = 该列所有 cell 中最长单词的宽度（防断词），最小 `MIN_COLUMN_WIDTH`。
- `idealWidth` = 该列所有 cell 中最长 cell 的完整显示宽度，最小 `MIN_COLUMN_WIDTH`。
- 对 header 和所有 body row 取 max。

**Step 2** —— 计算可用宽度：

```typescript
const numCols = token.header.length;
const borderOverhead = 1 + numCols * 3;  // │ + (2 padding + 1 border) per col
const availableWidth = Math.max(
    terminalWidth - borderOverhead - SAFETY_MARGIN,
    numCols * MIN_COLUMN_WIDTH
);
```

- `borderOverhead` = `1 + numCols * 3`：左边框 1 字符 + 每列（左 padding 1 + 右 padding 1 + 右分隔线 1）。
- `availableWidth` = `terminalWidth - borderOverhead - SAFETY_MARGIN`，下限 `numCols * MIN_COLUMN_WIDTH`。

**Step 3** —— 列宽分配（三种情况）：

```typescript
const totalMin = minWidths.reduce((sum, w) => sum + w, 0);
const totalIdeal = idealWidths.reduce((sum, w) => sum + w, 0);

let needsHardWrap = false;
let columnWidths: number[];

if (totalIdeal <= availableWidth) {
    // 情况 A：理想总宽 ≤ 可用 → 用 idealWidths
    columnWidths = idealWidths;
} else if (totalMin <= availableWidth) {
    // 情况 B：最小总宽 ≤ 可用 < 理想 → 按溢出比例分配
    const extraSpace = availableWidth - totalMin;
    const overflows = idealWidths.map((ideal, i) => ideal - minWidths[i]);
    const totalOverflow = overflows.reduce((sum, o) => sum + o, 0);
    columnWidths = minWidths.map((min, i) => {
        if (totalOverflow === 0) return min;
        const extra = Math.floor((overflows[i] / totalOverflow) * extraSpace);
        return min + extra;
    });
} else {
    // 情况 C：最小总宽 > 可用 → 按比例缩放，允许断词
    needsHardWrap = true;
    const scaleFactor = availableWidth / totalMin;
    columnWidths = minWidths.map(w => Math.max(Math.floor(w * scaleFactor), MIN_COLUMN_WIDTH));
}
```

- **情况 A**（全 fit）：直接用 idealWidths，每列完整显示。
- **情况 B**（需要缩）：每列保 minWidth，剩余空间按 `(ideal - min)` 比例分配给各列（溢出多的列多分一点）。
- **情况 C**（太窄）：按 `minWidth` 比例缩放，允许 `hardWrap`（断词）。每列至少 `MIN_COLUMN_WIDTH`。

**Step 4** —— 决定是否降级 key-value：

```typescript
function calculateMaxRowLines(): number {
    let maxLines = 1;
    for (let i = 0; i < token.header.length; i++) {
        const wrapped = wrapText(formatCell(token.header[i].tokens), columnWidths[i], { hard: needsHardWrap });
        maxLines = Math.max(maxLines, wrapped.length);
    }
    for (const row of token.rows) {
        for (let i = 0; i < row.length; i++) {
            const wrapped = wrapText(formatCell(row[i]?.tokens), columnWidths[i], { hard: needsHardWrap });
            maxLines = Math.max(maxLines, wrapped.length);
        }
    }
    return maxLines;
}

const maxRowLines = calculateMaxRowLines();
const useVerticalFormat = maxRowLines > MAX_ROW_LINES;
```

- 用 `columnWidths` wrap 每个 cell，算出最大行数。
- 超过 `MAX_ROW_LINES=4` 行 → 降级。

**安全网**（最终检查）：

```typescript
const maxLineWidth = Math.max(...tableLines.map(line => stringWidth(stripAnsi(line))));
if (maxLineWidth > terminalWidth - SAFETY_MARGIN) {
    return <Ansi>{renderVerticalFormat()}</Ansi>;
}
```

- 全部画完后再检查一次最大行宽，如果离终端边缘 < `SAFETY_MARGIN` 也降级。防 resize race。

### key-value 降级格式

```typescript
function renderVerticalFormat(): string {
    const lines: string[] = [];
    const headers = token.header.map(h => getPlainText(h.tokens));
    const separatorWidth = Math.min(terminalWidth - 1, 40);
    const separator = '─'.repeat(separatorWidth);
    const wrapIndent = '  ';
    token.rows.forEach((row, rowIndex) => {
        if (rowIndex > 0) lines.push(separator);
        row.forEach((cell, colIndex) => {
            const label = headers[colIndex] || `Column ${colIndex + 1}`;
            const value = formatCell(cell.tokens).trimEnd().replace(/\n+/g, ' ').replace(/\s+/g, ' ').trim();
            // 两遍 wrap：第一行窄（label 占位），续行宽
            const firstLineWidth = terminalWidth - stringWidth(label) - 3;
            const subsequentLineWidth = terminalWidth - wrapIndent.length - 1;
            const firstPassLines = wrapText(value, Math.max(firstLineWidth, 10));
            // ... rewrap continuation to wider width ...
            lines.push(`${ANSI_BOLD_START}${label}:${ANSI_BOLD_END} ${wrappedValue[0] || ''}`);
            for (let i = 1; i < wrappedValue.length; i++) {
                lines.push(`${wrapIndent}${wrappedValue[i]}`);
            }
        });
    });
    return lines.join('\n');
}
```

格式：

```
header1: value1 row1
header2: value2 row1
header3: value3 row1
────────────────────────────────
header1: value1 row2
...
```

- 行间用 `─` * `min(terminalWidth - 1, 40)` 分隔。
- label 加粗（`\x1b[1mlabel:\x1b[22m`），后跟 value。
- 续行缩进 2 空格。
- 两遍 wrap：第一行窄（减去 label 宽度 + 3），续行宽（减去缩进 + 1）。

### 边框绘制

```typescript
function renderBorderLine(type: 'top' | 'middle' | 'bottom'): string {
    const [left, mid, cross, right] = {
        top:    ['┌', '─', '┬', '┐'],
        middle: ['├', '─', '┼', '┤'],
        bottom: ['└', '─', '┴', '┘'],
    }[type];
    let line = left;
    columnWidths.forEach((width, colIndex) => {
        line += mid.repeat(width + 2);
        line += colIndex < columnWidths.length - 1 ? cross : right;
    });
    return line;
}
```

- top: `┌─┬─┬─┐`
- middle (header/body 间 + 行间): `├─┼─┼─┤`
- bottom: `└─┴─┴─┘`
- 每列占 `width + 2` 字符（width + 左右各 1 padding）。

### cell 渲染 + 对齐

```typescript
function renderRowLines(cells, isHeader): string[] {
    const cellLines = cells.map((cell, colIndex) => {
        return wrapText(formatCell(cell.tokens), columnWidths[colIndex], { hard: needsHardWrap });
    });
    const maxLines = Math.max(...cellLines.map(lines => lines.length), 1);
    const verticalOffsets = cellLines.map(lines => Math.floor((maxLines - lines.length) / 2));
    const result: string[] = [];
    for (let lineIdx = 0; lineIdx < maxLines; lineIdx++) {
        let line = '│';
        for (let colIndex = 0; colIndex < cells.length; colIndex++) {
            const contentLineIdx = lineIdx - verticalOffsets[colIndex];
            const lineText = (contentLineIdx >= 0 && contentLineIdx < cellLines[colIndex].length)
                ? cellLines[colIndex][contentLineIdx] : '';
            const align = isHeader ? 'center' : (token.align?.[colIndex] ?? 'left');
            line += ' ' + padAligned(lineText, stringWidth(lineText), columnWidths[colIndex], align) + ' │';
        }
        result.push(line);
    }
    return result;
}
```

- 每个 cell 用 `wrapText` wrap 到 `columnWidths[colIndex]`。
- 多行 cell 垂直居中（`verticalOffsets`）。
- header 强制 center 对齐，body 用 markdown table 的 `token.align`（`left` / `center` / `right`，默认 `left`）。
- 每行格式：`│ ` + padAligned(text, textWidth, colWidth, align) + ` │`。

### 关键依赖函数

- `stringWidth(s)` —— 来自 `../ink/stringWidth.js`，等价于 PyInk 的 `ink.layout.measure.string_width`（剥 CSI + wcwidth）。
- `wrapAnsi(text, width, { hard, trim, wordWrap })` —— 来自 `../ink/wrapAnsi.js`，等价于 PyInk 的 `ink.layout.measure.wrap_text`（mode="wrap" 或 "hard"）。
- `stripAnsi(s)` —— 来自 `strip-ansi` npm 包，等价于 PyInk 的 `_strip_ansi`（`measure.py:105-106`，private）。
- `padAligned(text, textWidth, width, align)` —— 来自 `../utils/markdown.js`，左/中/右 pad 到 width。
- `formatToken(token, theme, ...)` —— 来自 `../utils/markdown.js`，把 marked token 转 ANSI 字符串（等价于 PyInk 的 `_render_inline_token`）。

## Python 化移植建议

### 直接对应的 PyInk API

| claude-code | PyInk 等价 | 文件:行 |
|---|---|---|
| `stringWidth(s)` | `ink.layout.measure.string_width(s)` | `measure.py:88-102` |
| `wrapAnsi(text, width, {wordWrap:true})` | `wrap_text(text, width, mode="wrap")` | `measure.py:369-416` |
| `wrapAnsi(text, width, {hard:true})` | `wrap_text(text, width, mode="hard")` | `measure.py:369-416` |
| `stripAnsi(s)` | `_strip_ansi(s)` (private) 或 `re.sub(_ANSI_RE, "", s)` | `measure.py:105-106` |
| `formatToken(token, theme, ...)` | `_render_inline_token(token, theme)` | `markdown.py:450-455` |
| `padAligned(text, textWidth, width, align)` | 需自己写（见下方） | — |

`padAligned` 没有 PyInk 等价，需要自己实现：

```python
def _pad_aligned(text: str, text_w: int, width: int, align: str) -> str:
    if text_w >= width:
        return text
    pad = width - text_w
    if align == "center":
        left = pad // 2
        right = pad - left
        return " " * left + text + " " * right
    elif align == "right":
        return " " * pad + text
    else:  # "left"
        return text + " " * pad
```

### 移植要点

1. **常量直接搬**：`SAFETY_MARGIN=4` / `MIN_COLUMN_WIDTH=3` / `MAX_ROW_LINES=4`。
2. **三步列宽分配**：照搬 A/B/C 三种情况。`minWidth` = 最长单词宽度，`idealWidth` = 最长 cell 完整宽度。
3. **降级判定**：先算 `maxRowLines`（每个 cell wrap 后行数的 max），> 4 降级；画完后再用 `maxLineWidth > terminalWidth - SAFETY_MARGIN` 做安全网。
4. **边框自画**：用 `renderBorderLine` 等价函数拼 `┌─┬─┐` / `├─┼─┤` / `└─┴─┘` 字符串，包成 `Text` leaf（参见 `table-border-options.md`）。
5. **对齐**：header 强制 center，body 用 markdown table 的 align（markdown-it-py 的 `token.align` 字段）。
6. **cell 渲染**：用 `_render_inline_token(token, theme)` 拿带 ANSI 的字符串，然后 `wrap_text(cell_str, col_width, mode="wrap" or "hard")` wrap 到列宽。
7. **vertical format**：行间用 `─` * `min(terminalWidth - 1, 40)` 分隔，label 加粗（`apply_style(label + ":", bold=True)`），续行缩进 2 空格。

### 与 PyInk 现状的差异

- **terminalWidth 来源**：claude-code 用 `useTerminalSize()` hook；PyInk 用 `get_current_text_width()`（layout 宽度上下文，见 `layout-width-context.md`）。静态 fast path 拿不到 layout 宽度，需要走响应式分支或退化到 viewport columns。
- **markdown-it-py 的 align**：CommonMark table 插件的 `token.align` 是 `list[str | None]`，每个元素是 `"left"` / `"center"` / `"right"` / `None`。需要从 `table_open` 之后的 `thead_open` / `tr_open` / `th_open` 序列里读出 align。实际上 markdown-it-py 的 table 插件把 align 存在 `Token.attrs` 或 `Token.meta` —— 需要实现时确认（本次研究未深入）。
- **`_render_inline_token` 已存在**（`markdown.py:450-455`），可直接用。返回带 ANSI 的字符串，`string_width` 能正确测量（见 `ansi-width-measurement.md`）。

### 实现风险

1. **静态 fast path 无法响应式**（见 `layout-width-context.md`）—— 如果 PRD 要求静态 Markdown 也响应式收缩，需要把静态分支也改成 `Text(callable)` 形式，破坏 `test_static_str_returns_box_host_with_column_direction`。
2. **markdown-it-py 的 align 读取**：需要实验确认 `token.align` 在 Python 侧的具体字段名和结构。
3. **wrap_text 的 `mode="hard"`**：PyInk 的 `wrap_text` 支持 `mode="hard"`（`measure.py:405-406`），等价于 claude-code 的 `wrapAnsi({hard:true})`，可直接用。
4. **OSC 8 链接 cell**：`_render_inline_token` 可能产出带 OSC 8 序列的字符串，`wrap_text` 和 `string_width` 都剥 OSC（`measure.py:44`），但 wrap 时 OSC 序列的"attach 到当前 cursor position"行为需要验证 —— claude-code 侧用 `wrapAnsi` 处理，PyInk 的 `wrap_text` 在 `_hard_break` / `_word_break` 里把 escape run 当作"附加到当前行"（`measure.py:149-154`），应该兼容。

## Caveats / Not Found

- 没有读 claude-code 的 `../utils/markdown.js` 的 `padAligned` 实现（只读了 `MarkdownTable.tsx`），但根据用法推断是左/中/右 pad 到 width。
- 没有深入 markdown-it-py 的 `token.align` 字段结构 —— 实现时需要写个小脚本 parse 一个带 align 的 markdown table 看 token 结构。
- claude-code 用 `marked`（TS），PyInk 用 `markdown-it-py`（Python），两者的 token 树结构不同，但 table 的 `thead` / `tbody` / `tr` / `th` / `td` 概念一致。
