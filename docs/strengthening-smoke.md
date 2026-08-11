# MewCode 首轮强化实机冒烟

验证日期：2026-07-18  
Provider：本地 CLIProxyAPI（Anthropic 协议）  
MCP：Context7 stdio

| 场景 | 结果 | 耗时 | Token | 关键证据 |
|---|---|---:|---:|---|
| 普通解释 | `answered` | 22.47s | 3123 in / 48 out | MCP `idle → idle`，首条消息未发生连接 |
| Write → Read → compileall | `completed` | 18.22s | 10092 in / 435 out | WriteFile、ReadFile、Bash exit 0，Completion Gate `started → passed` |
| ToolSearch → Context7 | `completed` | 41.75s | 22700 in / 563 out | MCP `idle → connected`，注册 2 个工具并连续调用 resolve/query |

文件链路生成 `VALUE = 42`，并执行 `python -m compileall -q smoke_evidence.py`；结构化 Evidence
包含目标文件、测试命令、exit code、诊断和 unresolved。冒烟结束后已清理临时源码。

Context7 链路实际顺序：

```text
ToolSearch
  → mcp_context7_resolve_library_id
  → mcp_context7_query_docs
  → APIRouter 中文总结
```

离线门禁另见全量测试：`514 passed, 1 skipped`，Ruff、mypy、compileall 和 `uv build` 均通过。

