# CH10：Slash Command Tasks

## T1：先固定 API Contract

- 文件：`docs/api-contract.md`
- 产出：命令模型、Registry、解析/补全、UIController、十个命令和执行分流契约。

## T2：实现命令核心框架

- 文件：`mewcode/commands/registry.py`、`mewcode/commands/parser.py`
- 产出：`CommandType`、`Command`、`CommandContext`、`UIController`、
  `CommandRegistry`、`parse_command`、`complete`。
- 验收：覆盖同步/异步注册、别名查找、四类冲突、hidden 过滤和解析边界。

## T3：实现十个内置命令

- 文件：`mewcode/commands/handlers/`
- 产出：`help`、`compact`、`clear`、`plan`、`do`、`session`、`memory`、
  `permission`、`status`、`review` 及 `ALL_COMMANDS/register_all_commands`。
- 验收：`ALL_COMMANDS` 恰好 10 项；不提前注册 CH11+ 的 Skill/Task/Trace/Worktree。

## T4：完善权限规则管理能力

- 文件：`mewcode/permissions/rules.py`
- 产出：`clear_local_rules()` 只清理 local tier，不影响用户级和项目级规则。

## T5：实现补全组件

- 文件：`mewcode/commands/completion.py`
- 产出：`CompletionPopup.show/hide/is_visible/Selected`，基于 Textual `OptionList`。

## T6：接入 MewCodeApp

- 文件：`mewcode/app.py`
- 产出：Registry 启动装配、`_build_command_context`、`_dispatch_command`、
  UIController 五方法、普通 prompt 的 `send_user_message` 入口、Tab 补全与选中回填。
- 验收：本地命令不调用 Provider；`review` 通过同一普通消息入口进入 Agent Loop。

## T7：补齐测试

- 文件：`tests/test_commands.py`、`tests/test_tui.py`、CH8/CH9 相关回归测试。
- 产出：框架单测、十命令集合、help/plan/do/review 行为、TUI 拦截、未知命令、
  单/多命中补全、PROMPT 命令回灌，以及 compact/session/memory 兼容回归。

## T8：文档与全量验收

- 文件：`docs/ch10/{spec,tasks,checklist}.md`、`README.md`、`docs/README.md`
- 验收命令：
  - `python -m pytest -q --basetemp=.pytest-full`
  - `ruff check .`
  - `mypy mewcode`
  - `python -m compileall -q mewcode`

## 进度

- [x] T1
- [x] T2
- [x] T3
- [x] T4
- [x] T5
- [x] T6
- [x] T7
- [x] T8
