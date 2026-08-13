# CH2–CH12 真实运行验收记录

> 验收日期：2026-07-18  
> 环境：Windows 11、Python 3.12、本地 CLIProxyAPI、真实 Anthropic/OpenAI 兼容接口、Context7 MCP

本记录用于区分“代码/文档看起来完成”和“能力已在真实链路运行”。测试过程中未记录或输出 API Key。

## 1. 章节证据矩阵

| 章节 | 验收能力 | 自动化证据 | 真实运行证据 | 结论 |
|---|---|---|---|---|
| CH2 | Provider、流式事件、thinking、错误分类、对话序列化 | client / conversation / Agent 测试通过 | Anthropic 返回 `LIVE_OK`；OpenAI Responses 返回 `OPENAI_LIVE_OK`；Claude 4.6 adaptive thinking 收到 thinking delta；无效 Key 被分类为 `AuthenticationError(401)` | 通过 |
| CH3 | 工具抽象、Registry、文件/Bash/搜索工具 | tools / tool_search / Agent 测试通过 | 模型自主执行 `WriteFile -> ReadFile` 并核对 `CH2_CH12_LIVE_OK` | 通过 |
| CH4 | 多轮 Agent Loop、工具结果回灌、终止条件 | 单步、多步、重试和消息拼接测试通过 | 真实模型完成“判断任务 -> 调工具 -> 读取结果 -> 最终回答”的闭环 | 通过 |
| CH5 | System Prompt、环境上下文与行为约束 | prompt / Agent 集成测试通过 | Plan 模式只读扫描并生成计划，没有修改目标代码 | 通过 |
| CH6 | Accept Edits / Plan / YOLO 权限模式 | permission / TUI 测试通过 | Accept Edits 触发权限请求；YOLO 零弹窗直接执行；Plan 仅使用只读工具与 `WritePlan` | 通过 |
| CH7 | MCP 配置、连接、延迟工具发现与调用 | MCP / ToolSearch 测试通过 | Context7 成功连接；真实模型自主执行 `ToolSearch -> resolve_library_id -> query_docs -> 最终回答` | 通过 |
| CH8 | Token 估算、大结果落盘、自动/手动压缩 | context / recovery 测试通过 | 真实 LLM 手动 compact：约 `7553 -> 510` tokens，compact Hook 同步触发 | 通过 |
| CH9 | SIMPLECODE.md、会话 JSONL、自动记忆 | memory / recovery 测试通过 | `@include` 展开成功；坏 JSONL 行跳过且不完整工具链被截断；真实 LLM 写入用户偏好和项目知识 | 通过 |
| CH10 | 命令框架、10 个内置命令、UIController、Tab 补全 | commands / TUI 测试通过 | 真实 TUI 执行 help/status/session/memory/permission/plan/do 等命令并正确刷新状态 | 通过 |
| CH11 | Skill 解析、三级加载、inline/fork、两阶段激活 | skills / Agent / commands 测试通过 | 动态项目 Skill 被热加载和 Tab 补全，真实模型按 Skill 回复 `SKILL_TUI_LIVE_OK` | 通过 |
| CH12 | 15 个 Hook 事件、条件 DSL、Action、拦截和 Agent Loop 集成 | `tests/test_hooks.py` 45 passed；配置、TUI、权限和文件事件均有覆盖 | pre-tool Hook 拒绝真实 Bash 调用，拒绝原因回灌后模型调整策略；startup/command/shutdown/compact 等事件真实触发 | 通过 |

## 2. 本轮发现并修复的问题

### Claude 4.6 thinking 参数不兼容

- **现象**：开启 `thinking: true` 后没有 `ThinkingDelta`，但普通文本正常。
- **根因**：客户端给 Claude 4.6 发送了旧式 `enabled + budget_tokens=0` 参数。
- **修复**：改用 Claude 4.6 支持的 `{"type": "adaptive"}`，并同步单元测试与 CH2 文档。
- **回归**：真实接口收到 thinking delta 和 thinking complete；全量测试继续通过。

### Textual 权限问题首帧竞态

- **现象**：权限问题刚弹出时，测试偶发读到空标题/空问题。
- **根因**：首个问题文本在 mount 后更新，自动化读取可能早于更新。
- **修复**：首个问题直接在 `compose` 阶段渲染；相关测试连续 5 次通过。

## 3. 最终质量门禁

```text
pytest:      418 passed, 1 skipped
ruff:        All checks passed
mypy:        Success: no issues found in 83 source files
compileall:  passed
uv build:    wheel + sdist built successfully
git diff --check: passed
```

唯一跳过项是当前 Windows 环境没有创建 symlink 的权限；测试会明确调用 `pytest.skip`，不影响权限路径校验的其余覆盖。

## 4. 范围边界

- CH12 的 `agent` Hook Action 仅为占位，不伪装成已实现 SubAgent。
- SubAgent、Worktree、Skill 市场/版本管理、Hook 热加载仍属于后续章节。
- 真实 Provider/MCP 验收依赖用户本地配置；自动化测试不要求联网或消耗付费额度。
