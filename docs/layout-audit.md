# 核心布局逻辑分析报告

> 范围：`src/ink/layout/flex.py`（`_layout_node` / `_layout_row` / `_layout_column` / `_distribute_main` / `_position_main`）与 `src/ink/layout/render_layout.py`（绘制与裁剪），结合本次两个修复（`Submitted:` 行右侧溢出、多行输入光标视口）。

## 一、当前实现已处理得当的部分

- **多 pass 收敛**：`_layout_node` → `_layout_row/column` 做了 measure → grow/shrink 分配 → 按分配重排 → stretch → 重定位 的多趟流程，配合 `_wrapped_width` / `_rendered_width` 记账避免重复 wrap，对常见场景是对的。
- **横向截断 + 光标跟随**：`text_input.py` 的 `_truncate_line_around_cursor` + 本次新增的纵向 `_clip_lines_to_height` 形成了横 / 纵两个方向的"光标始终可见"。
- **边框盒子的整体性保护**：`render_layout._paint_node`（约 251 行）有"高度不足以容纳上下边框就整块不画"的守卫，避免悬空半边框。
- **ANSI 宽度无关**：measure / wrap 全程 ANSI-aware，转义不计入宽度。

## 二、仍然存在的潜在问题（按严重度排序）

### 1.【较严重】垂直空间不足时会出现行重叠，而非干净裁剪

这是在 `rows=16` 复现到的 `(not submitted yet)me=5 notes=...` 现象的根源，是本次未触及的更底层问题。

链路：

- `_distribute_main`（`flex.py:1267`）shrink 时 `out[i] = max(0, sizes[i] - shrink_amount)`，**没有 min-content 下限**。一个 1 行的文本被分到 0 高度是可能的。
- `_position_main`（`flex.py:1316`）在 `free < 0`（超预算）时退回 flex-start 紧排，用的是被压缩后的尺寸；0 高度的子节点不占位 → 多个子节点落到同一个 `y`。
- `render_layout._paint_text`（约 306 行）：`if node.height > 0 and len > node.height` 才裁剪；当 `node.height == 0` 时**完全不裁剪**，照样画出那 1 行文本。
- `_Grid.put` 是覆盖写，两个文本落在同一行就会交错合并 → 乱码式重叠。

也就是说：**布局没有把子节点裁剪到父节点的内容盒**。`render_layout` 只在根 grid 的 `width/height` 边界处丢弃（`to_string`），父盒子内部的溢出 / 重叠不受约束。多行 input 加 `rows` 上限后更难触发它，但任何整体超预算的界面仍会重现。

建议方向（任选其一）：

- shrink 时给文本子节点设 min-content 下限；
- 或让 `_paint_text` 在 `node.height == 0` 时也不绘制；
- 或在 `_to_layout_tree` / 绘制阶段把子节点 clip 到父内容盒。

### 2.【中】grow / shrink 的取整误差（off-by-one）

`_distribute_main` 对每个 share 独立 `int(round(...))`（`flex.py:1293`、`flex.py:1311`），各自取整后总和不一定等于 `free`。三等分 10 → 3/3/3=9，丢 1 格。父盒子宽是精确的，子节点和却差 1 → 末尾留缝或最后一个子节点偏短。仓库已有对应 xfail（`test_flex_shrink_equally` 等），属已知 MVP 取舍，但在紧排版里会有 1 格抖动。

### 3.【中】单趟近似，不像 yoga 那样迭代收敛

shrink 只做一趟，没有"min-content 违例 → 重新分配"的迭代。嵌套、约束冲突的 flex 可能不完全收敛（多个 `test_align_content_*`、`test_flex_wrap_*` 已 xfail 标注 MVP 不支持）。

### 4.【中】宽字符（CJK）下光标列与截断可能错位

`text_input.py` 约 1182 行有注释承认：cursor SGR 是按**字符偏移**插入 `_build_displayed_line`，而 `_cursor_visible_column` 用的是**显示宽度**。两者在宽字符行混排时会有 1 格级别的偏差，横向截断点也可能偏。这是已记录的已知限制，但测试用例里正好含中文 `上点上点`，值得留意。

### 5.【低】本次新增逻辑的两点假设 / 耦合

- `_wrapped_width` 在 renderer 重跑时清空（`flex.py` 约 899 行）——依赖"最后一趟用的是最终宽度"。对 truncate 成立；对 `wrap` 模式的 callable 文本叶子（如某些反应式渲染）理论上可能让高度收敛不稳定。目前只有 Markdown 走这条路且测试通过，但这是一个隐含契约。
- `_ink_scroll` 是挂在**公共 `Text`** 上的私有 prop（`text_input.py` 约 1267 行 + `render_layout` 读取）。功能上惰性、安全，但它把一个内部约定暴露在公共组件的 props 面上，且通过"共享可变 dict 侧信道"传递动态光标行——可读性 / 可维护性偏弱，文档里没有体现。

