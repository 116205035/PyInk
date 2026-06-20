# PyInk Phase 2 — externals + new hooks

## Goal

在 MVP 基础上扩展 PyInk 能力，覆盖 Jarvis TUI 高频场景。Phase 2 完成后，PyInk 能搭出 Claude Code 风格的基础对话 UI（带 spinner 状态、可点链接、多输入焦点切换、动态布局）。

## Background

PyInk MVP（task `06-19-pyink-mvp`）+ examples 补全（task `06-20-pyink-examples-mvp-fits`）已交付：
- 内置 6 组件：Box/Text/Newline/Spacer/Static/Transform
- 3 hooks：use_input/use_app/use_window_size
- 534 测试通过 + 22 xfailed
- 13 个 examples 演示所有 MVP 能力

Phase 2 补：
- **Externals**：Spinner / Link / Divider
- **Hooks**：use_interval / use_focus / use_focus_manager / use_box_metrics
- **API**：measure_element
- **新基础设施**：Context 系统（use_focus 依赖）

## Decisions (ADR-lite)

### Decision 1: Context 系统用 ContextVar 栈实现

**Context**: use_focus 需要"树作用域"——Provider 只影响后代组件，Consumer 读最近的 Provider。React Context / Vue provide-inject / SolidJS context 都是这个语义。

**Decision**: 用 `contextvars.ContextVar` 维护"当前 Provider 链"栈。
- `create_context(default)` 返回 Context 对象（含默认值 + 唯一 ID）
- Provider mount 时 push (context_id, value) 到栈，unmount 时 pop
- `use_context(ctx)` 在组件 mount 时读栈顶匹配 ID 的值
- 实现：ContextVar 持有 `list[tuple[int, Any]]`，每个 Provider 用 `append`/`pop`

**Consequences**:
- ✅ 实现 ~50 行
- ✅ 树作用域正确（嵌套 Provider 自动覆盖）
- ✅ ContextVar 天然 async-safe（虽然 PyInk 全同步，但为未来留口）
- ❌ 栈 push/pop 顺序必须严格（mount/unmount 必须配对）—— reconciler 保证

### Decision 2: measure_element 用 LayoutNode ref 实现

**Context**: ink 的 `measureElement(ref)` 返回 `{width, height, left, top}`。需要 ref 系统能在 layout 后回填元素引用。

**Decision**:
- LayoutNode 加 `ref_id: str | None` 字段
- Box 接受 `ref` prop（`ref` 是 PyInk 的 `Ref[LayoutNode]`）
- layout 阶段：如果 Box 有 ref，把对应 LayoutNode 写到 ref.value
- `measure_element(ref)`：读 `ref.value`，返回其 width/height/left/top
- `use_box_metrics(ref)`：返回当前 metrics + `has_measured` 标志，在 effect 内每次 layout 后更新

**Consequences**:
- ✅ 复用 PR1 的 `ref()`，不引入新概念
- ✅ LayoutNode 已存在（PR3 实现），加字段即可
- ❌ ref.value 在首次 layout 后才有效（之前是 None）—— use_box_metrics 用 has_measured 标志表示

### Decision 3: Spinner 用 use_interval + computed 字符索引

**Context**: Spinner 显示一系列 frame 字符（dots/line/dots2 等），按 interval 切换。需要定时器。

**Decision**:
- 新 hook `use_interval(callback, interval_ms)` —— 定时调用 callback
- Spinner 用 `frame = signal(0)` + `use_interval(lambda: frame.value += 1, 80)`
- 显示 `Text(lambda: frames[frame.value % len(frames)])`
- 多种内置 frame 序列：dots/line/dots2/dots3/dots4/dots5/dots6/dots7/dots8/dots9/dots10/dots11/dots12/arc/line2/box/bouncingBall/...

**Consequences**:
- ✅ use_interval 通用，Spinner 之外也可用（计时显示、轮询等）
- ✅ Spinner 实现极简（~50 行）
- ✅ frame 用 signal 触发 rerender 自动

## Requirements

### 必交付（8 PRs）

#### PR1: `use_interval` hook

`src/pyink/hooks/interval.py`：
```python
def use_interval(
    callback: Callable[[], None],
    interval_ms: int,
    *,
    is_active: bool = True,
) -> Callable[[], None]:
    """定时调用 callback。
    - 内部启动 daemon thread，每 interval_ms 调一次 callback
    - is_active=False 暂停
    - 返回 dispose（自动注册到 ComponentInstance，unmount 时清理）
    """
```

