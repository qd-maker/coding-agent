# ch04: Agent Loop Checklist

> 所有条目必须可勾选、可观测。验收方式写在每项后面的括号里。

## 1. 实现完整性

- [x] 类 `Agent` 在 `mewcode/agent.py:284`，字段含 `client` / `registry` / `protocol` / `work_dir` / `max_iterations` / `permission_checker` / `permission_mode` / `context_window` / `session_dir` / `compact_breaker` / `instructions_content` / `memory_manager` / `hook_engine` / `coordinator_mode` / `team_name` / `_plan_path_cache`（`grep -n "class Agent:" mewcode/agent.py`）
- [x] 12 个 AgentEvent 类型 + `PermissionResponse(Enum)` 在 `mewcode/agent.py:55-153`：`StreamText` / `ThinkingText` / `RetryEvent` / `ToolUseEvent` / `ToolResultEvent` / `TurnComplete` / `LoopComplete` / `UsageEvent` / `ErrorEvent` / `CompactNotification` / `HookEvent` / `PermissionRequest`（`grep -nE "^@dataclass|^class [A-Z]" mewcode/agent.py` 至少返回 12 条）
- [x] 方法 `Agent.run` 在 `mewcode/agent.py:397` 实现，签名 `async def run(self, conversation) -> AsyncIterator[AgentEvent]`（`grep -n "async def run" mewcode/agent.py`）
- [x] 常量 `MAX_TOKENS_CEILING=64000` 与 `MAX_OUTPUT_TOKENS_RECOVERIES=3` 在 `mewcode/agent.py:49-50`，`MEMORY_EXTRACTION_INTERVAL=5` 在 agent.py:48（`grep -nE "MAX_TOKENS_CEILING|MAX_OUTPUT_TOKENS_RECOVERIES|MEMORY_EXTRACTION_INTERVAL" mewcode/agent.py`）
- [x] `StreamCollector.consume` 在 `mewcode/agent.py:178`，处理 `TextDelta` / `ThinkingDelta` / `ThinkingComplete` / `ToolCallComplete` / `StreamEnd` 五类事件（`grep -n "isinstance(event," mewcode/agent.py | head`）
- [x] `partition_tool_calls` 在 `mewcode/agent.py:218`，`ToolBatch` 在 agent.py:213，安全调用合并到同一并发批的逻辑实现完整
- [x] `StreamingExecutor.submit / collect_results` 在 `mewcode/agent.py:247-280`，使用 `asyncio.create_task` + `asyncio.gather(..., return_exceptions=True)`
- [x] `_execute_tool` 在 `mewcode/agent.py:788`，处理 unknown tool / disabled / permission deny / permission ask（`PermissionRequest` 带 `asyncio.Future`）/ `ALLOW_ALWAYS` 写规则 5 个分支
- [x] `_execute_batch_parallel` 在 `mewcode/agent.py:782`，`_execute_single_tool_direct` 在 agent.py:742
- [x] `_maybe_persist_or_truncate` 在 `mewcode/agent.py:1105`，按 `SINGLE_RESULT_CHAR_LIMIT` / `MAX_OUTPUT_CHARS` 分支
- [x] `Agent._get_plan_path` 在 `mewcode/agent.py:334`，使用 `_ADJECTIVES`(24) + `_NOUNS`(24) + `MMDD-HHMM` 拼 slug，`_plan_path_cache` 单例
- [x] Plan Mode 的 Provider Schema 只含只读工具与 `WritePlan`；`/do` 后恢复普通工具且隐藏 `WritePlan`（`test_plan_mode_only_exposes_read_tools_and_write_plan` / `test_do_mode_restores_full_tool_schemas_and_hides_write_plan`）
- [x] `WritePlan` 不接收路径参数，只写当前 `_get_plan_path()`，并通过临时文件替换完成原子落盘（`test_plan_mode_write_plan_saves_only_current_plan` / `test_write_plan_rejects_any_caller_supplied_path`）
- [x] Plan Mode 权限优先于 `ALLOW_ALWAYS` 持久化规则，历史规则不能放行写工具（`test_plan_mode_cannot_be_bypassed_by_allow_always_rule`）
- [x] `build_plan_mode_reminder` 在 `mewcode/prompts.py:203`，`_REMINDER_INTERVAL=5`，`iteration==1` 给完整 reminder（`grep -n "_REMINDER_INTERVAL" mewcode/prompts.py`）
- [x] 任务模型与四工具：`TaskCreateTool` / `TaskGetTool` / `TaskListTool` / `TaskUpdateTool` 在 `mewcode/tools/task_create.py`、`task_get.py`、`task_list.py`、`task_update.py`，皆继承 `Tool` 且 `is_concurrency_safe = True`
- [x] 工具结果回灌：`_infer_file_path` 在 `mewcode/agent.py:381` 按 `file_path → path` 顺序查找

