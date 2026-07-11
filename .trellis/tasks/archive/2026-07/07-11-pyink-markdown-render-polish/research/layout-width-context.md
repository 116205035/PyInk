# Research: Layout 宽度上下文 —— 表格响应式收缩怎么拿宽度

- **Query**: `get_current_text_width()` 的契约；表格渲染时上下文是否已建立；静态 fast path 能否拿宽度；`_render_markdown_to_string` 的 `columns` 来源
- **Scope**: internal
- **Date**: 2026-07-11

## Findings

### `get_current_text_width()` 的契约

**文件**: `src/ink/layout/_text_width_context.py:1-60`

- 实现：`contextvars.ContextVar[int | None]`，默认 `None`。
- `get_current_text_width()` 返回 `int | None`；`None` 表示"当前 layout pass 没有建立有限宽度"（例如无界宽度的测量 pass）。
- 谁注入：**只有一处 setter 调用方** —— `src/ink/layout/flex.py:937` 的 `_layout_node` 文本分支。

**注入时机**（`flex.py:906-944`）：

```python
if node.kind == "text":
    max_w_for_text = (own_w if own_w >= 0 else (effective_max_w if effective_max_w != float("inf") else float("inf")))
    if node.text_renderer is not None:
        ctx_width = (int(max_w_for_text) if max_w_for_text != float("inf") and max_w_for_text >= 1 else None)
        # ...
        token = set_current_text_width(ctx_width)
        try:
            rendered = node.text_renderer()
        finally:
            reset_current_text_width(token)
```

**关键约束**：宽度上下文 **只**在 layout pass 处理 `text` 节点且该节点持有 `text_renderer` callable（即 `Text(callable)` 形式）时注入。普通 `Text("literal")` 节点不触发 setter；Box 节点也不触发。

### 表格渲染时上下文是否已建立？

**否。** `_render_table`（`markdown.py:694-754`）在 `_render_tokens` 阶段同步调用，产出 `Box(*Text cells, flexDirection="row")` 元素树。这一阶段发生在：

1. **静态 fast path**（`Markdown("...")` str 分支，`markdown.py:1083-1100`）：在 `Markdown()` 工厂被调用时（mount 之前）就立即解析 + 渲染，产出 `Box` 元素。此时 layout 还没跑，`get_current_text_width()` 返回 `None`。
2. **响应式分支**（`_MarkdownImpl`，`markdown.py:923-992`）：`_render_tokens` 在 `render_reactive()` callable 里被调用（layout-time），callable 本身跑在 `Text(render_reactive)` leaf 的 `text_renderer` 上下文里 → 此时 `get_current_text_width()` **有值**（见 `markdown.py:975`）。

但即便响应式分支，`_render_table` 在 `_render_tokens` 里被调用时，产出的也是嵌套 `Box`/`Text` 元素树，最终通过 `_render_markdown_to_string` 喂给一个 throwaway `Reconciler` + `layout`（`markdown.py:873-885`）跑成一个 snapshot 字符串。这个 snapshot 字符串随后成为 `Text` leaf 的 body —— **内部 Box 结构已经被压平成字符串**，外层 layout 看不到表格的列结构，也无法再次响应式收缩。

### 静态 fast path 能拿宽度吗？

**拿不到 layout 宽度上下文，只能拿 viewport columns。** 静态分支直接返回 `Box(*elements, flexDirection="column", gap=1, **box_props)`，元素树是预构建好的；reconciler mount + layout 时不会再调任何 callable 重新渲染表格。`_render_table` 在 `Markdown()` 调用时就已经执行完毕，`get_current_text_width()` 当时是 `None`。

静态分支唯一的"宽度"信息来源是 reconciler mount 时的 viewport columns（由 `render(..., columns=N)` 传入，默认 80）—— 但这个值在 `_render_table` 执行时**不可达**（mount 还没发生）。

### `_render_markdown_to_string(text, columns, theme)` 的 columns 来源

**文件**: `markdown.py:858-885`

`columns` 参数由调用方传入：

