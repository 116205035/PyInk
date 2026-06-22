# Docs + PyPI publish 0.1.0

## Goal

创建完整文档（架构详解 + 设计决策 + API 参考 + 迁移指南），整理根目录，准备 PyPI 首次发布。

## Background

PyInk 经过 12 个 task、~100 个 commit 的开发，功能完整（signals + 6 内置 + 10 externals + 8 hooks + flex + render pipeline）。现在需要：
1. 正式的架构文档供用户/贡献者参考
2. PyPI 发布让其他人能 `pip install pyink`

## Requirements

### PR1: 文档创建

#### `docs/architecture.md`
架构详解，覆盖：
- **Reactive 模型**：signals vs React hooks 对比、mount-once 语义、effect/signal/computed 三原语
- **Reconciler**：Element → Instance 树、mount/unmount 生命周期、Context 栈语义（push/pop 在 subtree mount 边界）
- **Layout 引擎**：纯 Python flex 子集、min-content floor、clip-to-content-box、maxHeight CSS 语义、text measurement（CJK/emoji/ANSI）
- **Render 管线**：inline frame diff（无 `\x1b[2J`）、alternate screen、FPS throttle、TTY clamp、bracketed paste、UTF-8 incremental decoder + Windows codepage
- **Hooks 系统**：use_input/use_app/use_window_size/use_interval/use_focus/use_focus_manager/use_context/use_box_metrics
- **Externals 模式**：lazy import + optional-dependencies、factory + Impl 模式
- **线程模型**：render thread + input thread + interval thread，signal 线程安全（RLock）

#### `docs/design-decisions.md`
所有 ADR-lite 决策汇总（从 12 个 task 的 PRD 提取）：
- signals vs hooks
- 纯 Python flex vs Yoga
- inline 默认 vs alternate screen
- 函数组件 only
- Children 位置参数
- Python 3.11+
- 全同步 + 线程
- 两阶段 flush
- Context 栈语义
- measure_element ref 模式
- Spinner use_interval
- markdown-it-py + pygments
- difflib stdlib
- VirtualList 删除（对标错误）
- render() TTY clamp
- maxHeight CSS 语义
- 等等

#### `docs/api-reference.md`
完整 API 表：
- 内置组件：Box/Text/Newline/Spacer/Static/Transform（全 props）
- Externals：Spinner/Link/Divider/StreamingText/HighlightedCode/Markdown/StructuredDiff/TextInput/SelectInput/ConfirmInput
- Hooks：use_input/use_app/use_window_size/use_interval/use_focus/use_focus_manager/use_context/use_box_metrics
- Signals：signal/computed/effect/ref/batch
- Render：render/render_to_string/Instance
- Context：create_context/Provider/use_context
- measure_element

#### `docs/migration-from-textual.md`
Textual → PyInk 迁移指南（给 Jarvis 用）：
- 概念映射：Widget → Function Component、Reactive → Signal、App → render()
- 布局映射：CSS-like → Flex props
- 事件映射：on_key → use_input
- inline vs fullscreen 模式选择
- Static + scrollback 模式（Claude Code 风格）

#### 根目录整理
- `LAYOUT_ANALYSIS.md` → `docs/layout-audit.md`
- 根目录只留 README.md + LICENSE + AGENTS.md + pyproject.toml

### PR2: pyproject.toml 完善 + PyPI 发布

#### pyproject.toml 补充
- `readme = "README.md"`（确认 long_description 从 README 取）
- classifiers 补全：Python 3.11/3.12、Operating System、Topic 等
- `urls.Homepage` / `urls.Repository` 指向 GitHub

#### 构建验证
```bash
python -m build
twine check dist/*
```

#### PyPI 发布
```bash
twine upload dist/*
```

## Acceptance Criteria

- [ ] 4 个 docs 文件创建（architecture/design-decisions/api-reference/migration）
- [ ] LAYOUT_ANALYSIS.md 移到 docs/
- [ ] pyproject.toml 完善（classifiers/readme/urls）
- [ ] `python -m build` 成功
- [ ] `twine check` 通过
- [ ] PyPI 上 `pip install pyink` 可用（发布后验证）
- [ ] 全部测试通过（1188+22）

## Out of Scope

- readthedocs / sphinx 集成（Markdown 文档够了）
- CI/CD pipeline（手动发布即可）
- 版本号管理自动化

## Implementation Plan

2 PRs：
1. **PR1**: 4 个 docs 文件 + 根目录整理
2. **PR2**: pyproject.toml 完善 + build + publish

预计半天。

## Research References

（无；内容来自 12 个 task 的 PRD + 代码）
