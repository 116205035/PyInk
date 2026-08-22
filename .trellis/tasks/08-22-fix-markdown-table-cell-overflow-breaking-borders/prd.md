# PRD: fix markdown table cell overflow breaking borders

## 背景

jarvis TUI（PyInk 消费方）助手消息渲染 markdown 表格时边框错位错乱：数据行比上下边框宽，行在 cell 中间被二次折行——首行右边框丢失、续行没有左边框、右边框被挤到续行末尾。已在 jarvis 侧用 `_render_markdown_to_string` 直接复现（110/150/178 三种宽度全错），与消费方接线无关。

复现样例（单行合法 markdown 表格，结论列内容超长，例如 "L4 资金+抱团" 行 ~150 显示宽）：

```
│ L4 资金+抱团 │ 科技成交…全在 MA60 下
方大成交 = 筹码分歧博弈，不是趋势 │
```

复现脚本：jarvis 仓库 `.trellis/tmp/repro_table_align.py`。

## 根因

1. **主因：`src/ink/externals/markdown.py` `_render_table` Regime B/C 不换行** — 表格理想总宽超过可用宽度进入比例缩列宽后，`_dataline` → `_pad_aligned`（`if text_w >= width: return text`）对超过列宽的 cell 原样返回、不换行；`_max_wrap_lines()` 仅为决定 kv 降级模拟了换行，渲染路径本身从未换行——两处不一致。超宽数据行随后被布局层 word-wrap 二次折行在 cell 中间，边框错乱。
2. **次因：`src/ink/layout/measure.py` `wcswidth` 含 `\n` 返回 0** — upstream wcswidth 遇控制字符返回 -1，被 `w if w >= 0 else 0` 归 0，实测 `string_width("a\nb") == 0`。任何含换行文本的宽度测量全为 0，会以另一种方式打乱列宽/padding（本次案例未直接踩中，同一批修掉）。

## 需求（MVP，最小增量）

1. `_render_table` bordered 路径：画数据行前用 `wrap_text` 把每个 cell 折到该列列宽；同一行的所有 cell 按该行最大行数对齐，逐行补 padding、每行完整画 `│` 分隔——渲染结果与 `_max_wrap_lines()` 的模拟假设一致。边框行（`_hline`）不变。
2. `wcswidth`：字符串含 `\n`（及其他被 upstream 判 -1 的控制字符）时不再整体归 0，按行拆开取各行宽度 max。
3. 回归测试：以复现脚本两个 case（超长单行 cell 表格 @ 110/150/178；硬折行源表格）转成 pytest 用例，断言 (a) 每条数据行行首行尾都有边框字符、(b) 所有行显示宽度一致、(c) 无 cell 中途断行。

## 非目标

- 不改 Regime A/B/C 的列宽分配算法本身。
- 不改 kv 降级阈值与 key-value 渲染。
- 不动消费方（jarvis）代码；发版后 jarvis 单独升 pin。

## 验收

- 上述回归测试通过；现有 `tests/` 中 markdown/table 相关测试全绿。
- jarvis 仓库的复现脚本三宽度输出均为边框对齐（手动核对）。

## 发版与收尾

- 按 `RELEASE.md` bump 0.3.2 + tag；jarvis 侧随后把依赖 pin 从 v0.3.1 升到 v0.3.2（在 jarvis 仓库做，不在本任务内）。
