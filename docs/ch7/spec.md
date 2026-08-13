# ch07: MCP Protocol Spec

## 1. 背景

外部能力（Context7、GitHub、Slack、数据库等）通过 Model Context Protocol（MCP）暴露给 Agent。如果没有 MCP 客户端实现，Simple Code 就只能依赖内置工具，无法接入生态里已有的几百个 MCP server，等于砍掉一大块工具生态。MCP 规范定义了 JSON-RPC 2.0 之上的握手 → 工具发现 → 工具调用三阶段会话，需要本章把这三阶段、两种传输（stdio / Streamable HTTP）以及到 `Tool` 抽象基类的适配器实现，并接到 Textual TUI 的启动流程里。Python 版基于官方 `mcp` SDK（`ClientSession`、`stdio_client`、`streamable_http_client`），传输生命周期用 `AsyncExitStack` 统一收尾。

## 2. 目标

交付一个能在 Simple Code 启动时按配置批量连接外部 MCP server、把每个 server 暴露的工具注册到全局 `ToolRegistry` 的异步客户端。具体能力：单服务器 `MCPClient` 封装（`connect` / `list_tools` / `call_tool` / `close`）；多服务器 `MCPManager` 封装（`load_configs` / `register_all_tools` / `get_client` / `shutdown`）；`MCPToolWrapper` 把每个 MCP tool 适配到 Simple Code 的 `Tool` 抽象基类，并用 `pydantic.create_model` 动态生成参数模型；工具名按 `mcp_<server>_<tool>` 命名。最终效果是用户在 Textual TUI 里看到 MCP server 的工具与内置工具并列，能直接被 LLM 调用。

## 3. 功能需求

- F0: `config.yaml` 规范格式使用 `providers: list[ProviderConfig]` 与
  `mcp_servers: list[MCPServerConfig]`；列表元素显式包含 `name`。当前 TUI 使用第一项
  Provider，加载器继续兼容旧的 `provider:` 单对象、根级 Provider 字段和
  `mcp_servers` mapping。
- F1: `MCPServerConfig` 同时支持 stdio（`command + args + env`）和 HTTP（`url + headers`）两种传输，`is_stdio` 属性通过 `command is not None` 区分；`startup_timeout` 与 `tool_timeout` 分别约束启动阶段和单次工具调用。
- F2: HTTP 传输用 `mcp.client.streamable_http.streamable_http_client` 建立 Streamable HTTP 会话，外部 `httpx.AsyncClient` 注入 header。
- F3: stdio 子进程通过 `StdioServerParameters` 启动，环境用 `build_child_env` 白名单，仅保留 `PATH`、Windows 最小运行时变量与显式 `env`，避免泄露宿主机 API key。
- F4: HTTP 请求头通过 `resolve_env_vars` 在客户端层做 `${VAR}` 展开，方便从 ENV 取 API key。
- F5: 单服务器客户端 `MCPClient.connect` → `list_tools` → `call_tool` → `close` 四阶段，所有调用复用同一个 `ClientSession`，整套生命周期挂在 `AsyncExitStack` 上。
- F6: 多服务器连接 `MCPManager.register_all_tools` 并行执行 connect + list_tools，再按配置顺序确定性注册；单个失败或超时只 append 到 `errors` 列表，不阻塞其他 server。
- F7: 工具名按 `mcp_<server>_<tool>` 命名；远端 tool name 中非 ASCII 字母数字下划线的字符规范化为 `_`，原始名称仍用于 MCP `tools/call`，避免 Provider 名称校验失败。
- F8: `MCPToolWrapper.execute` 把 MCP 返回的 `TextContent / ImageContent / EmbeddedResource` 块按规则拼成字符串，把 `isError` 透传到 `ToolResult.is_error`，无输出时回填 `(no output)`。
- F9: `MCPToolWrapper` 用 `pydantic.create_model` 把 MCP 的 `inputSchema` 动态翻译成 `BaseModel`，作为 `params_model` 供工具调度层使用；`get_schema` 保持 `{name, description, input_schema}` envelope，其中 `input_schema` 直接使用原始 MCP Schema，避免 pydantic 往返转换破坏语义。
- F10: Textual TUI 启动时走 `asyncio.create_task(self._init_mcp())` 异步连接，连接结果回到主线程注册到 registry；用户按 enter 发消息前若 task 未完成，则等待 task 完成再发送。

## 4. 非功能需求

