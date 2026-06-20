# PyInk Phase 3 — content rendering

## Goal

为 Jarvis AI 助手 TUI 提供完整的内容渲染能力：流式文本、Markdown、代码高亮、Diff。Phase 3 完成后，PyInk 能搭出 Claude Code 风格的对话 UI（AI 流式回复 + Markdown 格式 + 语法高亮代码 + 文件编辑 diff）。

## Background

PyInk 已交付：
- MVP（534 测试）：signals + 6 内置组件 + flex + ANSI + render 管线
- Examples 补全（13 examples）：覆盖所有 MVP 能力
- Phase 2（693 测试）：Spinner/Link/Divider externals + Context 系统 + use_focus/use_box_metrics hooks + measure_element API

Phase 3 补 4 个内容渲染组件，全部在 `pyink/externals/`：
- **StreamingText**：无依赖
- **Markdown**：`markdown-it-py` optional dep
- **HighlightedCode**：`pygments` optional dep
- **StructuredDiff**：`difflib` stdlib

## Decisions (ADR-lite)

### Decision 1: Markdown 解析用 markdown-it-py

**Context**: Python Markdown 解析库选择。常见选项：`markdown-it-py`（CommonMark 标准 + 扩展）、`markdown`（Python-Markdown，老牌）、`mistune`（速度）。

**Decision**: 用 `markdown-it-py`。
- CommonMark 兼容（跟 GitHub/VSCode 一致）
- 流式解析友好（增量 token，适合 AI token-by-token）
- 扩展系统成熟（tables/strikethrough/task lists）
- 与 mdit-py-plugins 生态配合

**Consequences**:
- ✅ 标准兼容
- ✅ 流式友好
- ❌ 拖一个中等依赖（~200KB），需要 optional-dependencies 隔离

### Decision 2: 代码高亮用 Pygments

**Context**: Python 语法高亮库。`pygments`（事实标准，500+ lexers）、`tree-sitter`（精确但需 grammar 包）、`rich.syntax`（包装 pygments）。

**Decision**: 用 `pygments`。
- 不需要 grammar 包，开箱即用
- 500+ lexers 覆盖所有主流语言
- token 类型清晰，映射到颜色简单
- 跟 markdown-it-py 的 highlight 插件兼容

**Consequences**:
- ✅ 零配置支持多语言
- ✅ mature 稳定
- ❌ 拖较大依赖（~5MB），必须 optional
- ❌ 不如 tree-sitter 精确（如错误恢复、增量解析）

### Decision 3: Markdown 检测代码块时智能降级

**Context**: Markdown 渲染时遇到 code block，如果 `pyink[highlight]` 没装，怎么办？

**Decision**: 自动检测 HighlightedCode 是否可用：
- 装了 `pyink[highlight]` → 用 HighlightedCode 渲染（语法着色）
- 没装 → 降级为纯 Text + dimColor（保持可读）

Markdown 不强制依赖 HighlightedCode，反之亦然。

### Decision 4: Diff 用 stdlib difflib

**Context**: 文件编辑 diff 显示。

**Decision**: 用 `difflib`（stdlib，无依赖）。
- unified_diff / ndiff 算法够用
- 不需要 `diff-match-patch` 之类的字符级 diff
- 行级 diff 已经够清晰

### Decision 5: StreamingText 用 signal buffer + 可选平滑展开

**Context**: AI 流式回复需要展示逐字/逐 token 出现的效果。

**Decision**:
- **基本用法（默认）**：`buffer = signal("")` + `Text(lambda: buffer.value)` 已经能流式（信号变 → rerender）。StreamingText 提供便利封装。
- **StreamingText 组件**：包 buffer + 可选 cursor + 可选平滑展开（每 N ms 显示 +1 字符，模拟打字）
- 实现用 use_interval 驱动展开

**Consequences**:
- ✅ 简单场景就是 signal + Text，无新概念
- ✅ StreamingText 提供 UX 增强（cursor、平滑）
- ❌ 平滑展开需要 buffer 比当前显示更长——内部维护 `revealed_count`

## Requirements

### 必交付（6 PRs）

#### PR1: StreamingText external

`src/pyink/externals/streaming_text.py`：
```python
def StreamingText(
    buffer: Signal[str] | Callable[[], str] | str,
    *,
    cursor: str | None = None,        # 默认 None，可设 "▋" 或 "|"
    cursor_color: str | None = None,
    reveal_speed: int = 0,             # chars per second; 0 = 即时显示全部
    color: str | None = None,
    **text_props,
) -> Element:
    """流式文本展示。
    - buffer：源（signal 或 callable 在 layout 时求值，str 是静态）
    - cursor：末尾光标字符
    - reveal_speed>0：每秒显示 N 字符，模拟打字
    - revealSpeed=0：buffer 变化立即显示全部
    """
```

依赖：use_interval（Phase 2 PR1）

#### PR2: HighlightedCode external

`src/pyink/externals/highlighted_code.py`：
```python
PYGMENTS_TOKEN_COLORS = {
    "Token.Keyword": "magenta",
    "Token.String": "green",
    "Token.Comment": "brightBlack",  # dim
    "Token.Number": "cyan",
    "Token.Name.Function": "blue",
    "Token.Name.Class": "yellow",
    "Token.Operator": "red",
    "Token.Punctuation": None,  # 默认色
    # ... 完整 Pygments token tree
}

def HighlightedCode(
    code: str,
    *,
    language: str = "text",  # 自动检测如果 "text" 或 "auto"
    theme: dict | None = None,  # 覆盖默认颜色
    line_numbers: bool = False,
    **text_props,
) -> Element:
    """代码语法高亮。
    - lazy import pygments
    - 缺 pygments 抛友好 ImportError："pip install pyink[highlight]"
    """
```

