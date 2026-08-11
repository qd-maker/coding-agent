# MewCode API Contract

本文档先于实现定义 ch02 的可调用边界。当前版本没有 HTTP 服务；这里的 API 指 CLI、配置文件和 Python 内部接口。

## 1. CLI Contract

### 启动

```text
mewcode [--config PATH]
python -m mewcode [--config PATH]
```

### 成功行为

- 读取并校验 YAML 配置。
- 启动交互式终端界面。
- 用户提交文本后立即发起异步流式请求。
- 文本增量持续更新当前回复；同一进程内保留历史消息。
- Claude thinking 增量显示在独立区域。
- `Ctrl+C` 在生成中立即取消当前请求；非生成状态下第一次按键不退出，1.5 秒内第二次按键才退出。

### CLI 错误

| 场景 | 可观测结果 |
|---|---|
| 配置文件不存在或 YAML 非法 | stderr 输出 `Configuration error: ...`，退出码 `2` |
| 配置字段校验失败 | stderr 输出字段路径和原因，退出码 `2` |
| API key 缺失 | stderr 输出 `Invalid API key: API key is missing`，退出码 `2` |
| TUI 内 Provider 请求失败 | 对话区显示统一 `LLMError` 文本，当前会话保持可用 |

## 2. Agent Loop Contract

### 调用

- 主接口：`async for event in agent.run(conversation)`。
- `conversation` 是 `ConversationManager`；兼容入口也接受字符串并自动追加用户消息。
- 循环无工具调用时以 `LoopComplete` 结束；达到迭代上限或连续未知工具时以 `ErrorEvent` 结束。

### 事件

- 流式内容：`StreamText`、`ThinkingText`。
- 工具生命周期：`ToolUseEvent`、`ToolResultEvent`、`TurnComplete`。
- 状态：`UsageEvent`、`RetryEvent`、`CompactNotification`、`HookEvent`。
- 终止：`LoopComplete`、`ErrorEvent`。
- 人机确认：`PermissionRequest` 携带 Future，消费者回填 `ALLOW`、`DENY` 或 `ALLOW_ALWAYS`。

### Plan Mode

- `/plan` 切换到 `PermissionMode.PLAN`，Provider 只收到只读工具和 `WritePlan` 的 Schema。
- `WritePlan` 不接受路径参数，只能写入当前 Agent 的唯一 Plan 文件。
- Plan Mode 权限检查优先于持久化允许规则，非只读、非 `WritePlan` 工具始终被拒绝。
- `/do` 由客户端本地拦截并切回 `PermissionMode.DEFAULT`，隐藏 `WritePlan`、恢复完整普通
  工具集，并在下一模型轮注入退出提醒，使历史 Plan Mode reminder 不再生效。
- Plan 文件路径在一次 Agent 生命周期内保持不变，位于 `plan/`。
- `AskUserQuestion` 与 `PermissionRequest` 都在主对话流内渲染，不打开 Modal；选择项用
  `↑` / `↓` 移动，`Enter` 确认，完成后保留选择摘要。
- 文本型 `AskUserQuestion` 正在等待时输入 `/do`，客户端先切到执行模式，再取消当前问题，
  Agent Loop 随后使用普通工具 Schema 继续。

## 3. YAML Contract

规范配置使用 `providers` 与 `mcp_servers` 两个列表。当前 TUI 使用
`providers` 中的第一项作为活动 Provider：

```yaml
providers:
  - name: anthropic-official
    protocol: anthropic
    base_url: https://api.anthropic.com
    api_key: ${ANTHROPIC_API_KEY}
    model: claude-sonnet-4-6
    thinking: true
    max_output_tokens: 64000

system_prompt: You are MewCode, a concise coding assistant.

mcp_servers:
  - name: context7
    command: npx
    args: ["-y", "@upstash/context7-mcp"]
    startup_timeout: 20
    tool_timeout: 120
  - name: remote_tools
    url: https://mcp.example.com/mcp
    headers:
      Authorization: Bearer ${REMOTE_MCP_TOKEN}
```

`api_key` 支持明文、`${ENV_NAME}`、`$ENV_NAME` 和 `env:ENV_NAME`。环境变量不存在时视为缺失 key。

为兼容 CH2–CH6 已有配置，加载器仍接受 `provider:` 单对象、Provider 字段直接位于 YAML
根节点，以及旧的 `mcp_servers` mapping；文档和新生成的配置统一使用上述列表格式。
`providers` 至少包含一项，Provider name 不允许重复。`mcp_servers` 可为空，每个服务器必须且
只能选择一种传输，server name 不允许重复：

