# ch07: MCP Protocol Checklist

> 验收基于当前 `main` 分支；以符号、自动测试和真实调用链为准，不依赖历史分支行号。

## 1. 实现完整性

- [x] `config.yaml` 规范格式为 `providers` 列表与 `mcp_servers` 列表，元素显式包含
  `name`；当前 TUI 使用第一项 Provider。
- [x] 旧 `provider:` 单对象、根级 Provider 字段和 `mcp_servers` mapping 仍可加载。
- [x] `providers` 非空且 Provider/server name 分别唯一；不允许静默覆盖。
- [x] `MCPServerConfig` 在 `mewcode/config.py` 实现，字段含 `name / command / args / url / headers / env / startup_timeout / tool_timeout`，`is_stdio == (command is not None)`。
- [x] `command` 与 `url` 严格二选一；错误信息分别包含 `cannot have both` / `must have either`。
- [x] YAML `mcp_servers` 列表直接解析到 `AppConfig.mcp_servers`；兼容 mapping 的 key 会
  注入为 server name。
- [x] `resolve_env_vars` 支持一个或多个 `${VAR}`，变量缺失时保留原占位符。
- [x] `build_child_env` 只保留 `PATH`、Windows 最小运行时白名单（`SYSTEMROOT / COMSPEC / PATHEXT`）和显式 `env`，不会继承宿主 API key。
- [x] `MCPClient` 使用官方 SDK 的 `ClientSession / stdio_client / streamable_http_client`，并由 `AsyncExitStack` 管理 transport、HTTP client 和 session。
- [x] stdio command 通过 `shutil.which` 解析，Windows 上 `command: npx` 可定位到 `npx.cmd`。
- [x] `connect()` 完成 `ClientSession.initialize()`；失败时回滚资源。
- [x] `list_tools()` 返回 `response.tools`；`call_tool()` 在 `tool_timeout` 内透传 `CallToolResult`。
- [x] `close()` 与 `MCPManager.shutdown()` 幂等；已知 anyio cancel-scope RuntimeError 只写 debug。
- [x] `MCPManager.register_all_tools()` 并行连接 server、按配置顺序注册；单 server 失败或超时不会阻塞其他 server。
- [x] 单 server 的 wrappers 会先完整构造并检查冲突，再写入 registry，避免可预见的部分注册。
- [x] `get_client()` 支持首次 lazy connect 与 dead client 重建。
- [x] `MCPToolWrapper` 继承 `Tool`，设置 `category="command"`、`should_defer=True`。
- [x] wrapper 名为 `mcp_<server>_<tool>`；远端 tool 名中的非法字符规范化为 `_`，原始名称仍用于 `tools/call`。
- [x] 规范化后的重复名或与现有 registry 冲突时，整个 server 注册失败并进入 errors。
- [x] `get_schema()` 返回 `{name, description, input_schema}`，其中 `input_schema` 保留原始 MCP JSON Schema。
- [x] `_build_params_model()` 覆盖 string / integer / number / boolean / object / array，并处理常见 nullable scalar。
- [x] `_extract_text()` 处理 `TextContent / ImageContent / EmbeddedResource`；空内容返回 `(no output)`。
- [x] 无 content 但有 `structuredContent` 时序列化为 JSON，避免丢失有效结果。
- [x] wrapper 通过 manager 按 server name 获取 client，重连后不会继续引用旧 client。
- [x] 连接/调用异常返回 `ToolResult(is_error=True)`；`CancelledError` 继续传播；`result.isError` 原样映射。

## 2. TUI 接入完整性

- [x] `MewCodeApp` 从 `AppConfig.mcp_servers` 保存配置。
- [x] `on_mount` 使用 `asyncio.create_task(self._init_mcp())`，首屏不被连接过程阻塞。
- [x] 连接期间显示 `Waiting for MCP servers to connect...`。
- [x] `_init_mcp` 调用 `load_configs` 与 `register_all_tools(self.registry)`，不是死代码。
- [x] 第一条普通消息在 MCP task 未完成时先 await；本地 `/plan /do /mode` 命令不受影响。
- [x] 成功后显示 `Connected to N MCP server(s), M tools registered`，Provider 流开始后仍保留 MCP 摘要。
- [x] MCP server/tool 列表通过 `ConversationManager.add_system_reminder` 只注入一次。
- [x] reminder 明确要求先使用 `ToolSearch` 发现 deferred MCP 工具。
- [x] `on_unmount` 调 `_shutdown_mcp`，先取消初始化 task，再关闭 manager 中所有 client。

## 3. 编译与自动测试

- [x] `uv run python -m compileall -q mewcode tests` 通过。
- [x] `uv run ruff check mewcode tests` 通过。
- [x] `uv run mypy mewcode` 通过。
- [x] `tests/test_mcp.py` 超过 14 个离线测试，覆盖配置、ENV、client、wrapper、内容提取、manager 部分失败与冲突原子性。
- [x] `tests/test_tui.py` 覆盖后台初始化、首消息 barrier、一次性 reminder、状态显示和退出 shutdown。
- [x] `uv run pytest -q` 全量回归通过（具体数量见 README 的当前回归结果）。

## 4. Context7 端到端

- [x] Windows 最小子进程环境可运行 `npx --version`，且不携带 `ANTHROPIC_API_KEY`。
- [x] 真实启动 `@upstash/context7-mcp@latest`，成功注册 `mcp_context7_resolve_library_id` 与 `mcp_context7_query_docs`。
- [x] 通过 `ToolRegistry.execute()` 调用真实 `resolve-library-id`，返回 Next.js 的 Context7 library ID 结果且 `is_error=False`。
- [x] Textual `run_test` 使用真实 Context7 初始化，状态显示 `Connected to 1 MCP server(s), 2 tools registered`。
- [x] 通过 PTY 启动真实 CLI/TUI，并使用本地流式 Provider stub 驱动完整 `ToolSearch → Context7 tools/call → 最终回答`；3 次 Provider 请求完成，应用退出码 `0`，无新增 `node.exe` 残留。
- [x] 2026-07-18 使用真实付费 Provider 完成模型自主 `ToolSearch → mcp_context7_resolve_library_id → mcp_context7_query_docs → 最终回答`；输出以 `[MCP_LIVE_OK]` 收尾。

## 5. 文档与交付

- [x] `spec.md / tasks.md / checklist.md` 与当前 `main` 实现保持一致。
- [x] `docs/api-contract.md`、`mewcode.yaml.example`、README 和章节索引已同步 MCP Contract。
- [ ] commit 信息标注 `ch07`（由用户审阅后提交）。
