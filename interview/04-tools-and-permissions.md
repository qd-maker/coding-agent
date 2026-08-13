# 04 工具与权限：能干活，但不能乱来

## 工具：能力对象，不是一个函数

普通想法：工具 = `call()`。  
本仓库：工具是一份 **Contract**（`simplecode/tools/base.py`）。

每个工具至少声明：

| 字段 | 作用 |
|---|---|
| name / description | 给模型看，决定它会不会点这把工具 |
| params_model | Pydantic，入参不对直接错误结果，不执行 |
| category | read / write / command，权限矩阵用 |
| is_concurrency_safe | 默认真：不安全。只有自己证明只读才能并行 |
| should_defer | MCP 等低频工具先不占 schema |
| execute() | 只有校验 + 权限通过后才进来 |

新加工具：写一个类，`registry.register`。Loop、权限、TUI 不用改。需要 LLM / 父 Agent 的（如 `Agent` 工具）在构造时注入依赖。

内置六个核心动作：`ReadFile WriteFile EditFile Bash Glob Grep`，覆盖程序员日常。

---

## 模型从来没有「真的调用」工具

Function Calling 长这样：

1. 请求里带上工具列表（name + JSON Schema）
2. 模型返回 `tool_use`：名字、id、参数 JSON
3. **你的进程**执行
4. 用同一个 id 回 `tool_result`
5. 再请求下一轮

面试一句：

> 协议把「模型想做什么」和「世界里发生了什么」切开。执行权永远在宿主。

一次返回三个 tool_use 怎么办？**相邻同类分批**（`partition_tool_calls`）：

- 连续只读（Read / Glob / Grep，以及白名单里的 `ls`、`git status`）→ 一批并行
- 写文件、普通 Bash → 单独串行
- 模型给的先后顺序不改，只在安全的地方加速

两个 Read 读同一文件：只读，没问题。  
又读又写同一文件：分批后读批次先结束再写，或按模型顺序切开，不会读到写到一半的文件。

---

## 文件编辑是小事务，不是 `writeFile` 包一层 Prompt

coding agent 最容易翻车：凭记忆改、覆盖你刚改的字。

本仓库 CH16：

- `EditFile` 挂了 `FileCache` 时：**没读过 / 没刚写过 → 拒绝**；读完后磁盘变了 → 拒绝，让它重读
- 写入瞬间再变 → 还是拒绝
- 精确唯一替换：找不到或多处匹配都失败
- `WriteFile` 成功会把内容记进 cache，可以接着 Edit，不必再读一次

面试讲法：

> 写文件工具要像小型事务：基于刚读到的版本改。模型没看见当前内容，就不能改。

---

## 权限：五层，deny 不能被后面的 allow 翻掉

安全不能只写在 System Prompt。模型会忘、会绕。

本仓库 `permissions/checker.py` 大致顺序：

```text
1. Plan 豁免（只读工具、计划文件）
2. 安全只读命令白名单（ls / git status…）→ 直接 allow
3. 危险命令正则（rm -rf /、curl|sh、fork bomb…）→ 只能 deny，YOLO 也翻不掉
4. 路径沙箱（读/写类，必须在项目里）
5. Plan 对其余写/命令 deny
6. YOLO / Bypass：过了 3、4 才放行
7. 用户/项目/本地 YAML 规则
8. 模式矩阵（default / acceptEdits / plan / bypass / dontAsk）
9. 还没决定 → ask（TUI 弹窗）
```

| 模式 | 读 | 写 | 命令 | 谁用 |
|---|---|---|---|---|
| Accept Edits（TUI 默认） | 放 | 放 | 问 | 日常写代码 |
| Plan | 放 | 拒（计划文件除外） | 拒 | 先方案 |
| YOLO / Bypass | 放 | 放 | 放 | 你明确要全自动；危险命令仍拦 |
| DontAsk | 放 | 拒 | 拒 | `-p` 无人值守 |
| Default | 放 | 问 | 问 | 更保守 |

**为什么不能只靠弹窗：** 一个任务几十次工具，全问会疯。前面几层把「明显能 / 明显不能」自动判掉，人只看灰色地带。

**踩过的概念坑（课程题库也强调，面试很好用）：**  
规则优先级一旦反了，本地 deny 会被项目 allow 盖掉。原则是：**越靠近当前机器/当前会话的规则，越应该能收紧或放开可配置项；但危险命令和沙箱永远不能被规则放行。** 讲的时候带上「所以我专门有单测锁优先级」。

无人值守：子 Agent 和 `run_to_completion` **不能弹窗**。ask 直接变 deny，循环继续，界面不会挂死。

---

## 口述 60 秒

> 工具是带 schema 和并发声明的对象，不是裸函数。模型只许愿，Pydantic 和权限过了才执行。读写分开并行。改文件必须基于刚读的版本。权限五层，最里面的危险命令和路径沙箱，YOLO 也跳不过。人机确认只处理灰区。这样 Agent 有手，但手伸到项目外或敲毁灭命令时会被拍掉。

下一篇：[05-context-memory.md](05-context-memory.md)