- stdio：`command`，可选 `args`、`env`；子进程只继承 `PATH`、Windows 最小运行时白名单（`SYSTEMROOT / COMSPEC / PATHEXT`）和显式 `env`。
- Streamable HTTP：`url`，可选 `headers`；header 值中的 `${VAR}` 在连接时展开，缺失变量保留占位符。
- 两种传输都可配置 `startup_timeout`（connect + list_tools，默认 20 秒）和 `tool_timeout`（单次 tools/call，默认 120 秒）；两者必须大于 0。
- server 名只能包含 ASCII 字母、数字和下划线；远端 tool 名中的其他字符在 wrapper 名中规范化为 `_`，调用 MCP server 时仍使用原始名称。
- 同时配置 `command`/`url`，或两者都未配置，启动时抛 `ConfigurationError` 并以退出码 `2` 结束。

## 4. MCP Client Contract

- `MCPClient.connect()` 使用官方 MCP SDK 完成 transport、`ClientSession` 和 `initialize()`，并由 `AsyncExitStack` 统一关闭。
- `MCPManager.register_all_tools(registry)` 并行连接服务器；单服务器失败或超时返回到 `errors`，不阻塞其他服务器，注册顺序仍按配置顺序保持确定性。
- 注册名为 `mcp_<server>_<tool>`；Schema envelope 保持 `name / description / input_schema`，其中 `input_schema` 使用 MCP 原始 JSON Schema。
- MCP 工具默认 `should_defer = True`，模型先通过 `ToolSearch` 发现工具，再按普通 Tool Contract 调用。
- 工具执行错误返回 `ToolResult(is_error=True)`；`CallToolResult.isError` 原样映射；空内容返回 `(no output)`。
- TUI 首屏后台初始化 MCP；第一条模型消息发送前等待初始化；退出时取消初始化任务并关闭所有 session。`-p` headless 在 Agent 运行前同步初始化 MCP，并在 `finally` 中执行 Hook、会话持久化和相同的资源关闭流程。

## 5. Provider Contract

### 请求

```python
client.stream(
    conversation: ConversationManager,
    system: str | None,
    tools: list[dict[str, Any]] | None,
) -> AsyncIterator[StreamEvent]
```

### 响应事件

- `TextDelta(text)`：可立即渲染的文本增量。
- `ThinkingDelta(text)`：可立即渲染的思考增量。
- `ThinkingComplete(thinking, signature)`：可回放的完整 Claude thinking block。
- `ToolCallStart(tool_id, tool_name)`：工具调用开始。
- `ToolCallDelta(tool_id, arguments_delta)`：JSON 参数增量。
- `ToolCallComplete(tool_id, tool_name, arguments)`：完整工具调用。
- `StreamEnd(stop_reason, input_tokens, output_tokens)`：流正常结束。

### 统一错误

- `AuthenticationError`：认证失败或 key 缺失。
- `RateLimitError(retry_after)`：限流，可带服务端重试秒数。
- `NetworkError`：连接、DNS 或超时失败。
- `LLMError`：其他 Provider/API/响应格式错误。

## 6. Conversation Contract

- 写入操作按调用顺序追加，单消费者，不加锁。
- `get_messages()` 返回浅拷贝，外部不能替换内部列表。
- `serialize("anthropic")` 保留 thinking signature、tool input 和 tool result error 标记。
- `serialize("openai")` 将工具调用转换为 Responses API 顶层 input items。
- 未知协议抛出 `ValueError("Unknown protocol: ...")`。

## 7. Context Contract

### Token 估算

- `estimate_text_tokens(text)` 使用可解释的本地近似：ASCII 字符按约 4 字符/token，
  非 ASCII 字符按约 1 字符/token，并向上取整。
- `estimate_conversation_tokens(conversation)` 统计消息文本、thinking、tool name/id、tool
  input JSON、tool result 以及固定消息开销；不发网络请求、不依赖 Provider tokenizer。
- 每轮 Agent Loop 在压缩判断前刷新估算值；已有 Provider `input_tokens` 时取
  `max(provider_reported, local_estimate)`，避免低估当前新增的工具结果。

### 两层压缩

- Layer 1：单条工具结果超过 5,000 字符，或本轮结果聚合超过 20,000 字符时，完整内容
  写入 `.mewcode/session/tool-results/<tool_use_id>.txt`，历史仅保留
  `<persisted-output>` 预览与绝对路径。`apply_tool_result_budget` 本身返回新会话、不修改
  入参；Agent Loop 显式把返回历史提交回外层 `ConversationManager`。
