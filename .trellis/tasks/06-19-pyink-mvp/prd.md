# PyInk MVP — Python ink-style TUI framework

## Goal

实现一个 Python 版的 ink（React 风格的命令行 UI 框架），用于：

1. 替换 Jarvis 项目（`D:\Projects\Jarvis`）当前使用的 Textual 作为 TUI 前端
2. 对齐 Claude Code（`D:\Projects\github\claude-code`）的前端交互模式，便于借鉴其 UX

PyInk 是基础设施层；Jarvis 是业务层。两者分开，互不耦合。

## What I already know

### 上游参考项目

- **ink** v7.1.0 主仓库：`D:\Projects\github\ink\ink-master\`（5,178 行核心 TS，不含 components/hooks）
- **ink 生态 components**：`D:\Projects\github\ink\components\`（29 个第三方仓库已 git clone）
- **Claude Code 源码**：`D:\Projects\github\claude-code\`（fork 了 ink + 加了大量自有特性，~20k 行 fork + 113 个业务组件）
- **pyinkcli**（他人 Python 移植）：`D:\Projects\github\pyinkcli-main\`（133 文件，18k 行，但 API 不 Pythonic，**作为参考不 fork**）

### Claude Code 实际使用的 ink API 表面（grep 数据）

- 组件：`Box`、`Text`（几乎每个文件）、`Link`、`Newline`、`Ansi`、`NoSelect`
- Hooks：`useInput`（23 文件 55 处，主力）、`useApp`、`useStdin`、`useInterval`、`useTheme`
- 工具：`measureElement`、`wrapText`
- ink 自带的 `useAnimation` / `useCursor` / `usePaste` / `useFocus` 系列：**Claude Code 一次都没用过**——它自己写了等价的

### Claude Code 是 React Compiler + Bun 编译产物

源码是 post-compile 输出（`$[i]` memo 缓存数组 + `bun:bundle`），不是手写 TSX。**不能直接翻译**，要从模式反推原始结构。

### 终端渲染的本质

- 终端不是浏览器，每帧是 stdout 写入 ANSI 字符串
- 千条消息会卡——所有成熟 TUI 框架都用虚拟滚动（Claude Code 在 ~2800 消息会话里曾每帧 15 万次 stdout 写入，CPU 100%，靠 React.memo + Compiler + VirtualMessageList + optimizer.ts + frame.ts 解决）
- **inline 模式 vs alternate screen** 是两种产品哲学：ink 默认 inline（像 CLI 输出），Textual 默认 alternate screen（像 vim/htop）

## Assumptions (temporary)

- 目标用户主要是作者本人（Python 背景，不是 React 老用户）
- MVP 阶段不追求像素级对齐 Claude Code（够用即可）
- 不依赖任何 alpha 状态的第三方包（避免单点失败）
- Python 3.10+ 即可（不需要追新）

## Decisions (ADR-lite)

### Decision 1: 组件模型用 signals，不用 React hooks

**Context**: Python 没有 JSX，hooks 的"组件函数会被反复调用 + Rules of Hooks"约束对 Python 用户不直观；pyinkcli 用 hooks 导致 API 不 Pythonic。

**Decision**: 采用 signals 模型（参考 SolidJS / Vue 3 / Svelte 5 / Preact Signals）。组件函数只挂载时跑一次，状态包在 signal 对象里，谁读谁订阅。

**Consequences**:
- ✅ 无 Rules of Hooks 约束，更 Pythonic
- ✅ 细粒度订阅，组件级 memo 自动获得（hooks 需要 React.memo + Compiler）
- ✅ 实现简单 30%（无 fiber 树、无 call-order bookkeeping）
- ❌ 从 JS component 翻译有 5-30% 单组件成本（机械翻译：`useState` → `signal`，`useEffect(fn, [])` → `effect(fn)`）
- 退路：如未来某些 component 翻译困难，可在 signals 上加 hooks 兼容层

### Decision 2: 纯 Python flexbox 子集，不接 Yoga

**Context**: PyPI 上 `yoga-layout-python`（pyinkcli 用的）0 ⭐、alpha、纯 Python 翻译、和 pyinkcli 同作者；`poga` 17 ⭐ 9 个月没动；官方 `facebook/yoga` 没有 Python 绑定。

**Decision**: MVP 阶段自研纯 Python flexbox 子集。范围：column/row + padding/margin + gap + width/height + alignItems/justifyContent + 基础 wrap。**参考** yoga-layout-python 源码算法，但不依赖它。

**Consequences**:
- ✅ 零外部依赖风险
- ✅ 完全自控，bug 自己修
- ✅ Claude Code 的核心 UI 排版需求简单，子集够用
- ❌ 性能不如 C++ Yoga（MVP 阶段不瓶颈）
- ❌ 极端情况（百分比 min/max、aspectRatio）暂不支持
- 未来如需精确像素对齐或性能，再换 `poga` 或自己 pybind11 编译 Yoga

### Decision 3: 默认 inline 渲染模式，alternate screen 作为可选项

**Context**: 用户明确要 Claude Code 风格（终端里的对话，scrollback 保留）。Textual 风格（alternate screen，全屏接管）不符合预期。

**Decision**: `render(tree)` 默认 inline；`render(tree, alternate_screen=True)` 或 `<AlternateScreen>` 组件切换。切换时保存/恢复终端状态，scrollback 不丢。

**Consequences**:
- ✅ 匹配 Claude Code 体验
- ✅ 用户 shell 历史和对话历史自然共存
- ✅ alternate screen 留作"全屏对话框/设置面板"用
- 约束：禁用 `\x1b[2J`（全屏清），只能用 `\x1b[<N>A` + `\x1b[2K`（光标上移 + 清行）局部重画
- 约束：进 alternate screen 时要 `atexit.register` 退出钩子，防崩溃卡屏

### Decision 4: 不 fork pyinkcli，从零写但参考其实现

**Context**: pyinkcli API 不 Pythonic（`Box(*children, **props)` + camelCase/snake_case 双支持），但它的 reconciler/hooks runtime/output 解决了硬骨头。

**Decision**: PyInk 从零写。**参考** pyinkcli 的 `reconciler.py`、`hooks/_runtime.py`、`output.py`、`render_node_to_output.py`、ANSI 处理系列——学算法不抄代码。同样参考 yoga-layout-python 学 flex 算法。

**Consequences**:
- ✅ API 自由设计，不被 hooks 包袱绑架
- ✅ 代码风格统一
- ❌ 工作量比 fork + 重写 API 多 1-2 周（总 3-4 周）

### Decision 12: 两阶段 flush 保证 Computed 失效先于 Effect 重跑（PR2 修复）

**Context**: PR1 的 epoch dedupe 机制（Decision 11.7）解决了"effect 同一 flush 内只跑一次"，但没解决**通知顺序**问题。具体场景：effect 同时订阅了 Signal `a` 和派生 Computed `b = computed(a)`，写 `a` 时按订阅顺序通知——effect 的 trigger 比 `b._on_source_changed` 先注册（因为 effect 第一次跑时先读 a 再读 b），所以 effect 先 rerun，此时 `b` 的缓存还是旧值，effect 看到陈旧数据。PR1 的 epoch dedupe 阻止了 `b` 重算后的二次 effect 触发，于是 bug 被锁死。

PR1 测试 35/35 通过是**假象**——其他测试留下的全局状态恰好掩盖了这个 bug，单独跑 `test_computed_in_effect_subscribes_to_both` 就暴露。

**Decision**: 采用**两阶段 flush**（方案 A）：

- **Phase 1**：处理所有 Signal/Computed 的 notification（Computed 标记 dirty + 重算 + 通知下游 Computed）
- **Phase 2**：drain `_deferred_effects` 队列，逐个跑 effect rerun
- Effect 的 `_on_dependency_changed` 在 `_notifying=True` 时不再 inline 执行，而是把自己 append 到 `_deferred_effects` 队列
- 顶层 `_notify` 和 `_flush_batched` 在 phase 1 末尾调用 `_drain_deferred_effects()`
- drain 循环到队列空为止（处理 phase 2 期间的级联写入）

**Consequences**:
- ✅ 处理任意深度依赖链（A→B→C→D，effect 读 A 和 D，D 重算完 effect 才跑）
- ✅ epoch dedupe（Decision 11.7）继续生效——只是它现在作用于 phase 2 而非 inline
- ✅ 公共 API 不变（signal/computed/effect/ref/batch 签名一致）
- ✅ 5 个新 ordering 测试，每个独立验证通过
- ✅ 87 tests pass（PR1 35 + PR1 新增 5 + PR2 42 + PR2 新增 5）
- 实现位置：`src/pyink/core/signal.py` 的 `_deferred_effects` / `_defer_effect` / `_drain_deferred_effects`

### Decision 11: Signals 运行时行为细节澄清（PR1 实现中决出）

**Context**: PR1 实现过程中发现 PRD 对几个 edge case 描述模糊，需要锁定行为供后续 PR（reconciler、scheduler）遵循。

**Decision**:

1. **`effect(deps=[...])` 的 deps 元素是 Signal/Computed 对象**（不是当前的值）。effect 订阅每个 dep；任一 dep 变化触发 effect，重跑时读 `.value` 与上次快照用 `==` 比较，决定是否真的执行 fn。
   - 普通值（非 Signal/Computed）也接受，但**不参与触发**（只在比较时参与）。
   - 这跟 SolidJS/Vue 的语义一致。
2. **循环依赖抛 `CyclicDependency` 异常**（A 依赖 B、B 依赖 A）——不静默忽略，便于调试。
3. **订阅者回调抛异常被吞掉**（避免一个坏订阅者拖垮整个 reactive 图），但记录日志。这是安全默认。
4. **`signal.value = same_value`（is 或 == 相等）不触发通知**（no-op），减少无意义 rerender。
   - **`computed` 也应用相等短路**：源变化触发重算时，如果新值与缓存值 `==` 相等，不向下游订阅者发通知（与 SolidJS `equals` 一致）。
5. **`computed` 创建时不订阅上游**——纯惰性。首次 `.value` 读取才求值并建立订阅。没人读就一直不计算。
6. **多线程**：每个 signal 独立 `threading.RLock` 保护；`batch` 用 `ContextVar` 跟踪深度，最外层退出时统一 flush 通知。
7. **单次通知 flush 内 effect 至多重跑一次**（通知去重）：如果 effect 同时订阅了某个 Signal 和依赖于该 Signal 的 Computed，写入 Signal 时 effect 只重跑一次（而不是「直接订阅 + 经 Computed 间接订阅」各跑一次）。用单调递增的 `_notification_epoch` + `_notifying` ContextVar 实现，覆盖同步 flush 和 `batch` flush 两条路径。

**Consequences**:
- ✅ 后续 reconciler 可以放心依赖这些行为
- ✅ 调试友好（循环依赖显式报错）
- ✅ 性能优化自然（相等值 no-op、computed 惰性）
- ❌ 用户写 `effect(fn, deps=[some_value])` 时如果传值不传 signal，effect 不会因 some_value 变化重跑——README 要明确说明
- 实现：`src/pyink/core/signal.py:1-512`，35 个测试覆盖（`tests/core/test_signal.py`）

### Decision 10: Async 支持：MVP 全同步 + 线程并发

**Context**: PyInk 自身不需要 async（input loop、scheduler、effect 都是同步触发）。Jarvis 的流式 AI 响应可以在应用层用 `threading.Thread` 推数据到 signal。pyinkcli 也是这个模式（counter.py 用 `threading.Thread` 跑定时器）。

**Decision**: MVP 全同步。并发由应用层用 threading 处理（线程推送数据到 signal，触发 rerender）。asyncio 集成推迟到 Phase 2（如果届时确有需要）。

**Consequences**:
- ✅ PyInk 内部架构简单（无 event loop、无 await 污染）
- ✅ 用户写 `def Counter()` 而非 `async def Counter()`
- ✅ 调试简单（同步栈、可断点）
- ✅ signals 在多线程下用锁保护即可（GIL 帮忙）
- ❌ 跟 asyncio-heavy 的库（如 aiohttp、httpx.AsyncClient）集成需要应用层做适配（在线程里跑 asyncio.run）
- ❌ 极端高并发场景性能不如 asyncio（但 TUI 不是高并发场景）

```python
def JarvisApp():
    messages = signal([])

    def stream(text):
        def worker():
            for chunk in fake_stream(text):
                # 在 worker 线程里更新 signal，自动触发 rerender
                messages.value = [*messages.value, chunk]
        threading.Thread(target=worker, daemon=True).start()

    return Box(*[Text(m) for m in messages.value], flexDirection="column")
```

### Decision 9: 最低 Python 版本 3.11

**Context**: 本地装的是 3.11.9。3.11 提供 TypeVarTuple（让 `*children` 类型推断变好）、TaskGroup、match-case、Self 类型、tomllib。

**Decision**: `requires-python = ">=3.11"`。

**Consequences**:
- ✅ TypeVarTuple 让 `.pyi` stub 写得更精准
- ✅ Self 类型让 fluent API 类型友好
- ✅ match-case 让 input parser 等代码更清晰
- ❌ 挡住 3.10 用户（但 3.11 已发布 2.5 年，影响小）

### Decision 8: Children 用位置参数（A 风格），字符串 child 仅限 Text

**Context**: A（位置参数 `Box(Text("a"), padding=1)`）、B（关键字 `Box(children=[...])`）、C（builder `.add()`）。pyinkcli/Reflex/Pynecone/IDOM 等 React-like Python 库全部用 A 风格。A 跟 Claude Code TSX 翻译最直接。

**Decision**:
- 组件签名：`def Box(*children, **props):`，children 在前、props 在后
- 配 `.pyi` stub 文件做静态类型提示
- `Text` 接受 `str | callable | Element` 作为 child
- `Box` 等容器只接受 Element 作为 child（不接受裸字符串，强制包 `Text`）
- tuple 作为 fragment（返回多元素不包 wrapper）
- 支持 `*[...]` 解包 list comprehension

**Consequences**:
- ✅ 跟 Claude Code TSX 心智模型一致，借鉴翻译最直接
- ✅ Python 生态先例（pyinkcli/Reflex/IDOM）
- ✅ 配套 `*` 解包自然支持
- ❌ `*children` 静态类型推断差 → 用 `.pyi` stub 弥补
- ❌ Python 语法限制（位置参数不能跟在关键字后）→ 强制 children 在前，props 在后，跟 JSX 一致

```python
# 基础
Box(Text("a"), Text("b"), padding=1, flexDirection="column")

# 字符串 child 仅限 Text
Text("hello")
Text(f"count: {n}")
Text(lambda: str(count.value))   # callable 动态求值

# Fragment
def Header():
    return (
        Text("Title", bold=True),
        Text("Subtitle"),
    )  # tuple = fragment

# List comprehension
Box(*[Text(m.content) for m in messages], flexDirection="column")
```

### Decision 7: 函数组件 only（不支持类组件）

**Context**: signals 模型下，闭包变量就是实例状态，类组件的 `self.state` 反而不自然。Vue 3、SolidJS、Svelte 5、Preact 在 signals 模型下都用函数组件。

**Decision**: 只支持函数组件。`def Counter():` + 闭包变量 + signals + `return tree`。

**Consequences**:
- ✅ 无样板代码（不用 `class Xxx(Component): def render(self):`）
- ✅ 闭包变量天然就是 instance state
- ✅ 生命周期用 `effect` + cleanup 函数代替
- ✅ Reconciler 只需处理一种组件类型，简化实现
- ❌ 复杂生命周期场景（多阶段 did_mount/will_unmount/did_catch）需要用多个 effect 拼出

```python
def Counter():
    count = signal(0)

    def on_key(key):
        if key == 'up':
            count.value += 1

    use_input(on_key)

    # effect 替代类组件的 did_mount + will_unmount
    def setup():
        print("mounted")
        return lambda: print("unmounted")  # cleanup
    effect(setup)

    return Box(
        Text(lambda: str(count.value)),
        flexDirection="column",
    )
```

### Decision 6: Signals API 用 `.value` 属性风格（单一方式）

**Context**: 在 A（`.value` 属性，Vue 3 / Preact Signals）、B（`()` 调用 + `.set()`，SolidJS / React-useState）、C（两种都支持）三个选项中选择。C 看似灵活，实际是 API 表面翻倍 + 长期一致性债（代码库必然混用、实现要同步两套订阅逻辑、类型注解复杂、无主流 reactive 库采用）。

**Decision**: 采用 A 风格。读用 `count.value`，写用 `count.value = 1`。`computed` 同样 `.value`。

**Consequences**:
- ✅ 读写对称（`.value` 读 / `.value=` 写）
- ✅ Python 习惯（像 dataclass / property）
- ✅ IDE 补全友好（输入 `count.` 提示 `.value`）
- ✅ 类型注解干净（`@property`）
- ✅ 单一风格，代码库一致
- ❌ 比起 `count()` 啰嗦几字符，但可读性优势远超此成本
- 参考：Vue 3、Preact Signals、ReactivePy 都用 `.value`

```python
count = signal(0)
name = signal("")

def on_key(k):
    if k == 'up':
        count.value += 1

double = computed(lambda: count.value * 2)
print(double.value)  # 0

effect(lambda: print(f"count is {count.value}"))
```

### Decision 5: 三层组件划分 — components（内置）/ externals（扩展）/ 业务

**Context**: 内置组件应 1:1 对齐 ink 原版（`D:\Projects\github\ink\ink-master\src\components\`，共 6 个：Box/Text/Newline/Spacer/Static/Transform）。扩展组件（移植自 ink 生态 + PyInk 自创）需要单独目录，避免污染内置语义。

**Decision**:
- `pyink/components/` = **内置**，1:1 对齐 ink 原版的 6 个组件。零外部依赖。
- `pyink/externals/` = **扩展**，移植自 ink 生态（Spinner/TextInput/SelectInput/Link/Divider 等）+ PyInk 自创（VirtualList 等）。可选，部分组件有额外依赖（Markdown→pygments）通过 `pyproject.toml` 的 `optional-dependencies` + 模块内 lazy import 隔离。
- `jarvis/components/` = **业务**，Jarvis 产品级组件（Messages/PromptInput/StatusLine 等）。

**Consequences**:
- ✅ 内置/扩展边界清晰（components 是必须的，externals 是可选的）
- ✅ 内置永远零依赖，对齐 ink 原版便于交叉参考
- ✅ externals 重依赖组件用 optional-dependencies 隔离，`pip install pyink` 永远零额外依赖
- ✅ 业务/框架边界清晰（按仓库分）
- ❌ externals 名字略不准确（PyInk 自创的如 VirtualList 不是"外部移植"）—— 但比 plugins 好（plugins 暗示注册机制）

**`pyink/__init__.py` 导出策略**：
```python
# 默认导出 6 个内置（零依赖）
from .components.box import Box
from .components.text import Text
from .components.newline import Newline
from .components.spacer import Spacer
from .components.static import Static
from .components.transform import Transform

# 扩展组件用户显式 import
# from pyink.externals.spinner import Spinner
# from pyink.externals.text_input import TextInput
# from pyink.externals.markdown import Markdown  # 需要 pip install pyink[markdown]
```

## Requirements (evolving)

### MVP（Phase 1）

#### 组件（核心内置，零外部依赖）
- `Box`：flex 容器，支持 flexDirection、justifyContent、alignItems、padding/margin、gap、width/height、borderStyle
- `Text`：文本节点，支持 color、backgroundColor、bold、italic、underline、strikethrough、dimColor、inverse、wrap
- `Newline`：插入换行
- `Spacer`：弹性占位
- `Static`：永久输出区（用于已完成消息）
- `Transform`：文本变换

#### Hooks
- `use_input(handler, options?)`：键盘输入
- `use_app()`：app 生命周期（exit、waitUntilRenderFlush）
- `use_window_size()`：终端尺寸 + resize 事件

#### Signals API（替代 React hooks）
- `signal(initial)`：可观察值
- `computed(fn)`：派生值，自动追踪依赖
- `effect(fn, deps?)`：副作用，自动追踪或显式声明
- `ref(initial)`：跨渲染保持引用（不触发订阅）

#### Layout（纯 Python flex 子集）
- flexDirection: row / column / row-reverse / column-reverse
- justifyContent: flex-start/center/flex-end/space-between/space-around/space-evenly
- alignItems: flex-start/center/flex-end/stretch
- flexWrap: nowrap/wrap
- padding/margin（top/right/bottom/left + X/Y + shorthand）
- gap（columnGap/rowGap）
- width/height（绝对值，百分比推迟）
- flexGrow/flexShrink
- 文本宽度计算（CJK / emoji / 组合字符）

#### Render
- `render(tree, options?) -> Instance`：挂载根组件
- `Instance.rerender(tree)`：替换根
- `Instance.unmount()`：卸载
- `Instance.wait_until_exit()`：等待退出
- `Instance.clear()`：清屏
- `render_to_string(tree, options?) -> str`：同步渲染到字符串（用于测试/CI）
- 帧级 diff：重渲染时只写变化的行
- inline 模式：cursor move + line clear，禁用 `\x1b[2J`
- alternate screen：作为可选支持

#### 核心 engine
- Reconciler：树 diff/commit/effect 调度（适配 signals）
- Scheduler：rerender 批处理、maxFps 限速
- Component 实例：挂载、卸载、props diff
- 错误边界：组件抛错不炸整个 app

## Acceptance Criteria (evolving)

- [ ] 能渲染一棵静态树（Box/Text 嵌套），输出正确 ANSI 字符串
- [ ] `signal` + `computed` + `effect` 工作正常，依赖追踪准确
- [ ] `use_input` 能接收键盘事件并触发 rerender
- [ ] 状态变化时只重渲染订阅了变化 signal 的部分（验证 memoization）
- [ ] flex 布局：column/row + padding/margin/gap + 基础对齐，文本宽度计算正确（含 CJK）
- [ ] inline 模式下，按一个键改变状态 → 只有变化的行被重写（验证帧 diff）
- [ ] alternate screen 进入/退出，主屏 scrollback 不丢
- [ ] `render_to_string` 可在测试环境使用（无需真实 TTY）
- [ ] counter / select-input / borders / static 四个 demo 能跑
- [ ] pytest 测试覆盖核心模块

## Definition of Done

- 核心模块（core/、components/、hooks/、layout/、render/）有单元测试
- **flex 测试用例至少 80% 移植自 ink 的 `flex-*.tsx` 系列**（输入树 + 期望输出字符串）
- 至少 4-6 个 examples（counter、select-input、borders、static、use-input、use-focus）能正常运行
- 类型注解完整，mypy 通过
- ruff/black 格式统一
- README 说明安装、最小示例、与 ink TS 的 API 差异
- 在 Jarvis 项目里能 `pip install -e .` 后 `import pyink` 成功

## Testing Strategy

**测试金字塔**：

1. **单元测试**（pytest）：signal/computed/effect、flex 算法、文本宽度、ANSI 处理、key parser
2. **组件测试**（pytest + render_to_string）：把组件树渲染成字符串，对照期望输出
3. **集成测试**（pytest + ptyprocess）：真实 TTY 跑 examples，捕获 stdout 验证渲染

**ink 测试作为 oracle**：

ink 的 `flex-*.tsx` 系列测试是"输入 JSX 树 → 期望终端输出字符串"的精确对照。如果 PyInk 的 flex 算法和 ink（Yoga）实现一致，相同输入应产出相同输出。我们把这些测试的输入和期望输出**逐个移植**到 Python：

```python
# 例：flex-direction.tsx 的某个用例
def test_flex_direction_column():
    tree = Box(
        Text("X"),
        Text("Y"),
        flexDirection="column",
    )
    assert render_to_string(tree) == "X\nY"
```

如果某些用例因为算法差异（纯 Python flex 子集 vs Yoga 全集）失败，单独标记并文档化差异原因。

## Examples 清单

参考 ink 的 28 个 examples，MVP 阶段移植 6 个：

| Example | 验证什么 | ink 源 |
|---|---|---|
| `counter` | signals + rerender 基础 | `examples/counter/` |
| `select-input` | 键盘导航 + 状态切换 | `examples/select-input/` |
| `borders` | Box 边框样式渲染 | `examples/borders/` |
| `static` | Static 永久输出区 | `examples/static/` |
| `use-input` | use_input 各种按键 | `examples/use-input/` |
| `use-focus` | 多组件焦点切换（时间允许） | `examples/use-focus/` |

后期可补：table、terminal-resize、markdown（externals 实现后）、Suspense（如果做 async）。

## Out of Scope (explicit)

### MVP 不做
- React Devtools 集成
- Kitty keyboard protocol（功能键精确识别）
- Screen reader / ARIA 支持
- 鼠标命中测试 + 文本选择
- 搜索高亮
- 双向文字（阿拉伯语等）
- React Compiler 风格自动 memo（signals 已给等价能力）
- 并发渲染（React concurrent mode）
- VirtualList（Phase 5）
- 增量渲染优化（Phase 5）
- 百分比尺寸（width/height "50%"）
- aspectRatio、position absolute 的复杂场景
- **`text-width.tsx` 的 truncate 用例**（lines 94-141）—— 需要 PR4 的 `Text` 组件的 `wrap` prop，PR3 阶段所有 text leaf 默认 word-wrap。PR4 实现 `Text` 后再补这些 oracle 测试。
- **`alignContent` 多行 wrap**（已 xfail）—— MVP 阶段 flexWrap 实现了 nowrap/wrap/wrap-reverse 的单行场景，但多行 wrap 的 alignContent（start/center/end/space-between 等）需要 multi-line wrap path，留到后续

### MVP 已知行为边界（PR3-4 实现中发现）

- **`space-around` 在紧宽度下塌缩**：当 `free_space / (2n) < 1`（n 是子元素数），半 gap 向下取整为 0，space-around 退化为 flex-start 布局。ink 自身的 yoga 在极端情况下也有类似 rounding 问题，PyInk 这个 edge case 行为略有差异。MVP 接受此行为，文档化。
- **`width=0` 容器渲染时 text 会越界**：layout 报告的 width/height 正确（width=0, height=1），但 `render_layout` 的 sparse grid 允许 text 写到边界外。PR4/PR5 实现 box 边界裁剪后修复。
- **嵌套 `Text` 颜色继承/覆盖未实现**：`Text("outer", color="green", children=Text("inner", color="red"))` 不会让 inner 继承或覆盖 outer 的颜色——嵌套 text 元素会被递归展平，inner 的样式被丢弃，只剩字符串拼接。ink 有完整的 squash + transform 管道实现这个；Claude Code 实际很少用嵌套 styled Text（grep 数据显示），MVP 接受此简化。后续如需要，可在 PR7 的 `Transform` 组件实现时一并加 squash 管道。
- **ANSI reset 用 `\x1b[0m` 全 reset，而非 ink 的 `\x1b[39m`/`\x1b[49m` 分离 reset**：chalk level-3 行为，更通用（所有终端支持），与 ink 略有差异但功能等价。ink 自己的 `test/background.tsx:13-22` 也文档化了 chalk/ink 的这个分歧。

### 永远不做（除非 Jarvis 真要）
- 完整复刻 Claude Code 的 ink fork（layout engine、optimizer、termio 等专属特性）
- 完整复刻 Claude Code 的 113 个业务组件

## Implementation Plan (small PRs, TDD-style)

每个 PR 自带对应单元测试，**测试用例优先参考 ink 的 108 个测试文件**（路径：`D:\Projects\github\ink\ink-master\test\`）。ink 的 `flex-*.tsx` 测试是"输入树 → 期望输出字符串"的精确用例，可作为 PyInk 实现的 oracle（如果 flex 算法一致，输出字符应完全相同）。

- **PR1**: 项目骨架（pyproject.toml + 目录 + README 占位）+ `signal`/`computed`/`effect`/`ref` 实现 + 单元测试
  - 测试：signal 读写、computed 依赖追踪、effect 触发/cleanup、ref 不触发订阅
- **PR2**: Reconciler + Component 基础（挂载、卸载、props diff）+ `render_to_string` + 测试
  - 参考：`ink-master/test/components.tsx`、`reconciler` 相关测试
  - 测试：树挂载、props 变化触发 rerender、子组件 unmount 清理 effect
- **PR3**: 纯 Python flex 引擎（参考 yoga-layout-python）+ 文本宽度（CJK/emoji）+ flex 测试
  - **重点参考 ink 测试套件**：`flex-direction.tsx`、`flex-justify-content.tsx`、`flex-align-items.tsx`、`flex-align-self.tsx`、`flex-wrap.tsx`、`flex-align-content.tsx`、`flex.tsx`、`gap.tsx`（共 8 个 flex 测试文件，~240 个用例）
  - 测试策略：把 ink TS 测试的"输入树 + 期望输出字符串"移植成 Python 测试，作为算法 oracle
  - 文本宽度参考 `ink-master/test/string-width` 类测试
- **PR4**: `Box`/`Text`/`Newline`/`Spacer` 组件 + ANSI 输出（颜色/样式）+ components 测试
  - 参考：`ink-master/test/components.tsx`、`borders.tsx`、`border-backgrounds.tsx`、`background.tsx`、`ansi-tokenizer.ts`
- **PR5**: 渲染管线（inline 帧级 diff、Instance API：rerender/unmount/wait_until_exit/clear）+ output 测试
  - 参考：`ink-master/test/build-output.ts`
  - 测试：rerender 时帧 diff 正确（只写变化行）、Instance 生命周期
- **PR6**: `use_input` + `use_app` + `use_window_size` + raw mode（termios/msvcrt 抽象）+ hooks 测试
  - 参考：`ink-master/test/hooks-use-input.tsx`、`hooks-use-input-navigation.tsx`、`hooks-use-input-kitty.tsx`、`hooks-use-paste.tsx`、`input-parser.ts`、`parse-keypress.ts`、`cursor.tsx`、`exit.tsx`
- **PR7**: `Static`/`Transform` + alternate screen 支持 + 对应测试
  - 参考：`ink-master/test/alternate-screen-example.tsx`、`static-elements.tsx`（如有）
  - 测试：Static 永久输出、Transform 文字变换、alternate screen 进入/退出 scrollback 不丢
- **PR8**: examples（参考 ink 28 个 examples 选 6 个移植）+ 整合测试 + README
  - examples 清单（参考 ink `examples/`）：
    - `counter`（验证 signals + rerender 基础流程）
    - `select-input`（验证键盘导航 + 状态切换）
    - `borders`（验证 Box 边框样式）
    - `static`（验证 Static 永久输出）
    - `use-input`（验证 use_input 各种按键）
    - `use-focus`（验证焦点管理，如时间允许）
  - 整合测试：跑通每个 example，输出快照对比

## Module Structure

```
pyink/
├── __init__.py              # 默认导出 6 个内置组件 + 核心 hooks + signals
├── core/
│   ├── reconciler.py        # 树 diff/commit/effect 调度（适配 signals）
│   ├── component.py         # 组件实例、props diff
│   ├── scheduler.py         # rerender 批处理、maxFps
│   └── signal.py            # signal/computed/effect/ref
├── components/              # ⭐ 内置：1:1 对齐 ink 原版（6 个）
│   ├── box.py
│   ├── text.py
│   ├── newline.py
│   ├── spacer.py
│   ├── static.py
│   └── transform.py
├── externals/               # ⭐ 扩展：移植自 ink 生态 + PyInk 自创（按需 import）
│   ├── spinner.py           #   纯 Python
│   ├── text_input.py
│   ├── select_input.py
│   ├── link.py
│   ├── divider.py
│   ├── confirm_input.py
│   ├── markdown.py          # lazy import pygments
│   ├── highlighted_code.py  # lazy import tree-sitter
│   └── virtual_list.py
├── hooks/
│   ├── input.py
│   ├── app.py
│   └── window_size.py
├── layout/
│   ├── flex.py              # 纯 Python flex 子集
│   └── measure.py           # 文本宽度（CJK/emoji）
├── render/
│   ├── output.py            # ANSI 输出、颜色、样式
│   ├── diff.py              # 帧级 diff
│   └── terminal.py          # stdout 抽象、raw mode、alternate screen
└── tests/
```

业务层（不在 PyInk 仓库内）：

```
jarvis/
└── components/              # ⭐ 业务：Jarvis 产品级组件
    ├── messages.py
    ├── prompt_input.py
    ├── status_line.py
    └── tool_call_display.py
```

## Phase Roadmap (后续阶段，本任务只做 Phase 1)

| Phase | 内容 | 估计 |
|---|---|---|
| **Phase 1（本任务）** | MVP：核心引擎 + 基础组件 + 渲染管线 | 3-4 周 |
| Phase 2 | Link、Spinner、measure_element、use_focus | 2-3 周 |
| Phase 3 | Markdown 渲染、代码高亮、流式文本、Diff | 2-3 周 |
| Phase 4 | TextInput、SelectInput、ConfirmInput、历史命令 | 2-3 周 |
| Phase 5 | VirtualList、增量渲染、长会话性能 | 1-2 周 |

## Open Questions

### Blocking（影响 MVP 开工）

- ~~**Q1（signals API 风格）**~~ ✅ 已决：A 风格（`.value` 属性），见 Decision 6
- ~~**Q2（组件定义）**~~ ✅ 已决：函数组件 only，见 Decision 7
- ~~**Q3（children 传递）**~~ ✅ 已决：位置参数 A 风格，见 Decision 8
- ~~**Q4（Python 版本）**~~ ✅ 已决：3.11+，见 Decision 9
- ~~**Q5（async 支持）**~~ ✅ 已决：MVP 全同步 + 线程并发，见 Decision 10
- **Q3（children 传递）**：`Box(Text("a"), Text("b"), padding=1)`（位置参数）还是 `Box(children=[...], padding=1)`（关键字）还是 fluent builder？影响所有组件 API。
- **Q4（Python 版本）**：3.10 / 3.11 / 3.12？影响可用语法（match-case、Self 类型等）。
- **Q5（async 支持）**：MVP 完全同步？还是底层用 asyncio 跑 input loop？Jarvis 流式响应需要 async，但 MVP 可以先 sync 再加。

### Non-blocking（不影响开工，可以晚点定）

- 测试运行器和 TTY 模拟方案（倾向 pytest + ptyprocess）
- License（MIT？）
- 作者/维护者信息
- 包名最终确认（`pyink` 在 PyPI 上是否可用，可能要用 `pyink-tui`）
- 是否发布到 PyPI 还是仅本地 editable install

## Technical Notes

### 参考实现（读，不 fork）

- pyinkcli 的 `reconciler.py`、`hooks/_runtime.py`、`render_node_to_output.py`、`output.py`、ANSI 系列
- yoga-layout-python 的 flex 算法
- ink TS 原版的 `Box.tsx`/`Text.tsx`/`render-node-to-output.ts`/`measure-element.ts`
- **ink 测试套件**（`D:\Projects\github\ink\ink-master\test\`，108 个文件 ~20k 行）：
  - flex 系列测试作为 PyInk flex 实现的 oracle（输入树 + 期望输出字符串）
  - components/borders/background 测试作为 Box/Text 行为参考
  - hooks-use-input 系列作为 input 解析参考
  - build-output 测试作为渲染管线参考
- **ink examples**（`D:\Projects\github\ink\ink-master\examples\`，28 个目录）：
  - counter/select-input/borders/static/use-input/use-focus 作为 PyInk examples 的移植来源

### 关键技术约束

- **禁用** `\x1b[2J`（全屏清）——破坏 scrollback
- **禁用** `\x1b[?25l`（隐藏光标）除非 alternate screen——inline 模式要保留 shell 光标体验
- alternate screen 必须 `atexit.register` 退出钩子
- raw mode 用 `termios`（Unix）/ `msvcrt`（Windows）抽象层
- Windows 终端兼容性（Windows Terminal / cmd / PowerShell）：ANSI 支持参差，需要 colorama 或 Windows VT 模式启用

### 性能策略分层

- **L1 帧级 diff**：MVP 必需，否则任何 rerender 全屏重写
- **L2 组件级 memo**：signals 自动给
- **L3 虚拟滚动**：Phase 5
- **L4 增量渲染**：Phase 5+

## Research References

（暂无 persisted 研究文件；本任务的设计判断来自会话内对 ink/claude-code/pyinkcli 的直接代码分析）
