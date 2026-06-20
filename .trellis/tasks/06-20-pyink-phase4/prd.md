# PyInk Phase 4 — input components

## Goal

为 Jarvis AI 助手 TUI 提供完整输入能力：多行文本输入、选项选择、确认对话框。Phase 4 完成后，PyInk 能搭出 Claude Code 风格的输入交互（多行消息编辑 + 命令模式选择 + 危险操作确认）。

## Background

PyInk 已交付：
- MVP（534 测试）：signals + 6 内置组件 + flex + ANSI + render 管线
- Examples 补全（13 examples）
- Phase 2（693 测试）：Spinner/Link/Divider externals + Context + use_focus/use_box_metrics hooks
- Phase 3（885 测试）：StreamingText/HighlightedCode/Markdown/StructuredDiff externals + 内容渲染优化

Phase 4 补 3 个输入组件，全部在 `pyink/externals/`：
- **TextInput**：核心，多行文本编辑 + 光标 + 选择 + 粘贴
- **SelectInput**：选项列表（已有 examples/select-input 手写版做参考）
- **ConfirmInput**：Y/N 确认

## Decisions (ADR-lite)

### Decision 1: TextInput 用 signal 维护 buffer + cursor 双状态

**Context**: 文本编辑需要状态：当前文本 + 光标位置 + 选区起止。

**Decision**:
- `value: Signal[str]` — 文本内容
- `cursor: Signal[int]` — 光标字符偏移（不是字节偏移）
- `selection: Signal[tuple[int, int] | None]` — 选区 [start, end)
- 内部 hooks（use_input）写 value/cursor/selection，触发 rerender
- 用户传 `on_change: Callable[[str], None]` 和 `on_submit: Callable[[str], None]`

**Consequences**:
- ✅ value/cursor/selection 三个 signal 互不依赖，可独立更新
- ✅ rerender 自动（signals 模型）
- ❌ 三状态同步要小心（如选区在 value 变化后失效）

### Decision 2: 多行用 \n 分隔，行号用 line/col 而不是绝对偏移

**Context**: 多行文本编辑，光标在 line/col 还是 absolute offset？

**Decision**:
- 内部用 absolute offset（简单）
- 暴露 line/col 给用户（line = offset 前 \n 数，col = offset - last \n index）
- Enter 插入 \n（默认）或触发 on_submit（如果 multiline=False）
- ArrowUp/ArrowDown 跨行移动

### Decision 3: Vim 模式不做（Phase 4.5 或更后）

**Context**: Claude Code 有 VimTextInput（Insert/Normal/Visual 三模式）。

**Decision**: MVP 不实现 Vim 模式。理由：
- 复杂度极高（模式状态机 + 行内命令 + 寄存器 + . 重复）
- Claude Code 用户调查：~10% 用 Vim 模式
- 基础编辑 + 选择覆盖 90% 需求

未来如需要，可在 TextInput 加 `vim_mode: bool` 参数，单独立项。

### Decision 4: SelectInput 用 use_focus_manager 管理焦点

**Context**: 多个 SelectInput 共存（如 Claude Code 的 settings 菜单），需要焦点切换。

**Decision**: SelectInput 内部用 use_focus 注册自己，Tab 切换由外部 use_focus_manager 控制。SelectInput 不直接绑定 Tab。

### Decision 5: ConfirmInput 默认 y/n，可配置其他键

**Context**: Y/N 确认基础，但有时候要 confirm/cancel 全名。

**Decision**:
```python
ConfirmInput(
    on_confirm: Callable[[], None],
    on_cancel: Callable[[], None] | None = None,
    *,
    confirm_key: str = "y",
    cancel_key: str = "n",
    require_enter: bool = False,  # False = 单键确认；True = 必须 Enter
)
```

## Requirements

### 必交付（5 PRs）

#### PR1: TextInput core (single-line + cursor + basic editing)

`src/pyink/externals/text_input.py`：
```python
def TextInput(
    *,
    initial_value: str = "",
    placeholder: str | None = None,
    on_change: Callable[[str], None] | None = None,
    on_submit: Callable[[str], None] | None = None,
    multiline: bool = False,
    mask: str | None = None,  # "*" for password
    max_length: int | None = None,
    color: str | None = None,
    cursor_color: str | None = None,
    **box_props,
) -> Element:
    """单行/多行文本输入。

    功能（PR1）：
    - 单字符输入
    - Backspace/Delete 删除
    - Left/Right 光标移动
    - Home/End 行首/行尾
    - Ctrl+A/Ctrl+E 同 Home/End
    - Ctrl+K 删到行尾
    - Ctrl+U 删到行首
    - placeholder 显示（空时）
    - mask 密码模式
    - max_length 截断
    """
```