- Layer 2：达到 `context_window - 20,000 - 13,000` 时调用 LLM 生成九节结构化摘要，
  用摘要、压缩边界和恢复附件就地替换旧历史。摘要成功后重新估算 Token 并写回
  `conversation.last_input_tokens`。
- `/compact` 对任意非空会话立即执行 Layer 2，不受自动阈值限制，返回并显示
  `before_tokens -> after_tokens`。
- `CompactEvent` 与 `CompactNotification` 均携带 `before_tokens / after_tokens`。

## 8. Instructions / Session / Memory Contract

### 项目指令

- 发现顺序固定为 `<project>/MEWCODE.md`、`<project>/.mewcode/MEWCODE.md`、
  `~/.mewcode/MEWCODE.md`；高优先级内容排在前面，以 `\n---\n` 分隔。
- 独占一行的 `@include <relative-path>` 最多递归 5 层；解析后的真实路径必须仍位于
  project root，符号链接和 `..` 均不能逃逸。
- 指令与自动记忆作为两条独立 user message 注入 environment 之后，并追加 assistant
  确认边界；单次历史只注入一次，compact/resume 后允许重新注入。

### 会话文件

```text
<project>/.mewcode/sessions/
├── session_<timestamp>_<suffix>.jsonl
└── session_<timestamp>_<suffix>.meta
```

- JSONL 每行是一个 `SessionRecord`，类型为 `system_prompt / user / assistant /
  tool_result / compression`；追加后立即 flush，恢复时空行和坏行跳过。
- `compression` 是追加式 checkpoint；恢复时最后一个 checkpoint 的摘要替换它之前的消息，
  使 `/compact` 不需要重写整个 JSONL 也能保持压缩效果。
- `.meta` 独立保存 `id / title / summary / message_count / total_tokens / created_at /
  last_active`，通过临时文件 + replace 原子更新。
- 恢复时只保留 tool_use/tool_result 链完整的最大前缀；超过 Agent context window 时先执行
  一次手动 compact；距离上次活跃满 24 小时则追加代码可能变化的时间提示。

### 自动记忆

- 用户级文件：`~/.mewcode/memories.md`；项目级文件：
  `<project>/.mewcode/memories.md`。
- 每 5 个完成回合后台调用一次 LLM；输入包含当前完整记忆与增量对话，输出按
  `用户偏好 / 纠正反馈 / 项目知识 / 参考资料` 四类完整重写，由 LLM 负责语义去重。
- 用户偏好和纠正反馈写用户级；项目知识和参考资料写项目级；占位内容不落盘。
- `/memory [list|clear|edit]` 管理记忆；
  `/session [list|resume|new|delete]` 管理会话。

## 9. Slash Command / UIController Contract

### 命令模型

```text
CommandType = LOCAL | LOCAL_UI | PROMPT

Command(name, aliases, description, usage, type, arg_prompt, hidden, handler)
CommandContext(args, agent, conversation, session, session_manager,
               memory_manager, ui, config)
```

- 所有 command name/alias 均不带 `/`，注册和查找时大小写不敏感。
- `CommandRegistry.register` 使用 `asyncio.Lock`；`register_sync` 用于单线程启动装配。
  canonical name、alias 及跨字段冲突都抛 `ValueError`。
- `list_commands()` 默认排除 hidden 命令并按 canonical name 排序；`find()` 同时支持名称和别名。
- handler 固定为 `async handler(ctx) -> None`，只能通过 `CommandContext.ui` 或 `config`
  中注入的抽象回调影响 UI/App，不 import Textual 实现。

### 解析与补全

- `parse_command(text)` 返回 `(name, args, is_command)`；只处理去除前导空白后以 `/`
  开头的输入，name 转小写，args 保留内容并 trim；空串、纯空白和纯 `/` 均不抛异常。
- `complete(registry, prefix)` 对非 hidden 命令的 canonical name 和 alias 做大小写不敏感的
  前缀匹配，返回按字典序排列的 `/name` 候选。
- 单候选时 Tab 直接回填并补一个空格；多候选时显示 `CompletionPopup`，不触发 Agent。

### UIController

```text
add_system_message(text) -> awaitable
send_user_message(text) -> awaitable
set_plan_mode(enabled) -> None
get_token_count() -> int
refresh_status() -> None
```