- N1: 连接是异步的（`asyncio.create_task` 派生），不阻塞 TUI 启动；连接中显示 "Waiting for MCP servers to connect..." 占位。
- N2: 单个 server 连接失败要打 `logger.warning` 并追加到 `errors` 列表，其他 server 继续连。
- N3: 配置中的 server 名只允许 ASCII 字母数字下划线；远端 tool 名在 wrapper 边界确定性规范化，规范化冲突使该 server 注册失败，避免覆盖已有工具。
- N4: 复用官方 `mcp` Python SDK，不要手写 JSON-RPC 帧格式或 stdio 流解码。
- N5: `MCPManager.shutdown` 必须幂等，遍历 `self._clients` 调每个 `client.close()`，异常仅记录日志；`_cleanup_stack` 对 anyio 的 "cancel scope" RuntimeError 做静默吞没（这是已知的 SDK shutdown race）。
- N6: 进程退出时 Textual 的 `_shutdown_mcp` 先取消 `_mcp_init_task` 再调 `manager.shutdown`，保证未完成的连接任务被回收。
- N7: `providers` 至少包含一个元素；Provider name 与 MCP server name 分别保持唯一，
  避免活动 Provider 或 manager 字典覆盖产生歧义。
- N8: connect/list_tools 默认 20 秒超时，tools/call 默认 120 秒超时；外层取消必须回收已部分建立的 transport。

## 5. 设计概要

- 核心数据结构（Python 类型）:
  - `MCPServerConfig`（`simplecode/config.py`，Pydantic model）：承载 `name / command / args / url / headers / env`，并校验传输二选一与名称字符集，提供 `is_stdio` property。
  - `MCPClient`（`simplecode/mcp/client.py:17`）：单 server 的会话句柄，持有 `config / _session / _stack / _alive`。
  - `MCPManager`（`simplecode/mcp/manager.py:13`）：多 server 调度，持有 `_configs / _clients` 两张 dict。
  - `MCPToolWrapper`（`simplecode/mcp/tool_wrapper.py:57`）：把 MCP tool 适配到 `Tool` 抽象基类，动态生成 `params_model`。
- 主流程（调用链）:
  - `simplecode/__main__.py` 启动 `SimpleCodeApp(config)`；App 从完整 `AppConfig` 读取 `config.mcp_servers`。
  - `simplecode/app.py:810-811` `on_mount` 在 `self._mcp_server_configs` 非空时 `asyncio.create_task(self._init_mcp())`。
  - `_init_mcp`（`app.py:1496-1532`）实例化 `MCPManager`，`load_configs` + `register_all_tools(self.registry)`，把每个 server 的 tool 包成 `MCPToolWrapper` 注册。
  - 对每个 server `MCPClient(config).connect()`：按 `is_stdio` 分流到 `_connect_stdio` 或 `_connect_http`，握手得到 `ClientSession`，把 transport 和 session 都丢进 `AsyncExitStack`。
  - LLM 调用工具时按 `mcp_<server>_<tool>` 找到 wrapper，`execute` 走 session 上的 `call_tool`，把 `inputSchema` 校验后的 `BaseModel.model_dump(exclude_none=True)` 作为参数。
- 与其他模块的交互:
  - 依赖 `simplecode/tools`（注册到 `ToolRegistry`、继承 `Tool` 基类）。
  - 依赖官方 `mcp` Python SDK（`ClientSession` / `stdio_client` / `streamable_http_client` / `types`）。
  - 依赖 `httpx.AsyncClient` 作为 HTTP transport 的底层连接池。
  - 被 `simplecode/app.py`（Textual TUI 主类）在启动流程中调用。
  - 依赖 `simplecode/config.py` 提供 `MCPServerConfig` 反序列化目标及 `resolve_env_vars / build_child_env` 工具。

## 6. Out of Scope

- OAuth / 鉴权刷新：只做静态 header `${VAR}` 注入，不实现 OAuth step-up 401 处理。
- 连接缓存：每次启动重新连接，不做跨进程缓存或持久化 session。
- IDE 集成（双向 SSE / WebSocket / 进程内 transport）。
- MCP `resources / prompts / sampling` 三种非 tool 能力：只暴露 `tools/list` + `tools/call`；`EmbeddedResource` 在 wrapper 里仅做文本透传。
- 服务器健康检查与自动重连：当前实现仅在工具调用时 lazy 重连（`tool_wrapper.py:88-95`），不做后台 ping/heartbeat。
- 复杂工具名映射策略：当前仅做确定性的非法字符→下划线规范化与冲突拒绝，不维护跨启动别名表。

## 7. 完成定义

见 [checklist.md](checklist.md)，所有条目勾上即完成。
