# ch07: MCP Protocol Tasks

> 任务粒度: 每个任务可独立验收。实现与路径已按当前 `main` 分支更新；验收以符号、测试和主流程调用链为准，不依赖历史行号。

## T1: 定义 `MCPServerConfig` 与 ENV 工具
- 影响文件: `mewcode/config.py`（Pydantic `MCPServerConfig`、`is_stdio`、`resolve_env_vars`、`build_child_env`）
- 依赖任务: 无
- 完成标准: `MCPServerConfig` 字段含 `name / command / args / url / headers / env / startup_timeout / tool_timeout`，`is_stdio` 用 `command is not None` 判定；两个 timeout 都做正数范围校验；`resolve_env_vars` 把 `${VAR}` 展开成 env value，缺失变量保留占位符；`build_child_env` 仅注入 `PATH`、Windows 最小运行时白名单与显式 env，不携带宿主机敏感变量。

## T2: 在 `load_config` 中反序列化 `mcp_servers`
- 影响文件: `mewcode/config.py`（`providers` / `mcp_servers` 列表模型、旧格式规范化、
  Pydantic model validator、统一 `ConfigurationError`）
- 依赖任务: T1
- 完成标准: YAML 规范格式 `providers: [{...}]` 与
  `mcp_servers: [{name: ..., command/url: ...}]` 能正确解析；活动 Provider 为第一项；
  同时兼容旧 `provider:`、根级 Provider 字段和 `mcp_servers` map。空 Provider 列表、
  重复 Provider/server name、stdio/HTTP both/neither 均产生可观察的
  `ConfigurationError`。

## T3: 实现单服务器 `MCPClient.connect` 分流
- 影响文件: `mewcode/mcp/client.py:17-65`
- 依赖任务: T1
- 完成标准: `MCPClient.connect`（client.py:29-51）根据 `config.is_stdio` 分别走 `_connect_stdio`（53-65，用 `StdioServerParameters` + `stdio_client`）或 `_connect_http`（67-84，用 `httpx.AsyncClient` + `streamable_http_client`）；连接全部通过 `AsyncExitStack` 管理；连接失败时 `_cleanup_stack` 兜底回滚。

## T4: 实现 `list_tools` / `call_tool` / `close` / `_cleanup_stack`
- 影响文件: `mewcode/mcp/client.py:86-113`
- 依赖任务: T3
- 完成标准: `list_tools` 调 `self._session.list_tools()` 返回 `list[types.Tool]`；`call_tool` 用 `tool_timeout` 包裹并透传 `CallToolResult`；`close` 置 `_alive = False` 并交还 stack；`_cleanup_stack` 静默吞掉 anyio 的 "cancel scope" `RuntimeError`，其他异常仅打 debug 日志。

## T5: 实现 `MCPToolWrapper` 适配器
- 影响文件: `mewcode/mcp/tool_wrapper.py:57-109`
- 依赖任务: T4
- 完成标准: `MCPToolWrapper.__init__` 赋值 `self.name = f"mcp_{server_name}_{tool_def.name}"`，`category = "command"`，`should_defer = True`，调 `_build_params_model` 生成 Pydantic `BaseModel`；`get_schema` 返回完整 Tool envelope，并在 `input_schema` 字段保留原始 MCP Schema；`execute` 失败时返回 `ToolResult(output="...", is_error=True)`，并把 `result.isError` 透传。

## T6: 实现 `_build_params_model` 与 `_extract_text`
- 影响文件: `mewcode/mcp/tool_wrapper.py:12-54`
- 依赖任务: T5
- 完成标准: `_build_params_model`（12-26）用 `pydantic.create_model` 动态生成 `<tool_name>Params` 模型，required 字段标 `...`、optional 字段标 `None`；`_json_type_to_python`（29-38）覆盖 string/integer/number/boolean/object/array 六类；`_extract_text`（41-54）把 `TextContent` / `ImageContent` / `EmbeddedResource` 三种 block 类型按规则拼接，无 block 时回填 `(no output)`。

## T7: 实现 `MCPManager` 调度与重连
- 影响文件: `mewcode/mcp/manager.py:13-70`
- 依赖任务: T5, T6
- 完成标准: `load_configs` 把 `list[MCPServerConfig]` 按 name 灌进 `_configs` dict；`register_all_tools` 并行 connect + list_tools，再按配置顺序注册，单个失败/超时 append 到 `errors` 不阻塞；`get_client` 支持带超时的 lazy reconnect；`shutdown` 遍历 `_clients` 调 `close()`，异常仅 debug 记录。

## T8: 暴露 `MCPManager` 出包
- 影响文件: `mewcode/mcp/__init__.py:1-5`
- 依赖任务: T7
- 完成标准: `__init__.py` 通过 `__all__ = ["MCPManager"]` 暴露，调用方写 `from mewcode.mcp import MCPManager` 即可。

## T9: 接入 Textual TUI 启动流程
- 影响文件: `mewcode/app.py:50`（import），`mewcode/app.py:514-525`（构造参数），`mewcode/app.py:537-538`（实例字段），`mewcode/app.py:810-811`（`on_mount` 派任务），`mewcode/app.py:1042-1044`（发消息前 await），`mewcode/app.py:1068-1070`（追加 system reminder），`mewcode/app.py:1496-1532`（`_init_mcp`），`mewcode/app.py:1534-1544`（`_shutdown_mcp`）
- 依赖任务: T8
- 完成标准: `MewCodeApp(config)` 从 `config.mcp_servers` 复制配置，`on_mount` 派 `asyncio.create_task(self._init_mcp())`；`_init_mcp` 实例化 `MCPManager` + `load_configs` + `register_all_tools(self.registry)`，把 server 名与可用工具列表拼成 `_mcp_instructions` 用 `add_system_reminder` 注入；用户发消息时若 task 未完成则 `await self._mcp_init_task`；`on_unmount` 调 `_shutdown_mcp` 取消 task 并执行 `manager.shutdown()`。

## T10: 端到端验证
- 影响文件: 无（仅运行验证）
- 依赖任务: T9
- 完成标准: `pytest tests/test_mcp.py -v` 全绿；在 `config.yaml` 加入 context7 server（`command: npx, args: [-y, "@upstash/context7-mcp"]`），启动 TUI，提示 LLM 调 `mcp_context7_resolve_library_id`，能看到工具命中并返回结果；TUI 顶部状态条应出现 "Connected to N MCP server(s), M tools registered" 提示。

## 进度
- [x] T1
- [x] T2
- [x] T3
- [x] T4
- [x] T5
- [x] T6
- [x] T7
- [x] T8
- [x] T9
- [ ] T10（真实 Context7 discovery / tools/call / TUI 状态及本地流式 Provider stub 的完整 Agent Loop 已验证；真实模型自主调用需用户 Provider 凭据做付费手工验收）