1. `_cached_render(text, columns, theme)`（`markdown.py:902-920`）传给 `_render_markdown_to_string`。
2. `_cached_render` 的 `columns` 来自 `_MarkdownImpl.render_reactive()`（`markdown.py:975-983`）：
   ```python
   columns = get_current_text_width()
   if columns is None or columns < 1:
       inst = _get_current_instance()
       columns = 80
       if inst is not None:
           cols_attr = getattr(inst, "columns", 0)
           if isinstance(cols_attr, int) and cols_attr > 0:
               columns = cols_attr
   ```
   即：优先用 layout 宽度上下文，回退到 instance.columns（viewport）。

**关键发现**：`_render_markdown_to_string` 和 `_cached_render` **只被响应式分支调用**（`_MarkdownImpl.render_reactive`）。静态 fast path 完全不走这条路径 —— 它直接 `_parse(source)` + `_render_tokens` + 返回 `Box`，没有 columns 参数介入。

## 结论：表格响应式收缩的宽度获取方案

### 两条路径分别处理

**路径 A — 静态 fast path（`Markdown("...")` str 分支）**：

- `_render_table` 执行时 `get_current_text_width()` 一定返回 `None`（layout 未开始）。
- 可选方案：
  1. **不实现响应式收缩**：静态表格按 ideal widths 渲染，太宽就溢出（layout 不会重新拆表格）。这是当前行为。
  2. **延迟渲染**：把静态分支也改成返回 `Text(callable)` 形式（callable 内部跑 `_render_tokens`），让 layout 在拿到宽度后再渲染表格。代价：静态分支失去"eager 返回 Box"的简单性，且需要 throwaway Reconciler（和响应式分支一样）。
  3. **在 mount 时拿 viewport columns**：通过 `_get_current_instance()` 拿 viewport columns，但这在 `Markdown()` 调用时还是 `None`（mount 未发生）。
- **推荐**：静态分支目前无法拿到准确的 layout 宽度。如果 PRD 强求静态表格响应式，必须把静态分支也改成 `Text(callable)` 形式走 `text_renderer` 路径（方案 2）。

**路径 B — 响应式分支（`_MarkdownImpl`）**：

- `_render_table` 执行时 `get_current_text_width()` **有值**（layout 已经在处理 `Text(render_reactive)` leaf）。
- 但 `_render_table` 产出的 Box 树会被 `_render_markdown_to_string` 压平成字符串，外层 layout 看到的是一个固定宽度的字符串 leaf。
- 表格内部可以**在 `_render_table` 里读 `get_current_text_width()`** 拿到当前可用宽度，据此决定列宽 / 降级 key-value。
- **这是唯一能拿到 layout 宽度的路径**，且响应式分支已经有 `_cached_render` 的 (text, columns, theme) cache key，宽度变化时表格会重新渲染。

### 对实现的指导

1. **静态分支**：`_render_table` 拿不到 layout 宽度 → 表格按 ideal widths 渲染，无法响应式收缩。若 PRD 要求静态也响应式，需把静态分支改成 `Text(callable)`（参见下方"实现风险"）。
2. **响应式分支**：在 `_render_table` 开头 `from ink.layout._text_width_context import get_current_text_width; width = get_current_text_width()`，根据 width 决定列宽 / 降级。`width is None` 时回退到理想宽度或 viewport columns（通过 `_get_current_instance()`）。
3. **静态 + 响应式统一**：若想让静态分支也享受响应式收缩，最干净的做法是把静态分支也走 `_MarkdownImpl`（即 `Markdown("str")` 也返回 `create_element(_MarkdownImpl, source="str", ...)`）。但这会改变静态分支的元素类型（从 `"box"` host 变成 function component），破坏 `test_static_str_returns_box_host_with_column_direction`（断言 `el.type == "box"`）。

## Caveats / Not Found

- `_get_current_instance()` 在 `_render_table` 执行时是否可调用、能否返回有效实例，没有在静态分支测试过；推断它在 mount 之前返回 `None`（因为 hook context 还没建立）。
- 若静态分支改成延迟渲染，`_render_table` 的执行时机后移到 layout pass，此时 `get_current_text_width()` 有值 —— 但代价是失去 eager 解析 + 静态元素树。