- `LOCAL` 命令只回显本地结果；`LOCAL_UI` 可通过 UIController/注入回调修改 UI 状态；
  `PROMPT` 命令把模板化 prompt 交给 `send_user_message`，进入正常 Agent Loop。
- TUI 的 Enter 入口先调用 command dispatcher；只有 `is_command=False` 才启动普通 Agent 请求。
- handler 异常由 dispatcher 捕获并显示为系统消息，不中断 TUI。

### 十个内置命令

`help / compact / clear / plan / do / session / memory / permission / status / review`。
`review` 是 PROMPT 命令；本章不注册 Skill、Task、Trace 或 Worktree 命令。

## 10. Skill Contract

### 文件与数据模型

Skill 使用 YAML frontmatter + Markdown 正文：

```yaml
---
name: commit
description: Inspect changes and create a commit
allowedTools: [Bash, ReadFile, Grep]
mode: inline
model: inherit
context: full
---
Follow this SOP. User arguments: $ARGUMENTS
```

- `SkillDef` 固定包含 `name / description / prompt_body / allowed_tools / mode / model /
  context / source_path / is_directory`。
- `name` 匹配 `^[a-z][a-z0-9-]*$`；`mode` 只能是 `inline | fork`；`context` 只能是
  `full | recent | none`。格式或字段非法时抛 `SkillParseError`。
- 支持根目录单文件 `<skills>/<name>.md`，以及目录型
  `<skills>/<name>/SKILL.md + tool.json + references/*.py`。
- `$ARGUMENTS` 按原样替换为 Slash Command 参数；正文没有占位符时不追加参数。

### 发现、优先级与热加载

- 搜索顺序固定为 `<project>/.mewcode/skills`、`~/.mewcode/skills`、包内 builtins；
  首次出现的同名 Skill 生效，因此优先级为项目级 > 用户级 > 内置级。
- 单个文件解析失败只记录 warning 并跳过，不阻断其他 Skill；`get(name)` 每次重新读取源文件，
  热加载解析失败时回退最后一次有效缓存。
- 启动时只向 environment message 注入 `name + description` catalog；完整 SOP 只在
  `LoadSkill` 或显式 `/<skill-name>` 激活后注入 `## Active Skills`。
- 内置 Skill 恰好为 `commit`（inline）、`review`（fork）、`test`（inline）。

### 执行与工具边界

- inline：渲染正文并在主 Agent 调用 `activate_skill`；参数或默认触发语句作为普通 user
  message 进入主 Agent Loop，结果保留在主对话。
- fork：创建独立 `ConversationManager` 和 Agent；`full` 携带全量文本摘要，`recent` 携带
  最近 5 条消息，`none` 不携带历史；执行结束只把结果回流到主 UI。
- `allowedTools` 非空时形成工具白名单。多个激活 Skill 的白名单取交集；标记
  `is_system_tool=True` 的工具始终保留，以支持 Skill 嵌套。模型可见 Schema 与实际执行入口
  使用同一过滤结果，不能通过手工 tool call 绕过。
- App 启动时验证所有 `allowedTools`；名称既不在主 Registry、也不在该目录型 Skill 的
  `tool.json` 时抛 `SkillDependencyError`。执行前再次验证热加载版本。
- `LoadSkill` 是 read-only system tool；成功后激活完整 SOP，目录型 Skill 同时注册
  `tool.json` 声明且在 `references/<tool-name>.py` 实现的专属工具。

### 命令

- 每个 Skill 自动注册为 `/<name>` PROMPT 命令并在描述中标记 `[skill]`；内置 review
  Skill 替换 CH10 的硬编码 `/review`。
- `/skill list | info <name> | reload` 管理 catalog、详情与重新扫描；reload 同步刷新动态
  Slash Command 和 Agent catalog。
- `/clear` 同时清除对话与 `active_skills`/工具白名单状态。

## 11. Hook Contract

### YAML request

```yaml
hooks:
  - id: block-dangerous-delete
    event: pre_tool_use
    condition: 'tool == "Bash" && args.command =~ /rm\s+-rf/'
    reject: true
    once: false
    async: false
    action:
      type: prompt
      message: "禁止执行危险删除：$TOOL_ARGS.command"
```

- `event` 必须是 15 个 `LifecycleEvent` 值之一。
- `condition` 可省略；支持 `==`、`!=`、`=~`、`~=`，同一表达式只允许使用
  `&&` 或 `||` 中的一种。
- `action.type` 仅允许 `command`、`prompt`、`http`、`agent`；分别要求
  `command`、`message`、`url`、`prompt` 字段。