## 2. 接入完整性（杜绝死代码）

- [x] `grep -n "Agent(" mewcode/app.py` 显示 `mewcode/app.py:649` 构造 Agent 时传入 `client` / `registry` / `protocol` / `work_dir` / `permission_checker` / `context_window` / `instructions_content` / `memory_manager` / `hook_engine`
- [x] `grep -n "self.agent.run" mewcode/app.py` 至少 1 处（`mewcode/app.py:1085` 的 `async for event in self.agent.run(self.conversation)`）
- [x] `grep -rn "build_plan_mode_reminder" mewcode/` 至少 2 处调用方：`mewcode/agent.py:475` 与 `tests/test_agent.py`
- [x] `grep -rn "set_permission_mode\|set_plan_mode" mewcode/` 调用链：`mewcode/commands/handlers/plan.py` → `MewcodeApp.set_plan_mode`（`mewcode/app.py:850`）→ `agent.set_permission_mode(PermissionMode.PLAN)`（`mewcode/agent.py:352`）
- [x] `grep -rn "TaskCreateTool\|TaskGetTool\|TaskListTool\|TaskUpdateTool" mewcode/` 四个工具在团队注册路径上被引用（团队场景由 `TeamManager` 注册到 Registry）
- [x] `grep -n "permission_checker" mewcode/app.py` 在 TUI 构造 Agent 时使用（`mewcode/app.py:654`）
- [x] `Agent.coordinator_mode` 在 TUI 协调器路径上设值，`build_system_prompt` 据此切到 coordinator 系统提示
- [x] `Agent.hook_engine` 在 `mewcode/app.py:658` 注入 `HookEngine`，主循环 8 个 hook 事件点（session_start / turn_start / pre_send / post_receive / pre_tool_use / post_tool_use / turn_end / session_end）皆有触发
- [x] `_handle_permission_request` 在 `mewcode/app.py` 监听 `PermissionRequest` 事件，把用户选择 `future.set_result(PermissionResponse.X)` 回填
- [x] `AskUserQuestion` 与权限确认均挂载到 `#conversation`；交互期间 `len(app.screen_stack) == 1`，`↓` + `Enter` 可选中第二项（`test_tui_collects_ask_user_answer` / `test_tui_resolves_permission_request`）
- [x] `RetryEvent` 在 `mewcode/app.py:1119` 渲染为 `↻ Retrying: ...` 系统消息

## 3. 编译与测试

- [x] `python -m compileall mewcode` 通过，无语法 / 导入错误
- [x] `ruff check mewcode tests` 无 error
- [x] `pytest tests/test_agent.py -q` 16 个核心测试用例全部通过（当前该文件共 45 个测试）：
  - `test_single_step_tool_call`、`test_multi_step_autonomous`、`test_stop_end_turn`
  - `test_stop_max_iterations`、`test_stop_cancel`、`test_stop_consecutive_unknown_tools`
  - `test_message_splicing`、`test_concurrent_batch_execution`、`test_token_usage_accumulates`
  - `test_plan_mode`、`test_plan_mode_denied_tool_returns_error`
  - `test_partition_tool_calls`
  - `test_system_prompt_normal`、`test_system_prompt_plan`、`test_plan_mode_sparse_reminder`、`test_environment_context`

## 4. 端到端验证

