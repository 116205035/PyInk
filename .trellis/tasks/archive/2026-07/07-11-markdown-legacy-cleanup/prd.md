# markdown-legacy-cleanup

## Goal

修复 `pyink-markdown-render-polish` 任务留下的 2 个已知遗留：
1. `quote_color` 主题键存在于 `DEFAULT_MARKDOWN_THEME` 但从未被 wire-up，调用方传 `theme={"quote_color": "red"}` 无效
2. 表格边框字符集 `_TABLE_BORDER_CHARS` 与 `ink.render.ansi.BORDER_STYLES` 外角字符重复维护 + 命名 `rounded` / `round` 不一致

两个问题都是 `pyink-markdown-render-polish` 任务的 PR1/PR2 引入的代码债，本任务集中清理。

## What I already know

### 遗留 1：`quote_color` 未 wire-up

- `DEFAULT_MARKDOWN_THEME["quote_color"] = "muted"`（`markdown.py:227`，PR3 改的）
- 但 `_render_blockquote`（`markdown.py:848-858`）用 `__quote__` flag 机制：塞 `quote_theme["__quote__"] = True`，`_render_inline`（`markdown.py:541`）读到 flag 就 `quote_dim = True`，然后 `apply_style(..., dimColor=quote_dim)`
- `quote_color` 键从来没被任何函数读取（grep 确认：只出现在 docstring / CHANGELOG / 测试注释）
- PR3 把默认值从 `"gray"` 改成 `"muted"` 是空改——反正没读
- 测试 `test_blockquote_*` 只断言 `dimColor` SGR 2，没有 `quote_color` 的 SGR 断言

### 遗留 2：边框字符集重复 + 命名不一致

- `ink.render.ansi.BORDER_STYLES`（`ansi.py:74-144`）：7 种样式（`single`/`double`/`round`/`bold`/`singleDouble`/`doubleSingle`/`classic`），每种 8 个外角字符（`topLeft`/`top`/`topRight`/`right`/`bottomRight`/`bottom`/`bottomLeft`/`left`），**无 cross 字符**
- `markdown.py:_TABLE_BORDER_CHARS`（PR2 定义）：4 种样式（`single`/`rounded`/`double`/`none`），11 个字符（8 外角 + 5 cross：`top_cross`/`mid_cross`/`bottom_cross`/`mid_left`/`mid_right`）
- 外角字符两处重复维护（`┌┐└┘─│` 等在 BORDER_STYLES 和 _TABLE_BORDER_CHARS 各定义一次）
- 命名：BORDER_STYLES 用 `"round"`，markdown 用 `"rounded"`
- 消费者：`BORDER_STYLES` 被 `divider.py` + `test_ansi.py` + `test_divider.py` + `divider_demo.py` 使用；`_TABLE_BORDER_CHARS` 只在 `markdown.py` 内部使用
- **研究明确建议**（`research/table-border-options.md:130`）："不要试图扩展 `BORDER_STYLES` 加 `cross` / `topMid` 等 key —— `_paint_box_border` 不会读它们，改 layout renderer 代价太大"

## Assumptions (temporary)

- `quote_color` wire-up 后，blockquote 视觉效果从 `dimColor`（SGR 2）改为 `color="muted"`（SGR 90 gray）。两者都是 dim 效果，但 SGR 码不同，终端表现可能略有差异。这是可接受的语义化改进。
- `_TABLE_BORDER_CHARS` 外角字符从 `BORDER_STYLES` 读不会破坏表格渲染——两者的外角字符定义一致（`single` 都是 `┌┐└┘─│`，`double` 都是 `╔╗╚╝═║`，`round`/`rounded` 都是 `╭╮╰╯─│`）。
- `BORDER_STYLES` 加 `"rounded"` 别名不破坏现有消费者（`divider.py` 等）——加 key 是向后兼容。

## Open Questions

（无，方案已明确）

## Requirements

### R1：`quote_color` wire-up

