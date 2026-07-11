# Research: 补充发现 —— markdown-it-py 的 table align 字段

- **Query**: markdown-it-py 的 `token.align` 字段结构
- **Scope**: internal（实验验证）
- **Date**: 2026-07-11

## 发现

`claude-code-table-algorithm.md` 的 Caveats 里提到"需要实验确认 `token.align` 在 Python 侧的具体字段名"。本次实验已确认：

**markdown-it-py 不把 align 放在 `token.align`**（那是 `marked`/TypeScript 的字段），而是放在 **`th_open` / `td_open` token 的 `attrs['style']`** 里，格式是 `"text-align:left"` / `"text-align:center"` / `"text-align:right"`。

### 实验脚本

```python
from markdown_it import MarkdownIt
md = MarkdownIt("commonmark").enable("table")
tokens = md.parse("| Left | Center | Right |\n|:-----|:------:|------:|\n| a | b | c |\n")
for t in tokens:
    if t.type in ("th_open", "td_open"):
        print(t.type, "attrs=", t.attrs)
```

### 输出

```
th_open attrs= {'style': 'text-align:left'}
th_open attrs= {'style': 'text-align:center'}
th_open attrs= {'style': 'text-align:right'}
td_open attrs= {'style': 'text-align:left'}
td_open attrs= {'style': 'text-align:center'}
td_open attrs= {'style': 'text-align:right'}
```

**无 align 的列**：`th_open` / `td_open` 的 `attrs` 为 `{}`（空 dict）。实验：

```python
tokens = md.parse("| A | B |\n|---|---|\n| 1 | 2 |\n")
# th_open attrs= {}  ← 无 style，默认 left
```

## 对实现的影响

### 读取 align 的正确方式

在 `_render_table` 遍历 `body_tokens` 时，对每个 `th_open` / `td_open` token：

```python
def _cell_align(token) -> str:
    """Extract align from th_open/td_open attrs. Default 'left'."""
    style = (token.attrs or {}).get("style", "")
    # style like "text-align:center"
    if "text-align:center" in style:
        return "center"
    if "text-align:right" in style:
        return "right"
    return "left"  # includes "text-align:left" and no style
```

**只有 `th_open` 的 align 有意义**（header 行的 align 决定整列对齐）。`td_open` 的 align 通常继承 thead，但 markdown-it-py 会把 style 重复写在每个 `td_open` 上 —— 实现时可以只读 `th_open` 的 align 作为整列对齐，忽略 `td_open` 的（与 claude-code 行为一致：`token.align?.[colIndex]` 是列级数组）。

### 收集 align 的时机

在 `_render_table` 的现有循环里（`markdown.py:710-729`），遇到 `tr_open` 时如果是 thead 行，记录每个 `th_open` 的 align 到 `col_aligns: list[str]`；后续 body 行用这个 `col_aligns` 决定每列对齐。

## Caveats / Not Found

- 没有验证 markdown-it-py 是否对 `td_open` 的 align 做 override（即 body cell 能否单独指定与 header 不同的对齐）。CommonMark spec 说 table 的对齐由分隔行的 `:---:` 决定，整列一致，所以 body cell 的 align 应该和 header 一致 —— 实现时只读 header 即可。
