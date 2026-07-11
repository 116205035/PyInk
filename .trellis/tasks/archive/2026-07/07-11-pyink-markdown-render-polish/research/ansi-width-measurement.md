# Research: ANSI 宽度测量 —— 单元格 inline 样式后宽度怎么算

- **Query**: layout 测量字符串宽度的函数；是否剥 CSI；`_render_inline_token` 返回的带 ANSI 字符串能否直接交给 `Text` leaf 并被正确测量；表格列宽计算应该用什么宽度函数
- **Scope**: internal
- **Date**: 2026-07-11

## Findings

### layout 测量字符串宽度的函数

**文件**: `src/ink/layout/measure.py:1-417`

公开 API（`__all__`，`measure.py:21-26`）：

- `string_width(s: str) -> int` —— 显示宽度（CJK=2，combining=0，ANSI=0，其余=1）。
- `wcswidth(s: str) -> int` —— `string_width` 的底层实现别名。
- `wrap_text(s, width, *, mode)` —— 按宽度分行。

**核心正则**（`measure.py:42-45`）：

```python
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"      # CSI (SGR colours/styles + controls)
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC (BEL- or ST-terminated)
)
```

匹配两种 ANSI 序列：

1. **CSI** —— `\x1b[...m` SGR 颜色/样式序列（`apply_style` 产出的就是这种）。
2. **OSC** —— `\x1b]...ST` 操作系统命令（OSC 8 超链接，`_wrap_osc8` 产出的）。

**`wcswidth` 实现**（`measure.py:71-85`）：

```python
def wcswidth(s: str) -> int:
    stripped = _ANSI_RE.sub("", s)   # ← 先剥 CSI + OSC
    if _HAS_WCWIDTH:
        total = 0
        for ch in stripped:
            w = _wcwidth(ch)
            total += w if w >= 0 else 0
        return total
    return sum(0 if _COMBINING_RE.match(ch) else 1 for ch in stripped)
```

**确认**：`markdown.py:46-49` 的声明是真的 —— layout measure pass **确实剥掉 CSI（和 OSC）序列**，所以 SGR 字节不会膨胀列宽预算。

### `_render_inline_token` 返回的带 ANSI 字符串能否直接交给 `Text` leaf？

**能。** 验证链条：

1. `_render_inline_token(token, theme)` → `_render_inline(children, theme)`（`markdown.py:450-455`），返回一个拼接好的字符串，内含 `apply_style(...)` 产出的 `\x1b[1m...\x1b[0m` SGR 序列 + 可能的 `_wrap_osc8` OSC 8 序列。
2. 这个字符串被 `Text(...)` 包成 leaf（例如 `_render_paragraph` → `Text(_render_inline_token(inline, theme))`，`markdown.py:490`）。
3. layout 在 `_layout_node` 文本分支（`flex.py:906-944`）测量 `node.text` 时调用 `string_width(text)`（`flex.py:699, 704, 978`），`string_width` 会剥 CSI/OSC。
4. 渲染时 `render_layout.py:174, 361` 也用 `string_width(text)` 计算可见宽度。
5. 已有测试 `test_bold_inline`（`test_markdown.py:264-268`）断言 `\x1b[1mbold\x1b[0m` 出现在输出里 —— 说明带 ANSI 的字符串确实直接交给 `Text` leaf 并被 layout 正确处理。

**结论**：带 ANSI 的 styled 字符串可以直接作为 `Text` leaf 的 body，layout 会正确测量其可见宽度。

### 表格列宽计算应该用什么宽度函数？

**当前实现**（`markdown.py:736-739`）：

```python
widths = [0] * n_cols
for r in rows:
    for idx, cell in enumerate(r):
        widths[idx] = max(widths[idx], len(cell))  # ← len() 错的
```

`cell` 此时是 `_inline_plain_text(token)` 的返回值（纯文本，无 ANSI，`markdown.py:757-769`），所以 `len(cell)` 碰巧等于显示宽度（只要没有 CJK / combining char）。

**改用 `_render_inline_token` 后**：`cell` 会是带 ANSI SGR 的字符串，`len(cell)` 会把 `\x1b[1m`（4 字节）+ `\x1b[0m`（4 字节）算进去，列宽会虚高 8+ 字节，导致表格过宽。

