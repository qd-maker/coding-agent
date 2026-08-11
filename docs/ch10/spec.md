# CH10：Slash Command Spec

## 1. 背景

TUI 中的清屏、模式切换、上下文压缩、会话/记忆管理等操作不应发送给 LLM。否则既浪费
Token，又无法可靠修改本地 UI 状态。CH10 用统一的 Slash Command 命名空间替换散落在
`on_input_submitted` 中的 `if/elif`，并保留一类可把模板化提示词重新送入 Agent Loop 的命令。

## 2. 目标与范围

实现命令模型、集中式注册中心、解析器、补全器和 UI 抽象；TUI 必须在 Agent 调用前拦截
`/` 命令，支持 Tab 补全并在状态栏展示高频命令提示。

本章只交付十个内置命令：`help`、`compact`、`clear`、`plan`、`do`、`session`、
`memory`、`permission`、`status`、`review`。Skill、Hook、SubAgent、Task、Trace、
Worktree 属于后续章节，不作为 CH10 已完成功能。

## 3. 功能需求

- F1：`CommandRegistry` 提供 `register`、`register_sync`、`find`、`list_commands`；名称和
  别名大小写不敏感，名称/别名发生任意交叉冲突时抛 `ValueError`。
- F2：`CommandType` 包含 `LOCAL`、`LOCAL_UI`、`PROMPT`：分别表示本地回显、修改 UI
  状态，以及把预设提示词交给正常对话流。
- F3：`CommandContext` 是 handler 的唯一入参，固定包含 `args`、`agent`、
  `conversation`、`session`、`session_manager`、`memory_manager`、`ui`、`config`。
- F4：`UIController` 固定暴露 `add_system_message`、`send_user_message`、`set_plan_mode`、
  `get_token_count`、`refresh_status`，命令不得依赖 Textual 组件。
- F5：`parse_command(text)` 返回 `(name, args, is_command)`；支持前导空白、纯 `/`、
  多参数和大小写归一化，且不因异常输入抛错。
- F6：`complete(registry, prefix)` 对非隐藏命令的 canonical name 与 alias 做前缀匹配，
  返回排序、去重后的 `/<candidate>` 列表。
- F7：`register_all_commands` 一次性注册且只注册本章的十个内置命令。
- F8：`/help [name]` 从 Registry 元数据生成列表或详情；未知命令统一引导到 `/help`。
- F9：`/plan [prompt]`、`/do [prompt]` 先切模式，再可选地把参数送入 Agent Loop。
- F10：`/review [focus]` 发送固定审查模板，至少覆盖逻辑错误、安全、性能和代码风格；
  参数追加为“额外关注”。
- F11：`/session` 支持 `list/resume/new/delete`；`/memory` 支持 `list/clear/edit`；
  `/permission` 支持模式、规则查看、追加与本地规则重置。
- F12：TUI 的 Enter 分流顺序固定为：解析命令 → 本地分发 → 非命令才调用 Agent。
- F13：Tab 单命中直接回填，多命中展示 `CompletionPopup` 并可选择回填。
- F14：状态栏显示 `/help`、`/status` 与 Tab 补全提示，并继续显示当前权限模式。

## 4. 非功能需求

- N1：异步注册由 `asyncio.Lock` 串行化；同步注册适用于应用启动装配。
- N2：所有 handler 为 `async def handler(ctx) -> None`，结果通过 `UIController` 输出。
- N3：命令包不 import `mewcode.app`；需要的 App 副作用通过 `config` 回调注入。
- N4：单条命令异常由 `_dispatch_command` 捕获并显示，不能让 TUI 崩溃。
- N5：hidden 命令可查找、可执行，但不出现在普通列表和补全结果中。
- N6：不实现 fuzzy match、命令管道、Markdown 命令加载和热重载。

## 5. 设计与调用链

1. `MewCodeApp.__init__` 创建 `CommandRegistry`，调用 `register_all_commands`。
2. 用户回车后 `on_input_submitted` 先调用 `_dispatch_command`。
3. `_dispatch_command` 执行 `parse_command → registry.find → CommandContext → handler`。
4. LOCAL/LOCAL_UI handler 经 `add_system_message` 回显；PROMPT handler 经
   `send_user_message` 重新进入 `_stream_reply → Agent.run`。
5. 用户按 Tab 后执行 `complete`：单命中写回 Input，多命中交给 `CompletionPopup`。

## 6. 完成定义

以 [checklist.md](checklist.md) 为准；实现、真实 TUI 调用链、自动测试和静态检查必须同时通过。