- `_render_blockquote` 读 `quote_color = _resolve_theme_color(theme, "muted", "quote_color")`，塞进 `quote_theme["__quote_color__"]`
- `_render_inline` 读 `__quote_color__`：有值（非 None）用 `color=quote_color`，None 时不染色（默认文本色）
- 删除 `__quote__` flag（被 `__quote_color__` 完全取代）
- 默认 `quote_color="muted"` → SGR 90 gray（was SGR 2 dimColor）

### R2：边框字符集去重 + 命名统一

- 新增 `_get_table_border_chars(style)` 函数：从 `BORDER_STYLES` 读外角字符（`topLeft`→`top_left` 等映射），cross 字符从 `_TABLE_CROSS_CHARS` 补充
- `_TABLE_CROSS_CHARS` 只定义 cross 字符（5 个），按 ansi 的样式名（`single`/`double`/`round`/...）
- `_TABLE_BORDER_ALIASES = {"rounded": "round"}`：markdown 接受 `"rounded"`，映射到 ansi 的 `"round"`
- `BORDER_STYLES` 加 `"rounded"` 作为 `"round"` 的别名（在 ansi.py 里 `BORDER_STYLES["rounded"] = BORDER_STYLES["round"]`），让两边命名统一对外可互换
- 删除 `_TABLE_BORDER_CHARS`（被 `_get_table_border_chars` 取代）
- `table_border_style="none"` 保持现有行为（不画边框，不走 `_get_table_border_chars`）

## Acceptance Criteria

- [ ] `theme={"quote_color": "red"}` 传入时，blockquote 内容渲染为红色（SGR 31）
- [ ] 默认 theme 下 blockquote 内容渲染为 gray（SGR 90，来自 `quote_color="muted"`）
- [ ] `theme={"quote_color": None}` 传入时，blockquote 内容无 color SGR（默认文本色）
- [ ] `__quote__` flag 被删除，`_render_inline` 不再读取它
- [ ] `_TABLE_BORDER_CHARS` 字典被删除，外角字符从 `BORDER_STYLES` 读
- [ ] `table_border_style="rounded"` 和 `"round"` 都能正常渲染（别名兼容）
- [ ] `BORDER_STYLES["rounded"]` 存在且等于 `BORDER_STYLES["round"]`
- [ ] 现有 divider 测试 + markdown 测试全过（0 破坏）
- [ ] 新增测试覆盖：`quote_color` wire-up、`_get_table_border_chars` 从 BORDER_STYLES 读、`rounded`/`round` 别名

## Definition of Done

- 新增/更新测试覆盖 R1 + R2
- lint / typecheck 绿
- 不引入 jarvis 耦合
- CHANGELOG.md 更新（quote_color 行为变化 + 命名兼容说明）

## Technical Approach

### R1 实现

```python
# _render_blockquote (markdown.py:848-858)
quote_color = _resolve_theme_color(theme, "muted", "quote_color")
quote_theme = dict(theme)
quote_theme["__quote_color__"] = quote_color  # 替代 __quote__ flag
# 删除: quote_theme["__quote__"] = True
inner_elements, _ = _render_tokens(...)

# _render_inline (markdown.py:541)
quote_color = theme.get("__quote_color__")  # None 或颜色名
# 原: quote_dim = bool(theme.get("__quote__"))
# 新: 直接用 quote_color（None 时 apply_style 不加 color SGR）
```

`_render_inline` 里 text 分支的 `apply_style` 调用改为：
```python
apply_style(
    text,
    color=quote_color,        # None 时 apply_style 不加 color SGR
    bold=bold, italic=italic, ...
)
```

`__quote__` flag 完全删除，`__quote_color__` 携带全部语义（有值 = quote 上下文 + 颜色，None = 非 quote 或显式禁用染色）。

### R2 实现