**应该改用**：`from ink.layout.measure import string_width` → `widths[idx] = max(widths[idx], string_width(cell))`。

`string_width` 会：

1. 剥 CSI（SGR）和 OSC（链接）。
2. 用 `wcwidth` 算每个字符的显示宽度（CJK=2，combining=0，ASCII=1）。
3. 返回总显示宽度。

**已有更底层的 `_strip_ansi`**（`measure.py:105-106`），但它是 private；公开 API `string_width` 就是正确选择。

### `render_layout.py` 中 string_width 的使用位置（确认剥 CSI 在渲染侧也成立）

- `render_layout.py:174` —— `visible_w = string_width(text)` —— 文本 leaf 渲染时算可见宽度。
- `render_layout.py:361` —— `body_w = string_width(body)` —— 背景 band 渲染时算 body 宽度。

这两处都用 `string_width`，所以 SGR 序列不会让渲染侧误判宽度。

### `Table` 外部组件怎么算列宽（参考）

**文件**: `src/ink/externals/table.py:141-162`

```python
def _column_widths(columns, rows, padding):
    widths = []
    for col_idx, header in enumerate(columns):
        max_len = len(header)
        for row in rows:
            if col_idx < len(row):
                cell_len = len(row[col_idx])  # ← 也是 len()
                ...
```

`Table` 组件也用 `len()`，但它的 cell 是纯字符串（不接受 inline 样式），所以 `len()` 碰巧对（ASCII 场景）。**如果 Markdown 表格复用 `Table` 组件，需要先 strip ANSI 再传，或者在 `Table` 内部也改用 `string_width`**。

## 结论

### 实现指引

1. **表格列宽计算改用 `string_width`**：

   ```python
   from ink.layout.measure import string_width
   # ...
   for r in rows:
       for idx, cell in enumerate(r):
           # cell is now a styled string from _render_inline_token
           widths[idx] = max(widths[idx], string_width(cell))
   ```

2. **cell padding 也要用 `string_width`** 算当前 cell 的显示宽度，而不是 `len(cell)`：

   ```python
   cell_w = string_width(cell)
   padding = " " * (widths[idx] - cell_w)
   ```

3. **`_render_inline_token` 返回的带 ANSI 字符串可以直接交给 `Text` leaf**，layout 会正确测量。无需手动 strip ANSI 再渲染。

4. **不要复用 `_inline_plain_text`** 来算宽度 —— 它丢掉了样式信息，PRD 要求表格 cell 支持 `**bold**` / `` `code` `` inline 样式。

5. **`_render_inline_token` 的输出包含 OSC 8 序列**（如果 cell 里有链接）—— `string_width` 也剥 OSC（`measure.py:44`），所以链接 cell 的宽度计算同样正确。

### 风险点

- **CJK 字符**：`string_width("你好") == 4`，`len("你好") == 2`。改用 `string_width` 后表格列宽会正确反映 CJK 宽度，但 padding 逻辑（`" " * (widths[idx] - string_width(cell))`）也要用 `string_width` 算 cell 宽度，否则 padding 会算错。
- **combining char**（emoji ZWJ 序列等）：`string_width` 会把 combining mark 算 0 宽，但 `len()` 会算 1。改用 `string_width` 更正确，但要注意 `wcwidth` 库的版本对某些 emoji 的宽度定义可能不一致。
- **`wcwidth` 库依赖**：`measure.py:50-55` 尝试 import `wcwidth`，失败则退化为 ASCII fallback（CJK 也算 1 宽）。PyInk 的 `pyproject.toml` 应该已经依赖 `wcwidth`（需确认），否则 CJK 表格列宽会偏窄。

## Caveats / Not Found

- 没有确认 `wcwidth` 是否在 `pyproject.toml` 的必装依赖里（如果只是 optional，CJK 场景列宽会错）。建议实现前 grep 一下 `pyproject.toml`。
- `_render_inline_token` 目前在表格 cell 里没有被使用（表格走 `_inline_plain_text`），改用它后需要验证带 ANSI 的 cell 字符串在 `_render_markdown_to_string` 的 throwaway layout 里也能正确测量（应该能，因为同一套 `string_width`）。