- `reject: true` 只允许用于 `pre_tool_use`；`pre_tool_use` 不允许 `async: true`。
- `action.timeout` 为正整数秒，默认 30。

### Runtime context

`HookContext` 提供 `event_name`、`tool_name`、`tool_args`、`file_path`、`message`、
`error`。Action 模板支持 `$EVENT`、`$TOOL_NAME`、`$FILE_PATH`、`$MESSAGE`、
`$ERROR`、`$TOOL_ARGS.<key>`；不存在的变量替换为空串。

### Engine response

- `run_hooks(event, context)`：执行普通 Hook；单个 Hook 失败只生成失败通知，不中断主流程。
- `run_pre_tool_hooks(context)`：返回 `ToolRejectedError | None`。命中拒绝规则时不执行工具，
  工具结果为 `Hook rejected: <reason>`，供模型调整策略。
- prompt Action 的结果进入一次性消费队列并注入后续模型上下文。
- async Action 后台调度；once Hook 首次触发后不再运行。

### Lifecycle coverage

会话、轮次、模型消息、工具前后、启动/退出、错误、压缩、权限请求、文件变化和 Slash
Command 执行节点都会构造独立 `HookContext`。`agent` Action 在 CH12 只返回占位结果，不创建子
Agent。

### Errors

- 配置错误：启动失败并返回 `HookConfigError`，消息包含 Hook id 或数组序号及字段原因。
- command 超时：返回失败 `ActionResult`，并在 kill/wait 子进程后继续主流程。
- HTTP/模板/条件/Action 执行错误：记录日志和 `HookNotification`，不传播到 Agent Loop。

## 12. SubAgent Contract

### Agent definition

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
Markdown system prompt body
```

- 搜索优先级固定为 project > user > builtin > plugin，同名定义由更高层覆盖。
- `get(name)` 对磁盘文件热重载；坏文件只记 warning，并回退最近一次有效定义。
- 未识别 frontmatter 字段保存在 `metadata`，供后续 hooks/MCP/skills 扩展消费。

### Unified Agent tool

```text
Agent(prompt, description, subagent_type?, model?, run_in_background?, name?, isolation?, team_name?)
```

- 有 `subagent_type`：空白对话 + 对应定义的 system prompt、模型、轮次、权限与工具限制。
- 无 `subagent_type`：深拷贝父 Agent 完整消息，追加 fork boilerplate，强制后台执行；已经包含
  `<fork_boilerplate>` 的历史拒绝再次 fork。
- 子 Agent 使用非交互式 `RunToCompletion`；权限决策中的 ask 转为 deny，不创建无人消费的 UI future。
- Fork 复用父客户端、system prompt、Hook 引擎和消息前缀；工具、权限、Token、replacement state、
  对话和文件缓存保持子实例隔离。

### Task and trace response

- 前台超过 120 秒自动挂入 `TaskManager`；ESC 可将当前子 Agent 原实例转后台，不终止重跑。
- 后台默认 600 秒超时；状态为 running/completed/failed/cancelled。
- 完成结果通过 `<task-notification>` user message 注入父对话，result 最多保留 5000 字符。
- `/tasks` 列举任务，`/task info <id>` 查看详情，`/task cancel <id>` 取消运行任务，
  `/trace [trace-id]` 查看父子链路及 Token 汇总。

### Tool filtering order

MCP 工具直通；其余依次应用全局禁止、自定义 Agent 限制、后台白名单、定义级
`disallowedTools` 与 `tools` 白名单。全局禁止始终包含 `Agent` 和 `AskUserQuestion`，避免递归和
无人值守交互。

## 13. Tool Contract

### 工具定义

每个工具继承统一的 `Tool` 抽象类，并提供名称、描述、Pydantic 参数模型、类别、并发安全标记和异步执行方法。工具执行返回：

```text
ToolResult(output: str, is_error: bool = false)
```

参数校验失败、未知工具、禁用工具、超时和执行异常均转换成 `is_error=true` 的结果回灌模型；`asyncio.CancelledError` 不被吞掉。

Agent 将工具结果写回会话后自动请求下一步，直到模型返回最终文本；每个用户回合最多运行 50 轮。达到上限时返回 `ErrorEvent`。TUI 仅展示工具摘要和耗时；超长结果保存到 `.mewcode/session/tool-results/`，预览和文件位置进入对话历史。

### Registry

```text
register(tool)                    注册工具，重名时报错
get(name)                         按名获取已启用工具
get_all_schemas(protocol)         导出当前可见工具 Schema
enable(name) / disable(name)      控制工具是否可用
mark_discovered(name)             披露 deferred 工具
```

Anthropic Schema 使用 `{name, description, input_schema}`；OpenAI Schema 使用 `{type: function, name, description, parameters}`。

### 六个内置工具

- `ReadFile`：读取 UTF-8 文本，可按 offset/limit 返回带行号内容。
- `WriteFile`：创建或覆盖 UTF-8 文本，自动创建父目录。
- `EditFile`：对旧文本做唯一匹配替换。
- `Bash`：运行 shell 命令，返回 stdout、stderr 和退出状态，支持超时。
- `Glob`：按 glob 模式查找文件，跳过常见依赖与缓存目录。
- `Grep`：按正则搜索文本内容，可用 basename glob 限定文件。

## 14. Prompt Contract

### Section builder

```python
PromptSection(name: str, priority: int, content: str)

