# Fix audit critical bugs

## Goal

修 6-agent 并行审计发现的 7 个 Critical bug。范围限定在**正确性问题**，不包括性能优化（双趟 layout）和 polish。

## Background

PyInk 完成所有 phase 后做了一次全代码审计（6 个 Explore agent 并行），汇总出 7 个 Critical + 10 个 Medium + 多个 Low。本 task 只修 Critical。

## Decisions

### Decision 1: 用 "disposed flag" 防止 hooks double-dispose

**Context**: use_input/use_interval 的 dispose 同时被手动调用 + effect cleanup 触发，导致重复执行或对已死订阅调用。

**Decision**: dispose 函数加 `_disposed` flag（闭包变量），首次调用后设 True，后续调用直接 return。

### Decision 2: is_active Signal 改为真订阅

**Context**: TextInput 广告 `is_active: Signal[bool]` 支持，但 `_resolve_is_active` 只读 `.value` 不建立订阅，signal 变化不触发 handler 重评估。

**Decision**: 改用 `effect` 包装：内部 effect 订阅 is_active signal，每次变化时重新注册/注销 use_input handler。或者更简单——直接在 handle_key 里读 signal（每次按键重新求值，已经是当前实现，但 docstring 要明确说"signal 变化不触发 handler 重新注册，只在下次按键时生效"）。

### Decision 3: signal.py 用更细粒度锁防竞态

**Context**: epoch 读取无锁，deferred effects list swap 在锁外。

**Decision**: 把 epoch 读取包到 `_epoch_lock` 内，list swap 包到现有 `_deferred_lock` 内。

### Decision 4: flex measured_width 同步更新

**Context**: flex_shrink 强制 width 后，measured_width 没同步，导致后续读到不一致值。

**Decision**: 在 force-clamp layout_width 后，也更新 measured_width = layout_width。

### Decision 5: rerender lock 加注释 + 防释放中断

**Context**: rerender 持 RLock 调 _paint_now（也持同锁），RLock 复入暂时没事，但中途释放锁破坏原子性。

**Decision**: 把 line 154 的锁释放挪到 _paint_now 之后，保证整个 rerender + paint 原子。或者整个 rerender 不持锁（让 _paint_now 自己管）。

## Requirements

### PR1: Hooks double-dispose fix (#3 #4)

修改：
- `src/pyink/hooks/input.py`：dispose 加 `_disposed` flag
- `src/pyink/hooks/interval.py`：同上
- `src/pyink/hooks/focus.py:198-204`：effect cleanup 用唯一标识，防止 remount 覆盖

测试：
- `test_use_input_double_dispose_idempotent`（已有，确认仍过）
- `test_use_interval_double_dispose_idempotent`（新增）
- `test_use_focus_remount_does_not_leak_handle`（新增）

### PR2: Signal/Computed race conditions (#1 #2)

修改 `src/pyink/core/signal.py`：
- Line 559 `_notification_epoch` 读取加锁
- Line 184-190 `_deferred_effects` swap 包到 `_deferred_lock` 内
- Line 297 setter 的 `is` → `==` 检查合并到同一锁内（或用 try-except 包 `==`）

测试：
- 并发 signal 写 + computed 求值（stress test）
- 并发 batch flush + effect 触发

### PR3: TextInput is_active 真订阅 (#5)

修改 `src/pyink/externals/text_input.py`：
- 选项 A：`_resolve_is_active` 改为在 effect 里读 signal（建立订阅），signal 变化触发 effect rerun，重新注册 use_input handler
- 选项 B：保留当前实现，改 docstring 明确"signal 变化只在下次按键时生效，不触发 handler 重新注册"

推荐 A（真订阅，符合广告）。但复杂——需要动态订阅/注销 use_input。

简化方案：把 is_active 求值放在 handle_key 内部（已经是这样），加 docstring 说明"每次按键重新评估 is_active，但不会自动重新订阅"。

### PR4: flex measured_width + rerender lock (#6 #7)

修改：
- `src/pyink/layout/flex.py:1145-1151`：force-clamp layout_width 后同步 measured_width
- `src/pyink/render/instance.py:128-157`：rerender 释放锁的时机调整，或重构让 _paint_now 自管锁

测试：
- `test_flex_shrink_keeps_measured_width_consistent`（新增）
- `test_rerender_atomic_under_concurrent_signal_write`（新增）

## Acceptance Criteria

- [ ] 4 PRs 全部交付
- [ ] 7 个 Critical bug 全修
- [ ] 全部现有测试通过（1172+22）
- [ ] 新增回归测试覆盖每个修复
- [ ] mypy strict + ruff 全绿

## Out of Scope

- Medium 性能优化（双趟 layout、Grid 内存 churn、diff dirty region）
- Externals reactive prop 一致性
- polish（docstring、naming）

## Implementation Plan

4 PRs（每 PR implement → check → commit）：

1. **PR1**: Hooks double-dispose（最易触发，先修）
2. **PR2**: Signal race conditions（核心 engine）
3. **PR3**: TextInput is_active（用户 API）
4. **PR4**: flex + rerender lock（layout 正确性）

预计 2 天。

## Research References

- 审计 agent 输出（在 conversation 上下文）
