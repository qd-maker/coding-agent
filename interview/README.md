# Simple Code 面试准备总稿

这份材料只服务一件事：**让你能把本仓库讲清楚，并能扛住追问。**

它对照了两份来源：

1. **本仓库真实代码**（`simplecode/`，CH2–CH16）
2. **课程 `agent-doc-tech` 第 16–17 章**的叙事和题库结构

课程教「怎么讲一个 Coding Agent」；这里改写成「怎么讲**你这个仓库**」。数字、文件名、模式名，都以本仓库为准。讲不出证据的句子，不要上简历。

---

## 你要达成的三件事

面试官听项目，其实在听：

| 他想听什么 | 你要准备什么 | 对应文件 |
|---|---|---|
| 你做了什么产品 | 30 秒画面 + 3 分钟成长故事 | [01](01-project-story.md) |
| 你怎么想的 | 分层、取舍、为什么不是另一种方案 | [02](02-architecture.md) 起 |
| 你是不是真做过 | 踩坑、边界、源码位置 | [08](08-qa-bank.md) [09](09-hard-questions.md) |

岗位按 **全栈 / AI 应用工程师（偏产品落地）** 准备：少背框架名词，多讲「为什么这样拆、风险在哪、成本怎么控、下一步怎么迭代」。

---

## 怎么用（建议 7 天）

不要从题库第一题开始刷。顺序是：**先能讲故事 → 再吃透骨架 → 再练口述**。

| 天 | 读什么 | 当天必须能口述 |
|---|---|---|
| Day 1 | 本 README + [01](01-project-story.md) + [02](02-architecture.md) | 30 秒介绍；五层各一句话；画一张数据流 |
| Day 2 | [03](03-agent-loop.md) + [04](04-tools-and-permissions.md) | Loop 一轮顺序；工具怎么校验；五层权限短路 |
| Day 3 | [05](05-context-memory.md) + [06](06-mcp-skills-hooks.md) | 两层压缩；MCP 为什么延迟加载；Skill ≠ 工具 |
| Day 4 | [07](07-subagent-teams.md) | 为什么要子 Agent；Worktree 解决什么；邮箱为什么不是 RPC |
| Day 5 | [08](08-qa-bank.md) 题 1–20 | 每题自己讲一遍，不看答案 |
| Day 6 | [08](08-qa-bank.md) 题 21–35 + [09](09-hard-questions.md) | 「照抄 Claude Code？」；局限；如果重做 |
| Day 7 | [10](10-resume-and-demo.md) + 对着墙讲 3 遍 | 简历 4 条 bullet；一个 2 分钟演示脚本 |

时间不够时，**只保这三块**：01 故事、03 Loop、08 题库前 15 题。课程原话也是：骨架 + 介绍，够撑一场面试。

---

## 目录

| 文件 | 内容 |
|---|---|
| [01-project-story.md](01-project-story.md) | 产品一句话、30 秒 / 3 分钟稿、动机、不要踩的坑 |
| [02-architecture.md](02-architecture.md) | 五层图、模块文件地图、数据怎么流 |
| [03-agent-loop.md](03-agent-loop.md) | 循环、停止条件、配对、恢复、完成门 |
| [04-tools-and-permissions.md](04-tools-and-permissions.md) | 工具协议、文件事务、并发、五层权限 |
| [05-context-memory.md](05-context-memory.md) | Token、落盘、摘要、记忆、会话 |
| [06-mcp-skills-hooks.md](06-mcp-skills-hooks.md) | MCP / Skill / Hook / Slash 各管一层 |
| [07-subagent-teams.md](07-subagent-teams.md) | 子 Agent、Worktree、Team、邮箱 |
| [08-qa-bank.md](08-qa-bank.md) | 高频题 + 按本仓库改过的参考口述 |
| [09-hard-questions.md](09-hard-questions.md) | 尖锐追问、差距、重做清单 |
| [10-resume-and-demo.md](10-resume-and-demo.md) | 简历模板、演示脚本、诚实口径 |

---

## 本仓库一句话地图

```text
用户打字
  → app.py（TUI / Slash / 权限弹窗）
  → agent.py（while 循环）
  → client.py（Anthropic / OpenAI 流式事件）
  → conversation.py（历史 + 序列化）
  → tools/（校验后执行）
  → permissions/（危险命令 / 沙箱 / 规则 / 模式 / 询问）
  → context/（大结果落盘 + 自动摘要）
  → memory/（SIMPLECODE.md + JSONL + memories.md）
  → mcp/ + skills/ + hooks/ + agents/ + teams/ + worktree/
```

四要素（课程第 1 章，面试必说）：

- **LLM**：决策，不直接碰磁盘
- **工具**：动手
- **循环**：做完一步看结果，再决定下一步
- **反馈**：工具结果、测试、报错驱动下一轮

安全、记忆、MCP、多 Agent **不是四要素**，是「敢不敢用、好不好用、能不能变大」。面试这样分，比把所有功能平铺更清楚。

---

## 铁律

1. **只提你能被追问两层的东西。** 提了「85% Token 下降」就要能讲实验怎么测；测过再写，没测过就说「延迟加载是为了不被工具 schema 挤爆窗口」。
2. **承认参考 Claude Code。** 价值在独立实现和取舍，不在假装首创。
3. **每个技术名词连一个决策。** 不是「我用了 JSONL」，而是「会话是追加写，JSONL 崩溃好恢复、还能 grep」。
4. **看代码对答案。** 文件路径写在各章里，背之前打开对应文件扫一眼。
5. **口述五遍。** 看懂 ≠ 能说。课程第 17 章这句话是对的。

准备好了从 [01-project-story.md](01-project-story.md) 开始。
