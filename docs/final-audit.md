# Simple Code CH2–CH15 最终审计报告

审计日期：2026-07-18

## 1. 结论

**Simple Code 已达到“可演示、可安装、可继续迭代的终端 Coding Agent MVP”标准。**

CH2–CH15 的核心能力不是只存在于独立模块中，而是已经接入 Provider → Conversation → Agent
Loop → Tool / Permission / Hook → Context / Session → SubAgent / Worktree / Team 主链。最终门禁为
`514 passed, 1 skipped`，Ruff、mypy、compileall、sdist 与 wheel 构建全部通过；真实付费 Provider
完成了普通回答、文件写入回读以及自主 `ToolSearch → Context7 MCP → 最终回答` 三条链路。

该结论针对单机 MVP 和面试演示，不等同于跨主机调度、长期稳定性或企业级 SLA 已完成。

## 2. 逐章完整性矩阵

| 章节 | 能力 | 主流程证据 | 结论 |
|---|---|---|:---:|
| CH2 | Anthropic/OpenAI Provider、流式事件、Thinking、Conversation | `client.py` 统一事件进入 `Agent.run`；Provider/序列化测试与真实付费流通过 | ✅ |
| CH3 | Tool Contract、注册、Pydantic Schema、文件/搜索/Bash | `ToolRegistry` 接入 Agent 执行器；真实 WriteFile → ReadFile 回读成功 | ✅ |
| CH4 | Agent Loop、工具结果回灌、停止/恢复、TUI 事件 | 多轮工具、并发批次、max_tokens、取消和 TUI 测试通过 | ✅ |
| CH5 | System Prompt Builder、环境与 Plan/Coordinator 分区 | 每轮 `_system_prompt()` 重建；Prompt、compact 后重注入测试通过 | ✅ |
| CH6 | Accept Edits / Plan / YOLO、规则与路径沙箱 | 模式切换、HITL、危险命令、路径越界和无弹窗 YOLO 测试通过 | ✅ |
| CH7 | stdio/HTTP MCP、ToolSearch 延迟发现 | Context7 真实启动；付费模型自主连续调用 3 个工具并完成回答 | ✅ |
| CH8 | Token 估算、大结果落盘、Auto-Compact、`/compact` | 两层压缩在每轮 Provider 调用前执行，压缩结果回写外层历史 | ✅ |
| CH9 | SIMPLECODE.md、JSONL 会话、恢复、双层自动记忆 | TUI 与 headless 均落盘；坏行/断链/时间跨度/记忆测试通过 | ✅ |
| CH10 | Slash Command、UIController、补全、状态栏 | 命令先于 Agent 分流；内置/动态命令和 Tab 补全测试通过 | ✅ |
| CH11 | Skill 两阶段加载、inline/fork、目录能力包 | Catalog 摘要注入、LoadSkill 激活、白名单及热加载测试通过 | ✅ |
| CH12 | 15 个 Hook 事件、条件 DSL、Action、拦截 | startup/turn/message/tool/shutdown 接入，reject 回灌与配置校验通过 | ✅ |
| CH13 | 定义式/Fork SubAgent、后台任务、Trace | RunToCompletion、工具多层过滤、通知、取消和 Token 汇总测试通过 | ✅ |
| CH14 | Git Worktree 隔离、保护、恢复和清理 | 生命周期、dirty/unpushed fail-closed、SubAgent 自动隔离测试通过 | ✅ |
| CH15 | AgentTeam、共享任务、邮箱、后端、恢复与合并 | in-process 完整链、pane 命令构造、依赖任务、消息、恢复和事务合并通过 | ✅ |

## 3. 真实付费 API 验收

### 3.1 Provider 与 headless

- 普通解释请求总耗时 `22.47s`，usage `3123 in / 48 out`，outcome 为 `answered`。
- Context7 在请求前后均保持 `idle`，证明 headless 首条普通消息不触发 MCP 冷启动。

### 3.2 文件工具闭环

- 模型创建 `smoke_evidence.py`，写入 `VALUE = 42`，调用 ReadFile 回读，再执行 compileall。
- Bash exit code 为 `0`，Completion Gate 为 `started → passed`，outcome 为 `completed`；总耗时
  `18.22s`，usage `10092 in / 435 out`。

### 3.3 MCP 自主调用

```text
ToolSearch
  → mcp_context7_resolve_library_id
  → mcp_context7_query_docs
  → FastAPI APIRouter 中文总结
```

