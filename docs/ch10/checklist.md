# CH10：Slash Command Checklist

> 验收以当前工作树中的符号、真实调用链和自动测试为准，不依赖历史分支行号。

## 1. API 与框架

- [x] `docs/api-contract.md` 已定义 Slash Command / UIController Contract。
- [x] `CommandType` 包含 `LOCAL / LOCAL_UI / PROMPT`。
- [x] `CommandContext` 恰好包含八个约定字段。
- [x] `UIController` 暴露五个约定方法，handler 不依赖 Textual。
- [x] `Command` 包含名称、别名、描述、用法、类型、参数提示、handler、hidden 元数据。
- [x] `CommandRegistry` 支持同步/异步注册、别名查找、排序列举和 hidden 过滤。
- [x] name/name、name/alias、alias/name、alias/alias 冲突均抛含 `conflicts with` 的错误。
- [x] `parse_command` 覆盖普通输入、前导空白、大小写、纯 `/`、空输入和多参数。
- [x] `complete` 覆盖 canonical/alias、排序去重、hidden 排除和无命中。

## 2. 十个命令

- [x] `register_all_commands` 恰好注册 help、compact、clear、plan、do、session、memory、
  permission、status、review 十个命令。
- [x] `/help` 列表和详情均从 Registry 元数据生成。
- [x] `/compact` 显示压缩前后 Token 并持久化压缩后的消息。
- [x] `/clear` 重置会话、对话、Agent loop、记忆游标和可见消息。
- [x] `/plan [prompt]`、`/do [prompt]` 先切模式再可选发送参数。
- [x] `/session` 支持 list/resume/new/delete，并通过注入回调更新 App 状态。
- [x] `/memory` 支持 list/clear/edit。
- [x] `/permission` 支持 mode/rules/add/reset，`/mode` 别名兼容原循环行为。
- [x] `/status` 显示模式、会话、Token、工具、记忆、工作目录和版本。
- [x] `/review` 模板含逻辑错误、安全、性能、代码风格和可选“额外关注”。
- [x] CH10 未实现或注册 Skill、Task、Trace、Worktree 命令。

## 3. TUI 集成

- [x] `MewCodeApp` 启动时构造 Registry 并注册十个命令。
- [x] Enter 入口先 `_dispatch_command`，非命令才进入 Agent Loop。
- [x] 未知命令回显并引导 `/help`，handler 异常不会拉崩 TUI。
- [x] MewCodeApp 实现 UIController 五方法。
- [x] `CommandContext.config` 注入 Registry、会话/对话 setter、清空/恢复、持久化、
  权限、busy 状态、工作目录和版本。
- [x] Tab 单命中直接回填；多命中显示 `CompletionPopup`；选择后回填 Input。
- [x] 状态栏显示 `/help · /status · Tab complete` 和当前权限模式。
- [x] `/status`、未知命令不调用 Provider；`/review` 会进入 Agent Loop。

## 4. 自动验证

- [x] `tests/test_commands.py` 覆盖 parser、registry、complete、十命令集合和核心 handler。
- [x] `tests/test_tui.py` 覆盖命令优先拦截、未知命令、Tab 单/多补全和 review 回灌。
- [x] `python -m pytest -q --basetemp=.pytest-ch10-final` 全量通过：`342 passed, 1 skipped`。
- [x] `ruff check mewcode tests/test_commands.py tests/test_context.py tests/test_memory.py tests/test_tui.py` 通过。
- [x] `mypy mewcode` 通过。
- [x] `python -m compileall -q mewcode` 通过。

## 5. 文档

- [x] `docs/ch10/spec.md` 与明确需求一致。
- [x] `docs/ch10/tasks.md` 记录实际任务和产出。
- [x] `README.md` 与 `docs/README.md` 更新到 CH10。
