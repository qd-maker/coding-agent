# MewCode

**一个从零搭建的终端 Coding Agent MVP。**

MewCode 使用 Python + Textual 实现，将模型流式输出、结构化工具调用、Agent Loop、
上下文回灌、System Prompt、权限控制和跨会话持久化串成一条可运行、可观察、可测试的执行链路。

> 当前完成并验收的是 **CH2–CH15**：Provider 与对话管理、工具系统、Agent Loop、
> System Prompt、权限系统、MCP 外部工具接入、两层上下文管理，以及项目指令、会话存档和
> 自动记忆、统一 Slash Command、两阶段 Skill、Hook 生命周期自动化，以及可定义、可 Fork、
> 可后台运行的 SubAgent、Git Worktree 隔离执行，以及可持久化、可恢复、可合并的 AgentTeam。

## 界面预览

### 终端原生 TUI

启动后可以看到当前模型、工作目录、连接状态和会话状态；输入、思考、工具调用和结果都在
同一个终端事件流中展示。

![MewCode TUI 入口界面](docs/assets/tui-overview.png)

### YOLO：连续执行，无确认弹窗

YOLO 模式下，Agent 可以连续完成写文件、回读验证等操作，不进入普通权限确认流程；底栏以
红色常驻提示当前模式。

![YOLO 模式下自动写入并回读文件](docs/assets/yolo-mode.png)

### Plan：先理解项目，再输出实施计划

Plan 模式只开放只读工具和固定路径的 `WritePlan`。Agent 会先使用 `Glob`、`Grep`、
`ReadFile` 理解仓库，再把实施计划写入独立计划文件，不直接修改业务代码。

![Plan 模式读取项目结构和关键文件](docs/assets/plan-mode-analysis.png)

![Plan 模式保存电商系统实施计划](docs/assets/plan-mode-result.png)

### Accept Edits：文件编辑直接执行，命令按需确认

Accept Edits 是默认执行模式。文件写入和编辑可以直接完成；删除文件等 Bash 操作会显示内联
权限选择，用户可以单次允许、按模式永久允许或拒绝。

![Accept Edits 模式下的 Bash 删除权限确认](docs/assets/accept-edits-permission.png)

## 已实现能力

| 模块 | 状态 | 当前实现 |
|---|:---:|---|
| Provider 与流式对话 | ✅ | Anthropic Messages API、OpenAI Responses API、统一异步事件、Extended Thinking、多轮对话 |
| 工具系统 | ✅ | 统一 `Tool` Contract、Pydantic 参数校验、内置文件/搜索/Bash 工具、结构化错误 |
| Agent Loop | ✅ | 工具调用收集、执行、结果回灌、自动进入下一轮、串并行批次、最大轮次与取消 |
| 两层上下文管理 | ✅ | 本地 Token 估算、大结果落盘与预览、自动结构化摘要、恢复附件、`/compact` 前后对比 |
| System Prompt | ✅ | 分区 Prompt Builder、环境上下文、普通/协调/Plan 身份约束、动态 reminder |
| 权限系统 | ✅ | 危险命令检测、路径沙箱、三层 YAML 规则、HITL、Plan 豁免、三种 TUI 模式 |
| MCP Protocol | ✅ | stdio / Streamable HTTP、并行连接、启动/调用超时、部分失败隔离、动态 Tool Wrapper、ToolSearch 延迟发现 |
| 指令、会话与自动记忆 | ✅ | 三层 `MEWCODE.md`、安全 `@include`、崩溃安全 JSONL、链路校验、双层 `memories.md`、`/session`、`/memory` |
| Slash Command | ✅ | Registry/Parser/UIController、内置与动态命令、命令优先拦截、Tab 补全、状态栏提示 |
| Skill 系统 | ✅ | YAML+Markdown、三级覆盖、热加载、inline/fork、LoadSkill、工具白名单、目录能力包、动态短命令 |
| Hook 系统 | ✅ | 15 个生命周期事件、条件 DSL、command/prompt/http、once/async/timeout、pre-tool 拦截与 YAML 校验 |
| SubAgent 系统 | ✅ | 四级 Agent 定义、统一 Agent 工具、上下文 Fork、RunToCompletion、多层工具过滤、后台任务、Trace 与 Slash Command |
| Git Worktree 隔离 | ✅ | 安全命名、完整生命周期、dirty/unpushed 保护、崩溃恢复、过期清理、SubAgent 自动隔离、`/worktree` |
| AgentTeam | ✅ | Lead/成员、协程与 pane 后端、共享任务、磁盘邮箱、空闲恢复、事务合并、纯调度双锁与 `/team` |

## Agent 如何工作

