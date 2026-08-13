# CH13：SubAgent 系统 Spec

## 1. 背景

主 Agent 同时承担探索、规划、实现和验证时，对话会被大量中间信息污染。CH13 增加可定义、可
隔离、可后台运行的子 Agent，让主 Agent 只接收结构化结果，同时保留取消、成本和调用链证据。

## 2. 目标

- 用一个稳定的 `Agent` 工具统一定义式 Agent 与上下文 Fork。
- 定义式 Agent 使用独立对话、工具池、权限状态、文件缓存和 Token 计数。
- Fork 深拷贝父 Agent 完整历史并复用客户端、system prompt、Hook 和消息前缀，支持 prompt cache。
- 后台任务不阻塞当前对话，完成后通过 `<task-notification>` 异步回流。
- 使用多层工具过滤和禁止嵌套 Fork，避免递归失控。

## 3. API Contract

完整请求、响应与错误语义见 [`../api-contract.md`](../api-contract.md#12-subagent-contract)。

### Agent 定义

Agent 文件使用 YAML frontmatter + Markdown body：

```yaml
---
name: Explore
description: Fast read-only codebase exploration.
model: haiku
maxTurns: 30
permissionMode: dontAsk
tools: [ReadFile, Glob, Grep, Bash, ToolSearch]
disallowedTools: [Agent, EditFile, WriteFile]
background: false
---
Markdown system prompt
```

加载优先级固定为：

```text
<project>/.simplecode/agents
  > ~/.simplecode/agents
  > packaged builtins
  > plugin agents directories
```

项目和用户插件目录中的 `<plugin>/agents/*.md` 自动发现，也可以调用
`register_plugin_source()` 显式注册。文件级解析失败只记录 warning；`get(name)` 热重载失败时
回退最近一次有效定义。未知 frontmatter 字段保存在 `metadata`，供后续扩展使用。

### 统一 Agent 工具

```text
Agent(
  prompt: str,
  description: str,
  subagent_type?: str,
  model?: str,
  run_in_background?: bool,
  name?: str,
  isolation?: str,
  team_name?: str,
)
```

- `subagent_type` 非空：加载对应定义，创建空白对话的专家 Agent。
- `subagent_type` 为空：构造 Fork，继承父完整消息，强制后台。
- 未知类型返回带可用类型列表的错误 `ToolResult`。
- `team_name` 和 `isolation=worktree` 作为稳定 Schema 预留，分别由 CH15、CH14 落地。

## 4. 功能需求

### F1：定义和加载

- `AgentDefinition`/`AgentDef` 保存角色、描述、prompt、工具白黑名单、模型、轮次、权限、后台、
  isolation、来源和原始 metadata。
- 校验 name、description、body、model、permissionMode、isolation、maxTurns 和列表字段。
- 内置 `Explore`、`Plan`、`general-purpose`；`Verification` 仅在 flag 开启时出现。

### F2：Fork 与 RunToCompletion

- Fork 用 `copy.deepcopy` 保留 text、thinking、tool_use、tool_result 的完整结构。
- 父历史末尾存在悬挂 tool_use 时补 `interrupted` 错误结果。
- Fork 指令包含 `<fork_boilerplate>`；历史中已存在该标签时拒绝再次 Fork。
- `RunToCompletion` 不等待用户输入，以模型最后一个无工具调用回合的文本作为结果。

### F3：隔离和共享

- 隔离：ConversationManager、PermissionChecker、FileCache、replacement state、Token 和轮次状态。
- 共享：Provider client、HookEngine、文件系统和父工具实现中的外部连接基础设施。
- Fork 额外继承父 system prompt、完整历史、active skills 和 replacement decisions 的独立副本。

### F4：工具过滤

过滤顺序：

1. MCP 工具直通。
2. 全局禁用 Agent、AskUserQuestion 等递归/交互工具。
3. project/user/plugin 自定义定义叠加管理类工具限制。
4. 后台 Agent 叠加 `ASYNC_AGENT_ALLOWED_TOOLS`。
5. 定义级 `disallowedTools` 和 `tools`。

模型可见 Schema 与实际 Registry 使用同一个过滤结果。核心文件工具重新实例化，避免共享父文件缓存。

### F5：后台任务

- `TaskManager` 状态：running / completed / failed / cancelled。
- 显式 `run_in_background`、定义 `background: true`、Fork 三种路径直接后台启动。
- 前台执行超过 120 秒时原实例自动转后台，不取消重跑。
- 用户按 ESC 可把正在执行的前台子 Agent 原实例移交后台。
- 后台默认 600 秒超时；`cancel` 同时清理 monitor 和实际执行 task。
- 完成 ID 进入 `asyncio.Queue`，TUI 只在父 Agent 空闲时注入通知，不修改正在发送的请求。

### F6：链路追踪

- `TraceRegistry` 记录 `agent_id / parent_id / trace_id`、状态、起止时间、Task ID 和 Token。
- 支持按 trace 查询节点和汇总输入/输出 Token。
- 同步完成、后台完成、失败、超时和取消都关闭 TraceNode。

### F7：Slash Command

- `/tasks`：列出后台任务。
- `/task info <id>`：查看状态、耗时、Token、Trace 和结果。
- `/task cancel <id>`：取消运行任务。
- `/trace [trace-id]`：查看调用树和 Token 汇总。

## 5. 非功能需求

- 子 Agent 不可使用 `Agent`，Fork-of-Fork 必须确定性拒绝。
- 非交互权限决策中的 ask 转为 deny，不创建无人消费的 UI future。
- 后台完成通知 result 最多 5000 字符。
- TaskManager 只在同一 asyncio event loop 访问内部状态。
- App 退出时取消并等待仍在运行的后台任务。
- 单个 Agent 定义、子任务或通知失败不得导致 TUI 进程退出。

## 6. Out of Scope

- Git worktree 创建、合并和清理：CH14。
- 长期 Team、Mailbox、共享任务和外部 pane backend：CH15。
- 后台任务跨进程持久化与恢复。
- 远程 Agent backend、Agent 市场和版本管理。

## 7. 完成定义

- `tasks.md` 全部任务完成。
- `checklist.md` 所有实现、接入、测试和边界条目关闭。
- `pytest`、Ruff、Mypy、compileall、wheel/sdist 构建通过。