PromptBuilder()
    .add(section: PromptSection)
    .build() -> str
```

- `PromptBuilder.add` 返回 builder 自身，可链式调用。
- `build` 按 `priority` 升序稳定排序，忽略 trim 后为空的 section，并以恰好两个换行分隔。

### System prompt

```python
build_system_prompt(
    hook_prompts: list[str] | None = None,
    coordinator_mode: bool = False,
    agent_catalog: str = "",
    custom_instructions: str = "",
    skill_section: str = "",
    memory_section: str = "",
    work_dir: str | Path = ".",
) -> str
```

- 普通模式按 Identity、System、DoingTasks、ExecutingActions、UsingTools、ToneStyle、
  TextOutput、Environment 顺序构造固定 section。
- CustomInstructions、Skills、Memory 为空时省略；Hook context 始终位于尾部。
- `coordinator_mode=True` 时身份主体由 `mewcode.teams.coordinator` 提供，仍可追加环境、
  可选 section 与 Hook context。

### Environment and dynamic reminders

```python
environment_section(work_dir: str | Path) -> PromptSection

build_environment_context(
    work_dir: str | Path,
    active_skills: dict[str, str] | None = None,
    skill_catalog: str = "",
    agent_catalog: str = "",
) -> str

build_plan_mode_reminder(
    plan_path: str | Path,
    plan_exists: bool,
    iteration: int,
) -> str
```

- Environment context 通过 `ConversationManager.inject_environment` 进入 user channel。
- Plan Mode reminder 通过 `ConversationManager.add_system_reminder` 进入 user channel，不拼入
  System Prompt；Plan 文件只能由 CH4 的 `WritePlan` 写入。

## 15. Permission Contract

### Decision API

```python
checker.check(tool: Tool, arguments: dict[str, Any]) -> Decision