```mermaid
flowchart LR
    U["User / Textual TUI"] --> C["ConversationManager"]
    C --> A["Agent Loop"]
    A --> P["Provider Adapter"]
    P -->|"Text / Thinking / ToolCall"| A
    A --> R["ToolRegistry"]
    R --> PC["PermissionChecker"]
    PC -->|"allow"| T["Built-in Tools"]
    PC -->|"ask"| H["Inline HITL"]
    PC -->|"deny"| A
    H -->|"PermissionResponse"| A
    T -->|"ToolResult"| C
    C -->|"next model turn"| A
    C --> S["JSONL Session Archive"]
    A --> M["Automatic Memory Extractor"]
    M --> C
    A --> W["Git Worktree Isolation"]
    W --> T
    A --> G["Persistent AgentTeam"]
    G --> Q["Shared Tasks / Mailboxes"]
    G --> W
```

一次完整工具闭环：

```text
模型流式输出
  → 产生结构化 ToolCall
  → Pydantic 校验参数
  → 权限系统判定 allow / ask / deny
  → 执行工具并产生 ToolResult
  → 将结果写回 ConversationManager
  → Agent 继续下一轮模型调用
  → 输出最终回答
```

### 核心模块边界

- **Provider Adapter**：将不同厂商的 SDK 事件转换成统一 `StreamEvent`，Agent 不依赖具体协议。
- **ConversationManager**：保存文本、Thinking、ToolUse 和 ToolResult，并按 Provider 协议序列化。
- **Agent Loop**：管理模型与工具之间的循环、停止条件、错误恢复、并发和上下文预算。
- **ToolRegistry**：统一工具 schema、参数模型、类别、并发属性与执行入口。
- **PermissionChecker**：在模型之外处理危险命令、路径范围、规则、模式和用户确认。
- **Memory / Session**：加载分层项目指令与记忆，追加保存 JSONL，并在恢复时校验工具消息链。
- **SubAgent Runtime**：隔离对话和权限状态，管理定义式/Fork 路径、后台任务、异步通知与成本追踪。
- **Textual TUI**：消费 Agent 事件，展示流式回答、思考、工具状态和内联交互。

## 内置工具

| 工具 | 类别 | 功能 |
|---|---|---|
| `ReadFile` | read | 分段读取带行号的 UTF-8 文件 |
| `WriteFile` | write | 创建或覆盖文件，自动创建父目录 |
| `EditFile` | write | 对唯一匹配文本执行精确替换 |
| `Bash` | command | 执行命令，返回 stdout、stderr、退出码和超时 |
| `Glob` | read | 按 glob 查找文件，支持精确文件名递归回退 |
| `Grep` | read | 按正则搜索代码，可限制文件模式 |
| `ToolSearch` | read | 按需发现延迟注册工具 |
| `AskUserQuestion` | read | 在 Agent Loop 中发起结构化用户问题 |
| `WritePlan` | plan only | 仅在 Plan 模式写入当前计划文件 |
| `Agent` | command | 启动定义式子 Agent，或 Fork 当前完整上下文并在后台执行 |

工具失败会作为结构化结果返回模型，不会直接终止 TUI。超长结果保存在
`.mewcode/session/tool-results/`，只把结果预览和文件位置回灌给模型；声明为并发安全的相邻只读工具可
并行执行。

## 三种执行模式

使用 `Shift+Tab` 按以下顺序循环，底栏始终显示当前模式：

```text
Accept Edits → Plan → YOLO → Accept Edits
```

| 模式 | 快捷入口 | 文件读写 | Bash / command | 权限弹窗 |
|---|---|---|---|---|
| **Accept Edits** | 默认、`/do` | 读写直接执行 | 安全白名单直放，其余询问 | 按需显示 |
| **Plan** | `/plan` | 只读；仅 `WritePlan` 可写计划文件 | 拒绝 | 不显示 |
| **YOLO** | `/mode yolo` | 直接执行 | 通过硬安全层后直接执行 | 不显示 |

YOLO 跳过可配置规则和 HITL，但仍保留两层不可绕过边界：硬编码危险命令检测和项目路径沙箱。
例如 `rm -rf /` 或访问项目/临时目录之外的路径仍会被拒绝。

## 快速开始

### 1. 安装