测试 `tests/hooks/test_use_interval.py`：
- 基础触发
- is_active=False 不触发
- dispose 停止
- unmount 自动清理
- 多个 use_interval 并存
- interval_ms=0 边界

#### PR2: `Spinner` external

`src/pyink/externals/spinner.py`：
```python
SPINNERS = {
    "dots": ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"],
    "line": ["-", "\\", "|", "/"],
    "dots2": ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"],
    # ... 至少 12 种（参考 cli-spinners 包）
}

def Spinner(*, type: str = "dots", color: str | None = None) -> Element:
    """加载 spinner。"""
```

依赖：use_interval

测试 `tests/externals/test_spinner.py`：
- 各 spinner type 渲染首帧
- 帧切换（mock use_interval）
- color 应用
- 未知 type fallback

#### PR3: `Link` external

`src/pyink/externals/link.py`：
```python
def Link(
    *children,
    url: str,
    **props,
) -> Element:
    """OSC 8 超链接。
    - 渲染为 \\x1b]8;;URL\\x1b\\\\TEXT\\x1b]8;;\\x1b\\\\
    - children 是链接文字
    - 其他 Text props 透传（color/bold 等）
    """
```

测试：
- 基础链接序列正确
- 颜色 + 链接组合
- 嵌套（Link 在 Text 里）

#### PR4: `Divider` external

`src/pyink/externals/divider.py`：
```python
def Divider(
    *,
    label: str | None = None,
    direction: str = "horizontal",  # "horizontal" | "vertical"
    border_style: str = "single",
    color: str | None = None,
    width: int | None = None,
) -> Element:
    """分隔线。
    - horizontal：一行 ─────
    - vertical：一列 │（在 row 容器内）
    - label：分隔线上加文字（"── My Section ──"）
    """
```

测试：
- 水平 divider
- 带 label
- 垂直 divider
- 颜色

#### PR5: Context 系统 + use_context hook

`src/pyink/core/context.py`：
```python
class Context(Generic[T]):
    """Provider/Consumer 上下文。"""
    default: T
    id: int  # 唯一 ID

def create_context(default: T) -> Context[T]:
    """创建 Context。"""

def Provider(ctx: Context[T], value: T, *children) -> Element:
    """Provider host element。mount 时 push value 到栈，unmount 时 pop。"""

def use_context(ctx: Context[T]) -> T:
    """读栈顶最近 Provider 的 value；栈空返回 default。"""
```

集成到 reconciler：
- Provider 是 host "provider"
- mount 时：`_context_stack.append((ctx.id, value))`
- unmount 时：`_context_stack.pop()`（确认 pop 的是自己）

测试 `tests/core/test_context.py`：
- 基础 Provider + use_context
- 嵌套 Provider（内层覆盖外层）
- 跨组件边界（A 提供，B 在子树读）
- 默认值（无 Provider）
- unmount pop 顺序正确

#### PR6: `use_focus` + `use_focus_manager` hooks

`src/pyink/hooks/focus.py`：
```python
def use_focus(options: dict | None = None) -> FocusHandle:
    """订阅焦点系统。
    - 返回 {is_focused, focus_self, blur}
    - options: {auto_focus: bool, id: str | None, is_active: bool}
    - 注册到当前 FocusManager context
    """

def use_focus_manager() -> FocusManagerHandle:
    """管理焦点。
    - 返回 {focus_next, focus_previous, focus(id), enable_focus, disable_focus, active_id}
    - 内部维护 FocusContext（Provider 注入到根）
    """
```

依赖：Context 系统（PR5）

实现：内部 FocusManager 状态用 signal，组件 mount 时注册、unmount 时注销，Tab/Shift+Tab 切换。

测试 `tests/hooks/test_focus.py`：
- 单组件 use_focus（无 manager）—— is_focused 默认 True
- 多组件焦点切换（focus_next/previous）
- Tab 默认绑定（可选，可只暴露 API）
- focus(id) 跳转
- enable/disable focus
- unmount 自动注销

#### PR7: `measure_element` API + `use_box_metrics` hook