- MCP 链耗时 `41.75s`。
- 累计 usage `22700 in / 563 out`；这是多轮 Provider 请求和工具结果重复进入上下文的累计值。
- 三个工具结果均写回 Conversation 后再进入下一轮，不是伪造的最终文本。

## 4. 本轮发现并修复的问题

### P0：`-p` headless 生命周期不完整

原实现直接调用 `run_to_completion`，未执行 MCP/startup/shutdown Hook、会话持久化或资源关闭，
会留下 `message_count=0` 的空会话。现已增加统一 headless 启停：

- 运行前只加载 MCP 配置；ToolSearch 首次确需外部工具时再连接并注入工具摘要；
- 执行 startup/shutdown Hook；
- `finally` 中持久化有效消息并关闭 Session、Task、Team、Hook、MCP；
- 无 prompt 或未知 Agent 的提前退出不再污染会话列表；
- headless 关闭不额外发起强制记忆请求，避免一次性 CLI 隐性增加成本和延迟。

### P1：MCP 可能无限等待且多个服务器串行启动

原实现对 connect/list_tools/call_tool 都没有超时，多服务器依次连接。现已改为：

- 所有 MCP Server 并行连接；
- `startup_timeout` 同时约束 connect 与 list_tools，默认 `20s`；
- `tool_timeout` 约束单次工具调用，默认 `120s`；
- 单服务失败或超时只降级该服务，不阻断其他服务；
- 连接取消时回收部分创建的 transport，避免子进程/会话泄漏。

两台各延迟 1 秒、超时 0.02 秒的模拟服务器总耗时小于 0.08 秒，证明不是串行累加。

### P1：初始化失败产生空会话与原始 traceback

- Session 创建移到所有 fail-fast Skill/Agent/Worktree 校验之后。
- CLI 对 OSError/RuntimeError/ValueError 输出简洁 `Startup error` 并返回退出码 `2`。

## 5. 性能画像

| 项目 | 结果 | 说明 |
|---|---:|---|
| App 本地构造 | `1.705s` | Provider Client、工具、命令、Agent/Skill/Team catalog 全部装配 |
| System Prompt | 约 `723 tokens` | 2,892 字符 |
| 首轮核心工具 Schema | 约 `1,428 tokens` | 11 个非延迟工具，5,712 字符 |
| 普通请求 MCP 开销 | `0s` | 普通请求保持 `idle → idle`，不再等待外部 `npx` |
| Context7 按需链路 | `41.75s` | ToolSearch 后才冷启动并完成 resolve/query |
| 普通完整模型轮次 | `9.667s` | 含完整 Coding Agent Prompt 与工具 Schema |
| 全量测试 | `75.06s` | 512 passed / 1 skipped |

当前成本主要不在 Python Agent Loop，而在外部 MCP 冷启动、模型思考时间，以及多轮工具调用时
重复进入 Provider 的上下文。Prompt cache、延迟发现和大结果落盘已经缓解后两项；如果高频使用
Context7，可把 npm 包预装为本地命令，避免每次由 `npx -y` 做包解析。

## 6. 质量门禁

```text
compileall: pass
ruff:       pass
mypy:       pass（120 source files）
pytest:     514 passed, 1 skipped in 75.06s
build:      simplecode-0.1.0.tar.gz + simplecode-0.1.0-py3-none-any.whl
```

唯一跳过项是当前 Windows 环境没有符号链接创建权限时的 symlink escape 用例；非 symlink 的
路径越界、真实路径校验和 Worktree 保护用例均执行。

## 7. 已知边界与下一步

1. Windows 当前环境无法真实启动 tmux/iTerm2 pane；已完整验证跨平台 in-process Team，pane
   后端验证了检测、命令构造和失败不静默降级。目标系统仍应补一次真实 pane 冒烟。
2. 自动记忆的主触发是每 5 个完成回合；TUI 退出时的最后增量刷新为 2 秒 best-effort，在慢
   Provider 下可能来不及完成。后续可改为独立持久队列或低成本专用模型。
3. Provider 5xx 当前 fail-fast，没有通用指数退避；生产化应增加有限重试、熔断和请求 ID 日志。
4. 尚未做数小时 soak test、数十并发 SubAgent/Team 压测、跨主机队列和分布式锁。
5. 仓库尚无开源许可证；对外发布前需要明确授权方式。

以上边界不影响当前单机 Coding Agent Demo 的核心闭环，但决定了它与生产级产品之间的距离。