依赖：pygments（optional）

#### PR3: Markdown basic renderer

`src/pyink/externals/markdown.py`：
```python
def Markdown(
    source: str | Signal[str] | Callable[[], str],
    *,
    theme: dict | None = None,  # 样式覆盖（heading_color, code_bg, etc.）
    **box_props,
) -> Element:
    """Markdown 渲染。
    - lazy import markdown_it
    - 缺 markdown_it 抛友好 ImportError："pip install pyink[markdown]"

    支持的 markdown 元素：
    - 标题（h1-h6，带颜色 + bold）
    - 段落
    - 强调（bold/italic/strikethrough）
    - 行内 code
    - 链接（用 Link external 渲染 OSC 8）
    - 列表（ordered/unordered，缩进）
    - 代码块（用 HighlightedCode 如果可用，否则纯 Text）
    - 引用块（blockquote，缩进 + dimColor）
    - 水平线（用 Divider external）
    - 表格（基本支持，column 对齐）
    """
```

依赖：markdown-it-py（optional）+ Link + Divider + 可选 HighlightedCode

#### PR4: Markdown + HighlightedCode 集成

修改 PR3 的 Markdown，让 code block 检测 HighlightedCode 可用性：
- 可用 → 用 HighlightedCode(language=lang, theme=...)
- 不可用 → 降级 Text(code, dimColor=True) + Box 包装

加测试覆盖：
- 装 pygments + markdown 时 code block 高亮
- 只装 markdown 时 code block 纯文本
- 各种语言代码块（Python/JS/SQL/YAML/JSON 等）

#### PR5: StructuredDiff external

`src/pyink/externals/diff.py`：
```python
def StructuredDiff(
    before: str,
    after: str,
    *,
    language: str = "text",       # 可选高亮
    context_lines: int = 3,       # 显示多少行 context
    show_header: bool = True,     # 显示 file path header
    **box_props,
) -> Element:
    """文件编辑 diff 显示。
    - 用 difflib.unified_diff 计算
    - 行级染色：+ green / - red / @@ magenta / context default
    - 可选用 HighlightedCode 给 +/- 行加语法高亮（如果可用）
    """
```

依赖：difflib（stdlib）+ 可选 HighlightedCode

#### PR6: Examples + README + integration tests

5 个新 examples：
- `examples/markdown/markdown_demo.py` —— 各种 markdown 元素
- `examples/highlighted-code/highlighted_code_demo.py` —— 多语言代码高亮
- `examples/streaming-text/streaming_text_demo.py` —— 模拟 AI 流式回复
- `examples/diff/diff_demo.py` —— 文件编辑 diff
- `examples/markdown-code-integration/integration_demo.py` —— Markdown 含代码块

更新：
- `pyproject.toml`：加 `[project.optional-dependencies]` 的 markdown/highlight/all
- `tests/test_examples.py`：+5 测试
- `README.md`：API 表 + examples 索引 + 安装说明（`pip install pyink[markdown]`）

## Acceptance Criteria

- [ ] 6 PRs 全部交付
- [ ] 4 个新 externals（StreamingText/Markdown/HighlightedCode/StructuredDiff）+ 测试
- [ ] `pyproject.toml` optional-dependencies 配置正确
- [ ] lazy import 错误信息清晰（"pip install pyink[xxx]"）
- [ ] 5 个新 examples 跑通
- [ ] mypy strict + ruff 全绿
- [ ] 全部测试通过（应该 693 + ~80 新 = 773+）
- [ ] 不破坏 Phase 1-2 现有测试
- [ ] README 完整更新

## Definition of Done

- 6 PRs（每个含 implement + check + commit）
- 集成测试覆盖
- 5 个 examples 真实终端验证
- 全部 quality gates 绿
- 公共导出（externals 不默认导出，用户显式 import）

## Out of Scope

- 不实现 tree-sitter 高亮（Pygments 已够 MVP）
- 不实现 Markdown 的 GFM 扩展（task lists、footnotes、math 等）—— 基础 CommonMark + tables 够用
- 不实现流式 Markdown（增量解析）—— 整段重渲就行
- 不写 VirtualList（Phase 5）

## Technical Notes

### 参考实现

- ink 第三方包：`D:\Projects\github\ink\components\` 下：
  - `ink-markdown/` —— Markdown（用 marked-terminal）
  - `ink-syntax-highlight/` —— 代码高亮
- Claude Code：`D:\Projects\github\claude-code\src\components\` 下：
  - `Markdown.tsx` —— 流式 Markdown
  - `HighlightedCode/` —— 代码块
  - `StructuredDiff/` —— 文件 diff
  - `FileEditToolDiff.tsx` —— 工具调用 diff
- Pygments 文档：https://pygments.org/docs/tokens/
- markdown-it-py：https://github.com/executablebooks/markdown-it-py

### 关键约束

- Python 3.11+，mypy strict + ruff
- 全同步 + 线程并发
- 函数组件 only
- Children 位置参数
- 不写 `\x1b[2J`
- style props 支持 callable
- externals 用 lazy import + 友好 ImportError

## Implementation Plan

6 PRs（每 PR implement → check → commit）：

1. **PR1**: StreamingText（无依赖，简单）
2. **PR2**: HighlightedCode（pygments optional，独立）
3. **PR3**: Markdown basic（markdown-it-py optional）
4. **PR4**: Markdown + HighlightedCode 集成
5. **PR5**: StructuredDiff（difflib stdlib，可选集成 HighlightedCode）
6. **PR6**: Examples + README + 集成测试

预计 2-3 周。

## Research References

（暂无；可在 PR3 前派 trellis-research 调研 markdown-it-py AST 渲染模式）
