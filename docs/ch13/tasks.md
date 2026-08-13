# CH13：SubAgent 系统 Tasks

## T1：固定 API Contract

- [x] 在 `docs/api-contract.md` 定义 Agent 文件、统一工具、Task/Trace、通知和过滤顺序。
- [x] 明确 CH14 Worktree、CH15 Team 不在本章伪实现。

## T2：AgentDefinition 与解析器

- [x] 新增 `AgentDefinition`，并提供课程命名兼容别名 `AgentDef`。
- [x] 解析 YAML frontmatter + Markdown body。
- [x] 校验必填字段、枚举、正整数、布尔值和工具列表。
- [x] 保留未知 metadata。

## T3：多来源 AgentLoader

- [x] project > user > builtin > plugin，first-win 覆盖。
- [x] 自动发现项目/用户插件 agents 目录并支持显式注册。
- [x] 文件失败隔离、大小写无关 get、热重载与有效缓存回退。
- [x] Verification flag 和 catalog prompt。

## T4：内置 Agent

- [x] Explore：haiku、只读探索、30 轮。
- [x] Plan：只读规划、15 轮。
- [x] general-purpose：继承主模型、通用实现、50 轮。
- [x] Verification：可选且默认隐藏。

## T5：多层工具过滤

- [x] MCP 直通、全局禁止、自定义限制、后台白名单、定义白黑名单。
- [x] Agent/AskUserQuestion 全局移除。
- [x] 核心文件工具使用子级 FileCache 实例。
- [x] deferred/discovered 状态按需复制。

## T6：Fork 构造

- [x] 深拷贝父完整消息和注入状态。
- [x] 悬挂 tool_use 补 interrupted tool_result。
- [x] 注入强约束 fork boilerplate 和具体任务。
- [x] 扫描标签拒绝 Fork-of-Fork。

## T7：RunToCompletion 与独立权限

- [x] 返回最后一个模型回合文本，而不是拼接所有中间轮次。
- [x] 子 Agent 使用独立 PermissionChecker、Sandbox、RuleEngine 和 Token 状态。
- [x] ask 决策在非交互子 Agent 中转为 deny。
- [x] 共享 Provider、HookEngine 和文件系统基础设施。

## T8：TraceRegistry

- [x] 创建、更新、完成、单节点查询和整棵 trace 查询。
- [x] 记录父子 ID、状态、Task、时间、错误和 Token。
- [x] 汇总 trace 输入/输出 Token，不存在 ID 时 no-op。

## T9：TaskManager

- [x] launch、adopt_task、adopt_running、get、list、cancel、poll、shutdown。
- [x] running/completed/failed/cancelled 状态机。
- [x] 默认后台超时和 asyncio completion queue。
- [x] 解决 monitor 尚未启动就取消时的状态收尾竞态。

## T10：统一 AgentTool

- [x] 一个工具按 `subagent_type` 分流定义式和 Fork。
- [x] 模型覆盖优先级：调用参数 > 定义 > 父 client。
- [x] 同步、显式后台、定义后台和强制 Fork 后台。
- [x] 未知类型、禁用 Fork、保留参数和子 Agent 失败均返回结构化结果。

## T11：前台转后台

- [x] 前台 wait 使用 shield，超时只移交、不杀掉重跑。
- [x] ESC 调用 `detach_foreground`，移交后再取消父 TUI worker。
- [x] 重复 adopt 同一 execution task 时幂等返回已有任务。

## T12：通知与 Slash Command

- [x] task-notification 包含任务、Agent、状态、耗时、Token 和结果。
- [x] 5000 字符截断。
- [x] `/tasks`、`/task info`、`/task cancel`、`/trace`。
- [x] TUI 空闲轮询后注入 ConversationManager 并持久化。

## T13：App 接入

- [x] 启动时加载 Agent catalog、创建 Trace/Task manager、注册 Agent 工具。
- [x] Agent catalog 注入环境上下文。
- [x] App shutdown 等待后台任务收尾。
- [x] Textual 增加 ESC 后台移交绑定。

## T14：测试与质量门禁

- [x] `tests/test_subagent.py`：31 个测试结果。
- [x] CH13 TUI 注册和异步通知测试。
- [x] 全量回归：450 passed，1 skipped。
- [x] Ruff、Mypy、compileall、wheel/sdist 全部通过。
- [x] 真实 Provider：定义式 Agent 与 Fork 后台路径均完成，Trace 记录 Token。

## 进度

- [x] T1–T14 全部完成。
