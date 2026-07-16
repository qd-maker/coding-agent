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

最小请求配置：

```yaml
protocol: anthropic
model: claude-sonnet-4-6
base_url: https://api.anthropic.com
api_key: ${ANTHROPIC_API_KEY}
```

也接受把上述字段放在 `provider:` 下。扩展字段：

```yaml
provider:
  name: default
  protocol: anthropic
  model: claude-sonnet-4-6
  base_url: https://api.anthropic.com
  api_key: ${ANTHROPIC_API_KEY}
  thinking: true
  max_output_tokens: 64000
system_prompt: You are MewCode, a concise coding assistant.
```

`api_key` 支持明文、`${ENV_NAME}`、`$ENV_NAME` 和 `env:ENV_NAME`。环境变量不存在时视为缺失 key。

## 4. Provider Contract

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

## 5. Conversation Contract

- 写入操作按调用顺序追加，单消费者，不加锁。
- `get_messages()` 返回浅拷贝，外部不能替换内部列表。
- `serialize("anthropic")` 保留 thinking signature、tool input 和 tool result error 标记。
- `serialize("openai")` 将工具调用转换为 Responses API 顶层 input items。
- 未知协议抛出 `ValueError("Unknown protocol: ...")`。

## 6. Tool Contract

### 工具定义

每个工具继承统一的 `Tool` 抽象类，并提供名称、描述、Pydantic 参数模型、类别、并发安全标记和异步执行方法。工具执行返回：

```text
ToolResult(output: str, is_error: bool = false)
```

参数校验失败、未知工具、禁用工具、超时和执行异常均转换成 `is_error=true` 的结果回灌模型；`asyncio.CancelledError` 不被吞掉。

Agent 将工具结果写回会话后自动请求下一步，直到模型返回最终文本；每个用户回合最多运行 50 轮。达到上限时返回 `ErrorEvent`。TUI 仅展示工具摘要和耗时；超长结果保存到 `.mewcode/sessions/`，预览和文件位置进入对话历史。

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

## 7. Prompt Contract

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

## 8. Permission Contract

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