`src/pyink/api/measure.py`（或 `core/measure.py`）：
```python
def measure_element(ref: Ref[LayoutNode]) -> BoxMetrics:
    """读 ref 指向的 LayoutNode 的尺寸。
    返回 {width, height, left, top, has_measured}。
    ref.value 为 None 时 has_measured=False。
    """
```

`src/pyink/hooks/box_metrics.py`：
```python
def use_box_metrics(ref: Ref[LayoutNode]) -> BoxMetrics:
    """订阅元素尺寸变化。
    - 内部用 effect 监听 layout signal
    - 每次 layout 后 ref.value 更新，触发 hook 返回新 metrics
    """
```

集成：Box 接受 `ref` prop，layout 阶段把 LayoutNode 写到 ref.value。

测试：
- ref 在 mount 后回填
- measure_element 返回正确尺寸
- use_box_metrics 在 layout 变化时更新
- has_measured 在首次 layout 前为 False
- 多个 ref 各自正确

#### PR8: Examples + README + 集成测试

5 个新 examples：
- `examples/spinner/spinner_demo.py` —— 各种 spinner 类型 + 颜色
- `examples/link/link_demo.py` —— 各种 OSC 8 链接（URL、文件路径）
- `examples/divider/divider_demo.py` —— 水平/垂直 + label
- `examples/use-focus-real/use_focus_demo.py` —— 用真正的 use_focus hook（对比现有手写版）
- `examples/measure-element/measure_demo.py` —— 动态测量 Box 尺寸

更新：
- `tests/test_examples.py`：+5 测试
- `README.md`：API 表加新组件/hooks，examples 索引加 5 项
- `src/pyink/__init__.py`：导出新公共 API

## Acceptance Criteria

- [ ] 8 个 PR 全部交付
- [ ] 7 个新模块（use_interval/Spinner/Link/Divider/Context/use_focus/measure）实现 + 测试
- [ ] 5 个新 examples 跑通
- [ ] mypy strict + ruff 全绿（含 examples）
- [ ] 全部测试通过（应该 534 + ~80 新 = 614+）
- [ ] 不破坏 MVP 现有测试
- [ ] README 完整更新

## Definition of Done

- 8 PRs（每个含 implement + check + commit）
- 集成测试覆盖
- 5 个 examples 真实终端验证
- 全部 quality gates 绿
- API 公共导出清晰（用户能 `from pyink import Spinner, use_focus` 直接用）

## Out of Scope

- 不实现 Phase 3+ 内容渲染（Markdown / 代码高亮 / 流式文本 / Diff）
- 不实现 Phase 4 输入组件（TextInput / SelectInput externals / ConfirmInput）—— select-input example 是手写版，不在本 task 升级
- 不实现 Phase 5 性能（VirtualList / 增量渲染）
- 不重写 MVP 已有实现（除非新功能暴露 bug）

## Technical Notes

### 参考实现

- ink externals：`D:\Projects\github\ink\components\` 下：
  - `ink-spinner/` —— Spinner（参考 frame 序列）
  - `ink-link/` —— Link（OSC 8）
  - `ink-divider/` —— Divider
- ink hooks：`D:\Projects\github\ink\ink-master\src\hooks\` 下：
  - `use-focus.ts` + `use-focus-manager.ts`
- ink API：`D:\Projects\github\ink\ink-master\src\` 下：
  - `measure-element.ts`
- pyinkcli 参考（不抄）：`D:\Projects\github\pyinkcli-main\src\pyinkcli\packages\react_context.py`

### 关键设计约束

- Python 3.11+，mypy strict + ruff
- 全同步 + 线程并发（MVP Decision 10）
- 函数组件 only（MVP Decision 7）
- Children 位置参数（MVP Decision 8）
- 不写 `\x1b[2J`（MVP Decision 3）
- style props 支持 callable（MVP reactive props）

## Implementation Plan

8 PRs（每 PR 自带 implement → check → commit 流程）：

1. **PR1**: use_interval hook（独立）
2. **PR2**: Spinner external（依赖 PR1）
3. **PR3**: Link external（独立）
4. **PR4**: Divider external（独立）
5. **PR5**: Context 系统（独立基础设施）
6. **PR6**: use_focus + use_focus_manager（依赖 PR5）
7. **PR7**: measure_element + use_box_metrics（独立）
8. **PR8**: Examples + README + 集成测试（依赖前 7）

预计 2-3 周。

## Research References

（无；本任务设计来自 PyInk PRD Phase 2 路线图 + ink 上游源码分析）