需要 Python 3.11+，推荐使用 [uv](https://docs.astral.sh/uv/)：

```powershell
git clone https://github.com/qd-maker/coding-agent.git
cd coding-agent
uv sync --extra dev
```

也可以使用 pip：

```powershell
python -m pip install -e ".[dev]"
```

### 2. 配置

```powershell
Copy-Item mewcode.yaml.example mewcode.yaml
$env:ANTHROPIC_API_KEY = "your-key"
```

默认配置示例：

```yaml
providers:
  - name: anthropic-official
    protocol: anthropic
    base_url: https://api.anthropic.com
    api_key: ${ANTHROPIC_API_KEY}
    model: claude-sonnet-4-6
    thinking: true

system_prompt: You are MewCode, a concise and helpful coding assistant.

mcp_servers:
  - name: context7
    command: npx
    args: ["-y", "@upstash/context7-mcp"]
    startup_timeout: 20
    tool_timeout: 120
  # - name: remote_tools
  #   url: https://mcp.example.com/mcp
  #   headers:
  #     Authorization: Bearer ${REMOTE_MCP_TOKEN}
```

`providers` 中第一项是当前活动 Provider。MCP 配置为列表且每项显式声明 `name`；每个
server 只能选择 `command`（stdio）或 `url`
（Streamable HTTP）。MCP 工具按 `mcp_<server>_<tool>` 注册（远端名称中的 `-` 等字符会规范化为 `_`）并默认延迟加载，Agent 会先用
`ToolSearch` 发现所需工具。stdio 子进程只继承 `PATH`、Windows 启动所需的最小运行时变量
以及配置中显式声明的 `env`，避免泄漏宿主机 API key。Context7 stdio 示例需要本机安装
Node.js / `npx`。多个 MCP Server 并行启动；`startup_timeout` 约束连接和工具枚举，
`tool_timeout` 约束单次工具调用，单个服务失败不会阻断其他服务。

OpenAI 兼容配置：

```yaml
providers:
  - name: openai-official
    protocol: openai
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY}
    model: gpt-5.5
    thinking: false
```

配置发现顺序：当前目录的 `mewcode.yaml`，然后是 `~/.mewcode/config.yaml`。也可以使用
`--config` 显式指定路径。

### 3. 运行

```powershell
uv run mewcode
# 或
uv run python -m mewcode

# 非交互模式（同样加载 MCP/Hook，并保存有效会话）
uv run mewcode -p "解释这个项目的入口"
```

### 4. 可选：项目指令与持久记忆

在项目根目录创建 `MEWCODE.md`，可记录技术栈、编码规范和项目注意事项：

```markdown
# Project Instructions

- 修改接口前先更新 API Contract。
- 完成后运行 ruff、mypy 和 pytest。
@include ./docs/coding-style.md
```

MewCode 按 `<project>/MEWCODE.md` → `<project>/.mewcode/MEWCODE.md` →
`~/.mewcode/MEWCODE.md` 的顺序加载。`@include` 最多递归 5 层，且不能跳出项目目录。

会话消息追加保存到 `<project>/.mewcode/sessions/*.jsonl`，摘要和消息数保存在同名 `.meta`
文件。自动记忆分开写入 `~/.mewcode/memories.md`（用户偏好、纠正反馈）与
`<project>/.mewcode/memories.md`（项目知识、参考资料）。这些运行时文件已被 `.gitignore`
忽略。

## 为什么采用 spec / tasks / checklist

这个项目使用 AI-assisted / vibe coding 完成，但没有把自然语言对话本身当作需求和验收依据。
每个章节都先拆成三份文档：

```text
spec.md      明确目标、功能需求、非功能需求、Out of Scope、完成定义
tasks.md     拆分实现步骤、文件影响、依赖关系和交付标准
checklist.md 把“完成”转换成可搜索、可运行、可观察的验收证据
```

编码前先维护 [`docs/api-contract.md`](docs/api-contract.md)，编码后执行测试和静态检查。这套方式
不能消除模型错误，但能显著减少长任务中的目标漂移、漏接主流程、只生成死代码以及“看起来
完成了”的错误判断。

章节状态和三件套索引位于 [`docs/README.md`](docs/README.md)；更完整的学习复盘见
[`docs/learning-notes.md`](docs/learning-notes.md)，最终完整性、性能和付费 API 证据见
[`docs/final-audit.md`](docs/final-audit.md)。

## 关于“AI 项目最大的坑”

目前我的答案是：**最大的坑不是模型不会生成代码，而是模型的不确定性沿调用链累积后，系统
仍然可能给出一个看起来合理的完成结果。**

- 模型说“已修改”，工具可能没有真正执行。
- 工具执行成功，结果可能没有正确写回上下文。
- AI 可以生成完整模块，但模块可能没有接入 Agent 主流程。
- Prompt 中的安全要求会被遗忘，不能代替程序化权限边界。
- 上下文持续增长会同时增加成本、延迟和错误理解的概率。
- Vibe coding 可以快速产生代码，也会快速产生“代码很多，所以已经完成”的错觉。

MewCode 目前的应对方式是：统一事件和数据 Contract、结构化工具结果、明确停止条件、程序化
权限层、分层上下文预算，以及以 checklist 和自动测试验证完成声明。

## 验证

```powershell
uv run python -m compileall -q mewcode tests
uv run ruff check mewcode tests
uv run mypy mewcode
uv run pytest -q
```

当前回归结果：

```text
497 passed, 1 skipped
```

跳过项是 Windows 未授予符号链接创建权限时的 symlink escape 测试；其余 Provider、对话、
工具、Agent Loop、Prompt、权限系统、上下文、记忆持久化、Slash Command、Skill、Hook、SubAgent、
Worktree、AgentTeam 和 TUI
用例均执行。

## 项目结构

```text
mewcode/
├── agent.py              # Agent Loop、事件、工具执行与恢复
├── client.py             # Anthropic / OpenAI Provider Adapter
├── conversation.py       # Provider-neutral 对话与工具块
├── context/              # Token 估算、两层压缩、落盘与压缩后恢复
├── memory/               # MEWCODE.md、JSONL 会话与自动记忆
├── prompts.py            # System Prompt Builder
├── app.py                # Textual TUI
├── commands/             # Registry、Parser、补全组件与本地/动态命令
├── skills/               # Skill 解析、三级加载、inline/fork、目录工具与三个内置 SOP
├── hooks/                # 生命周期事件、条件、Action 执行器、YAML loader 与 HookEngine
├── agents/               # Agent 定义、四级加载、Fork、过滤、后台任务、通知与 Trace
├── worktree/             # Git worktree 生命周期、恢复、保护、初始化与过期清理
├── teams/                # 持久化团队、共享任务、磁盘邮箱、后端、transcript 与合并
├── mcp/                  # MCP client、manager 与 Tool wrapper
├── permission_dialog.py  # AskUser / 权限内联交互
├── permissions/          # 模式、危险检测、路径沙箱、规则、Checker
└── tools/                # Tool Contract 与内置工具

docs/
├── api-contract.md
├── ch2/ ... ch15/        # spec / tasks / checklist
├── assets/               # 真实运行截图
└── learning-notes.md
```

## Roadmap（尚未完成）

- 跨主机团队调度、远程队列与分布式锁
- Agent/Skill 市场与版本化分发

仓库中的后续实验性脚手架不代表其已达到 CH2–CH15 相同的完成与验收标准。

## 操作提示

- `Shift+Tab`：循环 Accept Edits、Plan、YOLO。
- `/plan`：进入 Plan 模式。
- `/do`：返回 Accept Edits。
- `/mode yolo`：进入 YOLO。
- `/compact`（或 `/c`）：手动生成结构化摘要，并显示压缩前后的 Token 估算。
- `/session list`：查看最近会话；`resume <id|序号>`、`new`、`delete <id>` 管理存档。
- `/memory`：查看自动记忆；`/memory clear` 清空；`/memory edit` 显示双层文件路径。
- `/help`：列出内置命令；输入 `/` 后按 Tab 可补全。
- `/status`：查看模式、会话、Token、工具、记忆、工作目录和版本。
- `/permission`：查看或修改权限模式和本地规则。
- `/commit [范围]`：激活 inline commit SOP，在主对话内检查、提交并验证变更。
- `/review [关注点]`：在隔离 fork 中执行 review Skill，结果摘要回流主界面。
- `/test [范围]`：识别项目类型并执行对应测试 SOP。
- `/skill list|info|reload`：查看来源和详情，或重新扫描 Skill 目录。
- `/tasks`：列出后台 SubAgent；`/task info <id>` 查看结果；`/task cancel <id>` 取消。
- `/trace [trace-id]`：查看 SubAgent 父子链路与 Token 汇总。
- `/worktree create <name> [base]`：创建并进入独立 Git worktree；`list/enter/status` 管理目录。
- `/worktree exit [--remove] [--discard]`：默认保留；删除前保护未提交和未推送工作。
- `/team create <name>`：创建长期团队；`list/status/tasks/merge/stop/delete` 管理成员和工作。
- `Agent` 工具携带 `team_name` 和 `name` 时，会在独立 worktree 中启动可恢复 teammate。
- `teammate_mode: in-process` 适合跨平台演示；tmux/iTerm2 模式使用独立 CLI pane 强隔离。
- `mewcode --resume`：进程中断后恢复上次活动的 worktree session。
- 子 Agent 前台运行时按 `Esc`：把原实例切换到后台，不终止重跑。
- `Ctrl+O`：展开或收起 Extended Thinking。
- 生成期间按一次 `Ctrl+C`：取消当前回复。
- 空闲时 1.5 秒内连续按两次 `Ctrl+C`：退出。
- `Ctrl+L`：清空当前显示，不删除对话历史。

## License

当前仓库尚未附加开源许可证。代码默认保留所有权利；如需复用，请先联系仓库作者。
