# Research: Markdown 调用方 + theme 键使用面（break change 影响评估）

- **Query**: grep 全仓 `Markdown(` 的调用点；每个调用点用了哪些 `theme=` 键；有没有调用方传 `**box_props`；列出所有 markdown 相关测试文件路径
- **Scope**: internal
- **Date**: 2026-07-11

## Findings

### 调用点清单（PyInk 仓内）

| 文件 | 行 | 调用形式 | source 类型 | theme 用法 | box_props 用法 |
|---|---|---|---|---|---|
| `examples/markdown/markdown_demo.py` | 125 | `Markdown(SOURCE)` | `str` 静态 | 不传 | 不传 |
| `examples/markdown-streaming/markdown_streaming_demo.py` | 107 | `Markdown(buffer)` | `Signal[str]` 响应式 | 不传 | 不传 |
| `tests/externals/test_markdown.py` | 188 | `Markdown("# Hi")` | `str` | — | — |
| `tests/externals/test_markdown.py` | 196 | `Markdown(buf)` | `Signal` | — | — |
| `tests/externals/test_markdown.py` | 203 | `Markdown(lambda: "# Hi")` | `Callable` | — | — |
| `tests/externals/test_markdown.py` | 209 | `Markdown("# Hi", borderStyle="round", padding=1)` | `str` | — | `borderStyle="round", padding=1` |
| `tests/externals/test_markdown.py` | 215 | `Markdown("# Hi", flexDirection="row")` | `str` | — | `flexDirection="row"`（被组件忽略覆盖） |
| `tests/externals/test_markdown.py` | 225,233,239,246 | `Markdown("# ...")` 系列 | `str` | 不传，断言默认色 | — |
| `tests/externals/test_markdown.py` | 691 | `Markdown("# Title", theme={"h1_color": "cyan"})` | `str` | 覆盖 h1_color | — |
| `tests/externals/test_markdown.py` | 698 | `Markdown("Use \`x\`.", theme={"code_color": "green"})` | `str` | 覆盖 code_color | — |
| `tests/externals/test_markdown.py` | 704 | `Markdown("# Title", theme={"h1_bold": False})` | `str` | 覆盖 h1_bold | — |

### Theme 键使用矩阵

| Theme 键 | 默认值（`DEFAULT_MARKDOWN_THEME`） | 调用方使用情况 |
|---|---|---|
| `h1_color` | `"magenta"` | 测试 691 行覆盖为 `"cyan"`；其余用默认值断言 SGR 35 |
| `h1_bold` … `h6_bold` | `True` | 测试 704 行关闭 h1 bold；其余断言默认 bold（SGR 1） |
| `h2_color` | `"yellow"` | 仅断言默认（SGR 33） |
| `h3_color` | `"green"` | 仅断言默认（SGR 32） |
| `h4_color` / `h5_color` / `h6_color` | `"cyan"` / `"blue"` / `"gray"` | 无覆盖；h4-h6 合并测试只断言 bold |
| `code_color` | `"red"` | 测试 698 行覆盖为 `"green"`；其余断言默认 SGR 31 |
| `code_bg` | `None` | 无人使用 |
| `link_color` | `"blue"` | 无覆盖；断言默认 SGR 34 |
| `quote_color` | `"gray"` | 无覆盖 |
| `code_block_lang_color` | `"gray"` | 无覆盖 |
| `hr_color` | `None` | 无覆盖 |
| `code_block_theme` | `None` | 无覆盖 |
| `code_block_border_color` | `"gray"` | 无覆盖 |
| `code_block_show_border` | `True` | 无覆盖 |
| `code_block_show_language` | `True` | 无覆盖 |

### box_props 使用情况

- 只有 `tests/externals/test_markdown.py:209` 一处调用方传了 `borderStyle="round", padding=1`，并断言这两个 prop 被原样转发到 outer `Box`。
- `flexDirection="row"`（第 215 行）会被组件强制覆盖为 `"column"`（组件契约）。
- **没有任何仓内调用方在 `Markdown(...)` 上传 `width=`**。

### Markdown 相关测试文件路径

- `tests/externals/test_markdown.py`（唯一测试文件，800+ 行）

## 结论与 break-change 影响评估

### 改默认值的影响面

- **若改 `h1_color` 默认值**（例如从 `"magenta"` 改成更柔的颜色）：会直接破坏 `test_h1_gets_magenta_bold`（断言 `ESC[35m`）、`test_theme_bold_disabled_for_h1`（断言 `ESC[35m` 仍在）、`test_theme_h1_color_override`（断言默认 `ESC[35m` 不出现）。**必须同步更新这 3 个测试**。仓内 `examples/markdown/markdown_demo.py` 只是肉眼 demo，无断言。
- **若改 `code_color` 默认值**（如从 `"red"` 改掉）：破坏 `test_inline_code_gets_code_color`（断言 `ESC[31m`）、`test_theme_code_color_override`（断言默认 `ESC[31m` 不出现）。
- **若改 `h2_color`/`h3_color` 默认值**：破坏 `test_h2_gets_yellow_bold` / `test_h3_gets_green_bold`。
- **若改 `link_color`/`quote_color` 默认值**：破坏 `test_link_color_applied_to_label`（断言 `ESC[34m`）；`quote_color` 没有专门的 SGR 断言（`test_blockquote_*` 只断言 `dimColor` SGR 2），影响小。
- **若改 `code_block_*` 系列默认值**：只有 `test_fenced_code_block_*` 系列会受影响，且主要是断言 `"python"` 头出现 + 边框字符存在；改默认颜色不会断 SGR 码，影响小。

### box_props 契约的约束

- 测试 209 行明确断言 `borderStyle` / `padding` 被转发到 outer `Box`。若新实现把 outer `Box` 替换成别的结构（例如自画表格边框不再用 `Box(borderStyle=...)`），此测试会失败。**改实现时需要保留 outer `Box` 的 box_props 转发契约**，或同步改测试。

### 实现建议

1. 改默认 theme 值时，同步更新 `tests/externals/test_markdown.py` 中的 SGR 断言。仓内无其它调用方依赖具体颜色。
2. 保留 `**box_props` 转发到 outer `Box` 的契约（测试 209/215 行）。
3. 如果新增 theme 键（如 `table_*`），不会破坏任何现有调用方或测试（没有调用方依赖 theme dict 的 key set）。

## Caveats / Not Found

- 仓外（用户的下游项目）可能有自己的 theme 覆盖；本评估只覆盖 PyInk 仓内。
- `docs/api-reference.md:171` 和 `README.md:182` 是 API 文档行，不是调用方。
