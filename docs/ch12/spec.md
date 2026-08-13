# CH12：Hook 生命周期自动化系统 Spec

## 1. 背景

CH11 已支持按需加载 Skill，但格式化、危险操作拦截、项目上下文注入等固定动作仍需用户
重复触发。CH12 引入声明式 Hook：用“事件 + 条件 + 动作”将自动化挂到 Agent 生命周期，
同时保证 Hook 失败不会破坏主流程。

## 2. 目标与边界

本章交付：

- 15 个生命周期事件；
- Hook/Action/HookContext/Condition/ToolRejectedError 等公共模型；
- 条件 DSL、上下文变量替换、once/async/timeout；
- command、prompt、http、agent 四类 Action；
- `run_hooks` 与可拦截的 `run_pre_tool_hooks`；
- YAML 加载、集中校验及 CLI 启动失败定位；
- Agent Loop、TUI、权限、压缩、文件变化和 Slash Command 全生命周期接入。

本章不实现 agent Action 的真实子 Agent、Hook 热更新、条件括号/优先级、HTTP 重试和认证。

## 3. 生命周期事件

`LifecycleEvent` 是可直接与字符串比较的 `StrEnum`，固定包含：

| 层级 | 事件 |
| --- | --- |
| 应用 | `startup`、`shutdown` |
| 会话 | `session_start`、`session_end` |
| 轮次 | `turn_start`、`turn_end` |
| 模型消息 | `pre_send`、`post_receive` |
| 工具 | `pre_tool_use`、`post_tool_use` |
| 系统 | `error`、`compact`、`permission_request`、`file_change`、`command_execute` |

## 4. YAML Contract

```yaml
hooks:
  - id: block-dangerous-delete
    event: pre_tool_use
    condition: 'tool == "Bash" && args.command =~ /rm\s+-rf/'
    reject: true
    action:
      type: prompt
      message: "拒绝危险删除：$TOOL_ARGS.command"

  - id: format-python
    event: file_change
    condition: 'args.file_path ~= "*.py"'
    async: true
    action:
      type: command
      command: "ruff format $FILE_PATH"
      timeout: 20
```

加载期规则：

1. `event` 必须属于 15 个事件；缺少 id 时生成 `<event>_<zero-based-index>`。
2. Action 仅允许 `command/prompt/http/agent`，分别要求 `command/message/url/prompt`。
3. `reject: true` 仅允许 `pre_tool_use`；`pre_tool_use` 禁止 `async: true`。
4. `timeout` 必须为正整数，默认 30 秒。
5. condition 必须是字符串且能被 DSL 解析。
6. 任意错误抛 `HookConfigError`，消息包含 Hook id 或数组序号。

## 5. 条件 DSL

- `==`：精确相等；`!=`：不相等；
- `=~`：正则搜索，`/.../` 外层斜线自动移除，非法正则按不匹配处理；
- `~=`：`fnmatch` glob；
- 字段：`tool`、`event`、`args.<key>`，工具参数支持嵌套点路径；
- 一条表达式可用 `&&` 或 `||`，但禁止混用；不实现括号和隐式优先级。

## 6. HookContext 与模板

上下文包含 `event_name/tool_name/tool_args/file_path/message/error/agent_id/result`。Action
字符串支持：

- `$EVENT`
- `$TOOL_NAME`
- `$FILE_PATH`
- `$MESSAGE`
- `$ERROR`
- `$TOOL_ARGS.<key>`

未定义变量替换为空串；dict/list 参数序列化为 JSON，避免模板执行因缺字段失败。

## 7. Action 执行器

### command

使用 `asyncio.create_subprocess_shell`，stdout/stderr 合并。`wait_for` 超时后先 kill 再 wait，
取消时也清理子进程。退出码非零返回 `success=false`，不抛进 Agent Loop。

### prompt

展开模板并进入 HookEngine 的 prompt 队列。Agent 在下一次 provider 请求前将其放进系统提示词的
`HookInjectedContext` 区域；`pre_send/session_start` 的 prompt 当轮即可生效。

### http

默认 POST，通过 `run_in_executor` 执行 `urllib.request.urlopen`，固定 30 秒网络超时，响应最多
读取 500 字节；存在 body 时默认 `Content-Type: application/json`。

### agent

CH12 仅返回 `agent executor not yet implemented` 的成功占位结果，待 CH13 SubAgent 接入。

## 8. HookEngine

- `find_matching_hooks` 按 event、once 状态、condition 三层筛选；
- `run_hooks` 顺序执行同步 Hook，`async` Hook 用后台 Task 派发；
- 首次调度即 `mark_executed`，避免 once 异步任务被重复派发；
- Action 成功或失败都写入 `HookNotification`；prompt 成功结果进入一次性队列；
- `run_pre_tool_hooks` 同步执行 `pre_tool_use`，命中 reject 时返回
  `ToolRejectedError(tool, reason, hook_id)`；
- Action/回调异常被记录为失败通知，不向主流程传播；
- 保留 CH11 前最小 callback `register` API，保证既有扩展兼容。

## 9. 生命周期集成

- `SimpleCodeApp.on_mount/on_unmount`：`startup/shutdown`；
- `Agent.run`：`session_start/turn_start/pre_send/post_receive/turn_end/session_end`；
- 工具权限判断前：同步 `pre_tool_use`，拒绝后跳过权限弹窗与真实工具；
- 工具完成后：`post_tool_use`；成功写工具再触发 `file_change`；
- 需要人工审批前：`permission_request`；
- 自动或手动压缩完成：`compact`；内部和 UI 异常：`error`；
- Slash Command 分发前：`command_execute`。

拦截结果作为 `ToolResultEvent(is_error=True)` 回灌模型：

```text
Hook rejected: <reason>
```

因此模型可以观察原因并选择更安全的下一步。

## 10. 非功能要求

- Hook 是辅助机制：失败隔离，不能让 Agent/TUI 崩溃；
- pre-tool 拦截必须早于权限请求和工具执行；
- async Hook 不阻塞当前事件；进程退出前回收后台任务；
- command 超时/取消不遗留直接子进程；
- 配置错误在 TUI 创建前失败，避免运行一半才暴露；
- 保持 Python 3.11、Ruff、Mypy 与既有 CH2–CH11 API 兼容。

## 11. 完成定义

以 `checklist.md` 为准；当前专项 45 个 Hook 测试与全仓 418 个测试通过。