Decision.effect: Literal["allow", "deny", "ask"]
Decision.reason: str
```

判定顺序固定为：Plan Mode 豁免、安全命令、危险命令、路径沙箱、Plan 限制或 YOLO
快速放行、规则引擎、模式矩阵。危险命令与越界路径不能被 `bypassPermissions` 或 allow
规则绕过；YOLO 通过这两层后跳过可配置规则。

### Permission modes

- `default`：读操作放行，写操作与非白名单命令询问。
- `acceptEdits`：读写操作放行，非白名单命令询问。
- `plan`：只读；仅系统交互工具和当前 `WritePlan` 计划文件写入豁免。
- `bypassPermissions`：通过静态安全层后放行。
- `custom`：通过静态安全层和显式规则后，其余操作询问。
- `dontAsk`：读操作放行，需要询问的写入或命令直接拒绝。

### Rule files

规则按 user、project、local 三层加载，优先级依次升高；同一文件后写规则优先。文件格式：

```yaml
- rule: WriteFile(src/**)
  effect: allow
- rule: Bash(git push*)
  effect: deny
```

`ALLOW_ALWAYS` 只追加 local YAML 文件。规则只能放行已经通过危险命令检测和路径沙箱的操作。

### TUI mode surface

TUI 只暴露三个面向用户的模式，并通过 Shift+Tab 按固定顺序循环：

- `Accept Edits`：对应 `/do`，底层使用 `acceptEdits`，文件编辑直接执行。
- `Plan`：对应 `/plan`，仅允许只读工具和计划文件写入。
- `YOLO`：底层使用 `bypassPermissions`，通过不可绕过的危险命令与路径沙箱检查后，
  跳过规则和 HITL，操作直接执行且不显示权限弹窗。

底部状态栏始终显示 `<Mode> on (shift+tab to cycle)`；YOLO 标签使用红色强调。

## 16. Git Worktree Contract

### Safe name API

```python
validate_slug(name: str) -> str
flatten_slug(name: str) -> str
generate_worktree_name() -> str  # agent-<8 hex>
```

- 名称最多 64 字符，可由 `/` 分隔多个安全段。
- 每段只允许 ASCII 字母、数字、点、下划线、连字符；`.`、`..`、空段、绝对路径和反斜杠
  逃逸均抛 `InvalidWorktreeName`。
- worktree 路径为 `<repo>/.mewcode/worktrees/<name>`；分支为
  `worktree-<flatten_slug(name)>`。

### Lifecycle API

```python
await manager.create(name: str, base: str = "HEAD") -> Worktree
await manager.enter(name: str) -> Worktree
await manager.exit(
    name: str | None = None,
    action: Literal["keep", "remove"] | None = None,
    discard_changes: bool = False,
    *,
    remove: bool | None = None,
    discard: bool | None = None,
) -> Worktree
await manager.remove(name: str, *, discard: bool = False) -> None
manager.list_worktrees() -> list[Worktree]
manager.get_current_session() -> WorktreeSession | None
manager.restore_session() -> WorktreeSession | None
```

- `create` 返回分支、路径、base HEAD 和创建时间；目标已是有效 worktree 时走纯文件系统恢复。
- 一个进程同一时刻只能 enter 一个 worktree；重复 enter 其他目录返回 `WorktreeInUseError`。
- `exit(action="keep")` 只返回主目录并保留工作；`action="remove"` 才删除目录和分支。
- remove 前检查 uncommitted、新 commit 和 unpushed commit。存在变更且未指定 discard 时抛
  `WorktreeHasChangesError`，消息含准确的 file/files、commit/commits 数量。
- Git 状态检查失败视为存在变更（fail closed）。

### Tool schema

```json
{"name":"EnterWorktree","input":{"name":"feature/demo"}}
{"name":"ExitWorktree","input":{"action":"keep|remove","discard_changes":false}}
```

- Enter 已有活动 session 时返回 `is_error=true`，不会额外创建目录。
- Exit 无活动 session、非法 action、dirty remove 均返回 `is_error=true`。
- 两个工具均为 deferred command tool；成功切换后下一轮核心工具使用新的 work_dir。

### Slash command

```text
/worktree create <name> [base]
/worktree list
/worktree enter <name>
/worktree exit [--remove] [--discard]
/worktree status
```

别名为 `/wt`。`create` 创建并进入；`exit` 默认 keep。

### SubAgent isolation

```python
AgentToolParams(
    prompt="...",
    description="...",
    subagent_type="general-purpose",
    isolation="worktree",
)
```

- `isolation` 可来自调用参数或 Agent Markdown frontmatter。
- 子 Agent 的 Read/Write/Edit/Glob/Grep/Bash、PathSandbox 和环境上下文绑定新 worktree。
- 无变更完成返回普通结果并删除 worktree；有变更返回普通结果并追加
  `[Worktree preserved at <path>, branch <branch>]`。
- 后台任务沿用 TaskManager/Trace Contract；取消或失败时不主动删除可能含部分工作的目录。

### Persistence and cleanup

- 活动 session 原子写入 `.mewcode/worktree_session.json`；清空时文件内容为 `{}`。
- CLI `--resume` 验证 `.git` 指针与 HEAD 后恢复；坏 JSON、无效路径或损坏元数据返回 None。
- stale cleanup 按“临时命名 → 非当前且已过期 → Git clean 且无未推送 commit”顺序过滤；
  任一检查失败均跳过。

## 17. AgentTeam Contract

### Team lifecycle

```python
manager.create_team(
    name: str,
    lead_agent_id: str,
    *,
    description: str = "",
    teammate_mode: str = "",
    is_interactive: bool = True,
) -> AgentTeam

await manager.resume_member(team_name: str, name_or_id: str) -> bool
await manager.stop_member(team_name: str, name_or_id: str) -> bool
await manager.merge_team(team_name: str) -> MergeResult
await manager.delete_team(team_name: str, *, discard: bool = False) -> None
```

- name 必须为 1–63 个可移植 ASCII 字符，不能为 `.`/`..`，也不能包含路径分隔符。
- 相同 team name 自动使用 `-2/-3/...`；每个 member name 在团队内唯一。
- delete 在任一成员 active 时抛 `TeamError`；默认保留有受保护变更的 worktree。
- merge 要求所有成员 idle 且 Lead worktree clean；任一冲突回滚整个本次 merge 序列。

### Shared tasks

```python
store.create(subject, description="", blocks=None, blocked_by=None,
             *, assignee="", created_by="") -> SharedTask
store.get(task_id) -> SharedTask | None
store.list_tasks(status=None, assignee=None) -> list[SharedTask]
store.update(task_id, ..., add_blocks=None, add_blocked_by=None) -> SharedTask | None
```

状态仅为 `pending / in_progress / completed / blocked`。持久化结构为
`{"next_id": int, "tasks": [...]}`，每次更新通过临时文件原子替换。

### Mailbox

```python
mailbox.write(agent_id, message) -> Path
mailbox.read(agent_id) -> list[MailboxMessage]
mailbox.consume(agent_id) -> list[MailboxMessage]
mailbox.broadcast(member_ids, message, exclude="") -> list[Path]
```

消息类型为 `text / shutdown_request / shutdown_response / approval_response`。`text` 经工具发送时
必须提供 160 字符以内摘要。直接消息只允许当前团队的 Lead/member；`to="*"` 广播时自动排除自己。

### Tool schemas

```json
{"name":"TeamCreate","input":{"team_name":"refactor","description":"optional"}}
{"name":"Agent","input":{"team_name":"refactor","name":"api","prompt":"...","description":"...","requires_approval":false}}
{"name":"SendMessage","input":{"to":"api|agent-id|*","message":"...","summary":"...","message_type":"text"}}
{"name":"TeamStop","input":{"member":"api","team_name":""}}
{"name":"TeamMerge","input":{"team_name":""}}
{"name":"TeamDelete","input":{"team_name":"","discard_worktrees":false}}
```

成功创建 Team 后 Lead 才看到协作工具。普通 SubAgent 不看到 Team 工具；teammate 只能看到隔离后的
执行工具、共享 Task 和 SendMessage，不能再调用 Agent 或销毁团队。

### Backend and resume

- `teammate_mode` 接受 `"" / auto / in-process / tmux / iterm2`。显式 pane backend 不可用时返回
  错误，不回退到协程。
- pane CLI 入口为 `mewcode -p --work-dir <path> [--agent-type X] [--model X] <prompt>`，团队信息
  由 `MEWCODE_TEAM_NAME / MEWCODE_TEAMMATE_NAME / MEWCODE_MAILBOX_DIR` 传递。
- in-process 成员完成时保存完整 transcript。向 idle 成员发送消息会重用原 Agent、恢复 transcript
  并执行新 mailbox 内容；不存在 pane 时返回显式恢复失败。

### Slash command

```text
/team create <name> [description]
/team list
/team status [name]
/team tasks [name]
/team merge [name]
/team stop <member>
/team delete [name] [--discard]
```

## 首轮强化：Completion Evidence 与懒加载 MCP

### ToolResult（向后兼容）

```python
ToolResult(
    output: str,
    is_error: bool = False,
    data: dict[str, Any] | None = None,
    preview: str | None = None,
    artifact_path: str | None = None,
    exit_code: int | None = None,
    diagnostics: tuple[str, ...] = (),
)
```

前两个位置参数保持兼容。大结果仍由上下文预算层落盘；工具可通过 `artifact_path` 主动暴露产物。

### LoopComplete 与 EvidenceBundle

```python
LoopComplete(
    stop_reason: str,
    input_tokens: int,
    output_tokens: int,
    outcome: Literal[
        "answered", "completed", "waiting_background", "verification_failed"
    ] = "answered",
    evidence: EvidenceBundle | None = None,
)
```

`EvidenceBundle` 固定包含 `changed_files / commands / tests / diagnostics / diff_stat /
unresolved / issues`。解释型请求返回 `answered` 且不强制验证；修改型请求验证失败后最多修复两次，
最终以 `verification_failed` 结束，不能投影为成功。

### MCP 生命周期

Server 状态为 `idle / connecting / connected / failed / expired`。App 与 headless 启动只加载配置；
当 ToolSearch 在本地延迟工具中找不到匹配项时才初始化 MCP。并发调用共享同一个注册锁，失败 Server
必须通过 `retry:<query>` 显式重试；`/status` 展示每个 Server 状态与最近错误。

### 权限精确规则

交互式 “Always allow” 保存 `match: exact` 与 `arguments_hash`，哈希绑定 Tool 名称和规范化后的完整
参数。参数变化必须重新审批；`/permission add` 创建的声明式规则仍使用 glob 匹配。
