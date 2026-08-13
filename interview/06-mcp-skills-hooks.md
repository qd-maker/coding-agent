# 06 MCP、Skill、Hook、Slash：四种「变强」不要混

面试最容易糊成一锅。先记这张表：

| 机制 | 解决什么 | 像什么 | 本仓库 |
|---|---|---|---|
| Function Calling | 模型和宿主怎么交接工具 | 嘴和手的接头标准 | `conversation` + `client` |
| MCP | **还能干什么**（外部工具） | 往工具箱里插新工具 | `mcp/` |
| Skill | **这件事该怎么干** | 作业指导书 | `skills/` `LoadSkill` |
| Hook | **到点必须发生的确定动作** | 门禁/流水线脚本 | `hooks/` |
| Slash | **用户显式点名的本地命令** | 快捷键 | `commands/` |

---

## MCP：工具放到别人的进程里

**为什么不全部写死在 Agent 里：**  
GitHub 建 PR、查文档、浏览器，跟 Loop 无关。写死就要改核心、难共享。MCP 让工具以独立 Server 存在：Claude Code、Cursor、Simple Code 都能连同一个 Server。

**用户加工具不用重编译：** 改 YAML，写启动命令即可。

本仓库：

- 传输：本地 **stdio**（子进程，stdin/stdout JSON-RPC）；远端 **Streamable HTTP**
- 启动某个 Server 挂了：**降级**，Agent 照开，状态栏提示失败。扩展能力不能拖死核心
- 包装名：`mcp_{server}_{tool}`，进同一个 Tool 字典
- **默认 `should_defer=True`**：先不把完整 schema 塞给模型，省窗口。模型先 `ToolSearch`，命中后再 `mark_discovered`，下一轮才带完整定义
- 只读 annotation 会标成 read、可并行；没标注就当有风险的 command
- stdio Server = 本机进程，配置要可信

延迟加载不要随口报「85%」——那是课程示例数字。你要说：

> 我测的是：工具一多，schema 比对话还占窗口。所以默认只露名字，用到再展开。具体省多少看你配了几个 Server。

Anthropic 有服务端 `tool_reference`；兼容端点没有。本仓库把「展开 schema」放在**客户端**，双协议都能用。这是一个很好的差异化故事。

---

## Skill：按需说明书，不是新工具

一个 Skill = YAML 头 + Markdown 正文。

- 平时模型只看见**目录里的短描述**（省 token）
- 意图匹配时调 `LoadSkill`，把完整 SOP 钉进环境
- `allowed_tools` 白名单，多个 Skill 取**交集**（越用越收，防能力膨胀）
- 来源：内置 `commit/review/test` < 用户目录 < 项目 `.simplecode/skills`，项目优先
- inline：注入当前会话；有的走 fork 子 Agent，不污染主对话

和改 System Prompt 的区别：Prompt 是全局人格；Skill 是这次任务的作业，用完可卸。全塞进 System Prompt 会互相打架、浪费窗口。

**Skill 不能替代权限。** 部署说明书可以写步骤，发不发版由权限和 Hook 决定。

---

## Hook：到点跑确定的事

模型「自觉每次跑测试」不可靠。Hook 在固定生命周期触发：启动、每轮前后、工具前后、结束。

`PreToolUse` 可以 reject。失败只通知，不拖垮 Loop。

条件是有限 DSL，不是任意 Python——避免用户配置变成远程代码执行。

---

## Slash：用户说了才走本地

`/plan` `/do` `/compact` `/session` `/memory` `/skill` `/worktree` `/team` …  
**先于 Agent 拦截**，不浪费一轮模型。Tab 可补全。

和 Skill 的分工：Slash 是人点的开关；Skill 是模型按需加载的流程包。

---

## 口述 45 秒

> Function Calling 是接头协议。MCP 往外接工具，失败降级，schema 默认延迟加载。Skill 是按需 SOP 加工具白名单，不提供新能力。Hook 是确定时机的确定动作。Slash 是用户本地命令。四套叠在一起：能扩展、能约束、还不把 System Prompt 写成圣经。

下一篇：[07-subagent-teams.md](07-subagent-teams.md)