```python
# markdown.py
from ink.render.ansi import BORDER_STYLES

#: 表格专属 cross 字符（BORDER_STYLES 没有，因为 Box border 不需要）
_TABLE_CROSS_CHARS: dict[str, dict[str, str]] = {
    "single": {"top_cross": "┬", "mid_cross": "┼", "mid_left": "├", "mid_right": "┤", "bottom_cross": "┴"},
    "double": {"top_cross": "╦", "mid_cross": "╬", "mid_left": "╠", "mid_right": "╣", "bottom_cross": "╩"},
    "round":  {"top_cross": "┬", "mid_cross": "┼", "mid_left": "├", "mid_right": "┤", "bottom_cross": "┴"},
    "bold":   {"top_cross": "┳", "mid_cross": "╋", "mid_left": "┣", "mid_right": "┫", "bottom_cross": "┻"},
}

#: markdown → ansi 命名别名
_TABLE_BORDER_ALIASES = {"rounded": "round"}


def _get_table_border_chars(style: str) -> dict[str, str]:
    """Build table border char set from BORDER_STYLES + cross additions."""
    ansi_style = _TABLE_BORDER_ALIASES.get(style, style)
    base = BORDER_STYLES.get(ansi_style, BORDER_STYLES["single"])
    cross = _TABLE_CROSS_CHARS.get(ansi_style, _TABLE_CROSS_CHARS["single"])
    return {
        "top_left": base["topLeft"],
        "top_right": base["topRight"],
        "bottom_left": base["bottomLeft"],
        "bottom_right": base["bottomRight"],
        "horizontal": base["top"],
        "vertical": base["left"],
        **cross,
    }
```

```python
# ansi.py (BORDER_STYLES 末尾加别名)
"rounded": BORDER_STYLES["round"],  # 别名，与 markdown 命名统一
```

## Decision (ADR-lite)

### quote_color wire-up 决策

**Context**: `quote_color` 键存在但未 wire-up，PR3 改默认值是空改。`__quote__` flag 机制只走 dimColor，与 `quote_color` 语义脱节。
**Decision**: 删除 `__quote__` flag，单一 `__quote_color__` flag 携带全部语义。`_render_inline` 读 `__quote_color__`：有值用 color，None 不染色。默认 `quote_color="muted"` → SGR 90 gray。
**Consequences**: blockquote 视觉从 SGR 2（dimColor）变为 SGR 90（gray color），完成 PR3 改默认值的本意。调用方传 `theme={"quote_color": "red"}` 现在生效；`theme={"quote_color": None}` 显式禁用染色（默认文本色）。测试断言从 dimColor SGR 2 更新为 color SGR 90。

### 边框字符集去重决策

**Context**: `_TABLE_BORDER_CHARS` 与 `BORDER_STYLES` 外角字符重复维护，研究建议不要扩展 BORDER_STYLES 加 cross。
**Decision**: markdown 外角字符从 BORDER_STYLES 读，cross 字符留在 markdown（表格专属）。命名用别名统一（`"rounded"` → `"round"`），BORDER_STYLES 加 `"rounded"` 别名。
**Consequences**: 外角字符单一来源（BORDER_STYLES），ansi 改了 markdown 自动跟随。cross 字符是表格特有，留在 markdown 合理。命名兼容，不破坏 PR2 的 `"rounded"` 默认值。

## Out of Scope

- `BORDER_STYLES` 的其他样式（`singleDouble`/`doubleSingle`/`classic`）是否要支持 `table_border_style` —— 当前只支持 `single`/`rounded`/`double`/`none`，扩展留后续
- `quote_color` 是否要应用于 blockquote 内的代码块 / 链接 —— 当前 inline code 和 link 有自己的 color，不继承 quote_color
- 流式 stable prefix 优化（PRD Out of Scope，独立任务）
- Jarvis 侧的 theme 覆盖（下一个任务）

## Technical Notes

- `markdown.py:227` — `quote_color` 默认值
- `markdown.py:541` — `quote_dim = bool(theme.get("__quote__"))` 待改
- `markdown.py:848-858` — `_render_blockquote` 待改
- `markdown.py:_TABLE_BORDER_CHARS` — 待删，被 `_get_table_border_chars` 取代
- `ansi.py:74-144` — `BORDER_STYLES` 定义，末尾加 `"rounded"` 别名
- `research/table-border-options.md:130` — 明确不要扩展 BORDER_STYLES 加 cross
- 消费者：`divider.py` / `test_ansi.py` / `test_divider.py` / `divider_demo.py` —— 加 `"rounded"` 别名不破坏
