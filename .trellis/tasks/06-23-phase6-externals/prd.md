# Phase 6: Additional externals

## Goal

移植 5 个 ink 生态组件到 PyInk externals：TaskList / Gradient / ProgressBar / Table / BigText。

## Background

PyInk 已发布 0.1.0（13 个 task、1188 测试）。Phase 6 补充 Jarvis 真实场景需要的通用组件。

## Components

### 1. TaskList（最高优先级）

参考 ink-task-list。管理一组 task 状态机：
- pending（queued）: `○`
- running: Spinner
- done: `✓` green
- error: `✗` red
- warning: `⚠` yellow

```python
def TaskList(
    tasks: list[Task] | Signal[list[Task]],
    *,
    on_complete: Callable[[], None] | None = None,
    **box_props,
) -> Element:
    """任务列表。每个 task 有 label + state。"""

# Task dataclass
@dataclass
class Task:
    label: str
    state: Literal["pending", "running", "done", "error", "warning"] = "pending"
    output: str | None = None  # 可选输出行
```

### 2. Gradient

参考 ink-gradient。渐变色文字（ANSI truecolor 插值）：

```python
def Gradient(
    *children,
    colors: list[str],  # 渐变色端点（如 ["red", "blue"]）
    **text_props,
) -> Element:
    """渐变色文字。每个字符的颜色在 colors 间线性插值。"""
```

无依赖，纯 ANSI truecolor。

### 3. ProgressBar

参考 ink-progress-bar。进度条：

```python
def ProgressBar(
    *,
    value: float | Signal[float] | Callable[[], float],  # 0.0 - 1.0
    width: int = 30,
    character: str = "█",
    remaining_character: str = "░",
    color: str | None = None,
    show_percentage: bool = True,
    **box_props,
) -> Element:
    """进度条。"""
```

### 4. Table

参考 ink-table。简单表格：

```python
def Table(
    *,
    data: list[list[str]] | list[dict[str, str]],
    columns: list[str] | None = None,  # 表头（dict 模式自动取 keys）
    padding: int = 1,
    **box_props,
) -> Element:
    """表格。每列按最长内容对齐。"""
```

### 5. BigText

参考 ink-big-text。ASCII 艺术大字：

```python
def BigText(
    text: str,
    *,
    font: str = "block",  # font name from the font registry
    align: str = "left",  # "left" | "center" | "right"
    color: str | None = None,
    **box_props,
) -> Element:
    """ASCII 艺术大字。内置几个 font。"""
```

内置 font（从 figlet 抄或简化）：
- "block"（最常用）
- "simple"
- "tiny"
- "grid"

## Implementation Plan

5 PRs（每 PR 一个组件 + 测试）：

1. **PR1**: TaskList（最复杂 + 最有用）
2. **PR2**: ProgressBar（简单）
3. **PR3**: Gradient（简单）
4. **PR4**: Table（中等）
5. **PR5**: BigText（字体数据 + 渲染）+ examples + README

## Out of Scope

- ink-form / ink-stepper（业务组合，Jarvis 自己拼）
- ink-tab / ink-chart / ink-picture（太复杂）
- ink-virtual-list（已删，wrong abstraction）

## Research References

- ink-task-list: `D:\Projects\github\ink\components\ink-task-list\`
- ink-gradient: `D:\Projects\github\ink\components\ink-gradient\`
- ink-progress-bar: `D:\Projects\github\ink\components\ink-progress-bar\`
- ink-table: `D:\Projects\github\ink\components\ink-table\`
- ink-big-text: `D:\Projects\github\ink\components\ink-big-text\`