#### PR2: TextInput advanced (multi-line + selection + paste)

扩展 PR1：
- Enter 插入 \n（multiline=True）或 on_submit（multiline=False）
- ArrowUp/ArrowDown 跨行
- Shift+方向键 选区
- Ctrl+C/Ctrl+V 区分（默认 Ctrl+C 退出，TextInput 内 Ctrl+C 复制？妥协：Ctrl+Shift+C 复制，Ctrl+C 仍退出）
- Paste 序列处理（\x1b[200~...\x1b[201~）
- selection 渲染（inverse video）
- Ctrl+W 删单词
- Alt+Backspace 同 Ctrl+W

#### PR3: SelectInput external

`src/pyink/externals/select_input.py`：
```python
def SelectInput(
    items: list[str] | list[dict],  # str 或 {"label": ..., "value": ...}
    *,
    initial_index: int = 0,
    on_select: Callable[[Any], None] | None = None,
    multi_select: bool = False,  # 多选（Space 切换）
    indicator: str = "❯",        # 当前选中前缀
    color: str | None = None,
    **box_props,
) -> Element:
    """选项列表。
    - 上下键切换（ArrowUp/Down 或 j/k）
    - Enter 确认（on_select）
    - Space 多选切换（multi_select=True）
    - Esc 取消
    - 内部用 use_focus 注册（多 SelectInput 时 Tab 切换）
    """
```

#### PR4: ConfirmInput external

`src/pyink/externals/confirm_input.py`：
```python
def ConfirmInput(
    on_confirm: Callable[[], None],
    on_cancel: Callable[[], None] | None = None,
    *,
    prompt: str = "Confirm?",
    confirm_key: str = "y",
    cancel_key: str = "n",
    require_enter: bool = False,
    default: str | None = None,    # "confirm" / "cancel" / None
    color: str | None = None,
) -> Element:
    """Y/N 确认。
    - 单键确认（默认）：按 confirm_key 立即 on_confirm
    - require_enter=True：先选 y/n 再 Enter 确认
    - Esc 取消
    """
```

#### PR5: Examples + README + integration tests

5 个新 examples：
- `examples/text-input/text_input_demo.py` —— 单行 + 多行 + 密码 + placeholder
- `examples/text-input-selection/selection_demo.py` —— 选区操作
- `examples/select-input-real/select_input_demo.py` —— 真正 SelectInput（对比 examples/select-input 手写版）
- `examples/select-input-multi/multi_select_demo.py` —— 多选
- `examples/confirm-input/confirm_demo.py` —— Y/N 确认

更新：
- `tests/externals/test_text_input.py`、`test_select_input.py`、`test_confirm_input.py`
- `tests/test_examples.py`：+5 测试
- `README.md`：externals 表 + examples 索引 + 安装说明

## Acceptance Criteria

- [ ] 5 PRs 全部交付
- [ ] 3 个新 externals（TextInput/SelectInput/ConfirmInput）+ 测试
- [ ] 5 个新 examples 跑通
- [ ] mypy strict + ruff 全绿
- [ ] 全部测试通过（应该 885 + ~100 新 = 985+）
- [ ] 不破坏 Phase 1-3 现有测试
- [ ] README 完整更新

## Definition of Done

- 5 PRs（每个含 implement + check + commit）
- 集成测试覆盖
- 5 个 examples 真实终端验证
- 全部 quality gates 绿
- 公共导出（externals 不默认导出）

## Out of Scope

- Vim 模式（Phase 4.5）
- 复杂输入历史（上下键翻历史命令）—— 推迟
- 自动补全（autocomplete dropdown）—— 推迟
- 国际化输入法（IME）—— 推迟
- React-Compiler-style memoization（signals 已给等价）

## Technical Notes

### 参考实现

- ink 第三方包：`D:\Projects\github\ink\components\` 下：
  - `ink-text-input/` —— TextInput（600+ 行，完整功能）
  - `ink-select-input/` —— SelectInput
  - `ink-confirm-input/` —— ConfirmInput
- Claude Code：`D:\Projects\github\claude-code\src\components\` 下：
  - `BaseTextInput.tsx` —— 基础版
  - `VimTextInput.tsx` —— Vim 模式（参考但 Phase 4 不实现）
- ink 核心 hooks：`use-input` 已在 PyInk PR6 实现

### 关键约束

- Python 3.11+，mypy strict + ruff
- 全同步 + 线程并发
- 函数组件 only
- Children 位置参数
- 不写 `\x1b[2J`
- style props 支持 callable
- externals 用 lazy import（如有重依赖；Phase 4 全部无重依赖）

## Implementation Plan

5 PRs（每 PR implement → check → commit）：

1. **PR1**: TextInput core（单行 + 光标 + 基础编辑）
2. **PR2**: TextInput advanced（多行 + 选区 + 粘贴）
3. **PR3**: SelectInput
4. **PR4**: ConfirmInput
5. **PR5**: Examples + README + 集成测试

预计 2-3 周。

## Research References

（暂无；可在 PR1 前派 trellis-research 调研 ink-text-input 的状态管理 + paste handling）

## Bug Fixes (post-PR5 hardening pass)

### Bug 9 — 中文/emoji 输入在 TextInput 中显示乱码

**Symptom**: 用户在 `TextInput` 中输入中文（如 "你好"）显示乱码，每个汉字
变成 3 个无关字符。

**Root cause**: `src/pyink/render/terminal.py` 的 input 读取管线按 chunk 调用
`KeyParser.feed(bytes)`，而 `KeyParser.feed` 内部用
`bytes(data).decode("utf-8", errors="replace")` —— 这是 **per-chunk 一次性
解码**。当内核把单个汉字的多字节 UTF-8 序列（"你" = `\\xe4\\xbd\\xa0`）
拆分到两次 `_read_stdin_chunk` 调用时（合法情况，内核不保证按 codepoint
原子交付）：

* 第一次 feed 收到 `b"\\xe4"`，独立解码 → 0xe4 是 3-byte UTF-8 序列的 lead
  byte，单独无效，被 `errors="replace"` 转成 `\\ufffd`（U+FFFD 替换符）。
* 第二次 feed 收到 `b"\\xbd\\xa0"`，两个 continuation byte 单独无效 → 又
  得到 `\\ufffd\\ufffd`。
* `KeyParser` 把这 3 个 `\\ufffd` 当作 3 个独立字符 emit → TextInput 收到
  3 个垃圾 Key 而不是 1 个含 "你" 的 Key。

即使是单 chunk 内的多字节序列，"per-feed 解码" 也只是恰好掩盖了问题；
根本性修复需要让 decoder 跨 chunk 累积。

**Fix (方案 A — Incremental UTF-8 decoder)**:
`src/pyink/render/terminal.py` —— 在 `Terminal` 上加了一个
`codecs.getincrementaldecoder("utf-8")(errors="replace")` 实例
（`_utf8_decoder` slot）。新增 `_decode_utf8(raw) -> str` 方法把原始
bytes 喂给增量 decoder，decoder 内部缓存不完整的尾部序列；下一次调用
自动把缓存的 lead byte + 新到的 continuation bytes 拼成完整 codepoint。
`_key_loop` 和 `read_key` 都改成 `_decode_utf8(raw)` → `_key_parser.feed(text)`
两步：decoder 返回空字符串（说明 bytes 还不够组成一个 codepoint）就
`continue` 等下一 chunk，绝不把不完整序列喂给 KeyParser。

`enter_raw_mode` 末尾 reset decoder，避免上一个 raw session 残留的 lead
byte 泄漏到新 session。

为什么不选方案 B（KeyParser 自己处理 UTF-8）：KeyParser 现在的
`feed(bytes|str)` 接口已经接受 str；只需要 Terminal 在 feed 之前做增量
解码即可，KeyParser 不动。方案 A 干净 + 测试覆盖好。

**Files**:

* `src/pyink/render/terminal.py`: 加 `_utf8_decoder` slot + `_decode_utf8`
  方法 + 在 `_key_loop` / `read_key` 中包增量解码层 + `enter_raw_mode`
  末尾 reset decoder。
* `tests/render/test_utf8_input.py`: 新增 13 个回归测试：
  * 单 chunk 单 codepoint（ASCII / 2-byte é / 3-byte 你 / 4-byte 🎉）
  * Split chunk：lead byte 单独一个 chunk、continuation bytes 在下一个
    chunk，验证 decoder 仍只 emit 一个 Key
  * 混合 chunk（ASCII + 中文 + emoji 同一 stream）
  * 不完整序列后接 ASCII（验证 decoder buffer 不丢）
  * `enter_raw_mode` reset decoder（上一个 session 残留 lead byte 不泄漏）
  * 端到端：`use_input` handler 收到 1 个完整 Key（不是 N 个垃圾 Key）
  * 集成：`TextInput` 累积 "你好" / 单 split "你" 都正确显示

**Verification**:

* 1093 tests pass（1080 baseline + 13 新增）。
* mypy strict + ruff 全绿（pre-existing `measure.py` wcwidth stub errors
  与本 bug 无关）。
* 用户验证步骤：`python examples/text-input/text_input_demo.py`，在
  Single-line input 里输入 "你好" —— 应在 input 框中正确显示 "你好"
  而不是 6 个乱码字符。再输入 emoji 🎉 也应正确显示。

