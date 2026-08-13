# 02 架构：五层、文件地图、数据怎么流

## 先建立画面

把 Simple Code 想成一个**工头带着工具箱在仓库里干活**：

- 工头（LLM）只负责想下一步
- 工具箱（Tools / MCP）负责动手
- 工头每动一次，监工（权限）先看允不允许
- 笔记本（Conversation）记下「我想干什么 / 实际干成了什么」
- 笔记本太厚就摘要（Context）
- 换班时把偏好留给下一班（Memory）
- 终端是工位（TUI），不是大脑

面试一句：

> 高质量 Agent 不是超长 System Prompt，而是状态机 + 工具协议 + 权限管线 + 上下文投影 + 恢复策略。

---

## 五层（和本仓库目录对齐）

```text
交互层   app.py  commands/  permission_dialog.py
引擎层   agent.py  client.py  conversation.py  agents/  teams/
工具层   tools/  mcp/  skills/  hooks/
记忆层   memory/  context/
安全层   permissions/
```

| 层 | 干什么 | 换掉它影响谁 |
|---|---|---|
| 交互 | 展示流、Slash、确认框、取消 | 引擎继续跑；headless 可以不要 TUI |
| 引擎 | 循环、停、恢复、调模型 | 换 Web UI 也还是这套 Loop |
| 工具 | schema、执行、MCP/Skill 包装 | 新工具注册即可，Loop 不用改 |
| 记忆 | 指令、会话、压缩、自动记忆 | 压缩坏了会把引擎协议搞崩 |
| 安全 | 危险命令、沙箱、规则、模式、询问 | Prompt 里写「别 rm」不算安全 |

**为什么分层：** 每层只依赖下层稳定 Contract。换模型厂商只改 `client.py`；换界面只改 `app.py`；加 MCP 工具不改 Loop。

**分层最容易出问题的边界：**

1. 引擎 ↔ 记忆：压缩和注入时机要对齐。刚注入的记忆如果下一步被整段摘要吃掉，等于白注入。本仓库压缩成功后会 `force` 重注入环境。
2. 协作 ↔ 引擎：队友邮箱必须在**每一轮调模型之前**消费（`agent.py` 的 `_consume_mailbox`），插在工具中间会把历史顺序弄乱。

---

## 一次请求怎么走（面试画这张）

```text
你回车
  → Slash？是则本地命令，不调模型
  → 否则 Agent.run
      → 注入环境 / 项目指令 / 记忆（每会话一次，压缩后重注）
      → 大结果预算（落盘 + 预览）
      → 必要时 auto_compact
      → client.stream(历史, system, 当前可见工具)
      → TUI 收 StreamText / Thinking / ToolUse
      → 没有 tool_calls →（可选）完成验证 → LoopComplete
      → 有 tool_calls → 分批
           PreToolUse hook → 权限 → execute → PostToolUse
           结果写成 tool_result（同一 id）
      → 下一轮
```

关键文件：

| 环节 | 文件 |
|---|---|
| 入口 | `simplecode/__main__.py` `app.py` |
| 循环 | `simplecode/agent.py` `run()` |
| 协议适配 | `simplecode/client.py` |
| 历史 | `simplecode/conversation.py` |
| 工具字典 | `simplecode/tools/__init__.py` |
| 权限 | `simplecode/permissions/checker.py` |
| 压缩 | `simplecode/context/manager.py` |

---

## 四个设计原则（讲架构时收尾用）

1. **模型不可信，协议可信。** 入参必须过 Pydantic；有 `tool_use` 必须有 `tool_result`。
2. **默认保守。** 工具默认不可并发、默认会写入；安全的自己声明。
3. **能确定的事不要问模型。** 危险命令、路径越界用代码拦。
4. **给模型的是投影，不是全部历史。** UI / JSONL 可以留全量；请求里按预算裁。

---

## 和「普通后端」怎么类比

面试官能听懂这个类比：

| 后端 | Simple Code |
|---|---|
| HTTP 路由 | 工具 name + schema |
| 鉴权中间件 | PermissionChecker |
| 事务 / 外键 | tool_use_id 配对 |
| 限流 / 熔断 | max_iterations、压缩熔断、PTL 重试上限 |
| 审计日志 | JSONL 会话 |
| 插件 | MCP / Skill / Hook |

你不是在「调 ChatGPT」，你是在做一个**带副作用的工作流引擎**，执行器碰巧是 LLM。

---

## 口述 60 秒版

> 我按五层拆。交互层只消费事件，不决策。引擎层是一个循环：投影上下文、调模型、有工具就校验权限再执行、结果按 id 写回、再调。工具层统一 Contract，内置六个动作，MCP 当外部供应商延迟加载。安全层在模型外做五道门，deny 不可被后面的 allow 翻掉。记忆层管会话和压缩，压缩必须切在完整轮次上，否则 Function Calling 会炸。这样换 UI、换模型、加工具，Loop 不用重写。

下一篇：[03-agent-loop.md](03-agent-loop.md)