### 6.【低 / 代码异味】`main_is_fixed` 计算了却没被使用

`_layout_row/column` 构造了 `main_is_fixed` 传给 `_distribute_main` 的 `fixed` 形参，但 `_distribute_main`（`flex.py:1267`）从头到尾没用 `fixed`，只看 `flex_grow / flex_shrink`。死参数，建议清理。

### 7.【低 / 性能】重复布局 / 重复渲染

- 每次 `_paint_now` 之外，`_effect_body`（`instance.py:271`）还会跑一趟"订阅布局"并丢弃结果 → 每次刷新至少 2 趟 `layout()`。
- 单趟 `layout()` 内文本 renderer 会因 `ctx_width` 变化被多次调用。Markdown 有 memo，TextInput 的 `_render_lines` 没有，属轻量重复。正确性无碍，规模大时是开销。

## 三、总体评价

核心布局对"内容能放下"的常规场景考虑是周全的，本次两个修复（`Submitted:` 溢出、多行光标视口）都打在了真正的根因上，并补了回归测试。

**最值得后续投入的是第 1 点**——"超预算时干净裁剪而非重叠"，它是一类问题（任何界面在小终端下都可能撞上）；目前的多行 `rows` 上限只是把触发概率降低了，没有从布局层根治。

## 四、gap 与 collapsed children（已对齐 CSS flexbox）

### 背景

PyInk 的 `gap` flexbox prop 早期按 `len(children)` 计数 gap slot——N 个 child 固定 N-1 个 gap，不区分 collapsed（main-axis 测量为 0）的 child。CSS flexbox 标准要求 gap 只在 *visible items* 之间生效（[css-flexbox-1 §8.4](https://www.w3.org/TR/css-flexbox-1/#gutters)、css-align 的 gap 定义）。React Ink (TS) 走 yoga/浏览器引擎天然遵守。

Jarvis 侧的痛点：`Box(*dynamic_widgets, gap=1)` 在空闲状态下 4 个 `Text(..., collapseIfEmpty=True)` 子节点都返回 `""`，按 CSS 应该全部收光不占行；但旧实现会显示 N-1 行空白（`Worked for 3m 31s` 与底部 status bar 之间多出数行空白就是这个 bug）。

### 当前行为（已修复）

`gap` 只在相邻 visible children（main-axis 测量 > 0）之间生效：

- 连续 collapsed children 之间、collapsed 和 visible 之间不加 gap
- 全部 collapsed → `total_gap = 0`，父盒高度收光
- 全部 visible → 行为等价于"按 `len(children)` 计 gap"的旧实现

覆盖范围：`_layout_row` / `_layout_column` 的 `total_gap` 计算、`_compute_min_content_main` 的 gap 项、`_position_main` 的 6 个 justify 分支（`flex-start` / `flex-end` / `center` / `space-between` / `space-around` / `space-evenly`）。

### 触发 collapsed 的唯一机制

PyInk 当前没有 `display: none` 或 `visibility: hidden` prop。`collapseIfEmpty=True` 是把空文本叶子的 main-axis 测量压到 0 的唯一手段（见 `_layout_node` 文本分支 + `_compute_min_content_main` 的 text 分支）。任何 main-axis 测量为 0 的 child 都会被 gap 跳过——例如未来引入新的 collapse 机制时无需再改 gap 逻辑。

### 实现要点

- **`_visible_children_mask(main_sizes)`**：`[s > 0 for s in main_sizes]`——margin 仍算 visible（margin 让 child 即使内容为 0 也保留 gap slot，符合 CSS 语义）。
- **`_gap_after_index(visible_mask, i, gap)`**：判断 "child i 之后是否有 visible child"，用于决定 cursor 推进时是否加 main_gap。
- **`_has_next_visible(visible_mask, i)`**：用于 `space-between` 的 `between` 分布——分布空间独立于 `main_gap` 是否设置（即 `gap=0` 时 `space-between` 仍分布）。
- **`space-around` / `space-evenly` 的 `between` 计算**：用 `visible_n` 替代 `len(children)`，确保 collapsed 不消耗分布 slot。

### 已知遗留

- `flexWrap + gap`（`test_gap_wrap` 仍 xfail）——多行 wrap 路径不在 MVP 范围，gap 的 collapsed 处理与单行 nowrap 一致。
- `_position_main` 的 6 个 justify 分支用了同样的 visible-mask 推进模式，但实现是顺序写的（每个分支自己 walk cursor）；未来若新增 justify 模式，需要照同样的 collapsed 处理模板写一遍。