- [x] Textual 入口：用户在输入框敲普通消息后看到 `StreamText` 渲染、最终 `LoopComplete` 终止 —— 调用链 `MewcodeApp.send_user_message → asyncio.create_task(_send_message) → async for event in self.agent.run(self.conversation) → isinstance 分支`（`mewcode/app.py:840 → :1085 → :1099-1230`）
- [x] Plan Mode：输入 `/plan` 走 `handle_plan` → `set_plan_mode(True)` → `agent.set_permission_mode(PermissionMode.PLAN)`，下一轮只暴露只读工具与 `WritePlan` 并注入 reminder；输入 `/do` 走 `handle_do` → 恢复 `PermissionMode.DEFAULT` 与普通工具 Schema（`mewcode/commands/handlers/plan.py` / `do.py`）
- [x] HITL 权限：`PermissionRequest` 事件触发时 Textual 在对话流内渲染 `InlinePermissionPrompt`（`mewcode/permission_dialog.py`），用户用 `↑` / `↓` 与 `Enter` 选「允许一次 / 始终允许 / 拒绝」，对应 `PermissionResponse.ALLOW` / `ALLOW_ALWAYS` / `DENY`；选 `ALLOW_ALWAYS` 时调 `rule_engine.append_local_rule` 持久化（`mewcode/agent.py:846-851`）
- [x] Plan 问答：输入 `/plan` 后模型调用 `AskUserQuestion`，`InlineQuestionPrompt` 在当前对话区域显示；按 `↓` 再按 `Enter` 后可观察到摘要 `mode: fast`，随后 Agent Loop 继续（`test_tui_collects_ask_user_answer`）
- [x] Plan 文件目录：`Agent._get_plan_path()` 的父目录等于 `<work_dir>/plan`，首次使用自动创建，文件扩展名为 `.md`（`test_plan_mode`）
- [x] 完成一次 Plan 对话后输入 `/do`，下一次 Provider Schema 含 `WriteFile` 且不含 `WritePlan`，对话中出现 `Plan Mode is no longer active`（`test_tui_plan_and_do_commands_switch_permission_mode`）
- [x] 文本型 `AskUserQuestion` 等待时输入 `/do`，当前问题显示 `Question cancelled`、`permission_mode` 变为 `DEFAULT`，Agent 下一轮输出 `Execution mode ready.`（`test_do_command_exits_plan_mode_from_inline_question`）
- [x] ReAct 文件操作闭环：精确文件名位于 `plan/` 时，第一次 `Glob` 返回嵌套相对路径，下一轮执行删除命令，第三轮输出完成信息且磁盘文件不存在（`test_react_finds_nested_named_file_then_deletes_and_verifies`）
- [x] 等待首个 token 时 `.thinking-label` 在 120ms 内渲染出不同帧；回复 `✅ 已成功删除` 最终 Markdown source 为 `✓ 已成功删除`，且 thinking 行隐藏（`test_thinking_animation_and_terminal_safe_status_emoji`）
- [x] 工具执行阻塞时 `.tool.is-running` 在 120ms 内变化并显示 `Running ControlledRead` 与耗时；工具完成后 `running is False` 且稳定显示 `✓ ControlledRead`（`test_tool_line_animates_until_execution_finishes`）
- [x] 完整 emoji 序列降级：笑脸、ZWJ 职业、国旗、keycap、BMP variation、默认彩色符号、肤色修饰和状态 emoji 输入 `🥰 👨‍💻 🇨🇳 1️⃣ ☀️ ©️ ❤️ ⏰ 👍🏽 ✅ ❌` 后得到 `:) ◇ ◇ 1 ☀ © ❤ ◇ ◇ ✓ ✗`（`test_terminal_safe_text_replaces_colored_emoji_sequences`）
- [x] max_tokens 升档：模拟 `stop_reason="max_tokens"` 看到 `RetryEvent(reason="max_tokens escalation")` 与 `client.set_max_output_tokens(64000)`；连续 3 次后停止恢复进入下一轮主流程（`mewcode/agent.py:529-559`）
- [ ] 留存证据：验收阶段无截图；如需补，可在 Textual 中输入 `hi` 拍照保存 stream 渲染

## 5. 文档

- [x] spec.md / tasks.md / checklist.md 三件套齐全（`docs/ch4/`）
- [ ] commit 信息标注 `ch04` 与三件套关闭状态（待统一打包提交）

## 6. 本次验收记录

- 2026-07-16：`compileall`、Ruff、Mypy 全部通过；完整测试为 `83 passed`。
- 原文中的行号来自参考分支；当前仓库按同名类、函数和调用链验收，实际行号随格式化发生偏移。
- 自动化 TUI 测试已覆盖普通流式回复、`/plan` / `/do` 和权限选择；截图与 Git commit 保留为人工发布步骤。
